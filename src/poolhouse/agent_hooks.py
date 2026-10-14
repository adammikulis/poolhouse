"""The message-board nudge hooks written into each coding agent's own settings, and a check of them."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import tomllib
from pathlib import Path

from poolhouse import agent_hooks_stale, home
from poolhouse.checks import Finding

__all__ = ["AGENTS", "codex_hooks", "findings", "install", "install_report", "paths", "remove_stale", "removed_lines"]

AGENTS = ("claude-code", "codex")
IDS = {"claude-code": "claude", "codex": "codex"}
BINARY = "poolhouse-workspace"
OWN = (f"{BINARY} nudge", "claude-nudge.sh", "poolhouse.codex_lifecycle", "poolhouse-workspace codex-hook", "poolhouse-workspace codex-stop")
CLAUDE_EVENTS = (("PostToolUse", "post", "*"), ("Stop", "stop", ""), ("UserPromptSubmit", "prompt", ""))
CODEX_EVENTS = tuple((event, event, ".*" if event not in ("Stop", "UserPromptSubmit") else "")
                     for event in ("SessionStart", "SubagentStart", "SubagentStop", "SessionEnd",
                                   "PostToolUse", "Stop", "UserPromptSubmit"))
FIX = f"{BINARY} install-hooks"
BEGIN = "# >>> poolhouse nudge hooks (managed) >>>"
END = "# <<< poolhouse nudge hooks <<<"
CODEX_OTHER_POST = "poolhouse.harnesshook post"
_MANAGED = re.compile(rf"\n*{re.escape(BEGIN)}.*?{re.escape(END)}\n?", re.S)
_NOTIFY = re.compile(rf'(?m)^notify\s*=\s*\[[^\]\n]*"{re.escape(BINARY)}"[^\]\n]*\]\s*\n')
_FEATURES = re.compile(r"(?m)^\[features\][^\n]*\n")
_TABLE = re.compile(r"(?m)^\[")


def command(agent: str, hook: str) -> str:
    """The command an ``agent``'s ``hook`` event runs."""
    if agent == "codex":
        return "poolhouse-workspace codex-hook"
    return f"{BINARY} nudge --agent {agent} --hook {hook}"


def paths() -> dict[str, Path]:
    """Where each agent keeps the settings the hooks are written to."""
    claude = os.environ.get("CLAUDE_CONFIG_DIR")
    codex = os.environ.get("CODEX_HOME")
    return {"claude-code": (Path(claude) if claude else home.user_home() / ".claude") / "settings.json",
            "codex": (Path(codex) if codex else home.user_home() / ".codex") / "config.toml"}


def present(agent: str, path: Path) -> bool:
    """Whether ``agent`` is on this machine: its settings folder exists or its command is on PATH."""
    return path.parent.is_dir() or shutil.which(agent.split("-")[0]) is not None


def _own(command_line: str) -> bool:
    return any(mark in command_line for mark in OWN)


def install_claude(settings: Path) -> list[str]:
    """Write the PostToolUse, Stop and UserPromptSubmit nudge hooks into Claude Code's ``settings``,
    replacing earlier nudge hooks and keeping every other setting; returns the events written."""
    data = json.loads(settings.read_text()) if settings.exists() else {}
    hooks = data.setdefault("hooks", {})
    for event, hook, matcher in CLAUDE_EVENTS:
        groups = []
        for group in hooks.get(event, []):
            kept = [h for h in group.get("hooks", []) if not _own(h.get("command", ""))]
            if kept:
                groups.append({**group, "hooks": kept})
        groups.append({**({"matcher": matcher} if matcher else {}),
                       "hooks": [{"type": "command", "command": command(IDS["claude-code"], hook)}]})
        hooks[event] = groups
    text = json.dumps(data, indent=2) + "\n"
    if not settings.exists() or settings.read_text() != text:
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(text)
    return [event for event, _, _ in CLAUDE_EVENTS]


def _enable_hooks(text: str) -> tuple[str, bool]:
    """``text`` with the Codex ``hooks`` feature on, and whether a features table is still to be written."""
    head = _FEATURES.search(text)
    if head is None:
        return text, True
    end = _TABLE.search(text, head.end())
    stop = end.start() if end else len(text)
    section = text[head.end():stop]
    line = re.search(r"(?m)^\s*hooks\s*=.*$", section)
    section = (section[:line.start()] + "hooks = true" + section[line.end():]) if line else "hooks = true\n" + section
    return text[:head.end()] + section + text[stop:], False


def codex_hooks(with_post: bool = True, with_features: bool = False, assigned: str = "") -> str:
    out = [BEGIN]
    if with_features:
        out += ["[features]", "hooks = true", ""]
    for event, hook, matcher in CODEX_EVENTS:
        if event == "PostToolUse" and not with_post:
            continue
        out += [f"[[hooks.{event}]]", *([f'matcher = "{matcher}"'] if matcher else []), "",
                f"[[hooks.{event}.hooks]]", 'type = "command"', f'command = "{command("codex", hook)}{" --assigned " + assigned if assigned else ""}"',
                f"timeout = {3 if event == 'SessionEnd' else 5}", "additionalContextLimit = 4000", ""]
        if event == "Stop":
            out += [f"[[hooks.{event}.hooks]]", 'type = "command"',
                    f'command = "poolhouse-workspace codex-stop{" --assigned " + assigned if assigned else ""}"',
                    "timeout = 5", ""]
    return "\n".join([*out[:-1], END]) + "\n"



def _owned_codex_post(line: str) -> bool:
    if any(char in line for char in ';|&<>$`'):
        return False
    try:
        words = shlex.split(line)
    except ValueError:
        return False
    if len(words) < 4 or not re.fullmatch(r'python(?:3(?:\.\d+)?)?', Path(words[0]).name):
        return False
    if words[1:4] != ['-m', *CODEX_OTHER_POST.split()]:
        return False
    tail = words[4:]
    return len(tail) % 2 == 0 and all(key in ('--role', '--agent', '--root', '--protect', '--wait') for key in tail[::2])

def install_codex(config: Path) -> list[str]:
    """Write native lifecycle hooks, replacing Poolhouse notifications and preserving other settings."""
    text = config.read_text() if config.exists() else ""
    base = _NOTIFY.sub("", _MANAGED.sub("\n", text)).rstrip("\n")
    base, with_features = _enable_hooks(base)
    base, _ = agent_hooks_stale._codex(base, _owned_codex_post, "PostToolUse")
    new = (base + "\n\n" if base else "") + codex_hooks(True, with_features)
    tomllib.loads(new)
    if new != text:
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(new)
    return [event for event, _, _ in CODEX_EVENTS]


def install(only: tuple[str, ...] = AGENTS, where: dict[str, Path] | None = None) -> dict[str, list[str]]:
    """Write the nudge hooks for each of ``only`` that is on this machine (or named in ``where``);
    returns the events written by agent."""
    found = {**paths(), **(where or {})}
    writers = {"claude-code": install_claude, "codex": install_codex}
    return {agent: writers[agent](found[agent]) for agent in only
            if agent in (where or {}) or present(agent, found[agent])}


def remove_stale(only: tuple[str, ...] = AGENTS, where: dict[str, Path] | None = None) -> dict[str, list[str]]:
    """Take the hooks that can no longer run (an old-name command, a program that is gone) out of each of
    ``only``'s settings that is on this machine or named in ``where``; returns the commands removed by agent."""
    found = {**paths(), **(where or {})}
    removed = {agent: agent_hooks_stale.remove(agent, found[agent]) for agent in only
               if agent in (where or {}) or present(agent, found[agent])}
    return {agent: commands for agent, commands in removed.items() if commands}


def removed_lines(removed: dict[str, list[str]], where: dict[str, Path] | None = None) -> list[str]:
    """What `remove_stale` removed, as the lines to show."""
    found = {**paths(), **(where or {})}
    return [f"{agent}: removed the hook {command!r} from {found[agent]} (a backup sits beside it)"
            for agent, commands in removed.items() for command in commands]


def install_report() -> list[str]:
    """`install` for every agent on this machine, as the lines to show; a refusal is a line, not an error."""
    try:
        gone = removed_lines(remove_stale())
        done = install()
    except (OSError, ValueError) as err:
        return [f"message-board hooks were not written: {err}"]
    return gone + [f"{agent}: message-board hooks ({', '.join(events)}) are in {paths()[agent]}"
                   for agent, events in done.items()]


def _commands(groups: object) -> list[str]:
    return [h.get("command", "") for g in groups or [] if isinstance(g, dict)
            for h in g.get("hooks", []) if isinstance(h, dict)]


def _problems(agent: str, data: dict, events: tuple[tuple[str, str, str], ...]) -> list[str]:
    hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
    out = []
    for event, hook, _ in events:
        have = [c for c in _commands(hooks.get(event)) if _own(c)]
        if not have:
            out.append(f"{event} missing")
        elif have != ([command(IDS[agent], hook), "poolhouse-workspace codex-stop"]
                      if agent == "codex" and event == "Stop" else [command(IDS[agent], hook)]):
            out.append(f"{event} stale")
    if agent == "codex" and (data.get("features") or {}).get("hooks") is not True:
        out.append("the hooks feature is off")
    return out


def findings(where: dict[str, Path] | None = None) -> list[Finding]:
    """One finding per agent on this machine: whether its nudge hooks are all present and current."""
    found = {**paths(), **(where or {})}
    events = {"claude-code": CLAUDE_EVENTS, "codex": CODEX_EVENTS}
    out = []
    for agent in AGENTS:
        path = found[agent]
        if agent not in (where or {}) and not present(agent, path):
            continue
        try:
            data = (json.loads(path.read_text()) if agent == "claude-code" else tomllib.loads(path.read_text())) \
                if path.exists() else {}
            problems = _problems(agent, data, events[agent])
            problems += [f"{command!r} is an old-name or missing program, so it fails on every event"
                         for command in agent_hooks_stale.found(agent, path)]
        except (OSError, ValueError) as err:
            problems = [f"{path} cannot be read ({err})"]
        if shutil.which(BINARY) is None:
            problems.append(f"{BINARY} is not on PATH")
        out.append(Finding(name=f"{agent}: message-board hooks", good=not problems,
                           said="installed in " + str(path) if not problems else "; ".join(problems),
                           fix=FIX.split() if problems else []))
    return out
