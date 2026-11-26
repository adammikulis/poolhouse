"""OpenCode on a model this machine serves, in a proved confinement: ``poolhouse-opencode MODEL [-- message words]`` runs ``opencode run MESSAGE``.

The launcher leases the model, proves a confinement (`poolhouse.isolation`) and starts ``opencode run`` inside it with a config of its own in the
agent home, so the person's ``~/.config/opencode`` is never read or changed. The provider is the bridge's OpenAI-compatible URL with the bridge's
token (minted for the launch, never asked of a person); OpenCode's own network traffic (update checks, the model list, LSP downloads) is switched
off. A plugin written read-only beside the config asks the parent's classifier over the session bridge before each tool call and after it, as
Pi's extension does. The binary is found on PATH or fetched once (`poolhouse.opencode_install`).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from poolhouse import harness_js, harnessing, isolation, opencode_install
from poolhouse.claude import DEFAULT_PORT, DEFAULT_SLOTS, alias_of
from poolhouse.client import families
from poolhouse.guard import harnesses
from poolhouse.log import say
from poolhouse.serve import provenance
from poolhouse.workspace import tools

__all__ = ["INSTRUCTIONS", "config", "launch", "plugin"]

INSTRUCTIONS = harnesses.RECORDS["opencode"]
"""The instruction and settings names OpenCode reads from a project; `guard.runs_later` refuses a change to one at any depth."""
PROVIDER = "poolhouse"
CONFIG = "opencode.json"
PLUGIN = "poolhouse-plugin.js"
BRIEF = "poolhouse-brief.md"


def config(served: tuple[str, str, int], token: str, files: tuple[str, str], thinking: dict[str, Any] | None = None) -> str:
    """The ``opencode.json`` of one run: the provider (the bridge's ``/v1`` and its token), the one model with its window, the plugin, no sharing and
    no update, and permissions that never ask (the classifier and the mount decide). ``served`` is the bridge's URL, the model's name and its window; ``files``
    are the plugin and the brief, by path."""
    base_url, alias, window = served
    plugin_file, brief_file = files
    model: dict[str, Any] = {"name": alias, "tool_call": True, "reasoning": True, "limit": {"context": window, "output": min(window, 32768)}}
    if thinking:
        model["options"] = {"chat_template_kwargs": thinking}
    return json.dumps({
        "$schema": "https://opencode.ai/config.json",
        "model": f"{PROVIDER}/{alias}", "small_model": f"{PROVIDER}/{alias}",
        "provider": {PROVIDER: {"npm": "@ai-sdk/openai-compatible", "name": "poolhouse",
                                "options": {"baseURL": base_url.rstrip("/") + "/v1", "apiKey": token}, "models": {alias: model}}},
        "enabled_providers": [PROVIDER], "plugin": [Path(plugin_file).as_uri()], "instructions": [brief_file], "share": "disabled", "autoupdate": False,
        "permission": {"*": "allow", "webfetch": "deny", "external_directory": "deny"},
    }, sort_keys=True, indent=1)


def plugin(settings: dict[str, Any]) -> str:
    """OpenCode plugin applying poolhouse's tool classifier. ``settings["hook"]`` is the client command that asks the parent; a tool call it
    refuses, or cannot ask about, throws, which OpenCode returns to the model as the tool's error."""
    return harness_js.prelude(settings) + '''export const Poolhouse = async () => ({
  "tool.execute.before": async (input, output) => {
    const args = {...output.args};
    if (args.filePath && !args.file_path) args.file_path = args.filePath;
    const result = call("pre", {tool_name: names[input.tool] || input.tool, tool_input: args, cwd: cfg.root});
    if (result.failed) throw new Error("poolhouse: tool policy failed; blocked");
    const answer = result.hookSpecificOutput || {};
    if (answer.permissionDecision === "deny") throw new Error("poolhouse: " + (answer.permissionDecisionReason || "blocked by policy"));
  },
  "tool.execute.after": async () => {
    if (call("post", {}).failed) throw new Error("poolhouse: mutation checkpoint failed; stopped");
  },
});
'''


def launch(argv: Sequence[str] | None = None, *, say: Callable[[str], None] = say,
           run_opencode: Callable[[isolation.Confinement], int] | None = None, session_factory: Callable[..., Any] | None = None) -> int:
    """Run ``opencode run`` against a leased local model inside a proved confinement; the words after ``--`` are the message and OpenCode's own run options."""
    words = list(sys.argv[1:] if argv is None else argv)
    ours, extra = (words[:words.index("--")], words[words.index("--") + 1:]) if "--" in words else (words, [])
    args = harnessing.parser("opencode", "OpenCode", DEFAULT_PORT, DEFAULT_SLOTS).parse_args(ours)
    provenance.told(args.lease_for)
    args.session_factory = session_factory
    binary = args.opencode or harnessing.binary_for("opencode")
    if not binary:
        try:
            binary = opencode_install.ensure()
        except opencode_install.InstallFailed as why:
            say(f"error: {why}")
            return 2
    if not harnessing.checked(args, "opencode", say, interactive=False):
        return 2
    runner = run_opencode or harnessing.terminal_runner
    return harnessing.serve_and_run(args, "opencode", say, lambda *got: alias_of(*got), lambda served: _run(args, [binary, *extra], served, say, runner))


def _run(args: argparse.Namespace, command: Sequence[str], served: tuple[str, str, int], say: Callable[[str], None],
         runner: Callable[[isolation.Confinement], int]) -> int:
    """Prove a confinement for ``command`` (``opencode`` and the message) and hand it to the runner."""
    _, alias, window = served
    binary, *extra = command
    how = harnessing.Harness("opencode", binary, served, say, workspace=tools.for_helper)
    opened: list[harnessing.Session] = []

    def render(context: isolation.Context) -> isolation.Rendering:
        run = opened[0]
        files = {f".opencode-config/{CONFIG}": config((context.base_url, alias, window), context.token,
                                                      (str(context.file(f".opencode-config/{PLUGIN}")), str(context.file(f".opencode-config/{BRIEF}"))),
                                                      families.for_model_id(alias).think_kwargs(False)),
                 f".opencode-config/{PLUGIN}": plugin({"hook": context.client_argv("hook"), "root": context.stage}),
                 f".opencode-config/{BRIEF}": run.brief}
        return isolation.Rendering((binary, "run", "--standalone", "--format", "json", "--model", f"{PROVIDER}/{alias}", *extra), files)

    try:
        with harnessing.opened(args, "opencode", served, say) as run:
            opened.append(run)
            with harnessing.confined(args, run, how, render) as confinement:
                say(f"permissions {args.permissions}; mode {args.execution_mode}; working on a copy of {run.cwd}, in a box the operating system "
                    "holds; OpenCode's tool calls pass through poolhouse's classifier")
                return int(runner(confinement))
    except (OSError, RuntimeError, ValueError) as why:
        say(f"error: {why}")
        return 2


if __name__ == "__main__":
    raise SystemExit(launch())
