"""Run a command under a policy: choose the backend, start it in its own process group, hold it
to its limits, and report what the sandbox refused."""

from __future__ import annotations

import array
import contextlib
import logging
import os
import selectors
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

if os.name == "posix":
    import fcntl
    import resource
    import termios
else:
    resource = None

from poolhouse.platform import start_process
from poolhouse.sandbox.backend import (
    SILENT_REFUSAL_LOG,
    UNREPORTED_FAILURE_LOG,
    Backend,
    SandboxUnavailable,
)
from poolhouse.sandbox.bubblewrap import Bubblewrap
from poolhouse.sandbox.hold import Hold
from poolhouse.sandbox.io import CHUNK, IO, Control
from poolhouse.sandbox.policy import AllowUnsandboxed, Limits, Policy, PolicyError
from poolhouse.sandbox.seatbelt import Seatbelt

__all__ = ["Events", "Result", "SandboxViolation", "backend", "run", "wrapped"]

logger = logging.getLogger("poolhouse.sandbox")

Events = Callable[[str, dict[str, object]], None]
"""``on(event, fields)`` -- what happened in the sandbox, and what came with it."""


WEIGHT = ("network", "process-exec", "file-write", "file-read-data")
"""Operations in the order a refusal is worth reporting first."""


class SandboxViolation(RuntimeError):
    """The sandbox refused something the command tried."""


@dataclass(slots=True)
class Result:
    """How a run ended."""

    argv: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    truncated: bool = False
    sandboxed: bool = True
    backend: str = ""
    seconds: float = 0.0
    denials: list[dict[str, str]] = field(default_factory=list)
    denials_unavailable: bool = False
    """The sandbox could not say what it refused, so an empty ``denials`` does not mean the run was clean."""
    denials_detail: str = ""
    """Why the refusals could not be known; empty when they could."""
    cancelled: bool = False
    failure_unreported: bool = False
    """The run exited non-zero and the refusal log was read and carried no report of it; that does not make ``denials_unavailable``."""

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.cancelled and not self.truncated

    def check(self) -> Result:
        """This result, or `SandboxViolation` saying what was refused or that the refusals could not be read."""
        if self.denials_unavailable:
            raise SandboxViolation(
                f"what the sandbox refused running {Path(self.argv[0]).name} could not be read ({self.denials_detail})")
        if self.denials:
            first = min(self.denials, key=lambda d: next(
                (i for i, prefix in enumerate(WEIGHT) if d["operation"].startswith(prefix)),
                len(WEIGHT)))
            raise SandboxViolation(
                f"the sandbox refused {first['operation']} {first['target']} "
                f"({len(self.denials)} refusal(s) running {Path(self.argv[0]).name})")
        return self


def backend() -> Backend:
    """The backend this platform uses; one that reports unavailable when there is none."""
    return Seatbelt() if sys.platform == "darwin" else Bubblewrap()


def wrapped(argv: Sequence[str], policy: Policy, *, via: Backend | None = None,
            unsandboxed: AllowUnsandboxed | None = None) -> tuple[list[str], str, Backend | None]:
    """``(argv, tag, backend)`` -- the backend is None when running without one -- that runs ``argv`` under ``policy``. When no backend can
    hold it, `SandboxUnavailable` unless ``unsandboxed`` names the reason to run without one."""
    policy = policy.validated()
    if unsandboxed is not None and (policy.deny_read or policy.deny_write):
        raise PolicyError("protected path exclusions cannot run without a sandbox")
    chosen = via or backend()
    state = chosen.available()
    if state.ok:
        made = chosen.wrap(argv, policy)
        chosen.watch(made.tag)
        return made.argv, made.tag, chosen
    if unsandboxed is None:
        raise SandboxUnavailable(
            f"{policy.name}: no sandbox is available ({chosen.name}: {state.reason}); the "
            f"command was not run. Pass AllowUnsandboxed(reason) to run it without one.")
    logger.warning("running WITHOUT a sandbox (%s): %s", unsandboxed.reason, argv[0])
    return list(argv), "", None


def _limits(limits: Limits) -> Callable[[], None]:
    if resource is None:
        raise SandboxUnavailable("process resource limits require a POSIX platform; command was not run")
    def apply() -> None:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        for which, value in ((resource.RLIMIT_CPU, limits.cpu_seconds),
                             (resource.RLIMIT_FSIZE, limits.file_bytes),
                             (resource.RLIMIT_NOFILE, limits.open_files)):
            if value is not None:
                resource.setrlimit(which, (value, value))
    return apply


class _Capture(threading.Thread):
    def __init__(self, stream: Any, cap: int | None, control: Control, *, deliver: bool = False) -> None:
        super().__init__(daemon=True)
        self.stream, self.cap, self.control, self.deliver = stream, cap, control, deliver
        self.data = bytearray()
        self.over = False
        self.finished = threading.Event()
        self.drain = 0

    def finish(self) -> None:
        try:
            pending = array.array("i", [0])
            fcntl.ioctl(self.stream.fileno(), termios.FIONREAD, pending, True)
            self.drain = max(0, pending[0]) + CHUNK
        except (OSError, ValueError) as error:
            self.control.cancel(error)
        finally:
            self.finished.set()

    def run(self) -> None:
        descriptor = self.stream.fileno()
        try:
            os.set_blocking(descriptor, False)
            with selectors.DefaultSelector() as reader:
                reader.register(descriptor, selectors.EVENT_READ)
                while True:
                    if self.finished.is_set() and self.drain <= 0:
                        return
                    if not reader.select(.01):
                        if self.finished.is_set():
                            return
                        continue
                    try:
                        chunk = os.read(descriptor, min(CHUNK, self.drain) if self.finished.is_set() else CHUNK)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        return
                    if self.finished.is_set():
                        self.drain -= len(chunk)
                    room = len(chunk) if self.cap is None else max(0, self.cap - len(self.data))
                    self.data += chunk[:room]
                    if room < len(chunk):
                        self.over = True
                        self.control.cancel()
                        return
                    if self.deliver:
                        self.control.emit(chunk)
        except (OSError, ValueError) as error:
            self.control.cancel(error)


class _Input(threading.Thread):
    def __init__(self, stream: Any, value: bytes, control: Control) -> None:
        super().__init__(daemon=True)
        self.stream, self.value, self.control = stream, value, control
        self.finished = threading.Event()

    def finish(self) -> None:
        self.finished.set()

    def run(self) -> None:
        try:
            descriptor, offset = self.stream.fileno(), 0
            os.set_blocking(descriptor, False)
            with selectors.DefaultSelector() as writer:
                writer.register(descriptor, selectors.EVENT_WRITE)
                while offset < len(self.value) and not self.finished.is_set() and not self.control.stopped():
                    if writer.select(.01):
                        try:
                            offset += os.write(descriptor, self.value[offset:offset + CHUNK])
                        except BlockingIOError:
                            continue
        except BrokenPipeError:
            pass
        except (OSError, ValueError) as error:
            self.control.cancel(error)
        finally:
            self.stream.close()


def _kill_group(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGKILL)


def _wait(proc: subprocess.Popen[bytes], deadline: float | None, control: Control, *captures: _Capture) -> bool:
    """Wait for ``proc`` to exit, or for the deadline or an output cap; True on the deadline."""
    while proc.poll() is None:
        if control.stopped():
            return False
        if deadline is not None and time.time() >= deadline:
            return True
        if any(c.over for c in captures):
            return False
        time.sleep(0.01)
    return False


class _NotFollowed(SandboxUnavailable):
    """The backend could not follow the process a run started as; the command was not run."""


def _reap(proc: subprocess.Popen[bytes]) -> None:
    """End ``proc`` and its group, wait for it and close its pipes."""
    _kill_group(proc.pid)
    with contextlib.suppress(ProcessLookupError, PermissionError):
        proc.kill()
    proc.wait()
    for pipe in (proc.stdin, proc.stdout, proc.stderr):
        if pipe is not None:
            with contextlib.suppress(OSError):
                pipe.close()


def _start(full: Sequence[str], argv: Sequence[str], policy: Policy, cwd: str | os.PathLike[str],  # noqa: PLR0913 - one argument per part of the start
           control: Control, follow: Callable[[int], None] | None = None) -> subprocess.Popen[bytes]:
    """Start ``full`` in a process group of its own, with a pipe for its input when ``piped``. With ``follow``,
    the process runs nothing until ``follow(pid)`` has returned; when that raises, the process is ended
    before it runs anything and `_NotFollowed` is raised."""
    limits = _limits(policy.limits)
    pipes = control.io.pipes
    passed = () if pipes is None else pipes.check()
    def followed(pid: int) -> None:
        if control.stopped():
            raise InterruptedError("The sandbox process was cancelled before execution")
        if follow is not None:
            follow(pid)
        if control.stopped():
            raise InterruptedError("The sandbox process was cancelled before execution")
    hold = None if follow is None else Hold(followed)
    proc, failure = None, None
    def prepare() -> None:
        limits()
        if pipes is not None:
            pipes.check(inherited=True)
        if control.stopped():
            raise InterruptedError("The sandbox process was cancelled before execution")
        if hold is not None:
            hold.wait()
    try:
        proc = start_process(
            full, stdin=subprocess.PIPE if control.io.input is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=HOOKS.environment(policy.env), cwd=cwd,
            preexec_fn=prepare, close_fds=True, pass_fds=passed)
    except (OSError, subprocess.SubprocessError) as exc:
        failure = exc
    finally:
        if hold is not None:
            hold.finish()
    if hold is not None and hold.error is not None:
        if proc is not None:
            _reap(proc)
        if isinstance(hold.error, InterruptedError):
            raise InterruptedError("The sandbox process was cancelled before execution") from hold.error
        raise _NotFollowed(f"{policy.name}: the processes of {argv[0]} could not be followed: {hold.error}") from hold.error
    if proc is None:
        if control.stopped():
            raise InterruptedError("The sandbox process was cancelled before execution")
        raise SandboxUnavailable(f"{policy.name}: could not start {argv[0]}: {failure}") from failure
    return proc


def _complete(proc: subprocess.Popen[bytes], argv: Sequence[str], policy: Policy, control: Control,
              began: float) -> Result:
    """Feed the child, bound its output and invalidate admission before ending its group."""
    limits = policy.limits
    out = _Capture(proc.stdout, limits.output_bytes, control, deliver=True)
    err = _Capture(proc.stderr, limits.output_bytes, control)
    sender = None if control.io.input is None else _Input(proc.stdin, control.io.input, control)
    streams = [out, err, *([] if sender is None else [sender])]
    timed_out, capped, cancelled = False, False, False
    try:
        for stream in streams:
            stream.start()
        deadline = None if limits.wall_seconds is None else began + limits.wall_seconds
        timed_out = _wait(proc, deadline, control, out, err)
        capped = out.over or err.over
        cancelled = control.stopped() and not capped
    finally:
        control.cancel()
        _kill_group(proc.pid)
        proc.wait()
        for stream in streams:
            stream.finish()
        for stream in streams:
            if stream.ident is not None:
                stream.join()
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            if pipe is not None:
                pipe.close()
    capped = out.over or err.over
    if control.failure is not None:
        raise control.failure
    return Result(list(argv), proc.returncode, bytes(out.data).decode("utf-8", "replace"),
                  bytes(err.data).decode("utf-8", "replace"), timed_out, capped,
                  cancelled=cancelled and not timed_out, seconds=round(time.time() - began, 3))


def run(argv: Sequence[str], policy: Policy, *,  # noqa: PLR0913 - one keyword per choice
        unsandboxed: AllowUnsandboxed | None = None,
        cwd: str | os.PathLike[str] | None = None, io: IO | None = None,
        on_event: Events | None = None, via: Backend | None = None,
        diagnose: str = "failure") -> Result:
    """Run ``argv`` under ``policy``. The environment is exactly ``policy.env``. The process
    starts in a group of its own; the wall-clock limit or an output over the cap kills that
    group and nothing else. A run that fails with the sandbox having refused something carries
    the refusals in ``Result.denials`` and reports them to ``on_event``; when the backend cannot say
    what it refused, ``Result.denials_unavailable`` is set and ``sandbox.denials-unavailable`` is reported."""
    if io is not None and type(io) is not IO:
        raise ValueError("Sandbox process channels require parent-owned IO")
    control = (io or IO()).checked()
    if unsandboxed is not None and io is not None:
        raise PolicyError("Process channels require an operating-system sandbox")
    if control.stopped():
        return Result(list(argv), -1, cancelled=True)
    emit = _emitter(on_event)
    prepared = False
    try:
        full, tag, chosen = wrapped(argv, policy, via=via, unsandboxed=unsandboxed)
        prepared = True
    except SandboxUnavailable as exc:
        emit("sandbox.unavailable", severity="warning", command=str(argv[0]),
             policy=policy.name, reason=str(exc))
        raise
    finally:
        if not prepared:
            control.cancel()
    name = chosen.name if chosen else "none"
    if chosen is None:
        emit("sandbox.unsandboxed", severity="warning", command=str(argv[0]),
             policy=policy.name, reason=unsandboxed.reason if unsandboxed else "")
    if cwd is None:
        cwd = (policy.write or policy.read or ("/",))[0]
    began = time.time()
    try:
        proc = _start(full, argv, policy, cwd, control, None if chosen is None else partial(chosen.started, tag))
        result = _complete(proc, argv, policy, control, began)
        result.sandboxed, result.backend = chosen is not None, name
        if chosen is not None:
            _read_denials(chosen, tag, began, diagnose, result)
    except InterruptedError:
        result = Result(list(argv), -1, sandboxed=chosen is not None, backend=name, cancelled=True)
    except _NotFollowed as exc:
        emit("sandbox.unavailable", severity="warning", command=str(argv[0]), policy=policy.name, reason=str(exc))
        raise
    finally:
        control.cancel()
        if chosen is not None:
            chosen.finish(tag)
    if result.denials_unavailable:
        emit("sandbox.denials-unavailable", severity="warning", command=str(argv[0]), policy=policy.name,
             backend=name, detail=result.denials_detail)
    if result.failure_unreported:
        emit("sandbox.failure-unreported", severity="notice", command=str(argv[0]), policy=policy.name,
             backend=name, detail=UNREPORTED_FAILURE_LOG)
    if result.denials:
        emit("sandbox.denied", severity="warning", command=str(argv[0]), policy=policy.name,
             count=len(result.denials), first=result.denials[0], backend=name)
    if result.timed_out:
        emit("sandbox.timeout", severity="notice", command=str(argv[0]), policy=policy.name,
             seconds=policy.limits.wall_seconds)
    return result


def _read_denials(chosen: Backend, tag: str, began: float, diagnose: str, result: Result) -> None:
    """Put what the sandbox refused the run in ``result``."""
    if _diagnose(diagnose, result):
        found = chosen.denials(tag, began)
        result.denials = found or []
        result.denials_detail = ""
        if found is None:
            result.denials_detail = chosen.unreadable(tag)
        elif not found and _says_refused(result):
            result.denials_detail = SILENT_REFUSAL_LOG
        elif not found and result.returncode != 0 and not result.timed_out:
            result.failure_unreported = True
        result.denials_unavailable = found is None or bool(result.denials_detail)


def _says_refused(result: Result) -> bool:
    """Whether the run failed and its own output says the system did not permit something; the sandbox is not the only thing that says so."""
    return result.returncode != 0 and "operation not permitted" in result.stderr.lower()


def _diagnose(when: str, result: Result) -> bool:
    if when == "always":
        return True
    return when == "failure" and (result.returncode != 0 or bool(result.stderr))


class Hooks:
    """What watches every run: ``events`` hears each event, ``scrub`` filters the environment a
    child gets. Set by the node's sentinel (`poolhouse.sentinel.default`) so that sandbox imports
    nothing from it."""

    def __init__(self) -> None:
        self.events: Events | None = None
        self.scrub: Callable[[Mapping[str, str]], dict[str, str]] | None = None

    def watch(self, events: Events | None,
              scrub: Callable[[Mapping[str, str]], dict[str, str]] | None) -> None:
        self.events, self.scrub = events, scrub

    def environment(self, env: Mapping[str, str]) -> dict[str, str]:
        return self.scrub(env) if self.scrub is not None else dict(env)


HOOKS = Hooks()


def _emitter(on_event: Events | None) -> Callable[..., None]:
    """Reports to the caller's ``on_event`` and to whatever watches every run (the sentinel)."""
    def emit(event: str, **fields: object) -> None:
        for call in (HOOKS.events, on_event):
            if call is not None:
                with contextlib.suppress(Exception):
                    call(event, dict(fields))
    return emit

