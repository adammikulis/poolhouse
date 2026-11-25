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

__all__ = ["MARKER", "confined"]

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


def _seatbelt() -> bool:
    """A sandbox applies and its default is to refuse. The engine's every profile starts `(deny default ...)`, so a process it confines
    answers yes to both; a profile that allows by default and names what to refuse (the one the test runner puts around the whole
    suite to deny writes under the real state root) holds no harness and is not the engine's confinement. Any answer other than a plain
    no counts as yes, so a failed call is the safe side."""
    try:
        library = ctypes.CDLL("/usr/lib/system/libsystem_sandbox.dylib")
        library.sandbox_check.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        library.sandbox_check.restype = ctypes.c_int
        pid = os.getpid()
        return library.sandbox_check(pid, None, 0) != 0 and library.sandbox_check(pid, b"default", 0) != 0
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
