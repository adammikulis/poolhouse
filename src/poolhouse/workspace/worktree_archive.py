"""Verify complete preservation of obsolete automatic integration trials."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from poolhouse import runtime_git
from poolhouse.errors import Denied
from poolhouse.files import read_json


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


def _read_bundle(path: Path) -> bytes:
    info = path.lstat()
    if path.resolve() != path or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise Denied('obsolete trial bundle must be an owned regular canonical file')
    content = path.read_bytes()
    header, separator, _ = content.partition(b'\n\n')
    if not separator or len(header) > 65536 or not header.startswith(b'# v2 git bundle\n'):
        raise Denied('obsolete trial requires a complete version 2 Git bundle')
    if any(line.startswith(b'-') for line in header.splitlines()):
        raise Denied('obsolete trial bundle has prerequisites')
    return content


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


def proof(scope: dict[str, Any], bundle: str, published: str = '') -> dict[str, Any]:
    """Reconstruct a finite preservation proof; no caller assertion substitutes for Git evidence."""
    try:
        _absent(scope)
        tip = _published(scope, published)
        history = _history(scope)
        primary, path = Path(scope['primary']), Path(bundle)
        content = _read_bundle(path)
        version = runtime_git.git(primary, '--version')
        with tempfile.TemporaryDirectory(prefix='poolhouse-archive-') as temporary:
            root = Path(temporary).resolve()
            view, saved = root / 'repository', root / 'preserved.bundle'
            view.mkdir()
            saved.write_bytes(content)
            runtime_git.git(view, 'init', '--quiet', '--template=')
            runtime_git.git(view, 'bundle', 'verify', str(saved))
            heads = {}
            for line in runtime_git.git(view, 'bundle', 'list-heads', str(saved)).splitlines():
                commit, ref = line.split()
                if not runtime_git.COMMIT.fullmatch(commit) or not ref.startswith('refs/heads/'):
                    raise Denied('obsolete trial bundle has an unsupported recovery reference')
                runtime_git.git(view, 'check-ref-format', ref)
                heads[ref] = commit
                runtime_git.git(view, 'fetch', '--quiet', '--no-tags', str(saved), ref + ':' + ref)
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
        if _read_bundle(path) != content:
            raise Denied('obsolete trial bundle changed during verification')
        _absent(scope)
        return {'disposition': 'archived-obsolete-trial', 'scope': binding(scope),
                'bundle': str(path), 'sha256': hashlib.sha256(content).hexdigest(),
                'published': tip, 'git': version, 'close': history,
                'refs': {ref: history['head']}, 'commits': commits}
    except (OSError, ValueError, KeyError, TypeError, runtime_git.DeployError) as error:
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
