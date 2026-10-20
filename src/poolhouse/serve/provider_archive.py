"""Read a pinned runtime archive without following its library aliases on disk."""
from __future__ import annotations

import hashlib
import io
import os
import stat
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class Entry:
    data: bytes
    executable: bool


def _read(path: Path) -> bytes:
    """Read only one owned nofollow descriptor, rejecting concurrent replacements/writes."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                or before.st_nlink != 1 or before.st_mode & 0o022 or before.st_size > 256 << 20):
            raise ValueError('The serving runtime has an unowned file or filesystem alias')
        parts = []
        remaining = before.st_size + 1
        while remaining:
            block = os.read(descriptor, min(1 << 20, remaining))
            if not block:
                break
            parts.append(block)
            remaining -= len(block)
        after = os.fstat(descriptor)
        fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_nlink')
        if any(getattr(before, key) != getattr(after, key) for key in fields):
            raise ValueError('The serving runtime changed while it was read')
        data = b''.join(parts)
        if len(data) != before.st_size:
            raise ValueError('The serving runtime file changed size')
        return data
    finally:
        os.close(descriptor)


def contents(archive: Path, digest: str, prefix: str) -> dict[str, Entry]:
    """Validate the archive pin, then materialize only its server, libraries and licence."""
    verified = _read(archive)
    if hashlib.sha256(verified).hexdigest() != digest:
        raise ValueError('The serving runtime archive does not match its pinned release')
    with tarfile.open(fileobj=io.BytesIO(verified)) as opened:
        indexed = {}
        total = 0
        for count, member in enumerate(opened):
            total += member.size
            if count >= 256 or total > 256 << 20 or member.size < 0:
                raise ValueError('The serving runtime archive exceeds its declared bounds')
            if member.name == '.' and member.isdir():
                continue
            parts = PurePosixPath(member.name).parts
            if (len(parts) != 2 or parts[0] != prefix or parts[1] in ('.', '..')
                    or '\\' in member.name or member.name.startswith('/')
                    or not (member.isfile() or member.issym())):
                raise ValueError('The serving runtime archive has an unexpected entry')
            name = parts[1]
            if member.issym() and not (name.startswith('lib') and name.endswith('.dylib')):
                raise ValueError('The serving runtime has an unexpected executable alias')
            if name in indexed:
                raise ValueError('The serving runtime archive repeats an entry')
            indexed[name] = member
        result = {}
        cached = {}
        materialized = 0
        for name in indexed:
            if name not in ('llama-server', 'LICENSE') and not (name.startswith('lib') and name.endswith('.dylib')):
                continue
            member = indexed[name]
            visited = set()
            while member.issym():
                target = member.linkname
                if (not name.endswith('.dylib') or target in visited or '/' in target or '\\' in target
                        or target not in indexed or not target.startswith('lib') or not target.endswith('.dylib')):
                    raise ValueError('The serving runtime has an invalid library alias')
                visited.add(target)
                member = indexed[target]
            if not member.isfile():
                raise ValueError('The serving runtime alias has no regular target')
            if member.name not in cached:
                stream = opened.extractfile(member)
                if stream is None:
                    raise ValueError('The serving runtime archive entry cannot be read')
                with stream:
                    cached[member.name] = stream.read()
            materialized += len(cached[member.name])
            if materialized > 256 << 20:
                raise ValueError('The materialized serving runtime exceeds its declared bounds')
            result[name] = Entry(cached[member.name], bool(member.mode & 0o111))
        if 'llama-server' not in result or not result['llama-server'].executable:
            raise ValueError('The serving runtime archive has no executable server')
        return result


def verify(directory: Path, expected: dict[str, Entry]) -> Path:
    """Refuse changed or extra code, including changed dylib aliases."""
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ValueError('The serving runtime directory is not privately owned')
    if {item.name for item in directory.iterdir()} != {*expected, 'release.tar.gz'}:
        raise ValueError('The serving runtime inventory changed')
    for name, entry in expected.items():
        path = directory / name
        if _read(path) != entry.data:
            raise ValueError('The serving runtime files do not match their pinned release')
        if bool(path.stat().st_mode & 0o111) != entry.executable:
            raise ValueError('The serving runtime executable permissions changed')
    return directory / 'llama-server'
