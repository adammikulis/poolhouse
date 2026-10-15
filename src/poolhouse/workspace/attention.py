"""What a coordinating agent owes the board: unanswered direct requests and its live subagents."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from poolhouse.workspace.plain import data_line

if TYPE_CHECKING:
    from poolhouse.board.session import Agent, Entry, Session

__all__ = ["ACTIVE_S", "age", "closed", "owed_lines", "status_lines", "workers"]

ACTIVE_S = 12 * 3600.0
SHOWN = 8
ANNOUNCEMENTS = "#announcements"


def age(seconds: float) -> str:
    """A compact age such as 14m, 3h or 2d."""
    return f"{int(seconds // 86400)}d" if seconds >= 86400 else f"{int(seconds // 3600)}h" if seconds >= 3600 \
        else f"{int(seconds // 60)}m"


def owed_lines(owed: list[dict[str, Any]]) -> list[str]:
    """One line per unanswered request, the oldest first, at most SHOWN of them."""
    lines = [f"[{o['seq']}] {o['type']} from {o['from']}, unanswered {age(o['age_s'])}: {o['subject']}"
             for o in sorted(owed, key=lambda o: -o["age_s"])[:SHOWN]]
    return [*lines, f"(+{len(owed) - SHOWN} more)"] if len(owed) > SHOWN else lines


def closed(s: Session, entries: list[Entry]) -> set[str]:
    """The ids of the ``entries`` sent by a retired session that this session has read (acked, or shown by `dm`).

    A retired session never reads again, so a request of one that was seen is closed and only an unread one
    still needs somebody's attention.
    """
    gone = {a.name for a in s.agents(retired=True) if a.retired}
    mine = [e for e in entries if e.sender in gone]
    cursors = {q: s.cursor(q) for q in {"inbox", *(f"dm:{e.sender}" for e in mine)}}
    return {e.id for e in mine if any(e.number <= cursors[q].get(e.origin, 0) for q in ("inbox", f"dm:{e.sender}"))}


def workers(s: Session) -> dict[str, Agent]:
    """The authenticated, non-retired descendants of this token's actual owner."""
    caller = s.call("whoami")["name"]
    if not isinstance(caller, str) or not caller:
        raise ValueError("The workspace did not identify this session.")
    s.named = caller
    known = {a.name: a for a in s.agents(retired=True)}
    below: set[str] = set()
    grew = True
    while grew:
        grew = False
        for a in known.values():
            if a.name != caller and a.name not in below and (a.parent == caller or a.parent in below):
                below.add(a.name)
                grew = True
    return {name: known[name] for name in below if not known[name].retired}


def status_lines(s: Session, owed: list[dict[str, Any]]) -> list[str]:
    """Registered subagents, bounded announcement details, workspace claims and unanswered requests."""
    now = s.clock()
    held = s.claims()
    registered = workers(s)
    last: dict[str, Entry] = {}
    for entry in s.read(channel=ANNOUNCEMENTS, limit=200).entries:
        if (entry.sender in registered and entry.at_ms / 1000 > now - ACTIVE_S
                and (entry.sender not in last or entry.at_ms > last[entry.sender].at_ms)):
            last[entry.sender] = entry
    lines = []
    for name, agent in sorted(registered.items(), key=lambda kv: (-kv[1].seen_ms, kv[0]))[:SHOWN]:
        mine = [data_line(f"{c.kind}:{c.key}", 80) for c in held if c.owner == name]
        entry = last.get(name)
        detail = f"{data_line(entry.fields.get('type'), 32)} {age(max(0, now - entry.at_ms / 1000))} ago" if entry \
            else f"registered, seen {age(max(0, now - agent.seen_ms / 1000))} ago"
        idle = ", workspace idle" if agent.idle else ""
        lines.append(f"{name}: {detail}{idle}{', claims ' + ', '.join(mine) if mine else ''}"
                     + (f": {data_line(entry.text, 80)}" if entry else ""))
    if len(registered) > SHOWN:
        lines.append(f"(+{len(registered) - SHOWN} more live registered subagents)")
    return [f"Live registered subagents ({len(registered)}):", *(lines or ["(none)"]),
            f"Unanswered for {s.name} ({len(owed)}):", *(owed_lines(owed) or ["(none)"])]
