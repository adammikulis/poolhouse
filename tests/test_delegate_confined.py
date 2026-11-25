"""A delegate that edits: `poolhouse-delegate start --edit` runs the agent in the isolated launcher's confinement and publishes plain source.

The confinement is real here: the stage, both bridges, the proof and the Seatbelt profile, the board (a real node), git and the project. The one thing
that is not real is Claude Code and the model behind it: a marked program (`HARNESS`, NOT A MODEL and NOT CLAUDE CODE) stands in the harness's
place inside the profile and does what a delegate's model would do with its tools, the model side being the kit's `CANNED-NOT-A-MODEL` server. The
run with Claude Code on a real model is in the report.
"""

from __future__ import annotations

import contextlib
import os
import signal
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from delegate_kit import agent_names, said, started

from poolhouse import (
    delegate,
    delegate_confined,
    delegate_run,
    delegate_store as store,
    harnessing,
    home,
    isolation,
)
from poolhouse.isolation import session_verbs
from poolhouse.isolation.refusals import SENTENCES, IsolationRefused
from tests import isolation_kit as kit
from tests.sandbox_kit import AbsentBackend

pytest_plugins = ["node_kit", "delegate_kit", "tests.confined_kit"]
CANNED = "[CANNED RESPONSE, NOT A MODEL]"
HARNESS = '''#!{python}
# NOT A MODEL and NOT CLAUDE CODE: a program standing in for the harness inside the real confinement. It does what a delegate's model would do with
# its tools (writes files, asks its lead, tries what it must not) and prints the one JSON result the real harness prints. Every word it says is marked.
import json, os, subprocess, sys, time

CANNED = "[CANNED RESPONSE, NOT A MODEL]"
args, task = sys.argv[1:], sys.stdin.read()
server = json.load(open(args[args.index("--mcp-config") + 1]))["mcpServers"]["workspace"]
os.makedirs("src", exist_ok=True)
open("src/feature.py", "w").write("def feature():\\n    return 1\\n")
if "linger" in task:
    open("src/pending.py", "w").write("x = 1\\n")
    open("home", "w").write(os.environ["HOME"])
    open("pid", "w").write(str(os.getpid()))
    time.sleep(120)
os.makedirs(".github/workflows", exist_ok=True)
open(".github/workflows/ci.yml", "w").write("name: ci\\n")
try:
    open({project!r} + "/direct.py", "w").write("x = 1\\n")
    direct = "WRITTEN"
except OSError:
    direct = "refused"
mcp = subprocess.Popen([server["command"], *server["args"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
count = [0]


def rpc(method, **params):
    count[0] += 1
    mcp.stdin.write(json.dumps({{"jsonrpc": "2.0", "id": count[0], "method": method, "params": params}}) + "\\n")
    mcp.stdin.flush()
    return json.loads(mcp.stdout.readline())["result"]


def call(name, **arguments):
    done = rpc("tools/call", name=name, arguments=arguments)
    return ("ERROR " if done["isError"] else "") + done["content"][0]["text"]


rpc("initialize")
lead = {lead!r}
note = call("workspace_send", to=lead, type="status", body="halfway: src/feature.py is written")
answer = call("workspace_send", to=lead, type="question", body="which module is the main one?")
stranger = call("workspace_send", to="someone-else", type="status", body="hello")
inbox = call("workspace_inbox")
text = f"{{CANNED}} direct={{direct}} | note={{note}} | answer={{answer}} | stranger={{stranger}} | inbox={{inbox}} | home={{os.environ['HOME']}} | flags={{' '.join(args)}}"
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "result": text, "num_turns": 3, "duration_api_ms": 1500,
                  "usage": {{"input_tokens": 30, "output_tokens": 5, "cache_read_input_tokens": 7, "cache_creation_input_tokens": 0}}, "session_id": "s"}}))
'''


@pytest.fixture
def editing(lead_node, detached, confinable, from_a_terminal, monkeypatch, tmp_path):  # noqa: PLR0913
    """A lead, a git project and the marked program in the harness's place, run by the delegate's own runner in the real confinement."""
    node, lead, project = lead_node
    project = project.resolve()
    server = ThreadingHTTPServer(("127.0.0.1", 0), kit.Canned)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    script = tmp_path / "harness-NOT-CLAUDE-CODE.py"
    script.write_text(HARNESS.format(python=os.path.realpath(sys.executable), project=str(project), lead=lead.name))
    script.chmod(0o755)

    @contextlib.contextmanager
    def leased(_model, _say):
        yield f"http://127.0.0.1:{server.server_port}", SimpleNamespace(serving=SimpleNamespace(slot_context=8192)), "canned.gguf"

    monkeypatch.setattr(delegate_confined, "leased", leased)
    monkeypatch.setattr(delegate_confined.harnessing, "binary_for", lambda _name: str(script))
    monkeypatch.setattr(delegate_confined.claude, "alias_of", lambda _url, _model: kit.MODEL)
    monkeypatch.setattr(delegate_run, "POLL_S", 0.05)
    before = signal.getsignal(signal.SIGTERM)
    yield node, lead, project
    signal.signal(signal.SIGTERM, before)
    delegate_run.STOPPED.clear()
    server.shutdown()
    server.server_close()


def answering(node, lead, name, text):
    """The lead answers the first question the delegate asks, from another thread: the delegate waits for it."""
    def answer():
        deadline = time.time() + 120
        while time.time() < deadline and not done.is_set():
            try:
                if [e for e in node.session(lead).read(inbox=True).entries if e.fields["type"] == "question" and e.sender == name]:
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


def test_an_edit_delegate_publishes_plain_source_refuses_the_rest_asks_its_lead_and_reports(editing):
    node, lead, project = editing
    assert started("--task", "add a feature module", "--project", str(project), "--edit") == 0
    [name] = agent_names(node, lead)
    rec = store.load(name)
    assert rec.edit and rec.workdir == rec.source == str(project), "no copy is made: the project is where the delegate's stage is published"
    answers = answering(node, lead, name, "main.py")
    assert delegate_run.run(name) == 0
    answers.join(30)
    answers.done.set()
    row = store.result(name)
    assert row["state"] == "done" and (row["turns"], row["input_tokens"], row["cache_read_tokens"]) == (3, 30, 7), row
    text = row["text"]
    assert CANNED in text and "direct=refused" in text, "the confined agent cannot write the project itself"
    assert f"{lead.name} answered:" in text and "main.py" in text
    assert "Refused: a delegate sends only to its lead" in text and "Refused: a delegate has one workspace verb" in text
    assert "--strict-mcp-config" in text and "--tools Read,Grep,Glob,Edit,Write" in text and "--output-format json" in text
    assert "Publication, in the launcher's words" in text and "Applied 1 file" in text and "Refused " in text
    assert (project / "src" / "feature.py").read_text() == "def feature():\n    return 1\n"
    assert not (project / ".github").exists() and not (project / "direct.py").exists()
    patches = list(home.state("isolation").glob("refused/*/p*.patch"))
    assert patches and b"ci.yml" in patches[0].read_bytes(), "the refused workflow is kept as a patch under the state root"
    entries = node.session(lead).read(limit=100).entries
    sent = [(e.sender, e.fields.get("type"), e.fields.get("subject", "")) for e in entries]
    assert any(sender == name and kind == "question" for sender, kind, _s in sent)
    assert any(sender == lead.name and subject == "isolation.report" for sender, _k, subject in sent), sent
    assert not [1 for sender, _k, subject in sent if sender == name and subject == "isolation.report"], "a session never carries the launcher's kind"
    assert all(session_verbs.LABEL in e.text for e in entries if e.sender == name and e.fields.get("type") == "question"), "every question is labelled as data"
    agent_home = Path(text.split("home=")[1].split(" | ")[0])
    assert not agent_home.exists(), "what the launch made is removed"


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


def test_start_edit_on_a_computer_that_cannot_confine_is_refused_at_once_with_the_sentence(lead_node, detached, from_a_terminal, monkeypatch, capsys):
    node, lead, project = lead_node
    kit.without_a_sandbox(monkeypatch, AbsentBackend)
    assert started("--task", "edit things", "--project", str(project), "--edit") == 2
    assert SENTENCES["no_sandbox_tool"] in said(capsys)
    assert detached == [] and agent_names(node, lead) == [], "no agent is registered and nothing runs"


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


def test_only_the_send_verb_reaches_the_lead_and_a_question_waits_for_it():
    asked, noted = [], []
    run = delegate_confined.lead_only("lead-1", lambda text: asked.append(text) or "the answer", lambda text: noted.append(text) or "noted")
    assert run("workspace_send", {"to": "lead-1", "type": "question", "body": "q"}) == "the answer"
    assert run("workspace_send", {"to": "lead-1", "type": "status", "body": "n"}) == "noted"
    assert run("workspace_send", {"to": "lead-1", "type": "decision", "body": "x"}) == "noted", "any other kind is a note, never a kind of its own"
    assert "only to its lead" in run("workspace_send", {"to": "other", "type": "question", "body": "q"})
    for tool in ("workspace_inbox", "workspace_announce", "workspace_claim", "workspace_attach"):
        assert run(tool, {}) == delegate_confined.REFUSED_VERB
    assert asked == ["q"] and noted == ["n", "x"]


def test_the_model_sees_one_workspace_tool_and_the_harness_takes_no_other_server(tmp_path):
    rec = store.Delegate("child", "lead-1", "board", "t", str(tmp_path), "m", source=str(tmp_path), edit=True, max_turns=7)
    context = SimpleNamespace(client_argv=lambda *words: ["/py", "-I", "/h/client.py", "--socket", "/s", "--pid", "1", *words],
                              hook=lambda event: f"hook {event}", file=lambda name: tmp_path / name)
    rendering = delegate_confined.renderer(rec, "/bin/claude")(context)
    argv = list(rendering.argv)
    assert argv[argv.index("--tools") + 1] == "Read,Grep,Glob,Edit,Write" and "Bash" not in argv[argv.index("--tools") + 1]
    hidden = argv[argv.index("--disallowedTools") + 1].split(",")
    assert sorted(hidden) == sorted(f"mcp__workspace__workspace_{name}" for name in session_verbs.VERBS if name != "send")
    assert "--strict-mcp-config" in argv and argv[argv.index("--max-turns") + 1] == "7"
    assert argv[argv.index("--output-format") + 1] == "json" and "--print" in argv
    assert '"workspace"' in rendering.files["session/mcp.json"] and "mcp" in rendering.files["session/mcp.json"]


def test_a_run_that_prints_no_result_is_a_failure_and_a_result_that_says_error_is_one():
    with pytest.raises(RuntimeError, match="no result"):
        delegate_confined.answer_of(b"not json", 1, 1.0)
    failed = delegate_confined.answer_of(b'{"type": "result", "is_error": true, "result": "model refused", "num_turns": 1}', 0, 1.0)
    assert failed.is_error and failed.text == "model refused"
    listed = delegate_confined.answer_of(b'[{"type": "system"}, {"type": "result", "result": "done", "usage": {"output_tokens": 4}}]', 0, 1.0)
    assert not listed.is_error and listed.text == "done" and listed.spent.output_tokens == 4
