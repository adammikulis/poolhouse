"""Reviews of commits, seen from a session.

A review is a board entry the node stamps with the reviewer's name and model. The node answers which reviews of a
commit stand for a caller (those of this device, none from the caller). What the node cannot know is a model's tier,
so this module holds the bar for who may review and which stamped standing counts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from poolhouse.board import client

if TYPE_CHECKING:
    from poolhouse.board.session import Agent, Session
from poolhouse.workspace.model_tiers import tier_of
from poolhouse.workspace.modelid import VERIFIED

__all__ = ["accepts", "eligible", "rejected", "review", "standing", "tier_reason", "trust"]

CAUSES = ("revoked", "forged")


def tier_reason(model: str) -> str:
    """Why a session on ``model`` may not review or lead, or an empty string: the model must be listed and above the
    lowest tier.

    A model id counts as the session claimed it: the node has no model verification yet, so the table is
    read as if every listed id were verified (a stub, listed in docs/node.md with the grants).
    """
    return tier_of(model, VERIFIED).reason


def eligible(who: Agent) -> str:
    """Why ``who`` may not review or lead, or an empty string."""
    return tier_reason(who.model)


def _caller(s: Session) -> Agent:
    me = s.whoami()
    why = eligible(me)
    if why:
        raise client.Denied("denied", why)
    return me


def review(s: Session, branch: str, sha: str, verdict: str) -> dict[str, Any]:
    """Record a review of exactly ``sha`` under ``branch``."""
    _caller(s)
    return dict(s.call("land_review", branch=branch, sha=sha, verdict=verdict))


def accepts(s: Session, sha: str) -> dict[str, Any]:
    """The reviews of ``sha`` under any branch name that stand for the caller."""
    return dict(s.call("land_accepts", sha=sha))


def trust(s: Session, device: str, trusted: bool = True) -> dict[str, Any]:
    """Count (or stop counting) the tasks and grants of another device of the pool, by its certificate fingerprint.

    Authority is this device's own until this says otherwise; the node needs a session with no parent that
    holds the grant `land_trust` and records the change on the board.
    """
    return dict(s.call("land_trust", device=device, trusted=trusted))


def _stamped(entry: dict[str, Any], who: str) -> dict[str, Any]:
    """The standing the node stamped on a review by ``who``, or an empty dict when it carries none."""
    found = entry.get("standing")
    return found if isinstance(found, dict) and found.get("name") == who and isinstance(found.get("model"), str) else {}


def _ended_for_cause(s: Session) -> dict[str, str]:
    """The sessions of this board retired as revoked or forged, by name, with why."""
    return {a.name: a.retired_reason for a in s.agents(retired=True) if a.retired and a.retired_reason in CAUSES}


def _live(s: Session, found: dict[str, Any]) -> dict[str, str]:
    """The verdict of each reviewer in ``found`` whose stamped model is above the lowest tier and who was not retired as revoked or forged."""
    cause = _ended_for_cause(s)
    live = {}
    for found_review in found["reviews"]:
        who = found_review["by"]
        seen = _stamped(found_review, who)
        if seen and not tier_reason(seen["model"]) and who not in cause:
            live[who] = found_review["verdict"]
    return live


def rejected(s: Session, found: dict[str, Any]) -> bool:
    """Whether a reviewer in ``found`` (from `accepts`) stands with a reject.

    The node stamped each reviewer's name and model on its review, so a reviewer that ended normally still counts;
    one retired as ``revoked`` or ``forged`` after it reviewed no longer does.
    """
    return "reject" in _live(s, found).values()


def standing(s: Session, found: dict[str, Any]) -> str:
    """Why the reviews in ``found`` (from `accepts`) are not a standing accept, or an empty string."""
    live = _live(s, found).values()
    if "reject" in live:
        return "an independent reviewer rejected it"
    return "" if "accept" in live else "needs review: no independent accept that still stands"
