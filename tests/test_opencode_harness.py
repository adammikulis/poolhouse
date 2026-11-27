"""OpenCode runs through the one door (`isolation.prepare` then `Confinement.popen`), on the bridge's URL and token, with poolhouse's plugin, and its
binary is fetched with no install step."""
from __future__ import annotations

import hashlib
import http.server
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from confined_kit import repository

from poolhouse import (
    coding,
    coding_policy,
    delegate_confined,
    harnessing,
    isolation,
    opencode,
    opencode_install,
)
from poolhouse.http import Server

pytest_plugins = ["tests.confined_kit"]

HARNESS = '''#!{python}
# NOT A MODEL and NOT OPENCODE: it holds the port as `serve`; as `run` it writes one file and, only when `hooked`, calls the parent as the plugin does.
import json, os, socket, subprocess, sys
args = sys.argv[1:]
if args[0] == "serve":
    held = socket.socket()
    held.bind(("127.0.0.1", int(args[args.index("--port") + 1])))
    held.listen()
    while True:
        held.accept()[0].close()
plugin = open(json.load(open(os.environ["OPENCODE_CONFIG"]))["plugin"][0].removeprefix("file://")).read()
hook = json.loads(plugin.split("const cfg = ", 1)[1].split(";\\n", 1)[0])["hook"]
if {hooked}:
    subprocess.run([*hook, "post"], input=json.dumps({{"loaded": True}}), capture_output=True, text=True, check=True)
open("made.py", "w").write("x = 1\\n")
'''


@pytest.mark.usefixtures("confinable", "from_a_terminal")
def test_opencode_run_is_confined_offline_and_points_at_the_bridge(monkeypatch, tmp_path):
    seen = {}
    binary = tmp_path / "opencode"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    project = repository(tmp_path / "project")
    run = harnessing.Session(SimpleNamespace(name="coding-test", parent=None, managed_inbox=False), project, "trusted role brief")

    @contextmanager
    def opened(*_args, **_kwargs):
        yield run

    monkeypatch.setattr(opencode.harnessing, "binary_for", lambda _, **__: str(binary))
    monkeypatch.setattr(opencode, "alias_of", lambda *_: "local-model")
    monkeypatch.setattr(opencode.harnessing, "window_of", lambda _: 49152)
    monkeypatch.setattr(opencode.harnessing, "opened", opened)

    def run_opencode(confinement):
        seen["argv"], seen["env"] = list(confinement.argv), dict(confinement.env)
        seen["net"] = confinement.profile.policy.net
        seen["config"] = json.loads(Path(seen["env"]["OPENCODE_CONFIG"]).read_text())
        seen["plugin"] = Path(seen["env"]["OPENCODE_CONFIG"]).with_name(opencode.PLUGIN).read_text()
        return 0

    said = []
    assert opencode.launch(["--sandbox", "--on", "http://127.0.0.1:8080", "--project", str(project), "--", "fix it"], say=said.append,
                           run_opencode=run_opencode) == 0, said
    sh, flag, script, name, program, port, *rest = seen["argv"]
    assert (sh, flag, name, program) == ("/bin/sh", "-c", "opencode", str(binary.resolve()))
    assert rest == ["--format", "json", "--model", "poolhouse/local-model", "fix it"]
    assert '"$1" serve --hostname 127.0.0.1 --port "$2"' in script and 'run --server "http://127.0.0.1:$port"' in script and "--standalone" not in script
    assert seen["net"].listen == (int(port),) and len(seen["net"].ports) == 1, "the model bridge and the server the launcher chose, no other port"
    assert seen["env"]["OPENCODE_PASSWORD"] == provider_key(seen), "the server answers only to the bridge token"
    assert seen["env"]["OPENCODE_DISABLE_AUTOUPDATE"] == "true" and seen["env"]["OPENCODE_DISABLE_MODELS_FETCH"] == "true"
    assert seen["env"]["OPENCODE_DISABLE_PROJECT_CONFIG"] == "true", "a project's own config replaces neither the permissions nor the plugin"
    assert set(seen["env"]) <= isolation.allowed("opencode")
    provider = seen["config"]["provider"]["poolhouse"]
    assert provider["options"]["baseURL"].startswith("http://127.0.0.1:") and provider["options"]["baseURL"].endswith("/v1")
    assert not provider["options"]["baseURL"].startswith("http://127.0.0.1:8080"), "OpenCode reaches the bridge, never the server"
    assert provider["options"]["apiKey"] != "local" and len(provider["options"]["apiKey"]) >= 32, "the bridge's token, minted for the launch"
    assert seen["config"]["autoupdate"] is False and seen["config"]["share"] == "disabled"
    assert 'ctx.tool.hook("execute.before", before)' in seen["plugin"]
    assert "client.py" in seen["plugin"] and "poolhouse.harnesshook" not in seen["plugin"]


def provider_key(seen):
    return seen["config"]["provider"]["poolhouse"]["options"]["apiKey"]


def test_coding_agent_takes_opencode_by_name(monkeypatch, tmp_path):
    got = {}
    monkeypatch.setitem(coding.HARNESSES, "opencode", lambda argv, say, **options: got.update(argv=argv, options=options) or 0)
    project = tmp_path / "p"
    project.mkdir()
    assert coding.launch_coding_agent("m", coding_policy.Policy("guarded", "execute"), project, harness="opencode",
                                      harness_args=["fix it"], run_opencode=lambda confinement: 0) == 0
    assert got["argv"][-2:] == ["--", "fix it"] and "run_opencode" in got["options"]
    assert "opencode" in coding.HARNESSES


LOAD = """
async function load(path) {
  const made = (await import(path)).default, hooks = {};
  const hook = (domain) => ({hook: async (name, callback) => { hooks[`${domain}.${name}`] = callback; }});
  await made.setup({tool: hook("tool"), session: hook("session")});
  return {id: made.id, hooks};
}
"""
"""A stand-in for OpenCode's plugin host, NOT OPENCODE: it loads the module's default export and keeps the hooks it registers."""


def node_runs(tmp_path, script):
    """What ``script``, run by node after `LOAD`, printed as JSON."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required to load the plugin")
    driver = tmp_path / "driver.mjs"
    driver.write_text(LOAD + script)
    done = subprocess.run([node, str(driver)], capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def heard_by(tmp_path, answer):
    """A marked hook client that records what the plugin sent it and answers ``answer``."""
    record = tmp_path / "asked.jsonl"
    code = ("import sys; open(sys.argv[1], 'a').write(sys.argv[-1] + ' ' + sys.stdin.read() + chr(10)); "
            f"print({json.dumps(json.dumps(answer))})")
    return [sys.executable, "-c", code, str(record)], record


def test_opencode_plugin_is_a_plugin_of_this_release_that_denies_and_fails_closed(tmp_path):
    deny, record = heard_by(tmp_path, {"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": "read-only"}})
    for name, hook in (("deny", deny), ("allow", ["/bin/sh", "-c", "cat > /dev/null; echo '{}'", "sh"]), ("broken", ["/usr/bin/false"])):
        (tmp_path / f"{name}.mjs").write_text(opencode.plugin({"hook": hook, "root": str(tmp_path)}))
    seen = node_runs(tmp_path, """
const seen = {};
for (const name of ["deny", "allow", "broken"]) {
  const {id, hooks} = await load(`./${name}.mjs`), row = {id, hooks: Object.keys(hooks).sort()};
  try { hooks["tool.execute.before"]({tool: "write", id: "c1", input: {path: "x.py", content: "x"}}); row.before = "ok" } catch (e) { row.before = e.message }
  try { hooks["tool.execute.after"]({id: "c1", status: "completed", result: {}}); row.after = "ok" } catch (e) { row.after = e.message }
  try { hooks["session.context"]({system: [], tools: {}}); row.context = "ok" } catch (e) { row.context = e.message }
  seen[name] = row;
}
console.log(JSON.stringify(seen));
""")
    assert seen["deny"]["id"] == "poolhouse" and seen["deny"]["hooks"] == ["session.context", "tool.execute.after", "tool.execute.before"]
    assert seen["deny"]["before"] == "poolhouse: read-only" and seen["deny"]["after"] == "ok"
    assert (seen["allow"]["before"], seen["allow"]["after"], seen["allow"]["context"]) == ("ok", "ok", "ok")
    assert seen["broken"]["before"] == "poolhouse: tool policy failed; blocked" and "checkpoint failed" in seen["broken"]["after"]
    assert "did not answer" in seen["broken"]["context"], "no request goes to the model when the parent cannot be told the plugin is there"
    asked = record.read_text().splitlines()
    assert asked[0].startswith("pre ") and json.loads(asked[0][4:]) == {"tool_name": "Write", "tool_use_id": "c1", "cwd": str(tmp_path),
                                                                        "tool_input": {"path": "x.py", "content": "x", "file_path": "x.py"}}
    assert asked[1] == 'post {"tool_use_id":"c1"}' and asked[2] == 'post {"loaded":true}'
    assert not (tmp_path / "x.py").exists()


def test_the_opencode_plugin_adds_what_the_lead_sent_keeps_the_prompt_stable_and_offers_only_its_tools(tmp_path):
    said, _record = heard_by(tmp_path, {"hookSpecificOutput": {"additionalContext": "[PLUMBING] from your lead"}})
    settings = {"hook": said, "root": str(tmp_path), "tools": ["read"], "fixed": [["/tmp/stage-1", "."], ["/tmp/home-1", "$HOME"]]}
    (tmp_path / "said.mjs").write_text(opencode.plugin(settings))
    seen = node_runs(tmp_path, """
const {hooks} = await load("./said.mjs"), after = hooks["tool.execute.after"];
const text = {status: "completed", result: {content: "the file"}}, parts = {status: "completed", result: {content: [{type: "text", text: "the answer"}]}};
const bare = {status: "completed", result: {output: {n: 1}}}, failed = {status: "error", error: {message: "no such file"}};
for (const each of [text, parts, bare, failed]) after(each);
const context = {system: [{type: "text", text: "Working directory: /tmp/stage-1\\nPrefer /tmp/home-1/tmp"}],
  tools: {read: {description: "reads in /tmp/stage-1"}, webfetch: {description: "fetches"}, subagent: {description: "starts"}}};
hooks["session.context"](context);
console.log(JSON.stringify({text, parts, bare, failed, context}));
""")
    added = {"type": "text", "text": "[PLUMBING] from your lead"}
    assert seen["text"]["result"]["content"] == [{"type": "text", "text": "the file"}, added]
    assert seen["parts"]["result"]["content"] == [{"type": "text", "text": "the answer"}, added]
    assert seen["bare"]["result"] == {"output": {"n": 1}, "content": [{"type": "text", "text": '{"n":1}'}, added]}
    assert seen["failed"]["error"]["message"] == "no such file\n\n[PLUMBING] from your lead"
    assert seen["context"]["system"][0]["text"] == "Working directory: .\nPrefer $HOME/tmp"
    assert seen["context"]["tools"] == {"read": {"description": "reads in ."}}


def test_a_shell_command_the_plugin_bounds_ends_at_its_timeout_with_its_children_and_never_stays_in_the_background(tmp_path):
    bounded = tmp_path / "bounded.py"
    bounded.write_text(delegate_confined.BOUNDED)
    shell = {"argv": [sys.executable, "-I", str(bounded)], "program": "/bin/sh", "default": 2, "most": 3}
    (tmp_path / "bounded.mjs").write_text(opencode.plugin({"hook": ["/bin/sh", "-c", "cat > /dev/null", "sh"], "root": str(tmp_path), "shell": shell}))
    made = node_runs(tmp_path, """
const {hooks} = await load("./bounded.mjs");
const call = (input) => { const event = {tool: "shell", id: "c", input}; hooks["tool.execute.before"](event); return event.input; };
console.log(JSON.stringify([call({command: "echo 'it is' \\"$((1+1))\\"; exit 3"}),call({command: "sleep 30 & echo $! > left", timeout: 900000, background: true}),
  call({command: "sleep 30", timeout: 1000})]));
""")
    assert [(each["timeout"], each["background"]) for each in made] == [(32000, False), (33000, False), (31000, False)]
    runs = [subprocess.run(["/bin/sh", "-c", each["command"]], cwd=tmp_path, capture_output=True, text=True, timeout=20) for each in made]
    assert (runs[0].returncode, runs[0].stdout) == (3, "it is 2\n")
    assert runs[1].returncode == 0
    with pytest.raises(ProcessLookupError):
        for _ in range(100):
            os.kill(int((tmp_path / "left").read_text()), 0)
            time.sleep(0.05)
    assert runs[2].returncode == 124 and "ended at its timeout of 1s" in runs[2].stdout


def test_opencode_names_are_in_the_closed_records():
    from poolhouse.guard import harnesses

    assert ".opencode" in harnesses.names() and "opencode.json" in harnesses.names()
    assert harnesses.RECORDS["opencode"] == opencode.INSTRUCTIONS


@pytest.fixture
def releases(monkeypatch, tmp_path):
    """A local release feed that serves the two pinned releases (real zips with a marked file in each) with the digests the feed reports."""
    monkeypatch.setattr(opencode_install, "asset_name", lambda: "opencode-test-arm64.zip")
    monkeypatch.setattr(opencode_install, "ripgrep_asset_name", lambda: "ripgrep-test-arm64.zip")
    bodies = {}
    for asset, member, said in (("opencode-test-arm64.zip", "opencode", "2.4.2"), ("ripgrep-test-arm64.zip", "ripgrep-x/rg", "rg")):
        made = io.BytesIO()
        with zipfile.ZipFile(made, "w") as archive:
            archive.writestr(member, f"#!/bin/sh\necho {said} NOT THE REAL PROGRAM\n")
        bodies[asset] = made.getvalue()
    state = {"digest": {name: hashlib.sha256(body).hexdigest() for name, body in bodies.items()}, "asked": []}

    class Feed(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            state["asked"].append(self.path)
            name = self.path.rsplit("/", 1)[-1]
            if name in bodies:
                reply = bodies[name]
            else:
                asset = "ripgrep-test-arm64.zip" if self.path.startswith("/rg/") else "opencode-test-arm64.zip"
                reply = json.dumps({"tag_name": name, "assets": [
                    {"name": asset, "size": len(bodies[asset]), "digest": "sha256:" + state["digest"][asset],
                     "browser_download_url": f"http://127.0.0.1:{self.server.server_address[1]}/{asset}"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

    server = Server(("127.0.0.1", 0), Feed)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setattr(opencode_install, "RELEASE", f"{base}/oc/")
    monkeypatch.setattr(opencode_install, "RIPGREP_RELEASE", f"{base}/rg/")
    yield state
    server.shutdown()


def older_and_path_copies(tmp_path, monkeypatch):
    """A kept copy of an older release and an ``opencode`` on PATH: neither is the pinned one."""
    old = opencode_install.root() / "v2.4.1" / "opencode"
    old.parent.mkdir(parents=True)
    other = tmp_path / "bin" / "opencode"
    other.parent.mkdir()
    for each in (old, other):
        each.write_text("#!/bin/sh\necho OTHER\n")
        each.chmod(0o755)
    monkeypatch.setenv("PATH", f"{other.parent}{os.pathsep}{os.environ['PATH']}")


@pytest.mark.usefixtures("loopback_net")
def test_exactly_the_pinned_release_and_its_ripgrep_are_fetched_once_and_no_other_copy_stands_in(releases, tmp_path, monkeypatch):
    assert opencode_install.VERSION == "v2.4.2"
    older_and_path_copies(tmp_path, monkeypatch)
    assert opencode_install.kept() == "" and harnessing.binary_for("opencode", pinned=True) == "", "a sandboxed run and a delegate"
    binary = Path(opencode_install.ensure())
    assert binary.name == "opencode" and binary.parts[-3:-1] == ("v2.4.2", "bin") and binary.stat().st_mode & 0o100
    assert subprocess.run([str(binary)], capture_output=True, text=True, check=True).stdout.startswith("2.4.2")
    assert subprocess.run([str(binary.with_name("rg"))], capture_output=True, text=True, check=True).stdout.startswith("rg ")
    asked = list(releases["asked"])
    assert asked[0] == "/oc/v2.4.2" and f"/rg/{opencode_install.RIPGREP}" in asked and not [each for each in asked if "per_page" in each]
    assert opencode_install.ensure() == str(binary) and releases["asked"] == asked, "a kept copy is taken without asking the network"
    assert harnessing.binary_for("opencode", pinned=True) == str(binary)
    monkeypatch.setattr(opencode_install, "VERSION", "v9.9.9")
    assert harnessing.binary_for("opencode", pinned=True) == "" and Path(opencode_install.ensure()).parts[-3] == "v9.9.9", "a later pin takes effect"


@pytest.mark.usefixtures("loopback_net")
def test_a_program_still_being_copied_is_never_taken_for_the_pinned_one(releases, monkeypatch):
    seen = []
    copy = shutil.copy2

    def watched(source, target):
        copy(source, target)
        seen.append(opencode_install.kept())

    monkeypatch.setattr(opencode_install.shutil, "copy2", watched)
    assert opencode_install.ensure() and seen == ["", ""], "neither program counts until it is whole and in place"
    assert releases["asked"]


@pytest.mark.usefixtures("loopback_net")
def test_a_download_that_does_not_match_its_digest_installs_nothing(releases):
    releases["digest"]["opencode-test-arm64.zip"] = "0" * 64
    with pytest.raises(opencode_install.InstallFailed, match="digest"):
        opencode_install.ensure()
    assert opencode_install.kept() == ""


@pytest.mark.usefixtures("confinable", "from_a_terminal")
@pytest.mark.parametrize(("hooked", "status"), [(False, 2), (True, 0)])
def test_a_launch_whose_plugin_never_reported_publishes_nothing_and_says_so(monkeypatch, tmp_path, hooked, status):
    binary = tmp_path / "opencode-NOT-OPENCODE"
    code = HARNESS.format(python=os.path.realpath(sys.executable), hooked=hooked)
    binary.write_text(code)
    binary.chmod(0o755)
    project = repository(tmp_path / "project")
    run = harnessing.Session(SimpleNamespace(name="coding-test", parent=None, managed_inbox=False), project, "trusted role brief")

    @contextmanager
    def opened(*_args, **_kwargs):
        yield run

    monkeypatch.setattr(opencode.harnessing, "binary_for", lambda _, **__: str(binary.resolve()))
    monkeypatch.setattr(opencode, "alias_of", lambda *_: "local-model")
    monkeypatch.setattr(opencode.harnessing, "window_of", lambda _: 49152)
    monkeypatch.setattr(opencode.harnessing, "opened", opened)
    said = []
    assert opencode.launch(["--sandbox", "--on", "http://127.0.0.1:8080", "--project", str(project), "--", "fix it"], say=said.append) == status, said
    assert (f"error: {opencode.UNGUARDED}" in said) is not hooked
    assert (project / "made.py").exists() is hooked, said

