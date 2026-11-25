"""The project policy a person owns: which widenings a project may ever be launched with.

It lives in the account's own state root, keyed by the id of the project the node has registered (never by the exact path a launch names, so a
worktree or another place of the project has the project's policy), and never in a file or under a control name inside the project: whatever
the repository says about itself is read as data and is never a source of a widening (docs/design-isolated-launcher.md, 8.3). It is
written by the person who registers the project, from a terminal outside any agent, and is never a prompt. The default is no policy,
which restricts nothing beyond the caps; when one is present it names every widening the project may be launched with, so an agent that
holds it cannot pass one it does not name.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from poolhouse import caller, home, node_health, session_identity
from poolhouse.files import read_json, write_json
from poolhouse.isolation import ancestry

__all__ = ["ProjectPolicy", "Unregistered", "key_of", "load", "path_of", "save"]

KINDS = ("read", "toolchain", "net", "publish")


@dataclass(frozen=True, slots=True)
class ProjectPolicy:
    """The widenings a project may be launched with, by kind; nothing is named by an empty policy."""

    read: tuple[str, ...] = ()
    toolchain: tuple[str, ...] = ()
    net: tuple[str, ...] = ()
    publish: tuple[str, ...] = ()
    in_place: bool = False

    def names(self, kind: str, subject: str) -> bool:
        """Whether the policy names ``subject`` as a widening of ``kind``."""
        if kind == "in-place":
            return self.in_place
        return subject in getattr(self, kind, ())


class Unregistered(ValueError):
    """The node holds no project that contains the folder, so there is no project to keep a policy for."""


@home.real_roots
def key_of(project: str | os.PathLike[str]) -> str:
    """The id of the project the node has registered that contains ``project`` (a worktree and any other place of it give the same id), or an
    empty string when the node holds none or cannot answer."""
    try:
        found = session_identity.ReadConnection(ancestry.node_state()).query("project_resolve", path=os.path.realpath(project))["board"]
    except (OSError, ValueError, KeyError, node_health.Refused):
        return ""
    return found if isinstance(found, str) else ""


@home.real_roots
def path_of(key: str) -> Path:
    """Where the policy of the registered project ``key`` is kept: under the account's state root, by a digest of the project's id."""
    return home.guarded("isolation", "projects", f"{hashlib.sha256(key.encode()).hexdigest()}.json")


def _plain(value: Any) -> tuple[str, ...]:
    return tuple(item for item in value if isinstance(item, str)) if isinstance(value, list) else ()


@home.real_roots
def load(project: str | os.PathLike[str]) -> ProjectPolicy | None:
    """The policy of the project the node has registered around ``project``, or None when it holds none or the person never wrote one. A file that cannot be read as a policy is a policy that names
    nothing, so a damaged record narrows and never widens."""
    key = key_of(project)
    where = path_of(key) if key else None
    if where is None or not where.exists():
        return None
    data = read_json(where, {})
    data = data if isinstance(data, dict) else {}
    return ProjectPolicy(*(_plain(data.get(kind)) for kind in KINDS), in_place=data.get("in-place") is True)


@home.real_roots
def save(project: str | os.PathLike[str], policy: ProjectPolicy, env: Mapping[str, str] | None = None) -> None:
    """Write the policy of the project the node has registered around ``project``. An agent and a process inside a confinement are refused at
    once, with the reason; a folder the node holds no project for has none to write (`Unregistered`)."""
    try:
        where = ancestry.attest()
    except ancestry.Unattested as why:
        raise caller.AgentRefused(str(why)) from why
    caller.refuse_agent("writing the widenings a project may be launched with",
                        "that is the person's own record of the project, kept outside the repository", env,
                        attested="an agent's process tree runs it" if where.agent else "")
    key = key_of(project)
    if not key:
        raise Unregistered("the node holds no project that contains this folder, so there is no project to keep a policy for")
    write_json(path_of(key), {**{kind: list(getattr(policy, kind)) for kind in KINDS}, "in-place": policy.in_place, "project": key})
