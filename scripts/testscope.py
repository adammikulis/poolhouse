"""How much of the suite an agent may run locally: the files it changed, any number of them, never the whole suite."""
from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

WHOLE_TIERS = ("fast", "full", "slow", "all")
AGENT_MARKERS = ("CLAUDECODE", "POOLHOUSE_AGENT", "POOLHOUSE_NONINTERACTIVE", "CODEX_THREAD_ID", "CODEX_SESSION_ID")
REFUSAL = "the whole suite runs on a schedule and on pull requests; name the files you changed (scripts/test all <files>)"


class Refused(ValueError):
    """A run an agent may not ask the broker for."""


def agent_marked(env: Mapping[str, str] | None = None) -> bool:
    """Whether ``env`` (the process's by default) marks an agent or a harness process; a person's terminal sets none."""
    env = os.environ if env is None else env
    return any(env.get(name) for name in AGENT_MARKERS)


def named_files(words: list[str], root: Path) -> set[str]:
    """The test files the pytest words name: a file or node id counts as its file, a directory as every test file under it."""
    found: set[str] = set()
    for word in words:
        if word.startswith("-"):
            continue
        path = root / word.split("::", 1)[0]
        if path.is_file():
            found.add(str(path.resolve()))
        elif path.is_dir():
            found.update(str(item.resolve()) for item in path.rglob("test_*.py"))
    return found


def selects_whole_suite(words: list[str], root: Path) -> bool:
    """Whether the files the words name include every test file under ``root/tests``."""
    suite = {str(item.resolve()) for item in (root / "tests").rglob("test_*.py")}
    return bool(suite) and suite <= named_files(words, root)


def tier_refusal(tier: str, words: list[str], root: Path, env: Mapping[str, str] | None = None) -> str:
    """The refusal line for an agent's run of ``tier``: a whole-suite tier needs named files, and no tier may name every test file."""
    if not agent_marked(env):
        return ""
    return REFUSAL if (tier in WHOLE_TIERS and not named_files(words, root)) or selects_whole_suite(words, root) else ""


def size_refusal(words: list[str], root: Path, env: Mapping[str, str] | None = None) -> str:
    """The refusal line for an agent's pytest run that names every test file, or an empty string."""
    if not agent_marked(env):
        return ""
    return REFUSAL if selects_whole_suite(words, root) else ""
