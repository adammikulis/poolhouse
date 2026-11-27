"""The app asks whether to add the macOS signing certificate: an ordinary question in setup and the app that blocks nothing, answered
once, changeable in settings.

The daemon, its routes and the page are real and the browser is Chromium. The keychain is the plumbing-only one of
`test_node_signing` (`security` and `openssl` answered with canned text, nothing read or written), and the page is told it runs on an
installed Mac.
"""

from __future__ import annotations

import time

import pytest
from launch_support import browser, sign_in, ticket
from test_fleet_ui import Serving
from test_node_signing import (  # noqa: F401 - the plumbing-only keychain's fixtures
    SHA,
    installed,
    tools,
)

from poolhouse import caller, node_signing, runtime_identity
from poolhouse.fleet import signature_routes

QUESTION = "Keep macOS permissions after updates?"


@pytest.fixture
def app(tmp_path, installed, monkeypatch):  # noqa: F811
    """A signed-in-able daemon on what counts as the installed software of a Mac, with no certificate and no answer yet."""
    monkeypatch.setattr(signature_routes, "CHANGE", signature_routes.Change())
    server = Serving(tmp_path / "ui", secure=False)
    server.ui.settings.setup_done = True
    server.tools = installed
    try:
        yield server
    finally:
        server.close()


def read(app, cookie: str) -> dict:
    status, body, _ = app.call("/ui/signature", cookie=cookie, headers=browser(app))
    assert status == 200, body
    return body


def say(app, cookie: str, answer: str) -> tuple[int, dict]:
    status, body, _ = app.call("/ui/signature", method="POST", body={"certificate": answer}, cookie=cookie, headers=browser(app))
    return status, body


def settled(app, cookie: str) -> dict:
    deadline = time.monotonic() + 30
    while (state := read(app, cookie))["working"]:
        assert time.monotonic() < deadline, state
        time.sleep(0.05)
    return state


def test_asking_changes_nothing_and_nothing_is_chosen_until_the_person_says(app):
    state = read(app, sign_in(app))
    assert state == {"applies": True, "certificate": False, "answer": "", "working": False, "error": "", "leftovers": False}
    assert app.tools.writes() == [] and node_signing.answer() == ""


def test_no_is_remembered_across_sessions_and_adds_nothing(app):
    status, state = say(app, sign_in(app), "no")
    assert status == 200 and state["answer"] == "no" and not state["working"]
    assert read(app, sign_in(app))["answer"] == "no" and app.tools.writes() == []


def test_yes_adds_the_certificate_and_is_remembered(app):
    cookie = sign_in(app)
    assert say(app, cookie, "yes")[0] == 200
    state = settled(app, cookie)
    assert state["certificate"] and state["answer"] == "yes" and state["error"] == ""
    assert [argv[1] for argv in app.tools.writes()] == ["req", "pkcs12", "import", "add-trusted-cert"]


def test_a_declined_password_leaves_nothing_says_why_and_is_not_asked_again(app):
    app.tools.trust_step = 1
    cookie = sign_in(app)
    say(app, cookie, "yes")
    state = settled(app, cookie)
    assert not state["certificate"] and state["answer"] == "yes" and "add-trusted-cert" in state["error"]
    assert app.tools.certificates == {}


def test_with_a_certificate_there_keep_changes_nothing_and_remove_deletes_it(app):
    app.tools.trusted()
    cookie = sign_in(app)
    assert read(app, cookie)["certificate"]
    assert say(app, cookie, "yes")[1]["answer"] == "yes" and app.tools.writes() == []
    say(app, cookie, "no")
    state = settled(app, cookie)
    assert not state["certificate"] and state["answer"] == "no" and SHA not in app.tools.certificates


def test_a_daemon_an_agent_restarted_takes_the_persons_answer_and_the_audit_says_the_window_asked(app, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    cookie = sign_in(app)
    say(app, cookie, "yes")
    assert settled(app, cookie)["certificate"]
    [row] = [row for row in caller.audit_rows() if row.get("action") == node_signing.ENABLE]
    assert (row["event"], row["via"]) == ("ran", "app window") and row["who"].startswith("agent CLAUDECODE")


def test_the_terminal_command_answers_the_apps_question(app):
    node_signing.remove(lambda _text: None)
    assert node_signing.answer() == "no" and read(app, sign_in(app))["answer"] == "no"
    node_signing.enable(lambda _text: None)
    assert node_signing.answer() == "yes"


def test_where_poolhouse_signs_nothing_there_is_nothing_to_answer(app, monkeypatch):
    monkeypatch.setattr(runtime_identity, "applies", lambda: False)
    cookie = sign_in(app)
    assert read(app, cookie) == {"applies": False}
    assert say(app, cookie, "yes")[0] == 409 and app.tools.writes() == [] and node_signing.answer() == ""


def base(app) -> str:
    return f"http://127.0.0.1:{app.port}/ui/"


def opened(app, chromium, anchor: str = ""):
    page = chromium.new_page(viewport={"width": 1200, "height": 900})
    failed: list[str] = []
    page.on("pageerror", lambda error: failed.append(str(error)))
    page.goto(f"{base(app)}?launch_ticket={ticket(app)[1]['ticket']}{anchor}")
    return page, failed


@pytest.mark.slow
def test_in_a_browser_the_question_blocks_nothing_no_is_kept_and_settings_change_it(app, playwright, tmp_path):
    from playwright.sync_api import expect

    with playwright.chromium.launch(headless=True) as chromium:
        page, failed = opened(app, chromium, "#chat")
        asked = page.locator("#workspace macos-signature[ask]")
        expect(asked.get_by_role("heading", name=QUESTION)).to_be_visible()
        expect(asked).to_contain_text("Poolhouse works whether or not you answer.")
        expect(asked).to_contain_text("Any program running as you could use that key")
        expect(asked).to_contain_text("macOS may ask for the same permissions again")
        page.screenshot(path=str(tmp_path / "1-asked-in-the-app.png"))
        page.evaluate("location.hash = 'settings'")
        expect(page.locator("#settings")).to_be_visible()
        assert node_signing.answer() == "" and app.tools.writes() == [], "using the app answers nothing"
        page.evaluate("location.hash = 'chat'")
        asked.get_by_role("button", name="Decide later").click()
        expect(asked.get_by_role("heading")).to_have_count(0)
        assert node_signing.answer() == "", "putting it off is not an answer"
        page.reload()
        page.goto(f"{base(app)}?launch_ticket={ticket(app)[1]['ticket']}#chat")
        asked.get_by_role("button", name="Do not add it").click()
        expect(asked).to_contain_text("You can change this in Settings.")
        expect(asked.get_by_role("button")).to_have_count(0)
        page.screenshot(path=str(tmp_path / "2-answered-no.png"))
        assert node_signing.answer() == "no"
        again, failed_again = opened(app, chromium, "#chat")
        expect(again.locator("#app")).to_be_visible()
        expect(again.get_by_role("heading", name=QUESTION)).to_have_count(0)
        again.screenshot(path=str(tmp_path / "3-not-asked-again.png"))
        again.evaluate("location.hash = 'settings'")
        again.get_by_role("tab", name="Compute & device").click()
        pane = again.locator("#settings macos-signature")
        pane.scroll_into_view_if_needed()
        expect(pane).to_contain_text("Poolhouse is signed locally.")
        again.screenshot(path=str(tmp_path / "4-settings-local.png"))
        pane.get_by_role("button", name="Add the certificate").click()
        expect(pane).to_contain_text("Poolhouse is signed with the certificate")
        again.screenshot(path=str(tmp_path / "5-settings-certificate.png"))
        pane.get_by_role("button", name="Remove the certificate").click()
        expect(pane).to_contain_text("Poolhouse is signed locally.")
        assert node_signing.answer() == "no" and app.tools.certificates == {}
        assert failed == [] and failed_again == []


@pytest.mark.slow
def test_in_a_browser_setup_asks_beside_the_wizard_which_goes_on_unanswered(app, playwright, tmp_path):
    from playwright.sync_api import expect

    app.ui.settings.setup_done = False
    with playwright.chromium.launch(headless=True) as chromium:
        page, failed = opened(app, chromium)
        asked = page.locator("first-run macos-signature")
        expect(page.get_by_role("heading", name="Welcome to Poolhouse")).to_be_visible()
        expect(asked.get_by_role("heading", name=QUESTION)).to_be_visible()
        page.screenshot(path=str(tmp_path / "1-asked-in-setup.png"), full_page=True)
        expect(page.get_by_role("button", name="Continue")).to_be_enabled()
        asked.get_by_role("button", name="Add the certificate").click()
        expect(asked).to_contain_text("Poolhouse is signed with the certificate")
        page.screenshot(path=str(tmp_path / "2-added-in-setup.png"), full_page=True)
        assert node_signing.answer() == "yes" and SHA in app.tools.certificates
        page.get_by_role("button", name="Continue").click()
        expect(page.get_by_role("heading", name="Make this device ready")).to_be_visible()
        assert failed == []


@pytest.mark.slow
def test_in_a_browser_a_certificate_already_there_is_asked_about_and_kept(app, playwright, tmp_path):
    from playwright.sync_api import expect

    app.tools.trusted()
    with playwright.chromium.launch(headless=True) as chromium:
        page, failed = opened(app, chromium, "#chat")
        asked = page.locator("#workspace macos-signature[ask]")
        expect(asked.get_by_role("heading", name="Keep the Poolhouse signing certificate?")).to_be_visible()
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(tmp_path / "1-keep-or-remove-narrow.png"))
        asked.get_by_role("button", name="Keep the certificate").click()
        expect(asked).to_contain_text("You can change this in Settings.")
        assert node_signing.answer() == "yes" and app.tools.writes() == [] and failed == []
