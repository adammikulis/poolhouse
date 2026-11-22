"""Who is calling: an agent (a coding harness put a marker in the environment) or not, and the audit trail of what ran.

The answer is an input to automatic policy and nothing else. Nothing in Poolhouse waits for a person, prompts, or needs a terminal:
an action is allowed by a rule or refused at once with the reason (`refuse_agent` raises `AgentRefused`), and either way the audit
log (`authority-audit.jsonl` in the state root) says what ran and who ran it. A process with no marker (a terminal of its own) runs
every action directly. A person typing `! cmd` inside Claude Code inherits the marker and cannot be told from the agent, so the
agent rules apply to that command too and the refusal says to run it from a terminal outside the agent.
"""

from __future__ import annotations

import ctypes
import getpass
import importlib
import json
import os
import sys
import time
from collections.abc import Mapping
from typing import Any

from poolhouse import home

__all__ = ["AGENT_MARKERS", "AgentRefused", "audit_rows", "is_terminal", "marked", "record", "refuse_agent", "who"]

_MSVCRT = importlib.import_module("msvcrt") if sys.platform == "win32" else None

AGENT_MARKERS = ("CLAUDECODE", "POOLHOUSE_AGENT", "POOLHOUSE_NONINTERACTIVE", "CODEX_THREAD_ID",
                 "CLAUDE_CODE_SESSION_ID", "POOLHOUSE_SESSION_ID")
"""Environment variables whose presence means an agent started this process."""
SESSION_VARS = ("POOLHOUSE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "POOLHOUSE_WORKSPACE_AGENT")
"""Where the session an agent runs in names itself, first found wins."""
OUTSIDE = "run it from a terminal outside the agent (no CLAUDECODE or POOLHOUSE_AGENT in its environment)"


class AgentRefused(PermissionError):
    """An action that an agent may not take, refused at once by policy; the message says why and what to do instead."""


def marked(env: Mapping[str, str] | None = None) -> str:
    """The first agent marker in the process or supplied context; supplied data cannot hide the process."""
    found = os.environ if env is None else env
    return next((name for name in AGENT_MARKERS if found.get(name) or os.environ.get(name)), "")


def who(env: Mapping[str, str] | None = None) -> str:
    """The process or session that is running: the agent's session when it names one, else the marker, else the user, with the pid."""
    found = os.environ if env is None else env
    marker = marked(env)
    session = next((found[v] for v in SESSION_VARS if found.get(v)), "")
    kind = f"agent {marker}" + (f" session {session}" if session else "") if marker else f"user {getpass.getuser()}"
    return f"{kind} pid {os.getpid()}"


def is_terminal(stream) -> bool:
    """Whether the stream is attached to an interactive terminal; it labels audit rows and gates nothing."""
    if not stream.isatty():
        return False
    if sys.platform != "win32":
        return True
    try:
        descriptor = stream.fileno()
        handle = _MSVCRT.get_osfhandle(descriptor)
        mode = ctypes.c_ulong()
        return bool(ctypes.windll.kernel32.GetConsoleMode(ctypes.c_void_p(handle), ctypes.byref(mode)))
    except (AttributeError, OSError, ValueError):
        return False


def _log(entry: Mapping[str, Any]) -> None:
    path = home.state("authority-audit.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": time.time(), **entry}) + "\n")


def record(event: str, **fields: Any) -> None:
    """Append ``event`` to the audit log with who ran it and ``fields``; nothing that runs is silent."""
    _log({"event": event, "who": who(), **fields})


def audit_rows() -> list[dict[str, Any]]:
    """Every recorded event, oldest first."""
    path = home.state("authority-audit.jsonl")
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line] if path.exists() else []


def refuse_agent(action: str, why: str, env: Mapping[str, str] | None = None) -> None:
    """Raise `AgentRefused` at once, with ``why``, when an agent runs ``action``; the refusal is audited. Otherwise record that it ran."""
    found = marked(env)
    if found:
        _log({"event": "refused", "who": who(env), "action": action, "why": why})
        raise AgentRefused(f"{action} is refused to an agent ({found} is set): {why}. To do it, {OUTSIDE}.")
    record("ran", action=action)
