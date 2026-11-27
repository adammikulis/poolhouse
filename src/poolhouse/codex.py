"""Codex on a model this machine serves: ``poolhouse-codex MODEL [-- codex args]``.

Codex speaks only the Responses API (``wire_api = "responses"``), which llama-server serves at
``/v1/responses``. The launcher leases the model (262,144 tokens, one slot, q8_0 KV cache) and starts ``codex`` on
the project folder with the person's own ``~/.codex``, naming the provider, the model and the window in ``-c``
overrides of this one run; ``~/.codex`` is not written. The server goes when Codex exits.

With ``--sandbox`` the run is a proved confinement (`poolhouse.isolation`) with a ``CODEX_HOME`` of its own in the
agent home. Its config, written read-only, holds the provider, the window and the hooks that send Bash, ``apply_patch``
and MCP calls through the destructive-action classifier in the parent, over the session bridge. Codex's own
sandbox is off inside the confinement (it cannot start there) and it never asks. The hook is pre-trusted for
this run with ``--dangerously-bypass-hook-trust``, since the launcher wrote it.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from poolhouse import coding_policy, harnessing, inplace, isolation, lead_bridge
from poolhouse.claude import DEFAULT_PORT, DEFAULT_SLOTS, alias_of
from poolhouse.guard import harnesses
from poolhouse.log import say
from poolhouse.serve import provenance

__all__ = ["FLAGS", "INSTRUCTIONS", "STANCES", "config_toml", "launch", "overrides", "printing"]

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


def _allowed(sandbox: str) -> tuple[tuple[str, str], ...]:
    return ("sandbox_mode", _q(sandbox)), ("approval_policy", _q("never"))


STANCES = {coding_policy.READ_ONLY: _allowed("read-only"), coding_policy.PLAN: _allowed("read-only"),
           coding_policy.YOLO: _allowed("danger-full-access"), "edits": _allowed("workspace-write"), "": ()}
"""Codex's own sandbox and approval settings for each `harnessing.stance` of a run in place."""


def _model(base_url: str, alias: str, window: int) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """The top-level settings and the provider's own that point Codex at the served model, each value as TOML."""
    top = [("model", _q(alias)), ("model_provider", _q(PROVIDER))]
    if window:
        top += [("model_context_window", str(int(window))), ("model_auto_compact_token_limit", str(int(window * COMPACT_SHARE)))]
    return top, [("name", _q("poolhouse")), ("base_url", _q(base_url.rstrip("/") + "/v1")), ("wire_api", _q("responses")), ("env_key", _q(KEY_ENV))]


def overrides(base_url: str, alias: str, window: int, more: Sequence[tuple[str, str]] = ()) -> list[str]:
    """The ``-c key=value`` words of a run in place: the served model, no code mode (a local model is given plain tools), then ``more``."""
    top, provider = _model(base_url, alias, window)
    given = [*top, *((f"model_providers.{PROVIDER}.{key}", value) for key, value in provider), ("features.code_mode.enabled", "false"), *more]
    return [word for key, value in given for word in ("-c", f"{key}={value}")]


def config_toml(base_url: str, alias: str, window: int, hooks: tuple[str, str, float], mcp: Sequence[str] = ()) -> str:
    """The ``config.toml`` for one sandboxed run: provider, window, the (pre, post, wait) hooks, and the MCP server `lead` (``mcp`` is its command
    line). Same input, same bytes."""
    pre, post, wait = hooks
    top, provider = _model(base_url, alias, window)
    lines = [*(f"{key} = {value}" for key, value in top[:2]), "check_for_update_on_startup = false", *(f"{key} = {value}" for key, value in top[2:])]
    lines += [
        "", "[shell_environment_policy]", 'inherit = "core"',
        "", "[features]", "hooks = true",
        "", "[features.code_mode]", "enabled = false",
        'direct_only_tool_namespaces = ["mcp__lead"]',
        "", f"[model_providers.{PROVIDER}]", *(f"{key} = {value}" for key, value in provider),
        "", "[[hooks.PreToolUse]]", 'matcher = ".*"',
        "", "[[hooks.PreToolUse.hooks]]", 'type = "command"', f"command = {_q(pre)}",
        f"timeout = {int(wait) + 30}", 'statusMessage = "poolhouse: checking the call"',
        "", "[[hooks.PostToolUse]]", 'matcher = ".*"',
        "", "[[hooks.PostToolUse.hooks]]", 'type = "command"', f"command = {_q(post)}", "timeout = 10",
    ]
    if mcp:
        lines += ["", f"[mcp_servers.{isolation.SERVER}]", f"command = {_q(mcp[0])}", f"args = {json.dumps(list(mcp[1:]))}", "startup_timeout_sec = 30",
                  f"tool_timeout_sec = {int(lead_bridge.tool_timeout())}", "required = true"]
    return "\n".join(lines) + "\n"


def printing(extra: Sequence[str]) -> bool:
    """Whether the arguments after ``--`` run Codex's non-interactive `exec`, the only mode a sandboxed run has."""
    return bool(extra) and extra[0] in ("exec", "e")


def launch(argv: Sequence[str] | None = None, *, say: Callable[[str], None] = say,
           run_codex: Callable[[Any], int] | None = None,
           session_factory: Callable[..., Any] | None = None, extras: Mapping[str, str] | None = None) -> int:
    """Lease the model, run ``codex`` on the project inside the lease and return its exit code. ``run_codex`` is given the run (`inplace.Direct`,
    or with ``--sandbox`` the proved `isolation.Confinement`) and starts the harness with its `popen`."""
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
         start: tuple[Callable[[Any], int], Mapping[str, str] | None]) -> int:
    """Start ``command`` (``codex`` and its arguments) in place or in a proved confinement and hand it to the runner. A sandboxed run's
    ``CODEX_HOME`` is inside the agent home; its config and its instructions are written read-only, and its hooks and its MCP server `lead` (ask and
    note) are clients of the session bridge."""
    runner, extras = start
    binary, *extra = command
    how = harnessing.Harness("codex", binary, served, say, extras or {}, not printing(extra))
    _, alias, window = served
    opened: list[harnessing.Session] = []

    def direct(context: inplace.Context) -> inplace.Rendering:
        more = [("developer_instructions", _q(opened[0].brief))] if opened[0].brief else []
        words = overrides(context.base_url, context.alias, context.window, [*more, *STANCES[harnessing.stance(args, printing=printing(extra))]])
        return inplace.Rendering((binary, *words, *extra), env={KEY_ENV: context.key})

    def render(context: isolation.Context) -> isolation.Rendering:
        run = opened[0]
        managed = run.session.managed_inbox
        config = config_toml(context.base_url, alias, window, (context.hook("pre"), context.hook("post"), harnessing.WAIT_S),
                             () if managed else context.client_argv("mcp"))
        files = {".codex-home/config.toml": config, ".codex-home/AGENTS.md": run.brief}
        return isolation.Rendering((binary, *FLAGS, "--cd", context.stage, *extra), files)

    try:
        with harnessing.opened(args, "codex", served, say) as run:
            opened.append(run)
            with inplace.started(args, run, how, (render, direct)) as running:
                return int(runner(running))
    except (OSError, RuntimeError, ValueError) as why:
        say(f"error: {why}")
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(launch())
