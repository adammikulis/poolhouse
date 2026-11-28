"""What a job runs is fixed when it is scheduled: the code of its bench steps and the files its lines name, copied into the job.

A job runs hours or days later, unsandboxed, so nothing it executes or reads as settings may be a file that can change in
between. The code of a source checkout is copied (a build of Poolhouse that ships no benchmark runs it from that copy); an input
file a bench line names is copied too, and one too large to copy (a model) is recorded by size and time and refused when changed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from poolhouse import paths
from poolhouse.home import expand
from poolhouse.schedule import spawn

__all__ = ["COPY_MOST", "changed", "check_bench", "code_dir", "environment", "freeze", "revision", "snapshot"]

COPY_MOST = 64 * 1024 * 1024
"""The largest input file copied into a job; a larger one is a model, and is recorded as it is."""
PARTS = ("src/poolhouse", "docs", "contracts", "patches")
"""What of a checkout the benchmark reads: the package, the documents its fixed text and configurations are, the contracts and
the patches of the llama.cpp build."""
NEEDED = ("src/poolhouse/bench", "src/poolhouse/schedule")
CHECK_S = 600


def code_dir(named: str = "") -> Path | None:
    """The source checkout whose code a job's bench steps run: ``named``, else the checkout this command runs from; None when
    the installed Poolhouse ships the benchmark itself. `ValueError` when there is no benchmark to run."""
    if named:
        found = expand(named).resolve()
        if missing := [part for part in NEEDED if not (found / part).is_dir()]:
            raise ValueError(f"--code {found} is not a Poolhouse checkout with scheduled jobs: it has no {', '.join(missing)}")
        return found
    if (checkout := paths.source_checkout()) is not None and (checkout / NEEDED[0]).is_dir():
        return checkout
    if find_spec("poolhouse.bench") is not None:
        return None
    raise ValueError("this copy of Poolhouse ships no benchmark (a release build leaves it out): name a source checkout with "
                     "--code DIR, and its code is copied into the job")


def snapshot(code: Path, into: Path) -> None:
    """Copy the parts of the checkout ``code`` that a bench step reads to ``into``."""
    for part in PARTS:
        if (code / part).is_dir():
            shutil.copytree(code / part, into / part, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "node_modules"))


def revision(code: Path) -> dict[str, Any]:
    """The commit the copy was taken from and whether the checkout held changes that were in no commit."""
    def git(*words: str) -> str:
        try:
            done = subprocess.run(["git", "-C", str(code), *words], capture_output=True, text=True, timeout=60, check=False)
        except (OSError, subprocess.SubprocessError):
            return ""
        return done.stdout.strip() if done.returncode == 0 else ""

    return {"checkout": str(code), "commit": git("rev-parse", "HEAD"),
            "uncommitted": bool(git("status", "--porcelain", "--", *PARTS))}


def environment(copy: Path | None) -> dict[str, str]:
    """The environment of a bench step: the clean one of a job, importing from the job's copy of the code when it has one."""
    out = spawn.environment()
    if copy is not None:
        out["PYTHONPATH"] = str(copy / "src")
    return out


def check_bench(argvs: Sequence[Sequence[str]], *, python: str, copy: Path | None, cwd: Path) -> list[dict[str, Any]]:
    """What the parser of the code that will run them makes of each bench line (`poolhouse.schedule.check`)."""
    command = [python, "-P", "-B", "-m", "poolhouse.schedule.cli", "check-lines"]
    try:
        done = subprocess.run(command, input=json.dumps([list(argv) for argv in argvs]), capture_output=True, text=True,
                              cwd=cwd, env=environment(copy), timeout=CHECK_S, check=False)
        if done.returncode == 0:
            rows = json.loads(done.stdout.strip().splitlines()[-1])
            if len(rows) == len(argvs):
                return rows
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as why:
        raise ValueError(f"the bench lines could not be checked with {python}: {why}") from why
    raise ValueError(f"the bench lines could not be checked with {python}: "
                     f"{(done.stderr.strip().splitlines() or ['it printed nothing'])[-1][:300]}")


def _identity(path: Path) -> list[int]:
    found = path.stat()
    return [found.st_size, found.st_mtime_ns, found.st_ino]


def freeze(argv: list[str], values: Sequence[str], *, base: Path, into: Path, pins: Mapping[str, list[int]]) -> list[str]:
    """``argv`` with every word that names an input file (one of ``values``, alone or after ``=``) replaced by the job's copy of
    it under ``into``, or by its full path with its size and time added to ``pins`` when it is too large to copy. A relative
    name is read from ``base``, the folder the job was scheduled from."""
    out = list(argv)
    wanted = {value for value in values if value}
    for index, word in enumerate(argv):
        cuts = [0, *(at + 1 for at, char in enumerate(word) if char == "=")]
        for cut in cuts:
            name = word[cut:]
            if name not in wanted:
                continue
            found = expand(name)
            found = found if found.is_absolute() else base / found
            if not found.is_file():
                continue
            if found.stat().st_size > COPY_MOST:
                kept = found.absolute()
                pins[str(kept)] = _identity(kept)
            else:
                kept = into / str(sum(1 for _ in into.glob("*")) if into.is_dir() else 0) / found.name
                kept.parent.mkdir(parents=True, mode=0o700)
                shutil.copyfile(found, kept)
            out[index] = word[:cut] + str(kept)
            break
    return out


def changed(pins: Mapping[str, list[int]]) -> str:
    """Which pinned input is no longer the file it was when the job was scheduled, "" when all are."""
    for name, identity in pins.items():
        try:
            if _identity(Path(name)) == list(identity):
                continue
        except OSError:
            pass
        return f"{name} is not the file it was when the job was scheduled"
    return ""
