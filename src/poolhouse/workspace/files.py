"""What a file posted to a board is checked against: its handle, name, kind, scan verdict, source and output path."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Sequence
from pathlib import Path

from poolhouse.home import expand
from poolhouse.net import scan as netscan, sniff
from poolhouse.net.scanners import default_scanners
from poolhouse.workspace import plain
from poolhouse.workspace.screen import Refused

__all__ = ["HOUR_S", "NOT_AVAILABLE", "REF", "classify", "clean_name", "human_size", "out_path", "read_source",
           "scan_verdict", "valid_handle"]

REF = re.compile(r"(?<![A-Za-z0-9_])file:([0-9a-f]{12})(?![A-Za-z0-9_])")
HANDLE = re.compile(r"[0-9a-f]{12}")
BLOCKED_KINDS = ("executable", "zip", "gzip", "tar", "gguf", "safetensors")
BLOCKED_SUFFIXES = (*sniff.ARCHIVE_SUFFIXES, *sniff.PICKLE_SUFFIXES, ".exe", ".dll", ".so",
                    ".dylib", ".app", ".dmg", ".pkg", ".msi", ".sh", ".bat", ".cmd", ".ps1",
                    ".command", ".py", ".js", ".jar", ".gguf", ".safetensors")
PICKLE_MAGIC = re.compile(rb"\x80[\x02-\x05]")
HOUR_S = 3600.0
NOT_AVAILABLE = "file {} (not available to you)"


def human_size(n: int) -> str:
    """``n`` bytes as ``812 B``, ``12 KB`` or ``1.5 MB``."""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n // 1024} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def valid_handle(text: str) -> bool:
    """Whether ``text`` is a file handle: 12 lowercase hex characters."""
    return bool(HANDLE.fullmatch(text))


def clean_name(name: str, width: int) -> str:
    """``name`` as a plain file name: no separators, control or bidirectional characters, cut
    to ``width``; ``file`` when nothing is left."""
    base = re.split(r"[\\/]", name.replace("\x00", " "))[-1]
    cleaned = plain.line(re.sub(r"[\\/:*?\"<>|]", "_", base), width).strip(". ")
    return cleaned or "file"


def classify(name: str, data: bytes) -> tuple[str, bool]:
    """``(kind, is_text)`` of ``data`` posted as ``name``; `Refused` for what is never kept."""
    shown = sniff.detect(data[:sniff.HEAD])
    low = name.lower()
    if low.endswith(BLOCKED_SUFFIXES) or shown in BLOCKED_KINDS or PICKLE_MAGIC.match(data[:2]):
        raise Refused(f"{plain.line(name, 40)} is not accepted: archives, executables, scripts, "
                      f"pickles and model weights are never posted ({shown or 'by its name'})")
    if shown == "text" or shown == "json":
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return "binary", False
        return shown, b"\x00" not in data
    return shown or "binary", False


def scan_verdict(scratch: Path, kind: str, data: bytes, scanners: Sequence[netscan.Scanner] | None = None) -> str:
    """Why the scanners refuse ``data`` (written to a temporary file in ``scratch`` while they look), or an empty string."""
    scanners = scanners if scanners is not None else default_scanners()
    scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = scratch / f"{hashlib.sha256(data).hexdigest()[:16]}.bin"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        summary = netscan.scan_file(path, scanners)
        keep, why = netscan.ScanPolicy().decide(netscan.category(kind), summary)
    finally:
        path.unlink(missing_ok=True)
    return "" if keep else why


def read_source(path: str, cap: int, avoid: Sequence[Path]) -> tuple[bytes, str]:
    """The bytes of the file at ``path``, at most one byte over ``cap``, and its own name; a link, a
    folder or a file inside one of the ``avoid`` folders is refused."""
    target = expand(path)
    if any(target.resolve().is_relative_to(base.resolve()) for base in avoid):
        raise Refused("a file inside the workspace's own state is never attached")
    if not stat.S_ISREG(target.lstat().st_mode):
        raise Refused("attach a regular file, not a link or a folder")
    fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as handle:
        return handle.read(cap + 1), target.name


def out_path(target: str, roots: Sequence[Path]) -> Path:
    """A path inside one of ``roots``, with no ``..`` and no symlink on the way, that does not exist yet."""
    raw = Path(target)
    if not target or "\x00" in target or ".." in raw.parts:
        raise Refused("--out is a path inside your worktree, with no ..")
    for root in roots:
        base = root.resolve()
        path = raw if raw.is_absolute() else base / raw
        try:
            inside = path.relative_to(base)
        except ValueError:
            continue
        walk = base
        for part in inside.parts:
            walk = walk / part
            if walk.is_symlink():
                raise Refused("--out never goes through a symlink")
        if not path.parent.is_dir():
            raise Refused("--out needs its folder to exist")
        if path.exists():
            raise Refused("--out never overwrites a file")
        if not stat.S_ISDIR(path.parent.stat().st_mode):
            raise Refused("--out needs its folder to exist")
        return path
    raise Refused("--out is a path inside your worktree")
