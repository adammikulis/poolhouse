"""Loosening the authority registry for the tests that revoke a delegation and then restore it."""

from __future__ import annotations

from collections.abc import Iterable

import pytest

from poolhouse import authority, caller


def delegate(gates: Iterable[str], project: str = "") -> list[dict[str, str]]:
    """Move ``gates`` to delegated as a process with no agent marker (the only caller that may loosen a gate); nothing is typed."""
    with pytest.MonkeyPatch.context() as context:
        for marker in caller.AGENT_MARKERS:
            context.delenv(marker, raising=False)
        return authority.set_state(gates, authority.DELEGATED, by="person", scope=authority.Scope(project=project), env={})
