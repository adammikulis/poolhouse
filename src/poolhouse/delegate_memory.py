"""A delegate's project memory, all of it on the launcher's side: what it is told once at the start, what its `recall` tool answers while it runs, and
the facts its `remember` tool proposes, which are saved only when its lead's checked grade accepts the work.

The store is the project's own (`poolhouse.memory`, project scope only, never the person's); its key and file are opened by the runner and the
grading command, never inside the sandbox. A proposal waits in the delegate's folder, passes the checks every fact passes, and is saved marked
agent-observed, naming the card and the commit. Nobody is asked.
"""

from __future__ import annotations

import contextlib
import dataclasses
import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from poolhouse import delegate_store as store, files, lead_bridge, lock
from poolhouse.isolation import session_verbs
from poolhouse.memory import project as projects, tools
from poolhouse.memory.facts import Refused
from poolhouse.memory.store import Setup, Store, Tampered
from poolhouse.memory.vault import KeyUnavailable

__all__ = ["LIMIT", "NOTHING", "held", "opened", "propose", "reach", "recall", "settle"]

FILE = "memory.json"
LIMIT = 5
"""The facts one delegate may propose."""
NOTHING = "Nothing remembered about this project matches."
UNREAD = "The project's memory could not be read."
PROPOSED = "Proposed: it is saved only if your lead's review accepts this work."
STATUS = re.compile(r"(?i)\b(?:landed|merged|committed|pushed|pull request|PR ?#?\d+|commit [0-9a-f]{7,40}|todo|in progress|next step|"
                    r"still (?:to do|pending|failing)|not yet (?:done|built|landed))\b")
"""Words of the state of work, which the backlog and git hold and a fact must not."""
ACCEPTS, REJECTS = ("correct",), ("partly", "wrong", "unusable")
FAILED = (OSError, RuntimeError, ValueError, ImportError)


def held(name: str) -> dict[str, Any]:
    """What delegate ``name`` was told and asked of the memory: the ids ``recalled`` at its start, the ``recalls`` it made, the facts it
    ``proposed``, and once graded what was ``saved`` (None until then)."""
    found = files.read_json(store.folder(name) / FILE, {})
    found = found if isinstance(found, dict) else {}
    return {"recalled": list(found.get("recalled") or []), "recalls": int(found.get("recalls") or 0),
            "proposed": list(found.get("proposed") or []), "saved": found.get("saved")}


@contextlib.contextmanager
def _edited(name: str) -> Iterator[dict[str, Any]]:
    folder = store.folder(name)
    with lock.held(folder / "memory.lock"):
        found = held(name)
        yield found
        files.write_json(folder / FILE, files.versioned(found, 1))


def _store(source: str) -> Store | None:
    """The memory of the project at ``source``; None for a delegate given no project."""
    found = projects.detect(explicit=Path(source)) if source else None
    return Store(setup=Setup(project=found)) if found else None


def _looked(source: str, query: str) -> tuple[str, list[str]]:
    """The fenced facts of the project that answer ``query`` and their ids; a sentence instead of the facts where the store cannot be read."""
    try:
        mine = _store(source)
        if mine is None:
            return "", []
        try:
            return tools.lookup(mine, query)
        finally:
            mine.close()
    except FAILED:
        return UNREAD, []


def opened(name: str, source: str, query: str) -> str:
    """What delegate ``name`` is told once, at the start of its task: the facts that answer ``query``, fenced as data; empty when none does."""
    text, ids = _looked(source, query)
    with _edited(name) as found:
        found["recalled"] = ids
    return text if ids else ""


def recall(name: str, source: str, query: str) -> str:
    """The answer of the `recall` tool."""
    text, ids = _looked(source, query)
    with _edited(name) as found:
        found["recalls"] += 1
    return text or (NOTHING if not ids else "")


def propose(name: str, fact: str) -> str:
    """The answer of the `remember` tool: the fact kept as a proposal, or why it is not."""
    if STATUS.search(fact):
        return "Not kept: the state of work belongs in the backlog or git. Remember only what stays true about the project."
    offer = tools.propose(fact, "note", "agent-observed")
    if not offer["ok"]:
        return f"Not kept: {offer['reason']}"
    with _edited(name) as found:
        if offer["text"] in found["proposed"]:
            return PROPOSED
        if len(found["proposed"]) >= LIMIT:
            return f"Not kept: a task proposes at most {LIMIT} facts."
        found["proposed"].append(offer["text"])
    return PROPOSED


def _said(arguments: Mapping[str, Any], key: str) -> str:
    """The model's own words of a call, without the label the session bridge puts above them."""
    return str(arguments[key]).removeprefix(f"{session_verbs.LABEL}\n\n")


def reach(base: lead_bridge.Reach, name: str, source: str) -> lead_bridge.Reach:
    """``base``, the lead's two tools, with `recall` and `remember` of delegate ``name`` over the project at ``source``."""
    def run(tool: str, arguments: Mapping[str, Any]) -> Any:
        if tool == "recall":
            return recall(name, source, _said(arguments, "query"))
        if tool == "remember":
            return propose(name, _said(arguments, "fact"))
        return base.run(tool, arguments)

    return dataclasses.replace(base, run=run, verbs={**base.verbs, **session_verbs.MEMORY})


def _saved(source: str, facts: list[str], about: list[str]) -> tuple[list[str], list[str]]:
    """``(ids saved, why each other was not)`` of ``facts`` added to the project's memory, each naming ``about``."""
    saved: list[str] = []
    refused: list[str] = []
    mine = _store(source)
    if mine is None:
        return saved, ["the project is gone"]
    try:
        for text in facts:
            try:
                saved.append(mine.add(text, "note", "agent-observed", entities=about).id)
            except (Refused, Tampered, KeyUnavailable, OSError) as why:
                refused.append(str(why))
    finally:
        mine.close()
    return saved, refused


def settle(rec: store.Delegate, from_lead: bool, verdict: str, verified: bool) -> str:
    """What a grade of ``verdict`` does to the facts ``rec`` proposed, as a sentence; empty when there is nothing to say. Only a lead's grade
    (``from_lead``) counts: a checked `correct` saves them, any lower verdict discards them, and one without a check leaves them waiting."""
    found = held(rec.name)
    if not rec.memory or not found["proposed"] or found["saved"] is not None:
        return ""
    count = len(found["proposed"])
    if not from_lead:
        return f"{count} proposed fact(s) wait for the grade of its lead, {rec.lead}"
    if verdict in ACCEPTS and not verified:
        return f"{count} proposed fact(s) wait for a grade with a check (--check)"
    if verdict in REJECTS:
        with _edited(rec.name) as changed:
            changed["saved"] = []
        return f"{count} proposed fact(s) discarded: the work was not accepted"
    commit = str((store.result(rec.name).get("tree") or {}).get("commit") or "")[:12]
    about = [f"task:{rec.card or rec.name}", *([f"task:commit {commit}"] if commit else [])]
    try:
        saved, refused = _saved(rec.source, found["proposed"], about)
    except FAILED as why:
        return f"{count} proposed fact(s) could not be saved: {why}"
    with _edited(rec.name) as changed:
        changed["saved"] = saved
    return f"{len(saved)} of {count} proposed fact(s) saved to the project's memory" + (f"; not saved: {refused[0]}" if refused else "")
