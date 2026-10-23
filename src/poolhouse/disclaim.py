"""Start a macOS process that answers for itself when macOS asks who is responsible for it.

A process inherits a responsible process from whatever started it: a daemon started from a terminal, an editor or an agent's
shell is attributed to that app, so a Local Network prompt names the terminal and the answer is given to it. A process started
with its responsibility disclaimed is its own, and the prompt names the executable, which for the runtime is Poolhouse
(`runtime_identity`). The attribute is `responsibility_spawnattrs_setdisclaim` in libSystem, used with `posix_spawn`, which Python's
`subprocess` does not reach.
"""

from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Mapping, Sequence

POSIX_SPAWN_SETSID = 0x400
POSIX_SPAWN_CLOEXEC_DEFAULT = 0x4000
READ_ONLY = os.O_RDONLY


class DisclaimError(OSError):
    """The process could not be started."""


def available() -> bool:
    """Whether this system can disclaim responsibility (macOS)."""
    return sys.platform == "darwin"


def _check(code: int, what: str) -> None:
    if code:
        raise DisclaimError(code, f"{what} failed: {os.strerror(code)}")


def _array(words: Sequence[str]) -> ctypes.Array:
    return (ctypes.c_char_p * (len(words) + 1))(*[os.fsencode(word) for word in words], None)


def spawn(argv: Sequence[str], environment: Mapping[str, str], out: int, cwd: str | os.PathLike[str] | None = None) -> int:
    """Start ``argv`` in a session of its own with its own responsibility, standard input empty and standard output and error
    written to the open descriptor ``out``, in directory ``cwd`` (the caller's when not given); returns its pid. The caller reaps it
    or lets it be adopted.

    A process that answers for itself asks macOS for its own access to the Documents, Desktop and Downloads folders and waits,
    for ever when nobody can answer, at the first read there; so a caller gives it a directory that is not one of them."""
    libc = ctypes.CDLL(None, use_errno=True)
    if not hasattr(libc, "responsibility_spawnattrs_setdisclaim"):
        raise DisclaimError("this system cannot start a process that answers for itself")
    attr, actions, pid = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_int()
    _check(libc.posix_spawnattr_init(ctypes.byref(attr)), "posix_spawnattr_init")
    try:
        _check(libc.posix_spawn_file_actions_init(ctypes.byref(actions)), "posix_spawn_file_actions_init")
        flags = ctypes.c_short(POSIX_SPAWN_SETSID | POSIX_SPAWN_CLOEXEC_DEFAULT)  # no descriptor of the caller reaches the daemon
        _check(libc.posix_spawnattr_setflags(ctypes.byref(attr), flags), "posix_spawnattr_setflags")
        _check(libc.responsibility_spawnattrs_setdisclaim(ctypes.byref(attr), 1), "responsibility_spawnattrs_setdisclaim")
        _check(libc.posix_spawn_file_actions_addopen(ctypes.byref(actions), 0, os.fsencode(os.devnull), READ_ONLY, 0), "addopen")
        if cwd is not None:
            _check(libc.posix_spawn_file_actions_addchdir_np(ctypes.byref(actions), os.fsencode(cwd)), "addchdir")
        for target in (1, 2):
            _check(libc.posix_spawn_file_actions_adddup2(ctypes.byref(actions), out, target), "adddup2")
        words = _array(list(argv))
        _check(libc.posix_spawn(ctypes.byref(pid), os.fsencode(argv[0]), ctypes.byref(actions), ctypes.byref(attr), words,
                                _array([f"{key}={value}" for key, value in environment.items()])), f"starting {argv[0]}")
    finally:
        libc.posix_spawn_file_actions_destroy(ctypes.byref(actions))
        libc.posix_spawnattr_destroy(ctypes.byref(attr))
    return pid.value
