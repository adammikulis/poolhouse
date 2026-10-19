"""Shared pieces for the sandbox tests: a fixture that skips without a real Seatbelt, a policy
for running this interpreter, and a loopback listener."""

from __future__ import annotations

import contextlib
import os
import shutil
import socket
import sys
import tempfile
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from poolhouse import sandbox
from poolhouse.sandbox import AllowUnsandboxed, Limits, Net, Policy
from poolhouse.sandbox.backend import LOST_REFUSAL_LOG, NO_REFUSAL_LOG, Availability, Wrapped
from poolhouse.sandbox.policies import SYSTEM_EXEC
from poolhouse.sandbox.seatbelt import Seatbelt


class UnreadableBackend:
    """A backend that confines nothing and cannot say what was refused."""

    name = "unreadable"

    def available(self):
        return Availability(True)

    def wrap(self, argv, policy):
        return Wrapped(list(argv), tag="unreadable")

    def watch(self, *_tag):
        return None

    def denials(self, tag, since):
        return None

    def unreadable(self, tag):
        return LOST_REFUSAL_LOG

    def finish(self, *_tag):
        return None


class UnloggedBackend(UnreadableBackend):
    """A backend that confines nothing and keeps no refusal log by construction."""

    name = "unlogged"

    def unreadable(self, tag):
        return NO_REFUSAL_LOG


class RecordingBackend(UnreadableBackend):
    """A backend that remembers which runs it was asked to watch and which it was told had ended."""

    name = "recording"

    def __init__(self):
        self.watched, self.finished = [], []

    def watch(self, tag):
        self.watched.append(tag)

    def finish(self, tag):
        self.finished.append(tag)


@contextlib.contextmanager
def kernel_log(alone: bool = False) -> Iterator[None]:
    """Hold the machine-wide lock around a test that reads Seatbelt refusals (shared) or makes the kernel
    cut one off (alone): a cut report reaches every refusal stream on the machine, and every run being
    watched meanwhile becomes unreadable. A test waiting to be alone holds back the ones that follow."""
    import fcntl

    base = Path(tempfile.gettempdir()) / "poolhouse-seatbelt-log"
    gate, lock = (os.open(f"{base}.{name}", os.O_RDWR | os.O_CREAT, 0o600) for name in ("gate", "lock"))
    mode = fcntl.LOCK_EX if alone else fcntl.LOCK_SH
    try:
        fcntl.flock(gate, mode)
        fcntl.flock(lock, mode)
        if not alone:
            fcntl.flock(gate, fcntl.LOCK_UN)
        yield
    finally:
        os.close(gate)
        os.close(lock)


def _held_backend(alone: bool) -> Iterator[Seatbelt]:
    backend = Seatbelt()
    state = backend.available()
    if not state.ok:
        pytest.skip(state.reason)
    with kernel_log(alone):
        yield backend


@pytest.fixture
def seatbelt() -> Iterator[Seatbelt]:
    """The real Seatbelt backend, or a skip where ``sandbox-exec`` does not exist; no test that makes the
    kernel cut a refusal report off runs meanwhile."""
    yield from _held_backend(False)


@pytest.fixture
def seatbelt_alone() -> Iterator[Seatbelt]:
    """The real Seatbelt backend, with no other test that reads refusals running meanwhile."""
    yield from _held_backend(True)


@pytest.fixture
def held() -> Callable[..., Policy]:
    """The kit's `policy`, for a test that takes it as a fixture."""
    return policy


def no_sandbox_here() -> AllowUnsandboxed | None:
    """None where a sandbox exists; elsewhere the named opt-out, so a test of something else
    can still start its server."""
    if sandbox.backend().available().ok:
        return None
    return AllowUnsandboxed("this test host has no sandbox and the test is not about one")


def require_native_sandbox() -> None:
    """Skip native success tests when the host cannot run a confined process."""
    state = sandbox.backend().available()
    if not state.ok:
        pytest.skip(state.reason)
    executable = os.path.realpath(shutil.which("true") or "/bin/true")
    held = Policy("native-probe", exec=(executable,), env={},
                  limits=Limits(wall_seconds=3, output_bytes=4096))
    result = sandbox.run([executable], held, diagnose="never")
    error = result.stderr.lower()
    namespace_denied = "namespace" in error and any(
        message in error for message in ("operation not permitted", "permission denied",
                                        "not allow non-privileged user namespaces"))
    if not result.ok and namespace_denied:
        pytest.skip(f"native sandbox namespace unavailable: {result.stderr.strip()[:300]}")
    assert result.ok, f"native sandbox probe failed: {result.stderr}"


def runtime_reads() -> list[str]:
    """What this interpreter needs read access to: its prefixes and Homebrew's libraries."""
    found = {os.path.realpath(sys.prefix), os.path.realpath(sys.base_prefix)}
    if Path("/opt/homebrew").is_dir():
        found.add("/opt/homebrew")
    return sorted(found)


def policy(*, read: list[Path | str] = (), write: list[Path | str] = (), net: Net | None = None,  # noqa: PLR0913
           env: dict[str, str] | None = None, limits: Limits | None = None,
           exec_: list[str] = (), python: bool = False,
           name: str = "test") -> Policy:
    """A policy that reads ``read``, writes ``write`` and runs the system tools; with
    ``python`` it also runs this interpreter."""
    runs = [*SYSTEM_EXEC, *exec_]
    reads = [str(p) for p in read]
    if python:
        runs.append(os.path.realpath(sys.executable))
        reads += runtime_reads()
    return Policy(name, read=tuple(reads), write=tuple(str(p) for p in write), exec=tuple(runs),
                  net=net or Net.deny(), env={"PATH": "/usr/bin:/bin", **(env or {})},
                  limits=limits or Limits(wall_seconds=30))


class Listener:
    """A loopback server on an ephemeral port that answers ``pong`` to every connection."""

    def __init__(self) -> None:
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.accepted = 0
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.accepted += 1
            conn.sendall(b"pong")
            conn.close()

    def close(self) -> None:
        self.sock.close()


@pytest.fixture
def listener() -> Iterator[Listener]:
    server = Listener()
    yield server
    server.close()


CONNECT = """
import socket, sys
s = socket.socket()
s.settimeout(3)
try:
    s.connect(("127.0.0.1", {port}))
    print("connected", s.recv(4).decode())
except OSError as e:
    print("refused", e.errno)
"""
