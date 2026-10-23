"""Authenticated cleanup owns its temporary claims; local attribution only restricts scope.

This preserves the existing cooperative filesystem/claim boundary, not cryptographic provenance.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from poolhouse import worktreerules
from poolhouse.board.session import Claim, Session
from poolhouse.errors import Denied
from poolhouse.lock import only_one
from poolhouse.workspace import integration_git as repo, worktree_lifecycle as lifecycle


def _scope(base: Path, owner: str, target: Path) -> dict[str, Any]:
    matches = [row for row in lifecycle.scopes(base, owner) if row['path'] == str(target)]
    if len(matches) != 1:
        raise Denied('cleanup requires this worker\'s attributed checkout')
    return matches[0]


def _binding(target: Path, scope: dict[str, Any]) -> tuple:
    primary = Path(scope['primary'])
    if target == primary or worktreerules.checkouts(target) != (target, primary):
        raise Denied('cleanup requires an attributed isolated checkout')
    branch = repo.git(target, 'branch', '--show-current')
    if branch not in scope['branches'] or branch in ('main', scope['development']):
        raise Denied('cleanup requires an attributed isolated branch')
    info = target.stat()
    return branch, repo.git(target, 'rev-parse', 'HEAD'), info.st_dev, info.st_ino, (target / '.git').read_bytes()


def _matches(s: Session, rows: list[Claim]) -> bool:
    current = {(row.kind, row.key, row.owner, row.lease) for row in s.claims()}
    return all((row.kind, row.key, s.name, row.lease) in current for row in rows)


def _release(s: Session, rows: list[Claim]) -> None:
    for row in rows:
        if _matches(s, [row]):
            s.call('lease_release', id=row.lease)


def _finished(target: Path, scope: dict[str, Any]) -> dict[str, Any]:
    proof = scope.get('cleanup', {})
    primary = Path(scope['primary'])
    registered = repo.git(primary, 'worktree', 'list', '--porcelain').splitlines()
    exact_ref = f"refs/heads/{scope['branch']}"
    branches = repo.git(primary, 'for-each-ref', '--format=%(refname)', exact_ref).splitlines()
    if (target.exists() or not proof or exact_ref in branches or
            any(line.startswith('worktree ') and Path(line[9:]).resolve() == target for line in registered) or
            not repo.ancestor(primary, proof['commit'], proof['landed'])):
        raise Denied('cleanup requires this worker\'s existing attributed checkout or verified cleanup proof')
    return {'path': str(target), **proof, 'cleanup_verified': True}


def cleanup(s: Session, base: Path, path: str) -> dict[str, Any]:
    """Reserve the authenticated worker's exact cleanup scopes, preserving unrelated leases."""
    owner = s.whoami().name
    target = Path(path).resolve()
    key = hashlib.sha256(str(target).encode()).hexdigest()
    with only_one(base / 'cleanup' / f'{key}.lock', wait=False, note='owned checkout cleanup'):
        scope = _scope(base, owner, target)
        if not target.exists():
            return _finished(target, scope)
        bound = _binding(target, scope)
        rows = s.reserve([('worktree', str(target)), ('branch', bound[0])], pid=os.getpid())
        completed = False

        def claim_owner(path: str) -> str:
            if path != str(target) or _binding(target, scope) != bound or not _matches(s, rows):
                raise Denied('cleanup scope or live reservation changed')
            for row in rows:
                s.call('lease_renew', id=row.lease)
            if not _matches(s, rows):
                raise Denied('cleanup reservation expired or changed')
            return owner

        try:
            if len(rows) != 2 or {(row.kind, row.key, row.owner) for row in rows} != {
                    ('worktree', str(target), owner), ('branch', bound[0], owner)}:
                raise Denied('cleanup received an invalid scoped reservation')
            claim_owner(str(target))
            result = lifecycle.cleanup(base, owner, str(target), claim_owner)
            _finished(target, _scope(base, owner, target))
            completed = True
            return result
        finally:
            _release(s, rows if completed else [row for row in rows if row.changed])
