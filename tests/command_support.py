"""What a test daemon runs for ``POST /jobs``."""

from __future__ import annotations

from collections.abc import Sequence


def any_command(argv: Sequence[str]) -> list[str]:
    """What a test daemon runs for ``POST /jobs``: whatever it is sent."""
    return list(argv)
