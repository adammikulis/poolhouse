"""The one place the runner starts a command of a project: under the deny-default sandbox, with the project read-only."""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from poolhouse import sandbox
from poolhouse.sandbox import policies

__all__ = ["Outcome", "run_confined"]

_PASSED_ENV = ("LANG", "TZ")


@dataclass(frozen=True, slots=True)
class Outcome:
    """How a command ended: its status, what it printed and for how long it ran."""

    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool
    seconds: float


def _environment(scratch: str, bin_dir: str) -> dict[str, str]:
    kept = {name: value for name, value in os.environ.items() if name in _PASSED_ENV or name.startswith("LC_")}
    return {"LANG": "C.UTF-8", **kept, "PATH": os.pathsep.join([bin_dir, *policies.SYSTEM_EXEC]), "HOME": scratch, "TMPDIR": scratch}


def run_confined(argv: Sequence[str], project: Path, *, timeout: float | None) -> Outcome:
    """Run ``argv`` in ``project`` with no network, the project and the interpreter readable, a scratch directory the only place it can
    write and an allowlist environment, for at most ``timeout`` seconds (never unbounded: a caller with no number refuses first)."""
    if timeout is None:
        raise ValueError("a command of a project is never started without a time limit")
    root = os.path.realpath(project)
    bin_dir = str(Path(os.path.realpath(sys.executable)).parent)
    with policies.scratch("poolhouse-testfarm-") as scratch:
        policy = sandbox.Policy("testfarm-project", read=(root, scratch, bin_dir, *policies.runtime_reads()), write=(scratch,),
                                exec=(*policies.SYSTEM_EXEC, bin_dir, root), net=sandbox.Net.deny(), env=_environment(scratch, bin_dir),
                                limits=sandbox.Limits(wall_seconds=timeout, output_bytes=None))
        result = sandbox.run(list(argv), policy, cwd=root)
    return Outcome(result.returncode, result.stdout.encode("utf-8", "replace"), result.stderr.encode("utf-8", "replace"),
                   result.timed_out, result.seconds)
