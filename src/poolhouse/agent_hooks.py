"""The message-board nudge hooks of one project, written only when asked and only into that project's own settings.

Poolhouse never writes a user's global settings: not ``~/.claude``, not ``~/.codex`` and not the folders ``CLAUDE_CONFIG_DIR`` and
``CODEX_HOME`` name. `install` takes a project folder and writes ``.claude/settings.local.json`` and ``.codex/config.toml`` inside
it. Only `poolhouse-workspace install-hooks --project DIR` calls it, and nothing checks that the hooks are there.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import tomllib
from pathlib import Path

from poolhouse import home
from poolhouse.files import write_text

__all__ = ["AGENTS", "codex_hooks", "install", "targets"]

AGENTS = ("claude-code", "codex")
FILES = {"claude-code": (".claude", "settings.local.json"), "codex": (".codex", "config.toml")}
BINARY = "poolhouse-workspace"
CLAUDE_EVENTS = (("PostToolUse", "post", "*"), ("Stop", "stop", ""), ("UserPromptSubmit", "prompt", ""))
CODEX_EVENTS = tuple((event, event, ".*" if event not in ("Stop", "UserPromptSubmit") else "")
                     for event in ("SessionStart", "SubagentStart", "SubagentStop", "SessionEnd",
                                   "PostToolUse", "Stop", "UserPromptSubmit"))
BEGIN = "# >>> poolhouse nudge hooks (managed) >>>"
END = "# <<< poolhouse nudge hooks <<<"
_MANAGED = re.compile(rf"\n*{re.escape(BEGIN)}.*?{re.escape(END)}\n?", re.S)
_FEATURES = re.compile(r"(?m)^\[features\][^\n]*\n")
_TABLE = re.compile(r"(?m)^\[")


def command(agent: str, hook: str) -> str:
    """The command an ``agent``'s ``hook`` event runs."""
    if agent == "codex":
        return f"{BINARY} codex-hook"
    return f"{BINARY} nudge --agent {agent} --hook {hook}"


def _same(one: Path, other: Path) -> bool:
    """Whether two paths are one folder, however each is spelled."""
    return one == other or (one.exists() and other.exists() and one.samefile(other))


def _user_folders() -> list[Path]:
    """The folders that hold the user's own settings of each agent."""
    user = home.user_home()
    named = [home.expand(value) for key in ("CLAUDE_CONFIG_DIR", "CODEX_HOME") if (value := os.environ.get(key))]
    return [Path(os.path.realpath(folder)) for folder in (user / ".claude", user / ".codex", *named)]


def targets(project: Path, only: tuple[str, ...] = AGENTS) -> dict[str, Path]:
    """The settings file of each of ``only`` inside ``project``; ValueError unless every one is the project's own."""
    root = Path(os.path.realpath(project))
    if not root.is_dir():
        raise ValueError(f"{project} is not a folder")
    user = Path(os.path.realpath(home.user_home()))
    if any(_same(root, folder) for folder in (user, *user.parents)):
        raise ValueError(f"{project} is the home folder or holds it, not a project: the user's own settings are never written")
    folders = _user_folders()
    out = {}
    for agent in only:
        path = Path(os.path.realpath(root.joinpath(*FILES[agent])))
        if not path.is_relative_to(root):
            raise ValueError(f"{root.joinpath(*FILES[agent])} is a link that leaves the project, so it is not written")
        if any(_same(parent, folder) for parent in path.parents for folder in folders):
            raise ValueError(f"{path} is among the user's own settings, which are never written")
        out[agent] = path
    return out


def _own(command_line: str) -> bool:
    """Recognize only standalone commands this installer actually generated."""
    if not isinstance(command_line, str) or any(char in command_line for char in ';|&<>$`\r\n()#'):
        return False
    try:
        words = shlex.split(command_line)
    except ValueError:
        return False
    if len(words) < 2 or words[0] != BINARY:
        return False
    options = {"nudge": {"--agent", "--hook"}, "codex-hook": {"--assigned"}}
    allowed = options.get(words[1])
    tail = words[2:]
    return (allowed is not None and (words[1] != "nudge" or bool(tail))
            and len(tail) % 2 == 0 and len(set(tail[::2])) == len(tail[::2])
            and all(key in allowed and (value in {"post", "stop", "prompt"} if key == "--hook"
                                        else re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", value))
                    for key, value in zip(tail[::2], tail[1::2], strict=True)))


def _claude_text(text: str) -> str:
    """Claude Code settings ``text`` with the nudge hooks, earlier nudge hooks replaced and every other setting kept."""
    data = json.loads(text) if text else {}
    hooks = data.setdefault("hooks", {})
    for event, hook, matcher in CLAUDE_EVENTS:
        groups = []
        for group in hooks.get(event, []):
            kept = [h for h in group.get("hooks", []) if not _own(h.get("command", ""))]
            if kept:
                groups.append({**group, "hooks": kept})
        groups.append({**({"matcher": matcher} if matcher else {}),
                       "hooks": [{"type": "command", "command": command("claude", hook)}]})
        hooks[event] = groups
    return json.dumps(data, indent=2) + "\n"


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
    """The managed block of Codex lifecycle hooks as TOML."""
    out = [BEGIN]
    if with_features:
        out += ["[features]", "hooks = true", ""]
    for event, hook, matcher in CODEX_EVENTS:
        if event == "PostToolUse" and not with_post:
            continue
        out += [f"[[hooks.{event}]]", *([f'matcher = "{matcher}"'] if matcher else []), "",
                f"[[hooks.{event}.hooks]]", 'type = "command"', f'command = "{command("codex", hook)}{" --assigned " + assigned if assigned else ""}"',
                f"timeout = {3 if event == 'SessionEnd' else 5}"]
        if event in ("SessionStart", "SubagentStart", "PostToolUse", "UserPromptSubmit"):
            out.append("additionalContextLimit = 4000")
        out.append("")
    return "\n".join([*out[:-1], END]) + "\n"


def _codex_text(text: str) -> str:
    """Codex config ``text`` with the managed hook block replaced and every other setting kept."""
    base, with_features = _enable_hooks(_MANAGED.sub("\n", text).rstrip("\n"))
    new = (base + "\n\n" if base else "") + codex_hooks(True, with_features)
    tomllib.loads(new)
    return new


def install(project: Path, only: tuple[str, ...] = AGENTS) -> dict[str, tuple[Path, list[str]]]:
    """Write the nudge hooks of each of ``only`` into ``project``; returns each agent's file and the events written.

    Every file is read and checked before the first one is written.
    """
    rendered = {"claude-code": (_claude_text, [event for event, _, _ in CLAUDE_EVENTS]),
                "codex": (_codex_text, [event for event, _, _ in CODEX_EVENTS])}
    planned = []
    for agent, path in targets(project, only).items():
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        planned.append((agent, path, old, rendered[agent][0](old)))
    for _agent, path, old, new in planned:
        if new != old:
            write_text(path, new)
    return {agent: (path, rendered[agent][1]) for agent, path, _old, _new in planned}
