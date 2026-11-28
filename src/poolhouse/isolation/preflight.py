"""The preflight every launch runs inside its own proved box before the model starts: git shows the project, and the project's tests start.

What is checked is read from the working copy's own files and no flag: `git status`, `diff`, `log` and `show`; then, for a stage the session
writes, the project's test runner, asked to collect one small test file (`scripts/test` where the project has one, pytest for a Python project
with tests, the test script's program for a `package.json`, `cargo metadata` for a Cargo project). What the launcher can set up it has set up
before this runs (`environments`, `history`, `profile`); what still fails is said in plain words to the launching session at once, in a
delegate's first message and in the result, and an editing run that could not test does not end as plainly done (`Findings.untested`).
"""

from __future__ import annotations

import json
import shlex
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from poolhouse import sandbox
from poolhouse.isolation.environment import SHELL, SHELL_DEFAULT_S
from poolhouse.isolation.profile import Profile

__all__ = ["Findings", "checks", "run", "shell"]

GIT = "git status --short >/dev/null && git diff --stat >/dev/null"
HISTORY = f"{GIT} && git diff HEAD --stat >/dev/null && git log -1 --format=%h >/dev/null && git show --name-status HEAD >/dev/null"
"""What a project with a commit is checked for: its history too."""
SNAPSHOT = ('t=$(mktemp -d "${TMPDIR:-/tmp}/snapshot.XXXXXX") && git init -q --bare "$t" && '
            'printf \'%s\\n\' "$PWD/.git/objects" > "$t/objects/info/alternates" && cp .git/index "$t/index" && '
            'git --git-dir="$t" --work-tree=. write-tree >/dev/null; s=$?; rm -rf "$t"; exit $s')
"""Writes the tree of a copy of the stage's index in a repository of its own that borrows the stage's objects, as a tool's snapshot of the
working copy does: every object the index names must be there (`isolation.history`). Neither status nor diff reads them all."""
BARE = "/usr/bin/env -i PATH=/usr/bin:/bin git --version >/dev/null"
"""git as a program of the project may run it: with no variable of the box's and only the system's `PATH`, which on macOS finds Apple's shim."""
WARM = "{ /usr/bin/xcrun --find git; /usr/bin/xcrun --find python3; } >/dev/null 2>&1 || true"
"""The two shims of `/usr/bin` a shell runs most, asked of the system's selector (`xcrun`) once, on macOS, before the model starts: its first
look for a tool in a box takes seconds and writes to standard error, and it keeps the answer in the box's ``TMPDIR``, so it answers at once
and in silence after."""
SAID = 300
"""The characters of a failed check's own last words a finding repeats."""


@dataclass(frozen=True, slots=True)
class Findings:
    """What failed: why the tests cannot start and why git does not show the project, each empty when it works."""

    untested: str = ""
    git: str = ""

    def words(self) -> str:
        """One plain sentence for each thing that does not work in the box; empty when all of it does."""
        said = []
        if self.untested:
            said.append(f"The preflight could not start this project's tests in the sandbox, so work here is untested ({self.untested}).")
        if self.git:
            said.append(f"The preflight found that git does not show the project in the sandbox ({self.git}).")
        return " ".join(said)


def _smallest(stage: Path) -> str:
    """The smallest pytest file of the project's `tests` (or root), as its path in the stage; empty when there is none."""
    found = [each for folder, pattern in ((stage / "tests", "**/test_*.py"), (stage, "test_*.py")) for each in folder.glob(pattern)
             if each.is_file() and not each.is_symlink()]
    return str(min(found, key=lambda each: (each.stat().st_size, str(each))).relative_to(stage)) if found else ""


def _node_test(stage: Path) -> str:
    try:
        script = json.loads((stage / "package.json").read_text(encoding="utf-8"))["scripts"]["test"]
        return str(script).split()[0]
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return ""


def checks(stage: Path) -> tuple[tuple[str, str], ...]:
    """The commands that show the project's tests start, from its own files: the kind (`tests`) and one shell line each."""
    found: list[tuple[str, str]] = []
    one = _smallest(stage)
    python = any((stage / each).is_file() for each in ("pyproject.toml", "pytest.ini", "tox.ini", "setup.py", "setup.cfg", "requirements.txt"))
    if one and (stage / "scripts" / "test").is_file():
        found.append(("tests", f"scripts/test all {shlex.quote(one)} --collect-only"))
    elif one and python:
        found.append(("tests", f"python -m pytest --collect-only -q -p no:cacheprovider {shlex.quote(one)}"))
    if program := _node_test(stage):
        if program != "node":
            found.append(("tests", "command -v node"))
        found.append(("tests", f"PATH=\"$PWD/node_modules/.bin:$PATH\" command -v {shlex.quote(program)}"))
    if (stage / "Cargo.toml").is_file():
        found.append(("tests", "cargo metadata --offline --no-deps --format-version 1 >/dev/null"))
    return tuple(found)


def shell(profile: Profile, command: str) -> sandbox.Result:
    """``command`` run in the box in the stage, by its shell, within a shell call's default time."""
    bounded = replace(profile.policy, limits=replace(profile.policy.limits, wall_seconds=SHELL_DEFAULT_S))
    return sandbox.run([SHELL, "-c", command], bounded, cwd=profile.stage)


def _failed(profile: Profile, command: str) -> str:
    """Why ``command``, run in the box in the stage, does not work: the command and its last words; empty when it ends well."""
    try:
        done = shell(profile, command)
    except (OSError, ValueError, RuntimeError) as why:
        return f"`{command}` did not run: {why}"
    if done.ok:
        return ""
    lines = [line.strip() for line in f"{done.stdout}\n{done.stderr}".splitlines() if line.strip()]
    return f"`{command}` failed: {' | '.join(lines[-3:])[-SAID:]}"


def run(profile: Profile, committed: bool) -> Findings:
    """Run the preflight in ``profile``'s box, with ``committed`` saying that the project has a history. A stage the session cannot write is
    checked for git alone. On macOS the developer tools are looked for first (`WARM`)."""
    if sys.platform == "darwin":
        _failed(profile, WARM)
    stage = Path(profile.stage)
    untested = [] if profile.read_only else [why for _, command in checks(stage) if (why := _failed(profile, command))]
    git = [why for command in (HISTORY if committed else GIT, SNAPSHOT, BARE) if (why := _failed(profile, command))]
    return Findings("; ".join(untested), "; ".join(git))
