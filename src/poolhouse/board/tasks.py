"""The task board of one board as one session uses it: the node's `task_*` methods with their arguments checked first.

The node folds the task entries and decides who may take each step; this side checks the shape of what is sent (the
same bounds the node enforces) and answers times in seconds.
"""

from __future__ import annotations

from typing import Any

from poolhouse.board import taskspec
from poolhouse.board.session import Session

__all__ = ["Tasks"]

CHECKPOINT = {"summary", "progress", "commit", "environment"}


def _checkpoint(value: Any) -> dict[str, str]:
    if type(value) is not dict or set(value) - CHECKPOINT:
        raise ValueError("unsupported checkpoint fields")
    fields = {key: taskspec.text(item, key, 2000) for key, item in value.items()}
    if not fields.get("summary"):
        raise ValueError("a checkpoint summary is required")
    return fields


class Tasks:
    """Create, claim, work, submit, review and land the tasks of the session's board."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def _call(self, method: str, **params: Any) -> Any:
        return taskspec.seconds(self.session.call(method, **params))

    def create(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Make a task, or return the one this session already made under the same source key."""
        return dict(self._call("task_create", **taskspec.wire_spec(taskspec.task_spec(spec))))

    def list(self) -> dict[str, Any]:
        """Every task with its lease, checkpoints, proposal and reviews, and the board's overview and metrics."""
        return dict(self._call("task_list"))

    def get(self, ident: str) -> dict[str, Any]:
        """One task in full."""
        return dict(self._call("task_get", task=ident))

    def admit(self, ident: str) -> dict[str, Any]:
        """Read this worker's working task after checking its task and model leases."""
        if type(ident) is not str or ident.count(":") != 2 or not taskspec.TASK_ID.fullmatch(ident):
            raise ValueError("task admission requires the canonical task entry ID")
        return dict(self._call("task_admit", task=ident))

    def subscribe(self, ident: str, subscriber: str = "") -> dict[str, Any]:
        """Hear the task's state changes in the inbox; a parent may subscribe its worker."""
        return dict(self._call("task_watch", task=ident, on=True, **({"who": subscriber} if subscriber else {})))

    def unsubscribe(self, ident: str) -> dict[str, Any]:
        """Stop hearing them."""
        return dict(self._call("task_watch", task=ident, on=False))

    def watchers(self, ident: str) -> list[str]:
        """The sessions that follow the task."""
        return list(self.session.call("task_watchers", task=ident)["watchers"])

    def assign(self, ident: str, worker: str, lease: str, capabilities: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
        """Give ``worker`` the task on the model lease ``lease`` it holds; the allocation the worker then claims."""
        return dict(self._call("task_assign", task=ident, worker=worker, lease=lease, capabilities=list(capabilities)))

    def claim(self, ident: str, allocation_id: str) -> dict[str, Any]:
        """Take a queued task on an allocation made to this session; the task lease."""
        return dict(self._call("task_claim", task=ident, assign=allocation_id))

    def heartbeat(self, ident: str) -> dict[str, Any]:
        """Renew the lease of the task this session is working."""
        return dict(self._call("task_beat", task=ident))

    def checkpoint(self, ident: str, value: dict[str, Any]) -> dict[str, Any]:
        """Record progress under the live lease."""
        return dict(self._call("task_checkpoint", task=ident, **_checkpoint(value)))

    def submit(self, ident: str, value: dict[str, Any]) -> dict[str, Any]:
        """Hand in artifact hashes and claimed checks for independent review."""
        fields = taskspec.submission(value)
        return dict(self._call("task_submit", task=ident, **{**fields, "usage": taskspec.wire_usage(fields["usage"])}))

    def review(self, ident: str, decision: dict[str, Any]) -> dict[str, Any]:
        """Decide a submitted task as an independent session."""
        task = self.get(ident)
        proposal = task.get("proposal") or {}
        fields = taskspec.review_decision(decision, task["acceptance"], proposal.get("artifacts", {}))
        if "verified_usage" in fields:
            fields["verified_usage"] = taskspec.wire_usage(fields["verified_usage"])
        return dict(self._call("task_review", task=ident, **fields))

    def block(self, ident: str, reason: str, kind: str = "infrastructure") -> dict[str, Any]:
        """Record that the task cannot go on, without an outcome."""
        return dict(self._call("task_block", task=ident, reason=taskspec.text(reason, "blocked reason", 2000), kind=kind))

    def recover(self, ident: str, reason: str) -> dict[str, Any]:
        """Put a task whose lease expired back in the queue, spending one retry."""
        return dict(self._call("task_ready", task=ident, reason=taskspec.text(reason, "resume reason", 2000), expired=True))

    def resume(self, ident: str, reason: str) -> dict[str, Any]:
        """Put a blocked task back in the queue once what blocked it is dealt with."""
        return dict(self._call("task_ready", task=ident, reason=taskspec.text(reason, "resume reason", 2000), expired=False))

    def mode(self) -> dict[str, Any]:
        """The enforcement mode of the board: `open` or `strict`."""
        return dict(self.session.call("task_mode"))

    def set_mode(self, mode: str) -> dict[str, Any]:
        """Change the mode; a person, or a session with no parent holding the grant."""
        return dict(self.session.call("task_mode", mode=mode))

    def scope(self, ident: str, scope: dict[str, Any]) -> dict[str, Any]:
        """Record the checkout reserved for the task, whose landing then completes it."""
        return dict(self._call("task_scope", task=ident, scope=scope))

    def record(self, ident: str, kind: str, rid: str, record: dict[str, Any]) -> dict[str, Any]:
        """Keep an integration, an attempt or an event of landing the task; a later record of one id adds to it."""
        return dict(self._call("task_record", task=ident, kind=kind, rid=rid, record=record))

    def complete(self, ident: str, landed: str) -> dict[str, Any]:
        """Mark an accepted task completed by the commit that landed it."""
        return dict(self._call("task_complete", task=ident, landed=landed))
