"""Darwin inventory of an owned, unreaped transport process group."""

from __future__ import annotations

import ctypes
import os
from typing import ClassVar


class _BSDInfo(ctypes.Structure):
    _fields_: ClassVar = [
        *[(name, ctypes.c_uint32) for name in (
            'flags', 'status', 'xstatus', 'pid', 'ppid', 'uid', 'gid', 'ruid', 'rgid',
            'svuid', 'svgid', 'reserved')],
        ('comm', ctypes.c_char * 16), ('name', ctypes.c_char * 32),
        *[(name, ctypes.c_uint32) for name in ('nfiles', 'pgid', 'jobc', 'tdev', 'tpgid')],
        ('nice', ctypes.c_int32), ('start_sec', ctypes.c_uint64), ('start_usec', ctypes.c_uint64),
    ]


def _library():
    try:
        library = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
        library.proc_listpids.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int]
        library.proc_listpids.restype = ctypes.c_int
        library.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
        library.proc_pidinfo.restype = ctypes.c_int
    except AttributeError:
        raise OSError('the Darwin process inventory ABI is unavailable') from None
    if ctypes.sizeof(_BSDInfo) != 136:
        raise OSError('the Darwin process inventory ABI is unavailable')
    return library


def _snapshot(library, leader: int) -> tuple[tuple[int, ...], ...]:
    members = (ctypes.c_int * 16)()
    size = library.proc_listpids(2, leader, members, ctypes.sizeof(members))
    if size <= 0 or size >= ctypes.sizeof(members) or size % ctypes.sizeof(ctypes.c_int):
        raise OSError('the owned transport group inventory is incomplete')
    pids = list(members)[:size // ctypes.sizeof(ctypes.c_int)]
    if leader not in pids or len(set(pids)) != len(pids) or any(pid <= 0 for pid in pids):
        raise OSError('the owned transport group inventory is invalid')
    rows = []
    for pid in sorted(pids):
        info = _BSDInfo()
        size = library.proc_pidinfo(pid, 3, 1, ctypes.byref(info), ctypes.sizeof(info))
        if (size != ctypes.sizeof(info) or info.pid != pid or info.pgid != leader
                or info.uid != os.getuid() or info.status not in {1, 2, 3, 4, 5}
                or not info.start_sec or info.start_usec >= 1_000_000
                or (pid == leader and info.ppid != os.getpid())):
            raise OSError('the owned transport group member could not be verified')
        rows.append((pid, info.pgid, info.uid, info.status, info.start_sec, info.start_usec))
    return tuple(rows)


def quiescent(process) -> bool:
    """Verify that an owned exited leader's complete group contains only zombies."""
    flags = os.WEXITED | os.WNOHANG | os.WNOWAIT
    ended = os.waitid(os.P_PID, process.pid, flags)
    if ended is None:
        return False
    if ended.si_pid != process.pid:
        raise OSError('the owned transport leader could not be verified')
    library = _library()
    first = _snapshot(library, process.pid)
    second = _snapshot(library, process.pid)
    held = os.waitid(os.P_PID, process.pid, flags)
    if held != ended or first != second:
        raise OSError('the owned transport group changed during verification')
    return all(row[3] == 5 for row in second)
