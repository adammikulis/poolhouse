"""What git itself makes of two histories that have one merge base or several: its merge of them, and the conflicts it leaves."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Merged:
    """A tree (or the commit holding it) as git merged it, and the paths it could not merge, which hold conflict hunks with their base."""

    tree: str
    conflicted: frozenset[str]


def output(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=False, env=env)


def bases(one: str, other: str) -> list[str]:
    """Every merge base of two commits: more than one after a criss-cross, where each side merged a commit of the other."""
    return output("merge-base", "--all", one, other).stdout.split()


def merged(one: str, other: str) -> Merged | None:
    """Git's own merge of two commits, which merges several merge bases into one first; None when this git cannot write it."""
    done = output("-c", "merge.conflictStyle=diff3", "merge-tree", "--write-tree", "--name-only", "--no-messages", "-z",
                  "--allow-unrelated-histories", one, other)
    if done.returncode not in (0, 1):
        return None
    tree, *paths = done.stdout.split("\0")
    return Merged(tree, frozenset(filter(None, paths))) if tree else None


def merged_bases(found: list[str]) -> Merged | None:
    """The one history that several merge bases stand for, as a commit; None when this git cannot write it."""
    held, conflicted = found[0], frozenset()
    identity = {**os.environ, **{f"GIT_{who}_{what}": "merge-history" for who in ("AUTHOR", "COMMITTER") for what in ("NAME", "EMAIL")}}
    for other in found[1:]:
        step = merged(held, other)
        if step is None:
            return None
        held = output("commit-tree", step.tree, "-p", held, "-p", other, "-m", "merged bases", env=identity).stdout.strip()
        if not held:
            return None
        conflicted |= step.conflicted
    return Merged(held, conflicted)


def regions(lines: list[str]) -> list[str | tuple[list[str], list[str], list[str]]]:
    """The lines of a merged file: each line outside a conflict, and each conflict hunk as (one side, base, other side). A base that is
    itself conflicted (two merge bases that disagree) keeps its longer markers, so it is read again by the caller."""
    out: list[str | tuple[list[str], list[str], list[str]]] = []
    at = 0
    while at < len(lines):
        opened = re.match(r"(<{7,})(?: |$)", lines[at])
        hunk = _hunk(lines, at + 1, len(opened.group(1))) if opened else None
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
        line = lines[at]
        if re.match(rf">{{{size}}}(?: |$)", line):
            return sides, at + 1
        if side == 0 and re.match(rf"\|{{{size}}}(?: |$)", line):
            side = 1
        elif side < 2 and line == "=" * size:
            side = 2
        else:
            sides[side].append(line)
    return None
