"""Saved conversations with transactional messages and model/project relationships."""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from pathlib import Path

from poolhouse.fleet import conversation_graph as graph
from poolhouse.fleet.conversation_settings import checked
from poolhouse.fleet.conversation_types import (
    ROLES,
    TITLE_CHARS,
    CodingState,
    Conversation,
    Message,
    safe,
    title,
)

__all__ = ["Conversation", "Conversations", "Message"]


class Conversations:
    """One conversation graph under the daemon's chat root."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser()

    def all(self) -> list[Conversation]:
        with graph.opened(self.root) as store:
            found = [conversation for cid in graph.all_ids(store) if (conversation := graph.get(store, cid)) is not None]
        return sorted(found, key=lambda conversation: conversation.created, reverse=True)

    def get(self, cid: str) -> Conversation | None:
        if not safe(cid):
            return None
        with graph.opened(self.root) as store:
            return graph.get(store, cid)

    def start(self, model: str = "", title: str = "", settings: dict | None = None) -> Conversation:
        made = Conversation(id=uuid.uuid4().hex[:12], title=title, model=model, settings=checked(settings))
        with graph.opened(self.root) as store, store.transaction():
            graph.save(store, made)
        return made

    def coding_state(self, cid: str) -> CodingState:
        """Read one current coding policy without loading its conversation history."""
        if not safe(cid):
            raise ValueError("invalid conversation id")
        with graph.opened(self.root) as store:
            found = graph.record(store, cid)
            if found is None:
                raise ValueError("no such coding session")
            return CodingState.from_conversation(found)

    @contextmanager
    def coding_guard(self, expected: CodingState, *, current):
        """Hold current policy only around a bounded parent-owned publication.

        Resource resolution, captures and tool execution precede this guard. The
        trusted current callback checks retained resource and execution identity
        locally, without subprocesses, network calls or acquiring registry locks.
        """
        if (not isinstance(expected, CodingState) or not safe(expected.conversation)
                or type(expected.revision) is not int or expected.revision < 0):
            raise ValueError("invalid coding session policy")
        with graph.opened(self.root) as store, store.transaction():
            found = graph.record(store, expected.conversation)
            if found is None or CodingState.from_conversation(found) != expected or current() is not True:
                raise ValueError("The coding session changed; reload its current settings")
            yield store
            found = graph.record(store, expected.conversation)
            if found is None or CodingState.from_conversation(found) != expected or current() is not True:
                raise ValueError("The coding session changed before publishing its result")

    def append(self, cid: str, role: str, content: str, *,
               reasoning: str = "", status: str = "complete") -> Conversation:
        if role not in ROLES:
            raise ValueError(f"a message is from {' or '.join(ROLES)}, not {role!r}")
        if not safe(cid):
            raise ValueError("invalid conversation id")
        with graph.opened(self.root) as store, store.transaction():
            found = graph.get(store, cid)
            if found is None:
                raise ValueError("no such chat")
            message = Message(role=role, content=content, reasoning=reasoning, status=status)
            graph.append(store, found, message, len(found.messages))
            found.messages.append(message)
            if not found.title and role == "user":
                found.title = title(content)
                graph.save(store, found)
        return found

    def update(self, cid: str, *, expected_revision: int | None = None, **changes) -> Conversation | None:
        if set(changes) - {"model", "settings", "title"}:
            raise ValueError("unknown conversation field")
        model, settings, title = (changes.get(key) for key in ("model", "settings", "title"))
        if not safe(cid):
            return None
        with graph.opened(self.root) as store, store.transaction():
            found = graph.get(store, cid)
            if found is None:
                return None
            original = found.model, found.settings
            coding = bool(found.settings["harness"] or (isinstance(settings, dict) and settings.get("harness")))
            if (coding and (model is not None or settings is not None)
                    and (type(expected_revision) is not int or expected_revision != found.policy_revision)):
                raise ValueError("The coding session changed; reload its current settings")
            if settings is not None:
                if not isinstance(settings, dict):
                    raise ValueError("conversation settings must be an object")
                found.settings = checked({**found.settings, **settings})
            if model is not None:
                if not isinstance(model, str):
                    raise ValueError("model must be text")
                found.model = model
            if title is not None:
                if not isinstance(title, str):
                    raise ValueError("title must be text")
                found.title = title.strip()[:TITLE_CHARS]
            if (found.model, found.settings) != original:
                found.policy_revision += 1
            graph.save(store, found)
        return found

    def remove(self, cid: str) -> bool:
        if not safe(cid):
            return False
        with graph.opened(self.root) as store, store.transaction():
            return graph.remove(store, cid)

    def search(self, needle: str) -> list[Conversation]:
        want = needle.strip().casefold()
        return [conversation for conversation in self.all()
                if not want or want in conversation.title.casefold()
                or any(want in message.content.casefold() for message in conversation.messages)]
