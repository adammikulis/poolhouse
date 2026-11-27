"""What a shell command of a delegate says of a test run: whether it was one, its counts, and whether a failure looks like the box's setup rather
than the work. Read from the command's text, its output and its exit status; nothing is run."""

from __future__ import annotations

import re
from typing import Any

__all__ = ["read"]

COMMAND = re.compile(
    r"(?:\A|[;&|(\n])[ \t]*(?:[A-Za-z_]\w*=(?:'[^']*'|\"[^\"]*\"|\S*)\s+)*(?:(?:timeout|nice|env|time)\s+(?:-?\w+\s+)*?)?"
    r"(?:(?:\S*/)?(?:python[\d.]*|py)\s+(?:-[A-Za-z]\s+)*-m\s+|(?:uv|poetry|pdm|hatch)\s+run\s+|(?:npx|bunx)\s+)?"
    r"(?:\S*/)?(?:scripts/test|pytest|py\.test|tox|nox|unittest|ctest|jest|vitest|mocha|cargo\s+(?:\+\S+\s+)?(?:test|nextest)|go\s+test|"
    r"node\s+--test|(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test[\w:-]*|make\s+(?:test|check)\w*)(?![\w./-])")
"""A shell line that runs a test program: its name stands where a command starts, so a line that only reads or searches for one is none."""
PYTEST = re.compile(r"^[= ]*((?:\d+ (?:passed|failed|errors?|skipped|deselected|xfailed|xpassed|warnings?|rerun)(?:, )?)+|no tests ran)"
                    r"(?: \([^)]*\))? in [\d.]+s", re.MULTILINE)
CARGO = re.compile(r"test result: \w+\. (\d+) passed; (\d+) failed; (\d+) ignored")
SUMMARY = re.compile(r"^[ \t]*Tests:?[ \t]+((?:\d+ (?:passed|failed|skipped|todo)[ ,|]*)+)(?:\(\d+\)|\d+ total)", re.MULTILINE)
"""The summary of jest and of vitest."""
NODE = re.compile(r"^(?:#|\u2139) (pass|fail|skipped) (\d+)[ \t]*$", re.MULTILINE)
UNITTEST = re.compile(r"^Ran (\d+) tests? in [\d.]+s", re.MULTILINE)
UNITTEST_FAILED = re.compile(r"^FAILED \(([^)]*)\)", re.MULTILINE)
BROKEN = re.compile(r"INTERNALERROR|errors? during collection|ImportError while loading conftest|found no collectors", re.IGNORECASE)
"""What a test runner says when it could not get as far as the tests."""
SETUP = re.compile(r"Operation not permitted|Permission denied|Read-only file system|ModuleNotFoundError|No module named|command not found|"
                   r"fixture '[^']+' not found|Connection refused|Could not resolve host|Network is unreachable|[Aa]ddress already in use|"
                   r"not a git repository|dubious ownership|index\.lock|\bsandbox|\b(?:EPERM|EACCES|EROFS|ECONNREFUSED)\b")
"""What a line says when the box, not the work, is what failed."""
WORDS = {"passed": "passed", "failed": "failed", "error": "errors", "errors": "errors", "skipped": "skipped"}
SAID = 200
LINE, OUTPUT = 4000, 32_000
"""The characters of a command, and the last characters of its output, that are read."""


def _counts(output: str) -> dict[str, int]:
    """The counts of every summary ``output`` holds, summed; empty when it holds none."""
    found: dict[str, int] = {}

    def add(key: str, number: str | int) -> None:
        found[key] = found.get(key, 0) + int(number)

    def started() -> None:
        for key in ("passed", "failed", "errors", "skipped"):
            add(key, 0)

    for match in PYTEST.finditer(output):
        started()
        for number, word in re.findall(r"(\d+) (\w+)", match.group(1)):
            if word in WORDS:
                add(WORDS[word], number)
    for passed, failed, ignored in CARGO.findall(output):
        started()
        add("passed", passed)
        add("failed", failed)
        add("skipped", ignored)
    for match in SUMMARY.finditer(output):
        started()
        for number, word in re.findall(r"(\d+) (\w+)", match.group(1)):
            if word in WORDS:
                add(WORDS[word], number)
    for word, number in NODE.findall(output):
        started()
        add({"pass": "passed", "fail": "failed", "skipped": "skipped"}[word], number)
    if ran := UNITTEST.findall(output):
        started()
        told = {key: int(value) for line in UNITTEST_FAILED.findall(output) for key, value in re.findall(r"(\w+)=(\d+)", line)}
        add("failed", told.get("failures", 0))
        add("errors", told.get("errors", 0))
        add("skipped", told.get("skipped", 0))
        add("passed", max(sum(map(int, ran)) - sum(told.get(key, 0) for key in ("failures", "errors", "skipped")), 0))
    return found


def _line(output: str, pattern: re.Pattern[str]) -> str:
    return next((line.strip()[:SAID] for line in output.splitlines() if pattern.search(line)), "")


def _setup(output: str, status: int | None, counts: dict[str, int]) -> str:
    """The line, or the reason, that makes a failed run look like the box's setup; empty when it looks like the work."""
    if line := _line(output, BROKEN):
        return _line(output, SETUP) or line
    if status in (126, 127):
        return _line(output, SETUP) or f"the command could not be started (exit {status})"
    if status == 124 and not counts:
        return "the command was ended at its timeout before any summary"
    if not counts.get("passed") or counts.get("errors"):
        return _line(output, SETUP)
    return ""


def read(command: str, output: str, status: int | None) -> dict[str, Any] | None:
    """The test run ``command`` was, with ``output`` and exit ``status``: ``passed``, ``failed``, ``errors`` and ``skipped`` (None when its output
    holds no summary), ``exit``, ``ok`` and ``setup`` (why a failure looks like setup, else empty). None for a command that runs no tests."""
    if not COMMAND.search(command[:LINE]):
        return None
    output = output[-OUTPUT:]
    counts = _counts(output)
    ok = status in (0, None) and not counts.get("failed") and not counts.get("errors")
    return {**{key: counts.get(key) for key in ("passed", "failed", "errors", "skipped")}, "exit": status, "ok": ok,
            "setup": "" if ok else _setup(output, status, counts)}
