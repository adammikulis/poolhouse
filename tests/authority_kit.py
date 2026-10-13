"""A person's yes to loosening the authority registry, for the tests that revoke a delegation and then restore it."""

from __future__ import annotations

from collections.abc import Iterable

from poolhouse import authority


def delegate(gates: Iterable[str], project: str = "") -> list[dict[str, str]]:
    """Move ``gates`` to delegated as a person at a terminal who types yes."""
    return authority.set_state(gates, authority.DELEGATED, by="person", scope=authority.Scope(project=project),
                               asking=authority.Asking(terminal=(True, True), env={}, typed=lambda prompt: "yes"))
