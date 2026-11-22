"""How the first person key, the first phone or a change to the pool is made by the node (docs/phone-approval.md, "The first phone";
docs/node.md, "Standing grants").

The node makes it, never the client. A node started as the launcher starts it (`--presence auto`, the default) asks nobody: the one-time secret
or ticket is made as it is asked for, kept in the node's memory and handed once to the session that asked, so neither a file in the state
directory nor a request can make one; who may ask is the node's rule (the device's own session for the first key and phone, a live main session
also for a pool's card), and a secret is bound to the key it was made for. A node somebody started by hand with `--presence os` or `terminal` puts
a question to the user first, and `ask` then waits for the answer; this module has no way to give one, and the launcher never starts such a node.
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
    """What to say while a node started by hand with `--presence os` or `terminal` waits for its user: the question and where it is answered."""
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
    """How a question is put by a node that asks (`--presence terminal`): the terminal device it writes on (this process's own by default) and
    where the words that say where to answer go. A node that asks nobody (`auto`) ignores both."""

    terminal: str | None = None
    say: Callable[[str], Any] = lambda _text: None


SILENT = Voice()
"""This process's own terminal, with nothing said about it."""


class Card(NamedTuple):
    """A change the node drafted (``id``). Asking for it enrols the key the node made for it, when it made one."""

    id: str


def ask(
    client: Client, token: str, purpose: str, public: str = "", voice: Voice = SILENT
) -> dict[str, Any]:
    """Ask the node for the one-time secret that lets the session ``token`` add the first person key (``purpose`` `key`) or the first phone
    (`phone`): the node's reply, with the `secret` (and, for a phone, its `link`). The device's own session asks; the node refuses any other.

    ``public`` is the public key (hex) of the first key: the secret is for that key and no other; a phone has none. Raises `Denied` when the node
    refuses the asker or a key is enrolled already; a node somebody started to ask a user (`os`, `terminal`) is waited for, and a no, a late
    answer or a dialog that could not be shown are each worded as what they were.
    """
    return _asked(client, token, voice, purpose=purpose, public=public, card="")


def ask_card(client: Client, token: str, purpose: str, card: Card, voice: Voice = SILENT) -> dict[str, Any]:
    """Ask the node to answer the change ``card`` names (``purpose`` `pool_key` for a card that made a key for this computer, `card` for any other):
    the reply's `secret` is the ticket a signer shows the node for each payload it signs (a `pool_key` reply also carries the new key's `seed`,
    once). Raises `Denied` as `ask` does."""
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
