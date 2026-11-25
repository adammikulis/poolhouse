"""Asking for grants, as an agent does it: one request of up to eight asks, and what became of it (docs/node.md, "Standing grants").

A request is decided before it is returned: `poolhouse.board.autoapprove` has this device's key sign at once what the caller may have and refuses
the rest with the reason. Nothing here waits for an answer, so there is no handle to poll and no pending state to read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from poolhouse.board import grants

__all__ = ["Approval", "Ask", "wire"]

SAYS = {
    "approved": "It was approved, so the grants exist.",
    "denied": "It was declined; nothing changed.",
    "expired": "The request ended before it was answered; ask again if it is still needed.",
    "cancelled": "The request was withdrawn.",
    "failed": "It was approved, but it could not be carried out",
}
"""What an agent says of a request in each state it can end in, one sentence."""


@dataclass(frozen=True, slots=True)
class Ask:
    """One grant a session asks for: an action of the registry, where it applies, for how long, how often."""

    action: str
    resource: str = "*"
    lasts: str = "1h"
    """How long it lasts once approved: 90m, 12h, 30d (at most a year)."""
    uses: int | None = None
    """How often it may be used; None is as often as it is needed."""
    review: bool = False

    def wire(self) -> dict[str, Any]:
        """The ask as the node takes it."""
        out: dict[str, Any] = {"action": self.action, "resource": self.resource, "lasts_ms": grants.duration_ms(self.lasts), "review": self.review}
        if self.uses is not None:
            out["uses"] = self.uses
        return out


@dataclass(frozen=True, slots=True)
class Approval:
    """A request a session made, as the node holds it once it was decided."""

    id: str
    """`ap-` and 16 hex digits."""
    state: str
    """approved, denied, expired, cancelled or failed."""
    text: str
    """The plain words the node wrote for the request."""
    title: str
    """The one line the request is known by."""
    risk: str
    """routine, sensitive or irreversible."""
    grants: tuple[str, ...]
    """The ids of the grants an approval wrote."""
    reason: str = ""

    @classmethod
    def of(cls, raw: dict[str, Any]) -> Approval:
        """The request as the node returned it."""
        return cls(str(raw["id"]), str(raw["state"]), str(raw["text"]), str(raw["title"]), str(raw["risk"]), tuple(raw["grants"]), str(raw.get("reason", "")))

    def message(self) -> str:
        """One sentence to pass on for the state this is in."""
        base = SAYS.get(self.state, self.state)
        return f"{base}: {self.reason}" if self.state == "failed" and self.reason else base


def wire(asks: list[Ask] | tuple[Ask, ...], *, note: str = "", task: str = "", expect_policy: str = "") -> dict[str, Any]:
    """The params of `approval_request` for ``asks``.

    ``note`` is the agent's own words (up to 160 plain characters), shown as the agent's and checked by nobody; ``task`` names
    the board entry the request came from; ``expect_policy`` is what the agent believes the pool's join policy is, and the node
    refuses the request if it is not.
    """
    params: dict[str, Any] = {"asks": [a.wire() for a in asks]}
    for key, value in (("note", note), ("task", task), ("expect_policy", expect_policy)):
        if value:
            params[key] = value
    return params
