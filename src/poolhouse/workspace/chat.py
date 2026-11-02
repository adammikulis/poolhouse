"""The person's live conversation in a terminal: the stream of one room or conversation, and
every line typed sent to it, over the person's session on the board."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from poolhouse import activity
from poolhouse.board.client import NodeError
from poolhouse.board.session import Entry, Session
from poolhouse.errors import Denied
from poolhouse.workspace import board_cli, inbound, plain
from poolhouse.workspace.screen import Refused

__all__ = ["QUIT", "Console", "Target", "render", "run"]

QUIT = "/quit"
POLL_S = 0.2


def render(e: Entry) -> str:
    """One message as a line of plain text: its number, sender, and subject and text with the line breaks kept
    as spaces."""
    subject = str(e.fields.get("subject", ""))
    text = f"{subject}: {e.text}" if subject else e.text
    return f"[{e.ref}] {plain.line(e.sender, 48)}: {plain.line(text, 2000)}"


def _send(s: Session, to: str, text: str, out: Callable[[str], None]) -> None:
    try:
        board_cli.delivered(s, to, "note", text)
        activity.record("board.post", subject=to if to.startswith("#") else "dm", meta={"size": len(text)})
    except (Refused, NodeError, Denied, ValueError) as err:
        out(f"! not sent: {plain.line(err, 200)}")


@dataclass(frozen=True, slots=True)
class Target:
    """What a chat follows: the room ``board`` (such as ``#general``) or the conversation with the session ``dm``,
    starting ``backlog`` messages back."""

    board: str = ""
    dm: str = ""
    backlog: int = 20


@dataclass(slots=True)
class Console:
    """Where a chat reads and writes: the typed lines, where text goes, and the stop signal."""

    lines: Iterable[str]
    out: Callable[[str], None]
    cancel: threading.Event = field(default_factory=threading.Event)


def _typing(s: Session, to: str, console: Console) -> None:
    for line in console.lines:
        text = line.rstrip("\n")
        if text.strip() == QUIT:
            break
        if text.strip():
            _send(s, to, text, console.out)
    console.cancel.set()


def run(s: Session, target: Target, console: Console) -> None:
    """Print the last ``target.backlog`` messages of the room or conversation ``target`` names, then each new one as it
    arrives, and send every line of ``console.lines`` to it until ``/quit``, the end of the lines or ``console.cancel``.
    ``s`` must be the person's session (`poolhouse.board.person`)."""
    if not s.whoami().person:
        raise Denied("chat is for the person")
    if bool(target.board) == bool(target.dm):
        raise ValueError("chat with one board (--board) or one agent (--to)")
    scope: dict[str, Any] = {"channel": target.board} if target.board else {"conversation": target.dm}
    if target.board and not any(r["name"] == target.board and r["member"] for r in s.rooms()):
        s.room("join", target.board)
    seen = s.read_all(**scope)
    for e in seen[-target.backlog:] if target.backlog > 0 else ():
        console.out(render(e))
    cursor = inbound.advanced(seen, {})
    threading.Thread(target=_typing, args=(s, target.board or target.dm, console), daemon=True, name="chat-input").start()
    while not console.cancel.is_set():
        fresh = s.read_all(cursor, **scope)
        cursor = inbound.advanced(fresh, cursor)
        for e in fresh:
            console.out(render(e))
        console.cancel.wait(POLL_S)
