"""How much of the suite an agent may run locally: only the files it changed, at most 30 of them."""
from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

MAX_FILES = 30
WHOLE_TIERS = ("fast", "full", "slow", "all")
AGENT_MARKERS = ("CLAUDECODE", "POOLHOUSE_AGENT", "POOLHOUSE_NONINTERACTIVE", "CODEX_THREAD_ID", "CODEX_SESSION_ID")
REFUSAL = ("the full suite runs on GitHub for every pushed commit; run only the files you changed "
           "(scripts/test all <files>, at most 30)")


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


def tier_refusal(tier: str, words: list[str], root: Path, env: Mapping[str, str] | None = None) -> str:
    """The refusal line for an agent's run of ``tier``: a whole-suite tier needs 1 to 30 named files, any tier at most 30."""
    if not agent_marked(env):
        return ""
    count = len(named_files(words, root))
    return REFUSAL if count > MAX_FILES or (tier in WHOLE_TIERS and count == 0) else ""


def size_refusal(words: list[str], root: Path, env: Mapping[str, str] | None = None) -> str:
    """The refusal line for an agent's pytest run that names more than 30 test files, or an empty string."""
    if not agent_marked(env):
        return ""
    return REFUSAL if len(named_files(words, root)) > MAX_FILES else ""
