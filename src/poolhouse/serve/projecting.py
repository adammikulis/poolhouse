"""The projector half of a start (`ServerManager`): which one a server is given before it starts."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from poolhouse.serve import guarded, projector
from poolhouse.serve.backend import ServerSpec
from poolhouse.serve.leases import recorded_servers

__all__ = ["Projecting"]

logger = logging.getLogger(__name__)


class Projecting:
    """Mixed into the manager."""

    state_file: Path
    say: Callable[[str], None] | None
    if TYPE_CHECKING:
        def reclaim(self, port: int) -> bool: ...

    def _projected(self, spec: ServerSpec, spare: bool) -> tuple[ServerSpec, bool, ServerSpec]:
        """``spec`` as it starts, whether its projector was picked by default, and the spec it is admitted and compared by."""
        seen = projector.chosen(spec, recorded_servers(self.state_file))
        if seen.note:
            self._tell(spec, seen.note)
        if seen.picked:
            try:
                guarded.verify(seen.spec.mmproj, state_file=self.state_file, stop=self.reclaim)
            except guarded.SentinelRefused as refused:
                self._tell(spec, f"serving without the vision projector: {refused}")
                seen = projector.Chosen(replace(seen.spec, mmproj=None, vision=False))
        return seen.spec, seen.picked, projector.counted_as(spec, seen, spare)

    def _tell(self, spec: ServerSpec, line: str) -> None:
        (self.say or logger.warning)(f"port {spec.port}: {line}")
