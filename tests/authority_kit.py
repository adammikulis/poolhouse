"""A person's yes to loosening the authority registry, for the tests that revoke a delegation and then restore it."""

from __future__ import annotations

from collections.abc import Iterable

import pytest

from poolhouse import authority, person


def delegate(gates: Iterable[str], project: str = "") -> list[dict[str, str]]:
    """Move ``gates`` to delegated as a person at a terminal who types yes."""
    with pytest.MonkeyPatch.context() as context:
        for marker in person.AGENT_MARKERS:
            context.delenv(marker, raising=False)
        return authority.set_state(gates, authority.DELEGATED, by="person", scope=authority.Scope(project=project),
                                   asking=authority.Asking(terminal=(True, True), env={}, typed=lambda prompt: "yes"))
