"""The launcher's report: the one message of kind `isolation.report`, posted under the launcher's own identity (docs/design-isolated-launcher.md,
4.4 step 4 and 7).

A report is posted by the launcher's own session, which is a different identity from the confined session's: the board names the sender of
every message itself, and the confined session's token is not the launcher's. The board's message types are a closed list, so the kind
travels as the fixed subject `isolation.report` of a `status` message; the session bridge puts the sender label in front of every
subject a confined session sends and refuses every kind that begins with `isolation`, so no message of a session can carry this subject
(AF1). What a report says is the launcher's words and nothing a session chose (AF2): the words are built by the slice that reports.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

__all__ = ["KIND", "Reporter", "genuine"]

KIND = "isolation.report"
"""The message kind of the launcher's report; on the board it is the subject of a message of type `status`."""
TYPE = "status"
Post = Callable[..., dict[str, Any]]


class Reporter:
    """The launcher's way to report: ``post`` is the `post` of the launcher's own board session (`board.session.Session.post`)."""

    def __init__(self, post: Post) -> None:
        self._post = post

    def post(self, to: str, text: str) -> dict[str, Any]:
        """Post ``text``, the launcher's own words, to ``to`` as a message of kind `isolation.report`."""
        return self._post(to, TYPE, text, subject=KIND)


def genuine(sender: str, fields: dict[str, Any], launcher: str) -> bool:
    """Whether a board message is the launcher's report: sent by the launcher's own session, of the report's kind. A message that only looks
    like one (the right words from another sender, the right sender's name in the text) is not."""
    return sender == launcher and fields.get("type") == TYPE and fields.get("subject") == KIND
