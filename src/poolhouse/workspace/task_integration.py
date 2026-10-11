"""Independently reviewed task branches land through the maintained development gates."""

from __future__ import annotations

import hashlib
import re
import subprocess
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from poolhouse.board.client import Conflict, NodeError
from poolhouse.board.session import Session
from poolhouse.board.tasks import Tasks
from poolhouse.errors import Denied
from poolhouse.files import read_json, write_json
from poolhouse.lock import held
from poolhouse.sentinel.redaction import redact
from poolhouse.workspace import (
    integration_git as repo,
    integration_staging,
    localagent as la,
    worktree_lifecycle,
)

__all__ = ["DevelopmentIntegration", "integrate", "public", "reviewed"]

IMMUTABLE = ('task', 'owner', 'source_commit', 'review_id', 'review_hash')
PUBLIC = ('task', 'state', 'owner', 'worker', 'review_id', 'review_hash', 'proposal_hash', 'source_commit', 'commit',
          'development', 'policy', 'at', 'blocking_owner', 'publish_requested', 'publication_verified', 'cleanup_verified')
CLAIM_S = 4 * 3600


def _now() -> int:
    return int(time.time() * 1000)


def _parent(s: Session, name: str) -> str:
    return next((a.parent for a in s.agents(True) if a.name == name), '')


def reviewed(s: Session, task_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The task and its recorded checkout, when its proposal was accepted by an independent review that names it exactly."""
    task = Tasks(s).get(task_id)
    proposal, review = task.get('proposal'), task.get('review')
    if task['state'] not in ('accepted', 'completed') or not proposal or not review \
            or not review['accepted'] or review['outcome'] != 'accepted':
        raise Denied('integration requires an independently accepted proposal')
    if s.name not in (task['worker'], task['created_by'], review['verifier'], _parent(s, task['worker'])) \
            and s.name not in la.leads(s):
        raise Denied('only the task worker, registered parent, creator or independent reviewer may request integration')
    if review['proposal_id'] != proposal['id'] or review['proposal_hash'] != proposal['proposal_hash'] \
            or review['worker'] != proposal['worker'] or task['worker'] != proposal['worker'] \
            or not all(check['passed'] for check in review['checks']):
        raise Denied('the independent acceptance does not match this exact task proposal')
    worktree = task.get('scope')
    if not worktree or worktree.get('worker') != task['worker']:
        raise Denied('the prepared worktree belongs to another task or worker')
    lease = task.get('lease')
    if not lease or lease['active'] or lease['worker'] != task['worker'] or proposal['lease_id'] != lease['id']:
        raise Denied('the reviewed task must have released its exact native execution lease')
    return task, worktree


def _source(task: dict[str, Any], worktree: dict[str, Any]) -> tuple[Path, str]:
    source = Path(worktree['project']).resolve()
    commit = task['proposal']['provenance'].get('commit', '')
    if not repo.COMMIT.fullmatch(commit):
        raise Denied('the proposal needs the full committed source SHA, not an uncommitted patch')
    repo.clean(source)
    if repo.git(source, 'rev-parse', 'HEAD') != commit \
            or repo.git(source, 'branch', '--show-current') != worktree['branch']:
        raise Denied('the source branch changed after review; submit it for a new independent review')
    if not repo.ancestor(source, worktree['baseline_commit'], commit):
        raise Denied('the reviewed source diverged from its prepared task baseline')
    repo.reviewed_files(source, commit, task['proposal']['artifacts'])
    repo.native_patch(source, commit, worktree['baseline_commit'], task['proposal']['artifacts'])
    return source, commit


def _digest(s: Session, task: dict[str, Any]) -> str:
    return hashlib.sha256(f'{s.board}:{task["id"]}:{task["review"]["id"]}'.encode()).hexdigest()


def _private(base: Path, ident: str) -> Path:
    return la.folder(base) / 'integrations' / (hashlib.sha256(ident.encode()).hexdigest()[:40] + '.json')


def _load(base: Path, ident: str) -> dict[str, Any]:
    found = read_json(_private(base, ident), {})
    return found if isinstance(found, dict) else {}


def public(record: dict[str, Any], base: Path) -> dict[str, Any]:
    """What the board shows of an integration: its state and exact bindings, never a private path or a credential."""
    shown = {key: record[key] for key in PUBLIC if key in record}
    if record.get('reason'):
        reason = re.sub(re.escape(str(base)) + r'[^\s"\']*', '[workspace private path]', str(record['reason']))
        if record.get('candidate'):
            reason = reason.replace(record['candidate'], '[integration candidate]')
        shown['reason'] = redact(reason)[:2000]
    if record.get('checks'):
        shown['checks'] = [{key: check[key] for key in ('passed', 'exit', 'output_hash') if key in check}
                           | {'command': check['command'][1:]} for check in record['checks']]
    return shown


def _event(s: Session, base: Path, integration: dict[str, Any], state: str, **details: Any) -> dict[str, Any]:
    previous = _load(base, integration['id'])
    if previous and any(previous.get(key) != integration[key] for key in IMMUTABLE):
        raise Denied('another owner or immutable review already owns this integration record')
    result = {**previous, **integration, 'state': state, **details, 'at': _now()}
    path = _private(base, integration['id'])
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_json(path, result)
    path.chmod(0o600)
    tasks = Tasks(s)
    tasks.record(integration['task'], 'integration', integration['id'], public(result, base))
    tasks.record(integration['task'], 'event', 'integration-event:' + uuid4().hex,
                 {'integration': integration['id'], **public({'state': state, **details, 'at': result['at']}, base)})
    return result


def _owner(s: Session, kind: str, key: str) -> str:
    return next((c.owner for c in s.claims(kind) if c.key == key), '')


class DevelopmentIntegration:
    """Own a candidate checkout, gate its combined tree and publish only development."""

    def __init__(self, s: Session, base: Path, task_id: str, *, publish: bool = False):
        self.s, self.base, self.publish_requested = s, base, publish
        self.task, self.worktree = reviewed(s, task_id)
        self.source, self.commit = _source(self.task, self.worktree)
        self.primary, self.development, self.before = repo.repository(self.source, require_clean=False)
        self.preserved = integration_staging.files(self.primary, self.commit)
        parent = _parent(s, self.task['worker'])
        if (self.preserved or integration_staging.merge_state(self.primary, self.commit)) and s.name != parent:
            raise Denied("only the authenticated task lead may preserve primary files")
        digest = _digest(s, self.task)
        self.ident = 'integration:' + digest
        self.branch = s.name + '/integrate-' + digest[:16]
        self.candidate = self.primary.parent / (self.primary.name + '-integrate-' + digest[:16])
        self.record = {'id': self.ident, 'task': self.task['id'], 'owner': s.name, 'worker': self.task['worker'],
                       'source_commit': self.commit, 'review_id': self.task['review']['id'],
                       'review_hash': self.task['review']['review_hash'],
                       'proposal_hash': self.task['proposal']['proposal_hash'],
                       'development': self.development, 'before': self.before,
                       'candidate': str(self.candidate), 'branch': self.branch,
                       'primary': str(self.primary), 'source': str(self.source),
                       'source_branch': self.worktree['branch'], 'publish_requested': publish,
                       'preserved': self.preserved, 'policy': 'development-integration-v1'}

    def _claim(self, kind: str, key: str) -> None:
        self.s.claim(kind, key, ttl_s=CLAIM_S)

    def prepare(self) -> None:
        if integration_staging.files(self.primary, self.commit) != self.preserved:
            raise Denied("primary preservation changed before integration")
        if repo.git(self.primary, 'rev-parse', 'HEAD') != self.before:
            raise Denied('development changed before integration acquired its lock')
        repo.remote_baseline(self.primary, self.development, self.before)
        self._claim('branch', self.development)
        self._claim('area', str(self.primary / '.poolhouse-integration'))
        for name in self.preserved:
            self._claim('area', str(self.primary / name))
        self._claim('branch', self.branch)
        self._claim('worktree', str(self.candidate))
        owner = _owner(self.s, 'worktree', str(self.source))
        worker = self.worktree['worker']
        if owner == worker:
            if self.s.name != _parent(self.s, worker):
                raise Denied('the current child worktree claim requires its registered parent to return it')
            self.s.release('worktree', str(self.source), held_by=worker)
            self.s.release('branch', self.worktree['branch'], held_by=worker)
            for held in self.s.claims('area'):
                if held.owner == worker and Path(held.key).is_relative_to(self.source):
                    self.s.release('area', held.key, held_by=worker)
            _event(self.s, self.base, self.record, 'scope_returned', scope_owner=self.s.name, worker=worker)
            owner = ''
        if owner not in ('', self.s.name):
            raise Denied('the prepared task source is held by another session')
        self._claim('worktree', str(self.source))
        self._claim('branch', self.worktree['branch'])
        if not Path(repo.git(self.source, 'rev-parse', '--git-path', 'locked')).exists():
            repo.git(self.primary, 'worktree', 'lock', '--reason', self.ident, str(self.source))
        if self.candidate.exists():
            raise Denied('the owned candidate already exists; inspect its recorded outcome before retrying')
        repo.git(self.primary, 'worktree', 'add', '-b', self.branch, str(self.candidate), self.before)
        repo.git(self.primary, 'worktree', 'lock', '--reason', self.ident, str(self.candidate))
        repo.git(self.candidate, 'merge', '--no-edit', self.commit)
        tip = repo.git(self.candidate, 'rev-parse', 'HEAD')
        repo.reviewed_files(self.candidate, tip, self.task['proposal']['artifacts'])

    def publish(self, checks: list[dict]) -> dict:
        self._claim('branch', self.development)
        primary, branch, before = repo.repository(self.source, require_clean=False)
        if (primary, branch, before) != (self.primary, self.development, self.before):
            raise Denied('development changed during gating; preserve candidate and recheck the new baseline')
        current, worktree = reviewed(self.s, self.task['id'])
        if current['review']['review_hash'] != self.record['review_hash'] \
                or current['proposal']['proposal_hash'] != self.record['proposal_hash'] \
                or worktree != self.worktree:
            raise Denied('the review or prepared task binding changed during gating')
        _source(self.task, self.worktree)
        repo.clean(self.candidate)
        repo.remote_baseline(self.primary, self.development, self.before)
        tip = repo.git(self.candidate, 'rev-parse', 'HEAD')
        for name in self.preserved:
            self._claim('area', str(self.primary / name))
        integration_staging.verify(self.primary, tip, self.preserved)
        if integration_staging.merge_state(self.primary, tip):
            repo.git(self.primary, 'merge', '--quit')
        repo.git(self.primary, 'merge', '--ff-only', tip)
        _event(self.s, self.base, self.record, 'integrated', commit=tip, checks=checks)
        if repo.git(self.primary, 'rev-parse', 'HEAD') != tip:
            raise Denied('development moved after its fast-forward; publication must be rechecked')
        return self.finish(tip, checks)

    def finish(self, tip: str, checks: list[dict]) -> dict:
        repo.clean(self.primary)
        if repo.git(self.primary, 'branch', '--show-current') != self.development \
                or not repo.ancestor(self.primary, tip, repo.git(self.primary, 'rev-parse', 'HEAD')):
            raise Denied('the task commit is not landed on the current development branch')
        if self.publish_requested and not self.record.get('publication_verified'):
            repo.remote_baseline(self.primary, self.development, tip)
            repo.git(self.primary, 'push', 'origin', f'{tip}:refs/heads/{self.development}')
            remote = repo.git(self.primary, 'ls-remote', 'origin', f'refs/heads/{self.development}').split()[0]
            if remote != tip:
                raise Denied('remote development did not confirm the exact gated commit')
            self.record = _event(self.s, self.base, self.record, 'publication_confirmed', commit=tip,
                                 checks=checks, publication_verified=True)
        _event(self.s, self.base, self.record, 'cleanup_required', commit=tip, checks=checks)
        for path, branch in ((self.source, self.worktree['branch']), (self.candidate, self.branch)):
            if path.exists() and _owner(self.s, 'worktree', str(path)) != self.record['owner']:
                raise Denied('cleanup requires the live worktree claim of this integration owner')
            for scope in worktree_lifecycle.scopes(self.base, self.record['owner']):
                if scope['path'] == str(path) and path.exists():
                    worktree_lifecycle.remember(self.base, scope['owner'], str(path))
            commit = repo.git(path, 'rev-parse', 'HEAD') if path.exists() else ''
            repo.remove_merged(self.primary, path, branch, tip, lock_reason=self.record['id'])
            if commit:
                worktree_lifecycle.record_cleanup(self.base, path, (commit, tip), self.primary, self.development)
        Tasks(self.s).scope(self.task['id'], {**self.worktree, 'state': 'cleaned', 'landed_commit': tip})
        Tasks(self.s).complete(self.task['id'], tip)
        for kind, key in (('worktree', str(self.source)), ('branch', self.worktree['branch']),
                          ('worktree', str(self.candidate)), ('branch', self.branch),
                          ('branch', self.development)):
            if _owner(self.s, kind, key) == self.record['owner']:
                self.s.release(kind, key)
        for name in self.record.get('preserved', []):
            if _owner(self.s, 'area', str(self.primary / name)) == self.record['owner']:
                self.s.release('area', str(self.primary / name))
        return _event(self.s, self.base, self.record, 'published' if self.publish_requested else 'completed',
                      commit=tip, checks=checks, cleanup_verified=True)

    def run(self) -> dict:
        with held(self.primary / '.git' / 'poolhouse-integration.lock'):
            try:
                self.prepare()
                _event(self.s, self.base, self.record, 'candidate')
                checks = repo.gates(self.candidate, self.before)
                return self.publish(checks)
            except (Denied, NodeError, OSError, RuntimeError, subprocess.SubprocessError) as error:
                details: dict[str, Any] = {'reason': str(error)}
                if isinstance(error, repo.GateFailed):
                    details['checks'] = error.checks
                if isinstance(error, Conflict):
                    details['blocking_owner'] = str(error).rpartition(' is held by ')[2] or str(error)
                return _event(self.s, self.base, self.record, 'blocked', **details)
            finally:
                area = str(self.primary / '.poolhouse-integration')
                if _owner(self.s, 'area', area) == self.record['owner']:
                    self.s.release('area', area)


def integrate(s: Session, base: Path, task_id: str, *, publish: bool = False) -> dict:
    """Land and clean a reviewed task; publication requires explicit authorization."""
    task, worktree = reviewed(s, task_id)
    previous = _load(base, 'integration:' + _digest(s, task))
    if previous and previous.get('cleanup_verified'):
        primary = Path(previous['primary'])
        with held(primary / '.git' / 'poolhouse-integration.lock'):
            registered = repo.git(primary, 'worktree', 'list', '--porcelain').splitlines()
            for path, branch in ((Path(previous['source']), previous['source_branch']),
                                 (Path(previous['candidate']), previous['branch'])):
                refs = repo.git(primary, 'for-each-ref', '--format=%(refname)', f'refs/heads/{branch}').splitlines()
                if path.exists() or f'refs/heads/{branch}' in refs or any(
                        line.startswith('worktree ') and Path(line[9:]).resolve() == path.resolve()
                        for line in registered):
                    raise Denied('a completed task checkout, registration or branch reappeared; preserve it before completion')
            return previous
    if previous and previous['state'] in ('integrated', 'publication_confirmed', 'cleanup_required', 'blocked') \
            and previous.get('commit'):
        operation = DevelopmentIntegration.__new__(DevelopmentIntegration)
        operation.s, operation.base, operation.task, operation.worktree = s, base, task, worktree
        operation.record = previous
        operation.source, operation.primary = Path(previous['source']), Path(previous['primary'])
        operation.candidate, operation.branch = Path(previous['candidate']), previous['branch']
        operation.development, operation.publish_requested = previous['development'], publish
        with held(operation.primary / '.git' / 'poolhouse-integration.lock'):
            return operation.finish(previous['commit'], previous['checks'])
    return DevelopmentIntegration(s, base, task_id, publish=publish).run()
