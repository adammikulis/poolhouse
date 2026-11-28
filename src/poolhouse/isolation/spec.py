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
    server: str = ""
    """On macOS, the private folder of the Unix socket a harness's own server listens on (`Rendering.serves`): the session serves and reaches
    sockets in it and makes no other file there. Empty where the harness runs no server, or the box has a loopback of its own (Linux)."""
    environments: tuple[str, ...] = ()
    """The real paths of the project's environment folders the stage links (`workspace.environment_links`): read, run and on ``PATH``, never
    written."""
    window: tuple[int, ...] = ()
    """Loopback ports a program of the session may serve on and reach, each bound for it by the launcher (`isolation.ports`); never one the box
    binds itself."""
    window_socket: str = ""
    """The Unix socket on which the launcher hands those ports over."""
    temp: str = ""
    """The session's own temporary folder, its ``TMPDIR``: on macOS a short one in the shared temporary folder, so the path of a socket made in
    it fits; empty where the agent home holds the temporary files."""
    withheld: tuple[int, ...] = ()
    """Loopback ports of this machine's services that a box which reaches its loopback may still not reach: the model server's."""
    commits: bool = False
    """The session commits in its copy, so it writes the stage's `.git`; the launcher reads nothing there after the start."""

    @property
    def read_only_stage(self) -> bool:
        """`read-only` and any profile in plan mode mount the stage read-only (8.1); no other profile moves the boundary (I13)."""
        return not self.policy.writable
