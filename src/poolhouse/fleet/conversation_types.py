"""Conversation and message records exchanged with the UI."""
from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from poolhouse.fleet.conversation_settings import checked

TITLE_CHARS = 60
ROLES = ("system", "user", "assistant")


@dataclass(frozen=True, slots=True)
class CodingState:
    """Immutable current settings bound to one coding conversation."""

    conversation: str
    revision: int
    model: str
    settings: tuple[tuple[str, Any], ...]

    @classmethod
    def from_conversation(cls, conversation: Conversation) -> CodingState:
        if not conversation.settings["harness"] or not conversation.settings["project_id"]:
            raise ValueError("Choose a registered project for this coding session")
        return cls(conversation.id, conversation.policy_revision, conversation.model,
                   tuple(sorted(conversation.settings.items())))


@dataclass
class Message:
    role: str
    content: str
    at: float = field(default_factory=time.time)
    reasoning: str = ""
    status: str = "complete"

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Conversation:
    id: str
    title: str = ""
    model: str = ""
    created: float = field(default_factory=time.time)
    policy_revision: int = 0
    messages: list[Message] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=checked)

    def public(self, *, full: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {"version": 1, "id": self.id, "title": self.title,
                               "settings": dict(self.settings), "policy_revision": self.policy_revision,
                               "model": self.model, "created": self.created,
                               "count": len(self.messages)}
        if full:
            out["messages"] = [m.public() for m in self.messages]
        return out


def safe(cid: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", cid or ""))


def title(text: str) -> str:
    line = " ".join(text.split())
    return line[:TITLE_CHARS].rstrip() or "New chat"
