"""The lines of a job file: ``#`` comments, ``set NAME=VALUE``, ``${NAME}``, and ``smoke:`` with the ``then:`` it guards.

``python -m poolhouse.bench queue`` and ``poolhouse schedule`` read their files with this, so a file written for one is read the
same way by the other. What a line's words mean is the reader's own.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Iterator
from dataclasses import dataclass

__all__ = ["Line", "LineError", "expand", "read_lines"]

_SET = re.compile(r"^set\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class LineError(ValueError):
    """A job file that cannot be run as written, said with the line it is on, before anything runs."""


@dataclass(frozen=True)
class Line:
    """One command of the file: the line it came from, ``step``, ``smoke`` or ``then``, and its words."""

    number: int
    kind: str
    argv: list[str]


def expand(text: str, variables: dict[str, str], *, line: int) -> str:
    """``${NAME}`` replaced from ``variables``, else from the environment.

    An unset name is refused rather than expanded to nothing: a queue whose `${FX}` went
    missing would otherwise serve the default model for six hours and call it a result.
    """
    missing: list[str] = []

    def one(m: re.Match[str]) -> str:
        name = m.group(1)
        if name in variables:
            return variables[name]
        if name in os.environ:
            return os.environ[name]
        missing.append(name)
        return ""

    said = _VAR.sub(one, text)
    if missing:
        raise LineError(f"line {line}: nothing sets " + ", ".join(f"${{{n}}}" for n in missing))
    return said


def read_lines(text: str) -> Iterator[Line]:
    """The commands of ``text`` in order, expanded and split into words; every ``smoke:`` has its ``then:`` and the other way
    round."""
    variables: dict[str, str] = {}
    smoke = 0
    """The line of a ``smoke:`` still waiting for its ``then:``, 0 when none is."""
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        named = _SET.match(line)
        if named:
            variables[named.group(1)] = expand(named.group(2).strip(), variables, line=number)
            continue
        kind = "step"
        if line.lower().startswith("smoke:"):
            kind, line = "smoke", line[len("smoke:"):].strip()
        elif line.lower().startswith("then:"):
            kind, line = "then", line[len("then:"):].strip()
        line = expand(line, variables, line=number)
        try:
            argv = shlex.split(line, comments=True)
        except ValueError as why:                       # an unbalanced quote
            raise LineError(f"line {number}: {why}") from None
        if not argv:
            continue
        if argv[:3] == ["python", "-m", "poolhouse.bench"]:      # written out in full: allowed, ignored
            argv = argv[3:]
        if not argv:
            raise LineError(f"line {number}: nothing to run")
        if kind == "then" and not smoke:
            raise LineError(f"line {number}: `then:` with no `smoke:` above it")
        if kind == "smoke" and smoke:
            raise LineError(f"line {number}: `smoke:` on line {smoke} has no `then:` under it")
        smoke = number if kind == "smoke" else 0
        yield Line(number, kind, argv)
    if smoke:
        raise LineError(f"line {smoke}: `smoke:` with no `then:` under it")
