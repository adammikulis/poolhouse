"""The libraries a macOS executable loads, read from its load commands (no `otool`, which a Mac without the developer tools answers
with an install dialog).
"""

from __future__ import annotations

import platform
import struct
from pathlib import Path

THIN_64 = 0xFEEDFACF
FAT = 0xCAFEBABE
CPU = {"arm64": 0x0100000C, "x86_64": 0x01000007}
LOAD_DYLIB = (0x0C, 0x80000018, 0x8000001F)
"""LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB, LC_REEXPORT_DYLIB."""
HEADER = 32
LIMIT = 1 << 26


def _slice(data: bytes) -> bytes:
    """The 64-bit slice of an executable for this machine: the file itself when thin, else its entry in a universal file."""
    if struct.unpack_from("<I", data)[0] == THIN_64:
        return data
    if struct.unpack_from(">I", data)[0] != FAT:
        return b""
    want = CPU.get(platform.machine().lower(), 0)
    for index in range(struct.unpack_from(">I", data, 4)[0]):
        cpu, _sub, offset, size, _align = struct.unpack_from(">iiIII", data, 8 + 20 * index)
        if cpu & 0xFFFFFFFF == want:
            return data[offset:offset + size]
    return b""


def dependencies(path: Path) -> list[str]:
    """The install names of the libraries ``path`` loads, in load order; empty for a file that is not a 64-bit Mach-O."""
    with path.open("rb") as stream:
        data = stream.read(LIMIT)
    try:
        body = _slice(data) if len(data) > 8 else b""
        return _names(body) if len(body) >= HEADER else []
    except struct.error:
        return []


def _names(body: bytes) -> list[str]:
    """The library names in the load commands of one slice."""
    count = struct.unpack_from("<I", body, 16)[0]
    out, at = [], HEADER
    for _ in range(count):
        if at + 8 > len(body):
            break
        command, size = struct.unpack_from("<II", body, at)
        if command in LOAD_DYLIB and size > 24:
            out.append(body[at + struct.unpack_from("<I", body, at + 8)[0]:at + size].split(b"\0", 1)[0].decode("utf-8", "replace"))
        at += max(size, 8)
    return out
