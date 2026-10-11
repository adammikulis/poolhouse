"""Durable ownership and completion checks for isolated coding checkouts on this device."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from poolhouse import worktreerules
from poolhouse.errors import Denied
from poolhouse.files import read_json, write_json
from poolhouse.lock import held
from poolhouse.workspace import integration_git as repo

__all__ = ["assigned", "assigned_file", "checkpoint", "cleanup", "pending", "record_cleanup", "remember", "require_clean", "scopes"]

FILE = "worktree-lifecycle.json"


def _read(base: Path) -> dict[str, dict[str, Any]]:
    found = read_json(base / FILE, {})
    rows = found.get("scopes") if isinstance(found, dict) else None
    return dict(rows) if isinstance(rows, dict) else {}


def _edit(base: Path, change: Callable[[dict[str, dict[str, Any]]], None]) -> None:
    """Apply ``change`` to the recorded scopes under the lock and write them down."""
    with held(base / "worktree-lifecycle.lock"):
        rows = _read(base)
        change(rows)
        write_json(base / FILE, {"version": 1, "scopes": rows})
        (base / FILE).chmod(0o600)


def assigned_file(base: Path, identity: str) -> Path:
    """The file naming the checkout the session ``identity`` works in for its task, while it does; the task's own landing cleans it."""
    return base / "local-agents" / f"{identity}.assigned"


def assigned(base: Path, identity: str) -> str:
    """The checkout the session ``identity`` is working in for its task now, or an empty string."""
    try:
        return assigned_file(base, identity).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def scopes(base: Path, owner: str) -> list[dict[str, Any]]:
    """The checkouts ``owner`` was recorded to hold."""
    return [row for row in _read(base).values() if row["owner"] == owner]


def remember(base: Path, owner: str, path: str) -> None:
    """Record an authenticated worker's reserved or materialized coding checkout."""
    found = worktreerules.checkouts(path)
    if (found and found[0] == found[1]) or (not found and Path(path).exists()):
        return
    checkout = found[0] if found else Path(path).resolve()
    primary = found[1] if found else None
    branch = repo.git(checkout, 'branch', '--show-current') if found else ''
    if found and not branch:
        raise Denied('coding worktree ownership requires a named branch')
    key = 'worktree-lifecycle:' + hashlib.sha256(f'{owner}:{checkout}'.encode()).hexdigest()
    commit = repo.git(checkout, 'rev-parse', 'HEAD') if found else ''
    development = repo.git(primary, 'branch', '--show-current') if primary else ''
    value = {'owner': owner, 'path': str(checkout),
             'primary': str(primary) if primary else '', 'branch': branch, 'development': development}

    def merged(rows: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
        previous = rows.get(key, {})
        wanted = dict(value)
        if not found and previous.get('primary'):
            wanted.update({name: previous[name] for name in ('primary', 'branch', 'development')})
        branches = sorted(set(previous.get('branches', [])) | ({branch} if branch else set()))
        commits = sorted(set(previous.get('commits', [])) | ({commit} if commit else set()))
        return previous, {**wanted, 'branches': branches, 'commits': commits}

    previous, attrs = merged(_read(base))
    if previous == attrs:
        return
    _edit(base, lambda rows: rows.__setitem__(key, merged(rows)[1]))


def checkpoint(base: Path, owner: str) -> None:
    """Capture current commits from the authenticated worker's surviving scopes."""
    for scope in scopes(base, owner):
        path = Path(scope['path'])
        if path.exists():
            remember(base, owner, str(path))


def record_cleanup(base: Path, path: Path, proof: tuple[str, str], primary: Path, development: str) -> None:
    """Record a completed maintained removal with its captured source and landed commits."""
    commit, landed = proof

    def change(rows: dict[str, dict[str, Any]]) -> None:
        for key, scope in rows.items():
            if scope['path'] != str(path):
                continue
            if path.exists():
                raise Denied('cleanup proof requires an absent checkout')
            if scope['primary'] and Path(scope['primary']) != primary:
                raise Denied('cleanup proof repository differs from the attributed checkout')
            if not repo.ancestor(primary, commit, landed):
                raise Denied('cleanup proof requires the exact source commit to be landed')
            rows[key] = {**scope, 'primary': str(primary), 'development': development,
                         'commits': sorted(set(scope['commits']) | {commit}),
                         'cleanup': {'commit': commit, 'landed': landed}}

    _edit(base, change)


def cleanup(base: Path, owner: str, path: str, claim_owner: Callable[[str], str]) -> dict[str, Any]:
    """Remove a claimed landed checkout and record its final commit after verified cleanup; ``claim_owner`` names who
    holds the worktree claim of a path."""
    target = Path(path).resolve()
    owned = [scope for scope in scopes(base, owner) if scope['path'] == str(target)]
    if not owned or not target.exists():
        raise Denied('cleanup requires this worker\'s existing attributed checkout')
    if claim_owner(str(target)) != owner:
        raise Denied('cleanup requires the live checkout claim of this worker')
    checkpoint(base, owner)
    owned = [scope for scope in scopes(base, owner) if scope['path'] == str(target)]
    source = owned[0]
    primary = Path(source['primary'])
    if worktreerules.checkouts(target) != (target, primary):
        raise Denied('cleanup checkout identity changed')
    branch = repo.git(target, 'branch', '--show-current')
    if branch not in source['branches'] or branch in ('main', source['development']):
        raise Denied('cleanup requires an attributed isolated branch')
    locked = target / repo.git(target, 'rev-parse', '--git-path', 'locked')
    if locked.exists():
        raise Denied('cleanup preserves locked worktrees')
    commit = repo.git(target, 'rev-parse', 'HEAD')
    landed = repo.git(primary, 'rev-parse', f"refs/heads/{source['development']}")
    repo.remove_merged(primary, target, branch, landed)
    record_cleanup(base, target, (commit, landed), primary, source['development'])
    return {'path': str(target), 'commit': commit, 'landed': landed, 'cleanup_verified': True}


def pending(base: Path, owner: str) -> list[dict[str, Any]]:
    """Inspect the worker's durable scopes without removing files or Git references."""
    result = []
    for scope in scopes(base, owner):
        path = Path(scope['path'])
        if scope['primary'] and path.exists():
            remember(base, scope['owner'], str(path))
            scope = next(row for row in scopes(base, owner) if row['path'] == str(path))
        if not scope['primary']:
            found = worktreerules.checkouts(path)
            if not found or found[0] == found[1]:
                if path.exists():
                    result.append({**scope, 'reasons': ['reserved checkout path remains']})
                continue
            remember(base, scope['owner'], scope['path'])
            scope = next(row for row in _read(base).values()
                         if row['path'] == str(found[0]) and row['owner'] == scope['owner'])
            path = Path(scope['path'])
        primary = Path(scope['primary'])
        registered = repo.git(primary, 'worktree', 'list', '--porcelain').splitlines()
        refs = repo.git(primary, 'for-each-ref', '--format=%(refname)', 'refs/heads/').splitlines()
        reasons = []
        tip = repo.git(primary, 'rev-parse', f"refs/heads/{scope['development']}")
        unlanded = [commit for commit in scope['commits'] if not repo.ancestor(primary, commit, tip)]
        if unlanded:
            reasons.append('recorded commits are not landed: ' + ', '.join(unlanded))
        if path.exists():
            reasons.append('checkout remains')
        elif not scope.get('cleanup'):
            reasons.append('verified cleanup proof is missing')
        if any(line.startswith('worktree ') and Path(line[9:]).resolve() == path
               for line in registered):
            reasons.append('worktree registration remains')
        remaining = [branch for branch in scope['branches'] if f'refs/heads/{branch}' in refs]
        if remaining:
            reasons.append('branches remain: ' + ', '.join(remaining))
        if reasons:
            result.append({**scope, 'reasons': reasons})
    return result


def require_clean(base: Path, owner: str, *, within: tuple[str, ...] = ()) -> None:
    """Refuse completion while this worker's own attributed checkout, registration or branch
    remains. With ``within`` only the checkouts at or around those roots: another worker's checkout never blocks it."""
    rows = pending(base, owner)
    if within:
        roots = [Path(r).resolve() for r in within]
        rows = [row for row in rows if any(
            (here := Path(row['path']).resolve()) == r or r in here.parents or here in r.parents
            for r in roots)]
    if rows:
        detail = '; '.join(f"{row['path']} ({row['branch']}): {', '.join(row['reasons'])}" for row in rows)
        raise Denied('completion requires landed work and verified cleanup: ' + detail)
