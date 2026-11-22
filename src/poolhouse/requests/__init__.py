"""The requests inbox: a notice a component raises, shown anywhere and answered at most once.

    handle = requests.raise_request(requests.Ask("host_approval", "example.org", "why",
                                                 ("allow-once", "deny")))
    outcome = handle.outcome()      # the state now; outcome.approved is true only for an approving answer

A terminal, the UI and the desktop dialog are three ways to answer the same request: the first
answer wins and a later one is refused as already resolved. An answer is bound to the fingerprint
of the words shown, and the `requests.answer` gate decides at once whether the caller may answer
(`AgentRefused` for an agent while the gate is held back). Nothing in an agent's call path waits for
a request: a call is allowed by a rule or refused with the reason before it runs.
"""

from __future__ import annotations

from poolhouse.requests.inbox import (
    Context,
    Handle,
    Outcome,
    answer,
    default,
    get,
    list_requests,
    oldest_pending,
    pending_count,
    raise_request,
    subscribe,
    summary,
)
from poolhouse.requests.model import CHOICES, KINDS, Choice, Origin, Request
from poolhouse.requests.store import Ask, Inbox, Refused, Unavailable

__all__ = ["CHOICES", "KINDS", "Ask", "Choice", "Context", "Handle", "Inbox", "Origin", "Outcome", "Refused", "Request",
           "Unavailable", "answer", "default", "get", "list_requests", "oldest_pending", "pending_count",
           "raise_request", "subscribe", "summary"]
