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
from typing import Any, NamedTuple

from poolhouse.board.client import Client, Denied

__all__ = ["SILENT", "WAIT_S", "Card", "Voice", "ask", "ask_card", "terminal_name"]

WAIT_S = 150.0
"""A little longer than the node waits for an answer (two minutes), so its word on a late answer is the one that is read."""
POLL_S = 0.25


def terminal_name() -> str:
    """The terminal this process runs in, as the node is told to find it: the device (`/dev/ttys004`) on Mac and Linux, this process's id on Windows (the
    node attaches to the console it runs in), or an empty string when it has none."""
    if sys.platform == "win32":
        return str(os.getpid()) if _has_console() else ""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            if stream.isatty():
                return os.ttyname(stream.fileno())
        except (OSError, ValueError):
            continue
    return ""


def _has_console() -> bool:
    """Whether this Windows process is attached to a console (a command window, or a pseudo-console), which a program started with none is not."""
    import ctypes

    slot = (ctypes.c_uint * 1)()
    return bool(ctypes.windll.kernel32.GetConsoleProcessList(slot, 1))  # type: ignore[attr-defined]


def _where(kind: str, sentence: str = "") -> str:
    """What to tell the person, word for word what the prompt says and where to answer it: the dialog can open behind their windows."""
    way = (
        "Say yes at the prompt this computer shows (it asks for your password or fingerprint)."
        if kind == "os"
        else "Type yes at the question the node wrote in this terminal."
    )
    return f"Waiting for you: {sentence} {way}" if sentence else way


def _why(said: dict[str, Any]) -> str:
    """Why nothing was added, as one sentence for whoever asked: a no, a question nobody answered and a prompt that could not be shown are three
    different things, and only the first is the person's word."""
    state = str(said["state"])
    if state == "refused":
        return "You said no, so nothing was added."
    if state in ("expired", "asking"):
        return "Nobody said yes in time, so nothing was added. That is not a no: tell the person the prompt is waiting, and ask again."
    if state == "failed":
        return f"The prompt could not be shown or ended with an error, so nothing was added. That is not a no: tell the person, and ask again. {said.get('error', '')}".strip()
    return "The node did not take a yes."


@dataclass(frozen=True, slots=True)
class Voice:
    """How the question is put: the terminal device the node writes it on when it asks on a terminal (this process's own by default) and
    where the words that tell the person to answer go."""

    terminal: str | None = None
    say: Callable[[str], Any] = lambda _text: None


SILENT = Voice()
"""The question put on this process's own terminal, with nothing said to the person about it."""


class Card(NamedTuple):
    """A change the node drafted (``id``). The yes at its prompt enrols the key the node made for it, when it made one."""

    id: str


def ask(
    client: Client, token: str, purpose: str, public: str = "", voice: Voice = SILENT
) -> dict[str, Any]:
    """Ask the person, through the node, to allow the session ``token`` to add the first person key (``purpose`` `key`) or the first
    phone (`phone`), and wait for the answer: the node's reply with the one-time `secret` (and, for a phone, its `link`).

    ``public`` is the public key (hex) of the first key: the person's yes is for that key and no other, and the question shows its words;
    a phone has none. Raises `Denied` when the person said no, did not answer or the dialog could not be shown (each worded as what it was: only the
    first is a no, and asking again raises the prompt again at once), or when the node has no way to ask.
    """
    return _asked(client, token, voice, purpose=purpose, public=public, card="")


def ask_card(client: Client, token: str, purpose: str, card: Card, voice: Voice = SILENT) -> dict[str, Any]:
    """Ask the person, through the node, to answer the change ``card`` names (``purpose`` `pool_key` for a card that made a key for this computer,
    `card` for any other): the sentence the prompt shows is the node's, written from its draft, and the reply's `secret` is the ticket a signer
    shows the node for each payload it signs (a `pool_key` reply also carries the new key's `seed`, once). Raises `Denied` as `ask` does."""
    return _asked(client, token, voice, purpose=purpose, card=card.id)


def _asked(client: Client, token: str, voice: Voice, **params: str) -> dict[str, Any]:
    named = terminal_name() if voice.terminal is None else voice.terminal
    said = client.call("presence_ask", "", token, terminal=named, **params)
    told = False
    end = time.monotonic() + WAIT_S
    while said["state"] == "asking" and time.monotonic() < end:
        if not told:
            voice.say(_where(str(said.get("presence", "")), str(said.get("sentence", ""))))
            told = True
        time.sleep(POLL_S)
        said = client.call("presence_status", "", token)
    if said["state"] != "confirmed" or not isinstance(said.get("secret"), str):
        raise Denied("denied", _why(said))
    return dict(said)
