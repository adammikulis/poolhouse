"""The git hooks are in place, not merely available."""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from poolhouse.doctor import HOOKS, hooks_of

REPO = Path(__file__).resolve().parent.parent


def test_the_shipped_hooks_are_the_ones_git_will_run() -> None:
    """conftest installs these at the start of a run, so a machine that never ran the
    installer still refuses a real name, a raised budget and an agent's push."""
    if subprocess.run(["git", "rev-parse", "--git-dir"], cwd=REPO,
                      capture_output=True, timeout=10).returncode != 0:
        pytest.skip("not a git checkout")
    found = hooks_of(REPO)
    assert found is None or found.good, f"{found.said}; fix: {found.fix}"


def test_the_installer_and_the_check_agree_on_which_hooks_there_are() -> None:
    """A hook the installer puts in place and the check does not look for is a hook that
    can go missing without anything saying so."""
    installer = ast.parse((REPO / "scripts" / "install-hooks.py").read_text(encoding="utf-8"))
    pairs = next(ast.literal_eval(row.value) for row in installer.body if isinstance(row, ast.Assign)
                 and any(isinstance(name, ast.Name) and name.id == "HOOKS" for name in row.targets))
    assert pairs, "the installer names no hooks"
    assert sorted(pairs) == sorted(HOOKS), f"installer installs {sorted(pairs)}, check looks for {sorted(HOOKS)}"
