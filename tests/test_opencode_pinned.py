"""The real pinned OpenCode (`opencode_install.VERSION`) on poolhouse's config, plugin and serve script, inside the real confinement.

It runs where the release is kept under the account's state root (read, and copied into the test's own directory; nothing there is written) and each
test skips with that reason elsewhere. NOT A MODEL: the model side is the suite's canned server (`poolhouse.testing.canned`), whose model id
(CANNED-NOT-A-MODEL) and every reply are marked. It asks for the tool calls a test names and no model behaviour is under test; the lead is the test
node's.
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import signal
from types import SimpleNamespace

import pytest
from confined_kit import repository
from delegate_kit import agent_names, started

from poolhouse import (
    delegate_confined,
    delegate_run,
    delegate_store as store,
    harnessing,
    home,
    lead_bridge,
    opencode,
    opencode_install,
)
from poolhouse.testing import canned

pytest_plugins = ["node_kit", "delegate_kit", "tests.confined_kit"]

SAID = canned.marked("done")
"""What the canned server says once the calls a test named have run out."""


@pytest.fixture
def release(_real_home, tmp_path):
    """The pinned release as a copy of the test's own (the binary, and the ripgrep kept beside it)."""
    kept = _real_home.state / "tools" / "opencode" / opencode_install.VERSION / "bin"
    if not all((kept / name).is_file() for name in ("opencode", "rg")):
        pytest.skip(f"the pinned OpenCode {opencode_install.VERSION} is not installed in {kept}, so the real binary cannot be run")
    copy = tmp_path / "release" / "bin"
    copy.mkdir(parents=True)
    for name in ("opencode", "rg"):
        shutil.copy2(kept / name, copy / name)
    return str((copy / "opencode").resolve())


@pytest.fixture
def served():
    with canned.canned_server(turns=[]) as server:
        yield server


def turns(asked):
    """One scripted reply of the canned server per ``(tool, arguments)`` of ``asked``, each asking for that call."""
    return [canned.Turn(calls=((tool, json.dumps(arguments)),)) for tool, arguments in asked]


def offered(server):
    """The requests that offered the model tools, and the text of each tool result the last of them carries."""
    asked = [each for each in server.bodies if each.get("tools")]
    return asked, [str(each["content"]) for each in asked[-1]["messages"] if each["role"] == "tool"] if asked else []


def tools_in(request):
    return sorted(each["function"]["name"] for each in request["tools"])


def system_of(request):
    return "".join(str(each["content"]) for each in request["messages"] if each["role"] == "system")


@pytest.fixture
def delegated(lead_node, release, served, monkeypatch, request):
    """A lead's project, and ``run(asked, *words)``: one delegate in it, run by the delegate's own runner on the real binary while the canned server
    asks for each call of ``asked``; the answer carries what the parent heard from the box and the delegate's log."""
    for each in ("detached", "confinable", "from_a_terminal"):
        request.getfixturevalue(each)
    node, lead, project = lead_node
    project = project.resolve()

    @contextlib.contextmanager
    def leased(_model, _say):
        yield served.base_url, SimpleNamespace(serving=SimpleNamespace(slot_context=32768)), "canned.gguf"

    heard, original = [], delegate_confined.Hooks.heard

    def listened(self, event, payload, said):
        heard.append((event, dict(payload)))
        original(self, event, payload, said)

    monkeypatch.setattr(delegate_confined, "leased", leased)
    monkeypatch.setattr(delegate_confined.harnessing, "binary_for", lambda _name: release)
    monkeypatch.setattr(delegate_confined.claude, "alias_of", lambda _url, _model: canned.CANNED_MODEL_ID)
    monkeypatch.setattr(delegate_confined.Hooks, "heard", listened)
    monkeypatch.setattr(lead_bridge, "POLL_S", 0.05)
    before = signal.getsignal(signal.SIGTERM)

    def run(asked, *words):
        served.turns = turns(asked)
        assert started("--task", "PLUMBING: call the tools", "--project", str(project), *words) == 0
        [name] = agent_names(node, lead)
        node.session(lead).post(name, "status", "PLUMBING word from the lead")
        status = delegate_run.run(name)
        return SimpleNamespace(status=status, row=store.result(name), log=store.log_file(name).read_text(errors="replace"), heard=heard)

    yield SimpleNamespace(project=project, run=run)
    signal.signal(signal.SIGTERM, before)
    lead_bridge.STOPPED.clear()


def judged(ran):
    """The tool calls the parent was asked about, as ``(name, id)``, and the ids it was then told had ended."""
    asked = [(payload["tool_name"], payload["tool_use_id"]) for event, payload in ran.heard if event == "pre"]
    return asked, [payload["tool_use_id"] for event, payload in ran.heard if event == "post" and "tool_use_id" in payload]


def hostile(project):
    """A project that brings its own OpenCode config and plugins: read, each would take poolhouse's plugin or the agent's brief away."""
    (project / "opencode.json").write_text(json.dumps({"plugins": ["-poolhouse"], "agents": {opencode.AGENT: {"system": "PROJECT-CONFIG-SYSTEM"}}}))
    (project / ".opencode" / "plugins").mkdir(parents=True)
    (project / ".opencode" / "opencode.json").write_text(json.dumps({"plugins": ["-poolhouse"]}))
    (project / ".opencode" / "plugins" / "own.js").write_text(
        'import fs from "node:fs"; export default {id: "own", setup: async () => { fs.writeFileSync("PROJECT-PLUGIN-RAN", "x") }};\n')


def test_a_read_only_delegate_on_the_pinned_release_reports_through_the_plugin_and_takes_nothing_from_the_projects_config(delegated, served,
                                                                                                                         monkeypatch):
    monkeypatch.setattr(opencode, "SERVE", opencode.SERVE.replace("--log-level warn", "--log-level debug"))
    hostile(delegated.project)
    ran = delegated.run([("glob", {"pattern": "**/*", "hidden": True}), ("read", {"path": "opencode.json"}),
                         ("write", {"path": "planted.py", "content": "x = 1\n"})])
    assert ran.status == 0 and ran.row["state"] == "done" and SAID in ran.row["text"], (ran.row, ran.log[-4000:])
    asked, ended = judged(ran)
    assert ran.heard[0] == ("post", {"loaded": True}), "the plugin reports before the first request to the model"
    assert [name for name, _ in asked] == ["Glob", "Read", "Write"] and ended == [call for _, call in asked][:2], ran.heard
    requests, results = offered(served)
    assert tools_in(requests[0]) == ["glob", "grep", "lead_ask", "lead_note", "read"]
    system = system_of(requests[0])
    assert "You are a coding agent on a local model." in system and "PROJECT-CONFIG-SYSTEM" not in system
    assert "Working directory: ." in system and str(delegated.project) not in system, "the context hook keeps the launch's paths out of the prompt"
    assert "opencode.json" in results[0] and "own.js" in results[0] and "PROJECT-CONFIG-SYSTEM" in results[1], "the files were there to be taken"
    assert "PLUMBING word from the lead" in results[0], "what the parent answers after a call is added to the tool's result"
    assert "poolhouse: " in results[2] and not (delegated.project / "planted.py").exists(), "an acting call of a reading session is refused"
    assert "level=DEBUG" in ran.log and "configuration normalization diagnostic" not in ran.log, "this release reads every key of the config"


def test_an_edit_delegate_on_the_pinned_release_has_each_call_judged_a_denied_one_not_run_and_its_shell_bounded(delegated, served):
    hostile(delegated.project)
    launchers = home.state("node")   # a call that names the launcher's own state is the one a boxed session that may write is refused
    ran = delegated.run([("shell", {"command": f"echo PLUMBING > denied.txt; ls {launchers}"}),
                         ("write", {"path": "made.py", "content": "x = 1\n"}),
                         ("edit", {"path": "made.py", "oldString": "x = 1", "newString": "x = 2"}),
                         ("shell", {"command": "cat made.py; sleep 30", "timeout": 1000, "background": True}),
                         ("grep", {"pattern": "x = 2"})], "--edit")
    assert ran.status == 0 and ran.row["state"] == "done", (ran.row, ran.log[-4000:])
    asked, ended = judged(ran)
    assert [name for name, _ in asked] == ["Bash", "Write", "Edit", "Bash", "Grep"], ran.heard
    assert ended == [call for _, call in asked][1:], "a denied call never ran, so nothing ended it"
    requests, results = offered(served)
    assert tools_in(requests[0]) == ["edit", "glob", "grep", "lead_ask", "lead_note", "read", "shell", "write"]
    assert "poolhouse: " in results[0] and "belongs to the launcher" in results[0], "the classifier's refusal is the tool's error"
    assert "x = 2" in results[3] and "ended at its timeout of 1s" in results[3], "the command was rewritten to end at its timeout, in the foreground"
    assert ran.row["applied"] == ["made.py"] and (delegated.project / "made.py").read_text() == "x = 2\n"
    assert not (delegated.project / "denied.txt").exists(), "the refused command never ran"
    assert not (delegated.project / "PROJECT-PLUGIN-RAN").exists(), "the project's own plugin never ran"


def test_a_plugin_the_pinned_release_cannot_load_is_named_in_the_delegates_log_and_the_run_fails(delegated, monkeypatch):
    monkeypatch.setattr(opencode, "plugin", lambda _settings: "PLUMBING: this is not a program (")
    ran = delegated.run([("glob", {"pattern": "*"})])
    assert ran.status == 1 and ran.row["state"] == "failed" and opencode.UNGUARDED in ran.row["error"], ran.row
    assert "failed to load plugin" in ran.log and "poolhouse-plugin" in ran.log, ran.log[-4000:]


@pytest.mark.usefixtures("confinable", "from_a_terminal")
def test_poolhouse_opencode_on_the_pinned_release_gives_the_model_the_brief_and_publishes_what_the_judged_run_made(release, served, monkeypatch,
                                                                                                                   tmp_path):
    project = repository(tmp_path / "project")
    run = harnessing.Session(SimpleNamespace(name="coding-test", parent=None, managed_inbox=False), project, "PLUMBING role brief")

    @contextlib.contextmanager
    def opened(*_args, **_kwargs):
        yield run

    monkeypatch.setattr(opencode.harnessing, "binary_for", lambda _: release)
    monkeypatch.setattr(opencode, "alias_of", lambda *_: canned.CANNED_MODEL_ID)
    monkeypatch.setattr(opencode.harnessing, "window_of", lambda _: 32768)
    monkeypatch.setattr(opencode.harnessing, "opened", opened)
    served.turns = turns([("write", {"path": "made.py", "content": "x = 1\n"})])
    out, err, said = io.BytesIO(), io.BytesIO(), []
    # a title given here keeps OpenCode from asking the server for one, which would take a scripted reply
    status = opencode.launch(["--on", served.base_url, "--project", str(project), "--", "--title", "PLUMBING", "PLUMBING: make the file"],
                             say=said.append, run_opencode=lambda confinement: confinement.run(None, out, err))
    assert status == 0 and f"error: {opencode.UNGUARDED}" not in said, (said, err.getvalue()[-4000:])
    requests, results = offered(served)
    assert "PLUMBING role brief" in system_of(requests[0]), "this release reads the brief the config names"
    assert "webfetch" not in tools_in(requests[0]), "this release honours the denied permission"
    assert "made.py" in results[0] and (project / "made.py").read_text() == "x = 1\n"
    assert SAID in out.getvalue().decode()
