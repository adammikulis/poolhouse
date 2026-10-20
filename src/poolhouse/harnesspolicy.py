"""What a coding harness's tool call is allowed to do under explicit session permissions.

`decide` takes the immutable coding policy and tool call and returns allow, deny or ask. The
destructive-action classifier labels the call; the permission profile decides what each label gets. A call
the classifier cannot read is ``unsure`` and asks. Text naming the launcher's session files, the
state root or an action only a person can take is denied in every profile.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from poolhouse import coding_policy, worktreerules
from poolhouse.chatpolicy import refusal_for
from poolhouse.guard import fetchlog
from poolhouse.guard.destructive import classify, reason_text
from poolhouse.guard.shellscan import segments
from poolhouse.interventions import Call

__all__ = ["CATALOG", "SHELL_TOOLS", "Decision", "decide", "workspace_authority"]

CATALOG: dict[str, str] = {
    **dict.fromkeys(("Read", "Glob", "Grep", "LS", "NotebookRead", "TodoWrite", "TaskList", "TaskGet",
                     "ToolSearch", "BashOutput", "read_file", "list_dir", "grep_files", "update_plan"), "safe"),
    **dict.fromkeys(("mcp__workspace__workspace_inbox", "mcp__workspace__workspace_thread",
                     "mcp__workspace__workspace_who_owns", "mcp__workspace__workspace_status",
                     "mcp__workspace__workspace_tasks", "mcp__workspace__workspace_task",
                     "mcp__workspace__workspace_reputation"), "safe"),
    **dict.fromkeys(("mcp__workspace__workspace_task_claim", "mcp__workspace__workspace_task_heartbeat",
                     "mcp__workspace__workspace_task_checkpoint", "mcp__workspace__workspace_task_submit",
                     "mcp__workspace__workspace_task_review", "mcp__workspace__workspace_task_credit",
                     "mcp__workspace__workspace_send", "mcp__workspace__workspace_claim",
                     "mcp__workspace__workspace_announce", "mcp__workspace__workspace_ack"), "reversible"),
    **dict.fromkeys(("Edit", "Write", "MultiEdit", "NotebookEdit", "apply_patch", "WebFetch", "WebSearch"), "reversible"),
}
"""Labels for the harness tools whose effect is known from the name alone."""

SHELL_TOOLS = frozenset({"Bash", "shell", "local_shell", "exec_command", "container.exec", "shell_command"})
"""Tools whose ``command`` argument is a shell line."""


@dataclass(frozen=True, slots=True)
class Decision:
    """``action`` is allow, deny or ask; ``kind`` is the request kind an ask raises."""

    action: str
    label: str
    reason: str
    kind: str = "tool_call"


def _denied(label: str, reason: str) -> Decision:
    return Decision("deny", label, reason)


def _mentions(args: dict[str, Any] | None, words: Sequence[str]) -> str:
    text = json.dumps(args, ensure_ascii=False, default=str) if args is not None else ""
    return next((w for w in words if w and w in text), "")


def _shell_line(name: str, args: dict[str, Any] | None) -> str:
    if name not in SHELL_TOOLS or not args:
        return ""
    line = args.get("command", args.get("cmd", ""))
    return " ".join(map(str, line)) if isinstance(line, list) else str(line)


def _primary_refusal(name: str, args: dict[str, Any] | None, cwd: str) -> str:
    """Why a write or git command in the primary checkout is refused, empty when it is not one."""
    if not args or not cwd:
        return ""
    if name in SHELL_TOOLS:
        return worktreerules.bash_refusal(_shell_line(name, args), cwd)
    if CATALOG.get(name) != "reversible":
        return ""
    paths = [str(args.get(key) or "") for key in ("file_path", "notebook_path", "path")]
    for value in args.values():
        if isinstance(value, str) and "*** Begin Patch" in value:
            paths += worktreerules.patch_paths(value)
    return worktreerules.edit_refusal(paths, cwd)


def primary_decision(name: str, args: dict[str, Any] | None, cwd: str) -> Decision | None:
    """A deny for a write or git command that changes the primary checkout, else None."""
    why = _primary_refusal(name, args, cwd)
    return _denied("destructive", why) if why else None


def _unbrokered_tests(line):
    commands, _ = segments(line)
    for command in commands:
        words = command.argv
        if not words:
            continue
        names = [Path(word).name for word in words]
        if names[0] in ("pytest", "py.test", "tox", "nox"):
            return True
        if re.fullmatch(r"python(?:[0-9.]+)?", names[0]) and "-m" in words:
            at = words.index("-m")
            if at + 1 < len(words) and words[at + 1] in ("pytest", "unittest", "tox", "nox"):
                return True
        if (names[0] in ("uv", "pipenv", "poetry") and "run" in words
                and any(name in ("pytest", "py.test", "tox", "nox") for name in names[words.index("run") + 1:])):
            return True
    return False


def decide(policy: coding_policy.Policy, call: Call, *, roots: Sequence[str] = (),
           protected: Sequence[str] = ()) -> Decision:
    """The decision for one tool call. ``protected`` are path strings no call may name."""
    name, args = call.name, call.arguments
    if hit := _mentions(args, protected):
        return _denied("destructive", f"the call names {hit}, which belongs to the launcher")
    if _unbrokered_tests(_shell_line(name, args)):
        return _denied("reversible", "tests must use the maintained scripts/test broker queue")
    verdict = classify(call, roots=roots, catalog=CATALOG)
    why = reason_text(verdict)
    if verdict.forbidden:
        return _denied(verdict.label, f"{why}; an agent never opens one")
    if verdict.label == "safe":
        return Decision("allow", "safe", why)
    if refusal := refusal_for(_shell_line(name, args)):
        return _denied("destructive", f"{refusal[0]} is for a person; run `{refusal[1]}` yourself")
    if not policy.writable:
        return _denied(verdict.label, f"{policy.execution_mode}/{policy.permissions} runs no acting call: {why}")
    if verdict.label == "unsure" or (verdict.label == "destructive" and policy.permissions != coding_policy.YOLO):
        return Decision("ask", verdict.label, why, "tool_call_destructive")
    if policy.permissions == coding_policy.ASK_CHANGES:
        return Decision("ask", verdict.label, why)
    return Decision("allow", verdict.label, why)


def after_downloads(decision: Decision, name: str, args: dict[str, Any] | None, roots: Sequence[str], fetched: Sequence[str]) -> Decision:
    """``decision``, turned into a question when the call runs a file this session downloaded."""
    line = _shell_line(name, args)
    used = fetchlog.uses(line, roots, [*fetched, *fetchlog.targets(line, roots, "", fetched)]) if line else ""
    if not used or decision.action == "deny":
        return decision
    why = f"runs {used}, which this session downloaded"
    label = decision.label if decision.label in ("unsure", "destructive") else "unsure"
    return Decision("ask", label, f"{decision.reason}; {why}" if decision.action == "ask" else why, "tool_call_destructive")


def workspace_authority(line: str, actor: str) -> Decision | None:
    """A model's workspace CLI call must use its launcher-bound session, never another token."""
    commands, _ = segments(line)
    for command in commands:
        words = command.argv
        start = next((at for at, word in enumerate(words) if Path(word).name == "poolhouse-workspace"), None)
        if start is None:
            continue
        words = words[start:]
        found = []
        for at, word in enumerate(words):
            if word == "--token-file" or word.startswith("--token-file="):
                return _denied("destructive", "the workspace token belongs to the launcher")
            if word == "--agent":
                found.append(words[at + 1] if at + 1 < len(words) else "")
            elif word.startswith("--agent="):
                found.append(word.split("=", 1)[1])
        if not found or any(value != actor for value in found):
            return _denied("destructive", "use the workspace identity assigned by the launcher")
    return None
