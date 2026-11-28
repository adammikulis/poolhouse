"""The Claude Code hooks of a connected project: how a phone's message reaches a session and a subagent."""

from __future__ import annotations

import io
import json

import pytest
from phone_kit import Key, linked

from poolhouse import agent_hooks, phone_hook
from poolhouse.cli import phone as command
from poolhouse.phone_mailbox import Store

SESSION = "7c1e"
MAIN = f"claude-{SESSION}"
SUB = f"subagent-{SESSION}-a9"
EVENT = {"session_id": SESSION, "cwd": "/work/project", "transcript_path": "/work/t.jsonl"}
CHILD = {**EVENT, "agent_id": "a9", "agent_type": "general-purpose"}


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "phones")


def _run(store, name, event):
    out, err = io.StringIO(), io.StringIO()
    status = phone_hook.run(name, event, store, out, err)
    return status, out.getvalue(), err.getvalue()


def _listen(store, **more):
    err = io.StringIO()
    more.setdefault("env", {"CLAUDE_CODE_ENTRYPOINT": "cli"})
    more.setdefault("sleep", lambda _s: None)
    return phone_hook.listen(store, EVENT, err, phone_hook.Host(**more)), err.getvalue()


def test_a_listener_wakes_the_session_with_the_persons_words(store):
    key = linked(store)
    store.send(MAIN, key.message(MAIN, "please run the suite"))
    status, said = _listen(store)
    assert status == 2
    assert said.startswith(phone_hook.HEADING) and "please run the suite" in said and key.phone and "Pixel" in said
    assert store.session(MAIN)["steering"] == "phone"


def test_a_listener_gives_way_to_a_newer_one_and_ends_with_its_harness(store):
    linked(store)

    def replaced(_seconds):
        store.report(MAIN, listener={"pid": 1, "id": "newer"})

    assert _listen(store, sleep=replaced)[0] == 0
    parents = iter([100, 1])
    assert _listen(store, parent=lambda: next(parents))[0] == 0

    def closed(_seconds):
        store.report(MAIN, state="ended")

    assert _listen(store, sleep=closed)[0] == 0


def test_a_run_with_nobody_at_it_gets_no_listener_unless_asked(store):
    key = linked(store)
    store.send(MAIN, key.message(MAIN, "hello"))
    assert _listen(store, env={"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}) == (0, "")
    assert store.waiting(MAIN) == 1
    assert _listen(store, env={"CLAUDE_CODE_ENTRYPOINT": "sdk-cli", phone_hook.LISTEN_ENV: "1"})[0] == 2


def test_a_working_session_takes_a_message_after_its_tool_call(store):
    key = linked(store)
    store.send(MAIN, key.message(MAIN, "also check the docs"))
    status, out, _ = _run(store, "tool", EVENT)
    context = json.loads(out)["hookSpecificOutput"]
    assert status == 0 and context["hookEventName"] == "PostToolUse" and "also check the docs" in context["additionalContext"]
    assert _run(store, "tool", EVENT)[1] == ""


def test_a_subagent_takes_its_own_message_when_its_turn_ends_and_the_lead_is_told(store):
    key = linked(store)
    out = _run(store, "subagent-start", CHILD)[1]
    assert "carries that authority" in json.loads(out)["hookSpecificOutput"]["additionalContext"]
    store.send(SUB, key.message(SUB, "add the word indigo"))
    assert _run(store, "tool", EVENT)[1] == ""   # the lead's tool call takes nothing of a running subagent
    assert _run(store, "tool", CHILD)[1] == "" and store.waiting(SUB) == 1   # beside a tool result it would be ignored
    assert "add the word indigo" in json.loads(_run(store, "subagent-stop", CHILD)[1])["reason"]
    told = _run(store, "tool", EVENT)[1]
    assert "subagent a9" in told and "add the word indigo" in told and phone_hook.HEADING in told
    assert _run(store, "tool", EVENT)[1] == ""


def test_a_message_keeps_an_ending_subagent_going(store):
    key = linked(store)
    _run(store, "subagent-start", CHILD)
    store.send(SUB, key.message(SUB, "one more thing"))
    blocked = json.loads(_run(store, "subagent-stop", CHILD)[1])
    assert blocked["decision"] == "block" and "one more thing" in blocked["reason"]
    assert _run(store, "subagent-stop", CHILD)[1] == ""
    assert store.session(SUB)["state"] == "ended"


def test_the_lead_passes_on_what_a_finished_subagent_cannot_take(store):
    key = linked(store)
    _run(store, "subagent-start", CHILD)
    _run(store, "subagent-stop", CHILD)
    store.send(SUB, key.message(SUB, "what did you find"))
    status, said = _listen(store)
    assert status == 2 and "addressed to your subagent a9" in said and "what did you find" in said


def test_only_typing_at_the_computer_moves_the_person_there(store):
    linked(store)
    store.report(MAIN, steering="phone")
    _run(store, "prompt", {**EVENT, "prompt": "<task-notification>a job ended</task-notification>"})
    _run(store, "prompt", {**EVENT, "prompt": f"{phone_hook.HEADING} \"Pixel\""})
    assert store.session(MAIN)["steering"] == "phone"
    _run(store, "prompt", {**EVENT, "prompt": "fix the bug"})
    assert store.session(MAIN)["steering"] == "computer"


QUESTION = {**EVENT, "tool_name": "AskUserQuestion", "tool_input": {"questions": [
    {"question": "Which colour?", "header": "Colour", "options": [{"label": "Red"}, {"label": "Blue"}]}]}}


def test_a_question_waits_at_the_computer_when_the_person_is_there(store):
    linked(store)
    store.report(MAIN, steering="computer")
    assert _run(store, "ask", QUESTION)[:2] == (0, "")
    assert store.open_questions() == []
    assert "Which colour?" in store.events(MAIN)[-1]["text"]


def test_a_question_is_answered_from_the_phone_the_person_is_using(store):
    key = linked(store)
    store.report(MAIN, steering="phone")

    def person(_seconds):
        question = store.open_questions()[0]["question"]
        store.answer(MAIN, question, key.answer(MAIN, question, {"Which colour?": "Blue"}))

    out = io.StringIO()
    assert phone_hook.ask(store, QUESTION, out, phone_hook.Host(sleep=person)) == 0
    answer = json.loads(out.getvalue())["hookSpecificOutput"]
    assert answer["permissionDecision"] == "allow"
    assert answer["updatedInput"]["answers"] == {"Which colour?": "Blue"}
    assert answer["updatedInput"]["questions"] == QUESTION["tool_input"]["questions"]


def test_an_unanswered_question_goes_to_the_computer(store):
    linked(store)
    store.report(MAIN, steering="phone")
    times = iter([0.0, 0.0, 10_000.0])
    out = io.StringIO()
    assert phone_hook.ask(store, QUESTION, out, phone_hook.Host(sleep=lambda _s: None, clock=lambda: next(times))) == 0
    assert out.getvalue() == "" and store.open_questions() == []
    assert [e["type"] for e in store.events(MAIN)] == ["question", "answered"]


def test_a_session_with_no_transcript_still_shows_its_replies(store, tmp_path):
    linked(store)
    secret = "ghp_" + "a1B2c3D4e5F6g7H8i9J0" * 2
    _run(store, "stop", {**EVENT, "transcript_path": str(tmp_path / "none.jsonl"), "last_assistant_message": f"done {secret}"})
    [reply] = [e for e in store.events(MAIN) if e["type"] == "reply"]
    assert reply["text"].startswith("done ") and secret not in reply["text"]
    kept = tmp_path / "kept.jsonl"
    kept.write_text("{}\n")
    _run(store, "stop", {**EVENT, "transcript_path": str(kept), "last_assistant_message": "read from the transcript"})
    assert len([e for e in store.events(MAIN) if e["type"] == "reply"]) == 1


def test_nothing_happens_until_a_phone_is_linked(store):
    assert _run(store, "session-start", EVENT) == (0, "", "")
    assert not store.root.exists()
    Key()


@pytest.mark.parametrize("name", sorted(set(phone_hook.EVENTS) - {"listen", "ask"}))
def test_every_hook_runs_from_the_command_line(name, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path))
    linked(Store())
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({**CHILD, "message": "Claude needs permission"})))
    assert command.main(["hook", name]) == 0
    assert "Traceback" not in capsys.readouterr().err


def test_a_broken_event_stops_nothing(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert command.main(["hook", "tool"]) == 1
    assert "phone hook tool" in capsys.readouterr().err


def test_connecting_a_project_writes_its_own_settings_and_disconnecting_takes_only_those(tmp_path):
    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    settings = project / ".claude" / "settings.local.json"
    foreign = {"type": "command", "command": "echo mine"}
    settings.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [foreign]}]}, "env": {"A": "1"}}))
    agent_hooks.phone(project)
    agent_hooks.phone(project)
    held = json.loads(settings.read_text())
    stops = [h for group in held["hooks"]["Stop"] for h in group["hooks"]]
    assert stops[0] == foreign and len(stops) == 3
    waking = [h for h in stops if h.get("asyncRewake")]
    assert [h["command"] for h in waking] == ["poolhouse phone hook listen"]
    assert waking[0]["timeout"] > phone_hook.LISTEN_S
    assert held["hooks"]["PreToolUse"][0]["matcher"] == "AskUserQuestion"
    assert held["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"] > phone_hook.QUESTION_S
    wired = {h["command"].split()[-1] for groups in held["hooks"].values() for g in groups for h in g["hooks"] if "phone hook" in h["command"]}
    assert wired == set(phone_hook.EVENTS)
    agent_hooks.phone(project, connect=False)
    assert json.loads(settings.read_text()) == {"hooks": {"Stop": [{"hooks": [foreign]}]}, "env": {"A": "1"}}


def test_the_home_folder_is_never_connected():
    from pathlib import Path

    with pytest.raises(ValueError, match="never written"):
        agent_hooks.phone(Path.home())
