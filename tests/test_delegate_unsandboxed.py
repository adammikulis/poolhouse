"""A delegate on a platform that has no sandbox: it starts read-only, a higher tier is the lead's own argument and says one warning, and it runs
on the folder itself with the hook asked before every call. A computer whose sandbox cannot be used refuses it, and runs nothing.

PLUMBING ONLY: no model is served and no OpenCode runs. A marked script stands in OpenCode's place; it reads the config and the plugin the delegate
wrote, asks the hook the plugin names and starts the MCP server the config names, as OpenCode would.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import signal
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from delegate_kit import agent_names, said, started

from poolhouse import (
    delegate,
    delegate_confined,
    delegate_guard,
    delegate_run,
    delegate_store as store,
    lead_bridge,
)
from poolhouse.isolation.refusals import sentence
from tests import isolation_kit as kit
from tests.sandbox_kit import AbsentBackend, UnloggedBackend

pytest_plugins = ["node_kit", "delegate_kit", "tests.confined_kit"]
CAPABILITY = sys.modules["poolhouse.isolation.capability"]

STAND_IN = r'''#!{python}
# PLUMBING ONLY: not OpenCode, and no model
import json, os, re, subprocess, sys

config = json.load(open(os.environ["OPENCODE_CONFIG"]))
plugin = open(os.path.join(config["plugins"][0], "index.js")).read()
cfg = json.loads(re.search(r"^const cfg = (.*);$", plugin, re.M).group(1))
task = sys.stdin.read()


def hook(event, payload):
    done = subprocess.run([*cfg["hook"], event], input=json.dumps(payload), capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def line(kind, **part):
    print(json.dumps({{"type": kind, "sessionID": "plumbing", "part": part}}), flush=True)


def asked(name, **given):
    answer = hook("pre", {{"tool_name": name, "tool_input": given, "tool_use_id": name + str(given)}})["hookSpecificOutput"]
    return answer["permissionDecision"]


line("step_start")
if "mode:no-plugin" in task:   # as OpenCode runs on without a plugin it cannot load: nothing is asked of the hook
    line("step_finish", reason="stop", tokens={{"input": 1, "output": 1}})
    sys.exit(0)
hook("post", {{"loaded": True}})
inside = asked("Write", file_path="made.py")
if inside == "allow":
    open("made.py", "w").write("x = 1\n")
outside = asked("Write", file_path={outside!r})
force = asked("Bash", command="git push --force")
server = subprocess.Popen(config["mcp"]["lead"]["command"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)


def rpc(number, method, **params):
    server.stdin.write(json.dumps({{"jsonrpc": "2.0", "id": number, "method": method, "params": params}}) + "\n")
    server.stdin.flush()
    return json.loads(server.stdout.readline())["result"]


tools = ",".join(each["name"] for each in rpc(1, "tools/list")["tools"])
note = rpc(2, "tools/call", name="note", arguments={{"text": "halfway there"}})
server.stdin.close()
found = {{"inside": inside, "outside": outside, "force": force, "tools": tools, "note": note["isError"], "home": os.environ["HOME"],
         "keys": sorted(name for name in os.environ if "KEY" in name or "TOKEN" in name), "cwd": os.getcwd(), "argv": sys.argv[1:],
         "base": config["provider"]["poolhouse"]["options"]["baseURL"], "token": config["provider"]["poolhouse"]["options"]["apiKey"],
         "rules": config["agents"]["poolhouse"]["permissions"], "system": config["agents"]["poolhouse"]["system"]}}
line("text", text=json.dumps(found))
line("step_finish", reason="stop", tokens={{"input": 1, "output": 1}})
'''


def on(monkeypatch, platform, backend):
    """This computer as one of ``platform`` whose sandbox backend is ``backend``, read again."""
    monkeypatch.setattr(CAPABILITY, "_family", lambda: platform)
    kit.without_a_sandbox(monkeypatch, backend)


@pytest.fixture
def unsandboxed(lead_node, detached, tmp_path, monkeypatch):
    """A lead on a platform with no sandbox, with the marked script in OpenCode's place and a server address that is never asked."""
    node, lead, project = lead_node
    on(monkeypatch, "win32", AbsentBackend)
    project = project.resolve()
    outside = tmp_path / "elsewhere" / "planted"
    outside.parent.mkdir()
    release = tmp_path / "release"
    release.mkdir()
    script = release / "opencode"
    script.write_text(STAND_IN.format(python=os.path.realpath(sys.executable), outside=str(outside)))
    script.chmod(0o755)

    @contextlib.contextmanager
    def leased(_model, _say):
        yield "http://127.0.0.1:9", SimpleNamespace(serving=SimpleNamespace(slot_context=8192)), "canned.gguf"

    monkeypatch.setattr(delegate_confined, "leased", leased)
    monkeypatch.setattr(delegate_run.delegate_unboxed.harnessing, "binary_for", lambda _name, **_: str(script))
    monkeypatch.setattr(delegate_run.delegate_unboxed.claude, "alias_of", lambda _url, _model: kit.MODEL)
    monkeypatch.setattr(lead_bridge, "POLL_S", 0.05)
    monkeypatch.setenv("POOLHOUSE_HOME", str(node.state.parent))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a-hosted-key")
    monkeypatch.setenv("GITHUB_TOKEN", "a-login")
    before = signal.getsignal(signal.SIGTERM)
    yield node, lead, project, outside
    signal.signal(signal.SIGTERM, before)
    lead_bridge.STOPPED.clear()


def ran(node, lead, *words, state="done"):
    assert started(*words) == 0
    [name] = agent_names(node, lead)
    code = delegate_run.run(name)
    row = store.result(name)
    assert (row["state"], code) == (state, 0 if state == "done" else 1), (row, store.log_file(name).read_text())
    return name, row


def test_a_delegate_on_a_computer_that_cannot_sandbox_starts_read_only_on_the_folder_itself(unsandboxed, capsys):
    node, lead, project, outside = unsandboxed
    name, row = ran(node, lead, "--task", "look around", "--project", str(project))
    rec = store.load(name)
    assert (rec.permissions, rec.edit, rec.sandboxed, rec.workdir) == ("read-only", False, False, str(project))
    told = said(capsys)
    assert "cannot sandbox" in told and "no shell and no editing" in told and "warning:" not in told
    found = json.loads(row["text"])
    assert (found["inside"], found["outside"], found["force"]) == ("deny", "deny", "deny")
    assert [each["action"] for each in found["rules"] if each["effect"] == "allow"] == ["read", "glob", "grep", "lead_ask", "lead_note"]
    assert not (project / "made.py").exists() and not outside.exists()


def test_an_edit_delegate_with_no_sandbox_warns_edits_the_folder_is_blocked_outside_it_and_reaches_its_lead(unsandboxed, capsys):
    node, lead, project, outside = unsandboxed
    _name, row = ran(node, lead, "--task", "add a module", "--project", str(project), "--edit")
    told = said(capsys)
    assert told.count("warning:") == 1 and "not a boundary" in told and "Only a sandbox stops that" in told
    found = json.loads(row["text"].split("\n\nThis run had no sandbox")[0])
    assert (found["inside"], found["outside"], found["force"]) == ("allow", "deny", "deny")
    assert (project / "made.py").read_text() == "x = 1\n" and not outside.exists() and found["cwd"] == str(project)
    assert found["tools"] == "ask,note" and found["note"] is False
    assert any("halfway there" in each.text for each in node.session(lead).read(inbox=True).entries)
    assert found["home"] != os.environ["HOME"] and not Path(found["home"]).exists(), "a home of its own, removed with the run"
    assert found["keys"] == [], "no key or login of the starting process"
    assert found["base"].startswith("http://127.0.0.1:") and not found["base"].startswith("http://127.0.0.1:9/") and len(found["token"]) >= 32
    assert "--standalone" in found["argv"] and "no sandbox" in found["system"]


def test_a_delegate_whose_plugin_never_asked_the_hook_is_ended_as_failed(unsandboxed):
    node, lead, _project, _outside = unsandboxed
    _name, row = ran(node, lead, "--task", "mode:no-plugin", state="failed")
    assert "without poolhouse's plugin" in row["error"]


@pytest.mark.parametrize("words", [["--permissions", "guarded"], ["--permissions", "yolo"], ["--edit"]])
def test_a_higher_tier_is_the_leads_own_argument_and_says_its_risk_once(unsandboxed, capsys, words):
    node, lead, project, _outside = unsandboxed
    assert started("--task", "do things", "--project", str(project), *words) == 0
    [name] = agent_names(node, lead)
    assert store.load(name).permissions == (words[1] if len(words) > 1 else "guarded")
    told = said(capsys)
    assert told.count("warning:") == 1 and "not a boundary" in told
    assert ("nothing judging it" in told) is (words[-1] == "yolo")


def test_edit_and_read_only_together_are_refused_at_once(unsandboxed, capsys):
    node, lead, _project, _outside = unsandboxed
    assert started("--task", "do things", "--edit", "--permissions", "read-only") == 2
    assert "give one of them" in said(capsys) and agent_names(node, lead) == []


@pytest.mark.parametrize("words", [[], ["--permissions", "yolo"]])
def test_a_computer_that_can_sandbox_keeps_every_tier_sandboxed(lead_node, detached, capsys, monkeypatch, words):
    node, lead, project = lead_node
    monkeypatch.setattr(delegate_confined, "unsandboxable", lambda: "")
    monkeypatch.setattr(delegate_confined, "admit", lambda _rec, _asked: None)
    assert started("--task", "look around", "--project", str(project), *words) == 0
    [name] = agent_names(node, lead)
    assert store.load(name).sandboxed is True and "warning:" not in said(capsys)
    assert delegate.tier_of(False, "") == "read-only"


@pytest.mark.redteam
@pytest.mark.usefixtures("from_a_terminal")
@pytest.mark.parametrize("case", [("darwin", AbsentBackend, False, "no_sandbox_tool"), ("darwin", UnloggedBackend, True, "nested"),
                                  ("linux", AbsentBackend, False, "userns_off"), ("win32", UnloggedBackend, True, "nested")],
                         ids=["the-tool-is-missing", "a-nested-sandbox", "the-probe-failed", "nested-where-there-is-none"])
def test_a_computer_whose_sandbox_cannot_be_used_refuses_the_delegate_and_runs_nothing(lead_node, detached, capsys, monkeypatch, case):
    node, lead, project = lead_node
    platform, backend, nested, why = case
    on(monkeypatch, platform, backend)
    monkeypatch.setattr(CAPABILITY, "confined", lambda: nested)
    for words in ([], ["--permissions", "yolo"]):
        assert started("--task", "do things", "--project", str(project), *words) == 2
        assert sentence(why) in said(capsys)
    assert agent_names(node, lead) == [] and detached == []


@pytest.mark.usefixtures("from_a_terminal")
def test_a_folder_a_capable_computer_cannot_stage_is_refused_and_never_runs_unsandboxed(lead_node, detached, capsys, monkeypatch, tmp_path):
    node, lead, _project = lead_node
    on(monkeypatch, "darwin", UnloggedBackend)
    monkeypatch.setattr(CAPABILITY, "confined", lambda: False)
    plain = tmp_path / "plain-folder"
    plain.mkdir()
    assert started("--task", "do things", "--edit", "--project", str(plain)) == 2
    assert sentence("not_a_repository") in said(capsys)
    assert agent_names(node, lead) == [] and detached == []


def answered(tmp_path, tier, name, **given):
    """The hook's exit status, decision and calls for one call."""
    root, home = tmp_path / "project", tmp_path / "person"
    for each in (root, home):
        each.mkdir(exist_ok=True)
    out = io.StringIO()
    calls = tmp_path / "calls.jsonl"
    words = ["--tier", tier, "--root", str(root), "--home", str(home), "--state", str(home / ".poolhouse"), "--scratch", str(tmp_path / "tmp"),
             "--calls", str(calls), "pre"]
    code = delegate_guard.run(words, io.StringIO(json.dumps({"tool_name": name, "tool_input": given, "tool_use_id": "1"})), out)
    decision = json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"] if out.getvalue() else ""
    return code, decision, calls.read_text() if calls.exists() else ""


@pytest.fixture
def own_environment(monkeypatch):
    for name in ("HOME", "POOLHOUSE_HOME"):
        monkeypatch.setenv(name, os.environ.get(name, ""))


@pytest.mark.redteam
@pytest.mark.parametrize("case", [
    ("yolo", "Bash", {"command": "git push --force"}, "deny"), ("yolo", "Bash", {"command": "rm -rf build"}, "allow"),
    ("guarded", "Bash", {"command": "rm -rf build"}, "deny"), ("guarded", "Write", {"file_path": "a.py"}, "allow"),
    ("read-only", "Write", {"file_path": "a.py"}, "deny"), ("read-only", "Read", {"file_path": "a.py"}, "allow"),
    ("read-only", "mcp__lead__ask", {"question": "which?"}, "allow"),
])
def test_the_hook_applies_the_blocks_in_every_tier_and_the_classifier_below_yolo(tmp_path, own_environment, case):
    tier, name, given, decision = case
    code, answer, calls = answered(tmp_path, tier, name, **given)
    assert (code, answer) == (0, decision) and name in calls


@pytest.mark.redteam
def test_a_hook_that_cannot_judge_a_call_blocks_it(tmp_path, own_environment):
    assert delegate_guard.run(["--tier", "yolo", "pre"], io.StringIO("{}"), io.StringIO()) == 2
    assert delegate_guard.run(["pre"], io.StringIO("not json"), io.StringIO()) == 2
