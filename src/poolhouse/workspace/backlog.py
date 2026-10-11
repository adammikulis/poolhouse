"""Authorized repository issues cached and leased in the workspace's issue ledger."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from poolhouse import worktreerules
from poolhouse.board.session import Session
from poolhouse.errors import Denied
from poolhouse.files import read_json, write_json
from poolhouse.lock import held
from poolhouse.serve.process import pid_exists
from poolhouse.workspace import localagent as la
from poolhouse.workspace.plain import line

POLL_S = 60
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
ISSUE_REF = re.compile(r"^([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#([1-9][0-9]*)$")
LEDGER = "issue-backlog.json"
Ledger = dict[str, Any]


def repository(project: str | Path) -> str:
    """Return the GitHub owner/repository named by the checkout's origin, if any."""
    try:
        result = subprocess.run(["git", "remote", "get-url", "origin"], cwd=project,
                                capture_output=True, text=True, timeout=5, check=True)
    except (OSError, subprocess.SubprocessError):
        return ""
    value = result.stdout.strip()
    match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?", value)
    return match.group(1) if match and REPO.fullmatch(match.group(1)) \
        and '..' not in match.group(1) else ""


def edited(base: Path, change: Callable[[Ledger], Any]) -> Any:
    """What ``change`` returns after it edited the ledger under its lock; the ledger is written when it changed."""
    with held(base / "issue-backlog.lock"):
        found = read_json(base / LEDGER, {})
        ledger: Ledger = found if isinstance(found, dict) else {}
        before = json.dumps(ledger, sort_keys=True)
        result = change(ledger)
        if json.dumps(ledger, sort_keys=True) != before:
            write_json(base / LEDGER, ledger)
            (base / LEDGER).chmod(0o600)
        return result


def subscribe_issue(s: Session, base: Path, ref: str, enabled: bool = True) -> dict[str, Any]:
    """Follow or stop following one ``OWNER/REPO#NUMBER`` issue in the caller's inbox."""
    match = ISSUE_REF.fullmatch(ref)
    if not match or not REPO.fullmatch(match.group(1)) or '..' in match.group(1):
        raise ValueError('issue reference must be OWNER/REPO#NUMBER')
    repo, number = match.group(1), int(match.group(2))
    edited(base, lambda ledger: ledger.setdefault("watches", {}).setdefault(f"{repo}#{number}", {}).__setitem__(s.name, bool(enabled)))
    return {'repo': repo, 'issue': number, 'subscriber': s.name, 'subscribed': bool(enabled)}


def issue_watchers(base: Path, repo: str, number: int) -> list[str]:
    """Return sessions following one source issue."""
    found = edited(base, lambda ledger: dict(ledger.get("watches", {}).get(f"{repo}#{number}", {})))
    return sorted(name for name, on in found.items() if on)


def scope_of(base: Path, agent: la.Agent) -> dict[str, Any]:
    """The repository scope saved with the worker, as it is now."""
    fresh = la.load(base, agent.name)
    return (fresh.extra.get("backlog") or {}) if fresh else {}


def configure(s: Session, base: Path, name: str, repo: str, project: str) -> dict[str, Any]:
    """Authorize one coding worker to select local work from a repository's open issues."""
    agent = la.load(base, name)
    if agent is None or agent.profile != "coding":
        raise ValueError("repository backlog requires an existing coding worker")
    if not la.manages(s, agent):
        raise Denied("only the person or this worker's registered parent may enable its backlog")
    if not REPO.fullmatch(repo) or ".." in repo:
        raise ValueError("repository must be owner/name")
    folder = la.check_project(project, base)
    if not folder:
        raise ValueError("repository backlog requires a source repository")
    if not worktreerules.checkouts(folder):
        raise ValueError("repository backlog requires a registered git checkout")
    if folder != agent.project:
        if not la.is_person(s):
            raise Denied("only the person may redirect a worker to a different project")
        agent = replace(agent, project=folder)
        la.save(base, agent)
    scope = {"repo": repo, "project": folder, "authority": s.name, "enabled": True}
    la.save(base, replace(agent, extra={**agent.extra, "backlog": scope}))
    return scope


def _decision(s: Session, base: Path, name: str, decision: tuple[Any, Any], what: str) -> tuple[la.Agent, dict[str, Any], str, str]:
    number, reason = decision
    agent = la.load(base, name)
    if agent is None:
        raise ValueError('the coding worker does not exist')
    if not la.manages(s, agent):
        raise Denied(f'only the person or registered worker parent may {what} an issue')
    scope = scope_of(base, agent)
    if not scope.get('enabled'):
        raise ValueError('configure the worker repository backlog first')
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        raise ValueError('issue number must be a positive integer')
    reason = line(reason, 400).strip()
    if not reason:
        raise ValueError('an explicit decision reason is required')
    return agent, scope, f"issue:{scope['repo']}:{number}", reason


def supersede(s: Session, base: Path, name: str, number: int, reason: str) -> dict[str, Any]:
    """Record an authorized repository decision that excludes an obsolete issue."""
    _, _, key, reason = _decision(s, base, name, (number, reason), 'supersede')

    def record(ledger: Ledger) -> dict[str, Any]:
        made = {**ledger.get("records", {}).get(key, {}), 'state': 'superseded', 'decision_by': s.name,
                'decision_at': time.time(), 'summary': reason}
        ledger.setdefault("records", {})[key] = made
        return made

    return edited(base, record)


def resume(s: Session, base: Path, name: str, number: int, reason: str) -> dict[str, Any]:
    """Authorize one blocked legacy intake retry while preserving its history."""
    agent, _, key, reason = _decision(s, base, name, (number, reason), 'resume')

    def record(ledger: Ledger) -> dict[str, Any]:
        prior = ledger.get("records", {}).get(key, {})
        if prior.get('state') != 'blocked':
            raise ValueError('only an explicitly blocked issue projection can resume')
        if prior.get('owner') != agent.identity:
            raise Denied('the blocked projection belongs to another worker')
        if any(row['issue']['key'] == key for row in ledger.get("dispatch", {}).values()):
            raise Denied('resume the task on the board with its existing retry budget')
        made = {**prior, 'state': 'ready', 'retry_budget': 1, 'resume_by': s.name, 'resume_reason': reason,
                'prior_attempt': 'issue-attempt:' + uuid4().hex}
        ledger.setdefault("records", {})[key] = made
        return made

    return edited(base, record)


def fetch(repo: str) -> list[dict[str, Any]]:
    """Read at most one hundred open issues through the installed GitHub CLI."""
    binary = shutil.which("gh")
    if not binary:
        raise RuntimeError("install and authenticate gh to read repository issues")
    result = subprocess.run([binary, "issue", "list", "--repo", repo, "--state", "open",
        "--limit", "100", "--json", "number,title,body,url,updatedAt,assignees,labels"],
        capture_output=True, text=True, timeout=30, check=True)
    rows = json.loads(result.stdout)
    if not isinstance(rows, list):
        raise ValueError("GitHub returned an invalid issue list")
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get("number"), int)
            and not isinstance(r["number"], bool) and r["number"] > 0
            and r.get("url") == f"https://github.com/{repo}/issues/{r['number']}"]


def _cache(base: Path, repo: str, fetcher: Callable[[str], list[dict[str, Any]]], now: float) -> str:
    before = edited(base, lambda ledger: dict(ledger.get("poll", {}).get(repo, {})))
    if now < before.get("next_poll", 0):
        return str(before.get("error", ""))
    try:
        issues, error = fetcher(repo), ""
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        issues, error = [], line(str(exc), 240)

    def save(ledger: Ledger) -> None:
        ledger.setdefault("poll", {})[repo] = {"next_poll": now + POLL_S, "error": error}
        if not error:
            ledger.setdefault("issues", {})[repo] = {"rows": issues}

    edited(base, save)
    return error


def pick(base: Path, agent: la.Agent, *, fetcher: Callable[[str], list[dict[str, Any]]] = fetch,
         clock: Callable[[], float] = time.time, exclude: frozenset[str] | set[str] = frozenset()) -> tuple[dict[str, Any] | None, str]:
    """Lease the next eligible issue, or return its idle/blocked explanation."""
    scope = scope_of(base, agent)
    if not scope.get("enabled"):
        return None, ""
    now, repo = clock(), scope["repo"]
    error = _cache(base, repo, fetcher, now)
    if error:
        return None, f"Repository intake blocked: {error}"

    def lease(ledger: Ledger) -> dict[str, Any] | None:
        rows = ledger.get("issues", {}).get(repo, {}).get("rows", [])
        records = ledger.setdefault("records", {})
        for issue in sorted(rows, key=lambda r: r["number"]):
            if issue.get("assignees") or any(label.get("name", "").lower() in
                    ("blocked", "wontfix", "duplicate") for label in issue.get("labels", [])):
                continue
            key = f"issue:{repo}:{issue['number']}"
            if key in exclude:
                continue
            old = records.get(key, {})
            if old.get('state') in ('blocked', 'superseded'):
                continue
            if old.get("state") == "working" and pid_exists(old.get("pid", 0)):
                continue
            if old.get("revision") == issue.get("updatedAt") and (
                    old.get("state") == "proposed"
                    or old.get("next_try", 0) > now):
                continue
            if old.get("resume_decision") and old.get("state") != "proposed" and old.get("retry_budget", 0) < 1:
                continue
            record = {**old, "revision": issue.get("updatedAt"), "state": "working",
                      "owner": agent.identity, "pid": os.getpid(), "started": now}
            if old.get("state") == "ready":
                record["retry_budget"] = old.get("retry_budget", 1) - 1
            if old.get("revision") != issue.get("updatedAt") and old.get("state") != "ready":
                record["failures"] = 0
            records[key] = record
            return {**issue, "key": key, "repo": repo, "scope": scope}
        return None

    found = edited(base, lease)
    if found is None:
        return None, "Repository backlog has no unclaimed eligible work; retrying after the next poll"
    return found, ""


def finish(base: Path, issue: dict[str, Any], agent: la.Agent, result: tuple[str, str], *, clock: Callable[[], float] = time.time) -> None:
    """Project a proposed or blocked outcome without completion credit."""
    kind, text = result

    def record(ledger: Ledger) -> None:
        old = ledger.get("records", {}).get(issue["key"], {})
        if old.get("owner") != agent.identity:
            raise Denied("this issue lease belongs to another worker")
        failed = kind != "answer"
        ledger["records"][issue["key"]] = {**old, "state": "blocked" if failed else "proposed",
                                           "failures": old.get("failures", 0) + int(failed), "finished": clock(),
                                           "summary": line(text, 400)}

    edited(base, record)
