"""The explicit fixed decision resource and its compiled pinned asset inventory."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path

from poolhouse.decide.pins import STRANDS, STRANDS_REV, STRANDS_V19
from poolhouse.hub import hfstore

from .model_content import _fingerprint, open_model_file

RESOURCE_ID = 'decision:strands-v19'

def _regular(path: Path) -> Path:
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError('Decision assets must not contain symbolic links')
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('Decision assets must be owned regular files')
    return path


def _member(path: Path) -> dict:
    info = _regular(path).lstat()
    return {'path': str(path), 'identity': [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]}


def _hash(path: Path) -> str:
    checksum = hashlib.sha256()
    with open_model_file(_member(path)) as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
    return checksum.hexdigest()


def _json(path: Path) -> dict:
    if path.stat().st_size > 1024 * 1024:
        raise ValueError('Decision metadata exceeds the supported size')
    with open_model_file(_member(path)) as stream:
        value = json.loads(stream.read())
    if not isinstance(value, dict):
        raise ValueError('Decision metadata must be an object')
    return value


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _pin_members() -> list[dict]:
    found = []
    for pin in STRANDS_V19.files:
        repository = hfstore.repo_root(pin.repo)
        path = repository / 'snapshots' / pin.revision / pin.filename
        if any(parent.is_symlink() for parent in path.parents):
            raise ValueError('Pinned decision asset directories cannot contain links')
        canonical = path
        if path.is_symlink():
            canonical = Path(os.path.normpath(path.parent / path.readlink()))
            if canonical.parent != repository / 'blobs':
                raise ValueError('Pinned decision links must stay in their repository')
            if canonical.is_symlink():
                canonical = Path(os.path.normpath(canonical.parent / canonical.readlink()))
                name = canonical.name
                if not re.fullmatch('[0-9a-f]{64}', name) or canonical != repository.parent / 'blobs' / name[:2] / name:
                    raise ValueError('Pinned decision shared blob locator is invalid')
        info = _regular(canonical).lstat()
        if info.st_size != pin.size:
            raise ValueError('The installed decision asset does not match its pinned size')
        namespace = 'base' if pin.repo == STRANDS_V19.base_repo else 'head'
        found.append({'relative': f'{namespace}/{pin.filename}', 'path': str(canonical),
                      'size': pin.size, 'identity': _member(canonical)['identity'], 'blob_digest': pin.sha256})
    return found


def members(model: dict) -> list[dict]:
    root = hfstore.repo_root(STRANDS) / 'snapshots' / STRANDS_REV
    if model.get('id') != RESOURCE_ID or Path(model['path']) != root or model.get('fixed_source') != 'strands-v19':
        raise ValueError('Select the explicit installed pinned decision model')
    return _pin_members()


def resource() -> dict:
    value = {'kind': 'model', 'id': RESOURCE_ID, 'path': str(hfstore.repo_root(STRANDS) / 'snapshots' / STRANDS_REV),
             'fixed_source': 'strands-v19', 'studio_type': 'decision-model', 'model_kind': 'decision', 'format': 'pointer'}
    _fingerprint(members(value))
    return value


def inventory() -> list[dict]:
    try:
        resource()
        issues = []
    except (ValueError, OSError):
        issues = ['Install the pinned Strands decision model and its base to use this node']
    return [{'id': RESOURCE_ID, 'title': 'Strands decision model (pinned)', 'available': not issues,
             'type': 'decision-model', 'issues': issues}]


def _pinned(model: dict):
    entries = members(model)
    _fingerprint(entries)
    paths = {entry['relative'].split('/', 1)[1]: Path(entry['path']) for entry in entries}
    cfg = _json(paths['hobson_config.json'])
    if cfg.get('head_type') != 'pointer':
        raise ValueError('The selected decision model has no pointer head')
    return paths, cfg
