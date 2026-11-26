"""What a launch asks of the confinement: the harness, the directories it gets and the one bridge it reaches."""

from __future__ import annotations

from dataclasses import dataclass, field

from poolhouse import coding_policy

__all__ = ["HARNESSES", "Spec"]

HARNESSES = ("claude", "codex", "pi", "opencode")
"""The coding harnesses Poolhouse launches (docs/harnesses.md)."""


@dataclass(frozen=True, slots=True)
class Spec:
    """One launch, as the launcher describes it to the library. Every directory exists before the profile is built: the stage is the
    parent-owned copy of the project, the home is the agent's private home, the install tree is the real path of the harness's own
    files, the patches directory is the one directory of refused changes the session may read (4.2 item 2)."""

    harness: str
    stage: str
    home: str
    install: str
    model_port: int
    session_socket: str = ""
    proxy_port: int = 0
    patches: str = ""
    project: str = ""
    policy: coding_policy.Policy = field(default_factory=coding_policy.Policy)
    interactive: bool = False
    frozen: tuple[str, ...] = ()
    """Paths inside the agent home the launcher wrote and the harness reads: its settings, its hooks and its client. The home is writable, so these
    are refused a write one by one, and the harness cannot rewrite the gate it runs under."""

    @property
    def read_only_stage(self) -> bool:
        """`read-only` and any profile in plan mode mount the stage read-only (8.1); no other profile moves the boundary (I13)."""
        return not self.policy.writable
