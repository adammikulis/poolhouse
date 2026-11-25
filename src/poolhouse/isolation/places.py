"""The folders no coding session is given: a credential location, the person's home and every folder above it, Poolhouse's own state, and
(for a project) a system folder or the working copy of another coding session (docs/design-isolated-launcher.md, 8.2 and 8.3)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from poolhouse import home, secretpaths

__all__ = ["STAGE_PREFIX", "SYSTEM", "never", "project_refusal"]

STAGE_PREFIX = "poolhouse-coding-"
"""The start of the name of the private folder a coding session's working copy is kept in, under the temporary folder."""
SYSTEM = ("/bin", "/sbin", "/usr", "/etc", "/lib", "/lib32", "/lib64", "/libx32", "/boot", "/dev", "/proc", "/sys", "/System", "/Library",
          "/private/etc")
"""The operating system's own folders: a coding session reads them as the system toolchain (8.2) and never works in one."""


def _around(path: Path, roots: set[Path]) -> bool:
    """Whether ``path`` is one of ``roots``, a folder above one, or a folder inside one."""
    return any(path == root or path in root.parents or root in path.parents for root in roots)


def never(real: str) -> str:
    """Why the folder ``real`` (a resolved path) can be neither read nor worked in by any coding session, for any caller and any source: a
    credential location, the person's home or a folder above it, or Poolhouse's state root, a folder above it or one inside it. Empty when
    nothing refuses it."""
    path = Path(real)
    if secretpaths.path_hit(real) is not None:
        return "credential location"
    if secretpaths.inside_home(path):
        return "home folder"
    if _around(path, {home.home().resolve(), home.account_roots()[0].resolve()}):
        return "state root"
    return ""


def project_refusal(real: str) -> str:
    """The key of the sentence (`refusals.SENTENCES`) that refuses the resolved folder ``real`` as a project to work in, or empty: what
    `never` refuses, a system folder, and a folder inside the working copy of another coding session."""
    why = never(real)
    if why:
        return {"credential location": "project_credential", "home folder": "project_home", "state root": "project_state"}[why]
    path = Path(real)
    if any(path == Path(each) or Path(each) in path.parents for each in SYSTEM):
        return "project_system"
    stages = Path(os.path.realpath(tempfile.gettempdir()))
    if stages in path.parents and path.relative_to(stages).parts[0].startswith(STAGE_PREFIX):
        return "project_stage"
    return ""
