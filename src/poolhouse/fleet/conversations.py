"""Saved conversations with transactional messages and model/project relationships."""
from __future__ import annotations

import uuid
from pathlib import Path

from poolhouse.fleet import conversation_creation as creation, conversation_graph as graph
from poolhouse.fleet.conversation_settings import checked
from poolhouse.fleet.conversation_types import (
    ROLES,
    TITLE_CHARS,
    Conversation,
    Message,
    safe,
    title,
)
from poolhouse.studio.store import digest

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

    def create(self, operation_id: str, request: dict, identity: dict, *, defaults: dict, current) -> dict:
        """Create or attach one conversation under its original live browser intent."""
        key, requested = creation.operation(operation_id), creation.request(request)
        creation.current(current)
        self.root.mkdir(parents=True, exist_ok=True)
        bound = creation.binding(self.root, identity)
        with graph.opened(self.root) as store, store.transaction():
            creation.current(current)
            row = creation.receipt(store, key, bound)
            if row is not None:
                if row["request"] != requested:
                    raise creation.Conflict(creation.ERROR)
            else:
                settings = checked({**checked(defaults), **requested["settings"]})
                made = Conversation(id=uuid.uuid4().hex, model=requested["model"], title=requested["title"], settings=settings)
                if graph.get(store, made.id) is not None:
                    raise creation.Conflict(creation.ERROR)
                graph.save(store, made)
                row = {"version": 1, "binding": bound, "request": requested,
                       "response": {**made.public(), "operation_id": operation_id}}
                store.put_doc(key, row)
                store.upsert_node({"id": key, "kind": "conversation-create", "attrs": {"digest": digest(row)}})
                store.upsert_edge({"source": key, "target": graph.node_id(made.id), "rel": "created"})
            if creation.binding(self.root, identity) != bound:
                raise creation.Conflict(creation.ERROR)
            creation.current(current)
            return row["response"]

    def creation(self, operation_id: str, identity: dict, *, current) -> dict | None:
        """Read historical creation evidence without creating a conversation or operation."""
        key = creation.operation(operation_id)
        creation.current(current)
        if not (self.root / "conversations.db").exists():
            creation.current(current)
            return None
        bound = creation.binding(self.root, identity)
        with graph.opened(self.root) as store:
            creation.current(current)
            row = creation.receipt(store, key, bound, lookup=True)
            if creation.binding(self.root, identity) != bound:
                raise creation.Conflict(creation.ERROR)
            creation.current(current)
            return row["response"] if row is not None else None

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

    def update(self, cid: str, *, model: str | None = None, settings: dict | None = None, title: str | None = None) -> Conversation | None:
        if not safe(cid):
            return None
        with graph.opened(self.root) as store, store.transaction():
            found = graph.get(store, cid)
            if found is None:
                return None
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
