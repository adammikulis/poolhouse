"""What sentinel allows, decided by policy at once, and what an agent's tool call may never touch.

Releasing, purging, inspecting and changing the mode are refused to an agent automatically (`mint` raises `AgentRefused` with the
reason) and run directly for a process with no agent marker; nothing is typed, pressed or waited for. A `Permit` is the proof the store
asks for, so code that skips `mint` cannot release or purge.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from poolhouse import authority, home
from poolhouse.caller import AgentRefused, refuse_agent

__all__ = ["GRANT_TTL_S", "Permit", "agent_may", "automatic", "mint", "mint_gated", "protect"]

GRANT_TTL_S = 120.0
_MINT = object()

_FORBIDDEN = (
    "poolhouse security", "poolhouse-security", "poolhouse.sentinel", "poolhouse/sentinel",
    "poolhouse_sentinel", "sentinel.release", "sentinel.purge", "sentinel/state",
    "authority.json", "authority-audit", "poolhouse.authority",
    "poolhouse-log", "poolhouse.activity", "poolhouse/activity", "poolhouse/activity", "poolhouse_activity", "activity.log",
)
_PROTECTED: set[str] = set()
_STATE_DIR = re.compile(r"poolhouse/+sentinel")
_VERBS = re.compile(r"\b(?:release|purge|unquarantine|disable|mode|baseline)\b")


@dataclass(frozen=True, slots=True)
class Permit:
    """Permission for one ``action`` on one ``subject``, good until ``expires``. Built by `mint` after policy allows it;
    `check` is what the store calls."""

    action: str
    subject: str
    expires: float
    _token: object

    def check(self, action: str, subject: str, *, now: float | None = None) -> None:
        """Raise `AgentRefused` unless this permit covers ``action`` on ``subject`` now."""
        if self._token is not _MINT:
            raise AgentRefused("not a permit minted by policy")
        if self.action != action or self.subject != subject:
            raise AgentRefused(f"this permit is for {self.action} {self.subject}")
        if (time.time() if now is None else now) > self.expires:
            raise AgentRefused("the permit expired")


WHY = "it changes what the sentinel holds or how it screens, and a prompt-injected agent must not be able to switch the screen off"


def mint(action: str, subject: str, *, env: Mapping[str, str] | None = None, why: str = WHY) -> Permit:
    """A permit for ``action`` on ``subject`` for a process with no agent marker; an agent is refused at once with ``why``."""
    refuse_agent(f"{action} {subject}", why, env)
    return Permit(action, subject, time.time() + GRANT_TTL_S, _MINT)


def automatic(action: str, subject: str) -> Permit:
    """A permit for an act the sentinel itself decided on its own rules (a hold that ends by itself); no caller asked for it."""
    return Permit(action, subject, time.time() + GRANT_TTL_S, _MINT)


def mint_gated(gate: str, action: str, subject: str, *, env: Mapping[str, str] | None = None) -> Permit:
    """A permit as `mint`, decided by the authority registry's ``gate``: an agent passes while the gate is delegated."""
    authority.require(gate, f"{action} {subject}", env)
    return Permit(action, subject, time.time() + GRANT_TTL_S, _MINT)


def protect(directory: Path | str) -> None:
    """Add a directory that `agent_may` refuses to let any tool call name."""
    _PROTECTED.add(Path(directory).resolve().as_posix().lower())
    _PROTECTED.add(Path(directory).as_posix().lower())


def _flatten(value: Any) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(value)
    text = text.replace("\\\\ ", " ")  # a shell-escaped space (`poolhouse\ security`) is a space
    text = text.replace("\\\\", "/").replace("\\", "").replace('"', " ").replace("'", " ").replace(",", " ")
    return re.sub(r"\s+", " ", re.sub(r"/+", "/", text)).strip().lower()


def agent_may(tool: str, arguments: Mapping[str, Any] | None = None) -> str:
    """Why an agent's tool call must not run, or an empty string when sentinel has no
    objection. A call naming a sentinel verb, the sentinel code or its state directory is
    refused whatever the tool."""
    name = tool.lower()
    if "sentinel" in name or name.startswith("security_") or name == "security":
        return f"tool {tool} is a sentinel verb"
    flat = _flatten(arguments or {})
    guarded = {_flatten(path) for path in (home.state("sentinel"), *_PROTECTED)}
    if any(d in flat for d in guarded) or _STATE_DIR.search(flat):
        return "the call names sentinel's state directory"
    for needle in _FORBIDDEN:
        if needle in flat:
            return f"the call names {needle}"
    if "sentinel" in flat and _VERBS.search(flat):
        return "the call asks to change sentinel"
    return ""
