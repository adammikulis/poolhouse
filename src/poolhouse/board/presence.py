"""The one step an agent cannot answer: the person says yes to the node's own question before the first person key or the first phone
is added (docs/phone-approval.md, "The first phone"; docs/node.md, "Standing grants").

The node asks, never the client: the operating system's prompt where the installed software runs on its real home, or the terminal
device named here, which the node writes the question on and reads the typed `yes` from itself. The one-time secret the node makes when
the person says yes is kept in its memory and handed once to the session that asked, so neither a file in the state directory nor a
request can make one. `ask` waits for the answer; it has no way to give one.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from poolhouse.board.client import Client, Denied

__all__ = ["SILENT", "WAIT_S", "Voice", "ask", "terminal_name"]

WAIT_S = 150.0
"""A little longer than the node waits for an answer (two minutes), so its word on a late answer is the one that is read."""
POLL_S = 0.25


def terminal_name() -> str:
    """The terminal device this process runs in (`/dev/ttys004`), or an empty string when it has none (a node on Windows reads no terminal)."""
    if sys.platform == "win32":
        return ""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            if stream.isatty():
                return os.ttyname(stream.fileno())
        except (OSError, ValueError):
            continue
    return ""


def _where(kind: str) -> str:
    return (
        "Say yes at the prompt this computer shows (it asks for your password or fingerprint)."
        if kind == "os"
        else "Type yes at the question the node wrote in this terminal."
    )


@dataclass(frozen=True, slots=True)
class Voice:
    """How the question is put: the terminal device the node writes it on when it asks on a terminal (this process's own by default) and
    where the words that tell the person to answer go."""

    terminal: str | None = None
    say: Callable[[str], Any] = lambda _text: None


SILENT = Voice()
"""The question put on this process's own terminal, with nothing said to the person about it."""


def ask(
    client: Client, token: str, purpose: str, public: str = "", voice: Voice = SILENT
) -> dict[str, Any]:
    """Ask the person, through the node, to allow the session ``token`` to add the first person key (``purpose`` `key`) or the first
    phone (`phone`), and wait for the answer: the node's reply with the one-time `secret` (and, for a phone, its `link`).

    ``public`` is the public key (hex) of the first key: the person's yes is for that key and no other, and the question shows its words;
    a phone has none. Raises `Denied` when the person said no or did not answer, or when the node has no way to ask.
    """
    named = terminal_name() if voice.terminal is None else voice.terminal
    said = client.call("presence_ask", "", token, purpose=purpose, public=public, terminal=named)
    told = False
    end = time.monotonic() + WAIT_S
    while said["state"] == "asking" and time.monotonic() < end:
        if not told:
            voice.say(_where(str(said.get("presence", ""))))
            told = True
        time.sleep(POLL_S)
        said = client.call("presence_status", "", token)
    if said["state"] != "confirmed" or not isinstance(said.get("secret"), str):
        raise Denied(
            "denied",
            {
                "refused": "You said no, so nothing was added.",
                "expired": "Nobody said yes in time, so nothing was added.",
                "asking": "Nobody said yes in time, so nothing was added.",
            }.get(str(said["state"]), "The node did not take a yes."),
        )
    return dict(said)
