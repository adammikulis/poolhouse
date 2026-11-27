"""Who runs this process, as the node and the operating system attest it (docs/design-isolated-launcher.md, 8.3).

Whether a caller is an agent or a terminal is decided from the process tree and never from the environment (a person's session and the
session that adds a project are no agents). The node reads the processes behind a request by the process id the operating system reports for
the other end of its socket, so a request cannot name its own, and answers with the registered sessions that still run a process the caller has
above it (`lineage`, `sessions`). An agent the node places in a session this way may do what every other may, a main session and a helper of
one alike. A caller none of them runs in is a terminal, or a process that left its tree (the node cannot attest what is not in the tree: a
process that double-forks away from its agent is read as a terminal, which is the limit of any process check and is stated in the design).
The environment is read only to narrow: a marked caller the node places in no session is neither. The node's state is the account's own
(`home.guarded`), so a caller cannot answer for itself with a node of its own.

An unattended agent (a delegate, a board's local agent) launches, widens and registers nothing, nor does what it starts: `unattended`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import psutil

from poolhouse import caller, home, node_health, session_identity
from poolhouse.board.place import PLACER

__all__ = ["UNATTENDED", "Ancestry", "Session", "Unattested", "attest", "node_state", "unattended"]

MOST = 64
"""The most sessions one answer is read for, and the most processes above a caller that are read for a runner."""

UNATTENDED = ("poolhouse.delegate_run", "poolhouse.workspace.localcoding", "poolhouse.workspace.localloop")
"""The modules whose process runs an agent with nobody at it: the runner of a delegate and the two runners of a board's local agents."""


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

    def may_launch(self, env: Mapping[str, str] | None = None) -> bool:
        """Whether the caller may launch coding agents, widen a launch, and register or remove a project: an agent the node places in a session
        above it, a lead or a helper of one, or a terminal outside every agent. A marked caller the node places in no session is neither."""
        return self.agent or not caller.marked(env)


def _words(process: psutil.Process) -> list[str]:
    """The command line of ``process``; empty for one that has ended or is another account's, which runs no agent of this one."""
    try:
        return process.cmdline()
    except (psutil.Error, OSError):
        return []


def unattended() -> bool:
    """Whether the runner of an unattended agent (`UNATTENDED`) started this process, at any depth. The operating system's process table says
    what runs above it, which no variable changes and the process asking cannot rewrite. A runner is not something it started itself, so it
    launches the agent it was made to run. A process whose parent had already ended has no runner above it: the limit of every check of a
    process tree (8.3)."""
    process = psutil.Process()
    own = _words(process)[1:]
    for depth in range(MOST):
        try:
            process = process.parent()
        except (psutil.Error, OSError):   # nothing above can be read, as above a process whose parent has ended
            break
        if process is None:
            break
        words = _words(process)
        if depth == 0 and words[1:] == own:   # an interpreter that starts the interpreter as its child (a virtual environment's on Windows)
            continue
        if any(flag == "-m" and module in UNATTENDED for flag, module in pairwise(words)):
            return True
    return False


@home.real_roots
def node_state() -> Path:
    """The state of the node this account runs: the account's own, never one the environment names (`home.guarded`)."""
    return home.guarded("node")


def _session(raw: object) -> Session:
    if (not isinstance(raw, dict) or not all(isinstance(raw.get(key), str) for key in ("board", "name", "parent", "harness"))
            or not isinstance(raw.get("person"), bool)):
        raise ValueError("a session is its board, name, parent, harness and whether it is a person's")
    return Session(raw["board"], raw["name"], raw["parent"], raw["harness"], raw["person"])


@home.real_roots
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
