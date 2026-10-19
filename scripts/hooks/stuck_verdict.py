"""The first line of `scripts/status` for the lead's attention hook: cached 5 minutes, ended after 3 seconds, empty unless stuck.

$POOLHOUSE_STATUS_COMMAND (a JSON list) replaces the command; without it the line is read only on the person's machine.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import workspace_hook

ROOT = Path(__file__).resolve().parents[2]
CACHE_S = 300.0
TIMEOUT_S = 3.0
STUCK = "STUCK:"


def default_command() -> list[str]:
    """`scripts/status --verdict` of this checkout."""
    return [sys.executable, str(ROOT / "scripts" / "status"), "--verdict"]


def configured() -> list[str]:
    """The command to run: $POOLHOUSE_STATUS_COMMAND, else the default on the person's machine, else none."""
    named = os.environ.get("POOLHOUSE_STATUS_COMMAND")
    if named:
        try:
            value = json.loads(named)
        except ValueError:
            return []
        return [str(w) for w in value] if isinstance(value, list) else []
    return default_command() if workspace_hook.lan.on_the_persons_machine() else []


def ask(command: list[str], timeout: float) -> str:
    """The first line ``command`` prints when it starts with STUCK; empty when it is ok, fails or does not answer in time."""
    try:
        done = subprocess.run(command, capture_output=True, text=True, errors="replace", check=False, timeout=timeout,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    first = (done.stdout.splitlines() or [""])[0].strip() if done.returncode == 0 else ""
    return first if first.startswith(STUCK) else ""


def check(state: Path, command: list[str] | None = None, now: float | None = None, timeout: float = TIMEOUT_S) -> str:
    """The stuck line, or an empty string; the answer is kept in ``state`` for ``CACHE_S`` seconds, a failed one too."""
    command = configured() if command is None else command
    if not command:
        return ""
    now = time.time() if now is None else now
    stamp = state / "stuck-verdict.json"
    try:
        kept = json.loads(stamp.read_text(encoding="utf-8"))
        if now - float(kept["at"]) < CACHE_S:
            return str(kept["line"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    line = ask(command, timeout)
    try:
        state.mkdir(parents=True, exist_ok=True)
        stamp.write_text(json.dumps({"at": now, "line": line}), encoding="utf-8")
    except OSError as error:
        workspace_hook.warning("Attention", error)
    return line
