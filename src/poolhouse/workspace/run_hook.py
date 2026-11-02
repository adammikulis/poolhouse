"""The writers of run-script approvals: the answer hook that records one, and the Bash guard's exchange of one for a ticket."""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from poolhouse.sentinel.events import EventLog
from poolhouse.workspace import (
    person_auth,
    person_hook,
    person_record,
    person_store,
    person_targets,
    run_approval,
)

__all__ = ["issue", "on_event"]


def _answer(log: EventLog, session: str, question: str) -> dict[str, Any] | None:
    """The newest answer row the hook recorded in ``session`` for exactly ``question``."""
    wanted = person_record.prompt_hash(question)
    return next((r for r in reversed(person_store.records(log)) if r.get("type") == "answer"
                 and r.get("session_id") == session and r.get("question_sha256") == wanted), None)


def on_event(event: dict[str, Any], log: EventLog | None = None) -> dict[str, Any] | None:
    """Record the person's approving answer to a run question git and the registry still bear out as a `run-script` authorization."""
    if (event.get("hook_event_name") not in (None, "PostToolUse") or event.get("tool_name") != "AskUserQuestion"
            or event.get("agent_id") or not person_hook.attended() or not person_hook.valid_session(event.get("session_id"))):
        return None
    answers = (event.get("tool_response") or {}).get("answers")
    if not isinstance(answers, dict) or not answers:
        return None
    log = log or person_store.open_log()
    checkout = person_targets.inspect(str(event.get("cwd") or Path.cwd()))
    for question, label in answers.items():
        if not (isinstance(question, str) and isinstance(label, str)):
            continue
        found = run_approval.approved(log, question, label, checkout)
        row = _answer(log, event["session_id"], question) if found else None
        if found is None or row is None:
            continue
        where = run_approval.target(found)
        if not person_auth.live(run_approval.KIND, where, person_auth.Identity(event["session_id"]), log=log):
            person_record.record_authorization(log, row, person_record.Grant(run_approval.KIND, where, "asked", row["question_sha256"]))
        text = f"{run_approval.command(found)} may run once in {found.top} in the next {person_store.DEFAULT_MINUTES} minutes (attested by hook)"
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}
    return None


def issue(text: str, cwd: str, session: str | None = None, log: EventLog | None = None) -> str:
    """Spend the owner's approval of the command ``text`` run from ``cwd`` and return the secret of its one-minute ticket; raises `run_approval.NotARun` or `person_auth.NotAuthorized`."""
    parsed = run_approval.parse(text)
    found = run_approval.facts(cwd, Path(cwd) / parsed[0], parsed[1]) if parsed else None
    if found is None:
        raise run_approval.NotARun("not a run an approval covers")
    log = log or person_store.open_log()
    who = person_auth.Identity(person_auth.session_of_ancestry(log) if session is None else session)
    spent = person_auth.consume(run_approval.KIND, run_approval.target(found), who, log=log)
    held = next(a for a in person_store.authorizations(person_store.records(log)) if a.id == spent)
    secret = secrets.token_hex(16)
    grant = person_record.Grant(run_approval.TICKET, f"{run_approval.target(found)}#{secret}", "ticket",
                                minutes=run_approval.TICKET_MINUTES)
    person_record.record_authorization(log, {"seq": held.statement_seq, "session_id": held.session_id, "project": held.project}, grant)
    return secret
