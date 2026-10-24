"""Git hook inspection is read-only; explicit installation uses the advertised hook sources."""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

from poolhouse.doctor import HOOKS, hooks_of

REPO = Path(__file__).resolve().parent.parent


def test_readiness_inspection_preserves_a_checkout_without_installed_hooks(tmp_path) -> None:
    subprocess.run(["git", "init", "-q", "--template=", str(tmp_path)], check=True)
    config = (tmp_path / ".git/config").read_bytes()
    found = hooks_of(tmp_path)
    assert found is None or not found.good
    assert (tmp_path / ".git/config").read_bytes() == config
    assert not any((tmp_path / ".git/hooks" / name).exists() for name in HOOKS)


def test_the_installer_and_the_check_agree_on_which_hooks_there_are() -> None:
    """A hook the installer puts in place and the check does not look for is a hook that
    can go missing without anything saying so."""
    installer = ast.parse((REPO / "scripts" / "install-hooks.py").read_text(encoding="utf-8"))
    pairs = next(ast.literal_eval(row.value) for row in installer.body if isinstance(row, ast.Assign)
                 and any(isinstance(name, ast.Name) and name.id == "HOOKS" for name in row.targets))
    assert pairs, "the installer names no hooks"
    assert sorted(pairs) == sorted(HOOKS), f"installer installs {sorted(pairs)}, check looks for {sorted(HOOKS)}"
