"""Find the node of a state directory, start it when it is dead, and move it onto a new binary without losing the board.

Every client calls `ensure_node(state)`: it returns the node's health, starting the node first when nothing answers. Starting
is single-flight (`start.lock`), so any number of clients at once start exactly one node, and the node runs under a supervisor
(`node_supervise`) that restarts it when it exits. The node's state is its logs on disk, so a kill loses nothing.

    python -m poolhouse.node_launch ensure|status|stop|swap|supervise [--state DIR]
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import psutil

from poolhouse import (
    errors,
    jobs,
    lan,
    node_binary,
    node_pool,
    node_supervise,
    real_machine,
    runtime,
    runtime_git,
    runtime_store,
    runtime_unmerged,
    spawned,
    win32,
)
from poolhouse.command import Group, flag
from poolhouse.home import state as state_root
from poolhouse.lock import Busy, held_by, only_one
from poolhouse.log import say, warn
from poolhouse.node_health import node_health, node_stop_event, socket_path
from poolhouse.platform import is_windows, private_dir, start_process

START_LOCK = "start.lock"
START_WAIT_S = 20.0
STOP_WAIT_S = 10.0
SWAP_WAIT_S = 20.0
POLL_S = 0.05


LAN_ENV = lan.OFF_ENV
"""``off`` keeps the device's own node local whatever else holds; without it `lan.allowed` still decides by what the process is."""


class NodeUnavailable(OSError, errors.NotRunning):
    """The node could not be started or did not answer in time."""


def default_state() -> Path:
    """The node's state directory on this machine."""
    return state_root("node")


def _until(deadline: float, ready) -> dict | None:
    while time.monotonic() < deadline:
        if (found := ready()) is not None:
            return found
        time.sleep(POLL_S)
    return None


def supervised(state: Path) -> bool:
    """Whether a supervisor holds this state directory."""
    return bool(held_by(state / node_supervise.LOCK))


PRESENCE_FLAG = "--presence"


def _presence(extra: list[str] | None) -> list[str]:
    """``extra`` with the way the node asks the person for the first key or phone. The installed software on the real home asks with the
    operating system's prompt and no other way: a terminal can be answered by the program that asks, so `--presence terminal` is dropped
    there (and the node refuses it on that home as well). Anywhere else (a test, a checkout, a rig, a server) `--presence os` is dropped, so
    none raises a dialog (`real_machine`), and `--presence terminal` is the caller's own word."""
    real, words, out, i = real_machine.allowed(), list(extra or []), [], 0
    while i < len(words):
        if words[i] != PRESENCE_FLAG:
            out.append(words[i])
        elif words[i + 1 : i + 2] == ["terminal"] and not real:
            out += words[i : i + 2]
        i += 2 if words[i] == PRESENCE_FLAG else 1
    return [*out, PRESENCE_FLAG, "os"] if real else out


def _start_supervisor(state: Path, extra: list[str]) -> spawned.Child:
    parent = "" if state == default_state() else os.environ.get(node_supervise.PARENT_ENV, "")
    argv = ["supervise", "--state", str(state), *(f"--arg={word}" for word in extra), *(["--parent-pid", parent] if parent.isdigit() else [])]
    return spawned.child_of(jobs.detach("poolhouse.node_launch", argv, log=state / "supervisor.log"))


def _answers_or_ends(state: Path, supervisor: spawned.Child, wait_s: float) -> dict | None:
    """The node's health once it answers; None when ``wait_s`` passes with the supervisor still
    running; `NodeUnavailable` the moment the supervisor ends, with the reason it last gave for
    refusing the node."""
    try:
        return spawned.wait(supervisor, lambda: node_health(state), what="the node supervisor",
                            timeout_s=wait_s, watch=spawned.Watch(log=state / "supervisor.log", poll_s=POLL_S))
    except spawned.StartFailed as exc:
        if exc.kind == "timeout":
            return None
        why = node_supervise.refusal(state, wait_s + 30)
        raise NodeUnavailable(f"{exc}{f' ({why})' if why else ''}") from exc


def ensure_node(state: Path | None = None, *, extra: list[str] | None = None, wait_s: float = START_WAIT_S) -> dict:
    """The node's health, after starting it under a supervisor when it was not answering.

    The device's own node (the default state directory) starts on the LAN, so it joins its project's open pool when it hears one
    (`node_pool.network_args`), but only where `lan.allowed` (the installed software on the account's real state root, never a test or a
    moved root: `--lan` is dropped there); a node in any other state directory stays local unless ``extra`` says otherwise.
    Raises `NodeUnavailable` when it still does not answer after ``wait_s``, and `node_binary.NodeBinaryError` before starting
    anything when the binary is missing or does not match its recorded checksum.
    """
    state = state or default_state()
    if extra is None and state == default_state():
        extra = node_pool.network_args()
    extra = _presence(lan.clamp(extra) if extra else extra)
    if (said := node_health(state)) is not None:
        return said
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    private_dir(state)  # on Windows the mode above means nothing: this is the owner-only ACL
    node_supervise.resolve(state)
    deadline = time.monotonic() + wait_s
    try:
        with only_one(state / START_LOCK, timeout=wait_s, announce=lambda _: None, note="node start"):
            if (said := node_health(state)) is not None:
                return said
            supervisor = None if supervised(state) else _start_supervisor(state, extra or [])
            if supervisor is None:
                said = _until(deadline, lambda: node_health(state))
            else:
                said = _answers_or_ends(state, supervisor, max(deadline - time.monotonic(), 0.0))
    except Busy as exc:
        raise NodeUnavailable(f"another client is still starting the node: {exc}") from exc
    if said is None:
        why = node_supervise.refusal(state, wait_s + 30)
        raise NodeUnavailable(f"the node did not answer within {wait_s:.0f}s{f': {why}' if why else ''}; see {state / node_supervise.LOG}")
    return said


def stop_node(state: Path, *, wait_s: float = STOP_WAIT_S) -> bool:
    """Ask the node (and its supervisor) to exit and wait for it; True once nothing answers. Safe at any moment: state is on disk."""
    run = node_supervise.running(state)
    _ask_to_stop(state, run)
    if _until(time.monotonic() + wait_s, lambda: {} if node_health(state) is None and not supervised(state) else None) is not None:
        return True
    if (node := node_supervise.owned(state, run, "pid")) is not None:
        with contextlib.suppress(psutil.Error, OSError):
            node.kill()
    return node_health(state) is None


def _ask_to_stop(state: Path, run: dict) -> None:
    """SIGTERM to the supervisor and the node that ``run`` records for ``state``; on Windows, where a detached process has no signal, their two stop events."""
    if is_windows():
        win32.signal_event(node_supervise.supervisor_stop_event(state))
        win32.signal_event(node_stop_event(state))
        return
    for role in ("supervisor", "pid"):
        if (proc := node_supervise.owned(state, run, role)) is not None:
            with contextlib.suppress(psutil.Error, OSError):
                proc.terminate()


def status(state: Path | None = None) -> dict:
    """What `runtime status` shows: whether the node answers, its version, pid, socket, uptime and binary, and whether it is supervised."""
    state = state or default_state()
    said, run = node_health(state), node_supervise.running(state)
    mine = said is not None and run.get("pid") == said.get("pid")
    return {"state": str(state), "healthy": said is not None, "supervised": supervised(state),
            "version": said.get("version", "") if said else "", "pid": said.get("pid", 0) if said else 0,
            "socket": str(socket_path(state)), "latency_ms": said.get("latency_ms") if said else None,
            "uptime_s": round(time.time() - float(run.get("started_at", 0.0)), 1) if mine else None,
            "binary": run.get("binary", "") if mine else "", "sha256": run.get("sha256", "") if mine else "",
            "pinned": bool(node_supervise.pointer(state))}


def _answers_as(state: Path, sha: str) -> dict | None:
    said = node_health(state)
    run = node_supervise.running(state)
    return said if said and run.get("sha256") == sha and said.get("pid") == run.get("pid") else None


def swap(state: Path | None = None, *, wait_s: float = SWAP_WAIT_S, allow_unmerged_commit: str | runtime_unmerged.Approval = "",
         boundary_wait_s: float = 0.0) -> dict:
    """Make the node run the selected runtime's binary; on a new node that does not answer put the previous binary back.

    The supervisor follows the runtime selection by itself, so this checks and finishes the move: it starts a supervisor
    for a node that had none, then waits for a node of the new checksum to answer. Returns {action, detail}: current,
    idle (nothing was running; the next start uses the new binary), swapped or failed (the old binary runs again, or
    nothing was changed). One node owns a state directory, so the old node stops and the new one starts on the same socket
    (pipe) path; the board is its logs on disk and is read again at start.
    """
    state = state or default_state()
    try:
        prefix = runtime.selection_prefix()
        if prefix is None:
            raise node_binary.NodeBinaryError("no runtime is selected")
        new_sha = node_binary.record_of(prefix)["sha256"]
        node_binary.verified(prefix)
    except OSError as exc:
        return {"action": "failed", "detail": f"the selected runtime's node binary is refused: {exc}"}
    before, run, pin = node_health(state), node_supervise.running(state), node_supervise.pointer(state)
    if before is None and not supervised(state):
        return {"action": "idle", "detail": "no node was running; the next start runs the selected runtime's"}
    if before is not None and run.get("sha256") == new_sha and before.get("pid") == run.get("pid"):
        return {"action": "current", "detail": f"node {before['pid']} already runs it"}
    source = runtime_store.read_state().get("checkout")
    if not isinstance(source, str) or not source:
        return {"action": "deferred", "detail": "node replacement needs its recorded source checkout"}
    try:
        with runtime_git.crossing(Path(source), wait_s=boundary_wait_s, note="node runtime swap"):
            if runtime.selection_prefix() != prefix:
                return {"action": "deferred", "detail": "node runtime selection changed"}
            if allow_unmerged_commit:
                node_supervise._admitted(node_binary.location(prefix), new_sha, Path(source),
                                         allow_unmerged_commit=allow_unmerged_commit)
            else:
                node_supervise._admitted(node_binary.location(prefix), new_sha, Path(source))
            node_supervise.point(state, None)
            if allow_unmerged_commit or not supervised(state):
                try:
                    stop_node(state)
                    _start_supervisor(state, [])
                    came = _until(time.monotonic() + wait_s, lambda: _answers_as(state, new_sha))
                except (OSError, subprocess.SubprocessError):
                    came = None
                if came is not None:
                    return {"action": "swapped", "detail": f"node {came['pid']} runs {new_sha[:12]}"}
                return _rolled_back(state, pin, new_sha, wait_s, direct=True)
    except Busy:
        return {"action": "deferred", "detail": "node replacement waits for the runtime cutover"}
    except (OSError, ValueError, RuntimeError) as exc:
        return {"action": "failed", "detail": f"node replacement is refused: {exc}"}
    came = _until(time.monotonic() + wait_s, lambda: _answers_as(state, new_sha))
    if came is not None:
        return {"action": "swapped", "detail": f"node {came['pid']} runs {new_sha[:12]}"}
    try:
        with runtime_git.crossing(Path(source), wait_s=boundary_wait_s, note="node rollback"):
            if current := _answers_as(state, new_sha):
                return {"action": "swapped", "detail": f"node {current['pid']} runs {new_sha[:12]}"}
            rollback = _rollback_pointer(state, pin, new_sha)
    except Busy:
        return {"action": "deferred", "detail": "node replacement waits for the runtime cutover"}
    if rollback is None:
        return {"action": "failed", "detail": "the previous node runtime is refused"}
    return _rollback_answer(state, rollback, new_sha, wait_s)


def _rollback_pointer(state: Path, pin: dict, new_sha: str) -> tuple[Path, str] | None:
    now = node_supervise.running(state)
    before = pin or (now.get("previous") if now.get("sha256") == new_sha else now) or {}
    old, sha = Path(str(before.get("binary") or "")), str(before.get("sha256") or "")
    source = runtime_store.read_state().get("checkout")
    try:
        node_supervise._admitted(old, sha, Path(str(source or "")))
    except (OSError, ValueError, RuntimeError):
        return None
    node_supervise.point(state, old, sha)
    return old, sha


def _rollback_answer(state: Path, rollback: tuple[Path, str], new_sha: str, wait_s: float) -> dict:
    _, sha = rollback
    back = _until(time.monotonic() + wait_s, lambda: _answers_as(state, sha))
    return {"action": "failed", "detail": f"the new node ({new_sha[:12]}) did not answer; "
            + (f"node {back['pid']} runs the previous binary again" if back else "the previous binary did not answer either")}


def _rolled_back(state: Path, pin: dict, new_sha: str, wait_s: float, *, direct: bool = False) -> dict:
    rollback = _rollback_pointer(state, pin, new_sha)
    if rollback is None:
        return {"action": "failed", "detail": "the previous node runtime is refused"}
    if direct:
        stop_node(state)
        _start_supervisor(state, [])
    return _rollback_answer(state, rollback, new_sha, wait_s)


def smoke(prefix: Path, *, wait_s: float = START_WAIT_S) -> dict:
    """Start the runtime's node binary on a throwaway state directory, ask it `hello`, stop it; its health, or NodeUnavailable."""
    binary = node_binary.verified(prefix)
    with tempfile.TemporaryDirectory(prefix="mlsn") as temporary:
        state = Path(temporary)
        child = start_process([str(binary), "run", "--state", str(state)], stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            said = spawned.wait(child, lambda: node_health(state), what=f"the node {binary}",
                                timeout_s=wait_s, watch=spawned.Watch(poll_s=POLL_S))
        except spawned.StartFailed as exc:
            raise NodeUnavailable(f"{binary} did not answer hello within {wait_s:.0f}s"
                                  if exc.kind == "timeout" else str(exc)) from exc
        finally:
            node_supervise.end(child, state)
    return said


def _command(run):
    """A handler that prints the JSON of ``run(state, args)`` and exits 1 on a failure."""
    def handler(args) -> int:
        state = Path(args.state) if args.state else default_state()
        try:
            result = run(state, args)
        except OSError as exc:
            warn(f"node: {exc}")
            return 1
        say(json.dumps(result))
        return 1 if result.get("action") == "failed" else 0
    return handler


STATE = [flag("--state", default="", help="the node's state directory (default: the machine's)")]
GROUP = Group("poolhouse.node_launch", "Find, start, stop and move the node of this device.")
GROUP.add("ensure", _command(lambda state, args: ensure_node(state, extra=args.arg)), help="start the node if it is not answering",
          options=[*STATE, flag("--arg", action="append", default=[], help="an argument for `poolhouse-node run`")])
GROUP.add("status", _command(lambda state, args: status(state)), help="the node's health", options=STATE)
GROUP.add("stop", _command(lambda state, args: {"stopped": stop_node(state)}), help="stop the node and its supervisor", options=STATE)
GROUP.add("swap", _command(lambda state, args: swap(state)), help="move the node onto the selected runtime's binary", options=STATE)
GROUP.add("supervise", lambda args: node_supervise.supervise(Path(args.state) if args.state else default_state(), args.arg, args.parent_pid),
          help="keep the node running (what `ensure` starts)",
          options=[*STATE, flag("--arg", action="append", default=[], help="an argument for `poolhouse-node run`"),
                   flag("--parent-pid", type=int, default=0, help="stop when this process has exited")])

main = GROUP.run


if __name__ == "__main__":
    raise SystemExit(main())
