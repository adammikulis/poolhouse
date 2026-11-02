"""Read canonical domain declarations as data before global validation."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

from .common import keys


def load_fragments(root: Path) -> list[tuple[str, dict]]:
    directory = root / "domains"
    if directory.is_symlink():
        raise ValueError("canonical domains cannot be a symlink")
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise ValueError("canonical domains must be a directory")
    result = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if path.is_symlink() or not path.is_file() or not re.fullmatch(r"[a-z][a-z0-9_-]*\.toml", path.name):
            raise ValueError("unknown canonical domain entry")
        fragment = tomllib.loads(path.read_text(encoding="utf-8"))
        required = {"version", "status", "type", "operation"}
        keys(fragment, required, required)
        if (type(fragment["version"]) is not int or fragment["version"] != 1 or fragment["status"] != "proposal"
                or not isinstance(fragment["type"], list) or not isinstance(fragment["operation"], list)
                or not (fragment["type"] or fragment["operation"])):
            raise ValueError("unsupported or empty canonical domain")
        result.append((path.name, fragment))
    return result
