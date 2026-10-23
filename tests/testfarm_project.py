"""Neutral projects for the test runner's tests: a temporary directory, invented names, nothing of this repository."""
from __future__ import annotations

import textwrap
from pathlib import Path

NAME = "widgets"
LIST_SCRIPT = 'for file in tests/t_*.sh; do echo "$file"; done\n'
PASSING = "exit 0\n"

SHELL_CONFIG = """
schema = 1

[project]
name = "widgets"

[list]
run = ["sh", "tests/list.sh"]
timeout = 30

[run]
argv = ["sh", "{ids}"]
timeout = 60
"""


def project(tmp_path: Path, config: str | None = SHELL_CONFIG, files: dict[str, str] | None = None) -> Path:
    """A project directory named ``widgets`` under ``tmp_path`` holding ``config`` as testfarm.toml (None writes none) and ``files``."""
    root = tmp_path / NAME
    root.mkdir(parents=True)
    if config is not None:
        (root / "testfarm.toml").write_text(textwrap.dedent(config).lstrip(), encoding="utf-8")
    for relative, content in (files or {}).items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


def shell_project(tmp_path: Path, config: str = SHELL_CONFIG, tests: tuple[str, ...] = ("parse", "render")) -> Path:
    """The ``widgets`` project with one shell script per name in ``tests`` and a LIST that prints them."""
    files = {"tests/list.sh": LIST_SCRIPT, **{f"tests/t_{name}.sh": PASSING for name in tests}}
    return project(tmp_path, config, files)


def with_list(tmp_path: Path, script: str, extra: str = "") -> Path:
    """The ``widgets`` project whose LIST is the shell ``script``."""
    return project(tmp_path, SHELL_CONFIG + extra, {"tests/list.sh": script})
