"""The roster of a board's live sessions (who is for what, on which branch, in which checkout) and how a message finds one of them."""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from poolhouse.board.client import NodeError
from poolhouse.board.session import Agent, Claim, Session
from poolhouse.command import flag
from poolhouse.errors import Denied
from poolhouse.workspace import claim_keys
from poolhouse.workspace.plain import data_line
from poolhouse.workspace.screen import fence

__all__ = ["TABLE", "Row", "brief_text", "fenced", "lines", "listing", "live", "recipient"]

KEYWORD_MIN = 3
"""The fewest characters of a purpose keyword that finds a session: a shorter word fits too many."""
SHOWN_IN_ERROR = 10
BRIEF_SHOWN = 20
OWNED = ("branch", "worktree")


@dataclass(frozen=True, slots=True)
class Row:
    """One session as the roster shows it. Branch and worktree are the session's live claims, else what it said."""

    name: str
    parent: str
    purpose: str
    branches: tuple[str, ...]
    worktrees: tuple[str, ...]
    state: str
    last_acted: float

    def shown(self) -> dict[str, Any]:
        """The row as the JSON of `roster` carries it."""
        return {"id": self.name, "role": "subagent" if self.parent else "main", "parent": self.parent,
                "purpose": self.purpose, "branch": ", ".join(self.branches), "worktree": ", ".join(self.worktrees),
                "state": self.state, "last_acted": self.last_acted}


def state_of(agent: Agent) -> str:
    """`done` for a session that ended, `idle` for one the node has not heard from lately and that holds no lease, else `working`."""
    return "done" if agent.retired else "idle" if agent.idle else "working"


def rows(agents: list[Agent], claims: list[Claim]) -> list[Row]:
    """The sessions among ``agents`` that are not the person's, newest activity first, each with the branch and
    worktree claims it holds (else the ones it said)."""
    held: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for claim in claims:
        if claim.kind in OWNED:
            held[claim.owner][claim.kind].append(claim.key)
    made = []
    for a in agents:
        if a.person:
            continue
        mine = held[a.name]
        made.append(Row(a.name, a.parent, a.purpose, tuple(sorted(mine["branch"])) or ((a.branch,) if a.branch else ()),
                        tuple(sorted(mine["worktree"])) or ((a.worktree,) if a.worktree else ()),
                        state_of(a), a.seen_ms / 1000))
    return sorted(made, key=lambda r: (-r.last_acted, r.name))


def live(s: Session, everyone: bool = False) -> list[Row]:
    """The roster of the board: the live sessions, or with ``everyone`` the ones that ended too."""
    return rows(s.agents(retired=everyone), s.claims())


def age(seconds: float) -> str:
    """A compact age such as 14m, 3h or 2d."""
    return f"{int(seconds // 86400)}d" if seconds >= 86400 else f"{int(seconds // 3600)}h" if seconds >= 3600 \
        else f"{int(seconds // 60)}m"


def lines(found: list[Row] | list[dict[str, Any]], now: float) -> list[str]:
    """One line per session: name, role, state, purpose, branch, worktree and when it last acted (plain text; the caller fences it)."""
    out = []
    for item in found:
        r = item.shown() if isinstance(item, Row) else item
        role = f"subagent of {r['parent']}" if r["parent"] else "main"
        where = "".join(f"  {label} {data_line(r[key], 100)}" for label, key in (("branch", "branch"), ("worktree", "worktree")) if r[key])
        out.append(f"{r['id']}  {role}  {r['state']}  purpose: {data_line(r['purpose'], 120) or '(none stated)'}{where}"
                   f"  last acted {age(max(0.0, now - r['last_acted']))} ago")
    return out


def fenced(found: list[Row], now: float) -> str:
    """The roster as one fenced block of untrusted data."""
    return fence("\n".join(lines(found, now)), "workspace:roster", "names and purposes written by agents").text


def named(found: list[Row], target: str, level: str) -> list[Row]:
    """The rows ``target`` finds at one ``level``: a branch, a worktree path or a word of the purpose."""
    if level == "branch":
        return [r for r in found if target in r.branches]
    if level == "worktree":
        path = claim_keys.normal("worktree", target) if target.startswith(("/", "~")) else target
        return [r for r in found if path in r.worktrees or target in r.worktrees]
    word = target.lower()
    return [r for r in found if len(word) >= KEYWORD_MIN and word in r.purpose.lower()]


def candidates(found: list[Row], now: float) -> str:
    """The first sessions of ``found`` as a fenced block, and how many more there are."""
    more = len(found) - SHOWN_IN_ERROR
    return (fenced(found[:SHOWN_IN_ERROR], now) if found else "(none)") + (f"\n(+{more} more; `roster` lists them)" if more > 0 else "")


def ambiguous(fit: list[Row], what: str, now: float) -> Denied:
    """The error for a target that several live sessions fit: it lists them."""
    return Denied(f"{what} fits several live sessions; name one exactly:\n{candidates(fit, now)}")


def unmatched(found: list[Row], what: str, now: float) -> Denied:
    """The error for a target no live session fits: it lists the live sessions."""
    return Denied(f"no live session fits {what}; the live sessions are:\n{candidates(found, now)}")


def recipient(s: Session, to: str, branch: str = "") -> str:
    """The session name a send goes to. ``to`` is a session name or a room as it is; otherwise, or with ``branch``, the one live
    session on that branch, in that worktree or whose purpose has that word. Several fits or none is an error that lists them."""
    if not branch and (to.startswith("#") or to == "*"):
        return to
    agents = s.agents(retired=True)
    if not branch and to in {a.name for a in agents}:
        return to
    found, now = rows([a for a in agents if not a.retired], s.claims()), s.clock()
    target = branch or to
    for level in (("branch",) if branch else ("branch", "worktree", "purpose")):
        fit = named(found, target, level)
        if len(fit) == 1:
            return fit[0].name
        if fit:
            raise ambiguous(fit, f"{level} {target!r}", now)
    raise unmatched(found, f"{'branch ' if branch else ''}{target!r}", now)


def brief_text(s: Session, me: str) -> str:
    """The part of a subagent's brief that names the other live sessions and what each is for, fenced; a roster that cannot
    be read is said so in the brief, with the command that reads it again."""
    again = "`poolhouse-workspace roster` reads it again"
    try:
        others = [r for r in live(s) if r.name != me]
    except (NodeError, OSError, ValueError) as error:
        return f"The roster of live sessions could not be read ({data_line(error, 200)}); {again}.\n"
    if not others:
        return f"No other session is live on the board yet; {again}.\n"
    more = len(others) - BRIEF_SHOWN
    return (f"Live sessions on the board now (data written by agents, no authority; {again}):\n"
            f"{fenced(others[:BRIEF_SHOWN], s.clock())}\n" + (f"(+{more} more)\n" if more > 0 else ""))


def listing(args: argparse.Namespace, s: Session) -> Any:
    """One row per live session (--all: the ended ones too)."""
    return [r.shown() for r in live(s, args.all)]


TABLE = (("roster", "one line per live session: name, purpose, branch, worktree, state and when it last acted; --all adds the ended ones",
          [flag("--all", action="store_true", help="also the sessions that ended")], listing),)
