"""The trial driver (`poolhouse.delegate_acp`): a delegate driven over the Agent Client Protocol on the pinned OpenCode, in the real confinement,
beside today's driver (``run --format json``) where the two are compared.

It runs where the pinned release is kept and each test skips with that reason elsewhere. NOT A MODEL: the model side is the suite's canned server
(`poolhouse.testing.canned`), whose model id and replies are marked; a test that needs a reply with no text takes the server's unmarked block. It asks
for the tool calls a test names, and no model behaviour is under test. The delegate tests of `test_opencode_pinned` are collected here again, so they
run unchanged on the trial driver.
"""
from __future__ import annotations

import difflib
import json
import signal
import threading
import time
from types import SimpleNamespace

import pytest
import test_opencode_pinned as pinned
from delegate_kit import agent_names, started
from test_opencode_pinned import (  # noqa: F401  (the delegate tests of the pinned release, collected here to run on the trial driver)
    test_a_delegate_broken_off_on_the_pinned_release_takes_its_conversation_up_in_the_next_box,
    test_a_delegate_broken_off_twice_on_the_pinned_release_keeps_one_conversation,
    test_a_plugin_the_pinned_release_cannot_load_is_named_in_the_delegates_log_and_the_run_fails,
    test_a_read_only_delegate_on_the_pinned_release_reports_through_the_plugin_and_takes_nothing_from_the_projects_config,
    test_an_edit_delegate_on_the_pinned_release_has_each_call_judged_a_denied_one_not_run_and_its_shell_bounded,
    test_the_pinned_release_gives_up_on_a_replys_head_only_where_the_config_names_a_time,
    test_the_pinned_releases_server_answers_inside_its_box_and_its_shell_inherits_no_preload,
    test_the_writes_the_box_refuses_the_pinned_release_leave_the_delegate_partial_and_a_failing_command_does_not_count,
)

from poolhouse import (
    delegate_acp,
    delegate_confined,
    delegate_record,
    delegate_resume,
    delegate_run,
    delegate_store as store,
    jobs,
)
from poolhouse.testing import canned

pytest_plugins = ["node_kit", "delegate_kit", "tests.confined_kit"]

delegated, release, served = pinned.delegated, pinned.release, pinned.served   # the pinned tests' fixtures, for the tests here and those collected

BOTH = pytest.mark.parametrize("driver", ["run-json", "acp"], indirect=True)
SPENT = 7
"""The completion tokens each scripted reply says it spent; the reply after the last one says none."""


@pytest.fixture(autouse=True)
def driver(request, monkeypatch):
    """The trial driver in the runner's place, unless the test is parametrized with today's (``run-json``)."""
    name = getattr(request, "param", "acp")
    trial = delegate_acp.Driver(delegate_confined.renderer, delegate_confined.follow)
    if name == "acp":
        monkeypatch.setattr(delegate_confined, "renderer", trial.renderer)
        monkeypatch.setattr(delegate_confined, "follow", trial.follow)
    if "takes_nothing_from_the_projects_config" in request.node.name:   # that test raises today's log level; this raises the trial's
        monkeypatch.setattr(delegate_acp, "SERVE", delegate_acp.SERVE.replace("LOG_LEVEL=WARN", "LOG_LEVEL=DEBUG"))
    return SimpleNamespace(name=name, trial=trial)


@pytest.fixture
def spending(monkeypatch):
    """Each scripted reply says it spent `SPENT` tokens, so the tokens a driver reports can be counted."""
    monkeypatch.setattr(pinned, "turns", lambda asked: [canned.Turn(calls=((tool, json.dumps(arguments)),), completion_tokens=SPENT)
                                                        for tool, arguments in asked])


def events(name, kind):
    return [each for each in delegate_record.read(name)[0] if each["kind"] == kind]


def when_asked(monkeypatch, tool, then):
    """Call ``then()`` once, as the box's hook asks the parent about a call of ``tool``, before it is answered."""
    heard, done = delegate_confined.Hooks.heard, []

    def listened(self, event, payload, said):
        if event == "pre" and payload.get("tool_name") == tool and not done:
            done.append(then())
        heard(self, event, payload, said)

    monkeypatch.setattr(delegate_confined.Hooks, "heard", listened)


@BOTH
def test_the_progress_each_driver_records(delegated, served, driver, spending):
    """Three replies, two of them a tool call with no text (PLUMBING, not a model)."""
    with served.unmarked():
        ran = delegated.run([("write", {"path": "made.py", "content": "x = 1\n"}), ("shell", {"command": "false"})], "--edit")
    assert ran.status == 0 and ran.row["applied"] == ["made.py"], (ran.row, ran.log[-4000:])
    assert len(served.bodies) == 3, "no request but the three of the task: none for a title"
    assert [(each["tool"], each["status"], each["exit"]) for each in events(ran.name, "result")] == [
        ("write", "completed", None), ("shell", "completed", 1)], events(ran.name, "result")
    assert [each["command"] for each in events(ran.name, "call") if each["tool"] == "shell"] != [""]
    assert ran.row["output_tokens"] == 2 * SPENT and store.status(ran.name)["spent"]["output_tokens"] == 2 * SPENT
    steps, usage = events(ran.name, "step"), events(ran.name, "usage")
    if driver.name == "run-json":
        assert [each["turn"] for each in steps] == [1, 2, 3] and [each["output_tokens"] for each in usage] == [SPENT, SPENT, 0]
        assert [each["reason"] for each in usage] == ["tool-calls", "tool-calls", "stop"] and ran.row["turns"] == 3
    else:
        assert steps == [] and usage == [], "the protocol says neither when a step starts nor what it spent"
        assert ran.row["turns"] == 1, "only replies that carried text can be counted, so the turns are a lower bound"


@BOTH
def test_a_limit_of_turns_under_each_driver(delegated, served, driver):
    ran = delegated.run([("glob", {"pattern": "*"}), ("glob", {"pattern": "**/*"})], "--max-turns", "1")
    if driver.name == "run-json":
        assert ran.status == 1 and "limit of 1 turn" in ran.row["error"], ran.row
    else:
        assert ran.status == 0 and len(events(ran.name, "result")) == 2, "the protocol ends no step, so the limit is not kept"


@BOTH
def test_a_stop_during_a_command_ends_the_run_and_keeps_what_it_had(delegated, served, driver, spending, monkeypatch):
    stopped_at = []

    def stop():
        main = threading.main_thread().ident

        def send():
            stopped_at.append(time.monotonic())
            signal.pthread_kill(main, signal.SIGTERM)

        threading.Timer(1.5, send).start()

    when_asked(monkeypatch, "Bash", stop)
    ran = delegated.run([("write", {"path": "made.py", "content": "x = 1\n"}), ("shell", {"command": "sleep 120", "timeout": 130000})], "--edit")
    assert time.monotonic() - stopped_at[0] < 30, "the stop did not wait for the command"
    assert ran.row["state"] == "stopped" and canned.CANNED_MARKER in ran.row["text"], (ran.row, ran.log[-4000:])
    assert ran.row["patches"] and not (delegated.project / "made.py").exists(), (ran.row, ran.log[-4000:])
    assert len(served.bodies) == 2, "no request after the stop"
    assert ran.row["output_tokens"] >= SPENT, ran.row
    if driver.name == "acp":
        assert ran.row["subtype"] == "cancelled", "the agent answered the cancel, with the tokens of the turn"


@BOTH
def test_a_run_broken_off_by_an_error_resumes_on_the_same_prompt_prefix(delegated, served, driver, spending, monkeypatch):
    """The server refuses one request after the first tool call (PLUMBING, not a model)."""
    heard, original, mended = delegate_confined.Hooks.heard, delegate_resume.waited, []

    def broken(self, event, payload, said):
        if event == "post" and "tool_use_id" in payload and not mended:
            served.refuse[canned.CHAT] = 400
        heard(self, event, payload, said)

    def mend(model, say):
        mended.append(served.refuse.clear())
        served.turns = []
        return original(model, say)

    monkeypatch.setattr(delegate_confined.Hooks, "heard", broken)
    monkeypatch.setattr(delegate_resume, "waited", mend)
    ran = delegated.run([("glob", {"pattern": "**/*"})], "--edit", resumes=1)
    assert ran.status == 0 and ran.row["state"] == "done", (ran.row, ran.log[-4000:])
    first, last = served.bodies[1], served.bodies[-1]   # the request the server refused, and the one of the attempt that continued
    assert last["tools"] == first["tools"], ("tools", first["tools"], last["tools"])
    assert len(last["messages"]) > len(first["messages"])
    differs = [line for old, new in zip(first["messages"], last["messages"], strict=False) if old != new
               for line in difflib.ndiff(str(old["content"]).splitlines(), str(new["content"]).splitlines()) if line[0] in "+-"]
    # Under both drivers the resumed prompt names the first box's folder where the first request had ".": a finding of the trial, with no other
    # difference. A prompt that is the same passes too.
    assert all(line[2:].strip().startswith(("Working directory:", "Workspace root folder:")) for line in differs), ("messages", differs)
    ends = events(ran.name, "attempt_end")
    assert [bool(each["interrupted"]) for each in ends] == [True, False] and "refusing" in ends[0]["why"], ends
    assert ends[0]["output_tokens"] == (SPENT if driver.name == "run-json" else 0), "the protocol's error carries no usage"


@BOTH
def test_a_message_the_lead_sends_during_the_task_reaches_the_model(delegated, served, driver, lead_node, monkeypatch):
    node, lead, _project = lead_node
    when_asked(monkeypatch, "Bash", lambda: node.session(lead).post(agent_names(node, lead)[0], "status", "PLUMBING mid-task word"))
    ran = delegated.run([("glob", {"pattern": "*"}), ("shell", {"command": "echo PLUMBING"})], "--edit")
    assert ran.status == 0, (ran.row, ran.log[-4000:])
    results = pinned.offered(served)[1]
    assert "PLUMBING mid-task word" not in results[0] and "PLUMBING mid-task word" in results[1], results


@pytest.mark.parametrize("driver", ["run-json"], indirect=True)
def test_both_drivers_give_the_model_the_same_prompt_and_tools_and_the_agent_takes_neither_from_the_client(delegated, served, driver, lead_node,
                                                                                                           monkeypatch):
    node, lead, project = lead_node
    delegated.run([("glob", {"pattern": "*"})])
    today, before = served.bodies[0], len(served.bodies)
    monkeypatch.setattr(delegate_confined, "renderer", driver.trial.renderer)
    monkeypatch.setattr(delegate_confined, "follow", driver.trial.follow)
    known = set(agent_names(node, lead))
    assert started("--task", "PLUMBING: call the tools", "--project", str(project.resolve()), "--name", "second") == 0
    [name] = set(agent_names(node, lead)) - known
    served.turns = pinned.turns([("glob", {"pattern": "*"})])
    assert delegate_run.run(name) == 0, store.log_file(name).read_text(errors="replace")[-4000:]
    ours = served.bodies[before]
    assert ours["tools"] == today["tools"], ("tools", today["tools"], ours["tools"])
    first, second = known - {lead.name}, name   # the task's message names its delegate, the one difference between the two requests
    worded = json.dumps(today["messages"]).replace(next(iter(first)), second)
    assert json.dumps(ours["messages"]) == worded, ("messages", today["messages"], ours["messages"])
    capabilities = driver.trial.agent["agentCapabilities"]
    assert "systemPrompt" not in capabilities["sessionCapabilities"], "the agent takes no system prompt from the client"


def test_a_request_to_run_a_tool_is_refused_by_the_client_and_the_tool_does_not_run(delegated, served, monkeypatch):
    rules = delegate_confined.permissions
    monkeypatch.setattr(delegate_confined, "permissions", lambda tools: [*rules(tools), {"action": "shell", "resource": "*", "effect": "ask"}])
    ran = delegated.run([("shell", {"command": "echo PLUMBING > ran.txt"})], "--edit")
    assert ran.status == 1 and ran.row["subtype"] == "cancelled", ("a refusal over the protocol ends the turn", ran.row, ran.log[-4000:])
    assert not (delegated.project / "ran.txt").exists() and len(served.bodies) == 1


def test_the_client_gives_the_agent_no_file_no_terminal_and_no_permission():
    """PLUMBING: hand-written protocol lines, as a program in the box could write them."""
    sent = []
    talk = delegate_acp.Talk(sent.append, lambda: None, delegate_confined.Stream(), "/stage", "")
    talk.start("PLUMBING task")
    asked = sent[0]
    assert asked["method"] == "initialize" and set(asked["params"]["clientCapabilities"]) == set()
    for number, method in enumerate(("fs/read_text_file", "fs/write_text_file", "terminal/create", "elicitation/create")):
        talk.line(json.dumps({"jsonrpc": "2.0", "id": number, "method": method, "params": {"path": "/etc/hosts"}}).encode())
        assert sent[-1]["id"] == number and sent[-1]["error"]["code"] == -32601, sent[-1]
    options = [{"optionId": "once", "kind": "allow_once"}, {"optionId": "no", "kind": "reject_once"}]
    talk.line(json.dumps({"jsonrpc": "2.0", "id": 9, "method": "session/request_permission", "params": {"options": options}}).encode())
    assert sent[-1]["result"] == {"outcome": {"outcome": "selected", "optionId": "no"}}
    talk.line(json.dumps({"jsonrpc": "2.0", "id": 10, "method": "session/request_permission", "params": {"options": options[:1]}}).encode())
    assert sent[-1]["result"] == {"outcome": {"outcome": "cancelled"}}
    for junk in (b"not json", b"[1]", b'{"method": 3, "id": {}}', b'{"id": 1, "result": null}'):
        talk.line(junk)


@pytest.mark.parametrize("driver", ["run-json"], indirect=True)
def test_the_entry_point_for_a_live_run_starts_a_delegate_that_runs_over_the_protocol(delegated, served, driver, lead_node, detached):
    """The runner's own driver is today's here, so what `run` runs is what it put in place itself."""
    node, lead, project = lead_node
    plain, follow = jobs.detach, delegate_confined.follow
    assert delegate_acp.start(["--task", "PLUMBING: say done", "--project", str(project), "--no-review", "--resumes", "0"]) == 0
    [name] = agent_names(node, lead)
    assert detached[-1]["module"] == "poolhouse.delegate_acp" and detached[-1]["argv"] == ["run", name] and jobs.detach is plain
    assert delegate_acp.run(name) == 0
    assert store.result(name)["state"] == "done" and events(name, "step") == [] and delegate_confined.follow is follow
