"""What the agent may call: the tool lists, the acting tools a role may run under its rules, and the
protected actions an agent is refused at once, each with the command the owner runs outside the agent.
Nothing here waits for anyone: a call is allowed by a rule or refused with the reason.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from poolhouse import authority, home
from poolhouse.command_capture import captured
from poolhouse.guard.untrusted import EXTERNAL
from poolhouse.interventions import Base, Call, Context, Deny, Proceed, Verdict
from poolhouse.net.cli import command as security_command
from poolhouse.sentinel.permit import agent_may

__all__ = ["ACTING", "APPROVE_FIRST", "FENCED", "PLAN_AND_GO", "PROTECTED", "READ", "READ_ONLY",
           "ROLE_NAMES", "ProtectedRail", "catalog", "refusal_for", "refused", "review_view"]

READ_ONLY = "read-only"
APPROVE_FIRST = "approve-first"
PLAN_AND_GO = "plan-and-go"
ROLE_NAMES = (READ_ONLY, APPROVE_FIRST, PLAN_AND_GO)
_ROLE_WORDS = "|".join(re.escape(name) for name in ROLE_NAMES)


READ = frozenset({
    "serve_status", "models_find", "models_files", "bench_status", "bench_history", "bench_show",
    "fleet_members", "setup_look", "doctor", "models_on_disk", "ollama_models", "jobs_status",
    "jobs_wait", "review_view"})
"""Tools that only look. They run in every role."""

ACTING: dict[str, str] = {
    "serve_up": "start a model server (takes GPU and memory)",
    "serve_down": "stop a running model server",
    "serve_escalate": "give a running server more slots (takes more memory)",
    "models_fetch": "start a download into the model cache",
    "bench_run": "start a benchmark (a long job on the GPU)",
    "bench_standard": "start the standard benchmark sets (a long job on the GPU)",
    "bench_speed": "start a speed measurement (a long job on the GPU)",
    "bench_compare": "write a comparison file",
    "bench_animate": "render a video and write a file",
}
"""Tools that change something or cost something, with what each does. A role runs one only under its
rules (a saved always rule, or the plan it approved); any other call is refused at once with the reason."""

FENCED = EXTERNAL | READ | frozenset(ACTING)
"""Tools whose results carry text nobody here wrote (names, logs, model cards, reasons)."""

PROTECTED: tuple[tuple[str, re.Pattern[str], str], ...] = tuple(
    (what, re.compile(pattern, re.I | re.S), command) for what, pattern, command in (
        ("release or purge something held in quarantine",
         r"\bquarantin\w*\b.{0,60}\b(?:releas\w*|purg\w*|clear\w*|remov\w*|free\w*|delet\w*|restor\w*)"
         r"|\b(?:releas\w*|purg\w*|unquarantin\w*|unblock\w*|free)\b.{0,60}\b(?:quarantin\w*|held)\b"
         r"|^(?:unquarantine|release|purge)\b",
         "poolhouse-security review"),
        ("approve a host for downloads",
         r"\b(?:approv\w*|allow\w*|trust\w*|whitelist\w*|add)\b.{0,40}\b(?:hosts?|domains?)\b"
         r"|\bapprove[-_ ]?host\b",
         "poolhouse-security approve-host HOST"),
        ("mint a grant",
         r"\b(?:mint\w*|issu\w*|creat\w*|giv\w*|forg\w*|fak\w*)\b.{0,30}\bgrants?\b"
         r"|\bhuman[-_ ]?grants?\b",
         "poolhouse-security review"),
        ("change the sentinel or guard policy",
         r"\b(?:sentinel|guard|rails?|scan[-_ ]?policy|enforce|observe)\b.{0,40}"
         r"\b(?:off|disabl\w*|polic\w*|mode|weaken\w*|relax\w*|loosen\w*|turn\w*|switch\w*|set)\b"
         r"|\b(?:disabl\w*|turn\w*|switch\w*|weaken\w*|relax\w*|loosen\w*|set)\b.{0,30}"
         r"\b(?:sentinel|guard|rails?|scan[-_ ]?policy)\b",
         "poolhouse-security mode, or poolhouse-security scan-policy"),
        ("plant or remove baselines and decoys",
         r"\b(?:baseline|honey\w*|decoys?)\b.{0,30}\b(?:plant\w*|remov\w*|reset\w*|clear\w*)\b",
         "poolhouse-security baseline"),
        ("change the role this session runs under",
         rf"\b(?:roles?|permissions?|privileges?)\b.{{0,40}}\b(?:{_ROLE_WORDS}|raise\w*|elevat\w*"
         r"|upgrad\w*|admin|escalat\w*|grant\w*|chang\w*|switch\w*|set)\b"
         rf"|\b(?:{_ROLE_WORDS})\b.{{0,20}}\b(?:roles?|mode)\b",
         "/role NAME"),
        ("answer, approve, deny or cancel a request in the Requests inbox",
         r"\b(?:answer\w*|approv\w*|den(?:y|ies)|cancel\w*|resolv\w*|dismiss\w*|withdraw\w*)\b.{0,40}"
         r"\b(?:requests?|inbox|confirmations?)\b|\bpoolhouse-requests\b"
         r"|\b(?:requests?|inbox)\b.{0,30}\b(?:answer\w*|approv\w*|cancel\w*)\b",
         "poolhouse-requests answer ID CHOICE"),
    ))
"""Actions an agent is always refused: what it is, how it is spelled, the command the owner runs outside the agent."""

_TOOL_NAME = re.compile(
    r"quarantin|releas|purg|approv|grant|mint|sentinel|security|baseline|honey|polic|guard|rail|"
    r"unblock|role|permission|privilege|rule|always|classif|request|answer", re.I)


def catalog() -> dict[str, str]:
    """The destructive-action label of each tool the agent has built in: reads are ``safe``, every
    acting tool is ``reversible``."""
    return {**dict.fromkeys(ACTING, "reversible"), **dict.fromkeys(READ, "safe")}


def refusal_for(text: str) -> tuple[str, str] | None:
    """``(what, command)`` when ``text`` asks for a protected action; none while the
    ``chat.policy`` gate is delegated."""
    if authority.state_of("chat.policy") == authority.DELEGATED:
        return None
    for what, pattern, command in PROTECTED:
        if pattern.search(text):
            return what, command
    return None


def _words(value: Any) -> str:
    try:
        return json.dumps(value, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _command_for(name: str) -> tuple[str, str]:
    low = name.lower()
    if re.search(r"quarantin|releas|purg|unblock", low):
        return PROTECTED[0][0], PROTECTED[0][2]
    if re.search(r"request|answer", low):
        return PROTECTED[6][0], PROTECTED[6][2]
    if re.search(r"approv|host", low):
        return PROTECTED[1][0], PROTECTED[1][2]
    if re.search(r"grant|mint", low):
        return PROTECTED[2][0], PROTECTED[2][2]
    if re.search(r"baseline|honey", low):
        return PROTECTED[4][0], PROTECTED[4][2]
    if re.search(r"role|permission|privilege|rule|always", low):
        return PROTECTED[5][0], PROTECTED[5][2]
    return PROTECTED[3][0], PROTECTED[3][2]


def refused(what: str, command: str) -> str:
    """The sentence given back when a protected action is asked for: refused at once, with the
    command the owner runs and where (the chat prompt for a slash command, else a terminal
    outside the agent)."""
    where = "at the chat prompt" if command.startswith("/") else "from a terminal outside the agent"
    return (f"Refused to an agent: {what}. No argument changes that. "
            f"The owner runs `{command}` {where}.")


class ProtectedRail(Base):
    """Refuses a call to a tool named for a protected action, and any call whose arguments
    name sentinel's code or state, at once, with the command the owner runs."""

    name = "protected"

    def before_tool_call(self, call: Call, context: Context) -> Verdict:
        if _TOOL_NAME.search(call.name) and call.name not in READ | frozenset(ACTING):
            what, command = _command_for(call.name)
            return Deny(refused(what, command), self.name)
        if "agent-rules" in _words(call.arguments):
            return Deny(refused("change the always and never rules", "/rules"), self.name)
        why = agent_may(call.name, call.arguments)
        if why:
            return Deny(refused("change what sentinel holds or decides", "poolhouse-security review")
                        + f" ({why})", self.name)
        return Proceed()


def _outside_state(value: Any) -> list[str]:
    """Strings in ``value`` that name a path outside poolhouse's state."""
    root = str(home.home().resolve())
    found: list[str] = []
    if isinstance(value, str):
        text = value.strip()
        if (text.startswith(("/", "~")) or ".." in Path(text).parts) \
                and not str(Path(text).expanduser().resolve()).startswith(root):
            found.append(text)
    elif isinstance(value, Mapping):
        for item in value.values():
            found += _outside_state(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            found += _outside_state(item)
    return found


def review_view(what: str = "status", limit: int = 20) -> dict[str, Any]:
    """Read-only security views: ``what`` is ``status`` (mode, counts, what is held),
    ``held`` (everything in quarantine), ``events``, ``hosts``, ``downloads`` or
    ``scanners``. Acting on what is held is the owner's, from a terminal outside the agent."""
    views = {"status": ["status", "--json"], "held": ["review", "--list", "--json"],
             "events": ["events", "--json"], "hosts": ["hosts", "--json"],
             "downloads": ["downloads", "--json", "--limit", str(max(1, min(int(limit), 100)))],
             "scanners": ["scanners", "--json"]}
    if what not in views:
        return {"error": f"what is one of {', '.join(views)}"}
    return captured(lambda: security_command(views[what]))
