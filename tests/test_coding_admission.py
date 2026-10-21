"""Native adapters cannot dispatch without an enforceable workspace executor."""
from __future__ import annotations

from dataclasses import asdict
from types import SimpleNamespace

import pytest

from poolhouse import claude, codex, coding, harnessing, pi, roles
from poolhouse.fleet.conversations import Conversations
from poolhouse.workspace import (
    coding_turns,
    localcli,
    localloop,
    localroute,
    localstart,
    task_coding,
    task_launch,
    task_worker,
)


def forbidden(*_args, **_kwargs):
    pytest.fail("Unconfined coding reached a side effect")


@pytest.mark.parametrize("adapter", [pi, claude, codex])
@pytest.mark.parametrize("role", roles.ROLES)
@pytest.mark.parametrize("attached", [False, True])
def test_public_native_launch_refuses_before_model_session_or_process(adapter, role, attached, tmp_path, monkeypatch):
    for name in ("serving", "opened", "window_of"):
        monkeypatch.setattr(harnessing, name, forbidden)
    monkeypatch.setattr(adapter, "alias_of", forbidden)
    project = tmp_path / "repository"
    project.mkdir()
    name = adapter.__name__.rsplit(".", 1)[-1]
    args = ["--project", str(project), "--role", role,
            f"--{name}", str(tmp_path / "installed-runtime")]
    args += ["--on", "http://127.0.0.1:12345"] if attached else ["fixture-model.gguf"]
    for _ in range(2):
        messages = []
        result = adapter.launch(args, say=messages.append, session_factory=forbidden, **{f"run_{name}": forbidden})
        assert result == 2
        assert messages == ["error: This coding runtime cannot enforce isolated session permissions"]
    assert list(project.iterdir()) == []


def test_manager_refuses_before_history_or_worker_start(monkeypatch):
    manager = coding_turns.Manager(SimpleNamespace(get=forbidden, append=forbidden))
    monkeypatch.setattr(coding_turns, "worker", forbidden)
    for _ in range(2):
        with pytest.raises(ValueError, match="cannot enforce isolated session permissions"):
            manager.start("coding-session", "inspect the selected repository")
    with pytest.raises(ValueError, match="cannot enforce isolated session permissions"):
        manager._run(None, None, "inspect the selected repository")
    assert manager.turns == {}


def test_task_refuses_before_status_conversation_or_native_launch(monkeypatch, tmp_path):
    monkeypatch.setattr(task_coding.la, "Status", forbidden)
    monkeypatch.setattr(task_coding.work_reputation, "brief_on", forbidden)
    monkeypatch.setattr(coding, "launch_coding_agent", forbidden)
    for _ in range(2):
        with pytest.raises(ValueError, match="cannot enforce isolated session permissions"):
            task_coding.perform(None, tmp_path, None, None, task_coding.Run(tmp_path, lambda: False, forbidden))
    assert list(tmp_path.iterdir()) == []


def test_ordinary_chat_storage_remains_available_without_native_admission(tmp_path):
    store = Conversations(tmp_path / "chat-state")
    chat = store.start("local-model")
    store.append(chat.id, "user", "Describe the selected project")
    updated = store.update(chat.id, settings={"context": 2048})
    assert updated.settings["context"] == 2048
    assert store.get(chat.id).messages[0].content == "Describe the selected project"


@pytest.mark.parametrize("entry", ["worker", "task-launch", "local-start", "browser", "terminal", "model-lease"])
def test_upstream_coding_entries_refuse_before_registration_or_compute(entry, tmp_path, monkeypatch):
    monkeypatch.setattr(task_worker.la, "Status", forbidden)
    monkeypatch.setattr(task_launch, "authorize", forbidden)
    monkeypatch.setattr(localstart.localmodel, "choose", forbidden)
    monkeypatch.setattr(localroute.person, "session", forbidden)
    monkeypatch.setattr(localloop.hub, "located", forbidden)
    ask = localstart.Ask(profile="coding")
    args = SimpleNamespace(**asdict(ask), agent=False, target="", task="", no_wait=True)
    args.ctx = ""
    calls = {
        "worker": lambda: task_worker.run(tmp_path, "coding-worker"),
        "task-launch": lambda: task_launch.start(None, tmp_path, "coding-worker", "assigned-task"),
        "local-start": lambda: localstart.start(None, ask, base=tmp_path, spawn=forbidden),
        "browser": lambda: localroute._write(tmp_path, forbidden, "start", b'{"profile":"coding","project":"/selected"}'),
        "terminal": lambda: localcli._start(args, None, tmp_path),
        "model-lease": lambda: localloop.lease_model(SimpleNamespace(profile="coding", model="local-model")),
    }
    for _ in range(2):
        with pytest.raises(ValueError, match="cannot enforce isolated session permissions"):
            calls[entry]()
    assert list(tmp_path.iterdir()) == []


def test_chat_agent_browser_start_keeps_its_existing_non_native_path(tmp_path, monkeypatch):
    monkeypatch.setattr(harnessing, "require_isolated_execution", forbidden)
    starter = object()
    calls = []
    def start(session, ask, **options):
        calls.append((session, ask.profile, options["base"]))
        return localstart.Started("chat-worker", 123, "local-model", ask.role)
    monkeypatch.setattr(localstart, "start", start)
    status, result = localroute._write(tmp_path, lambda: starter, "start", b'{"profile":"chat"}')
    assert status == 200 and result["name"] == "chat-worker"
    assert calls == [(starter, "chat", tmp_path)]


@pytest.mark.parametrize("harness", [*coding.HARNESSES, "unrecognized-runtime"])
def test_native_runtime_cannot_lease_by_claiming_the_chat_profile(harness, monkeypatch):
    monkeypatch.setattr(localloop.hub, "located", forbidden)
    for _ in range(2):
        with pytest.raises(ValueError, match="cannot enforce isolated session permissions"):
            localloop.lease_model(SimpleNamespace(profile="chat", harness=harness, model="local-model"))


@pytest.mark.parametrize("harness", coding.HARNESSES)
def test_native_saved_worker_cannot_enter_the_ordinary_chat_loop(harness, tmp_path, monkeypatch):
    monkeypatch.setattr(localloop.la, "load", lambda *_: SimpleNamespace(profile="chat", harness=harness))
    monkeypatch.setattr(localloop.la, "Status", forbidden)
    for _ in range(2):
        with pytest.raises(ValueError, match="cannot enforce isolated session permissions"):
            localloop.run(tmp_path, "coding-worker", localloop.Settings(serve=forbidden))
