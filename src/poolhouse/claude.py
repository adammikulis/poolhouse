"""``poolhouse-claude``: Claude Code on a broker-managed local model, in place with the person's own settings, or sandboxed with ``--sandbox``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from poolhouse import coding_policy, harnessing, inplace, isolation, lead_bridge, serverkeys
from poolhouse.client import reported_models
from poolhouse.client.health import served_alias
from poolhouse.guard import harnesses
from poolhouse.isolation.environment import MIN_COMPACT_WINDOW, MODEL_VARS, OFFLINE
from poolhouse.log import say
from poolhouse.serve import provenance

__all__ = ["INSTRUCTIONS", "STANCES", "environment", "launch", "main", "settings"]

INSTRUCTIONS = harnesses.RECORDS["claude"]
"""The instruction and settings names Claude Code reads from a project; `guard.runs_later` refuses a change to one at any depth."""
DEFAULT_PORT = 8080

DEFAULT_SLOTS = 1      # one conversation, one slot, the whole measured cache
CLOUD = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")
"""The switches that send every request to a hosted provider instead of the served model."""
NO_CHANGES = ("--disallowed-tools=Bash,Edit,MultiEdit,Write,NotebookEdit",)
STANCES = {coding_policy.READ_ONLY: NO_CHANGES, coding_policy.PLAN: (*NO_CHANGES, "--permission-mode", "plan"),
           coding_policy.YOLO: ("--dangerously-skip-permissions",), "edits": ("--permission-mode", "acceptEdits"), "": ()}
"""Claude Code's own permission flags for each `harnessing.stance` of a run in place."""


def environment(base_url: str, alias: str, *, offline: bool = True, context: int = 0, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The variables Claude Code reaches a served model by, over ``base`` (the caller's own environment by default): every model call goes to
    ``base_url``."""
    env = dict(os.environ if base is None else base)
    env.pop("ANTHROPIC_API_KEY", None)
    env["ANTHROPIC_BASE_URL"] = base_url.rstrip("/")
    env["ANTHROPIC_AUTH_TOKEN"] = serverkeys.for_url(base_url) or "local"
    if context:
        env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = str(int(context))
        if context >= MIN_COMPACT_WINDOW:
            env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = str(int(context))
    env.update(dict.fromkeys(MODEL_VARS, alias))
    if offline:
        env.update(OFFLINE)
    return env


def settings(pre: str = "", post: str = "", wait: float = 0.0, stop: str = "", env: Mapping[str, str] | None = None) -> str:
    """The ``--settings`` JSON: no web-fetch preflight (a call to the real API), no always-on
    thinking, the variables of ``env`` (over the person's own `env` setting) and the command hooks when given."""
    out: dict[str, object] = {"skipWebFetchPreflight": True, "alwaysThinkingEnabled": False}
    if env:
        out["env"] = dict(env)
    if pre:
        out["hooks"] = {
            "PreToolUse": [{"matcher": "*", "hooks": [
                {"type": "command", "command": pre, "timeout": int(wait) + 30}]}],
            "PostToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": post, "timeout": 10}]}]}
    if stop:
        completion = [{"hooks": [{"type": "command", "command": stop, "timeout": 30}]}]
        out.setdefault("hooks", {}).update(Stop=completion, SubagentStop=completion)
    return json.dumps(out, sort_keys=True)


def alias_of(base_url: str, model: str) -> str:
    """The name the server serves the model under -- what every model variable must say."""
    return served_alias(reported_models(base_url), str(model))


def parser() -> argparse.ArgumentParser:
    return harnessing.parser("claude", "Claude Code", DEFAULT_PORT, DEFAULT_SLOTS)


def printing(extra: Sequence[str]) -> bool:
    """Whether the arguments after ``--`` make Claude Code answer once and exit (its print mode), the only mode a sandboxed run has."""
    return any(word in ("-p", "--print") for word in extra)


def launch(argv: Sequence[str] | None = None, *, say: Callable[[str], None] = say,
           run_claude: Callable[[Any], int] | None = None,
           session_factory: Callable[..., Any] | None = None, extras: Mapping[str, str] | None = None) -> int:
    """Lease the model, run ``claude`` on the project inside the lease and return its exit code. ``run_claude`` is given the run (`inplace.Direct`,
    or with ``--sandbox`` the proved `isolation.Confinement`) and starts the harness with its `popen`; ``extras`` are the variables (the effort and
    the extra body of a task) the caller sets for it."""
    words = list(sys.argv[1:] if argv is None else argv)
    ours, extra = (words[: words.index("--")], words[words.index("--") + 1:]) \
        if "--" in words else (words, [])
    args = parser().parse_args(ours)
    provenance.told(args.lease_for)
    args.session_factory = session_factory
    binary = args.claude or harnessing.binary_for("claude")
    if not binary:
        say("error: no `claude` on PATH; install Claude Code or pass --claude PATH")
        return 2
    if not harnessing.checked(args, "claude", say, interactive=not printing(extra)):
        return 2
    start = (run_claude or harnessing.terminal_runner, extras)
    return harnessing.serve_and_run(args, "claude", say, lambda *got: alias_of(*got), lambda served: _run(args, [binary, *extra], served, say, start))


def _run(args: argparse.Namespace, command: Sequence[str], served: tuple[str, str, int], say: Callable[[str], None],
         start: tuple[Callable[[Any], int], Mapping[str, str] | None]) -> int:
    """Start ``command`` (``claude`` and its arguments) in place or in a proved confinement and hand it to the runner; ``served`` is the base URL,
    the alias and the window."""
    runner, extras = start
    binary, *extra = command
    how = harnessing.Harness("claude", binary, served, say, extras or {}, not printing(extra))
    brief = ""

    def boxed(context: isolation.Context) -> isolation.Rendering:
        files = {"session/settings.json": settings(context.hook("pre"), context.hook("post"), harnessing.WAIT_S, context.hook("stop")),
                 "session/brief.md": brief, "session/mcp.json": context.lead_servers(lead_bridge.tool_timeout())}
        return isolation.Rendering((binary, "--settings", str(context.file("session/settings.json")), "--append-system-prompt-file",
                                    str(context.file("session/brief.md")), "--mcp-config", str(context.file("session/mcp.json")),
                                    "--strict-mcp-config", *extra), files)

    def direct(context: inplace.Context) -> inplace.Rendering:
        pointed = {**environment(context.base_url, context.alias, context=context.window, base={}), "ANTHROPIC_AUTH_TOKEN": context.key}
        files = {"settings.json": settings(env={**pointed, "ANTHROPIC_API_KEY": "", **dict.fromkeys(CLOUD, "0")})}
        argv = [binary, "--settings", str(context.file("settings.json"))]
        if brief:
            files["brief.md"] = brief
            argv += ["--append-system-prompt-file", str(context.file("brief.md"))]
        flags = STANCES[harnessing.stance(args, printing=printing(extra))]
        return inplace.Rendering((*argv, *flags, *extra), files, pointed, ("ANTHROPIC_API_KEY", *CLOUD))

    try:
        with harnessing.opened(args, "claude-code", served, say) as run:
            brief = run.brief
            with inplace.started(args, run, how, (boxed, direct)) as running:
                return int(runner(running))
    except (OSError, RuntimeError, ValueError) as why:
        say(f"error: {why}")
        return 2


def main(argv: Sequence[str] | None = None) -> int:
    return launch(argv)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
