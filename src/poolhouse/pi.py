"""Pi coding agent sessions on models served by poolhouse: in place with the person's own settings, an extension of the run adding the served model.

With ``--sandbox`` the run is a proved confinement: Pi's own tools run inside it, and its extension asks the parent's classifier over the session
bridge before each call."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from typing import Any

from poolhouse import coding_policy, harness_js, harnessing, inplace, isolation
from poolhouse.claude import DEFAULT_PORT, DEFAULT_SLOTS, alias_of
from poolhouse.client import families
from poolhouse.guard import harnesses
from poolhouse.log import say
from poolhouse.serve import provenance

__all__ = ["INSTRUCTIONS", "STANCES", "extension", "launch", "models", "provider_extension"]

INSTRUCTIONS = harnesses.RECORDS["pi"]
"""The instruction and settings names Pi reads from a project; `guard.runs_later` refuses a change to one at any depth."""


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


NO_CHANGES = ("--tools", "read,grep,find,ls")
STANCES = {coding_policy.READ_ONLY: NO_CHANGES, coding_policy.PLAN: NO_CHANGES}
"""Pi's own tool list for each `harnessing.stance` of a run in place that changes nothing; Pi has no permission prompts to set otherwise."""
SHAPING = '''  let turns = 0;
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
'''
"""The part of an extension every run has: the output limit, the model's thinking switch and the turn limit."""
POLICY = '''  pi.on("tool_call", (event) => {
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
'''


def extension(settings: dict[str, Any]) -> str:
    """Pi extension of a sandboxed run: poolhouse's tool classifier and the turn limit. ``settings["hook"]`` is the client command that asks the parent."""
    return harness_js.prelude(settings) + "export default function (pi) {\n" + SHAPING + POLICY + "}\n"


def provider_extension(settings: dict[str, Any]) -> str:
    """Pi extension of a run in place: it adds the served model (``settings["provider"]``) as the provider `poolhouse` beside the person's own."""
    return (f"const cfg = {json.dumps(settings)};\nexport default function (pi) {{\n"
            '  pi.registerProvider("poolhouse", cfg.provider);\n' + SHAPING + "}\n")


def parser() -> argparse.ArgumentParser:
    ap = harnessing.parser("pi", "Pi", DEFAULT_PORT, DEFAULT_SLOTS)
    ap.add_argument("--max-turns", type=int, default=None)
    ap.add_argument("--max-output-tokens", type=int, default=None)
    ap.add_argument("--effort", choices=("off", "low", "medium", "high"), default="off")
    return ap


def launch(argv: Sequence[str] | None = None, *, say: Callable[[str], None] = say,
           run_pi: Callable[[Any], int] | None = None, session_factory: Callable[..., Any] | None = None) -> int:
    """Run Pi against a leased local model, in place or with ``--sandbox`` in a proved confinement with per-tool policy checks in the parent."""
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
    if not harnessing.checked(args, "pi", say, interactive=False):  # a sandboxed run of Pi always answers once
        return 2
    if args.max_turns is None or args.max_output_tokens is None:
        say("Task limits include none. Tasks could run indefinitely until cancelled.")
    runner = run_pi or harnessing.terminal_runner
    return harnessing.serve_and_run(args, "pi", say, lambda *got: alias_of(*got), lambda served: _run(args, [binary, *extra], served, say, runner))


def printing(extra: Sequence[str]) -> bool:
    """Whether the arguments after ``--`` make Pi answer once and exit."""
    return bool({"--print", "-p", "--mode"} & set(extra))


def _run(args: argparse.Namespace, command: Sequence[str], served: tuple[str, str, int], say: Callable[[str], None],
         runner: Callable[[Any], int]) -> int:
    """Start ``command`` (``pi`` and its arguments) in place or in a proved confinement and hand it to the runner."""
    _, alias, window = served
    binary, *extra = command
    how = harnessing.Harness("pi", binary, served, say)
    opened: list[harnessing.Session] = []

    def direct(context: inplace.Context) -> inplace.Rendering:
        provider = json.loads(models(context.base_url, alias, window, args.max_output_tokens, context.key))["providers"]["poolhouse"]
        made = provider_extension({"provider": provider, "maxTurns": args.max_turns, "maxOutputTokens": args.max_output_tokens,
                                   "thinking": families.for_model_id(alias).think_kwargs(args.effort != "off")})
        brief = ["--append-system-prompt", opened[0].brief] if opened[0].brief else []
        flags = STANCES.get(harnessing.stance(args, printing=printing(extra)), ())
        return inplace.Rendering((binary, "--extension", str(context.file("poolhouse.ts")), "--provider", "poolhouse", "--model", alias,
                                  "--thinking", args.effort, *brief, *flags, *extra), {"poolhouse.ts": made})

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
            with inplace.started(args, run, how, (render, direct)) as running:
                return int(runner(running))
    except (OSError, RuntimeError, ValueError) as why:
        say(f"error: {why}")
        return 2


if __name__ == "__main__":
    raise SystemExit(launch())
