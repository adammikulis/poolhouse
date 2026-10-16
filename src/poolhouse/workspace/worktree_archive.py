"""Verify complete preservation of obsolete automatic integration trials."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from poolhouse import runtime_git
from poolhouse.errors import Denied
from poolhouse.files import read_json

MAXIMUM_BUNDLE = 512 * 1024**2
HEADER = 65536
CHUNK = 1 << 20
REFUSED = 'obsolete trial bundle must be an owned regular canonical file'


def binding(scope: dict[str, Any]) -> dict[str, Any]:
    """Bind the finite attributed history, independently of its disposition."""
    return {key: scope[key] for key in ('owner', 'path', 'primary', 'development', 'commits', 'branches')}


def _absent(scope: dict[str, Any]) -> None:
    path, primary = Path(scope['path']), Path(scope['primary'])
    if path.exists() or path.is_symlink():
        raise Denied('obsolete trial checkout remains')
    registered = runtime_git.git(primary, 'worktree', 'list', '--porcelain').splitlines()
    if any(line.startswith('worktree ') and Path(line[9:]).resolve() == path for line in registered):
        raise Denied('obsolete trial worktree registration remains')


def _published(scope: dict[str, Any], published: str) -> str:
    primary = Path(scope['primary'])
    development = scope['development']
    local = runtime_git.git(primary, 'rev-parse', f'refs/heads/{development}')
    remote = runtime_git.git(primary, 'rev-parse', f'refs/remotes/origin/{development}')
    if local != remote or not runtime_git.COMMIT.fullmatch(remote):
        raise Denied('obsolete trial disposition requires synchronized published development')
    if published and runtime_git.git(primary, 'merge-base', published, remote) != published:
        raise Denied('obsolete trial publication proof changed')
    return published or remote


def _history(scope: dict[str, Any]) -> dict[str, Any]:
    primary = runtime_git.primary(Path(scope['primary']))
    history = read_json(primary / '.git/poolhouse-trees.json', {}).get('history', [])
    matches = [row for row in history
               if row.get('path') == scope['path'] and row.get('mode') == 'bundle'
               and row.get('head') in scope['commits'] and row.get('branch') in scope['branches']]
    if not matches:
        raise Denied('obsolete trial requires a maintained bundle-close record')
    return matches[-1]


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_size, info.st_mtime_ns)


def _open(path: Path) -> int:
    """Open an absolute path one component at a time, following no link, and return the file descriptor."""
    if not path.is_absolute() or '..' in path.parts:
        raise Denied(REFUSED)
    directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for name in path.parts[1:-1]:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
            os.close(directory)
            directory = child
        return os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
    finally:
        os.close(directory)


def _snapshot(path: Path, write: Callable[[bytes], object]) -> tuple[str, bytes]:
    """Stream the bundle through one held descriptor, bounded; return its SHA-256 and its first bytes."""
    descriptor = _open(path)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or before.st_size > MAXIMUM_BUNDLE:
            raise Denied(REFUSED)
        digest, head, total = hashlib.sha256(), b'', 0
        while chunk := os.read(descriptor, min(CHUNK, MAXIMUM_BUNDLE + 1 - total)):
            total += len(chunk)
            if total > MAXIMUM_BUNDLE:
                raise Denied(REFUSED)
            digest.update(chunk)
            write(chunk)
            if len(head) < HEADER + 2:
                head += chunk[:HEADER + 2 - len(head)]
        if _identity(os.fstat(descriptor)) != _identity(before) or _identity(os.lstat(path)) != _identity(before):
            raise Denied('obsolete trial bundle changed while it was read')
    finally:
        os.close(descriptor)
    return digest.hexdigest(), head


def _check_header(head: bytes) -> None:
    header, separator, _ = head.partition(b'\n\n')
    if not separator or not header.startswith(b'# v2 git bundle\n'):
        raise Denied('obsolete trial requires a complete version 2 Git bundle')
    if any(line.startswith(b'-') for line in header.splitlines()):
        raise Denied('obsolete trial bundle has prerequisites')


def _merges(view: Path, heads: list[str], published: str) -> list[str]:
    unpublished = runtime_git.git(view, 'rev-list', '--parents', *heads, '^' + published).splitlines()
    commits = []
    for line in unpublished:
        commit, *parents = line.split()
        if len(parents) != 2:
            raise Denied('obsolete trial contains unpublished source or a non-binary merge')
        actual = runtime_git.git(view, 'rev-parse', commit + '^{tree}')
        reconstructed = runtime_git.git(view, '-c', 'merge.renormalize=false', 'merge-tree',
                                        '--write-tree', *parents).splitlines()
        if not reconstructed or reconstructed[0] != actual:
            raise Denied('obsolete trial contains a meaningful merge resolution')
        commits.append(commit)
    if not commits:
        raise Denied('obsolete trial has no unpublished automatic merges')
    return sorted(commits)


def _heads(view: Path, saved: Path) -> dict[str, str]:
    """Fetch every branch of the verified bundle into the private view; any other reference is refused."""
    heads = {}
    for line in runtime_git.git(view, 'bundle', 'list-heads', str(saved)).splitlines():
        commit, ref = line.split()
        if not runtime_git.COMMIT.fullmatch(commit) or not ref.startswith('refs/heads/'):
            raise Denied('obsolete trial bundle has an unsupported recovery reference')
        runtime_git.git(view, 'check-ref-format', ref)
        heads[ref] = commit
        runtime_git.git(view, 'fetch', '--quiet', '--no-tags', str(saved), ref + ':' + ref)
    return heads


def proof(scope: dict[str, Any], bundle: str, published: str = '') -> dict[str, Any]:
    """Reconstruct a finite preservation proof; no caller assertion substitutes for Git evidence."""
    try:
        _absent(scope)
        tip = _published(scope, published)
        history = _history(scope)
        primary, path = Path(scope['primary']), Path(bundle)
        version = runtime_git.git(primary, '--version')
        with tempfile.TemporaryDirectory(prefix='poolhouse-archive-') as temporary:
            root = Path(temporary).resolve()
            view, saved = root / 'repository', root / 'preserved.bundle'
            view.mkdir()
            with saved.open('xb') as copy:
                digest, head = _snapshot(path, copy.write)
            _check_header(head)
            runtime_git.git(view, 'init', '--quiet', '--template=')
            runtime_git.git(view, 'bundle', 'verify', str(saved))
            heads = _heads(view, saved)
            runtime_git.git(view, 'fsck', '--full', '--strict', '--no-reflogs')
            reachable = set(runtime_git.git(view, 'rev-list', '--all').splitlines())
            if not set(scope['commits']).issubset(reachable):
                raise Denied('obsolete trial bundle omits recorded history')
            ref = 'refs/heads/' + history['branch']
            if heads.get(ref) != history['head']:
                raise Denied('obsolete trial bundle does not preserve the closed head')
            if runtime_git.git(primary, 'rev-parse', ref) != history['head']:
                raise Denied('obsolete trial recovery reference changed')
            objects = runtime_git.git(primary, 'rev-parse', '--path-format=absolute', '--git-path', 'objects')
            (view / '.git/objects/info/alternates').write_text(objects + '\n')
            commits = _merges(view, list(heads.values()), tip)
        if _snapshot(path, lambda _chunk: None)[0] != digest:
            raise Denied('obsolete trial bundle changed during verification')
        _absent(scope)
        return {'disposition': 'archived-obsolete-trial', 'scope': binding(scope),
                'bundle': str(path), 'sha256': digest,
                'published': tip, 'git': version, 'close': history,
                'refs': {ref: history['head']}, 'commits': commits}
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, runtime_git.DeployError) as error:
        raise Denied('obsolete trial preservation could not be verified') from error


def verified(scope: dict[str, Any]) -> dict[str, Any]:
    """Return only an unchanged, freshly validated recorded disposition."""
    recorded = scope.get('archive', {})
    if not recorded:
        return {}
    try:
        current = proof(scope, recorded['bundle'], recorded['published'])
    except (Denied, KeyError, TypeError):
        return {}
    return current if current == recorded else {}
