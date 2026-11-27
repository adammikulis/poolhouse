"""Launch Claude Code against a broker-managed local model with authenticated harness hooks."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from poolhouse import coding_policy, harnessing, isolation, lead_bridge
from poolhouse.client import reported_models
from poolhouse.client.health import served_alias
from poolhouse.guard import harnesses
from poolhouse.log import say
from poolhouse.serve import provenance

__all__ = ["INSTRUCTIONS", "launch", "main", "settings"]

INSTRUCTIONS = harnesses.RECORDS["claude"]
"""The instruction and settings names Claude Code reads from a project (`guard.harnesses`)."""
DEFAULT_PORT = 8080

DEFAULT_SLOTS = 1      # one conversation, one slot, the whole measured cache


def settings(pre: str = "", post: str = "", wait: float = 0.0, stop: str = "") -> str:
    """The ``--settings`` JSON: no web-fetch preflight (a call to the real API), no always-on
    thinking, and the PreToolUse and PostToolUse command hooks when given."""
    out: dict[str, object] = {"skipWebFetchPreflight": True, "alwaysThinkingEnabled": False}
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
    """Whether the arguments after ``--`` make Claude Code answer once and exit (its print mode), the only mode a launch has until the relay of L6."""
    return any(word in ("-p", "--print") for word in extra)


def launch(argv: Sequence[str] | None = None, *, say: Callable[[str], None] = say,
           run_claude: Callable[[isolation.Confinement], int] | None = None,
           session_factory: Callable[..., Any] | None = None, extras: Mapping[str, str] | None = None) -> int:
    """Lease the model, run ``claude`` inside a proved confinement and the lease, return its exit code. ``run_claude`` is given the confinement and
    starts the harness with its `popen`; ``extras`` are the variables (the effort and the extra body of a task) the caller sets for it."""
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
    if args.on and args.model:
        say("error: --on names a server already running; do not name a model as well")
        return 2
    try:
        coding_policy.Policy(args.permissions, args.execution_mode)
        asked, args.project = args.project, str(harnessing.check_project(args.project))
        harnessing.admit(args, "claude", asked, interactive=not printing(extra))
    except (OSError, RuntimeError, ValueError) as why:
        say(f"error: {why}")
        return 2
    runner = run_claude or harnessing.terminal_runner
    if args.on:
        base_url = args.on.rstrip("/")
        alias = alias_of(base_url, "")
        if not alias:
            say(f"error: {base_url} did not say what model it serves")
            return 2
        say(f"claude on {base_url} as {alias!r}; this server was already up and is left up")
        return _run(args, [binary, *extra], (base_url, alias, harnessing.window_of(base_url)), say, (runner, extras))
    began = time.time()
    try:
        with harnessing.serving(
                args.model or harnessing.default_model(),
                harnessing.Want(args.port, args.slots, args.ctx, args.no_profile, args.draft), say, "claude") as (base_url, config, found):
            alias = alias_of(base_url, found)
            say(f"claude on {base_url} as {alias!r}, up in {time.time() - began:.0f}s")
            return _run(args, [binary, *extra], (base_url, alias, config.serving.slot_context), say, (runner, extras))
    except ValueError as why:
        say(f"error: {why}")
        return 2


def _run(args: argparse.Namespace, command: Sequence[str], served: tuple[str, str, int], say: Callable[[str], None],
         start: tuple[Callable[[isolation.Confinement], int], Mapping[str, str] | None]) -> int:
    """Prove a confinement for ``command`` (``claude`` and its arguments) and hand it to the runner; ``served`` is the base URL, the alias and the
    window. The settings and the brief are written read-only inside the agent home, and the hooks are clients of the session bridge."""
    runner, extras = start
    binary, *extra = command
    how = harnessing.Harness("claude", binary, served, say, extras or {}, not printing(extra))
    brief = ""

    def render(context: isolation.Context) -> isolation.Rendering:
        files = {"session/settings.json": settings(context.hook("pre"), context.hook("post"), harnessing.WAIT_S, context.hook("stop")),
                 "session/brief.md": brief, "session/mcp.json": context.lead_servers(lead_bridge.tool_timeout())}
        return isolation.Rendering((binary, "--settings", str(context.file("session/settings.json")), "--append-system-prompt-file",
                                    str(context.file("session/brief.md")), "--mcp-config", str(context.file("session/mcp.json")),
                                    "--strict-mcp-config", *extra), files)

    try:
        with harnessing.opened(args, "claude-code", served, say) as run:
            brief = run.brief
            with harnessing.confined(args, run, how, render) as confinement:
                say(f"permissions {args.permissions}; mode {args.execution_mode}; working on a copy of {run.cwd}, in a box the operating system "
                    "holds; changes arrive when the turn ends")
                return int(runner(confinement))
    except (OSError, RuntimeError, ValueError) as why:
        say(f"error: {why}")
        return 2


def main(argv: Sequence[str] | None = None) -> int:
    return launch(argv)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
