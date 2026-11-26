"""Codex on a model this machine serves: ``poolhouse-codex MODEL [-- codex args]``.

Codex speaks only the Responses API (``wire_api = "responses"``), which llama-server serves at
``/v1/responses``. The launcher leases the model (262,144 tokens, one slot, q8_0 KV cache), proves a
confinement for the run (`poolhouse.isolation`) and starts ``codex`` inside it with a ``CODEX_HOME`` of its
own in the agent home, so the person's ``~/.codex`` is never read or changed. The config, written read-only,
holds the provider, the context window and auto-compact limit and the hooks that send Bash, ``apply_patch``
and MCP calls through the destructive-action classifier in the parent, over the session bridge. Codex's own
sandbox is off inside the confinement (it cannot start there) and it never asks. The hook is pre-trusted for
this run with ``--dangerously-bypass-hook-trust``, since the launcher wrote it. The server goes when Codex exits.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from poolhouse import harnessing, isolation
from poolhouse.claude import DEFAULT_PORT, DEFAULT_SLOTS, alias_of
from poolhouse.guard import harnesses
from poolhouse.log import say
from poolhouse.serve import provenance

__all__ = ["FLAGS", "INSTRUCTIONS", "config_toml", "launch", "printing"]

INSTRUCTIONS = harnesses.RECORDS["codex"]
"""The instruction and settings names Codex reads from a project; `guard.runs_later` refuses a change to one at any depth."""
PROVIDER = "poolhouse"
KEY_ENV = "POOLHOUSE_API_KEY"
COMPACT_SHARE = 0.9
FLAGS = ("--sandbox", "danger-full-access", "--ask-for-approval", "never", "--dangerously-bypass-hook-trust")
"""Codex's own sandbox is off and it never asks: its macOS sandbox cannot start inside the confinement (a nested `sandbox_apply` is refused), and the
mount enforces what each permission profile means (docs/design-isolated-launcher.md, 8.1)."""


def _q(text: str) -> str:
    return json.dumps(text, ensure_ascii=True)


def config_toml(base_url: str, alias: str, window: int, hooks: tuple[str, str, float], mcp: Sequence[str] = ()) -> str:
    """The ``config.toml`` for one run: provider, window, the (pre, post, wait) hooks, and the workspace MCP server (``mcp`` is its command line).
    Same input, same bytes."""
    pre, post, wait = hooks
    lines = [
        f"model = {_q(alias)}",
        f"model_provider = {_q(PROVIDER)}",
        "check_for_update_on_startup = false",
    ]
    if window:
        lines += [f"model_context_window = {int(window)}",
                  f"model_auto_compact_token_limit = {int(window * COMPACT_SHARE)}"]
    lines += [
        "", "[shell_environment_policy]", 'inherit = "core"',
        "", "[features]", "hooks = true",
        "", "[features.code_mode]", "enabled = false",
        'direct_only_tool_namespaces = ["mcp__workspace"]',
        "", f"[model_providers.{PROVIDER}]", 'name = "poolhouse"',
        f"base_url = {_q(base_url.rstrip('/') + '/v1')}", 'wire_api = "responses"', f"env_key = {_q(KEY_ENV)}",
        "", "[[hooks.PreToolUse]]", 'matcher = ".*"',
        "", "[[hooks.PreToolUse.hooks]]", 'type = "command"', f"command = {_q(pre)}",
        f"timeout = {int(wait) + 30}", 'statusMessage = "poolhouse: checking the call"',
        "", "[[hooks.PostToolUse]]", 'matcher = ".*"',
        "", "[[hooks.PostToolUse.hooks]]", 'type = "command"', f"command = {_q(post)}", "timeout = 10",
    ]
    if mcp:
        lines += ["", "[mcp_servers.workspace]", f"command = {_q(mcp[0])}", f"args = {json.dumps(list(mcp[1:]))}", "startup_timeout_sec = 30",
                  "required = true"]
    return "\n".join(lines) + "\n"


def printing(extra: Sequence[str]) -> bool:
    """Whether the arguments after ``--`` run Codex's non-interactive `exec`, the only mode a launch has until the relay of L6."""
    return bool(extra) and extra[0] in ("exec", "e")


def launch(argv: Sequence[str] | None = None, *, say: Callable[[str], None] = say,
           run_codex: Callable[[isolation.Confinement], int] | None = None,
           session_factory: Callable[..., Any] | None = None, extras: Mapping[str, str] | None = None) -> int:
    """Lease the model, run ``codex`` inside a proved confinement and the lease, return its exit code. ``run_codex`` is given the confinement and
    starts the harness with its `popen`."""
    words = list(sys.argv[1:] if argv is None else argv)
    ours, extra = (words[: words.index("--")], words[words.index("--") + 1:]) \
        if "--" in words else (words, [])
    args = harnessing.parser("codex", "Codex", DEFAULT_PORT, DEFAULT_SLOTS).parse_args(ours)
    provenance.told(args.lease_for)
    args.session_factory = session_factory
    binary = args.codex or shutil.which("codex") or ""
    if not binary:
        say("error: no `codex` on PATH; install Codex or pass --codex PATH")
        return 2
    if not harnessing.checked(args, "codex", say, interactive=not printing(extra)):
        return 2
    start = (run_codex or harnessing.terminal_runner, extras)
    return harnessing.serve_and_run(args, "codex", say, lambda *got: alias_of(*got), lambda served: _run(args, [binary, *extra], served, say, start))


def _run(args: argparse.Namespace, command: Sequence[str], served: tuple[str, str, int], say: Callable[[str], None],
         start: tuple[Callable[[isolation.Confinement], int], Mapping[str, str] | None]) -> int:
    """Prove a confinement for ``command`` (``codex`` and its arguments) and hand it to the runner. This run's ``CODEX_HOME`` is inside the agent
    home; its config and its instructions are written read-only, and its hooks and its workspace MCP server are clients of the session bridge."""
    runner, extras = start
    binary, *extra = command
    how = harnessing.Harness("codex", binary, served, say, extras or {}, not printing(extra))
    _, alias, window = served
    opened: list[harnessing.Session] = []

    def render(context: isolation.Context) -> isolation.Rendering:
        run = opened[0]
        managed = run.session.managed_inbox
        config = config_toml(context.base_url, alias, window, (context.hook("pre"), context.hook("post"), harnessing.WAIT_S),
                             () if managed else context.client_argv("mcp"))
        note = "" if managed else (
            "\nUse the workspace MCP tools for inbox/send/thread/claim. They already carry your "
            "assigned identity; never use shell workspace commands inside the coding sandbox.\n")
        files = {".codex-home/config.toml": config, ".codex-home/AGENTS.md": run.brief + note}
        return isolation.Rendering((binary, *FLAGS, "--cd", context.stage, *extra), files)

    try:
        with harnessing.opened(args, "codex", served, say) as run:
            opened.append(run)
            with harnessing.confined(args, run, how, render) as confinement:
                say(f"permissions {args.permissions}; mode {args.execution_mode}; working on a copy of {run.cwd}, in a box the operating system "
                    "holds; changes arrive when the turn ends")
                return int(runner(confinement))
    except (OSError, RuntimeError, ValueError) as why:
        say(f"error: {why}")
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(launch())
