"""OpenCode on a model this machine serves, in a proved confinement: ``poolhouse-opencode MODEL [-- message words]`` runs ``opencode run MESSAGE``.

The launcher leases the model, proves a confinement (`poolhouse.isolation`) and starts ``opencode run`` inside it with a config of its own in the
agent home, so the person's ``~/.config/opencode`` is never read or changed. The provider is the bridge's OpenAI-compatible URL with the bridge's
token (minted for the launch, never asked of a person); OpenCode's own network traffic (update checks, the model list, LSP downloads) is switched
off. A plugin written read-only in a folder of its own asks the parent's classifier over the session bridge before each tool call and after it, as
Pi's extension does. OpenCode starts without a plugin it cannot load and says so only in a warning of its server, so the server's warnings go to the
run's error output (a delegate's log), and a run whose plugin never reported publishes nothing. The binary is the pinned release
(`poolhouse.opencode_install`), fetched once.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from poolhouse import harness_js, harnessing, isolation, lead_bridge, opencode_install
from poolhouse.claude import DEFAULT_PORT, DEFAULT_SLOTS, alias_of
from poolhouse.client import families
from poolhouse.guard import harnesses
from poolhouse.log import say
from poolhouse.serve import provenance
from poolhouse.serve.ports import free_port

__all__ = ["AGENT", "INSTRUCTIONS", "UNGUARDED", "Setup", "config", "launch", "plugin", "rendering"]

INSTRUCTIONS = harnesses.RECORDS["opencode"]
"""The instruction and settings names OpenCode reads from a project; `guard.runs_later` refuses a change to one at any depth."""
PROVIDER = "poolhouse"
AGENT = "poolhouse"
"""The agent of a run that brings its own (`Setup.agent`)."""
CONFIG = "opencode.json"
PLUGIN = "bin/poolhouse-plugin"
"""The plugin's folder in the agent home: OpenCode loads a configured plugin only from a folder (a path to a file is dropped with a warning), and
reads its `ENTRY`. It lies under ``bin``, which the box holds read-only as a whole, so a run cannot put a second entry beside ours."""
ENTRY = "index.js"
BRIEF = "poolhouse-brief.md"
UNGUARDED = "OpenCode ran without poolhouse's plugin reporting, so its tool calls were not judged; nothing more it changed is published"


@dataclass(frozen=True, slots=True)
class Setup:
    """What one run's ``opencode.json`` is made from: the bridge's URL, the model's name and its window (``served``) and its token; the path of the
    plugin's folder; the command of the MCP server `lead` (its tools reach OpenCode as `lead_ask` and `lead_note`); the model's thinking switches;
    and either the launcher's brief, by path, or an ``agent`` of the run's own (its system prompt and its permission rules), which every request then
    uses."""

    served: tuple[str, str, int]
    token: str
    plugin: str
    lead: Sequence[str]
    thinking: Mapping[str, Any] | None = None
    brief: str = ""
    agent: Mapping[str, Any] | None = None


def config(setup: Setup) -> str:
    """The ``opencode.json`` of one run: the provider, the one model with its window, the plugin, no sharing and no update, a tool's output cut at as
    many bytes as the window holds tokens (a quarter of it, so one call cannot fill it), and permissions that never ask (the classifier and the mount
    decide)."""
    base_url, alias, window = setup.served
    model: dict[str, Any] = {"name": alias, "tool_call": True, "limit": {"context": window, "output": min(window, 32768)}}
    if setup.thinking:
        model["options"] = {"chat_template_kwargs": dict(setup.thinking)}
    made: dict[str, Any] = {
        "$schema": "https://opencode.ai/config.json", "mcp": {isolation.SERVER: {"type": "local", "command": list(setup.lead), "enabled": True,
                                                                                  "timeout": int(lead_bridge.tool_timeout() * 1000)}},
        "model": f"{PROVIDER}/{alias}", "small_model": f"{PROVIDER}/{alias}",
        "provider": {PROVIDER: {"npm": "@ai-sdk/openai-compatible", "name": "poolhouse",
                                "options": {"baseURL": base_url.rstrip("/") + "/v1", "apiKey": setup.token}, "models": {alias: model}}},
        "enabled_providers": [PROVIDER], "plugins": [setup.plugin], "share": "disabled", "autoupdate": False}   # a path, to a folder
    if window:
        made["tool_output"] = {"max_bytes": window, "max_lines": window}
    if setup.agent is None:
        made.update({"instructions": [setup.brief], "permission": {"*": "allow", "webfetch": "deny", "external_directory": "deny"}})
    else:
        made.update({"default_agent": AGENT, "agents": {AGENT: dict(setup.agent)}})
    return json.dumps(made, sort_keys=True, indent=1)


def plugin(settings: dict[str, Any]) -> str:
    """OpenCode plugin applying poolhouse's tool classifier. ``settings["hook"]`` is the client command that asks the parent; a tool call it
    refuses, or cannot ask about, throws, which OpenCode returns to the model as the tool's error. It tells the parent it is loaded before the first
    request to the model, and adds to a tool's result what the lead sent. Where the settings give them: ``tools`` are the only tools offered to the
    model; ``fixed`` lists ``[text, replacement]``, each text taken out of the system prompt and the tools' descriptions, so every run starts with the same
    prompt; ``shell`` (``argv`` of a program that ends a command at its timeout, the ``program`` it runs the command with, and the ``default`` and
    ``most`` seconds) is what every shell command runs under, never in the background."""
    return harness_js.prelude(settings) + r'''const fileTools = ["read", "edit", "write"];
const quoted = (word) => "'" + String(word).replaceAll("'", "'\\''") + "'";
const stable = (text) => (cfg.fixed || []).reduce((out, [from, to]) => out.replaceAll(from, to), text);
function bounded(input) {
  const asked = Number(input.timeout);
  const seconds = asked > 0 ? Math.min(asked / 1000, cfg.shell.most) : cfg.shell.default;
  const words = [...cfg.shell.argv, seconds, cfg.shell.program, input.command];
  return {...input, command: words.map(quoted).join(" "), timeout: Math.ceil(seconds + 30) * 1000, background: false};
}
function added(result, said) {
  const base = result || {};
  const own = base.output === undefined ? [] : [{type: "text", text: typeof base.output === "string" ? base.output : JSON.stringify(base.output)}];
  const content = typeof base.content === "string" ? [{type: "text", text: base.content}] : Array.isArray(base.content) ? base.content : own;
  return {...base, content: [...content, {type: "text", text: said}]};
}
function before(event) {
  const input = event.input && typeof event.input === "object" ? event.input : {};
  const args = {...input};
  if (fileTools.includes(event.tool) && args.path && !args.file_path) args.file_path = args.path;
  const result = call("pre", {tool_name: names[event.tool] || event.tool, tool_input: args, cwd: cfg.root, tool_use_id: event.id});
  if (result.failed) throw new Error("poolhouse: tool policy failed; blocked");
  const answer = result.hookSpecificOutput || {};
  if (answer.permissionDecision === "deny") throw new Error("poolhouse: " + (answer.permissionDecisionReason || "blocked by policy"));
  if (cfg.shell && event.tool === "shell") event.input = bounded(input);
}
function after(event) {
  const result = call("post", {tool_use_id: event.id});
  if (result.failed) throw new Error("poolhouse: mutation checkpoint failed; stopped");
  const said = result.hookSpecificOutput?.additionalContext;
  if (!said) return;
  if (event.status === "error") event.error.message = event.error.message + "\n\n" + said;
  else event.result = added(event.result, said);
}
let announced = false;
function context(event) {
  if (!announced) {
    if (call("post", {loaded: true}).failed) throw new Error("poolhouse: the session bridge did not answer; stopped");
    announced = true;
  }
  for (const part of event.system || []) if (typeof part.text === "string") part.text = stable(part.text);
  for (const [name, tool] of Object.entries(event.tools || {})) {
    if (cfg.tools && !cfg.tools.includes(name)) delete event.tools[name];
    else if (typeof tool.description === "string") tool.description = stable(tool.description);
  }
}
export default {
  id: "poolhouse",
  setup: async (ctx) => {
    await ctx.tool.hook("execute.before", before);
    await ctx.tool.hook("execute.after", after);
    await ctx.session.hook("context", context);
  },
};
'''


SERVE = """\
PATH="${1%/*}:$PATH"
export PATH
# The server's warnings and errors (a plugin it could not load among them) go to the run's error output; `run` alone writes the events.
"$1" serve --print-logs --log-level warn --hostname 127.0.0.1 --port "$2" >&2 &
server=$!
# It listens before it is ready and answers 503 until it is, and `run` asks it only once: `run` starts when it says it is ready. The password
# reaches curl on its input, never on a command line.
tries=0
until [ "$(printf 'user = "opencode:%s"\\n' "$OPENCODE_PASSWORD" |
  /usr/bin/curl -q -s -o /dev/null -w '%{http_code}' --noproxy '*' --max-time 5 -K - "http://127.0.0.1:$2/api/info")" = 200 ]; do
  tries=$((tries + 1))
  if [ "$tries" -gt 300 ]; then echo "poolhouse: the OpenCode server did not start" >&2; kill "$server" 2>/dev/null; exit 1; fi
  sleep 0.1
done
binary=$1 port=$2
shift 2
"$binary" run --server "http://127.0.0.1:$port" "$@"
status=$?
kill "$server" 2>/dev/null
exit "$status"
"""


def argv_of(binary: str, port: int, alias: str, extra: Sequence[str]) -> tuple[str, ...]:
    """The command line of one run. ``opencode run`` has no mode without a server, and its own private one (`--standalone`) listens on a port the
    operating system picks, which the box (that names the ports it opens) cannot know. So the confined process starts ``opencode serve`` on ``port``,
    the one the launcher chose and opened, and runs ``opencode run --server`` against it, behind the password the bridge token gives both. The
    binary's own folder leads ``PATH``: the ripgrep kept beside it is the one the search tools run."""
    return ("/bin/sh", "-c", SERVE, "opencode", binary, str(port), "--format", "json", "--model", f"{PROVIDER}/{alias}", *extra)


def rendering(context: isolation.Context, setup: Setup, command: Sequence[str], settings: Mapping[str, Any],
              files: Mapping[str, str]) -> isolation.Rendering:
    """What a run of ``command`` (the binary and ``run``'s own words) needs written in its home: the config of ``setup`` (its bridge's URL, token,
    plugin and lead server are this launch's), the plugin with ``settings`` over the client command and the stage, and ``files``."""
    binary, *extra = command
    alias, port, server = setup.served[1], free_port(), context.lead_server()
    made = Setup((context.base_url, alias, setup.served[2]), context.token, str(context.file(PLUGIN)),
                 [server["command"], *server["args"]], families.for_model_id(alias).think_kwargs(False), setup.brief, setup.agent)
    written = {f".opencode-config/{CONFIG}": config(made),
               f"{PLUGIN}/{ENTRY}": plugin({"hook": context.client_argv("hook"), "root": context.stage, **settings}), **files}
    return isolation.Rendering(argv_of(binary, port, alias, extra), written, listen=(port,))


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
    window = served[2]
    reported: list[str] = []
    how = harnessing.Harness("opencode", command[0], served, say, observe=lambda event, _payload, _said: reported.append(event))
    opened: list[harnessing.Session] = []

    def render(context: isolation.Context) -> isolation.Rendering:
        brief = str(context.file(f".opencode-config/{BRIEF}"))
        return rendering(context, Setup((context.base_url, served[1], window), context.token, "", (), brief=brief), command, {},
                         {f".opencode-config/{BRIEF}": opened[0].brief})

    try:
        with harnessing.opened(args, "opencode", served, say) as run:
            opened.append(run)
            with harnessing.confined(args, run, how, render) as confinement:
                say(f"permissions {args.permissions}; mode {args.execution_mode}; working on a copy of {run.cwd}, in a box the operating system "
                    "holds; OpenCode's tool calls pass through poolhouse's classifier")
                status = int(runner(confinement))
                if confinement.process is not None and not reported:
                    say(f"error: {UNGUARDED}")
                    confinement.close(publish=False, keep_patch=True)
                    return status or 2
                return status
    except (OSError, RuntimeError, ValueError) as why:
        say(f"error: {why}")
        return 2


if __name__ == "__main__":
    raise SystemExit(launch())
