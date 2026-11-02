"""The writers of run-script approvals: the answer hook that records one, and the Bash guard's exchange of one for a ticket."""

from __future__ import annotations

import os
import secrets
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from poolhouse.sentinel.events import EventLog
from poolhouse.workspace import (
    person_auth,
    person_hook,
    person_record,
    person_store,
    run_approval,
)

__all__ = ["issue", "on_event"]

TICKET_FILE_KEEP_S = 3600
"""A ticket file that nobody spent is removed after an hour; its ticket expired after a minute."""


def _answer(log: EventLog, session: str, question: str) -> dict[str, Any] | None:
    """The newest answer row the hook recorded in ``session`` for exactly ``question``."""
    wanted = person_record.prompt_hash(question)
    return next((r for r in reversed(person_store.records(log)) if r.get("type") == "answer"
                 and r.get("session_id") == session and r.get("question_sha256") == wanted), None)


def _said(text: str) -> dict[str, Any]:
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}


def on_event(event: dict[str, Any], log: EventLog | None = None) -> dict[str, Any] | None:
    """Record the person's approving answer to a run question the code still bears out, asked exactly as `propose` printed it, as a `run-script` authorization; say in one sentence why when a genuine approval cannot be recorded."""
    if (event.get("hook_event_name") not in (None, "PostToolUse") or event.get("tool_name") != "AskUserQuestion"
            or event.get("agent_id") or not person_hook.attended() or not person_hook.valid_session(event.get("session_id"))):
        return None
    answers = (event.get("tool_response") or {}).get("answers")
    project = run_approval.session_project()
    if not isinstance(answers, dict) or not answers or not project:
        return None
    log = log or person_store.open_log()
    for question, label in answers.items():
        if not (isinstance(question, str) and isinstance(label, str)):
            continue
        try:
            found = run_approval.approved(log, question, label, project)
        except run_approval.Refused as why:
            return _said(f"the approval was not recorded: {why}; ask the question again with run-approval propose")
        row = _answer(log, event["session_id"], question) if found else None
        if found is None or row is None:
            continue
        if problem := run_approval.asked_problem(event.get("tool_input"), question, found):
            return _said(f"the approval was not recorded: {problem}; ask it again with exactly what run-approval propose printed")
        where = run_approval.target(found)
        if not person_auth.live(run_approval.KIND, where, person_auth.Identity(event["session_id"]), log=log):
            person_record.record_authorization(log, row, person_record.Grant(run_approval.KIND, where, "asked", row["question_sha256"]))
        return _said(f"{run_approval.command(found)} may run once in {found.top} in the next {person_store.DEFAULT_MINUTES} minutes (attested by hook)")
    return None


def _ticket_file(secret: str) -> Path:
    """A new private file holding ``secret`` in the ticket folder, whose old files are cleared."""
    folder = person_store.directory() / run_approval.TICKET_DIR
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder.chmod(0o700)
    for old in folder.iterdir():
        if time.time() - old.lstat().st_mtime > TICKET_FILE_KEEP_S:
            old.unlink(missing_ok=True)
    path = folder / secrets.token_hex(8)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii") as handle:
        handle.write(secret)
    return path


def issue(text: str, cwd: str, session: str | None = None, log: EventLog | None = None,
          environ: Mapping[str, str] | None = None) -> str:
    """Spend the owner's approval of the command ``text`` run from ``cwd`` and return the command line that starts the run with its ticket; raises `run_approval.NotARun`, `run_approval.Refused` or `person_auth.NotAuthorized`."""
    parsed = run_approval.parse(text)
    project = run_approval.session_project(environ)
    found = run_approval.facts(cwd, Path(cwd) / parsed[0], parsed[1], project) if parsed and project else None
    if found is None or parsed is None:
        raise run_approval.NotARun("not a run an approval covers")
    log = log or person_store.open_log()
    who = person_auth.Identity(person_auth.session_of_ancestry(log) if session is None else session)
    spent = person_auth.consume(run_approval.KIND, run_approval.target(found), who, log=log)
    held = next(a for a in person_store.authorizations(person_store.records(log)) if a.id == spent)
    secret = secrets.token_hex(16)
    grant = person_record.Grant(run_approval.TICKET, f"{run_approval.target(found)}#{secret}", "ticket",
                                minutes=run_approval.TICKET_MINUTES)
    person_record.record_authorization(log, {"seq": held.statement_seq, "session_id": held.session_id, "project": held.project}, grant)
    return run_approval.launcher(found, parsed[1], _ticket_file(secret), os.environ if environ is None else environ)
