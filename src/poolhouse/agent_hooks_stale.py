"""Hook entries in a coding agent's settings that can never run again: a command of the old project name
(``ml-stack-*``, ``ml_stack``, ``poolside-*``) or one whose program is a path that no longer exists.

An agent runs every hook it finds and shows each failure to the person, so a stale entry is a defect
until it is removed. `remove` takes them out of Claude Code's JSON and Codex's TOML, keeps a backup of
the file it changed and writes the new one atomically.
"""

from __future__ import annotations

import itertools
import json
import re
import shlex
import time
import tomllib
from collections.abc import Callable
from pathlib import Path

from poolhouse import home
from poolhouse.files import write_text

__all__ = ["OLD_PREFIXES", "found", "is_stale", "remove"]

OLD_PREFIXES = ("ml-stack", "ml_stack", "poolside-", "poolside_")
_HEADER = re.compile(r"(?m)^\[\[?([^\]\n]+)\]\]?[ \t]*(?:#.*)?$")
_COMMAND = re.compile(r'(?m)^\s*command\s*=\s*"((?:[^"\\]|\\.)*)"')
_NOTIFY = re.compile(r"(?m)^notify\s*=\s*\[[^\]\n]*\]\s*\n")


def is_stale(command_line: str) -> bool:
    """Whether running ``command_line`` can only fail: its program or ``-m`` module carries an old name,
    or its program is an absolute path that is not there."""
    try:
        words = shlex.split(command_line)
    except ValueError:
        words = command_line.split()
    if not words:
        return False
    program = words[0]
    if Path(program).name.startswith(OLD_PREFIXES):
        return True
    if program.startswith(("/", "~")) and not home.expand(program).exists():
        return True
    return any(word == "-m" and nxt.startswith(OLD_PREFIXES) for word, nxt in itertools.pairwise(words))


def _claude(text: str) -> tuple[str, list[str]]:
    data = json.loads(text)
    hooks = data.get("hooks")
    removed: list[str] = []
    if not isinstance(hooks, dict):
        return text, removed
    for event in list(hooks):
        groups = []
        for group in hooks[event] if isinstance(hooks[event], list) else []:
            inner = group.get("hooks", []) if isinstance(group, dict) else []
            kept = [h for h in inner if not (isinstance(h, dict) and is_stale(h.get("command", "")))]
            removed += [h["command"] for h in inner if h not in kept]
            if kept:
                groups.append({**group, "hooks": kept})
        if groups:
            hooks[event] = groups
        else:
            del hooks[event]
    return (json.dumps(data, indent=2) + "\n") if removed else text, removed


def _codex(text: str, predicate: Callable[[str], bool] = is_stale, event: str = "") -> tuple[str, list[str]]:
    """Drop each ``[[hooks.EVENT.hooks]]`` table with a stale command, then each ``[[hooks.EVENT]]``
    table left with none, and a ``notify`` line that names a stale command."""
    marks = list(_HEADER.finditer(text))
    spans = [(m.group(1).strip(), m.start(), marks[i + 1].start() if i + 1 < len(marks) else len(text))
             for i, m in enumerate(marks)]
    drop: set[int] = set()
    removed: list[str] = []
    for i, (name, start, end) in enumerate(spans):
        if name.startswith("hooks.") and name.endswith(".hooks") and (not event or name == f"hooks.{event}.hooks"):
            command = _COMMAND.search(text[start:end])
            value = tomllib.loads('command = "' + command.group(1) + '"')["command"] if command else ""
            if command and predicate(value):
                drop.add(i)
                removed.append(command.group(1))
    for i, (name, _start, _end) in enumerate(spans):
        if name.startswith("hooks.") and name.count(".") == 1:
            kids = []
            for j in range(i + 1, len(spans)):
                if spans[j][0] != name + ".hooks":
                    break
                kids.append(j)
            if kids and all(k in drop for k in kids):
                drop.add(i)
    out = (text[:spans[0][1]] if spans else text) + "".join(
        text[s:e] for k, (_n, s, e) in enumerate(spans) if k not in drop)
    for match in _NOTIFY.finditer(out) if not event else ():
        argv = re.findall(r'"([^"]*)"', match.group(0))
        if argv and predicate(" ".join(shlex.quote(a) for a in argv)):
            removed.append(match.group(0).strip())
            out = out.replace(match.group(0), "", 1)
    if removed:
        tomllib.loads(out)
    return out, removed


def found(agent: str, path: Path) -> list[str]:
    """The stale commands in ``path``, an ``agent``'s settings file; none when it is absent."""
    if not path.exists():
        return []
    return (_claude if agent == "claude-code" else _codex)(path.read_text())[1]


def remove(agent: str, path: Path) -> list[str]:
    """Take every stale hook out of ``path``; returns their commands. A changed file is backed up beside
    itself first and written atomically."""
    if not path.exists():
        return []
    text = path.read_text()
    new, removed = (_claude if agent == "claude-code" else _codex)(text)
    if removed:
        write_text(path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%dT%H%M%S')}"), text)
        write_text(path, new)
    return removed
