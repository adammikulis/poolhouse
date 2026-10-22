"""Built Rust test executables: compile the named integration-test targets of the node crate in one cargo call and name each file.

A test that asks ``cargo test --test X`` for every target waits on cargo's lock for the shared target folder behind every other
run's compile, and walks the fingerprints again each time. Building the targets once and running the files cargo wrote costs the
compile once, outside the tests' own timers, and leaves the run itself independent of any other cargo on the machine.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Callable
from pathlib import Path

import testnode

PACKAGE = "poolhouse-node"
JOBS_DIVISOR = 4
"""A build takes a quarter of the cores; the tests it serves each run on one."""


def jobs() -> str:
    """The compiler processes one build may run at once."""
    return str(max(1, (os.cpu_count() or 1) // JOBS_DIVISOR))


def build_argv(cargo: str, targets: list[str]) -> list[str]:
    """The cargo command that compiles ``targets`` without running them and reports each artifact as a JSON line."""
    argv = [cargo, "test", "--locked", "--offline", "--no-run", "--message-format=json", "-p", PACKAGE]
    for target in targets:
        argv.extend(("--test", target))
    return argv


def executables(output: str, targets: list[str]) -> dict[str, Path]:
    """The test executable of each target, from cargo's JSON lines; a target cargo reported no file for is an error."""
    found: dict[str, Path] = {}
    for line in output.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        spec = row.get("target") or {}
        if (row.get("reason") == "compiler-artifact" and spec.get("kind") == ["test"] and spec.get("name") in targets
                and (row.get("profile") or {}).get("test") is True and row.get("executable")
                and Path(spec.get("src_path", "")).parent.parent.name == PACKAGE):
            found[spec["name"]] = Path(row["executable"])
    missing = [target for target in targets if target not in found or not found[target].is_file()]
    if missing:
        raise testnode.BuildError("cargo built no test executable for " + ", ".join(missing))
    return found


def build(cargo: str, work: Path, targets: list[str], execute: Callable[[list[str], Path, dict[str, str]], str],
          environment: dict[str, str]) -> dict[str, Path]:
    """Compile ``targets`` of the staged workspace ``work`` in one cargo call (run by ``execute``); the executable of each.

    The workspace products this build used are touched, so the pruning of idle products (``testartifacts``) leaves them be.
    """
    output = execute(build_argv(cargo, targets), work, {**environment, "CARGO_BUILD_JOBS": jobs()})
    made = executables(output, targets)
    touch(output, work)
    return made


def touch(output: str, work: Path) -> None:
    """Mark as used each file (and its dep-info file) cargo reported for a crate or test of the workspace under ``work``."""
    for line in output.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("reason") != "compiler-artifact" or not str((row.get("target") or {}).get("src_path", "")).startswith(str(work)):
            continue
        for name in (*row.get("filenames", ()), row.get("executable") or ""):
            path = Path(name)
            for used in (path, path.with_suffix(".d")) if name else ():
                with contextlib.suppress(OSError):
                    os.utime(used)
