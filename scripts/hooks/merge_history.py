"""What git itself makes of histories that have one merge base or several: its merge of them, and the conflicts it leaves.

A merge is computed in a bare repository made for it, which borrows the objects of this one: no setting, attribute, merge driver or
replacement of the repository, the account or the command line reaches it, so what a repository plants decides nothing.
"""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

MARKER_SIZE = 32 + secrets.randbelow(64)
"""How long the conflict markers of a merge made here are: drawn for each run, so no line a file already holds reads as one."""


@dataclass(frozen=True, slots=True)
class Merged:
    """A commit holding the tree git merged, and the paths it could not merge, which hold conflict hunks with their base."""

    tree: str
    conflicted: frozenset[str]


def output(*args: str, env: dict[str, str] | None = None, cwd: str | None = None) -> subprocess.CompletedProcess:
    """git in this repository (or the one ``env`` names), reading real objects and never their replacements."""
    done = subprocess.run(["git", *args], capture_output=True, check=False, cwd=cwd,
                          env={**(os.environ if env is None else env), "GIT_NO_REPLACE_OBJECTS": "1"})
    return subprocess.CompletedProcess(done.args, done.returncode, done.stdout.decode("utf-8", "replace"),
                                       done.stderr.decode("utf-8", "replace"))


def bases(one: str, other: str) -> list[str]:
    """Every merge base of two commits: more than one after a criss-cross, where each side merged a commit of the other."""
    return output("merge-base", "--all", one, other).stdout.split()


@contextmanager
def bare() -> Iterator[tuple[dict[str, str], str]]:
    """The environment and folder of an empty bare repository over this repository's objects, with every other source of settings off."""
    objects = output("rev-parse", "--path-format=absolute", "--git-path", "objects").stdout.strip()
    kind = output("rev-parse", "--show-object-format").stdout.strip() or "sha1"
    with tempfile.TemporaryDirectory(prefix="merge-history-") as folder:
        env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_ATTR_NOSYSTEM="1", HOME=folder, XDG_CONFIG_HOME=folder)
        output("init", "-q", "--bare", "--template=", f"--object-format={kind}", folder, env=env, cwd=folder)
        info = Path(folder) / "info"
        info.mkdir(exist_ok=True)
        (info / "attributes").write_text(f"* conflict-marker-size={MARKER_SIZE}\n")
        env.update(GIT_DIR=folder, GIT_OBJECT_DIRECTORY=objects,
                   **{f"GIT_{who}_{what}": "merge-history" for who in ("AUTHOR", "COMMITTER") for what in ("NAME", "EMAIL")})
        yield env, folder


def merged(commits: list[str]) -> Merged | None:
    """Git's own merge of the commits, one after another, which merges the several bases of each step into one first; None when this
    git cannot write it."""
    held = [output("rev-parse", "--verify", "-q", f"{name}^{{commit}}").stdout.strip() for name in commits]
    if not all(held):
        return None
    result, conflicted = held[0], frozenset()
    with bare() as (env, folder):
        for other in held[1:]:
            done = output("-c", "merge.conflictStyle=diff3", "merge-tree", "--write-tree", "--name-only", "--no-messages", "-z",
                          "--allow-unrelated-histories", result, other, env=env, cwd=folder)
            if done.returncode not in (0, 1):
                return None
            tree, *paths = done.stdout.split("\0")
            result = output("commit-tree", tree, "-p", result, "-p", other, "-m", "merged", env=env, cwd=folder).stdout.strip()
            if not result:
                return None
            conflicted |= frozenset(filter(None, paths))
    return Merged(result, conflicted)


def regions(lines: list[str], size: int = MARKER_SIZE) -> list[str | tuple[list[str], list[str], list[str]]]:
    """The lines of a file merged here: each line outside a conflict, and each conflict hunk as (one side, base, other side). Only a
    marker of exactly ``size`` is one. A base that is itself conflicted (two merge bases that disagree) holds markers two longer, so
    it is read again by the caller."""
    out: list[str | tuple[list[str], list[str], list[str]]] = []
    at = 0
    while at < len(lines):
        hunk = _hunk(lines, at + 1, size) if re.match(rf"<{{{size}}}(?: |$)", lines[at]) else None
        if hunk is None:
            out.append(lines[at])
            at += 1
        else:
            out.append(hunk[0])
            at = hunk[1]
    return out


def _hunk(lines: list[str], start: int, size: int) -> tuple[tuple[list[str], list[str], list[str]], int] | None:
    """The hunk whose markers are ``size`` long and whose first line is ``start``, and the line after it; None when it never ends."""
    sides: tuple[list[str], list[str], list[str]] = ([], [], [])
    side = 0
    for at in range(start, len(lines)):
        line = lines[at].rstrip("\r")
        if re.match(rf">{{{size}}}(?: |$)", line):
            return sides, at + 1
        if side == 0 and re.match(rf"\|{{{size}}}(?: |$)", line):
            side = 1
        elif side < 2 and line == "=" * size:
            side = 2
        else:
            sides[side].append(lines[at])
    return None
