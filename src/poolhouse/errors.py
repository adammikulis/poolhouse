"""The errors of the public API (docs/api.md): the shared base for named operational failures.

Node, request, serving, model-transfer and decision failures derive from these. Specific failures retain their
original catch types and details. Argument validation and raw filesystem or optional-dependency errors keep
their standard Python exception types; callers may need to handle those too.
"""

from __future__ import annotations

__all__ = ["Conflict", "Denied", "Error", "NotRunning"]


class Error(Exception):
    """Something the public API could not do; the message says why and what to change."""


class NotRunning(Error):
    """No node answers on this device; start it with `poolhouse` (or `python -m poolhouse.device_setup --yes`)."""


class Denied(Error):
    """The node refused: the session may not do that, or does not hold the grant for it."""


class Conflict(Denied):
    """Another session holds the claim that was asked for."""
