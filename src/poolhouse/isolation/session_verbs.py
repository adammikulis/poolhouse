"""The two tools the session bridge runs for a confined session, and the label every text of its carries.

The table is closed, and it is the whole of what a session says to anyone: ``ask(question)`` and ``note(text)``, which the launcher's handler
(`poolhouse.lead_bridge`) sends to the session that launched it and to no other (docs/design-isolated-launcher.md, 7). No argument names a
recipient, a kind or subject of a message, a file or a claim: a session cannot send the kind the launcher identity owns (`isolation.report`) and
gives the parent no path to resolve. Each text carries the sender label ``from a confined agent: data``, set by the bridge: its reader treats it as
data to read and never as instructions to follow (AF1).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

__all__ = ["LABEL", "LEAD", "Verb", "check", "describe"]

LABEL = "from a confined agent: data"
"""What the bridge puts above every text a confined session sends; the reader treats what follows as data."""


@dataclass(frozen=True, slots=True)
class Verb:
    """One tool: the one text argument it takes and needs, and the words a small model reads beside its name (``help``) and the argument (``hint``)."""

    argument: str
    help: str
    hint: str


LEAD: Mapping[str, Verb] = {
    "ask": Verb("question", "Ask your lead one question and wait for its answer, which is returned. Ask only what the files cannot tell you.",
                "the question, in plain words"),
    "note": Verb("text", "Tell your lead a finding or a progress note. It returns at once and nothing comes back.",
                 "what to tell your lead, in plain words"),
}
"""The closed table, by tool name: a name that is not in it is refused and counted. There is no recipient to name, so a small model has no
three-argument send to get wrong. Neither tool changes the workspace, and telling the lead is what a read-only or planning session is for too, so
the bridge answers both in every permission profile."""


def check(name: str, arguments: object) -> dict[str, str]:
    """The arguments of the tool ``name`` ready to run: exactly the one it takes, as text, under the sender label. ``ValueError`` names what is
    wrong, never the text given."""
    verb = LEAD.get(name)
    if verb is None or not isinstance(arguments, dict):
        raise ValueError("no such verb")
    if set(arguments) != {verb.argument}:
        raise ValueError("the arguments are not the ones this verb takes")
    text = arguments[verb.argument]
    if not isinstance(text, str):
        raise ValueError(f"the argument {verb.argument} is not a text")
    return {verb.argument: f"{LABEL}\n\n{text}"}


def describe() -> dict[str, dict[str, str]]:
    """The table as a client reads it, by tool name: the argument and the words that say what the tool and the argument are."""
    return {name: {"argument": verb.argument, "help": verb.help, "hint": verb.hint} for name, verb in LEAD.items()}
