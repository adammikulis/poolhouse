"""Who runs this process, as the node attests it: the sessions whose process tree the caller is in (docs/design-isolated-launcher.md, 8.3).

Whether a caller is the lead, an agent or a terminal is decided from the process tree and never from the environment (a person's session and
the session that adds a project are no agents). The node reads the
processes behind a request by the process id the operating system reports for the other end of its socket, so a request cannot name its own, and answers with the registered sessions that
still run a process the caller has above it (`lineage`, `sessions`). A caller none of them runs in is a terminal, or a process that left its
tree (the node cannot attest what is not in the tree: a process that double-forks away from its agent is read as a terminal, which is the
limit of any process check and is stated in the design). Inside one harness the lead and its subagents are one process tree, so the tree
alone cannot tell them apart: the environment is read only to narrow (a subagent's own marker, or the name of a helper of the tree), never
to widen. The node's state is the account's own (`home.guarded`), so a caller cannot answer for itself with a node of its own.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from poolhouse import home, node_health, session_identity
from poolhouse.board.place import PLACER

__all__ = ["Ancestry", "Session", "Unattested", "attest", "node_state"]

SUBAGENT_MARKER = "POOLHOUSE_SUBAGENT"
"""What a harness's hook puts in the command of a subagent; it can only say "helper", never "lead"."""
NAMED = "POOLHOUSE_WORKSPACE_AGENT"
MOST = 64
"""The most sessions one answer is read for; a tree holds a lead and its helpers."""


class Unattested(PermissionError):
    """The node could not say who is asking; the message says why in one plain sentence, and nothing is guessed."""


@dataclass(frozen=True, slots=True)
class Session:
    """A registered session the caller runs in: its board, its name, its parent (empty for a main session), its harness and whether it is a
    person's session."""

    board: str
    name: str
    parent: str
    harness: str
    person: bool = False


@dataclass(frozen=True, slots=True)
class Ancestry:
    """The sessions the node found above the caller; none for a terminal."""

    sessions: tuple[Session, ...]

    @property
    def agent(self) -> bool:
        return bool(self.sessions)

    @property
    def lead(self) -> Session | None:
        """A main session of the tree the caller runs in."""
        return next((each for each in self.sessions if not each.parent), None)

    def helper(self, env: Mapping[str, str] | None = None) -> bool:
        """Whether the caller says it is a helper: its command carries a subagent's marker, or it names a helper of the tree as its own. The
        environment is read only to narrow."""
        found = os.environ if env is None else env
        claimed = found.get(NAMED) or os.environ.get(NAMED, "")
        if found.get(SUBAGENT_MARKER) or os.environ.get(SUBAGENT_MARKER):
            return True
        return any(each.name == claimed and each.parent for each in self.sessions)


def node_state() -> Path:
    """The state of the node this account runs: the account's own, never one the environment names (`home.guarded`)."""
    return home.guarded("node")


def _session(raw: object) -> Session:
    if (not isinstance(raw, dict) or not all(isinstance(raw.get(key), str) for key in ("board", "name", "parent", "harness"))
            or not isinstance(raw.get("person"), bool)):
        raise ValueError("a session is its board, name, parent, harness and whether it is a person's")
    return Session(raw["board"], raw["name"], raw["parent"], raw["harness"], raw["person"])


def attest(state: Path | None = None) -> Ancestry:
    """The sessions the caller runs in, as the node of ``state`` (default: the account's) reads them from the caller's own process.

    Raises `Unattested` with the reason when the node does not answer, is older than this code, or could not read the process asking."""
    try:
        answer = session_identity.ReadConnection(state or node_state()).query("lineage")
    except (OSError, ValueError, KeyError, node_health.Refused) as why:
        raise Unattested("Refused: the node did not answer, so who is asking cannot be told; the node starts with Poolhouse itself.") from why
    sessions, lineage = answer.get("sessions"), answer.get("lineage")
    if not isinstance(sessions, list) or not isinstance(lineage, list):
        raise Unattested("Refused: this node is older than Poolhouse and cannot tell who is asking; Poolhouse's update replaces it.")
    if not lineage:
        raise Unattested("Refused: the node could not read the process that asked, so who it is cannot be told.")
    try:
        return Ancestry(tuple(each for each in (_session(each) for each in sessions[:MOST]) if not each.person and each.harness != PLACER))
    except ValueError as why:
        raise Unattested("Refused: the node's answer about who is asking was not in the form Poolhouse reads.") from why
