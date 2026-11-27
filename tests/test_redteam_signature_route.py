"""Red-team: something that is not the person in this computer's signed-in window tries to answer the signing certificate question.

Real requests over loopback to a real daemon; the keychain is the plumbing-only one of `test_node_signing`, and every attempt must
leave it untouched and the question unanswered.
"""

from __future__ import annotations

import pytest
from launch_support import browser, sign_in
from test_node_signing import installed, tools  # noqa: F401 - the plumbing-only keychain's fixtures
from test_signature_app import app  # noqa: F401 - the daemon fixture

from poolhouse import caller, node_signing
from poolhouse.fleet import signature_routes

pytestmark = pytest.mark.redteam


def attempts(server) -> list[tuple[str, dict]]:
    """Each way of asking that is not the person's window, as (name, options of the call)."""
    person = sign_in(server)
    page = browser(server)
    unchecked = server.ui.sessions.cookie_header(server.ui.sessions.open("setup")).split(";", 1)[0]
    token = server.ui.sessions.cookie_header(server.ui.sessions.open("token", "token")).split(";", 1)[0]
    elsewhere = f"192.0.2.7:{server.port}"
    return [
        ("no session", {"cookie": "", "headers": page}),
        ("a session no credential opened", {"cookie": unchecked, "headers": page}),
        ("a token session", {"cookie": token, "headers": page}),
        ("an agent's header", {"cookie": person, "headers": {**page, "X-Poolhouse-Agent": "claude-000000"}}),
        ("a token header", {"cookie": person, "headers": {**page, "X-Poolhouse-Token": "guess"}}),
        ("an authorization header", {"cookie": person, "headers": {**page, "Authorization": "Bearer guess"}}),
        ("another page's origin", {"cookie": person, "headers": {**page, "Origin": "http://evil.example"}}),
        ("a cross-site fetch", {"cookie": person, "headers": {**page, "Sec-Fetch-Site": "cross-site"}}),
        ("a program that sends no browser header", {"cookie": person, "headers": {}}),
        ("a network address", {"cookie": person, "headers": {**page, "Host": elsewhere, "Origin": f"http://{elsewhere}"}}),
    ]


def test_only_the_persons_signed_in_window_reads_or_answers_the_question(app):  # noqa: F811
    for name, options in attempts(app):
        for call in ({"method": "POST", "body": {"certificate": "yes"}}, {"method": "POST", "body": {"certificate": "no"}}, {}):
            status, body, _ = app.call("/ui/signature", **call, **options)
            assert status in (401, 403), (name, call, status, body)
            assert "certificate" not in body and "answer" not in body, (name, body)
    assert app.tools.calls == [] and node_signing.answer() == ""


@pytest.mark.parametrize("body", [{"certificate": "maybe"}, {"certificate": "yes", "keychain": "/tmp/other"}, {}, ["yes"],
                                  {"certificate": "y" * 4000}], ids=["unknown", "extra-field", "empty", "not-an-object", "oversized"])
def test_an_answer_that_is_not_exactly_yes_or_no_changes_nothing(app, body):  # noqa: F811
    status, said, _ = app.call("/ui/signature", method="POST", body=body, cookie=sign_in(app), headers=browser(app))
    assert status == 400, said
    assert app.tools.calls == [] and node_signing.answer() == ""


def test_a_confined_daemon_writes_no_keychain_whoever_answers(app, monkeypatch):  # noqa: F811
    monkeypatch.setattr(caller, "confined", lambda: True)
    status, _, _ = app.call("/ui/signature", method="POST", body={"certificate": "yes"}, cookie=sign_in(app), headers=browser(app))
    assert status == 200
    signature_routes.CHANGE._thread.join(30)
    assert app.tools.writes() == [] and "confinement" in signature_routes.CHANGE.error
