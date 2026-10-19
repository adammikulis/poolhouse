"""Pyright reads the scripts and the hooks with the paths they import from, so an editor shows no false unresolved imports."""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
IMPORT_ROOTS = {"src", "scripts", "scripts/hooks"}


def environments() -> dict[str, dict]:
    config = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    return {env["root"]: env for env in config["tool"]["pyright"]["executionEnvironments"]}


def test_the_repository_root_environment_resolves_scripts_and_hooks_imports() -> None:
    assert set(environments()["."].get("extraPaths", [])) >= IMPORT_ROOTS


def test_the_tests_environment_resolves_the_same_roots() -> None:
    assert set(environments()["tests"].get("extraPaths", [])) >= IMPORT_ROOTS


def test_every_import_root_exists() -> None:
    for env in environments().values():
        for extra in env.get("extraPaths", []):
            assert (REPO / extra).is_dir(), extra
