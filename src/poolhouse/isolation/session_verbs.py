"""The workspace verbs the session bridge runs for a confined session, and the label every message of its carries.

The table is closed: a verb that is not in it is refused and counted, and no verb administers, grants, registers, mints a token, reads or
writes a path of the parent's, or starts a task's integration (docs/design-isolated-launcher.md, 7 `workspace`). Each verb names the
workspace tool that runs it, the arguments it takes by name and type, and whether it acts (sends, claims, records) or only reads. A verb
that carries text another session will read gets the sender label `from a confined agent: data` put in front of that text by the bridge,
and no argument can name the kind of message the launcher identity owns (`isolation.report`): the reader of a message from a session reads
it as data to read and never as instructions to follow (AF1).
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["LABEL", "RESERVED", "VERBS", "Verb", "check", "describe", "labelled"]

LABEL = "from a confined agent: data"
"""What the bridge puts in front of every text a confined session sends; the reader treats what follows as data."""
RESERVED = "isolation"
"""The start of every message kind and subject the launcher identity owns (`isolation.report`); no session argument may name one."""
TEXT, WHOLE, NUMBER, FLAG = "text", "whole", "number", "flag"
PLAIN = re.compile(r"[A-Za-z0-9._/@:+=,#~%-]{0,64}")
"""What an argument that names something (a claim key, a file name, a handle, a recipient) is made of: characters and no space, so a name
another agent reads carries no sentence of the session's, and at most 64 of them (the longest a branch, a handle or a recipient is named in
this workspace), so a hyphenated imperative has no room to be one."""
TTL_CAP = 900.0
"""The longest a claim or a heartbeat the session asks for may last, in seconds, when the launcher says nothing else: the workspace's configured
`claim_ttl_s`, which the launcher passes in (`check`)."""
FREE = ("query",)
"""The one text argument that is not a name and not shown to anyone: the words a search is for."""


@dataclass(frozen=True, slots=True)
class Verb:
    """One verb: the workspace tool that runs it, its arguments (name to type; a name in ``needs`` has no default), whether it acts, and the
    arguments that hold text a reader will read (``lines`` hold one line, ``bodies`` many)."""

    tool: str
    takes: Mapping[str, str]
    needs: tuple[str, ...] = ()
    acts: bool = False
    lines: tuple[str, ...] = ()
    bodies: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()
    """Arguments that name a message kind or type: none may name one of the launcher's."""
    always: tuple[str, ...] = ()
    """One-line arguments that carry the label alone when the session gives none, so no message leaves without it."""


VERBS: dict[str, Verb] = {
    "status": Verb("workspace_status", {}),
    "inbox": Verb("workspace_inbox", {"limit": WHOLE}),
    "thread": Verb("workspace_thread", {"root": TEXT, "limit": WHOLE}, ("root",)),
    "who_owns": Verb("workspace_who_owns", {"kind": TEXT, "key": TEXT}, ("kind", "key")),
    "claims": Verb("workspace_claims", {"for_agent": TEXT, "kind": TEXT}),
    "notes_search": Verb("workspace_notes_search", {"query": TEXT, "kind": TEXT, "include_old": FLAG, "limit": WHOLE}, ("query",)),
    "note_get": Verb("workspace_note_get", {"note_id": TEXT}, ("note_id",)),
    "file": Verb("workspace_file", {"handle": TEXT, "text": FLAG, "limit": WHOLE}, ("handle",)),
    "file_search": Verb("workspace_file_search", {"query": TEXT, "board": TEXT, "by": TEXT, "limit": WHOLE}, ("query",)),
    "audit_verify": Verb("workspace_audit_verify", {}),
    "reputation": Verb("workspace_reputation", {"agent": TEXT, "offset": WHOLE}),
    "send": Verb("workspace_send", {"to": TEXT, "type": TEXT, "body": TEXT, "subject": TEXT, "reply_to": TEXT}, ("to", "type", "body"),
                 acts=True, lines=("subject",), bodies=("body",), kinds=("type",), always=("subject",)),
    "announce": Verb("workspace_announce", {"kind": TEXT, "text": TEXT}, ("kind", "text"), acts=True, lines=("text",), kinds=("kind",)),
    "ack": Verb("workspace_ack", {"seq": TEXT}, ("seq",), acts=True),
    "note_add": Verb("workspace_note_add", {"kind": TEXT, "title": TEXT, "body": TEXT, "source": TEXT}, ("kind", "title", "body"),
                     acts=True, lines=("title", "source"), bodies=("body",), kinds=("kind",)),
    "claim": Verb("workspace_claim", {"kind": TEXT, "key": TEXT, "ttl_s": NUMBER}, ("kind", "key"), acts=True),
    "release": Verb("workspace_release", {"kind": TEXT, "key": TEXT}, ("kind", "key"), acts=True),
    "heartbeat": Verb("workspace_heartbeat", {"ttl_s": NUMBER}, acts=True),
    "attach": Verb("workspace_attach", {"to": TEXT, "text": TEXT, "name": TEXT, "note": TEXT, "reply_to": TEXT, "derived_from": TEXT},
                   ("to", "text"), acts=True, lines=("note",), bodies=("text",), always=("note",)),
}
"""The closed table. `attach` takes text and never a path, since a path would be read in the parent; the verbs that write or make a path
of the parent's (`file_save`, `scratch_*`), the task verbs and every verb of administration are not in it."""
KIND = {TEXT: str, WHOLE: int, NUMBER: (int, float), FLAG: bool}


def labelled(text: str, *, line: bool) -> str:
    """``text`` with the sender label in front: on one line for a subject, a title or an announcement, above the text for a body."""
    return f"{LABEL}: {text}" if line else f"{LABEL}\n\n{text}"


def reserved(name: str) -> bool:
    """Whether ``name`` begins with the word of the launcher's own kinds, whatever its case, width, surrounding space or invisible
    characters (a zero-width space is not a different kind)."""
    plain = "".join(each for each in unicodedata.normalize("NFKC", name) if unicodedata.category(each) not in ("Cf", "Cc", "Zs", "Zl", "Zp"))
    return plain.casefold().startswith(RESERVED)


def _typed(name: str, value: object, kind: str) -> object:
    ok = isinstance(value, KIND[kind]) and not (kind in (WHOLE, NUMBER) and isinstance(value, bool))
    if not ok or (kind == WHOLE and isinstance(value, int) and value < 0) or (kind == NUMBER and not math.isfinite(value)):
        raise ValueError(f"the argument {name} is not a {kind}")
    return value


def check(name: str, arguments: object, *, ttl_cap: float = TTL_CAP) -> tuple[Verb, dict[str, Any]]:
    """The verb ``name`` and its arguments ready to run: each named in the table, of its type, every needed one given, no kind that is the
    launcher's, and the sender label in front of every text a reader will read. A time to live is held at ``ttl_cap``, and a number that is not
    finite (`NaN`, `Infinity`) is no number. ``ValueError`` names what is wrong, never the text given."""
    verb = VERBS.get(name)
    if verb is None or not isinstance(arguments, dict):
        raise ValueError("no such verb")
    unknown = sorted(set(arguments) - set(verb.takes))
    if unknown or [each for each in verb.needs if each not in arguments]:
        raise ValueError("the arguments are not the ones this verb takes")
    ready = {each: _typed(each, value, verb.takes[each]) for each, value in arguments.items()}
    if "ttl_s" in ready:
        ready["ttl_s"] = max(0, min(ready["ttl_s"], ttl_cap))
    for each in verb.kinds:
        if each in ready and reserved(str(ready[each])):
            raise ValueError("that kind of message is the launcher's")
    for each, value in ready.items():
        if verb.takes[each] == TEXT and each not in (*verb.lines, *verb.bodies, *FREE) and not PLAIN.fullmatch(str(value)):
            raise ValueError(f"the argument {each} is not a plain name")
    for each in verb.lines:
        if each in ready:
            ready[each] = labelled(str(ready[each]), line=True)
        elif each in verb.always:
            ready[each] = LABEL
    for each in verb.bodies:
        if each in ready:
            ready[each] = labelled(str(ready[each]), line=False)
    return verb, ready


def describe() -> dict[str, dict[str, Any]]:
    """The table as a client reads it: per verb the arguments by type, the ones needed and whether it acts."""
    return {name: {"takes": dict(verb.takes), "needs": list(verb.needs), "acts": verb.acts} for name, verb in VERBS.items()}
