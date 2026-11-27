"""OpenCode on a model this machine serves: ``poolhouse-opencode MODEL`` opens it on the project folder, and ``-- message words`` runs ``opencode run``.

In place it is the ``opencode`` on PATH (else the kept release) with the person's own configuration and one config file of this run adding the served
model; no configuration of the person's is written.

With ``--sandbox``, which runs ``opencode run MESSAGE`` only, the launcher proves a confinement (`poolhouse.isolation`) and starts the pinned release
(`poolhouse.opencode_install`, fetched once) inside it with a config of its own in the agent home, so the person's ``~/.config/opencode`` is never
read. The provider is the bridge's URL with its token, minted for the launch; OpenCode's own network traffic (update checks, the model list, LSP
downloads) is switched off, and its server listens on a Unix socket on macOS, where the box binds no address (`through_socket`). A plugin written
read-only in a folder of its own asks the parent's classifier over the session bridge before each tool call and after it, as Pi's extension does.
OpenCode starts without a plugin it cannot load and says so only in a warning of its server, so the server's warnings go to the run's error output (a
delegate's log), and a run whose plugin never reported publishes nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from poolhouse import (
    coding_policy,
    harness_js,
    harnessing,
    inplace,
    isolation,
    lead_bridge,
    opencode_install,
)
from poolhouse.claude import DEFAULT_PORT, DEFAULT_SLOTS, alias_of
from poolhouse.client import families
from poolhouse.guard import harnesses
from poolhouse.isolation.serving import IDLE_SECONDS
from poolhouse.log import say
from poolhouse.serve import provenance

__all__ = ["AGENT", "INSTRUCTIONS", "UNGUARDED", "Setup", "config", "launch", "plugin", "rendering"]

INSTRUCTIONS = harnesses.RECORDS["opencode"]
"""The instruction and settings names OpenCode reads from a project (`guard.harnesses`)."""
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
    """What one run's config is made from: the server's URL, the model's name and its window (``served``) and its token; the path of the plugin's
    folder; the command of the MCP server `lead` (its tools reach OpenCode as `lead_ask` and `lead_note`); the model's thinking switches; either
    the launcher's brief, by path, or an ``agent`` of the run's own (its system prompt and its permission rules), which every request then uses; and
    the name the config gives the ``provider``."""

    served: tuple[str, str, int]
    token: str
    plugin: str
    lead: Sequence[str]
    thinking: Mapping[str, Any] | None = None
    brief: str = ""
    agent: Mapping[str, Any] | None = None
    provider: str = PROVIDER


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
        "model": f"{setup.provider}/{alias}", "small_model": f"{setup.provider}/{alias}",
        "provider": {setup.provider: {"npm": "@ai-sdk/openai-compatible", "name": "poolhouse",
                                      "options": {"baseURL": base_url.rstrip("/") + "/v1", "apiKey": setup.token}, "models": {alias: model}}},
        "enabled_providers": [setup.provider], "plugins": [setup.plugin], "share": "disabled", "autoupdate": False}   # a path, to a folder
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


SERVER_PORT = 4096
"""The port in the URL of a run's own server. In a box on macOS nothing binds it: the server listens on a Unix socket (`through_socket`) and the URL
is only its name. A box on Linux has a loopback of its own, where the server binds it."""
SOCKET_PRELOAD = "bin/poolhouse-server.js"
"""The file of `through_socket` in the agent home, under ``bin``, which the box holds read-only."""

THROUGH = r'''import http from "node:http";
delete process.env.BUN_INSPECT_PRELOAD;   // for this process only: nothing it starts loads this
const url = `http://127.0.0.1:${port}/`;
const listen = http.Server.prototype.listen;
http.Server.prototype.listen = function (options, ...rest) {
  if (options?.port !== port) return listen.call(this, options, ...rest);
  this.address = () => ({address: "127.0.0.1", family: "IPv4", port});
  return listen.call(this, {path: socket}, ...rest);
};
const reach = globalThis.fetch;
globalThis.fetch = Object.assign((input, init) => {
  const to = String(input?.url ?? input);
  return reach(input, to.startsWith(url) ? {...init, unix: socket} : init);
}, reach);
'''


def through_socket(path: str) -> str:
    """What both OpenCode processes of a run in a box that binds no address load before their own code: the server asked to listen on `SERVER_PORT`
    listens on the Unix socket ``path`` instead, and a request for that URL goes there. OpenCode takes no socket for ``--hostname``, so this is the
    one thing of it that is changed. A release that did not load it would try to bind the port, which the box refuses, so the run would not start."""
    return f"const socket = {json.dumps(path)};\nconst port = {SERVER_PORT};\n" + THROUGH


SERVE = """\
PATH="${1%/*}:$PATH"
export PATH
# $3 is the Unix socket the server listens on where the box binds no address, and $4 what both OpenCode processes load to use it.
if [ -n "$3" ]; then BUN_INSPECT_PRELOAD=$4; export BUN_INSPECT_PRELOAD; fi
# The server's warnings and errors (a plugin it could not load among them) go to the run's error output; `run` alone writes the events.
"$1" serve --print-logs --log-level warn --hostname 127.0.0.1 --port "$2" >&2 &
server=$!
# It listens before it is ready and answers 503 until it is, and `run` asks it only once: `run` starts when it says it is ready. The password
# reaches curl on its input, never on a command line.
tries=0
until [ "$(printf 'user = "opencode:%s"\\n' "$OPENCODE_PASSWORD" |
  /usr/bin/curl -q -s -o /dev/null -w '%{http_code}' --noproxy '*' ${3:+--unix-socket "$3"} --max-time 5 -K - "http://127.0.0.1:$2/api/info")" = 200 ]; do
  tries=$((tries + 1))
  if [ "$tries" -gt 300 ]; then echo "poolhouse: the OpenCode server did not start" >&2; kill "$server" 2>/dev/null; exit 1; fi
  sleep 0.1
done
binary=$1 port=$2
shift 4
"$binary" run --server "http://127.0.0.1:$port" "$@"
status=$?
kill "$server" 2>/dev/null
exit "$status"
"""


def argv_of(binary: str, alias: str, extra: Sequence[str], socket: str = "", preload: str = "") -> tuple[str, ...]:
    """The command line of one run. ``opencode run`` has no mode without a server, and its own private one (`--standalone`) listens on a port the
    operating system picks, which the box cannot grant. So the confined process starts ``opencode serve`` on `SERVER_PORT` and runs ``opencode run
    --server`` against it, behind the password the bridge token gives both; with ``socket`` and ``preload`` (the file of `through_socket`) the two
    meet on that Unix socket and no port is bound. The binary's own folder leads ``PATH``: the ripgrep kept beside it is the one the search tools
    run."""
    return ("/bin/sh", "-c", SERVE, "opencode", binary, str(SERVER_PORT), socket, preload, "--format", "json", "--model", f"{PROVIDER}/{alias}",
            *extra)


def rendering(context: isolation.Context, setup: Setup, command: Sequence[str], settings: Mapping[str, Any],
              files: Mapping[str, str]) -> isolation.Rendering:
    """What a run of ``command`` (the binary and ``run``'s own words) needs written in its home: the config of ``setup`` (its bridge's URL, token,
    plugin and lead server are this launch's), the plugin with ``settings`` over the client command and the stage, and ``files``."""
    binary, *extra = command
    alias, server = setup.served[1], context.lead_server()
    made = Setup((context.base_url, alias, setup.served[2]), context.token, str(context.file(PLUGIN)),
                 [server["command"], *server["args"]], families.for_model_id(alias).think_kwargs(False), setup.brief, setup.agent)
    written = {f".opencode-config/{CONFIG}": config(made),
               f"{PLUGIN}/{ENTRY}": plugin({"hook": context.client_argv("hook"), "root": context.stage, **settings}), **files}
    preload = ""
    if context.server_socket:
        written[SOCKET_PRELOAD] = through_socket(context.server_socket)
        preload = str(context.file(SOCKET_PRELOAD))

    def noted(lacks: str) -> dict[str, str]:
        """The config again, with what the box lacks at the end of the agent's own system prompt."""
        agent = {**made.agent, "system": f"{made.agent['system']}{lacks}\n"}
        return {f".opencode-config/{CONFIG}": config(replace(made, agent=agent))}

    return isolation.Rendering(argv_of(binary, alias, extra, context.server_socket, preload), written, serves=True,
                               noted=noted if made.agent else None)


OWN = ("$schema", "model", "small_model", "provider", "tool_output", "instructions")
"""The keys of `config` a run in place adds to the person's own configuration: the served model and the workspace brief."""
UNCHANGING = {"edit": "deny", "bash": "deny", "shell": "deny"}
STANCES = {coding_policy.READ_ONLY: UNCHANGING, coding_policy.PLAN: UNCHANGING, coding_policy.YOLO: {"*": "allow"}}
"""OpenCode's own permission rules for each `harnessing.stance` of a run in place that changes the person's."""
UNREAD = dict.fromkeys(("OPENCODE_CONFIG_PROJECT_DISABLE", "OPENCODE_DISABLE_PROJECT_CONFIG"), "true")
"""The switches that keep OpenCode from reading the project's configuration and plugins: an agent's rules there come after this run's, so a run asked
to change nothing reads none of them."""
INLINE = "OPENCODE_CONFIG_CONTENT"
"""The variable whose text OpenCode reads as configuration after the person's and the project's, so what it names neither can replace."""
SCREEN_VALUES = ("--prompt", "--session", "-s", "--server", "--log-level")
"""The flags of OpenCode's screen that take the next word as their value, which is then no message."""


def printing(extra: Sequence[str]) -> bool:
    """Whether the arguments after ``--`` hold a message, which OpenCode answers once; otherwise it opens its own screen."""
    words = iter(extra)
    for word in words:
        if word in SCREEN_VALUES:
            next(words, None)
        elif not word.startswith("-"):
            return True
    return False


def _beside(theirs: str, made: dict[str, Any]) -> dict[str, Any]:
    """``made`` over the inline configuration the person's own environment holds (``theirs``), which is read from the same variable."""
    if not theirs.strip():
        return made
    try:
        held = json.loads(theirs)
    except ValueError:
        held = None
    if not isinstance(held, dict):
        raise ValueError(f"the {INLINE} of this environment is not a JSON object, so the served model cannot be added to it")
    both = {**held, **made, "provider": {**held.get("provider", {}), **made["provider"]}}
    if "instructions" in made:
        both["instructions"] = [*held.get("instructions", []), *made["instructions"]]
    return both


def _own_server(binary: str, rest: Sequence[str]) -> list[str]:
    """``--standalone`` where ``binary`` has a background service (its help names the flag): that service is a process of its own, started before
    this run or outliving it, and it reads neither this run's inline config nor its key. A server the caller names with ``--server`` is theirs."""
    if any(word == "--standalone" or word.split("=")[0] == "--server" for word in rest):
        return []
    try:
        said = subprocess.run([binary, "--help"], capture_output=True, stdin=subprocess.DEVNULL, timeout=IDLE_SECONDS, check=False).stdout
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{binary} did not answer --help, so what its release does cannot be told") from None
    return ["--standalone"] if b"--standalone" in said else []


def direct(context: inplace.Context, command: Sequence[str], stance: str, brief: str) -> inplace.Rendering:
    """A run in place of ``command`` (the binary, then the words after ``--``): the screen, or ``run`` with a message, on a server of its own where
    the release would else use a background one. The served model is added by inline config (`INLINE`) under a provider name made for this run, so a
    project's config can neither move its address nor name it; the screen takes the model from there, since only ``run`` has ``--model``. The key is
    only in a variable named for this run, which the inline config refers to, so no text a config can read back holds it. A run asked to change
    nothing also reads none of the project's configuration (`UNREAD`)."""
    binary, *extra = command
    variable = inplace.key_variable()
    setup = Setup((context.base_url, context.alias, context.window), f"{{env:{variable}}}", str(context.file(PLUGIN)), (),
                  families.for_model_id(context.alias).think_kwargs(False), str(context.file(BRIEF)), provider=f"{PROVIDER}-{secrets.token_hex(8)}")
    made = {key: value for key, value in json.loads(config(setup)).items() if key in OWN and (key != "instructions" or brief)}
    env = {variable: context.key}
    if rules := STANCES.get(stance):
        made["permission"] = rules
        env["OPENCODE_PERMISSION"] = json.dumps(rules)
        if rules is UNCHANGING:
            env.update(UNREAD)
    env[INLINE] = json.dumps(_beside(os.environ.get(INLINE, ""), made), sort_keys=True)
    words = ["run"] if printing(extra) else []
    rest = extra[1:] if extra[:1] == ["run"] else extra
    model = ["--model", f"{setup.provider}/{context.alias}"] if words else []
    return inplace.Rendering((binary, *words, *_own_server(binary, rest), *model, *rest), {BRIEF: brief} if brief else {}, env)


def launch(argv: Sequence[str] | None = None, *, say: Callable[[str], None] = say,
           run_opencode: Callable[[Any], int] | None = None, session_factory: Callable[..., Any] | None = None) -> int:
    """Run OpenCode against a leased local model; the words after ``--`` are the message and OpenCode's own run options."""
    words = list(sys.argv[1:] if argv is None else argv)
    ours, extra = (words[:words.index("--")], words[words.index("--") + 1:]) if "--" in words else (words, [])
    args = harnessing.parser("opencode", "OpenCode", DEFAULT_PORT, DEFAULT_SLOTS).parse_args(ours)
    provenance.told(args.lease_for)
    args.session_factory = session_factory
    binary = args.opencode or harnessing.binary_for("opencode", pinned=args.sandbox)
    if not binary:
        try:
            binary = opencode_install.ensure()
        except opencode_install.InstallFailed as why:
            say(f"error: {why}")
            return 2
    if not harnessing.checked(args, "opencode", say, interactive=not printing(extra)):
        return 2
    runner = run_opencode or harnessing.terminal_runner
    return harnessing.serve_and_run(args, "opencode", say, lambda *got: alias_of(*got), lambda served: _run(args, [binary, *extra], served, say, runner))


def _run(args: argparse.Namespace, command: Sequence[str], served: tuple[str, str, int], say: Callable[[str], None],
         runner: Callable[[Any], int]) -> int:
    """Start ``command`` (``opencode`` and the message) in place or in a proved confinement and hand it to the runner."""
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
            def in_place(context: inplace.Context) -> inplace.Rendering:
                return direct(context, command, harnessing.stance(args, printing=printing(command[1:])), opened[0].brief)

            with inplace.started(args, run, how, (render, in_place)) as confinement:
                status = int(runner(confinement))
                if args.sandbox and confinement.process is not None and not reported:
                    say(f"error: {UNGUARDED}")
                    confinement.close(publish=False, keep_patch=True)
                    return status or 2
                return status
    except (OSError, RuntimeError, ValueError) as why:
        say(f"error: {why}")
        return 2


if __name__ == "__main__":
    raise SystemExit(launch())
