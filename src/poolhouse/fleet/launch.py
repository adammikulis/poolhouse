"""``poolhouse`` -- the thing a person double-clicks."""

from __future__ import annotations

import argparse
import contextlib
import socket
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any

from poolhouse import runtime, spawned
from poolhouse.http import Options, ServerError, request_json
from poolhouse.log import say, warn

from . import discovery, launch_open, wsl_network, wsl_registration, wsl_startup
from .daemon_control import ControlError, launch_of, request_replacement
from .discovery import (
    DEFAULT_PORT as DISCOVERY_PORT,  # noqa: F401  (keeps ports in view)
    memberships,
)
from .handover import claim, finish
from .measuring import same_commit
from .pool_modes import notice
from .runtime_paths import default_root
from .updates import state
from .wsl import WSLError, prepare, replace_running, start

__all__ = ["already_running", "last_screen", "main", "wait_for_health"]

HTTP_PORT = 8770


#: /health carries the device report, and a first telemetry sample on a loaded machine
#: takes over a second. A port with nothing on it is refused at once either way.
HEALTH_TIMEOUT_S = 5.0


def _health(port: int, timeout: float = HEALTH_TIMEOUT_S) -> dict[str, Any] | None:
    """What the poolhouse daemon on ``port`` says about itself, or None."""
    try:
        said = request_json(f"http://127.0.0.1:{port}/health", options=Options(timeout=timeout))
    except ServerError:
        return None
    return said if isinstance(said, dict) else {}


SLOW_HEALTH_TIMEOUT_S = 30.0
SLOW_HEALTH_TRIES = 4
"""How a held port is asked again after one missed answer: patiently, since a loaded daemon is slow, not absent."""


def port_held(port: int, timeout: float = 2.0) -> bool:
    """Whether something owns the port. Only a refused connection says it is free; a slow or odd answer does not."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except ConnectionRefusedError:
        return False
    except TimeoutError:
        # Windows retransmits the SYN to a closed loopback port for about two seconds before
        # it refuses, so a free port can time out here. Whether it can be bound says which.
        return not _can_bind(port)
    except OSError:
        return True


def _can_bind(port: int) -> bool:
    """Whether this machine can listen on the loopback port right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _held_health(port: int) -> dict[str, Any] | None:
    for _ in range(SLOW_HEALTH_TRIES):
        said = _health(port, SLOW_HEALTH_TIMEOUT_S)
        if said is not None:
            return said
    return None


def already_running(port: int = HTTP_PORT) -> dict[str, Any] | None:
    """A healthy daemon already on this port, or None."""
    return _health(port)


def wait_for_health(port: int = HTTP_PORT, *, seconds: float = 20.0,
                    child: spawned.Child | None = None, clean_exit_ends: bool = True) -> dict[str, Any] | None:
    """Block until the daemon answers, or give up. Returns its health.

    With ``child``, the process started to serve it, a child that ends first raises
    `spawned.StartFailed` saying how, instead of leaving the wait to run out. A launcher that
    hands over to another process passes ``clean_exit_ends=False``: only an error ends the wait."""
    if child is not None:
        try:
            return spawned.wait(child, lambda: _health(port), what="the daemon", timeout_s=seconds,
                                watch=spawned.Watch(poll_s=0.15, clean_exit_ends=clean_exit_ends))
        except spawned.StartFailed as exc:
            if exc.kind != "timeout":
                raise
            return None
    deadline = time.time() + seconds
    while time.time() < deadline:
        found = _health(port)
        if found is not None:
            return found
        time.sleep(0.15)
    return None


def last_screen(name: str, *, track: str = "", port: int = HTTP_PORT) -> list[str]:
    """The lines an installer ends on: what this machine runs, and how to open its page."""
    said = state()
    version = said["version"] or "?"
    commit = said["commit"]
    lines = [f"  machine     {name}",
             f"  running     {version}" + ("" if commit in ("", f"v{version}") else f"  {commit}")]
    groups = memberships()
    if groups:
        lines.append(f"  pool     {groups[0].group}")
    url = f"http://127.0.0.1:{port}/ui/"
    if already_running(port) is None:
        return [*lines, "",
                f"  next        poolhouse                        -- starts it and opens {url}",
                "              poolhouse-pool join --persist   -- joins the pool and starts "
                "it at every login"]
    return [*lines,
            f"  open        {url}",
            f"  updates     {'follows ' + track if track else 'releases'}, "
            "whenever nothing is running here",
            "",
            "  next        poolhouse-pool status    -- other devices in the pool"]


def _root(arguments: list[str]) -> Path:
    for index, arg in enumerate(arguments):
        if arg.startswith("--root="):
            return Path(arg.partition("=")[2]).expanduser()
        if arg == "--root" and index + 1 < len(arguments):
            return Path(arguments[index + 1]).expanduser()
    return default_root()


def _wait_for_exit(port: int, seconds: float = 20.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not port_held(port, 0.5):
            return True
        time.sleep(0.15)
    return False


def _open_when_ready(port: int, browser: bool, stopped: threading.Event,
                     root: Path | None = None) -> None:
    waiting = time.monotonic() + 20.0
    notified = False
    while not stopped.is_set():
        if _health(port) is not None:
            if browser:
                webbrowser.open(launch_open.page_url(port, root))
            return
        if not notified and time.monotonic() >= waiting:
            say("Poolhouse is still starting; waiting for its web interface.")
            notified = True
        stopped.wait(0.15)


def _arguments(argv):
    ap = argparse.ArgumentParser(
        prog="poolhouse",
        description="Start poolhouse on this machine and open it in your browser.")
    ap.add_argument("--port", type=int, default=HTTP_PORT)
    ap.add_argument("--no-browser", action="store_true",
                    help="start the daemon but do not open a browser")
    ap.add_argument('--restart', action='store_true',
                    help='restart the daemon while preserving jobs and independent model workers')
    return ap.parse_known_args(argv)


def _reuse(running: dict[str, Any], known: argparse.Namespace, rest: list[str]) -> int:
    groups = memberships()
    say(notice(running.get("pool_mode") or (groups[0].mode if groups else "dev")))
    say(f"Poolhouse is already running as '{running.get('name', '?')}'.")
    if not known.no_browser:
        webbrowser.open(launch_open.page_url(known.port, _root(rest)))
    say(f"  http://127.0.0.1:{known.port}/ui/")
    return 0


def _replace(known: argparse.Namespace, rest: list[str], running: dict[str, Any], expected: str,
             release: Callable[[], None]) -> int | None:
    """Ask the running daemon to stop and put another in its place. On Windows the daemon is the WSL one: its exit is waited for and
    None comes back, for the caller to start it. Elsewhere the successor is started and proved here (`handover`), so the exit status
    says whether a daemon answers."""
    previous = None
    try:
        if sys.platform == "win32":
            replace_running(rest, known.port, running, expected, restart="preserve")
        else:
            previous = launch_of(_root(rest), known.port)
            answer = request_replacement(_root(rest), known.port, running, expected, restart="preserve")
            if isinstance(answer, dict) and answer.get("preserved"):
                say(f"Restart preserves jobs: {answer['preserved']}")
    except (ControlError, ServerError, WSLError, OSError, ValueError) as exc:
        warn(str(exc))
        return 1
    while not _wait_for_exit(known.port):
        say("The previous daemon was told to stop and is still exiting; waiting for it.")
    if sys.platform == "win32":
        return None
    code = finish(_daemon_arguments(known, rest), previous, lambda: _health(known.port), release)
    if code == 0 and not known.no_browser:
        webbrowser.open(launch_open.page_url(known.port, _root(rest)))
    return code


def _daemon_arguments(known: argparse.Namespace, rest: list[str]) -> list[str]:
    arguments = ["--port", str(known.port), *rest]
    if not known.no_browser:
        arguments.append("--initial-setup")
    return arguments


def _settle(known: argparse.Namespace, rest: list[str], release: Callable[[], None]) -> int | None:
    """What the launcher does about the daemon already on the port: an exit status when that settles it, None when none runs."""
    running = already_running(known.port)
    if running is None and port_held(known.port):
        running = _held_health(known.port)
        if running is None:
            warn(f"Something holds port {known.port} and did not answer /health; not starting a second daemon. Retry shortly.")
            return 1
    if running is None:
        return None
    expected = str(state().get("commit") or "")
    if known.restart or not same_commit(str(running.get("commit") or ""), expected):
        return _replace(known, rest, running, expected, release)
    return _reuse(running, known, rest)


def main(argv: list[str] | None = None, *,
         daemon_main: Callable[[list[str]], int] | None = None) -> int:
    daemon_main is None and runtime.forward("poolhouse.fleet.launch", list(sys.argv[1:] if argv is None else argv))
    known, rest = _arguments(argv)

    linux_executable = None
    try:
        with contextlib.ExitStack() as held:
            if sys.platform != "win32":
                held.enter_context(claim(_root(rest), known.port))
            settled = _settle(known, rest, held.close)
    except ControlError as exc:
        warn(str(exc))
        return 1
    if settled is not None:
        return settled

    if sys.platform == "win32" and linux_executable is None:
        try:
            linux_executable = prepare()
        except (WSLError, OSError) as exc:
            warn(str(exc))
            return 1

    stopped = threading.Event()
    threading.Thread(target=_open_when_ready,
                     args=(known.port, not known.no_browser, stopped, _root(rest)), daemon=True).start()
    arguments = _daemon_arguments(known, rest)
    if sys.platform == "win32":
        try:
            return start(arguments, executable=linux_executable)
        except (WSLError, OSError) as exc:
            warn(str(exc))
            return 1
        finally:
            stopped.set()
    if wsl_startup.guest() and wsl_registration.configuration() is None and (note := wsl_network.nat_notice(discovery.primary_ip())):
        warn(note)
    if daemon_main is None:
        from .daemon import run as daemon_main

    try:
        return daemon_main(arguments)
    finally:
        stopped.set()


if __name__ == "__main__":
    raise SystemExit(main())
