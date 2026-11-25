"""A coding launch is admitted on a computer that can confine it, and refused at once, with the sentence of the first guarantee it cannot hold, on one
that cannot (an unavailable backend injected at the engine's one seam). Nothing is leased, registered or started before the refusal."""
from __future__ import annotations

from dataclasses import asdict
from types import SimpleNamespace

import pytest

from poolhouse import claude, codex, coding, coding_policy, harnessing, pi
from poolhouse.fleet.conversations import Conversations
from poolhouse.isolation import IsolationRefused
from poolhouse.isolation.refusals import SENTENCES
from poolhouse.workspace import (
    coding_turns,
    localcli,
    localloop,
    localroute,
    localstart,
)
from tests.isolation_kit import without_a_sandbox
from tests.sandbox_kit import AbsentBackend

SENTENCE = SENTENCES["no_sandbox_tool"]
pytest_plugins = ["tests.confined_kit"]
pytestmark = pytest.mark.usefixtures("from_a_terminal")


class Reached(AssertionError):
    """A side effect the refusal must come before."""


def forbidden(*_args, **_kwargs):
    raise Reached("a refused launch reached a side effect")


@pytest.fixture
def no_sandbox(monkeypatch):
    without_a_sandbox(monkeypatch, AbsentBackend)


@pytest.mark.parametrize("adapter", [pi, claude, codex])
@pytest.mark.parametrize("policy", [coding_policy.Policy(permission, mode) for permission in coding_policy.PERMISSIONS
                                     for mode in coding_policy.EXECUTION_MODES])
@pytest.mark.parametrize("attached", [False, True])
def test_a_public_native_launch_on_a_computer_that_cannot_confine_is_refused_with_the_sentence_before_model_session_or_process(  # noqa: PLR0913
        adapter, policy, attached, tmp_path, monkeypatch, no_sandbox):
    for name in ("serving", "opened", "window_of"):
        monkeypatch.setattr(harnessing, name, forbidden)
    monkeypatch.setattr(adapter, "alias_of", forbidden)
    project = tmp_path / "repository"
    project.mkdir()
    name = adapter.__name__.rsplit(".", 1)[-1]
    args = ["--project", str(project), "--permissions", policy.permissions, "--execution-mode", policy.execution_mode,
            f"--{name}", str(tmp_path / "installed-runtime")]
    args += ["--on", "http://127.0.0.1:12345"] if attached else ["fixture-model.gguf"]
    args += ["--", *(["--print", "go"] if name != "codex" else ["exec", "go"])] if name != "pi" else []
    for _ in range(2):
        messages = []
        result = adapter.launch(args, say=messages.append, session_factory=forbidden, **{f"run_{name}": forbidden})
        assert result == 2
        assert messages == [f"error: {SENTENCE}"]
    assert list(project.iterdir()) == []


def test_the_manager_refuses_with_the_sentence_before_history_or_worker_start_and_admits_where_it_can_confine(monkeypatch):
    manager = coding_turns.Manager(SimpleNamespace(get=forbidden, append=forbidden))
    monkeypatch.setattr(coding_turns, "worker", forbidden)
    without_a_sandbox(monkeypatch, AbsentBackend)
    for _ in range(2):
        with pytest.raises(IsolationRefused, match="built-in sandbox tool is missing"):
            manager.start("coding-session", "inspect the selected repository")
    assert manager.turns == {}
    monkeypatch.undo()
    reached = SimpleNamespace(get=forbidden, append=forbidden)
    monkeypatch.setattr(coding_turns, "worker", forbidden)
    with pytest.raises(Reached):
        coding_turns.Manager(reached).start("coding-session", "inspect the selected repository")


def test_ordinary_chat_storage_remains_available_without_native_admission(tmp_path):
    store = Conversations(tmp_path / "chat-state")
    chat = store.start("local-model")
    store.append(chat.id, "user", "Describe the selected project")
    updated = store.update(chat.id, settings={"context": 2048})
    assert updated.settings["context"] == 2048
    assert store.get(chat.id).messages[0].content == "Describe the selected project"


@pytest.mark.parametrize("entry", ["local-start", "browser", "terminal", "model-lease"])
def test_upstream_coding_entries_on_a_computer_that_cannot_confine_are_refused_with_the_sentence_before_registration_or_compute(
        entry, tmp_path, monkeypatch, no_sandbox):
    monkeypatch.setattr(localstart.localmodel, "choose", forbidden)
    monkeypatch.setattr(localroute.person, "session", forbidden)
    monkeypatch.setattr(localloop.hub, "located", forbidden)
    ask = localstart.Ask(profile="coding")
    args = SimpleNamespace(**asdict(ask), agent=False, target="", task="", no_wait=True)
    args.ctx = ""
    calls = {
        "local-start": lambda: localstart.start(None, ask, base=tmp_path, spawn=forbidden),
        "browser": lambda: localroute._write(tmp_path, forbidden, "start", b'{"profile":"coding","project":"/selected"}'),
        "terminal": lambda: localcli._start(args, None, tmp_path),
        "model-lease": lambda: localloop.lease_model(SimpleNamespace(profile="coding", model="local-model")),
    }
    for _ in range(2):
        with pytest.raises(IsolationRefused, match="built-in sandbox tool is missing"):
            calls[entry]()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("entry", ["local-start", "browser", "model-lease"])
def test_upstream_coding_entries_on_a_computer_that_confines_are_admitted_and_reach_their_first_side_effect(entry, tmp_path, monkeypatch):
    monkeypatch.setattr(localstart.localmodel, "choose", forbidden)
    monkeypatch.setattr(localroute.person, "session", forbidden)
    monkeypatch.setattr(localloop.hub, "located", forbidden)
    ask = localstart.Ask(profile="coding")
    calls = {
        "local-start": lambda: localstart.start(None, ask, base=tmp_path, spawn=forbidden),
        "browser": lambda: localroute._write(tmp_path, forbidden, "start", b'{"profile":"coding","project":"/selected"}'),
        "model-lease": lambda: localloop.lease_model(SimpleNamespace(profile="coding", model="local-model")),
    }
    with pytest.raises(Reached):
        calls[entry]()


def test_chat_agent_browser_start_keeps_its_existing_non_native_path(tmp_path, monkeypatch, no_sandbox):
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
def test_a_native_runtime_cannot_lease_by_claiming_the_chat_profile_on_a_computer_that_cannot_confine(harness, monkeypatch, no_sandbox):
    monkeypatch.setattr(localloop.hub, "located", forbidden)
    for _ in range(2):
        with pytest.raises(IsolationRefused, match="built-in sandbox tool is missing"):
            localloop.lease_model(SimpleNamespace(profile="chat", harness=harness, model="local-model"))
