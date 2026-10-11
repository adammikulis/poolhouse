"""A workspace sender's standing in the reputation ledger."""

from __future__ import annotations

from poolhouse.sentinel import observers

__all__ = ["KIND", "ledger_installed", "record_injection", "sender_standing"]

KIND = "member"
"""The ledger kind a sender is recorded under."""


def _key(name: str) -> str:
    return f"workspace:{name}"


def ledger_installed() -> bool:
    """Whether a reputation ledger is installed in this process."""
    return observers.installed() is not None


def sender_standing(name: str) -> str:
    """``good``, ``watch`` or ``bad`` for the session ``name`` (``good`` when the ledger holds nothing against it or is
    not installed), ``unknown`` for no name. Never raises."""
    if not name:
        return "unknown"
    gate = observers.gate(KIND, _key(name))
    if gate is None:
        return "good"
    return gate.state if gate.state in ("watch", "bad") else "good"


def record_injection(name: str) -> None:
    """Note one held hard-marker message against the session ``name``. Never raises."""
    observers.observe(KIND, _key(name), "injection_flagged")
