"""The package metadata of this tree, written from its own ``pyproject.toml``, for processes that import Poolhouse from ``src``.

Nothing installs a tree under test, so without this its version, requirements and entry points would be those of whatever copy the
interpreter happens to hold, or none. Put `folder`'s answer on the import path ahead of any installed copy.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from packaging.version import Version

ROOT = Path(__file__).resolve().parent.parent
NAME = "tests/_generated_metadata"
"""Where the metadata sits in a tree; git ignores it."""


def _requirements(project: dict) -> list[str]:
    lines = [f"Requires-Dist: {one}" for one in project.get("dependencies", ())]
    for extra, wanted in project.get("optional-dependencies", {}).items():
        lines.append(f"Provides-Extra: {extra}")
        for one in wanted:
            spec, _, marker = one.partition(";")
            only = f"({marker.strip()}) and " if marker.strip() else ""
            lines.append(f'Requires-Dist: {spec.strip()} ; {only}extra == "{extra}"')
    return lines


def _entry_points(project: dict) -> str:
    groups = {"console_scripts": project.get("scripts", {}), "gui_scripts": project.get("gui-scripts", {}),
              **project.get("entry-points", {})}
    return "".join(f"[{group}]\n" + "".join(f"{name} = {target}\n" for name, target in named.items()) + "\n"
                   for group, named in groups.items() if named)


def _keep(path: Path, text: str) -> None:
    """``text`` at ``path``, replaced in one step and only when it differs: several test workers ask at once, and a confined one may
    only read."""
    try:
        if path.read_text(encoding="utf-8") == text:
            return
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    draft = path.with_name(f".{path.name}.{os.getpid()}")
    draft.write_text(text, encoding="utf-8")
    draft.replace(path)


def folder(root: Path = ROOT) -> Path:
    """The folder to put on the import path so that ``root``'s Poolhouse states its own version and entry points. A tree with no such
    project (a made-up one a test runs the runner in) gets nothing written."""
    into = root / NAME
    try:
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8")).get("project", {})
    except FileNotFoundError:
        return into
    if project.get("name") != "poolhouse" or "version" not in project:
        return into
    version = str(Version(project["version"]))
    own = into / f"poolhouse-{version}.dist-info"
    for other in into.glob("poolhouse-*.dist-info"):
        if other != own:  # an earlier version of this tree
            for file in other.iterdir():
                file.unlink()
            other.rmdir()
    head = ["Metadata-Version: 2.1", f"Name: {project['name']}", f"Version: {version}"]
    _keep(own / "METADATA", "\n".join([*head, *_requirements(project)]) + "\n")
    _keep(own / "entry_points.txt", _entry_points(project))
    return into
