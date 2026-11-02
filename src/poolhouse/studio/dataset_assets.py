"""Small JSONL dataset artifacts through anchored regular-file descriptors."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
from contextlib import contextmanager, suppress
from pathlib import Path

from poolhouse.files import stat_signature
from poolhouse.graph.guard import MAX_BODY
from poolhouse.json_objects import unique_object

NAMES = {'dataset.jsonl', 'dataset-import.json'}
PRODUCER = hashlib.sha256(b'dataset').hexdigest()[:24]


def reserved(relative):
    parts = Path(relative).parts
    return (parts[:2] == ('datasets', 'huggingface')
            or (len(parts) >= 4 and parts[0] == 'studio' and re.fullmatch('[0-9a-f]{32}', parts[1]) is not None
                and parts[2:4] == (PRODUCER, 'dataset')))


@contextmanager
def folder(root, relative, *, create=False):
    root, relative = Path(root), Path(relative)
    if (not root.is_absolute() or '..' in root.parts or relative.is_absolute()
            or not relative.parts or '..' in relative.parts):
        raise ValueError('Choose an owned dataset location.')
    descriptors, anchors = [], []
    try:
        at = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(at)
        parts = [*root.parts[1:], *relative.parts]
        for index, name in enumerate(parts):
            if create and index >= len(root.parts) - 1:
                with suppress(FileExistsError):
                    os.mkdir(name, mode=0o700, dir_fd=at)
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=at)
            descriptors.append(child)
            anchors.append((at, name, child, stat_signature(os.fstat(child))[:4]))
            at = child
        info = os.fstat(at)
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise PermissionError('Dataset output must belong to this account.')
        yield at
        for parent, name, child, before in anchors:
            if (stat_signature(os.fstat(child))[:4] != before
                    or stat_signature(os.stat(name, dir_fd=parent, follow_symlinks=False))[:4] != before):
                raise PermissionError('The dataset location changed.')
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def read(directory, name, *, identity=None):
    if name not in NAMES:
        raise ValueError('Choose a declared dataset file.')
    handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(handle, 'rb') as source:
        before = os.fstat(source.fileno())
        if ((identity is not None and stat_signature(before)[:2] != identity)
                or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_uid != os.geteuid()
                or before.st_mode & 0o077 or before.st_size > MAX_BODY):
            raise ValueError('A dataset asset must be a private regular file within the record budget.')
        raw = source.read(MAX_BODY + 1)
        if (stat_signature(os.fstat(source.fileno())) != stat_signature(before)
                or stat_signature(os.stat(name, dir_fd=directory, follow_symlinks=False)) != stat_signature(before)
                or len(raw) != before.st_size):
            raise ValueError('The dataset asset changed.')
        return raw


def _write(directory, name, raw):
    try:
        if read(directory, name) != raw:
            raise ValueError('An immutable dataset artifact already has different content.')
        return
    except FileNotFoundError:
        pass
    part = '.' + name + '.' + secrets.token_hex(16) + '.part'
    handle = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    identity = stat_signature(os.fstat(handle))[:2]
    published = False
    try:
        with os.fdopen(handle, 'wb') as target:
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
            expected = stat_signature(os.fstat(target.fileno()))
        if stat_signature(os.stat(part, dir_fd=directory, follow_symlinks=False)) != expected:
            raise ValueError('The dataset staging file changed.')
        os.link(part, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
        linked = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if (stat_signature(linked)[:2] != identity or linked.st_nlink != 2
                or read_linked(directory, name, raw, identity) is not True):
            raise ValueError('The dataset promotion changed.')
        _remove_owned(directory, part, identity)
        if read(directory, name, identity=identity) != raw:
            raise ValueError('The dataset promotion changed.')
        published = True
    finally:
        _remove_owned(directory, part, identity)
        if not published:
            _remove_owned(directory, name, identity)


def _remove_owned(directory, name, identity):
    try:
        if stat_signature(os.stat(name, dir_fd=directory, follow_symlinks=False))[:2] == identity:
            os.unlink(name, dir_fd=directory)
    except FileNotFoundError:
        pass


def read_linked(directory, name, raw, identity):
    # The promotion's second link is transient; normal readers require nlink=1.
    handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(handle, 'rb') as source:
        before = os.fstat(source.fileno())
        return (stat_signature(before)[:2] == identity
                and stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
                and before.st_mode & 0o077 == 0 and before.st_nlink == 2
                and before.st_size == len(raw) and source.read(len(raw) + 1) == raw
                and stat_signature(os.fstat(source.fileno())) == stat_signature(before)
                and stat_signature(os.stat(name, dir_fd=directory, follow_symlinks=False)) == stat_signature(before))


def publish(root, relative, data, manifest):
    raw = json.dumps(manifest, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if len(data) > MAX_BODY or len(raw) > MAX_BODY:
        raise ValueError('The selected dataset exceeds the record budget.')
    with folder(root, relative, create=True) as directory:
        _write(directory, 'dataset.jsonl', data)
        _write(directory, 'dataset-import.json', raw)
        os.fsync(directory)
        verify(directory, manifest)
    return Path(root) / relative


def verify(directory, expected):
    raw = read(directory, 'dataset-import.json')
    manifest = json.loads(raw, object_pairs_hook=unique_object)
    if (not isinstance(manifest, dict) or set(manifest) != {'version', 'rows', 'columns', 'file', 'source'}
            or type(manifest['version']) is not int or manifest['version'] != 1
            or type(manifest['rows']) is not int or manifest['rows'] < 1
            or not isinstance(manifest['columns'], list) or not isinstance(manifest['source'], dict)
            or not isinstance(manifest['file'], dict) or set(manifest['file']) != {'name', 'size', 'sha256'}
            or manifest['file']['name'] != 'dataset.jsonl'):
        raise ValueError('The dataset manifest is invalid.')
    data = read(directory, 'dataset.jsonl')
    if (manifest['file']['size'] != len(data) or manifest['file']['sha256'] != hashlib.sha256(data).hexdigest()
            or manifest != expected):
        raise ValueError('The dataset content does not match its immutable manifest.')
    return data, manifest
