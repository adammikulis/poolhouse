"""The experimental layer's test files run in the CI shards, on pull requests, on main and nightly."""

from __future__ import annotations

import ast
from pathlib import Path

import ci_shards
import test_release_workflows as workflows
from test_layers import EXPERIMENTAL

REPO = Path(__file__).resolve().parents[1]
SHARDS = 8


def imported_packages(path: Path) -> set[str]:
    """The ``poolhouse`` packages a test file imports, at the top or inside a function."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[1] for a in node.names if a.name.startswith("poolhouse.")}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            parts = node.module.split(".")
            if parts[0] == "poolhouse":
                found |= {parts[1]} if len(parts) > 1 else {a.name for a in node.names}
    return found


def experimental_test_files() -> dict[str, list[str]]:
    """``package -> test files`` for every package in the experimental layer, by what the file imports."""
    out: dict[str, list[str]] = {name: [] for name in EXPERIMENTAL}
    for name in ci_shards.collected_files(REPO):
        for package in imported_packages(REPO / name) & set(EXPERIMENTAL):
            out[package].append(name)
    return out


def test_every_experimental_package_has_a_test_file() -> None:
    assert {name: files for name, files in experimental_test_files().items() if not files} == {}


def test_every_test_file_of_an_experimental_package_is_in_a_shard() -> None:
    placed = {name for group in ci_shards.shard_files(REPO, SHARDS) for name in group}
    missing = {name for files in experimental_test_files().values() for name in files} - placed
    assert not missing, f"in no CI shard: {sorted(missing)}"


def test_the_shards_job_takes_no_test_file_out_and_runs_nightly_on_both_pythons() -> None:
    job = workflows.CI["jobs"]["shards"]
    command = next(line for step in job["steps"] if step.get("name") == "tests" for line in step["run"].splitlines() if "-m pytest" in line)
    after = command.split(" pytest ", 1)[1]
    assert not {"--deselect", "--ignore", "-k", "-m"} & set(after.split()) and "--ignore=" not in after
    assert "schedule" in job["strategy"]["matrix"]["python"] and "3.14" in job["strategy"]["matrix"]["python"]
    assert job["if"] == "github.event_name != 'push' || github.ref == 'refs/heads/main'"
