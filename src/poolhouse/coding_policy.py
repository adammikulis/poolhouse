"""Immutable coding permissions, separate from pool task governance."""
from __future__ import annotations

from dataclasses import dataclass

READ_ONLY = "read-only"
GUARDED = "guarded"
"""Reversible changes run; destructive and unclassifiable calls are denied at once with the reason."""
YOLO = "yolo"
PERMISSIONS = (READ_ONLY, GUARDED, YOLO)
PLAN = "plan"
EXECUTE = "execute"
EXECUTION_MODES = (PLAN, EXECUTE)


@dataclass(frozen=True, slots=True)
class Policy:
    permissions: str = GUARDED
    execution_mode: str = EXECUTE

    def __post_init__(self) -> None:
        if self.permissions not in PERMISSIONS or self.execution_mode not in EXECUTION_MODES:
            raise ValueError("invalid coding permissions or execution mode")

    @property
    def writable(self) -> bool:
        return self.permissions != READ_ONLY and self.execution_mode == EXECUTE
