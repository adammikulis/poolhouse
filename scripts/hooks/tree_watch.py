#!/usr/bin/env python3
"""The calls the hooks make about worktrees; a worktree is read from git when listed or swept, so each does nothing."""

from __future__ import annotations

import sys


def check(stage: str, cwd: str, environment: dict | None = None, **_: object) -> str:
    """The text a hook adds about worktrees: none."""
    return ""


def started(stage: str, cwd: str, name: str, purpose: str) -> None:
    """A subagent started: nothing is recorded."""


def stopped(stage: str, cwd: str, environment: dict) -> None:
    """A subagent stopped: nothing is recorded."""


def main(argv: list[str]) -> int:
    """The git post-commit entry: nothing to do."""
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
