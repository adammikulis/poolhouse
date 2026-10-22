"""Owned pipe endpoints and bounded process output delivery."""
from __future__ import annotations

import os
import queue
import stat
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field

if os.name == "posix":
    import fcntl

CHUNK = 65536


def _pipe(descriptor: int, direction: int, *, inherited: bool = False) -> tuple[int, int]:
    if type(descriptor) is not int or descriptor <= 2 or os.name != "posix":
        raise ValueError("Sandbox IPC requires owned nonstandard pipe descriptors")
    info = os.fstat(descriptor)
    if (not stat.S_ISFIFO(info.st_mode) or info.st_uid != os.getuid()
            or fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE != direction
            or (not inherited and not fcntl.fcntl(descriptor, fcntl.F_GETFD) & fcntl.FD_CLOEXEC)):
        raise ValueError("Sandbox IPC requires private directional pipes with close-on-exec")
    return info.st_dev, info.st_ino


_OWNER = object()


@dataclass(frozen=True, slots=True, init=False)
class Pipes:
    read: int
    write: int
    parent_read: int
    parent_write: int
    identities: tuple[tuple[int, int], ...]
    _owner: object

    def __init__(self) -> None:
        raise TypeError("Sandbox pipes are created by Pipes.opened")

    @classmethod
    @contextmanager
    def opened(cls) -> Iterator[Pipes]:
        """Own both directions of two independent process channels."""
        if sys.platform != "darwin":
            raise ValueError("This sandbox backend cannot retain IPC descriptors")
        descriptors: list[int] = []
        made = None
        identities: list[tuple[int, int]] = []
        try:
            for _ in range(2):
                descriptors.extend(os.pipe())
            for index, descriptor in enumerate(descriptors):
                os.set_inheritable(descriptor, False)
                identities.append(_pipe(descriptor, os.O_RDONLY if index % 2 == 0 else os.O_WRONLY))
            made = object.__new__(cls)
            for key, value in (("read", descriptors[0]), ("write", descriptors[3]),
                               ("parent_read", descriptors[2]), ("parent_write", descriptors[1]),
                               ("identities", tuple(identities)), ("_owner", _OWNER)):
                object.__setattr__(made, key, value)
            made.check()
            yield made
        finally:
            if made is not None:
                object.__setattr__(made, "_owner", None)
            for index, descriptor in enumerate(descriptors):
                with suppress(OSError):
                    info = os.fstat(descriptor)
                    if index >= len(identities) or (info.st_dev, info.st_ino) == identities[index]:
                        os.close(descriptor)

    def check(self, *, inherited: bool = False) -> tuple[int, int]:
        """Verify factory ownership and endpoint identities at the process handoff."""
        if getattr(self, "_owner", None) is not _OWNER:
            raise ValueError("Sandbox IPC requires live factory-owned channels")
        descriptors = (self.read, self.parent_write, self.parent_read, self.write)
        if len(set(descriptors)) != 4:
            raise ValueError("The retained sandbox IPC endpoints changed")
        for index, descriptor in enumerate(descriptors):
            if inherited and index in (1, 2):
                continue
            actual = _pipe(descriptor, os.O_RDONLY if index % 2 == 0 else os.O_WRONLY, inherited=inherited)
            if actual != self.identities[index]:
                raise ValueError("The retained sandbox IPC endpoints changed")
        return self.read, self.write


@dataclass(frozen=True, slots=True)
class IO:
    """Channels for one process invocation; stop is set before every cleanup."""
    input: bytes | None = None
    pipes: Pipes | None = None
    output: queue.Queue[bytes] | None = None
    stop: threading.Event = field(default_factory=threading.Event)

    def checked(self) -> Control:
        """Validate trusted process channels without starting a child."""
        if self.input is not None and type(self.input) is not bytes:
            raise ValueError("Sandbox process input must be bytes")
        if self.output is not None and (type(self.output) is not queue.Queue
                or type(self.output.maxsize) is not int or self.output.maxsize < 1):
            raise ValueError("Sandbox output requires a bounded queue")
        if type(self.stop) is not threading.Event:
            raise ValueError("Sandbox cancellation requires its parent-owned event")
        if self.pipes is not None:
            if type(self.pipes) is not Pipes or sys.platform != "darwin":
                raise ValueError("This sandbox backend cannot retain IPC descriptors")
            self.pipes.check()
        return Control(self)


@dataclass(slots=True)
class Control:
    io: IO
    lock: threading.Lock = field(default_factory=threading.Lock)
    failure: Exception | None = None

    def cancel(self, error: Exception | None = None) -> None:
        """Invalidate admission once before process cleanup."""
        with self.lock:
            if self.failure is None and error is not None:
                self.failure = error
            self.io.stop.set()

    def stopped(self) -> bool:
        return self.io.stop.is_set()

    def emit(self, chunk: bytes) -> None:
        """Deliver one bounded stdout chunk without blocking a capture thread."""
        if self.io.output is None:
            return
        if type(chunk) is not bytes or len(chunk) > CHUNK:
            self.cancel(ValueError("Sandbox output exceeds its frame limit"))
            return
        try:
            self.io.output.put_nowait(chunk)
        except queue.Full:
            self.cancel(RuntimeError("The sandbox output consumer stopped reading"))
