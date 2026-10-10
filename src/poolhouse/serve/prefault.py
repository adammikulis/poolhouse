"""Page-cache calls for a model file: ask the OS to read ranges ahead, and ask which pages of
a range are resident now. The only module that touches the platform for this.

Linux and macOS ask with ``madvise(WILLNEED)`` on a read-only mapping, Windows with
``PrefetchVirtualMemory``; residency is ``mincore`` on a read-only shared
mapping (POSIX only).
"""

from __future__ import annotations

import ctypes
import ctypes.util
import mmap
import sys
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

__all__ = ["Advise", "Unsupported", "advise_for_platform", "resident_runs"]

Advise = Callable[[int, int, int], None]
"""``(file descriptor, offset, length)``: ask the OS to read those bytes into its cache."""

_ONE_GIB = 1 << 30
PAGE = mmap.PAGESIZE


class Unsupported(OSError):
    """The platform has no such call."""


def _advise_posix(fd: int, offset: int, length: int) -> None:
    start = offset - offset % mmap.ALLOCATIONGRANULARITY
    view = mmap.mmap(fd, length + offset - start, access=mmap.ACCESS_READ, offset=start)
    try:
        view.madvise(mmap.MADV_WILLNEED)
    finally:
        view.close()


def _advise_windows(fd: int, offset: int, length: int) -> None:
    class _Range(ctypes.Structure):
        _fields_: ClassVar = [("VirtualAddress", ctypes.c_void_p),
                          ("NumberOfBytes", ctypes.c_size_t)]

    grain = mmap.ALLOCATIONGRANULARITY
    start = offset - offset % grain
    view = mmap.mmap(fd, length + offset - start, access=mmap.ACCESS_COPY, offset=start)
    anchor = ctypes.c_char.from_buffer(view)
    try:
        entry = _Range(ctypes.addressof(anchor) + offset - start, length)
        kernel = ctypes.WinDLL("kernel32")  # type: ignore[attr-defined]
        if not kernel.PrefetchVirtualMemory(kernel.GetCurrentProcess(), 1,
                                            ctypes.byref(entry), 0):
            raise OSError("PrefetchVirtualMemory failed")
    finally:
        del anchor
        view.close()


def advise_for_platform() -> Advise:
    """The read-ahead call this platform has."""
    if sys.platform == "darwin" or sys.platform.startswith("linux"):
        return _advise_posix
    if sys.platform == "win32":
        return _advise_windows
    raise Unsupported(f"no read-ahead call known for {sys.platform}")


def _libc() -> ctypes.CDLL:
    if sys.platform == "win32":
        raise Unsupported("page residency needs mincore, which Windows does not have")
    lib = ctypes.CDLL(ctypes.util.find_library("c") or None, use_errno=True)
    lib.mmap.restype = ctypes.c_void_p
    lib.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, ctypes.c_longlong]
    lib.mincore.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_char_p]
    lib.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    return lib


def _window(lib: ctypes.CDLL, fd: int, start: int, end: int) -> bytes:
    """One byte per page of ``[start, end)`` (both page-aligned); bit 0 set when resident."""
    length = end - start
    where = lib.mmap(None, length, 1, 1, fd, start)  # PROT_READ, MAP_SHARED
    if where in (None, ctypes.c_void_p(-1).value):
        raise OSError(ctypes.get_errno(), "mmap failed")
    try:
        vec = ctypes.create_string_buffer(length // PAGE)
        if lib.mincore(where, length, vec) != 0:
            raise OSError(ctypes.get_errno(), "mincore failed")
        return vec.raw
    finally:
        lib.munmap(where, length)


def resident_runs(path: Path, start: int, end: int, *, lib: ctypes.CDLL | None = None
                  ) -> list[tuple[int, int]]:
    """The byte ranges of ``[start, end)`` of the file whose pages are in the page cache now,
    as ``(start, end)`` runs of whole pages clipped to the range."""
    low, high = start - start % PAGE, -(-end // PAGE) * PAGE
    size = path.stat().st_size
    high = min(high, -(-size // PAGE) * PAGE)
    lib = lib if lib is not None else _libc()
    runs: list[tuple[int, int]] = []
    with path.open("rb") as handle:
        for base in range(low, high, _ONE_GIB):
            top = min(base + _ONE_GIB, high)
            flags = _window(lib, handle.fileno(), base, top)
            for index, byte in enumerate(flags):
                if not byte & 1:
                    continue
                first, last = base + index * PAGE, base + (index + 1) * PAGE
                if runs and runs[-1][1] == first:
                    runs[-1] = (runs[-1][0], last)
                else:
                    runs.append((first, last))
    return [(max(a, start), min(b, end)) for a, b in runs if min(b, end) > max(a, start)]
