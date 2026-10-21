"""JSON files that another process may be reading while they are rewritten.

A pipeline writes its state as it goes and something else — a page being served, a second
command, a person with ``cat`` — reads it at any moment. A plain ``open(..., "w")`` shows
that reader an empty file and then half of one. Everything here writes beside the file and
renames over it, so a reader sees the old contents or the new, never the middle.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import stat
import sys
import tempfile
from collections.abc import Callable, Iterator, Mapping, Set
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TextIO

if os.name == "nt":
    from poolhouse.files_windows import open_read, replace
else:
    open_read = None
    replace = os.replace

__all__ = ["UNVERSIONED", "CrossDevice", "promote", "promote_new", "prune_orphans", "read_json", "reading", "records_in",
           "sha256_file", "stat_signature", "version_of", "versioned", "write_json", "write_text", "writing"]

#: A record with no version key.
UNVERSIONED = 0


class CrossDevice(OSError):
    """The source and the target are on different filesystems."""


def stat_signature(row):
    return (row.st_dev, row.st_ino, row.st_mode, row.st_uid, row.st_nlink,
            row.st_size, row.st_mtime_ns, row.st_ctime_ns)



def promote(source: Path | str, target: Path | str, *, directory_fd: int | None = None) -> Path:
    """Put a finished ``source`` in ``target``'s place in one step. Returns ``target``.

    ``source`` may be a file, a directory or a symlink, and must be on the same filesystem
    as ``target``; when it is not, `CrossDevice` says so rather than the bare ``EXDEV``.
    """
    source, target = Path(source), Path(target)
    try:
        if directory_fd is None:
            replace(source, target)
        else:
            _promote_relative(source, target, directory_fd)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        raise CrossDevice(
            errno.EXDEV,
            f"{source} and {target} are on different filesystems, so moving one onto the "
            f"other would copy it") from None
    return target


def _directory_descriptor(descriptor: int) -> None:
    if type(descriptor) is not int or descriptor < 0 or not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        raise ValueError("Atomic promotion requires an open directory descriptor.")


def _encoded_path(path: Path | str | bytes) -> bytes:
    name = os.fsencode(path)
    if b"\0" in name:
        raise ValueError("Atomic promotion rejects embedded null bytes.")
    return name


def _child_name(path: Path | str | bytes) -> bytes:
    name = _encoded_path(path)
    if name in (b"", b".", b"..") or b"/" in name:
        raise ValueError("Descriptor promotion accepts direct child names only.")
    return name


def promote_new(source: Path | str | bytes, target: Path | str | bytes, *,
                source_fd: int | None = None, target_fd: int | None = None) -> None:
    """Move an entry without replacement; descriptors pin inode scope, not pathnames.

    Callers retain descriptor ownership and validate source types and links.
    """
    relative = source_fd is not None or target_fd is not None
    if relative:
        if source_fd is None or target_fd is None:
            raise ValueError("Atomic promotion requires both directory descriptors.")
        source_name, target_name = _child_name(source), _child_name(target)
        _directory_descriptor(source_fd)
        _directory_descriptor(target_fd)
    else:
        source_name, target_name = _encoded_path(source), _encoded_path(target)
    if os.name == "nt":
        if relative:
            raise OSError(errno.ENOTSUP, "Descriptor promotion is unavailable")
        try:
            Path(os.fsdecode(source)).rename(os.fsdecode(target))
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            raise CrossDevice(exc.errno, "Atomic promotion requires the same filesystem", os.fsdecode(target)) from None
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == 'darwin' and hasattr(libc, 'renameatx_np' if relative else 'renamex_np'):
        if relative:
            operation = libc.renameatx_np
            operation.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            args = (source_fd, source_name, target_fd, target_name, 0x00000004)  # RENAME_EXCL
        else:
            operation = libc.renamex_np
            operation.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
            args = (source_name, target_name, 0x00000004)  # RENAME_EXCL
    elif sys.platform == 'linux' and hasattr(libc, 'renameat2'):
        operation = libc.renameat2
        operation.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        args = (source_fd if relative else -100, source_name,
                target_fd if relative else -100, target_name, 1)  # AT_FDCWD, RENAME_NOREPLACE
    else:
        raise OSError(errno.ENOTSUP, 'Atomic promotion without replacement is unavailable')
    operation.restype = ctypes.c_int
    if operation(*args) != 0:
        code = ctypes.get_errno()
        if code == errno.EXDEV:
            raise CrossDevice(code, "Atomic promotion requires the same filesystem", os.fsdecode(target))
        raise OSError(code, os.strerror(code), os.fsdecode(target))


def _promote_relative(source: Path, target: Path, descriptor: int) -> None:
    _directory_descriptor(descriptor)
    for path in (source, target):
        _child_name(path)
    info = os.stat(source, dir_fd=descriptor, follow_symlinks=False)
    if not (stat.S_ISDIR(info.st_mode) or (stat.S_ISREG(info.st_mode) and info.st_nlink == 1)):
        raise ValueError("Descriptor promotion requires a regular file or directory without links.")
    replace(str(source), str(target), src_dir_fd=descriptor, dst_dir_fd=descriptor)


@contextmanager
def writing(path: Path | str, *, suffix: str = ".tmp") -> Iterator[Path]:
    """Yield a temporary path beside ``path`` that takes ``path``'s place on a clean exit.

    A reader of ``path`` sees the old contents or the new ones. Leaving the block by an
    exception takes the temporary away and leaves ``path`` as it was.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, made = tempfile.mkstemp(dir=path.parent, suffix=suffix)
    os.close(fd)
    tmp = Path(made)
    try:
        yield tmp
        promote(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_text(path: Path | str, text: str, *, encoding: str = "utf-8") -> None:
    """Write ``text`` to ``path`` so a concurrent reader sees the old file or the new one."""
    with writing(path) as tmp:
        tmp.write_text(text, encoding=encoding)


def write_json(path: Path, obj: Any, *, indent: int | None = 2,
               default: Callable[[Any], Any] | None = None) -> None:
    """Write ``obj`` as JSON so a concurrent reader sees the old file or the new one.

    ``default`` is `json.dump`'s: what to call on a value JSON has no shape for. A failure
    part-way (a value that is not JSON, a full disk) leaves the old file as it was and no
    temporary behind.
    """
    with writing(path) as tmp, tmp.open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=indent, ensure_ascii=False, default=default)


def versioned(record: Mapping[str, Any], version: int) -> dict[str, Any]:
    """``record`` carrying the version key that says which shape it has."""
    return {"version": int(version), **dict(record)}


def version_of(record: Any) -> int:
    """The version ``record`` declares, or ``UNVERSIONED`` when it carries no version key."""
    if not isinstance(record, Mapping):
        return UNVERSIONED
    try:
        return int(record.get("version", UNVERSIONED) or UNVERSIONED)
    except (TypeError, ValueError):
        return UNVERSIONED


def read_json(path: Path, default: Any) -> Any:
    """What ``path`` holds, or ``default`` when it is missing or is not JSON."""
    try:
        with reading(path) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def reading(path: Path | str) -> TextIO:
    """Open a UTF-8 snapshot while allowing atomic replacement of its path."""
    return open(path, encoding="utf-8", opener=open_read)


def prune_orphans(directory: Path, live: Set[str], suffix: str = ".json") -> list[str]:
    """Delete every ``<id><suffix>`` in ``directory`` whose id is not in ``live``.

    For a directory of one file per record of a log — an extraction per message, a
    thumbnail per page — that would otherwise keep records the log has since dropped.
    Returns the ids that went, sorted. A directory that does not exist has no orphans.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return []
    gone: list[str] = []
    for f in sorted(directory.glob(f"*{suffix}")):
        if f.stem not in live:
            f.unlink()
            gone.append(f.stem)
    return gone


def records_in(path: str | Path, key: str) -> list[dict[str, Any]]:
    """The list of mappings a JSON file holds under ``key``, or the file itself when it is
    a list. Raises ``ValueError`` naming the file when there are none."""
    with reading(Path(path).expanduser()) as handle:
        held = json.load(handle)
    found = held.get(key) if isinstance(held, Mapping) else held
    if not isinstance(found, list) or not found:
        raise ValueError(f"{path}: no {key}")
    return [dict(one) for one in found if isinstance(one, Mapping)]


def sha256_file(path: Path | str, *, chunk: int = 1 << 20) -> str:
    """The hex sha256 of a file, read ``chunk`` bytes at a time."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()
