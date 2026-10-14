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
from pathlib import Path

from poolhouse import (
    build_update,
    keystore,
    node_app,
    node_binary,
    node_key,
    runtime,
    runtime_store,
    win32,
    worktreerules,
)
from poolhouse.files import read_json, write_json
from poolhouse.home import state as state_root
from poolhouse.lock import Busy, only_one
from poolhouse.node_health import node_stop_event, state_key
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
                try:
                    with only_one(worktreerules.landing_lock(Path(source)), wait=False, note="node runtime cutover"):
                        if signature(state) != wanted:
                            end(child, state)
                            return None
                except (Busy, OSError, ValueError):
                    continue


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
    """The supervisor holds the keys it hands the node for as long as it runs; a crash of it leaves no core file that holds them."""
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
        refused, denials, told, began = 0, 0, ("", 0.0), time.monotonic()
        child = _spawn(state, binary, sha, extra, key)
        key = None
        code = _wait(child, state, seen, stopping)
        if code is None and child.returncode is None:
            end(child, state)
        _note(state, f"node {child.pid} exited {child.returncode}")
        delay = MIN_DELAY_S if code is None or code == -signal.SIGTERM or time.monotonic() - began >= STABLE_S else min(delay * 2, MAX_DELAY_S)
        _pause(state, delay, seen, stopping)
    return 0


def end(child: subprocess.Popen, state: Path) -> None:
    """Stop a node the supervisor was told to leave or replace: ask (the stop event on Windows, SIGTERM elsewhere), then insist."""
    if not (is_windows() and win32.signal_event(node_stop_event(state))):
        child.terminate()
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()
