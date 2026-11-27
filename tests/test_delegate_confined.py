"""A delegate that edits: `poolhouse-delegate start --edit` runs the agent in the isolated launcher's confinement and publishes plain source.

The confinement is real here: the stage, both bridges, the proof and the Seatbelt profile, the board (a real node), git and the project. The one thing
that is not real is Claude Code and the model behind it: a marked program (`HARNESS`, NOT A MODEL and NOT CLAUDE CODE) stands in the harness's
place inside the profile and does what a delegate's model would do with its tools, the model side being the kit's `CANNED-NOT-A-MODEL` server. The
run with Claude Code on a real model is in the report.
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
)
from poolhouse.board import place
from poolhouse.harnesspolicy import LEAD_TOOLS
from poolhouse.isolation import confinement as choke_point, session_verbs
from poolhouse.isolation.bounds import PASSED
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
server = json.load(open(args[args.index("--mcp-config") + 1]))["mcpServers"]["lead"]
post = json.load(open(args[args.index("--settings") + 1]))["hooks"]["PostToolUse"][0]["hooks"][0]["command"]

def event(**row):
    print(json.dumps(row), flush=True)


def said(text, tool="", **asked):
    content = [{{"type": "text", "text": text}}] + ([{{"type": "tool_use", "id": "t", "name": tool, "input": asked}}] if tool else [])
    event(type="assistant", message={{"id": text, "content": content, "usage": {{"input_tokens": 11, "output_tokens": 2}}}})


def shell(command, seconds=0.0, **asked):
    said(CANNED + " running " + command, "Bash", command=command, **asked)
    time.sleep(seconds)
    event(type="user", message={{"role": "user", "content": [{{"type": "tool_result", "tool_use_id": "t", "content": "ran"}}]}})


def ended(text, error=False, subtype="success"):
    event(type="result", subtype=subtype, is_error=error, result=text, num_turns=3, duration_api_ms=1500, session_id="s",
          usage={{"input_tokens": 30, "output_tokens": 5, "cache_read_input_tokens": 7, "cache_creation_input_tokens": 0}})


os.makedirs("src", exist_ok=True)
if "mode:shell-writes-nothing" in task:
    shell("echo x > .venv/planted")
    ended(CANNED + " done and landed")
    sys.exit(0)
if "mode:shell-reads" in task:
    shell("git diff --stat && python3 -m pytest -q tests/test_app.py")
    ended(CANNED + " the tests pass")
    sys.exit(0)
if "mode:shell-slow" in task:
    shell("sleep 5", 5.0, timeout=60000)
    ended(CANNED + " waited for its command")
    sys.exit(0)
if "mode:shell-overdue" in task:
    shell("sleep 120", 120.0, timeout=2000)
if "mode:workflow-only" in task:
    os.makedirs(".github/workflows", exist_ok=True)
    open(".github/workflows/ci.yml", "w").write("name: ci\\n")
    ended(CANNED + " done and landed")
    sys.exit(0)
if "mode:edits-nothing" in task:
    said(CANNED + " editing", "Edit")
    ended(CANNED + " done and landed")
    sys.exit(0)
open("src/feature.py", "w").write("def feature():\\n    return 1\\n")
if "mode:clean" in task:
    ended(CANNED + " done")
    sys.exit(0)
if "mode:errors" in task:
    said(CANNED + " half way", "Write")
    ended(CANNED + " ran out of turns", True, "error_max_turns")
    sys.exit(1)
if "mode:dies" in task:
    said(CANNED + " half way", "Write")
    sys.exit(3)
if "mode:hoards" in task:
    said(CANNED + " half way", "Write")
    held = []
    for _ in range(48):
        held.append(os.urandom(16 << 20))
        time.sleep(0.05)
    time.sleep(15)
    ended(CANNED + " NOT ENDED")
    sys.exit(0)
if "mode:stalls" in task:
    said(CANNED + " about to stall", "Write")
    time.sleep(120)
if "mode:orphan" in task:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(100)"])
    ended(CANNED + " child=" + str(child.pid))
    sys.exit(0)
if "mode:patient" in task:
    import http.client
    server = json.load(open(args[args.index("--mcp-config") + 1]))["mcpServers"]["lead"]
    asked = subprocess.run([server["command"], *server["args"][:-1], "workspace", "ask", "--question", "which?"], capture_output=True, text=True)
    model = http.client.HTTPConnection(os.environ["ANTHROPIC_BASE_URL"].split("//")[1])
    model.request("POST", "/v1/messages", json.dumps({{"messages": [], "max_tokens": 1}}),
                  {{"content-type": "application/json", "x-api-key": os.environ["ANTHROPIC_AUTH_TOKEN"]}})
    reply = model.getresponse()
    reply.read()
    ended(CANNED + " model=" + str(reply.status) + " waited: " + asked.stdout)
    sys.exit(0)
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


said("writing the feature", "Write")
rpc("initialize")
listed = rpc("tools/list")["tools"]
tools = "; ".join(f"{{each['name']}}({{','.join(each['inputSchema']['properties'])}}) {{each['description']}}" for each in listed)
note = call("note", text="halfway: src/feature.py is written")
answer = call("ask", question="which module is the main one?")
generic = call("workspace_send", to="someone-else", type="status", body="hello")
inbox = call("workspace_inbox")
hooks = []
for _ in range(2):
    done = subprocess.run(post, shell=True, input=json.dumps({{"hook_event_name": "PostToolUse", "tool_name": "Read"}}), capture_output=True, text=True)
    hooks.append((done.stdout + done.stderr).strip())
hook, again = hooks
text = (f"{{CANNED}} direct={{direct}} | tools={{tools}} | note={{note}} | answer={{answer}} | generic={{generic}} | inbox={{inbox}} | hook={{hook}} | "
        f"again={{again}} | home={{os.environ['HOME']}} | flags={{' '.join(args)}}")
ended(text)
'''


class Slow(kit.Canned):
    """The kit's marked canned server, answering after `seconds` (0 unless a test sets it)."""

    seconds = 0.0

    def do_POST(self):
        time.sleep(Slow.seconds)
        super().do_POST()


@pytest.fixture
def editing(lead_node, detached, confinable, from_a_terminal, monkeypatch, tmp_path):  # noqa: PLR0913
    """A lead, a git project and the marked program in the harness's place, run by the delegate's own runner in the real confinement."""
    node, lead, project = lead_node
    project = project.resolve()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
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


def test_an_edit_delegate_publishes_plain_source_refuses_the_rest_asks_its_lead_and_reports(editing):
    node, lead, project = editing
    assert started("--task", "add a feature module", "--project", str(project), "--edit") == 0
    [name] = agent_names(node, lead)
    rec = store.load(name)
    assert rec.edit and rec.workdir == rec.source == str(project), "no copy is made: the project is where the delegate's stage is published"
    seen = []
    answers = answering(node, lead, name, "main.py", before=lambda: (time.sleep(0.5), seen.append(store.status(name))))
    assert delegate_run.run(name) == 1
    answers.join(30)
    answers.done.set()
    assert (seen[0]["state"], seen[0].get("turns"), seen[0].get("tool")) == ("asking", 1, "Write"), f"the status shows the run's progress: {seen}"
    row = store.result(name)
    assert row["state"] == "partial" and (row["applied"], row["refused"]) == (["src/feature.py"], [".github/workflows/ci.yml"]), row
    assert row["state"] == "partial" and (row["turns"], row["input_tokens"], row["cache_read_tokens"]) == (3, 30, 7), row
    text = row["text"]
    assert CANNED in text and "direct=refused" in text, "the confined agent cannot write the project itself"
    assert f"{lead.name} answered:" in text and "main.py" in text
    tools = text.split("tools=")[1].split(" | note=")[0]
    assert tools.startswith("ask(question) ") and "; note(text) " in tools and tools.count("(") == 2, f"the model has these two tools and no other: {tools}"
    assert "sent to " in text.split("note=")[1].split(" | ")[0], "a note returns at once, told what it did"
    assert "no such verb" in text.split("generic=")[1].split(" | ")[0] and "no such verb" in text.split("inbox=")[1].split(" | ")[0], "the generic verbs are gone"
    assert text.split("hook=")[1].split(" | again=")[0] == "", "the answer reached the question, so the hook has nothing more to say"
    assert "--strict-mcp-config" in text and "--tools Read,Grep,Glob,Edit,Write,Bash" in text and "--output-format stream-json --verbose" in text
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


def test_the_model_sees_the_two_lead_tools_and_the_harness_takes_no_other_server(tmp_path):
    rec = store.Delegate("child", "lead-1", "board", "t", str(tmp_path), "m", source=str(tmp_path), edit=True, max_turns=7)
    context = isolation.Context(home=tmp_path, config=tmp_path / "config", stage=str(tmp_path), python="/py", client="/h/client.py", socket="/s", pid=1,
                                base_url="http://127.0.0.1:1", alias="m", window=8192)
    rendering = delegate_confined.renderer(rec, "/bin/claude")(context)
    argv = list(rendering.argv)
    assert argv[argv.index("--tools") + 1] == "Read,Grep,Glob,Edit,Write,Bash"
    brief = rendering.files["session/brief.md"]
    assert "You have no shell" not in brief and "name every test you could not run" in brief
    for limit in ("no network", "no localhost port", "no Poolhouse node", "no nested sandbox", "no real model", "at most 600s"):
        assert limit in brief, limit
    assert "--disallowedTools" not in argv, "no tool is offered and then hidden: the session's table has two"
    assert "--strict-mcp-config" in argv and argv[argv.index("--max-turns") + 1] == "7"
    assert argv[argv.index("--output-format") + 1] == "stream-json" and "--verbose" in argv and "--print" in argv
    assert json.loads(rendering.files["session/mcp.json"])["mcpServers"] == {
        "lead": {"command": "/py", "args": ["-I", "/h/client.py", "--socket", "/s", "--pid", "1", "mcp"],
                 "timeout": int(lead_bridge.tool_timeout() * 1000)}}
    assert lead_bridge.tool_timeout() > lead_bridge.ask_wait(), "the harness gives up on a call only after the bridge has answered it"
    assert set(session_verbs.LEAD) == {"ask", "note"} and {f"mcp__lead__{name}" for name in session_verbs.LEAD} == set(LEAD_TOOLS)
    assert "mcp__lead__ask(question)" in rendering.files["session/brief.md"] and "mcp__lead__note(text)" in rendering.files["session/brief.md"]


def test_the_helpers_token_is_found_on_the_leads_board_when_the_project_is_on_another(editing, tmp_path):
    """The live failure: the lead ran in one checkout and named another as the project, so the helper (a subagent of the lead) was made on the lead's
    board while its notification hook looked for its token on the board of the project, a fresh one, and found none for every call. What the hook
    says is the lead's own message and nothing else, once."""
    node, lead, project = editing
    other = checkouts(tmp_path / "elsewhere").primary.resolve()
    (tmp_path / "harness-NOT-CLAUDE-CODE.py").write_text(HARNESS.format(python=os.path.realpath(sys.executable), project=str(other), lead=lead.name))
    fresh = place.resolve(node.client, other)
    assert started("--task", "add a feature module", "--project", str(other), "--edit") == 0
    [name] = agent_names(node, lead)
    assert fresh != store.load(name).board == node.board, "the project is on a board of its own, fresh to this lead"
    node.session(lead).post(name, "status", "a word for the helper before it starts")
    node.session(node.member("someone-else")).post(name, "status", "a stranger's word")
    answers = answering(node, lead, name, "main.py")
    assert delegate_run.run(name) == 1 and store.status(name)["state"] == "partial"
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
        stream.line(line)
    return stream.answer(status, 1.0)


def test_a_run_that_prints_no_result_is_a_failure_that_keeps_what_arrived_and_a_result_that_says_error_is_one():
    message = {"id": "a", "content": [{"type": "text", "text": "half"}], "usage": {"output_tokens": 3}}
    lost = read(b"not json", json.dumps({"type": "assistant", "message": message}).encode(), status=1)
    assert lost.is_error and "no result" in lost.why and lost.text == "half" and (lost.spent.turns, lost.spent.output_tokens) == (1, 3)
    failed = read(b'{"type": "result", "is_error": true, "result": "model refused", "num_turns": 1}')
    assert failed.is_error and failed.text == "model refused"
    fine = read(b'{"type": "system"}', b'{"type": "result", "result": "done", "usage": {"output_tokens": 4}}')
    assert not fine.is_error and fine.text == "done" and fine.spent.output_tokens == 4


def ran(editing, mode):
    """One edit delegate in the marked harness's ``mode``, run to its end: its exit status, its result, the project and what its lead was told."""
    node, lead, project = editing
    assert started("--task", f"mode:{mode}", "--project", str(project), "--edit") == 0
    [name] = agent_names(node, lead)
    status = delegate_run.run(name)
    told = [e.text for e in node.session(lead).read(limit=100).entries if e.sender == name and e.fields.get("subject", "").startswith("delegate ")]
    return SimpleNamespace(status=status, row=store.result(name), project=project, told="\n".join(told), name=name)


@pytest.mark.parametrize("mode", ["workflow-only", "edits-nothing"])
def test_a_run_that_tried_to_change_files_and_applied_nothing_is_not_done_and_says_so(editing, mode):
    run = ran(editing, mode)
    assert run.status == 1 and run.row["state"] == "unapplied" == store.status(run.name)["state"], run.row
    assert run.row["applied"] == [] and "delegate unapplied" not in run.told.split("\n")[0]
    assert "unapplied: nothing was applied to the project" in run.told, run.told
    assert f"{len(run.row['refused'])} refused" in run.told


def test_a_run_whose_changes_were_all_applied_is_done(editing):
    run = ran(editing, "clean")
    assert run.status == 0 and run.row["state"] == "done" and run.row["applied"] == ["src/feature.py"] and run.row["refused"] == [], run.row


@pytest.mark.parametrize(("mode", "why"), [("errors", "error_max_turns"), ("dies", "no result"), ("stalls", "no progress")])
def test_a_failed_edit_run_publishes_nothing_keeps_the_patch_and_names_its_place(editing, monkeypatch, mode, why):
    if mode == "stalls":
        monkeypatch.setattr(delegate_confined, "NO_PROGRESS_S", 3.0)
    began = time.monotonic()
    run = ran(editing, mode)
    assert time.monotonic() - began < 90, "a run with no progress ends"
    assert run.status == 1 and run.row["state"] == "failed" and why in run.row["error"], run.row
    assert not (run.project / "src").exists(), "a failed run publishes nothing"
    assert CANNED in run.row["text"], "what the agent had said is kept"
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
    assert run.status == 0 and run.row["state"] == "done" and "waited for its command" in run.row["text"], run.row


def test_a_shell_command_past_its_timeout_no_longer_holds_the_run(editing, monkeypatch):
    monkeypatch.setattr(delegate_confined, "NO_PROGRESS_S", 3.0)
    began = time.monotonic()
    run = ran(editing, "shell-overdue")
    assert time.monotonic() - began < 90 and run.row["state"] == "failed" and "no progress" in run.row["error"], run.row


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
    text = store.result(name)["text"]
    assert "model=200" in text and "main.py" in text, text


def test_the_edit_delegates_command_line_keeps_what_differs_per_launch_out_of_the_system_prompt(tmp_path):
    def rendered(name, task, where):
        rec = store.Delegate(name, "lead-1", "board", task, str(where), "m", source=str(where), edit=True)
        context = isolation.Context(home=where, config=where / "config", stage=str(where), python="/py", client="/h/client.py", socket="/s", pid=1,
                                    base_url="http://127.0.0.1:1", alias="m", window=8192)
        return delegate_confined.renderer(rec, "/bin/claude")(context)

    one, two = rendered("child-1", "first", tmp_path / "one"), rendered("child-2", "second", tmp_path / "two")
    assert "--exclude-dynamic-system-prompt-sections" in one.argv and "--system-prompt" not in one.argv
    assert one.files["session/brief.md"] == two.files["session/brief.md"]
