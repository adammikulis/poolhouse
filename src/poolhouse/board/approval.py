"""Asking a person for grants, as an agent does it: one request of up to eight asks, a handle to wait on, and the sentence
to pass on when it ends (docs/phone-approval.md).

The node writes the text the person reads and keeps the request until a person's key answers it. Nothing here answers one.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from poolhouse.board import grants
from poolhouse.board.session import Session

__all__ = ["ENDED", "PENDING", "Approval", "Ask", "request", "wire"]

PENDING = "pending"
ENDED = ("approved", "denied", "expired", "cancelled", "failed")
POLL_START_S = 0.4
POLL_MOST_S = 2.0
SAYS = {
    "pending": "Waiting for your answer on your phone.",
    "approved": "You approved it, so the grants exist.",
    "denied": "You said no; nothing changed.",
    "expired": "The request ended before you answered; ask again if it is still needed.",
    "cancelled": "The request was withdrawn.",
    "failed": "You approved it, but it could not be carried out",
}
"""What an agent tells its person for each state, one sentence."""


@dataclass(frozen=True, slots=True)
class Ask:
    """One grant a session asks a person for: an action of the registry, where it applies, for how long, how often."""

    action: str
    resource: str = "*"
    lasts: str = "1h"
    """How long it lasts once approved: 90m, 12h, 30d (at most 30 days)."""
    uses: int | None = None
    """How often it may be used; None is as often as it is needed."""
    review: bool = False

    def wire(self) -> dict[str, Any]:
        """The ask as the node takes it."""
        out: dict[str, Any] = {"action": self.action, "resource": self.resource, "lasts_ms": grants.duration_ms(self.lasts), "review": self.review}
        if self.uses is not None:
            out["uses"] = self.uses
        return out


class Approval:
    """A request a session made of the person, and what became of it.

    `wait` returns ``pending`` when its time runs out: the request is still on the person's phone, and the same handle goes on
    being good until the request ends, so waiting is never a failure.
    """

    __slots__ = ("_raw", "_session")

    def __init__(self, session: Session, raw: dict[str, Any]) -> None:
        self._session = session
        self._raw = raw

    @property
    def id(self) -> str:
        """The handle: `ap-` and 16 hex digits."""
        return str(self._raw["id"])

    @property
    def state(self) -> str:
        """pending, approved, denied, expired, cancelled or failed, as of the last time it was read."""
        return str(self._raw["state"])

    @property
    def text(self) -> str:
        """The plain-words text the person reads, which the node wrote."""
        return str(self._raw["text"])

    @property
    def title(self) -> str:
        """The one line a notification carries."""
        return str(self._raw["title"])

    @property
    def risk(self) -> str:
        """routine, sensitive or irreversible."""
        return str(self._raw["risk"])

    @property
    def expires_ms(self) -> int:
        """When the request ends, in milliseconds since the epoch by the node's clock."""
        return int(self._raw["expires_ms"])

    @property
    def grants(self) -> tuple[str, ...]:
        """The ids of the grants an approval wrote."""
        return tuple(self._raw["grants"])

    def message(self) -> str:
        """One sentence to pass on to the person for the state this is in."""
        base = SAYS.get(self.state, self.state)
        return f"{base}: {self._raw['reason']}" if self.state == "failed" and self._raw.get("reason") else base

    def refresh(self) -> str:
        """Read the request again from the node; its state."""
        self._raw = dict(self._session.call("approval_status", id=self.id))
        return self.state

    def wait(self, timeout_s: float = 30.0) -> str:
        """Wait up to ``timeout_s`` seconds for the person to answer; the state, which is `pending` when none came."""
        end = time.monotonic() + timeout_s
        pause = POLL_START_S
        while self.refresh() == PENDING and time.monotonic() < end:
            time.sleep(min(pause, max(0.0, end - time.monotonic())))
            pause = min(pause * 1.5, POLL_MOST_S)
        return self.state

    def cancel(self) -> str:
        """Withdraw the request; its state."""
        self._raw = dict(self._session.call("approval_cancel", id=self.id))
        return self.state


def wire(asks: list[Ask] | tuple[Ask, ...], *, note: str = "", task: str = "", ttl_s: int = 0, expect_policy: str = "") -> dict[str, Any]:
    """The params of `approval_request` for ``asks``.

    ``note`` is the agent's own words (up to 160 plain characters), shown as the agent's and checked by nobody; ``task`` names
    the board entry the request came from; ``expect_policy`` is what the agent believes the pool's join policy is, and the node
    refuses the request if it is not.
    """
    params: dict[str, Any] = {"asks": [a.wire() for a in asks]}
    for key, value in (("note", note), ("task", task), ("ttl_s", ttl_s), ("expect_policy", expect_policy)):
        if value:
            params[key] = value
    return params


def request(session: Session, params: dict[str, Any]) -> Approval:
    """Make the request ``params`` (from `wire`) of the person; the handle to wait on."""
    return Approval(session, dict(session.call("approval_request", **params)))
