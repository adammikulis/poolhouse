"""Pi coding agent sessions on models served by poolhouse, in a proved confinement: Pi's own tools run inside the box, and its extension asks the
parent's classifier over the session bridge before each call."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from typing import Any

from poolhouse import harness_js, harnessing, isolation
from poolhouse.claude import DEFAULT_PORT, DEFAULT_SLOTS, alias_of
from poolhouse.client import families
from poolhouse.guard import harnesses
from poolhouse.log import say
from poolhouse.serve import provenance

__all__ = ["INSTRUCTIONS", "extension", "launch", "models"]

INSTRUCTIONS = harnesses.RECORDS["pi"]
"""The instruction and settings names Pi reads from a project (`guard.harnesses`)."""


def models(base_url: str, alias: str, window: int, max_output_tokens: int | None = None, key: str = "local") -> str:
    """Pi provider configuration for the model already served by poolhouse; ``base_url`` is the bridge and ``key`` its token."""
    model = {"id": alias, "name": alias, "reasoning": True, "input": ["text"],
             "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
             "contextWindow": window, "maxTokens": max_output_tokens if max_output_tokens is not None else window}
    return json.dumps({"providers": {"poolhouse": {"baseUrl": base_url.rstrip("/"),
        "api": "openai-completions", "apiKey": key, "models": [model]}}}, sort_keys=True)


def mcp(context: isolation.Context) -> str:
    """Pi's ``mcp.json`` (read from its agent directory): the one server `lead`, its two tools declared to the model directly as `mcp__lead__ask` and
    `mcp__lead__note`, since Pi's default (`codemode`) leaves a tool out of the model's tool list."""
    return json.dumps({"mcpServers": {isolation.SERVER: {**context.lead_server(), "exposure": "direct"}}})


def extension(settings: dict[str, Any]) -> str:
    """Pi extension applying poolhouse's tool classifier and turn limit. ``settings["hook"]`` is the client command that asks the parent."""
    return harness_js.prelude(settings) + '''export default function (pi) {
  let turns = 0;
  pi.on("before_provider_request", (event) => {
    const payload = {...event.payload};
    delete payload.max_completion_tokens;
    delete payload.max_tokens;
    if (cfg.maxOutputTokens != null) payload.max_tokens = cfg.maxOutputTokens;
    if (cfg.thinking) payload.chat_template_kwargs = {...payload.chat_template_kwargs, ...cfg.thinking};
    return payload;
  });
  pi.on("turn_start", (_event, ctx) => {
    if (cfg.maxTurns != null && ++turns > cfg.maxTurns) {
      console.log(JSON.stringify({type:"error",message:"Pi turn budget exhausted"}));
      ctx.abort(); ctx.shutdown();
    }
  });
  pi.on("tool_call", (event) => {
    const mapped = names[event.toolName] || event.toolName;
    const input = {...event.input};
    if (input.path && !input.file_path) input.file_path = input.path;
    const result = call("pre", {tool_name:mapped,tool_input:input,cwd:cfg.root});
    if (result.failed) return {block:true,reason:"poolhouse: tool policy failed; blocked"};
    const answer = result.hookSpecificOutput || {};
    if (answer.permissionDecision === "deny") return {block:true,reason:"poolhouse: " + (answer.permissionDecisionReason || "blocked by policy")};
  });
  pi.on("tool_result", (event, ctx) => {
    if (event?.parentToolCallId) return;  // a nested call's result never reaches the model; its parent's does
    const result = call("post", {});
    if (result.failed) {
      console.log(JSON.stringify({type:"error",message:"Pi mutation checkpoint failed"}));
      ctx.abort(); ctx.shutdown();
      return;
    }
    const said = result.hookSpecificOutput?.additionalContext;
    if (said) return {content: [...(event?.content || []), {type: "text", text: said}]};
  });
}
'''


def parser() -> argparse.ArgumentParser:
    ap = harnessing.parser("pi", "Pi", DEFAULT_PORT, DEFAULT_SLOTS)
    ap.add_argument("--max-turns", type=int, default=None)
    ap.add_argument("--max-output-tokens", type=int, default=None)
    ap.add_argument("--effort", choices=("off", "low", "medium", "high"), default="off")
    return ap


def launch(argv: Sequence[str] | None = None, *, say: Callable[[str], None] = say,
           run_pi: Callable[[isolation.Confinement], int] | None = None, session_factory: Callable[..., Any] | None = None) -> int:
    """Run Pi against a leased local model inside a proved confinement, with per-tool poolhouse policy checks in the parent."""
    words = list(sys.argv[1:] if argv is None else argv)
    ours, extra = (words[:words.index("--")], words[words.index("--") + 1:]) if "--" in words else (words, [])
    args = parser().parse_args(ours)
    provenance.told(args.lease_for)
    args.session_factory = session_factory
    binary = args.pi or harnessing.binary_for("pi")
    if not binary:
        say("error: no `pi` on PATH; install @earendil-works/pi-coding-agent")
        return 2
    if args.max_output_tokens is not None and args.max_output_tokens <= 0:
        say("error: --max-output-tokens must be positive")
        return 2
    if args.max_turns is not None and args.max_turns <= 0:
        say("error: --max-turns must be positive")
        return 2
    if not harnessing.checked(args, "pi", say, interactive=False):
        return 2
    if args.max_turns is None or args.max_output_tokens is None:
        say("Task limits include none. Tasks could run indefinitely until cancelled.")
    runner = run_pi or harnessing.terminal_runner
    return harnessing.serve_and_run(args, "pi", say, lambda *got: alias_of(*got), lambda served: _run(args, [binary, *extra], served, say, runner))


def _run(args: argparse.Namespace, command: Sequence[str], served: tuple[str, str, int], say: Callable[[str], None],
         runner: Callable[[isolation.Confinement], int]) -> int:
    """Prove a confinement for ``command`` (``pi`` and its arguments) and hand it to the runner."""
    _, alias, window = served
    binary, *extra = command
    how = harnessing.Harness("pi", binary, served, say)
    opened: list[harnessing.Session] = []

    def render(context: isolation.Context) -> isolation.Rendering:
        run = opened[0]
        hook = context.file("session/poolhouse.ts")
        files = {".pi-config/models.json": models(context.base_url, alias, window, args.max_output_tokens, context.token),
                 ".pi-config/mcp.json": mcp(context),
                 "session/poolhouse.ts": extension({"hook": context.client_argv("hook"), "root": context.stage, "maxTurns": args.max_turns,
                                                    "maxOutputTokens": args.max_output_tokens,
                                                    "thinking": families.for_model_id(alias).think_kwargs(args.effort != "off")})}
        return isolation.Rendering((binary, "--provider", "poolhouse", "--model", alias, "--mode", "json", "--print", "--no-session",
                                    "--no-approve", "--no-skills", "--no-prompt-templates", "--no-extensions", "--extension", "builtin:mcp", "--extension", str(hook), "--append-system-prompt",
                                    run.brief, "--thinking", args.effort, *extra), files)

    try:
        with harnessing.opened(args, "pi", served, say) as run:
            opened.append(run)
            with harnessing.confined(args, run, how, render) as confinement:
                say(f"permissions {args.permissions}; mode {args.execution_mode}; working on a copy of {run.cwd}, in a box the operating system "
                    "holds; Pi's tool calls pass through poolhouse's classifier")
                return int(runner(confinement))
    except (OSError, RuntimeError, ValueError) as why:
        say(f"error: {why}")
        return 2


if __name__ == "__main__":
    raise SystemExit(launch())
