"""Missing runtime dependencies remain unavailable before launch."""
import sys
from types import SimpleNamespace

import pytest

from poolhouse import agent_dependency
from poolhouse.fleet.chat_routes import ChatRoutes
from poolhouse.workspace import localstart


def test_daemon_import_keeps_optional_chat_runtime_unloaded(tmp_path):
    import os
    import subprocess
    from pathlib import Path

    source = str(Path(__file__).parents[1] / "src")
    script = f"""
import sys
sys.path.insert(0, {source!r})
from poolhouse.fleet import api
assert 'poolhouse.fleet.sdk_chat' not in sys.modules
assert 'agents' not in sys.modules
"""
    result = subprocess.run([sys.executable, "-I", "-B", "-c", script],
                            cwd=tmp_path, env={**os.environ, "HOME": str(tmp_path)},
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_missing_sdk_reports_dependency(monkeypatch):
    monkeypatch.setitem(sys.modules, "agents", None)
    assert "poolhouse[agents]" in agent_dependency.problem()


def test_local_chat_refuses_before_spawn(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "agents", None)
    monkeypatch.setattr(localstart.la, "check_project", lambda *args: "")
    monkeypatch.setattr(localstart.la, "check_orders", lambda value: value)
    spawned = []
    with pytest.raises(localstart.Unavailable, match="agent runtime"):
        localstart.start(SimpleNamespace(board="b"), localstart.Ask(), base=tmp_path, spawn=lambda *a, **k: spawned.append(a))
    assert not spawned


def test_chat_readiness_reports_actual_dependency(monkeypatch):
    monkeypatch.setitem(sys.modules, "agents", None)
    from poolhouse.fleet import chat_routes
    monkeypatch.setattr(chat_routes, "load_pool_key", lambda path: None)
    monkeypatch.setattr(chat_routes, "targets", lambda *args: [])
    route = ChatRoutes()
    route.ui = SimpleNamespace(pool_key_path=None, serving=None)
    route.method = "GET"
    replies = []
    route.send = lambda status, body: replies.append((status, body))
    assert route._chat()
    assert replies[0][0] == 200
    assert replies[0][1]["runtime_ready"] is False
    assert "poolhouse[agents]" in replies[0][1]["runtime_error"]


def test_missing_chat_sdk_does_not_change_coding_prerequisites(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "agents", None)
    with pytest.raises(ValueError, match="cannot enforce isolated session permissions"):
        localstart.start(SimpleNamespace(board="b"), localstart.Ask(profile="coding"), base=tmp_path)


@pytest.mark.slow
def test_missing_sdk_disables_send_and_enter(tmp_path, playwright, monkeypatch):
    from playwright.sync_api import expect
    from test_fleet_ui import Serving

    from poolhouse.fleet.conversations import Conversations
    from poolhouse.fleet.serving import Serving as ModelsServing
    from poolhouse.testing.canned import CANNED_MODEL_ID, Canned, CannedServer

    fake = CannedServer(Canned(pieces=("hello",)))
    served = Serving(tmp_path)
    served.ui.settings.setup_done = True
    served.ui.serving = ModelsServing(tmp_path / "models.json")
    served.ui.serving.register(fake.port, [CANNED_MODEL_ID])
    served.ui.conversations = Conversations(tmp_path / "chats")
    monkeypatch.setitem(sys.modules, "agents", None)
    browser = playwright.chromium.launch(headless=True)
    try:
        page = browser.new_page()
        page.goto(f"http://127.0.0.1:{served.port}/ui#chat")
        expect(page.locator("chat-view #chat-send")).to_be_disabled()
        expect(page.locator("chat-view #chat-note")).to_contain_text("agent runtime")
        page.get_by_role("textbox", name="Message", exact=True).fill("Say hello")
        page.get_by_role("textbox", name="Message", exact=True).press("Enter")
        assert not fake.sent_to("/v1/chat/completions")
    finally:
        browser.close()
        served.close()
        fake.close()


def test_chat_transport_missing_is_not_reported_as_missing_agents(monkeypatch):
    original = agent_dependency.import_module

    def importing(name):
        if name == "poolhouse.fleet.sdk_chat":
            raise ModuleNotFoundError("missing transport", name="httpx2")
        return original(name)

    monkeypatch.setattr(agent_dependency, "import_module", importing)
    problem = agent_dependency.problem()
    assert "incomplete or incompatible" in problem
    assert "poolhouse[agents]" not in problem
    route = ChatRoutes()
    replies = []
    route.send = lambda status, body: replies.append((status, body))
    route.body = lambda: pytest.fail("unavailable chat must not parse/dispatch a request")
    assert route._say([])
    assert replies == [(503, {"error": problem})]


def test_declared_agents_transport_matches_actual_sdk():
    import tomllib
    from pathlib import Path

    import httpx2
    from packaging.requirements import Requirement
    from packaging.version import Version

    from poolhouse.client.sdk import local_client
    from poolhouse.fleet.chat import Target

    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    requirements = {r.name: r for r in map(Requirement, project["project"]["optional-dependencies"]["agents"])}
    assert Version(httpx2.__version__) in requirements["httpx2"].specifier
    assert Version("3") not in requirements["httpx2"].specifier
    client = local_client(Target("local", "http://127.0.0.1:4567/v1/chat/completions"), "local")
    try:
        assert isinstance(client._client, httpx2.AsyncClient)
        assert agent_dependency.problem() == ""
    finally:
        import asyncio
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=1) as worker:
            worker.submit(asyncio.run, client.close()).result()


def test_fresh_import_detects_missing_transport(tmp_path):
    import os
    import subprocess
    from pathlib import Path

    source = str(Path(__file__).parents[1] / "src")
    script = f"""
import importlib.abc
import sys
sys.path.insert(0, {source!r})
class MissingTransport(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'httpx2':
            raise ModuleNotFoundError('missing transport', name='httpx2')
sys.meta_path.insert(0, MissingTransport())
from poolhouse import agent_dependency
problem = agent_dependency.problem()
assert 'incomplete or incompatible' in problem, problem
assert 'poolhouse[agents]' not in problem, problem
"""
    result = subprocess.run([sys.executable, "-I", "-B", "-c", script],
                            cwd=tmp_path, env={**os.environ, "HOME": str(tmp_path)},
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_chat_module_programming_error_is_not_a_dependency_failure(monkeypatch):
    original = agent_dependency.import_module

    def importing(name):
        if name == "poolhouse.fleet.sdk_chat":
            raise AttributeError("broken implementation")
        return original(name)

    monkeypatch.setattr(agent_dependency, "import_module", importing)
    with pytest.raises(AttributeError, match="broken implementation"):
        agent_dependency.problem()
