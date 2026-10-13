"""A person's approval of one thing, asked once and read back by whoever needs it: a request of a kind whose
subject, words and recorded fields are exactly what was approved."""

from __future__ import annotations

import os

from poolhouse import requests
from poolhouse.requests.model import VIAS, shown

__all__ = ["DAY", "MOST_WAITING", "approved", "ask"]

DAY = 86400.0
MOST_WAITING = 10
"""How many requests of one kind wait at once; more would crowd out every other kind."""


def approved(kind: str, subject: str, sentence: str, **fields: str) -> bool:
    """Whether a person approved a request of ``kind`` with this ``subject`` that asked ``sentence`` and
    recorded ``fields``."""
    want = {shown(k, "name"): shown(v, "subject") for k, v in fields.items()}
    return any(one.answered_by in VIAS and one.subject == shown(subject, "subject")
               and one.reason == shown(sentence, "reason")
               and all(one.extra.get(k) == v for k, v in want.items())
               for one in requests.list_requests(state="approved", kind=kind))


def ask(kind: str, subject: str, sentence: str, **fields: str) -> str:
    """Put ``sentence`` to a person as a request of ``kind`` recording ``fields``; the text that
    tells the caller where it is answered, or that too many already wait."""
    others = [one for one in requests.list_requests(state="pending", kind=kind)
              if one.key != shown(subject, "name")]
    if len(others) >= MOST_WAITING:
        return (f"{sentence} {len(others)} other models already wait for a person; answer or clear "
                f"those in the requests inbox first.")
    origin = requests.Origin(agent=os.environ.get("POOLHOUSE_AGENT", ""))
    handle = requests.raise_request(requests.Ask(
        kind, subject, sentence, ("approve", "deny"), origin, ttl=DAY, key=subject,
        extra=tuple(fields.items())))
    tail = ("A person answers it in the requests inbox (`poolhouse-requests`, or the Requests page), "
            "then this can be repeated.")
    if handle.request is None or handle.outcome().state == "denied":
        tail = "No person can be asked from here; run it again from the app or a terminal."
    return f"{sentence} {tail}"
