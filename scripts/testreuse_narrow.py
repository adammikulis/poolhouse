"""Which pytest arguments leave part of a test file out of a run."""

from __future__ import annotations

import os
import shlex
from collections.abc import Mapping

TIER_MARKS = frozenset({"not slow and not heavy", "slow"})
NARROWING_FLAGS = frozenset({
    "--lf", "--last-failed", "--sw", "--stepwise", "--sw-skip", "--stepwise-skip", "--sw-reset", "--stepwise-reset",
    "--setup-plan", "--setup-only", "--collect-only", "--co"})
NARROWING_VALUES = ("--deselect", "--override-ini", "--config-file")
VALUE_LETTERS = "npWr"
WIDENING_LETTERS = "kco"


def short_option(word: str, following: str) -> bool:
    """Whether a single-dash word, which may bundle flags before an option that takes a value, narrows the run."""
    for index, letter in enumerate(word[1:], 1):
        if letter in WIDENING_LETTERS:
            return True
        if letter == "m":
            return (word[index + 1:] or following) not in TIER_MARKS
        if letter in VALUE_LETTERS:
            return False
    return False


def narrowed(command: list[str], environ: Mapping[str, str] | None = None) -> bool:
    """Whether the command, or ``PYTEST_ADDOPTS``, runs fewer tests than the whole of each file it names."""
    words = command[command.index("pytest") + 1:] if "pytest" in command else list(command)
    try:
        words += shlex.split((os.environ if environ is None else environ).get("PYTEST_ADDOPTS", ""))
    except ValueError:
        return True
    for index, word in enumerate(words):
        following = words[index + 1] if index + 1 < len(words) else ""
        if "::" in word or word in NARROWING_FLAGS or word.startswith(NARROWING_VALUES):
            return True
        if word.startswith("-") and not word.startswith("--") and short_option(word, following):
            return True
    return False
