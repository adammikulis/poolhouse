"""Shared and exclusive byte locks for managed-code maintenance on Windows."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import ClassVar

from poolhouse import lock


class Overlapped(ctypes.Structure):
    _fields_: ClassVar = [('Internal', ctypes.c_size_t), ('InternalHigh', ctypes.c_size_t),
                ('Offset', wintypes.DWORD), ('OffsetHigh', wintypes.DWORD), ('hEvent', wintypes.HANDLE)]


def _kernel():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped)]
    kernel.LockFileEx.restype = wintypes.BOOL
    kernel.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.DWORD, ctypes.POINTER(Overlapped)]
    kernel.UnlockFileEx.restype = wintypes.BOOL
    return kernel


def _handle(fd):
    return lock.windows_handle(fd)


def take(fd: int, *, shared: bool) -> bool:
    position = Overlapped(Offset=1 << 30)
    if _kernel().LockFileEx(_handle(fd), 1 | (0 if shared else 2), 0, 1, 0, ctypes.byref(position)):
        return True
    error = ctypes.get_last_error()
    if error == 33:  # ERROR_LOCK_VIOLATION: another process owns the requested range.
        return False
    raise ctypes.WinError(error)


def release(fd: int) -> None:
    position = Overlapped(Offset=1 << 30)
    if not _kernel().UnlockFileEx(_handle(fd), 0, 1, 0, ctypes.byref(position)):
        raise ctypes.WinError(ctypes.get_last_error())
