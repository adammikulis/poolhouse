"""Reuse existing compute/metadata checks only while their complete evidence is unchanged."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from poolhouse import private_path

from . import compute_artifact
from .managed_compute import process_environment

_LOCKS = {}
_LOCK = threading.Lock()
_LAYOUT = """import json,sys,sysconfig,os,site
print(json.dumps({'prefix':sys.prefix,'executable':sys.executable,
 'roots':sorted(set(sysconfig.get_paths()[key] for key in ('purelib','platlib'))),
 'abi':sys.implementation.cache_tag,'paths':sys.path,'user_site':site.ENABLE_USER_SITE,
 'standard':[sysconfig.get_path('stdlib'),sysconfig.get_path('platstdlib'),
 os.path.join(sysconfig.get_path('stdlib'),'lib-dynload'),
 os.path.join(sys.base_prefix,'lib',f'python{sys.version_info.major}{sys.version_info.minor}.zip')]}))
"""


@dataclass(frozen=True)
class Evidence:
    key: tuple
    layout: tuple
    signature: tuple
    payload: str


def _identity(path, *, links=False):
    value = path.lstat()
    if ((not links and private_path.redirected(value))
            or (not stat.S_ISLNK(value.st_mode) and value.st_mode & 0o022)):
        raise OSError('Managed dependency evidence contains linked or writable paths')
    owners = {0, os.getuid()} if hasattr(os, 'getuid') else {value.st_uid}
    if value.st_uid not in owners:
        raise OSError('Managed dependency evidence belongs to another account')
    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
            value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _parents(path):
    rows = []
    for parent in reversed(path.parents):
        info = parent.lstat()
        if private_path.redirected(info) or not stat.S_ISDIR(info.st_mode):
            raise OSError('Managed dependency directories cannot be linked')
        rows.append((str(parent), (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)))
    return rows


def _interpreter(path):
    rows = _parents(path)
    current = path
    seen = set()
    while True:
        if current in seen:
            raise OSError('Managed interpreter link cycle')
        seen.add(current)
        identity = _identity(current, links=True)
        if current.is_symlink():
            target = str(current.readlink())
            rows.append((str(current), identity, target))
            current = Path(os.path.normpath(current.parent / target))
            rows.extend(_parents(current))
        else:
            if not stat.S_ISREG(identity[2]):
                raise OSError('Managed interpreter is not a regular executable')
            rows.append((str(current), identity))
            return tuple(rows)


def _file(path):
    identity = _identity(path)
    if not stat.S_ISREG(identity[2]):
        raise OSError('Managed dependency evidence is not a regular file')
    return identity


def _context(environment):
    prefix = environment.path.absolute()
    expected = compute_artifact.expected()
    manifest = Path(compute_artifact.__file__).with_name('compute-artifact.json')
    runtime = manifest.with_name('built-from')
    app = (_file(manifest), _file(runtime), tuple(_parents(manifest)),
           hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest())
    key = (os.getpid(), str(prefix), _identity(prefix), _interpreter(environment.python.absolute()),
           _file(prefix / 'pyvenv.cfg'), app)
    return key, expected


def _layout(environment):
    result = subprocess.run([str(environment.python), '-B', '-I', '-c', _LAYOUT],
                            capture_output=True, text=True, timeout=20, env=process_environment())
    if result.returncode:
        raise OSError('The managed dependency layout could not be verified')
    value = json.loads(result.stdout)
    prefix = environment.path.absolute()
    if (not isinstance(value, dict) or set(value) != {'prefix', 'executable', 'roots', 'abi', 'paths', 'standard', 'user_site'}
            or value['prefix'] != str(prefix)
            or value['executable'] != str(environment.python.absolute()) or not isinstance(value['roots'], list)
            or not value['roots'] or not isinstance(value['abi'], str) or (value['user_site'] is not None and type(value['user_site']) is not bool)
            or any(not isinstance(value[field], list) or not all(isinstance(item, str) for item in value[field])
                   for field in ('roots', 'paths', 'standard'))):
        raise OSError('The managed dependency layout belongs to another interpreter')
    roots = tuple(Path(name) for name in value['roots'])
    if any(not root.is_absolute() or not root.is_relative_to(prefix) or root.name != 'site-packages' for root in roots):
        raise OSError('Managed dependency metadata must remain inside its interpreter')
    standard = tuple(Path(name) for name in value['standard'])
    if any(not path.is_absolute() for path in standard):
        raise OSError('The managed standard library layout is invalid')
    return (value['abi'], roots, standard, tuple(value['paths']), value['user_site'])


def _cacheable(layout):
    selectors = {'PYTHONHOME', 'PYTHONPATH', 'PYTHONUSERBASE', 'PYTHONPLATLIBDIR',
                 'PYTHONCASEOK', 'PYTHONEXECUTABLE', '__PYVENV_LAUNCHER__', 'PYTHON_PRESITE'}
    if layout[4] is not False or any(os.environ.get(name) for name in selectors):
        return False
    locations = (*layout[1], *layout[2], Path.cwd())
    if any((root / name).exists() or (root / name).is_symlink() for root in locations
           for name in ('sitecustomize.py', 'sitecustomize.pyc', 'sitecustomize',
                        'usercustomize.py', 'usercustomize.pyc', 'usercustomize')):
        return False
    if any(Path.cwd().glob('*.dist-info')) or any(Path.cwd().glob('*.egg-info')):
        return False
    if any((Path.cwd() / name).exists() for name in ('pip.py', 'pip.pyc', 'pip')):
        return False
    allowed = {str(path) for path in (*layout[1], *layout[2])}
    if any(name not in allowed for name in layout[3]):
        return False
    for root in layout[1]:
        for path in root.glob('*.pth'):
            if _file(path)[6] > 65536:
                return False
            for line in path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if line.startswith(('import ', 'import\t')) or str(root / line) not in allowed:
                    return False
    return True


def _signature(layout, expected):
    rows = {}
    def parents(path):
        for parent in reversed(path.parents):
            if str(parent) not in rows:
                info = parent.lstat()
                if private_path.redirected(info) or not stat.S_ISDIR(info.st_mode):
                    raise OSError('Managed dependency directories cannot be linked')
                rows[str(parent)] = (_identity(parent) if any(parent.is_relative_to(root) for root in layout[1])
                                     else (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid))
    def add(path, current=None):
        current = _identity(path) if current is None else current
        previous = rows.get(str(path))
        if previous is not None and len(previous) == 9 and previous != current:
            raise OSError('Managed dependency evidence changed during enumeration')
        rows[str(path)] = current
    def walk(path):
        add(path)
        if path.is_dir():
            with os.scandir(path) as listing:
                entries = sorted((Path(entry.path) for entry in listing), key=str)
            for entry in entries:
                walk(entry)
        elif not path.is_file():
            raise OSError('Managed dependency evidence contains a nonregular entry')
    names = set(expected['files']) | set(expected.get('base_files', {})) | {'poolhouse/fleet/built-from'}
    for root in layout[1]:
        parents(root)
        add(root)
        for name in names:
            relative = PurePosixPath(name)
            if relative.is_absolute() or '..' in relative.parts or '\\' in name:
                raise OSError('Managed dependency manifest contains an unsafe member')
            path = root / name
            if path.exists() or path.is_symlink():
                parents(path)
                add(path, _file(path))
        with os.scandir(root) as listing:
            entries = sorted((Path(entry.path) for entry in listing), key=str)
        for entry in entries:
            add(entry)
            if entry.name.endswith(('.dist-info', '.egg-info')):
                walk(entry)
        for name in compute_artifact.OWNED_ROOTS:
            folder = root / name
            if folder.exists() or folder.is_symlink():
                parents(folder)
                walk(folder)
        manifest = root / 'poolhouse/fleet/compute-artifact.json'
        add(manifest, _file(manifest))
    for root in layout[2]:
        if root.exists() or root.is_symlink():
            parents(root)
            add(root)
            if root.is_dir():
                for entry in root.iterdir():
                    if entry.name.endswith(('.dist-info', '.egg-info')):
                        walk(entry)
    return _restat(rows)


def _restat(rows):
    for name, observed in rows.items():
        path = Path(name)
        if len(observed) == 9:
            current = _identity(path)
        else:
            info = path.lstat()
            current = (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)
        if current != observed:
            raise OSError('Managed dependency evidence changed during enumeration')
    return tuple(sorted(rows.items()))


def read(environment):
    """The caller holds maintenance.read before obtaining this coalescing lock."""
    place = (os.getpid(), str(environment.path.absolute()))
    with _LOCK:
        guard = _LOCKS.setdefault(place, threading.Lock())
    with guard:
        key, expected = _context(environment)
        cached = environment._cache.get('dependency_proof')
        layout = cached.layout if isinstance(cached, Evidence) and cached.key == key else _layout(environment)
        if not _cacheable(layout):
            environment._cache.pop('dependency_proof', None)
            return _verified(environment)
        before = _signature(layout, expected)
        if isinstance(cached, Evidence) and cached.key == key and cached.signature == before and key == _context(environment)[0]:
            return json.loads(cached.payload)
        environment._cache.pop('dependency_proof', None)
        result = _verified(environment)
        error, companion, packages = result['error'], result['companion'], result['packages']
        after = _signature(layout, expected)
        if key != _context(environment)[0] or before != after:
            raise OSError('Managed dependency evidence changed during verification')
        result = {'companion': companion, 'error': error, 'packages': packages}
        if (error is None and companion == expected['sha256']
                and {'poolhouse', compute_artifact.DISTRIBUTION}.issubset(packages)):
            environment._cache['dependency_proof'] = Evidence(key, layout, after, json.dumps(result, sort_keys=True))
        return copy.deepcopy(result)


def _verified(environment):
    error, companion = None, None
    try:
        companion = environment.require_compute()
    except OSError as failure:
        error = str(failure)
    return {'companion': companion, 'error': error, 'packages': environment.installed()}
