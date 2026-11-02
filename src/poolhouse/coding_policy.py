"""Immutable coding permissions, separate from pool task governance."""
from __future__ import annotations

from dataclasses import dataclass

READ_ONLY = "read-only"
ASK_CHANGES = "ask-changes"
ASK_DANGEROUS = "ask-dangerous"
YOLO = "yolo"
PERMISSIONS = (READ_ONLY, ASK_CHANGES, ASK_DANGEROUS, YOLO)
PLAN = "plan"
EXECUTE = "execute"
EXECUTION_MODES = (PLAN, EXECUTE)


@dataclass(frozen=True, slots=True)
class Policy:
    permissions: str = ASK_DANGEROUS
    execution_mode: str = EXECUTE

    def __post_init__(self) -> None:
        if self.permissions not in PERMISSIONS or self.execution_mode not in EXECUTION_MODES:
            raise ValueError("invalid coding permissions or execution mode")

    @property
    def writable(self) -> bool:
        return self.permissions != READ_ONLY and self.execution_mode == EXECUTE
