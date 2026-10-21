"""Shared composer Coding lifecycle through real routes and native subprocesses."""
from __future__ import annotations

import pytest
from coding_kit import fixture_worker
from node_kit import node_binary, workspace_node  # noqa: F401
from test_fleet_ui import Serving

from poolhouse.fleet.conversation_settings import DEFAULTS
from poolhouse.fleet.conversations import Conversations
from poolhouse.workspace import coding_turns

pytestmark = pytest.mark.slow


@pytest.fixture
def coding_browser(tmp_path, monkeypatch, browser_owner, workspace_node):  # noqa: F811
    monkeypatch.setenv("POOLHOUSE_WORKSPACE_HOME", str(tmp_path / "workspace"))
    monkeypatch.setattr(coding_turns, "worker", fixture_worker)
    monkeypatch.setattr(coding_turns.harnessing, "binary_for", lambda name: "fixture-cli" if name == "codex" else "")
    served = Serving(tmp_path)
    try:
        served.ui.settings.setup_done = True
        served.ui.conversations = Conversations(tmp_path / "chats")
        project = tmp_path / "project"
        project.mkdir()
        with browser_owner.pages() as page:
            page.route("**/ui/conversations/defaults", lambda route: route.fulfill(json={"settings": {**DEFAULTS, "max_output_tokens": 13579}}))
            page.route("**/ui/chat", lambda route: route.fulfill(json={"models": [{"model": "test-model", "local": True}]}))
            page.route("**/ui/coding/catalogue", lambda route: route.fulfill(json={
                "ok": True, "harnesses": [{"name": "pi", "available": True}, {"name": "codex", "available": True}],
                "roles": [{"name": "read-only", "summary": "Inspect without editing"}],
                "default_role": "read-only", "context": 262144,
                "default_model": "test-model", "models": [{"name": "Fixture coding model", "ref": "test-model"}]}))
            yield served, page, project
    finally:
        try:
            if hasattr(served.ui, "coding_turns"):
                for turn in served.ui.coding_turns.turns.values():
                    if turn.state in ("starting", "running", "cancelling"):
                        turn.cancel()
        finally:
            served.close()


def test_shared_composer_saves_coding_settings_and_resumes_native_session(coding_browser):
    from playwright.sync_api import expect

    served, page, project = coding_browser
    page.goto(f"http://127.0.0.1:{served.port}/ui#chat")
    page.get_by_label("Tools", exact=True).check()
    page.get_by_label("Tool runtime", exact=True).select_option("codex")
    page.get_by_label("Tool permissions", exact=True).select_option("read-only")
    page.get_by_label("Project directory", exact=True).fill(str(project))
    page.get_by_label("Project directory", exact=True).press("Tab")
    expect(page.locator("chat-view #model")).to_have_value("test-model")
    page.get_by_role("button", name="Options", exact=True).click()
    output_tokens = page.get_by_label("Maximum output tokens", exact=True)
    expect(output_tokens).to_be_visible()
    expect(output_tokens).to_have_value("8192")
    expect(page.locator("#chat-output-tokens output")).to_have_text("13,579 tokens")
    output_tokens.press("End")
    output_tokens.press("Tab")
    page.get_by_role("button", name="Options", exact=True).click()
    composer = page.get_by_role("textbox", name="Message", exact=True)
    composer.fill("inspect the brakes")
    composer.press("Enter")
    expect(page.locator("chat-view #chat-messages")).to_contain_text("Inspected: inspect the brakes")
    expect(page.get_by_role("button", name="Stop", exact=True)).to_be_hidden()
    assert page.locator("chat-view textarea").count() == 1
    assert not page.locator("chat-view #chat-options").evaluate("node => node.open")
    cid = page.locator("chat-view").evaluate("node => node.open")
    saved = served.ui.conversations.get(cid)
    assert "mode" not in saved.settings
    assert saved.settings["project"] == str(project)
    assert saved.settings["harness"] == "codex"
    assert saved.settings["role"] == "read-only"
    assert saved.settings["max_output_tokens"] == 8192
    page.reload()
    expect(page.get_by_label("Tools", exact=True)).to_be_checked()
    expect(page.get_by_label("Project directory", exact=True)).to_have_value(str(project))
    page.get_by_role("button", name="Options", exact=True).click()
    expect(output_tokens).to_be_visible()
    expect(output_tokens).to_have_value("8192")
    page.get_by_role("button", name="Options", exact=True).click()
    composer.fill("inspect the sensors")
    composer.press("Enter")
    expect(page.locator("chat-view #chat-messages")).to_contain_text("Inspected: inspect the sensors")
    expect(page.get_by_role("button", name="Stop", exact=True)).to_be_hidden()
    assert len(served.ui.conversations.get(cid).messages) == 4


def test_reload_reattaches_running_coding_turn_and_stop_keeps_the_conversation(coding_browser):
    from playwright.sync_api import expect

    served, page, project = coding_browser
    saved = served.ui.conversations.start(model="test-model", title="Live coding", settings={
        "project": str(project), "role": "read-only", "harness": "codex"})
    page.goto(f"http://127.0.0.1:{served.port}/ui#chat")
    page.get_by_role("link", name="Live coding", exact=True).click()
    expect(page.get_by_label("Tools", exact=True)).to_be_checked()
    expect(page.get_by_label("Project directory", exact=True)).to_have_value(str(project))
    expect(page.locator("chat-view #model")).to_have_value("test-model")
    expect(page.locator("chat-view #chat-send")).to_be_enabled()
    composer = page.get_by_role("textbox", name="Message", exact=True)
    composer.fill("wait until cancelled")
    composer.press("Enter")
    expect(page.locator("chat-view #chat-status")).to_contain_text("running")
    page.reload()
    expect(page.get_by_role("button", name="Stop", exact=True)).to_be_visible()
    page.get_by_role("button", name="Stop", exact=True).click()
    expect(page.get_by_role("button", name="Stop", exact=True)).to_be_hidden()
    assert served.ui.coding_turns.status(saved.id)["state"] == "cancelled"
    composer.fill("continue inspection")
    composer.press("Enter")
    expect(page.locator("chat-view #chat-messages")).to_contain_text("Inspected: continue inspection")


def test_enter_preserves_draft_until_coding_prerequisites_are_ready(coding_browser):
    from playwright.sync_api import expect

    served, page, project = coding_browser
    page.goto(f"http://127.0.0.1:{served.port}/ui#chat")
    page.get_by_label("Tools", exact=True).check()
    page.get_by_label("Tool runtime", exact=True).select_option("codex")
    page.get_by_label("Tool permissions", exact=True).select_option("read-only")
    composer = page.get_by_role("textbox", name="Message", exact=True)
    composer.fill("inspect project")
    expect(page.locator("chat-view #chat-send")).to_be_disabled()
    composer.press("Enter")
    expect(composer).to_have_value("inspect project")
    assert not served.ui.conversations.all()
    page.get_by_label("Project directory", exact=True).fill(str(project))
    page.get_by_label("Project directory", exact=True).press("Tab")
    expect(page.locator("chat-view #chat-send")).to_be_enabled()
    composer.press("Enter")
    expect(page.locator("chat-view #chat-messages")).to_contain_text("Inspected: inspect project")


def test_coding_uses_its_runtime_when_chat_runtime_is_unavailable(coding_browser):
    from playwright.sync_api import expect

    served, page, project = coding_browser
    page.unroute("**/ui/chat")
    page.route("**/ui/chat", lambda route: route.fulfill(json={"models": [{"model": "test-model", "local": True}], "runtime_ready": False}))
    page.goto(f"http://127.0.0.1:{served.port}/ui#chat")
    page.get_by_label("Tools", exact=True).check()
    page.get_by_label("Tool runtime", exact=True).select_option("codex")
    page.get_by_label("Tool permissions", exact=True).select_option("read-only")
    page.get_by_label("Project directory", exact=True).fill(str(project))
    page.get_by_label("Project directory", exact=True).press("Tab")
    expect(page.locator("chat-view #chat-send")).to_be_enabled()
    composer = page.get_by_role("textbox", name="Message", exact=True)
    composer.fill("inspect without chat runtime")
    composer.press("Enter")
    expect(page.locator("chat-view #chat-messages")).to_contain_text("Inspected: inspect without chat runtime")
