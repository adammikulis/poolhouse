"""Coding controls preserve conversations when native isolation is unavailable."""
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
        served.ui.root = served.ui.conversations.root.parent  # creating a conversation binds it to the daemon's root
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



def refused(page, composer, prompt):
    from playwright.sync_api import expect

    composer.fill(prompt)
    composer.press("Enter")
    expect(page.locator("#chat-note")).to_contain_text("This coding runtime cannot enforce isolated session permissions")
    expect(composer).to_have_value(prompt)
    expect(page.get_by_role("button", name="Stop", exact=True)).to_be_hidden()


def test_coding_settings_persist_without_recording_a_refused_turn(coding_browser):
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
    output_tokens = page.get_by_role("slider", name="Maximum output tokens", exact=True)
    expect(output_tokens).to_have_attribute("aria-valuetext", "Custom · 13,579 tokens")
    output_tokens.press("Home")
    output_tokens.press("ArrowRight")
    output_tokens.press("Tab")
    expect(output_tokens).to_have_attribute("aria-valuetext", "512 tokens")
    page.get_by_role("button", name="Options", exact=True).click()
    composer = page.get_by_role("textbox", name="Message", exact=True)
    refused(page, composer, "inspect the brakes")
    cid = page.locator("chat-view").evaluate("chat => chat.open")
    saved = served.ui.conversations.get(cid)
    assert saved.settings["project"] == str(project)
    assert saved.settings["harness"] == "codex"
    assert saved.settings["role"] == "read-only"
    assert saved.settings["max_output_tokens"] == 512
    assert "mode" not in saved.settings
    assert saved.messages == []
    assert served.ui.coding_turns.turns == {}
    page.reload()
    expect(page.get_by_label("Tools", exact=True)).to_be_checked()
    expect(page.get_by_label("Project directory", exact=True)).to_have_value(str(project))
    expect(page.get_by_label("Tool runtime", exact=True)).to_have_value("codex")
    expect(page.get_by_label("Tool permissions", exact=True)).to_have_value("read-only")
    page.get_by_role("button", name="Options", exact=True).click()
    expect(page.get_by_role("slider", name="Maximum output tokens", exact=True)).to_have_attribute("aria-valuetext", "512 tokens")
    page.get_by_role("button", name="Options", exact=True).click()
    assert served.ui.conversations.get(cid).settings == saved.settings
    refused(page, page.get_by_role("textbox", name="Message", exact=True), "inspect the sensors")
    assert served.ui.conversations.get(cid).messages == []


def test_reload_of_refused_turn_preserves_existing_conversation(coding_browser):
    from playwright.sync_api import expect

    served, page, project = coding_browser
    saved = served.ui.conversations.start(model="test-model", title="Coding history", settings={
        "project": str(project), "role": "read-only", "harness": "codex"})
    served.ui.conversations.append(saved.id, "user", "Existing request")
    served.ui.conversations.append(saved.id, "assistant", "Existing response")
    before = served.ui.conversations.get(saved.id).messages
    page.goto(f"http://127.0.0.1:{served.port}/ui#chat")
    page.get_by_role("link", name=saved.title, exact=True).click()
    expect(page.get_by_label("Tools", exact=True)).to_be_checked()
    refused(page, page.get_by_role("textbox", name="Message", exact=True), "wait until cancelled")
    assert served.ui.conversations.get(saved.id).messages == before
    assert served.ui.coding_turns.turns == {}
    page.reload()
    expect(page.get_by_role("button", name="Stop", exact=True)).to_be_hidden()
    expect(page.locator("#chat-messages")).to_contain_text("Existing response")
    refused(page, page.get_by_role("textbox", name="Message", exact=True), "continue inspection")
    assert served.ui.conversations.get(saved.id).messages == before


def test_enter_preserves_draft_until_project_and_isolated_runtime_are_ready(coding_browser):
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
    refused(page, composer, "inspect project")
    assert all(conversation.messages == [] for conversation in served.ui.conversations.all())
    assert served.ui.coding_turns.turns == {}


def test_missing_chat_runtime_does_not_bypass_coding_isolation(coding_browser):
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
    refused(page, page.get_by_role("textbox", name="Message", exact=True), "inspect without chat runtime")
    assert all(conversation.messages == [] for conversation in served.ui.conversations.all())
    assert served.ui.coding_turns.turns == {}
