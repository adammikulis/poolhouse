"""Whether the process is a person's: a terminal on stdin and stdout and no agent marker; and a question put to the
person on the terminal device itself."""

from __future__ import annotations

import contextlib
import ctypes
import importlib
import os
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import TextIO

if sys.platform != "win32":
    import select
    import signal
    import termios

__all__ = ["AGENT_MARKERS", "HumanRequired", "confirm", "controlling_terminal", "is_terminal", "marked", "require_person", "typed"]

_MSVCRT = importlib.import_module("msvcrt") if sys.platform == "win32" else None

AGENT_MARKERS = ("CLAUDECODE", "POOLHOUSE_AGENT", "POOLHOUSE_NONINTERACTIVE")
"""Environment variables whose presence means an agent started this process."""


class HumanRequired(PermissionError):
    """An action that only a person at a terminal may take was asked for by something else."""


def marked(env: Mapping[str, str] | None = None) -> str:
    """The first agent marker set in ``env`` (the process's when not given), else an empty string."""
    env = os.environ if env is None else env
    return next((name for name in AGENT_MARKERS if env.get(name)), "")



def is_terminal(stream) -> bool:
    """Whether the stream is attached to an interactive terminal."""
    if not stream.isatty():
        return False
    if sys.platform != "win32":
        return True
    try:
        descriptor = stream.fileno()
        handle = _MSVCRT.get_osfhandle(descriptor)
        mode = ctypes.c_ulong()
        return bool(ctypes.windll.kernel32.GetConsoleMode(ctypes.c_void_p(handle), ctypes.byref(mode)))
    except (AttributeError, OSError, ValueError):
        return False

def require_person(action: str, terminal: tuple[bool, bool] | None = None,
                   env: Mapping[str, str] | None = None) -> None:
    """Raise `HumanRequired` unless stdin and stdout are terminals and no agent marker is
    set. Every way of minting a grant goes through this first."""
    tty_in, tty_out = terminal or (is_terminal(sys.stdin), is_terminal(sys.stdout))
    found = marked(env)
    if found:
        raise HumanRequired(f"{action} is for a person; this process was started by an agent "
                            f"({found} is set)")
    if not (tty_in and tty_out):
        raise HumanRequired(f"{action} needs a terminal on stdin and stdout")


@contextlib.contextmanager
def controlling_terminal() -> Iterator[tuple[TextIO, TextIO] | None]:
    """The controlling terminal opened to read from and to write to, or None when this process has none."""
    names = ("CONIN$", "CONOUT$") if sys.platform == "win32" else ("/dev/tty", "/dev/tty")
    with contextlib.ExitStack() as stack:
        try:
            both = (stack.enter_context(Path(names[0]).open()), stack.enter_context(Path(names[1]).open("w")))
        except OSError:
            both = None
        yield both


@contextlib.contextmanager
def _background_reads_fail() -> Iterator[None]:
    """Within it, reading the terminal from outside its foreground group fails instead of stopping the process."""
    if sys.platform == "win32":
        yield
        return
    try:
        previous = signal.signal(signal.SIGTTIN, signal.SIG_IGN)
    except ValueError:
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGTTIN, previous)


def confirm(prompt: str, *, wait_s: float = 120.0) -> bool:
    """Ask ``prompt`` on the controlling terminal: whether the person typed `yes` there within ``wait_s``.

    The terminal device is read and written itself, so what stdin and stdout are changes nothing; text typed before the
    prompt appeared is dropped, and no terminal, no answer, or anything but `yes` is a no.
    """
    return typed(prompt, wait_s=wait_s) == "yes"


def typed(prompt: str, *, wait_s: float = 120.0) -> str:
    """Ask ``prompt`` on the controlling terminal: the line the person typed there within ``wait_s``, lower case and stripped.

    Read as `confirm` reads it; no terminal, no answer or text that cannot be read is an empty line.
    """
    with controlling_terminal() as found, _background_reads_fail():
        if found is None:
            return ""
        reader, writer = found
        try:
            if sys.platform != "win32":
                termios.tcflush(reader.fileno(), termios.TCIFLUSH)
            writer.write(prompt)
            writer.flush()
            if sys.platform != "win32" and not select.select([reader], [], [], wait_s)[0]:
                writer.write("\nno answer: nothing was done\n")
                return ""
            return reader.readline().strip().lower()
        except (OSError, ValueError, UnicodeError):
            return ""
