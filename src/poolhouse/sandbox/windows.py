"""The Windows backend: it says native Windows cannot confine a coding agent's network to the model, and starts nothing.

An AppContainer with no network capability has no network at all, loopback included, and the exemption that lets it reach the model opens
every loopback port, not one; a per-port rule needs a filter whose reach no probe has shown (docs/design-isolated-launcher.md, 5.4, route B and
PROBE P-win-loopback). Until one is shown this backend is unavailable with that reason, and there is no weaker mode: not an unelevated
restricted token, not a loopback exemption of every port, not a start with the network open. The WSL2 route runs the agent inside Ubuntu,
where bubblewrap confines it (route A, docs/windows-runtime.md).
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence

from poolhouse.sandbox.backend import NO_REFUSAL_LOG, Availability, Wrapped
from poolhouse.sandbox.policy import Policy, PolicyError

__all__ = ["REASON", "ROUTE", "Windows"]

REASON = ("this Windows computer cannot confine the network of a coding agent to the model: a process in an AppContainer reaches loopback "
          "only through an exemption that opens every port, not one, and no rule for one port has been shown (docs/design-isolated-launcher.md, 5.4)")
ROUTE = "; run the coding agent inside WSL2, where namespaces confine it (docs/windows-runtime.md)"


class Windows:
    """Native Windows: unavailable, with the reason, and it runs nothing."""

    name = "windows"

    def available(self) -> Availability:
        return Availability(False, REASON + (ROUTE if shutil.which("wsl.exe") else ""))

    def wrap(self, argv: Sequence[str], policy: Policy) -> Wrapped:
        raise PolicyError(self.available().reason + "; command was not run")

    def watch(self, tag: str) -> None:
        return None

    def started(self, tag: str, pid: int) -> None:
        return None

    def denials(self, tag: str, since: float) -> list[dict[str, str]] | None:
        return None

    def unreadable(self, tag: str) -> str:
        return NO_REFUSAL_LOG

    def finish(self, tag: str) -> None:
        return None
