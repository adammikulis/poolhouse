"""Native adapters cannot dispatch without an enforceable workspace executor."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from poolhouse import claude, codex, coding, coding_policy, harnessing, pi
from poolhouse.fleet.conversations import Conversations
from poolhouse.workspace import coding_turns, task_coding


def forbidden(*_args, **_kwargs):
    pytest.fail("Unconfined coding reached a side effect")


@pytest.mark.parametrize("adapter", [pi, claude, codex])
@pytest.mark.parametrize("policy", [coding_policy.Policy(permission, mode) for permission in coding_policy.PERMISSIONS
                                     for mode in coding_policy.EXECUTION_MODES])
@pytest.mark.parametrize("attached", [False, True])
def test_public_native_launch_refuses_before_model_session_or_process(adapter, policy, attached, tmp_path, monkeypatch):
    for name in ("serving", "opened", "window_of"):
        monkeypatch.setattr(harnessing, name, forbidden)
    monkeypatch.setattr(adapter, "alias_of", forbidden)
    project = tmp_path / "repository"
    project.mkdir()
    name = adapter.__name__.rsplit(".", 1)[-1]
    args = ["--project", str(project), "--permissions", policy.permissions, "--execution-mode", policy.execution_mode,
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
