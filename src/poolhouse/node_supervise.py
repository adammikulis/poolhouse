"""The node's supervisor: keeps one `poolhouse-node` running for a state directory, restarting it when it exits.

Crash-only. The node's state is its logs on disk, so a restart loses nothing; the supervisor only starts the binary the
state directory points at (or the selected runtime's), checked against its recorded checksum on every start, and starts it
again with a growing delay if it exits. It leaves when no verified binary can be found.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from poolhouse import (
    build_update,
    keystore,
    node_app,
    node_binary,
    node_key,
    runtime,
    runtime_git,
    runtime_source,
    runtime_store,
    runtime_unmerged,
    win32,
)
from poolhouse.files import read_json, write_json
from poolhouse.home import state as state_root
from poolhouse.lock import Busy, only_one
from poolhouse.node_health import node_health, node_stop_event, state_key
from poolhouse.platform import is_windows, start_process

if os.name == "posix":
    import resource
else:
    resource = None

POINTER = "node-binary.json"
RUN = "node-run.json"
LOG = "node.log"
LOCK = "supervisor.lock"
MIN_DELAY_S = 0.1
MAX_DELAY_S = 30.0
STABLE_S = 60.0
GIVE_UP_AFTER = 5
REMIND_S = 60.0
DENIED_WAIT_S = keystore.COOLDOWN_S + 5.0
DENIED_GIVE_UP = 3
"""A keystore the person or the system refused is asked again only when its own cooldown is over, and three times at most."""
POLL_S = 0.1



@dataclass
class _Cutover:
    identity: tuple[bytes, ...]
    seen: tuple[bytes, bytes] = (b"", b"")
    next_release: float = 0.0
    denials: int = 0


@dataclass(frozen=True)
class _Replacement:
    state: Path
    source: Path
    boundary: Path
    binary: Path
    sha: str
    seen: tuple[bytes, bytes]
    extra: list[str]
    current: bool


def supervisor_stop_event(state: Path) -> str:
    """The named event whose signal stops the supervisor of a state directory on Windows, where a signal can not reach it."""
    return f"Local\\poolhouse-node-supervisor-stop-{state_key(state)}"


def pointer(state: Path) -> dict:
    """The binary a swap chose for this state directory ({binary, sha256}), or {} when the selected runtime decides."""
    row = read_json(state / POINTER, {})
    return row if isinstance(row, dict) else {}


def point(state: Path, binary: Path | None, sha: str = "") -> None:
    """Choose the binary the next start runs; None goes back to the selected runtime's."""
    if binary is None:
        (state / POINTER).unlink(missing_ok=True)
    else:
        write_json(state / POINTER, {"binary": str(binary), "sha256": sha})


def signature(state: Path) -> tuple[bytes, bytes]:
    """What decides the binary a start runs, as bytes: the pointer file and the runtime selection. A change restarts the node."""
    def read(path: Path) -> bytes:
        try:
            return path.read_bytes()
        except OSError:
            return b""
    return read(state / POINTER), read(runtime.directory() / "selected.json")


def resolve(state: Path) -> tuple[Path, str]:
    """The verified binary a start runs and its checksum: the pointer's when set, else the selected runtime's."""
    chosen = pointer(state)
    if chosen:
        binary = Path(str(chosen.get("binary", "")))
        if not binary.is_file() or node_binary.sha256(binary) != chosen.get("sha256"):
            raise node_binary.NodeBinaryError(f"{binary} does not match the checksum it was chosen with; refusing to start it")
        return binary, str(chosen["sha256"])
    prefix = runtime.selection_prefix()
    if prefix is None:
        raise node_binary.NodeBinaryError("no runtime is selected, so there is no node binary to start")
    binary = node_binary.verified(prefix)
    return binary, node_binary.record_of(prefix)["sha256"]


def running(state: Path) -> dict:
    """The record of the node this state directory last started: pid, binary, checksum, start time, supervisor pid."""
    row = read_json(state / RUN, {})
    return row if isinstance(row, dict) else {}


def _program(binary: Path, sha: str, extra: list[str]) -> Path:
    """What to execute for a verified binary: on macOS with the network on, the same binary inside Poolhouse.app, which is the
    identity the person gave Local Network permission to; otherwise the binary itself."""
    return node_app.bundled(binary, sha) if node_app.uses_bundle(extra) else binary


def _spawn(state: Path, binary: Path, sha: str, extra: list[str], key: node_key.Released | None) -> subprocess.Popen:
    """Start the node; a key that is kept in the keystore or under a passphrase goes to it on its standard input and nowhere else."""
    argv = [str(_program(binary, sha, extra)), "run", "--state", str(state), *(key.argv() if key else []), *extra, *build_update.node_args()]
    kept = {name: value for name, value in os.environ.items() if name != node_key.PASSPHRASE_ENV}
    with (state / LOG).open("ab") as out:
        child = start_process(argv, stdin=subprocess.PIPE if key else subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, env=kept)
    recorded = False
    try:
        if key and child.stdin is not None:
            try:
                child.stdin.write(key.line())
                child.stdin.close()
            except OSError:
                _note(state, f"node {child.pid} did not read its key")
        before = running(state)
        previous = before.get("previous") if before.get("sha256") == sha else {"binary": before.get("binary"), "sha256": before.get("sha256")}
        write_json(state / RUN, {"pid": child.pid, "binary": str(binary), "sha256": sha, "started_at": time.time(),
                                 "supervisor": os.getpid(), "previous": previous or {}})
        recorded = True
    finally:
        if not recorded:
            end(child, state)
    return child


def _wait(child: subprocess.Popen, state: Path, wanted: tuple[bytes, bytes], stopping: Callable[[], bool]) -> int | None:
    """Wait for the child to exit; None when asked to stop or when the chosen binary changed."""
    while True:
        try:
            return child.wait(timeout=POLL_S)
        except subprocess.TimeoutExpired:
            if stopping():
                return None
            if signature(state) != wanted:
                source = runtime_store.read_state().get("checkout")
                if not isinstance(source, str) or not source:
                    continue
                return None


def _pause(state: Path, seconds: float, seen: tuple[bytes, bytes], stopping: Callable[[], bool]) -> None:
    """Sleep up to ``seconds``, waking early when asked to stop or when a swap chose another binary."""
    end = time.monotonic() + seconds
    while time.monotonic() < end and not stopping() and signature(state) == seen:
        time.sleep(POLL_S)


def _note(state: Path, text: str) -> None:
    with (state / LOG).open("a", encoding="utf-8") as out:
        out.write(f"{time.strftime('%FT%T')} supervisor: {text}\n")


def refusal(state: Path, within_s: float) -> str:
    """Why the supervisor last refused to start the node, in its own words, when that was in the last ``within_s`` seconds; else an empty string."""
    try:
        tail = (state / LOG).read_text(encoding="utf-8", errors="replace")[-4000:]
    except OSError:
        return ""
    marker = " supervisor: cannot start the node: "
    for line in reversed(tail.splitlines()):
        if marker in line:
            when, _, why = line.partition(marker)
            try:
                recent = time.time() - time.mktime(time.strptime(when, "%Y-%m-%dT%H:%M:%S")) <= within_s
            except ValueError:
                recent = False
            return why if recent else ""
    return ""


def supervise(state: Path, extra: list[str] | None = None) -> int:
    """Keep the node of ``state`` running until stopped; 0 on a stop, 1 when it cannot find a verified binary or is already supervised."""
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    _no_core_files()
    stopping, close = _stop_requests(state)
    try:
        with only_one(state / LOCK, wait=False, note="node supervisor"):
            if state == state_root("node") and os.environ.get(build_update.OFF_ENV) != "off":
                build_update.watch(state, stopping)  # the device's own node keeps itself on the pool's newest build
            return _loop(state, extra or [], stopping)
    except Busy:
        return 1
    finally:
        close()


def _no_core_files() -> None:
    """Disable core dumps for key handoff buffers."""
    if resource is not None:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _stop_requests(state: Path) -> tuple[Callable[[], bool], Callable[[], None]]:
    """(whether a stop was asked for, how to let go): SIGTERM on Unix; on Windows Ctrl+Break, Ctrl+C and the stop event."""
    stop = {"now": False}
    handler = lambda *_: stop.update(now=True)  # noqa: E731
    if not is_windows():
        signal.signal(signal.SIGTERM, handler)
        return (lambda: stop["now"]), (lambda: None)
    signal.signal(signal.SIGBREAK, handler)  # type: ignore[attr-defined]
    signal.signal(signal.SIGINT, handler)
    event = win32.Event(supervisor_stop_event(state))
    return (lambda: stop["now"] or event.is_set()), event.close


def _loop(state: Path, extra: list[str], stopping: Callable[[], bool]) -> int:
    delay, refused, denials, told = MIN_DELAY_S, 0, 0, ("", 0.0)
    while not stopping():
        seen = signature(state)
        try:
            binary, sha = resolve(state)
            _program(binary, sha, extra)  # builds Poolhouse.app first when this start needs it, so a failure is a refusal here
            key = node_key.release(state)
        except node_key.KeyUnavailable as exc:
            if told[0] != str(exc) or time.monotonic() - told[1] > REMIND_S:
                _note(state, f"cannot start the node: {exc}")
                told = (str(exc), time.monotonic())
            denials += exc.denied
            if denials >= DENIED_GIVE_UP:
                _note(state, "the keystore was refused again and again, so it is not asked again until a person starts the node again")
                return 1
            _pause(state, DENIED_WAIT_S if exc.denied else delay, seen, stopping)
            delay = min(delay * 2, MAX_DELAY_S)
            continue
        except OSError as exc:
            refused += 1
            _note(state, f"cannot start the node: {exc}")
            if refused >= GIVE_UP_AFTER:
                return 1
            _pause(state, delay, seen, stopping)
            delay = min(delay * 2, MAX_DELAY_S)
            continue
        began = time.monotonic()
        child = _spawn(state, binary, sha, extra, key)
        key = None
        identity = _initial_identity(child, state, sha, stopping)
        if identity is None:
            refused += 1
            if stopping() or refused >= GIVE_UP_AFTER:
                return 0 if stopping() else 1
            _pause(state, delay, seen, stopping)
            delay = min(delay * 2, MAX_DELAY_S)
            continue
        refused, denials, told = 0, 0, ("", 0.0)
        binding = _Cutover(identity, seen)
        try:
            child, code = _watch(child, state, extra, binding, stopping)
        except OSError as exc:
            _note(state, f"node replacement unavailable: {exc}")
            return 1
        seen = binding.seen
        if code is None and child.returncode is None:
            end(child, state)
        _note(state, f"node {child.pid} exited {child.returncode}")
        delay = MIN_DELAY_S if code is None or code == -signal.SIGTERM or time.monotonic() - began >= STABLE_S else min(delay * 2, MAX_DELAY_S)
        _pause(state, delay, seen, stopping)
    return 0





def _watch(child: subprocess.Popen, state: Path, extra: list[str], binding: _Cutover,
           stopping: Callable[[], bool]) -> tuple[subprocess.Popen, int | None]:
    while True:
        code = _wait(child, state, binding.seen, stopping)
        if code is not None or stopping():
            return child, code
        changed = _cutover(child, state, extra, binding, stopping)
        if changed is not None:
            child, binding.seen = changed
        else:
            time.sleep(POLL_S)


def _initial_identity(child: subprocess.Popen, state: Path, sha: str, stopping: Callable[[], bool]) -> tuple[bytes, ...] | None:
    try:
        if _ready(child, state, sha, stopping):
            return _identity(state)
    except OSError:
        pass
    if child.poll() is None:
        end(child, state)
    return None


def _identity(state: Path) -> tuple[bytes, ...]:
    result = []
    for name in (node_key.RECORD, *node_key.IDENTITY):
        try:
            with (state / name).open("rb") as stream:
                data = stream.read(65537)
            if len(data) > 65536:
                raise node_key.KeyUnavailable("device identity metadata exceeds its bound")
            result.append(data)
        except FileNotFoundError:
            result.append(b"")
    return tuple(result)



def _same_identity(state: Path, identity: tuple[bytes, ...]) -> bool:
    try:
        return _identity(state) == identity
    except OSError:
        return False


def _ready(child: subprocess.Popen, state: Path, sha: str, stopping: Callable[[], bool]) -> bool:
    until = time.monotonic() + 20.0
    while time.monotonic() < until and not stopping() and child.poll() is None:
        healthy = node_health(state)
        if healthy and healthy.get("pid") == child.pid and running(state).get("sha256") == sha:
            return True
        time.sleep(POLL_S)
    return False


def _admitted(binary: Path, sha: str, source: Path, *, allow_unmerged_commit: str | runtime_unmerged.Approval = "") -> None:
    prefix = binary.parent.parent
    record = node_binary.record_of(prefix)
    node_binary.verified(prefix, expect=sha)
    commit = str(record.get("commit", ""))
    approved = allow_unmerged_commit.commit if isinstance(allow_unmerged_commit, runtime_unmerged.Approval) else allow_unmerged_commit
    if isinstance(allow_unmerged_commit, runtime_unmerged.Approval) and approved != commit:
        raise runtime_source.DeployError("the node runtime does not match the reviewed commit")
    exception = allow_unmerged_commit if isinstance(allow_unmerged_commit, runtime_unmerged.Approval) else True
    runtime_source.admit(source, commit, allow_unmerged=exception if approved and commit == approved else False)
    floor = runtime_source.floor_of(source, runtime_source.resolve_commit(source))
    if runtime_store.rejected(prefix) or runtime_store.epoch_of(prefix) < floor:
        raise node_binary.NodeBinaryError("node runtime is rejected or below the source floor")


def _prepare(child: subprocess.Popen, state: Path, extra: list[str], binding: _Cutover,
             stopping: Callable[[], bool]) -> _Replacement | None:
    source = runtime_store.read_state().get("checkout")
    if not isinstance(source, str) or not source or not _same_identity(state, binding.identity):
        return None
    seen = signature(state)
    try:
        boundary = runtime_git.boundary(Path(source))
        binary, sha = resolve(state)
        _program(binary, sha, extra)
        with only_one(boundary, wait=False, note="node runtime preparation"):
            if signature(state) != seen or not _same_identity(state, binding.identity) or stopping():
                return None
            _admitted(binary, sha, Path(source))
            current = running(state).get("sha256") == sha and _ready(child, state, sha, stopping)
        return _Replacement(state, Path(source), boundary, binary, sha, seen, extra, current)
    except (Busy, runtime_source.DeployError, OSError, ValueError):
        return None


def _replace(child: subprocess.Popen, plan: _Replacement, key: node_key.Released | None,
             stopping: Callable[[], bool]) -> tuple[subprocess.Popen, tuple[bytes, bytes]]:
    state = plan.state
    before = running(state)
    old, old_sha = Path(str(before.get("binary", ""))), str(before.get("sha256", ""))
    end(child, state)
    replacement = None
    try:
        replacement = _spawn(state, plan.binary, plan.sha, plan.extra, key)
        if _ready(replacement, state, plan.sha, stopping):
            return replacement, signature(state)
    except (OSError, subprocess.SubprocessError, Busy):
        pass
    if replacement is not None:
        end(replacement, state)
    try:
        _admitted(old, old_sha, plan.source)
    except (runtime_source.DeployError, OSError) as exc:
        raise node_binary.NodeBinaryError(f"previous node runtime is refused: {exc}") from exc
    point(state, old, old_sha)
    restored = None
    try:
        restored = _spawn(state, old, old_sha, plan.extra, key)
        if _ready(restored, state, old_sha, stopping):
            return restored, signature(state)
    except (OSError, subprocess.SubprocessError, Busy):
        pass
    if restored is not None:
        end(restored, state)
    raise node_binary.NodeBinaryError("replacement and previous node did not answer")


def _cutover(child: subprocess.Popen, state: Path, extra: list[str], binding: _Cutover,
             stopping: Callable[[], bool]) -> tuple[subprocess.Popen, tuple[bytes, bytes]] | None:
    plan = _prepare(child, state, extra, binding, stopping)
    if plan is None:
        return None
    if plan.current:
        return child, plan.seen
    if time.monotonic() < binding.next_release or binding.denials >= DENIED_GIVE_UP:
        return None
    binding.next_release = time.monotonic() + keystore.COOLDOWN_S
    key = None
    try:
        key = node_key.release(state)
        with only_one(plan.boundary, wait=False, note="node runtime cutover"):
            if signature(state) != plan.seen or not _same_identity(state, binding.identity) or stopping():
                return None
            try:
                _admitted(plan.binary, plan.sha, plan.source)
            except (runtime_source.DeployError, OSError):
                return None
            result = _replace(child, plan, key, stopping)
            if running(state).get("sha256") == plan.sha:
                binding.next_release, binding.denials = 0.0, 0
            return result
    except node_key.KeyUnavailable as exc:
        binding.denials += int(exc.denied)
        _note(state, f"node replacement waits for its key: {exc}")
        return None
    except Busy:
        return None
    finally:
        key = None


def end(child: subprocess.Popen, state: Path) -> None:
    """Stop a node the supervisor was told to leave or replace: ask (the stop event on Windows, SIGTERM elsewhere), then insist."""
    if not (is_windows() and win32.signal_event(node_stop_event(state))):
        child.terminate()
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()
