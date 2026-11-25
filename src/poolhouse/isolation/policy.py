"""The project policy a person owns: which widenings a project may ever be launched with.

It lives in the state root, keyed by the project's real path, and never in a file or under a control name inside the project: whatever
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

from poolhouse import caller, home
from poolhouse.files import read_json, write_json

__all__ = ["ProjectPolicy", "load", "path_of", "save"]

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


def path_of(project: str | os.PathLike[str]) -> Path:
    """Where the policy of ``project`` is kept: under the state root, by a digest of the project's real path."""
    digest = hashlib.sha256(os.path.realpath(project).encode()).hexdigest()
    return home.state("isolation", "projects", f"{digest}.json")


def _plain(value: Any) -> tuple[str, ...]:
    return tuple(item for item in value if isinstance(item, str)) if isinstance(value, list) else ()


def load(project: str | os.PathLike[str]) -> ProjectPolicy | None:
    """The policy of ``project``, or None when the person never wrote one. A file that cannot be read as a policy is a policy that names
    nothing, so a damaged record narrows and never widens."""
    where = path_of(project)
    if not where.exists():
        return None
    data = read_json(where, {})
    data = data if isinstance(data, dict) else {}
    return ProjectPolicy(*(_plain(data.get(kind)) for kind in KINDS), in_place=data.get("in-place") is True)


def save(project: str | os.PathLike[str], policy: ProjectPolicy, env: Mapping[str, str] | None = None) -> None:
    """Write the policy of ``project``. An agent and a process inside a confinement are refused at once, with the reason."""
    caller.refuse_agent("writing the widenings a project may be launched with",
                        "that is the person's own record of the project, kept outside the repository", env)
    write_json(path_of(project), {**{kind: list(getattr(policy, kind)) for kind in KINDS}, "in-place": policy.in_place,
                                  "project": os.path.realpath(project)})
