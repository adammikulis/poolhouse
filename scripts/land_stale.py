"""Whether checks that passed for a branch still stand after origin gained commits: what the new upstream paths can reach."""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from pathlib import Path

import affected
import land_exec as ex
import land_git as lg

SHARED = re.compile(r"^(tests/conftest\.py|scripts/test[^/]*|pyproject\.toml|packaging/.*)$")
GUARD_TESTS = frozenset(f"tests/{name}" for name in affected.GUARDS)


@dataclass(frozen=True)
class Green:
    """Checks that passed: the tip and the base they ran on, the paths the branch changes, the test files chosen for it and the checks' names."""

    sha: str
    base: str
    changed: frozenset[str]
    tests: frozenset[str]
    names: tuple[str, ...]
    tail: tuple[str, ...] = ()


def green_of(sha: str, base: str, plan: ex.Plan, tail: list[str]) -> Green:
    """The passed ``plan`` of ``sha`` against ``base``, with the end of the output of its checks."""
    tests = frozenset(f for check in plan.checks for f in check.files)
    return Green(sha, base, frozenset(plan.changed), tests, tuple(c.name for c in plan.checks), tuple(tail))


def carried(green: Green, sha: str, base: str) -> Green:
    """``green`` as it stands for ``sha`` against ``base`` once origin's new commits are known not to reach it."""
    return dataclasses.replace(green, sha=sha, base=base)


def reach(wt: Path, upstream: list[str], green: Green) -> str:
    """The first reason a change to the ``upstream`` paths can alter a test that ran for ``green``, or an empty string."""
    shared = next((p for p in upstream if SHARED.match(p) or p in affected.EVERYTHING or lg.LANDER.fullmatch(p)), "")
    if shared:
        return f"origin changed {shared}, which every test depends on"
    try:
        pick = affected.select(wt, upstream, frozenset(p for p in upstream if not (wt / p).exists()))
    except (OSError, RuntimeError, ValueError, SyntaxError):
        return "the paths origin changed could not be matched to tests"
    if pick.unmapped:
        return f"no selector maps {pick.unmapped[0]}, which origin changed"
    hit = sorted((pick.files & green.tests) - (GUARD_TESTS - set(upstream)))
    return f"origin changed code that {hit[0]} reaches" if hit else ""


def stale(wt: Path, green: Green, sha: str, base: str, plan: ex.Plan) -> str:
    """Why the passed checks of ``green`` say nothing about ``sha`` against ``base``, or an empty string when what origin gained cannot change them.

    ``plan`` is the plan of ``sha`` against ``base`` and ``wt`` holds ``sha``. Whole-tree results (the gate, the budgets) and the tree-wide
    guard tests are not run again for upstream paths that touch nothing the branch changes or its tests reach.
    """
    if not lg.is_ancestor(wt, green.base, base):
        return f"origin no longer descends from {green.base[:12]}, the commit the checks ran against"
    if frozenset(plan.changed) != green.changed or tuple(c.name for c in plan.checks) != green.names:
        return "the changes of the branch or the checks they call for differ from the ones that ran"
    upstream = lg.changed(wt, green.base, base)
    both = sorted(green.changed.intersection(upstream))
    if both:
        return f"origin changed {both[0]}, which the branch changes too"
    edited = sorted(green.changed.intersection(lg.changed(wt, green.sha, sha)))
    if edited:
        return f"{edited[0]} is not the file that was tested"
    tests_now = frozenset(f for check in plan.checks for f in check.files)
    new = sorted(tests_now - green.tests - (GUARD_TESTS - set(upstream)))
    if new:
        return f"origin gained a path through which {new[0]} reaches the branch"
    return reach(wt, upstream, green) if upstream else ""
