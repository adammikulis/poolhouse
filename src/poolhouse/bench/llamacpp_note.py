"""Which llama.cpp build the bench measures on: a line before a queue or sweep, and a note beside kept runs."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from poolhouse import net
from poolhouse.log import say, warn
from poolhouse.serve import llamacpp_ensure

__all__ = ["announce", "build_notes", "build_of", "standing_lines"]

STALE_HOURS = 24.0
REUSE_S = 6 * 3600.0
_BUILD = re.compile(r"\bb(\d{3,})\b|\bbuild\s+(\d+)")


def build_of(run: Mapping[str, Any]) -> int | None:
    """The llama.cpp build number a kept run was served by, or None when it recorded none."""
    server = run.get("server") or {}
    said = server.get("served_by") if isinstance(server.get("served_by"), Mapping) else {}
    found = _BUILD.search(str(said.get("version") or ""))
    return int(found.group(1) or found.group(2)) if found else None


def build_notes(kept: Sequence[Mapping[str, Any]]) -> list[str]:
    """The llama.cpp build the runs used, and a line saying so when they used different ones."""
    users: dict[int, list[str]] = {}
    for run in kept:
        if (number := build_of(run)) is not None:
            users.setdefault(number, []).append(str(run.get("label") or "?"))
    if len(users) < 2:
        return [f"llama.cpp build {number}" for number in users]
    named = ", ".join(f"{number} ({', '.join(dict.fromkeys(labels))})" for number, labels in sorted(users.items()))
    return [f"llama.cpp builds {named}",
            "these runs used different llama.cpp builds: not directly comparable"]


def _age(hours: float) -> str:
    return f"{hours:.0f} hours old" if hours < 48 else f"{hours / 24:.0f} days old"


def standing_lines(now: llamacpp_ensure.Standing) -> list[str]:
    """The line before a queue or sweep, and a warning line when the build is over a day old and behind."""
    if now.build is None:
        return ["llama.cpp: no managed build found"]
    aged = f" ({_age(now.age_hours)})" if now.age_hours is not None else ""
    if now.stale:
        gap = f"{now.behind} behind master" if now.behind else "behind master"
        out = [f"llama.cpp: build {now.build}, {gap}{aged}"]
        if now.age_hours is not None and now.age_hours > STALE_HOURS:
            out.append(f"warning: llama.cpp build {now.build} is {_age(now.age_hours)} and master has moved on; "
                       "`poolhouse-serve llama-cpp ensure` updates it when the machine is idle")
        return out
    if now.newer == "no":
        return [f"llama.cpp: build {now.build}, current with master{aged}"]
    return [f"llama.cpp: build {now.build}, upstream not checked{aged}"]


def announce(env: llamacpp_ensure.Env | None = None, *, tell: Callable[[str], None] = say,
             warning: Callable[[str], None] = warn) -> None:
    """Print where the active build stands against master. Never updates, never stops a run."""
    try:
        lines = standing_lines(llamacpp_ensure.standing(env, reuse_s=REUSE_S))
    except (OSError, ValueError, LookupError, RuntimeError, net.NeedsApproval):
        lines = ["llama.cpp: upstream not checked"]
    for line in lines:
        (warning if line.startswith("warning:") else tell)(line)
