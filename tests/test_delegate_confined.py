"""A delegate in the confinement: `poolhouse-delegate start` runs it there, read-only or editing, and an edit publishes plain source.

The confinement is real here: the stage, both bridges, the proof and the Seatbelt profile, the board (a real node), git, the project, the config and
plugin settings the delegate writes and the program that ends a shell command at its timeout. The one thing that is not real is OpenCode and the
model behind it: a marked program (`HARNESS`, NOT A MODEL and NOT OPENCODE) stands in the harness's place inside the profile, prints the events
``opencode run --format json`` prints and calls the parent's hooks as poolhouse's plugin does, the model side being the kit's `CANNED-NOT-A-MODEL`
server. The plugin itself is run by node in tests/test_opencode_harness.py. The run with OpenCode on a real model is in the report.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import sys
import threading
import time
from dataclasses import replace
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from delegate_kit import agent_names, said, started
from harness_kit import checkouts

from poolhouse import (
    delegate,
    delegate_confined,
    delegate_run,
    delegate_store as store,
    harnessing,
    home,
    isolation,
    lead_bridge,
    opencode,
)
from poolhouse.board import place
from poolhouse.isolation import confinement as choke_point, session_verbs
from poolhouse.isolation.bounds import PASSED
from poolhouse.isolation.refusals import SENTENCES, IsolationRefused
from tests import isolation_kit as kit
from tests.sandbox_kit import AbsentBackend

pytest_plugins = ["node_kit", "delegate_kit", "tests.confined_kit"]
CANNED = "[CANNED RESPONSE, NOT A MODEL]"
HARNESS = '''#!{python}
# NOT A MODEL and NOT OPENCODE: a program standing in for the harness inside the real confinement. As `serve` it only says it is ready; as `run` it does
# what a delegate's model would do with its tools (writes files, runs commands, asks its lead, tries what it must not), calls the parent's hooks as
# the plugin does and prints the events the real harness prints. Every word it says is marked.
import json, os, socket, subprocess, sys, time

CANNED = "[CANNED RESPONSE, NOT A MODEL]"
args = sys.argv[1:]
if args[0] == "serve":
    held = socket.socket()
    held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    held.bind(("127.0.0.1", int(args[args.index("--port") + 1])))
    held.listen()
    while True:
        connection = held.accept()[0]
        connection.recv(65536)
        connection.sendall(b"HTTP/1.1 200 OK\\r\\nContent-Length: 0\\r\\nConnection: close\\r\\n\\r\\n")   # ready, as OpenCode's server says once it is
        connection.close()
task = sys.stdin.read()
config = json.load(open(os.environ["OPENCODE_CONFIG"]))
plugin = open(os.path.join(config["plugins"][0], "index.js")).read()
cfg = json.loads(plugin.split("const cfg = ", 1)[1].split(";\\n", 1)[0])
server = config["mcp"]["lead"]["command"]
calls = [0]


def event(kind, **row):
    print(json.dumps({{"type": kind, "timestamp": 1, "sessionID": "s", **row}}), flush=True)


def hook(which, payload):
    done = subprocess.run([*cfg["hook"], which], input=json.dumps(payload), capture_output=True, text=True)
    return json.loads(done.stdout or "{{}}"), (done.stdout + done.stderr).strip()


def step(text=""):
    event("step_start", part={{}})
    if text:
        event("text", part={{"text": text}})


def finish(reason="tool-calls"):
    event("step_finish", part={{"reason": reason, "tokens": {{"input": 10, "output": 1, "reasoning": 1, "cache": {{"read": 7, "write": 0}}}}}})


def asked(tool, **arguments):
    calls[0] += 1
    name = {{"shell": "Bash", "edit": "Edit", "write": "Write", "read": "Read"}}.get(tool, tool)
    answer, _ = hook("pre", {{"tool_name": name, "tool_input": arguments, "cwd": cfg["root"], "tool_use_id": str(calls[0])}})
    return str(calls[0]), answer["hookSpecificOutput"]["permissionDecision"]


def used(tool, call, output="ran"):
    heard = hook("post", {{"tool_use_id": call}})[1]
    event("tool_use", part={{"tool": tool, "state": {{"status": "completed", "input": {{}}, "output": output}}}})
    return heard


def tool(name, **arguments):
    call, _decision = asked(name, **arguments)
    return used(name, call)


def shell(command, **more):
    call, decision = asked("shell", command=command, **more)
    if decision == "deny":
        return "denied"
    seconds = min(more["timeout"] / 1000, cfg["shell"]["most"]) if "timeout" in more else cfg["shell"]["default"]
    done = subprocess.run([*cfg["shell"]["argv"], str(seconds), cfg["shell"]["program"], command], capture_output=True, text=True)
    used("shell", call, done.stdout)
    return f"{{done.returncode}} {{done.stdout}}"


def ended(text):
    step(text)
    finish("stop")
    sys.exit(0)


if "mode:unguarded" not in task:
    hook("post", {{"loaded": True}})
if "mode:reads" in task:
    step()
    written = "refused"
    try:
        open("planted.py", "w").write("x = 1\\n")
        written = "WRITTEN"
    except OSError:
        pass
    shelled = subprocess.run(["/bin/sh", "-c", "echo x > shelled.txt"], capture_output=True, check=False).returncode != 0
    edit = asked("edit", file_path=cfg["root"] + "/planted.py")[1]
    heard = tool("read", file_path=cfg["root"] + "/source.py")
    found = subprocess.run(["rg", "--version"], capture_output=True, text=True).stdout.strip()
    finish()
    shown = open("source.py").read() if os.path.exists("source.py") else "nothing"
    ended(f"{{CANNED}} write={{written}} | shell-refused={{shelled and not os.path.exists('shelled.txt')}} | edit={{edit}}| heard={{heard}} | rg={{found}} | file={{shown}} | shell={{'shell' in cfg}} | "
          f"tools={{','.join(cfg['tools'])}} | permissions={{json.dumps(config['agents'][config['default_agent']]['permissions'])}}")
os.makedirs("src", exist_ok=True)
if "mode:shell-writes-nothing" in task:
    step()
    shell("echo x > .venv/planted")
    finish()
    ended(CANNED + " done and landed")
if "mode:shell-reads" in task:
    step()
    shell("git diff --stat && python3 -m pytest -q tests/test_app.py")
    finish()
    ended(CANNED + " the tests pass")
if "mode:shell-slow" in task:
    step()
    got = shell("sleep 5; echo slept", timeout=60000)
    finish()
    ended(CANNED + " waited for its command: " + got)
if "mode:shell-overdue" in task:
    step()
    began = time.time()
    got = shell("(sleep 90; touch late) & echo $! > child; sleep 90", timeout=2000)
    finish()
    ended(f"{{CANNED}} {{got}} after {{int(time.time() - began)}}s child={{open('child').read().strip()}}")
if "mode:settings-only" in task:
    open("opencode.json", "w").write("{{}}\\n")
    step()
    finish()
    ended(CANNED + " done and landed")
if "mode:edits-nothing" in task:
    step()
    tool("edit", file_path=cfg["root"] + "/src/none.py")
    finish()
    ended(CANNED + " done and landed")
open("src/feature.py", "w").write("def feature():\\n    return 1\\n")
if "mode:clean" in task:
    ended(CANNED + " done")
if "mode:errors" in task:
    step(CANNED + " half way")
    finish()
    event("error", error={{"type": "provider", "message": "the model server went away"}})
    sys.exit(1)
if "mode:dies" in task:
    step(CANNED + " half way")
    finish()
    sys.exit(3)
if "mode:cut-short" in task:
    step(CANNED + " half way")
    finish("length")
    sys.exit(0)
if "mode:endless" in task:
    for _ in range(50):
        step(CANNED + " half way")
        tool("read", file_path=cfg["root"] + "/src/feature.py")
        finish()
        time.sleep(0.2)
    ended(CANNED + " NOT ENDED")
if "mode:unguarded" in task:
    step(CANNED + " half way")
    event("tool_use", part={{"tool": "write", "state": {{"status": "completed"}}}})
    finish()
    time.sleep(20)
    ended(CANNED + " NOT ENDED")
if "mode:hoards" in task:
    step(CANNED + " half way")
    held = []
    for _ in range(48):
        held.append(os.urandom(16 << 20))
        time.sleep(0.05)
    time.sleep(15)
    ended(CANNED + " NOT ENDED")
if "mode:shell-hangs" in task:
    step(CANNED + " asked for a command and then nothing")
    asked("shell", command="sleep 120", timeout=2000)
    time.sleep(120)
if "mode:stalls" in task:
    step(CANNED + " about to stall")
    time.sleep(120)
if "mode:orphan" in task:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(100)"])
    ended(CANNED + " child=" + str(child.pid))
if "mode:patient" in task:
    import http.client
    step()
    answer = subprocess.run([*server[:-1], "workspace", "ask", "--question", "which?"], capture_output=True, text=True)
    options = config["provider"]["poolhouse"]["options"]
    model = http.client.HTTPConnection(options["baseURL"].split("//")[1].split("/")[0])
    model.request("POST", "/v1/chat/completions", json.dumps({{"model": config["model"].split("/", 1)[1], "messages": [], "max_tokens": 1}}),
                  {{"content-type": "application/json", "authorization": "Bearer " + options["apiKey"]}})
    reply = model.getresponse()
    reply.read()
    finish()
    ended(CANNED + " model=" + str(reply.status) + " waited: " + answer.stdout)
if "linger" in task:
    open("src/pending.py", "w").write("x = 1\\n")
    open("home", "w").write(os.environ["HOME"])
    open("pid", "w").write(str(os.getpid()))
    time.sleep(120)
os.makedirs(".github/workflows", exist_ok=True)
open(".github/workflows/ci.yml", "w").write("name: ci\\n")
open("opencode.json", "w").write("{{}}\\n")
try:
    open({project!r} + "/direct.py", "w").write("x = 1\\n")
    direct = "WRITTEN"
except OSError:
    direct = "refused"
mcp = subprocess.Popen(server, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
count = [0]


def rpc(method, **params):
    count[0] += 1
    mcp.stdin.write(json.dumps({{"jsonrpc": "2.0", "id": count[0], "method": method, "params": params}}) + "\\n")
    mcp.stdin.flush()
    return json.loads(mcp.stdout.readline())["result"]


def call(name, **arguments):
    done = rpc("tools/call", name=name, arguments=arguments)
    return ("ERROR " if done["isError"] else "") + done["content"][0]["text"]


step("writing the feature")
writing, _decision = asked("write", file_path=cfg["root"] + "/src/feature.py")
rpc("initialize")
listed = rpc("tools/list")["tools"]
tools = "; ".join(f"{{each['name']}}({{','.join(each['inputSchema']['properties'])}}) {{each['description']}}" for each in listed)
note = call("note", text="halfway: src/feature.py is written")
answer = call("ask", question="which module is the main one?")
generic = call("workspace_send", to="someone-else", type="status", body="hello")
inbox = call("workspace_inbox")
heard = used("write", writing)
again = hook("post", {{"tool_use_id": writing}})[1]
finish()
ended(f"{{CANNED}} direct={{direct}} | tools={{tools}} | note={{note}} | answer={{answer}} | generic={{generic}} | inbox={{inbox}} | hook={{heard}} | "
      f"again={{again}} | home={{os.environ['HOME']}} | flags={{' '.join(args)}} | offered={{','.join(cfg['tools'])}}")
'''


class Slow(kit.Canned):
    """The kit's marked canned server, answering after `seconds` (0 unless a test sets it)."""

    seconds = 0.0

    def do_POST(self):
        time.sleep(Slow.seconds)
        super().do_POST()


@pytest.fixture
def editing(lead_node, detached, confinable, from_a_terminal, monkeypatch, tmp_path):  # noqa: PLR0913
    """A lead, a git project and the marked program in the harness's place (a marked ``rg`` beside it, as the kept release has), run by the
    delegate's own runner in the real confinement."""
    node, lead, project = lead_node
    project = project.resolve()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    release = tmp_path / "release" / "bin"
    release.mkdir(parents=True)
    script = release / "opencode"
    script.write_text(HARNESS.format(python=os.path.realpath(sys.executable), project=str(project)))
    script.chmod(0o755)
    (release / "rg").write_text("#!/bin/sh\necho 'rg beside the harness, NOT RIPGREP'\n")
    (release / "rg").chmod(0o755)

    @contextlib.contextmanager
    def leased(_model, _say):
        yield f"http://127.0.0.1:{server.server_port}", SimpleNamespace(serving=SimpleNamespace(slot_context=8192)), "canned.gguf"

    monkeypatch.setattr(delegate_confined, "leased", leased)
    monkeypatch.setattr(delegate_confined.harnessing, "binary_for", lambda _name: str(script.resolve()))
    monkeypatch.setattr(delegate_confined.claude, "alias_of", lambda _url, _model: kit.MODEL)
    monkeypatch.setattr(lead_bridge, "POLL_S", 0.05)
    before = signal.getsignal(signal.SIGTERM)
    yield node, lead, project
    signal.signal(signal.SIGTERM, before)
    lead_bridge.STOPPED.clear()
    server.shutdown()
    server.server_close()


def answering(node, lead, name, text, before=lambda: None):
    """The lead answers the first question the delegate asks, from another thread: the delegate waits for it."""
    def answer():
        deadline = time.time() + 120
        while time.time() < deadline and not done.is_set():
            try:
                if [e for e in node.session(lead).read(inbox=True).entries if e.fields["type"] == "question" and e.sender == name]:
                    before()
                    node.session(lead).post(name, "answer", text)
                    return
            except (OSError, RuntimeError):   # the node of the test is gone
                return
            time.sleep(0.05)

    done = threading.Event()
    thread = threading.Thread(target=answer, daemon=True)
    thread.start()
    thread.done = done
    return thread


def test_an_edit_delegate_publishes_its_change_refuses_the_tools_settings_asks_its_lead_and_reports(editing):
    node, lead, project = editing
    assert started("--task", "add a feature module", "--project", str(project), "--edit") == 0
    [name] = agent_names(node, lead)
    rec = store.load(name)
    assert rec.edit and rec.workdir == rec.source == str(project), "no copy is made: the project is where the delegate's stage is published"
    seen = []
    answers = answering(node, lead, name, "main.py", before=lambda: (time.sleep(0.5), seen.append(store.status(name))))
    assert delegate_run.run(name) == 1, store.result(name)
    answers.join(30)
    answers.done.set()
    assert (seen[0]["state"], seen[0].get("turns"), seen[0].get("tool")) == ("asking", 1, "Write"), f"the status shows the run's progress: {seen}"
    row = store.result(name)
    assert row["state"] == "partial" and (row["applied"], row["refused"]) == ([".github/workflows/ci.yml", "src/feature.py"], ["opencode.json"]), row
    assert row["runs_later"] == [".github/workflows/ci.yml"], "the result names the applied files that run later"
    assert (row["turns"], row["input_tokens"], row["output_tokens"], row["cache_read_tokens"]) == (2, 20, 4, 14), row
    text = row["text"]
    assert CANNED in text and "direct=refused" in text, "the confined agent cannot write the project itself"
    assert f"{lead.name} answered:" in text and "main.py" in text
    tools = text.split("tools=")[1].split(" | note=")[0]
    assert tools.startswith("ask(question) ") and "; note(text) " in tools and tools.count("(") == 2, f"the model has these two tools and no other: {tools}"
    assert "sent to " in text.split("note=")[1].split(" | ")[0], "a note returns at once, told what it did"
    assert "no such verb" in text.split("generic=")[1].split(" | ")[0] and "no such verb" in text.split("inbox=")[1].split(" | ")[0], "the generic verbs are gone"
    assert text.split("hook=")[1].split(" | again=")[0] == "", "the answer reached the question, so the hook has nothing more to say"
    assert "--format json" in text and "--title delegate" in text and "run --server" in text
    assert "offered=read,glob,grep,edit,write,shell,lead_ask,lead_note" in text
    assert "Publication, in the launcher's words" in text and "Applied 2 files" in text and "Refused " in text
    assert (project / "src" / "feature.py").read_text() == "def feature():\n    return 1\n"
    assert (project / ".github" / "workflows" / "ci.yml").read_text() == "name: ci\n"
    assert not (project / "opencode.json").exists() and not (project / "direct.py").exists()
    patches = list(home.state("isolation").glob("refused/*/p*.patch"))
    assert patches and b"opencode.json" in patches[0].read_bytes(), "the refused settings are kept as a patch under the state root"
    entries = node.session(lead).read(limit=100).entries
    sent = [(e.sender, e.fields.get("type"), e.fields.get("subject", "")) for e in entries]
    assert any(sender == name and kind == "question" for sender, kind, _s in sent)
    assert any(sender == lead.name and subject == "isolation.report" for sender, _k, subject in sent), sent
    assert not [1 for sender, _k, subject in sent if sender == name and subject == "isolation.report"], "a session never carries the launcher's kind"
    assert all(session_verbs.LABEL in e.text for e in entries if e.sender == name and e.fields.get("type") == "question"), "every question is labelled as data"
    agent_home = Path(text.split("home=")[1].split(" | ")[0])
    assert not agent_home.exists(), "what the launch made is removed"


@pytest.mark.parametrize("folder", ["project", "none", "no-git"])
def test_a_read_only_delegate_reads_a_read_only_stage_with_no_editing_tool_and_is_given_what_its_lead_sent(editing, tmp_path, folder):
    """With a git project, with no folder (a scratch folder of its own) and with a folder that is no git project (read from a copy): one confined path."""
    node, lead, project = editing
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "source.py").write_text("y = 2\n")
    words = {"project": ["--project", str(project)], "none": [], "no-git": ["--project", str(plain)]}[folder]
    assert started("--task", "mode:reads", *words) == 0
    [name] = agent_names(node, lead)
    node.session(lead).post(name, "status", "a word for the helper")
    assert delegate_run.run(name) == 0, store.result(name)
    row = store.result(name)
    text = row["text"]
    assert row["state"] == "done" and row["applied"] == [] and "Publication" not in text, row
    assert "write=refused" in text and "edit=deny" in text, "neither the box nor the classifier lets it change a file"
    assert "shell-refused=True" in text, "a shell it is not offered could not write either: the box refuses it"
    assert "a word for the helper" in text.split("heard=")[1].split(" | rg=")[0], "the lead's message reaches it after a tool call"
    assert "rg=rg beside the harness, NOT RIPGREP" in text, "the ripgrep kept beside the binary is the one the box runs"
    assert ("file=nothing" not in text) is (folder != "none") and ("y = 2" in text) is (folder == "no-git")
    assert "shell=False" in text and "tools=read,glob,grep,lead_ask,lead_note |" in text
    rules = json.loads(text.split("permissions=")[1])
    assert rules[0] == {"action": "*", "resource": "*", "effect": "deny"}
    assert [each["action"] for each in rules[1:] if each["effect"] == "allow"] == ["read", "glob", "grep", "lead_ask", "lead_note"]
    assert not (project / "planted.py").exists()
    workdir = Path(store.load(name).workdir)
    assert project.exists() and (plain / "source.py").exists() and not (plain / ".git").exists() and workdir.exists() is (folder == "project")


def test_a_stopped_edit_delegate_ends_its_confined_processes_publishes_nothing_more_and_removes_what_it_made(editing, confinable):
    node, lead, project = editing
    assert started("--task", "linger over the feature", "--project", str(project), "--edit") == 0
    [name] = agent_names(node, lead)
    pid_of, home_of = [], []

    def stop_when_working():
        deadline = time.time() + 120
        while time.time() < deadline:
            found = [each for each in confinable.glob("**/pid") if (each.parent / "src" / "pending.py").exists()]
            if found and found[0].read_text().isdigit() and (found[0].parent / "home").exists():
                pid_of.append(int(found[0].read_text()))
                home_of.append(Path((found[0].parent / "home").read_text()))
                os.kill(os.getpid(), signal.SIGTERM)
                return
            time.sleep(0.05)

    threading.Thread(target=stop_when_working, daemon=True).start()
    assert delegate_run.run(name) == 1
    assert store.status(name)["state"] == "stopped" and pid_of
    with pytest.raises(ProcessLookupError):
        os.kill(pid_of[0], 0)
    assert not (project / "src" / "pending.py").exists() and not (project / "src" / "feature.py").exists(), "nothing more was published"
    assert not home_of[0].exists() and not list(confinable.glob("**/pending.py")), "the home and the stage are gone"
    assert project.exists()


def test_start_edit_without_a_project_edits_the_project_the_command_runs_in_and_names_no_widening(lead_node, detached, monkeypatch):
    node, lead, project = lead_node
    asked = []
    monkeypatch.setattr(harnessing, "decided", lambda args, folder: asked.append(folder) or isolation.Launch((), (), False, False))
    assert started("--task", "edit things", "--edit") == 0
    [name] = agent_names(node, lead)
    assert store.load(name).source == str(project.resolve()) and asked == [""], "the default reach asks for no widening"


@pytest.mark.parametrize("words", [["--edit"], []])
def test_start_on_a_computer_that_cannot_confine_is_refused_at_once_with_the_sentence(lead_node, detached, from_a_terminal, monkeypatch, capsys, words):  # noqa: PLR0913
    node, lead, project = lead_node
    kit.without_a_sandbox(monkeypatch, AbsentBackend)
    scratch = []
    made = delegate.tempfile.mkdtemp
    monkeypatch.setattr(delegate.tempfile, "mkdtemp", lambda **named: scratch.append(made(**named)) or scratch[-1])
    assert started("--task", "do things", "--project", str(project), *words) == 2
    assert started("--task", "do things with no folder") == 2
    assert SENTENCES["no_sandbox_tool"] in said(capsys)
    assert detached == [] and agent_names(node, lead) == [], "no agent is registered and nothing runs"
    assert not [each for each in scratch if "poolhouse-delegate-" in each and Path(each).exists()], "the scratch folder of a refused start is removed"


def test_start_edit_on_a_folder_the_gate_refuses_is_refused_at_once_with_its_reason(lead_node, detached, monkeypatch, capsys):
    node, lead, project = lead_node

    def refused(_args, _asked):
        raise IsolationRefused("Refused: this folder is not a registered project of this computer.")

    monkeypatch.setattr(harnessing, "decided", refused)
    assert started("--task", "edit things", "--project", str(project), "--edit") == 2
    assert "not a registered project" in said(capsys)
    assert detached == [] and agent_names(node, lead) == []


def test_the_scratch_copy_is_gone():
    assert not hasattr(delegate, "scratch_copy") and not hasattr(delegate, "CONTROL")


def test_a_delegates_two_tools_go_to_its_lead_a_question_waits_for_it_and_nothing_else_is_a_tool():
    asked, noted = [], []
    run = lead_bridge.lead_only(lambda text: asked.append(text) or "the answer", lambda text: noted.append(text) or "noted")
    assert run("ask", {"question": "q"}) == "the answer"
    assert run("note", {"text": "n"}) == "noted"
    for tool in ("workspace_send", "workspace_inbox", "workspace_announce", "workspace_claim", "workspace_attach"):
        assert run(tool, {}) == lead_bridge.REFUSED_VERB
    assert asked == ["q"] and noted == ["n"]
    broken = lead_bridge.lead_only(lambda _t: (_ for _ in ()).throw(ValueError("not sent: the board is gone")), lambda t: t)
    with pytest.raises(ValueError, match="the board is gone"):
        broken("ask", {"question": "q"})   # the bridge turns it into its own fixed sentence; the words never reach the box


def rendered(where, name="child", task="t", edit=True):
    rec = store.Delegate(name, "lead-1", "board", task, str(where), "m", source=str(where), edit=edit, max_turns=7)
    context = isolation.Context(home=where / "home", config=where / "home" / "config", stage=str(where / "stage"), python="/py", client="/h/client.py",
                                socket="/s", pid=1, base_url="http://127.0.0.1:1", alias="m", window=8192, token="t" * 40)
    return delegate_confined.renderer(rec, "/bin/opencode", ("http://127.0.0.1:2", "m", 8192))(context)


def test_the_model_sees_only_the_delegates_tools_and_opencode_is_bounded_by_its_own_settings(tmp_path):
    rendering = rendered(tmp_path)
    config = json.loads(rendering.files[".opencode-config/opencode.json"])
    agent = config["agents"][config["default_agent"]]
    assert config["default_agent"] == opencode.AGENT and "instructions" not in config and "permission" not in config
    allowed = [each["action"] for each in agent["permissions"] if each["effect"] == "allow"]
    assert allowed == ["read", "glob", "grep", "edit", "shell", "lead_ask", "lead_note"], "no fetch, search of the web, subagent or script"
    assert agent["permissions"][0] == {"action": "*", "resource": "*", "effect": "deny"}
    assert config["tool_output"] == {"max_bytes": 8192, "max_lines": 8192}
    assert config["mcp"]["lead"]["command"] == ["/py", "-I", "/h/client.py", "--socket", "/s", "--pid", "1", "mcp"]
    assert config["mcp"]["lead"]["timeout"] == int(lead_bridge.tool_timeout() * 1000) and lead_bridge.tool_timeout() > lead_bridge.ask_wait()
    assert config["provider"]["poolhouse"]["options"]["baseURL"] == "http://127.0.0.1:1/v1", "the bridge, never the server"
    assert config["plugins"] == [str(tmp_path / "home" / opencode.PLUGIN)], "the plugin's folder, by path"
    plugin = rendering.files[f"{opencode.PLUGIN}/{opencode.ENTRY}"]
    settings = json.loads(plugin.split("const cfg = ", 1)[1].split(";\n", 1)[0])
    assert settings["shell"]["most"] == 600.0 and settings["shell"]["argv"][:2] == ["/py", "-I"]
    assert rendering.files["session/bounded.py"] == delegate_confined.BOUNDED
    brief = agent["system"]
    assert "You have no shell" not in brief and "name every test you could not run" in brief
    for limit in ("no network", "no localhost port", "no Poolhouse node", "no nested sandbox", "no real model", "at most 600s"):
        assert limit in brief, limit
    assert "lead_ask(question)" in brief and "lead_note(text)" in brief and set(session_verbs.LEAD) == {"ask", "note"}
    reading = rendered(tmp_path, edit=False)
    assert "session/bounded.py" not in reading.files
    assert "You have no shell" in json.loads(reading.files[".opencode-config/opencode.json"])["agents"][opencode.AGENT]["system"]


def test_every_delegate_of_one_lead_starts_with_the_same_prompt_whatever_differs_per_launch(tmp_path):
    one, two = rendered(tmp_path / "one", "child-1", "first"), rendered(tmp_path / "two", "child-2", "second")

    def prompt(rendering):
        return json.loads(rendering.files[".opencode-config/opencode.json"])["agents"][opencode.AGENT]["system"]

    assert prompt(one) == prompt(two) and str(tmp_path) not in prompt(one) and "child-1" not in prompt(one)
    plugin = one.files[f"{opencode.PLUGIN}/{opencode.ENTRY}"]
    fixed = json.loads(plugin.split("const cfg = ", 1)[1].split(";\n", 1)[0])["fixed"]
    assert fixed == [[str(tmp_path / "one" / "stage"), "."], [str(tmp_path / "one" / "home"), "$HOME"]]


def test_the_helpers_token_is_found_on_the_leads_board_when_the_project_is_on_another(editing, tmp_path):
    """The live failure: the lead ran in one checkout and named another as the project, so the helper (a subagent of the lead) was made on the lead's
    board while its notification hook looked for its token on the board of the project, a fresh one, and found none for every call. What the hook
    says is the lead's own message and nothing else, once."""
    node, lead, project = editing
    other = checkouts(tmp_path / "elsewhere").primary.resolve()
    (tmp_path / "release" / "bin" / "opencode").write_text(HARNESS.format(python=os.path.realpath(sys.executable), project=str(other)))
    fresh = place.resolve(node.client, other)
    assert started("--task", "add a feature module", "--project", str(other), "--edit") == 0
    [name] = agent_names(node, lead)
    assert fresh != store.load(name).board == node.board, "the project is on a board of its own, fresh to this lead"
    node.session(lead).post(name, "status", "a word for the helper before it starts")
    node.session(node.member("someone-else")).post(name, "status", "a stranger's word")
    answers = answering(node, lead, name, "main.py")
    assert delegate_run.run(name) == 1 and store.status(name)["state"] == "partial", store.result(name)
    answers.join(30)
    answers.done.set()
    text = store.result(name)["text"]
    hook = text.split("hook=")[1].split(" | again=")[0]
    context = json.loads(hook)["hookSpecificOutput"]["additionalContext"]
    assert context == f"{lead_bridge.FROM_LEAD.format(lead=lead.name)}\na word for the helper before it starts", f"the inbox on the lead's board: {hook}"
    assert text.split("again=")[1].split(" | home=")[0] == "", "each message is given once"
    assert f"{lead.name} answered:" in text and "main.py" in text, "and its question to the lead was answered"
    assert (other / "src" / "feature.py").exists() and not (project / "src").exists(), "the work was published to the project it was asked to change"


def read(*lines, status=0):
    stream = delegate_confined.Stream()
    for line in lines:
        stream.line(json.dumps(line).encode() if isinstance(line, dict) else line)
    return stream.answer(status)


def test_only_a_run_whose_last_step_stopped_of_itself_succeeded_and_a_failed_one_keeps_what_arrived():
    start, text = {"type": "step_start", "part": {}}, {"type": "text", "part": {"text": "half"}}
    used = {"type": "step_finish", "part": {"reason": "tool-calls", "tokens": {"input": 5, "output": 3, "reasoning": 1, "cache": {"read": 2, "write": 1}}}}
    lost = read(b"not json", start, text, used, status=1)
    assert lost.is_error and "no final answer" in lost.why and lost.text == "half"
    assert (lost.spent.turns, lost.spent.input_tokens, lost.spent.output_tokens, lost.spent.cache_read_tokens, lost.spent.cache_creation_tokens) == (1, 5, 4, 2, 1)
    stopped = {"type": "step_finish", "part": {"reason": "stop", "tokens": {"output": 4}}}
    fine = read(start, text, used, start, {"type": "text", "part": {"text": "done"}}, stopped)
    assert not fine.is_error and fine.text == "done" and fine.spent.output_tokens == 8 and fine.spent.turns == 2
    told = read(start, text, stopped, {"type": "error", "error": {"type": "unknown", "message": "refused"}})
    assert told.is_error and "unknown: refused" in told.why and told.text == "half"
    assert read(start, text, stopped, status=1).is_error, "a nonzero status is a failure whatever was printed"
    assert read(status=0).is_error, "a run that printed nothing did not succeed"


def ran(editing, mode, *words):
    """One edit delegate in the marked harness's ``mode``, run to its end: its exit status, its result, the project and what its lead was told."""
    node, lead, project = editing
    assert started("--task", f"mode:{mode}", "--project", str(project), "--edit", *words) == 0
    [name] = agent_names(node, lead)
    status = delegate_run.run(name)
    told = [e.text for e in node.session(lead).read(limit=100).entries if e.sender == name and e.fields.get("subject", "").startswith("delegate ")]
    return SimpleNamespace(status=status, row=store.result(name), project=project, told="\n".join(told), name=name)


@pytest.mark.parametrize("mode", ["settings-only", "edits-nothing"])
def test_a_run_that_tried_to_change_files_and_applied_nothing_is_not_done_and_says_so(editing, mode):
    run = ran(editing, mode)
    assert run.status == 1 and run.row["state"] == "unapplied" == store.status(run.name)["state"], run.row
    assert run.row["applied"] == [] and "delegate unapplied" not in run.told.split("\n")[0]
    assert "unapplied: nothing was applied to the project" in run.told, run.told
    assert f"{len(run.row['refused'])} refused" in run.told


def test_a_run_whose_changes_were_all_applied_is_done(editing):
    run = ran(editing, "clean")
    assert run.status == 0 and run.row["state"] == "done" and run.row["applied"] == ["src/feature.py"] and run.row["refused"] == [], run.row


@pytest.mark.parametrize(("mode", "words", "why"), [
    ("errors", [], "the model server went away"), ("dies", [], "no final answer"), ("cut-short", [], "ended with length"),
    ("stalls", [], "no progress"), ("endless", ["--max-turns", "3"], "its limit of 3 turn(s)"), ("unguarded", [], "plugin"),
    ("shell-hangs", [], "no progress")])
def test_a_failed_edit_run_publishes_nothing_keeps_the_patch_and_names_its_place(editing, monkeypatch, mode, words, why):
    if mode in ("stalls", "shell-hangs"):
        monkeypatch.setattr(delegate_confined, "NO_PROGRESS_S", 3.0)
    began = time.monotonic()
    run = ran(editing, mode, *words)
    assert time.monotonic() - began < 90, "a run with no progress ends"
    assert run.status == 1 and run.row["state"] == "failed" and why in run.row["error"], run.row
    assert not (run.project / "src").exists(), "a failed run publishes nothing"
    assert CANNED in run.row["text"] and "NOT ENDED" not in run.row["text"], "what the agent had said is kept"
    [patch] = run.row["patches"]
    assert b"src/feature.py" in Path(patch).read_bytes() and patch in run.row["text"] and run.row["applied"] == []


def test_a_resource_bound_that_ends_an_edit_delegate_leaves_it_failed_with_the_reason_and_the_patch(editing, monkeypatch):
    derived = choke_point.derive
    monkeypatch.setattr(choke_point, "derive", lambda volume: replace(derived(volume), memory_bytes=256 << 20))
    run = ran(editing, "hoards")
    assert run.status == 1 and run.row["state"] == "failed", run.row
    assert PASSED in run.row["error"] and "bytes of memory" in run.row["error"] and "NOT ENDED" not in run.row["text"], run.row
    assert not (run.project / "src").exists() and run.row["applied"] == [], "a session that passed a bound publishes nothing"
    [patch] = run.row["patches"]
    assert b"src/feature.py" in Path(patch).read_bytes() and patch in run.row["text"]
    node, lead, _project = editing
    reports = [e.text for e in node.session(lead).read(limit=100).entries if e.fields.get("subject") == "isolation.report"]
    assert any(PASSED in each for each in reports), reports


def test_a_shell_command_within_its_timeout_is_not_counted_as_no_progress(editing, monkeypatch):
    monkeypatch.setattr(delegate_confined, "NO_PROGRESS_S", 3.0)
    run = ran(editing, "shell-slow")
    assert run.status == 0 and run.row["state"] == "done" and "waited for its command: 0 slept" in run.row["text"], run.row


def test_a_shell_command_is_ended_at_its_timeout_with_what_it_left_running_and_the_run_goes_on(editing):
    run = ran(editing, "shell-overdue")
    text = run.row["text"]
    assert run.row["state"] != "failed" and "124 poolhouse: the command was ended at its timeout of 2s" in text, run.row
    assert int(text.split(" after ")[1].split("s")[0]) < 30
    with pytest.raises(ProcessLookupError):
        os.kill(int(text.split("child=")[1].split()[0]), 0)


@pytest.mark.parametrize(("mode", "state"), [("shell-writes-nothing", "unapplied"), ("shell-reads", "done")])
def test_a_shell_call_that_writes_counts_as_trying_to_change_files_and_one_that_reads_does_not(editing, mode, state):
    run = ran(editing, mode)
    assert run.row["state"] == state and run.row["applied"] == [], run.row


def test_a_harness_that_ends_while_its_child_holds_the_pipe_ends_the_run_and_the_child(editing):
    began = time.monotonic()
    run = ran(editing, "orphan")
    assert time.monotonic() - began < 60 and run.status == 0, run.row
    with pytest.raises(ProcessLookupError):
        os.kill(int(run.row["text"].split("child=")[1].split()[0]), 0)


def test_the_wait_for_the_model_and_for_the_lead_is_not_counted_as_no_progress(editing, monkeypatch):
    node, lead, project = editing
    monkeypatch.setattr(delegate_confined, "NO_PROGRESS_S", 3.0)
    monkeypatch.setattr(Slow, "seconds", 4.5)
    assert started("--task", "mode:patient", "--project", str(project), "--edit") == 0
    [name] = agent_names(node, lead)
    answers = answering(node, lead, name, "main.py", before=lambda: time.sleep(4.5))
    assert delegate_run.run(name) == 0, store.result(name)
    answers.join(30)
    answers.done.set()
    row = store.result(name)
    assert "model=200" in row["text"] and "main.py" in row["text"], row
    assert row["model_seconds"] >= 3, "the time the model server had the turn"
