"""Descriptor-verified model content and identity-keyed checksum reuse."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path


def _identity(info: os.stat_result) -> list[int]:
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


@contextmanager
def open_model_file(member: dict):
    """Open an enumerated member without following a replaced link or changed file."""
    path = Path(member['path'])
    if any(parent.is_symlink() for parent in path.parents):
        raise ValueError('The model member directory changed')
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or _identity(before) != member['identity']:
        raise ValueError('The model member changed before reading')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as source:
        if _identity(os.fstat(source.fileno())) != member['identity']:
            raise ValueError('The model member changed while opening')
        if member.get('blob_digest'):
            checked = _ContentReader(source, member['blob_digest'], member['size'])
            yield checked
            while checked.read(1024 * 1024):
                pass
            if checked.checksum.hexdigest() != member['blob_digest']:
                raise ValueError('The shared model blob does not match its content digest')
        else:
            yield source
        if _identity(os.fstat(source.fileno())) != member['identity'] or _identity(path.lstat()) != member['identity']:
            raise ValueError('The model member changed while reading')


class _ContentReader:
    """Hash sequential reads without a second pass over native model weights."""

    def __init__(self, source, digest, size):
        self.source = source
        self.checksum = hashlib.sha1(usedforsecurity=False) if len(digest) == 40 else hashlib.sha256()
        if len(digest) == 40:
            self.checksum.update(f'blob {size}\0'.encode())

    def seek(self, offset, whence=0):
        current = self.source.tell()
        if whence != 0 or offset < current:
            raise ValueError('A verified model stream only supports forward absolute seeks')
        while current < offset:
            chunk = self.read(min(1024 * 1024, offset - current))
            if not chunk:
                raise ValueError('A model resume offset exceeds its declared content')
            current += len(chunk)
        return current

    def read(self, size=-1):
        data = self.source.read(size)
        self.checksum.update(data)
        return data


def base_fingerprint(handle: dict) -> str:
    """Hash an explicitly authorized staged base; this function grants no authority."""
    root = Path(handle['path'])
    if not root.is_dir() or any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError('A staged base must be a regular owned directory')
    members = []
    for path in sorted(root.rglob('*')):
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('A staged base cannot contain symbolic links or special files')
        members.append({'relative': path.relative_to(root).as_posix(), 'path': str(path),
                        'size': info.st_size, 'identity': _identity(info)})
    if not members:
        raise ValueError('A staged base has no regular files')
    return _fingerprint(members)


def _fingerprint(members: list[dict]) -> str:
    # Callers re-enumerate paths, links and identities before any content hash is reused.
    key = tuple((item['relative'], item['path'], item['size'], tuple(item['identity']), item.get('blob_digest', '')) for item in members)
    return _fingerprint_checked(key)


@lru_cache(maxsize=128)
def _fingerprint_checked(key: tuple) -> str:
    rows = []
    for relative, path, size, identity, expected in key:
        member = {'path': path, 'size': size, 'identity': list(identity)}
        if expected:
            member['blob_digest'] = expected
        checksum = hashlib.sha256()
        with open_model_file(member) as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                checksum.update(chunk)
        rows.append([relative, size, checksum.hexdigest()])
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
