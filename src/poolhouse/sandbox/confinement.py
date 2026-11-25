"""Whether this process runs inside a confinement the engine made.

A process the operating system confines is never a person (docs/design-isolated-launcher.md, I17): what asks a question of a person, or
widens what a session may reach, refuses when this says yes, whatever the process's descriptors, terminal or environment say. The
answer comes from the operating system and not from anything the process holds, so the confined process cannot change it. Where the
check cannot be made the answer is yes: a refusal is the safe side.

macOS asks the kernel whether a sandbox applies to this process and refuses everything by default (``sandbox_check`` of the process and
of the operation ``default``, VERIFIED on 26.6). Linux looks for the marker file
the backend binds into the namespace read-only: the null device at a fixed path, which no unprivileged process can make (PREDICTED
until a Linux device runs the probe, P-wsl). Windows asks for an AppContainer token and a job object, both of which the launcher builds
(PREDICTED; the backend is not built, docs/design-isolated-launcher.md 5.4).
"""

from __future__ import annotations

import ctypes
import os
import stat
import sys
from pathlib import Path

__all__ = ["MARKER", "confined", "seatbelt_state"]

MARKER = "/run/poolhouse/confined"
"""Where the Linux backend binds the null device, read-only, inside the namespace."""
NULL_DEVICE = (1, 3)
"""The major and minor number of the null device, which the marker must be."""
TOKEN_QUERY = 0x8
TOKEN_IS_APP_CONTAINER = 29


def confined() -> bool:
    """Whether this process runs inside a confinement the engine made; yes when that cannot be told."""
    if sys.platform == "darwin":
        return _seatbelt()
    if sys.platform.startswith("linux"):
        return _marker()
    if os.name == "nt":
        return _windows()
    return True


def seatbelt_state(pid: int) -> str:
    """What the kernel says of the sandbox of process ``pid``: ``none`` (no sandbox applies), ``refuses`` (one applies and its default is to
    refuse: every profile the engine builds starts `(deny default ...)`), ``allows`` (one applies and its default is to allow, a deny-list
    such as the one the test runner puts around the whole suite or a harness puts around its own tools) or ``unknown`` (the call failed).
    The one answer behind `confined` and behind the attestation of the launcher's proof (docs/design-isolated-launcher.md, 10.3): a process
    is confined, here and there, exactly when it is ``refuses``. Raises `OSError` or `AttributeError` where the library is missing."""
    library = ctypes.CDLL("/usr/lib/system/libsystem_sandbox.dylib")
    library.sandbox_check.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    library.sandbox_check.restype = ctypes.c_int
    applies = library.sandbox_check(pid, None, 0)
    if applies == 0:
        return "none"
    if applies != 1:
        return "unknown"
    return "refuses" if library.sandbox_check(pid, b"default", 0) != 0 else "allows"


def _seatbelt() -> bool:
    """A sandbox applies and its default is to refuse (`seatbelt_state`). A deny-list profile, which holds no harness and is not the engine's
    confinement, is not one (a process in it is judged by what the node attests of its ancestry, not by this); a failed call counts as
    yes, the safe side."""
    try:
        return seatbelt_state(os.getpid()) in ("refuses", "unknown")
    except (OSError, AttributeError):
        return True


def _marker() -> bool:
    try:
        info = Path(MARKER).stat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return stat.S_ISCHR(info.st_mode) and (os.major(info.st_rdev), os.minor(info.st_rdev)) == NULL_DEVICE


def _windows() -> bool:
    from ctypes import wintypes

    kernel, advapi = ctypes.WinDLL("kernel32", use_last_error=True), ctypes.WinDLL("advapi32", use_last_error=True)
    me = kernel.GetCurrentProcess()
    token, in_container, size, in_job = wintypes.HANDLE(), wintypes.DWORD(), wintypes.DWORD(), wintypes.BOOL()
    if not advapi.OpenProcessToken(me, TOKEN_QUERY, ctypes.byref(token)):
        return True
    try:
        if not advapi.GetTokenInformation(token, TOKEN_IS_APP_CONTAINER, ctypes.byref(in_container), ctypes.sizeof(in_container),
                                          ctypes.byref(size)):
            return True
    finally:
        kernel.CloseHandle(token)
    if not kernel.IsProcessInJob(me, None, ctypes.byref(in_job)):
        return True
    return bool(in_container.value) and bool(in_job.value)
