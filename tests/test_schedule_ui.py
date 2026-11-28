"""The app's Scheduled page: the routes that list, show and cancel jobs behind the page's own sign-in and for this computer alone,
no route that makes or changes one, and the page in a real browser engine. The daemon, its page and the records are real; no
job in them runs.
"""

from __future__ import annotations

import os
import time

import pytest
from browser_expect import expect
from test_fleet_ui import Serving

from poolhouse import schedule_record as record

HOSTILE = "<img src=x onerror=\"document.title='owned'\">"


def signed_in(serving) -> str:
    """The cookie of a window the person opened: it spent a launch ticket this daemon minted."""
    return serving.ticket_session()[2]["Set-Cookie"].split(";")[0]


def waiting() -> str:
    now = time.time()
    steps = [{"n": 1, "line": 1, "kind": "bench", "role": "step", "words": f"quants speed {HOSTILE}", "argv": ["quants"]}]
    job = record.create({"name": "night", "at": now + 3600, "latest": now + 7200, "max_hours": 1.0, "created": now,
                         "steps": steps, "by": {"who": "user tester", "board": "", "agent": "claude-lead", "cwd": "/"},
                         "code": {}, "pins": {}, "child": None})
    record.update(job["id"], lambda kept: kept.update(why=f"port 8080 is in use {HOSTILE}"))
    (record.folder(job["id"]) / "log").write_text(f"waiting {HOSTILE}\n", encoding="utf-8")
    return job["id"]


@pytest.fixture
def serving(tmp_path):
    server = Serving(tmp_path)
    server.ui.settings.setup_done = True
    yield server
    server.close()


def test_the_routes_list_show_and_cancel_and_nothing_makes_or_changes_a_job(serving):
    job = waiting()
    status, body, _headers = serving.call("/ui/schedule")
    assert status == 200, body
    [row] = body["jobs"]
    assert (row["id"], row["state"], row["by"], row["steps"][0]["state"]) == (job, "waiting", "claude-lead", "queued"), row
    assert "port 8080 is in use" in row["why"] and row["starts"] and row["latest_start"]
    status, body, _headers = serving.call(f"/ui/schedule/{job}")
    assert status == 200 and "waiting" in body["output"], body
    for method, path in (("POST", "/ui/schedule"), ("POST", f"/ui/schedule/{job}"), ("GET", f"/ui/schedule/{job}/cancel"),
                         ("DELETE", f"/ui/schedule/{job}")):
        status, body, _headers = serving.call(path, method=method, body={"steps": []})
        assert status in (404, 405), (method, path, status, body)
    assert [one["id"] for one in record.jobs()] == [job] and record.load(job)["state"] == "waiting"
    assert serving.call("/ui/schedule/s-20260101-00000000")[0] == 404
    status, body, _headers = serving.call(f"/ui/schedule/{job}/cancel", method="POST", body={}, cookie=signed_in(serving))
    assert status == 200 and body["job"]["state"] == "cancelled", body
    assert body["job"]["steps"][0]["state"] == "not run", "an ended job shows no step as still queued"
    assert "the person in the app" in record.load(job)["why"]


def test_a_local_program_with_no_signed_in_window_cancels_nothing_in_a_pool_nobody_founded(serving):
    """A sandboxed process that may reach loopback sends what the page sends, and holds no credential."""
    job = waiting()
    cancel = f"/ui/schedule/{job}/cancel"
    for cookie in ("", "poolhouse_ui=made-up"):
        status, body, _headers = serving.call(cancel, method="POST", body={}, cookie=cookie)
        assert status == 403 and "poolhouse open" in body["error"] and "poolhouse schedule cancel" in body["error"], (status, body)
    assert record.load(job)["state"] == "waiting" and record.load(job)["cancel"] is None
    assert serving.call("/ui/schedule")[0] == 200


def test_the_routes_keep_the_pages_own_checks_and_answer_no_other_computer_or_page(serving, monkeypatch):
    job = waiting()
    cancel = f"/ui/schedule/{job}/cancel"
    assert serving.call("/ui/schedule", ui_header=False)[0] == 403
    assert serving.call(cancel, method="POST", body={}, ui_header=False)[0] == 403
    for path, method in (("/ui/schedule", "GET"), (cancel, "POST")):
        status, body, _headers = serving.call(path, method=method, body={} if method == "POST" else None, host="192.0.2.9")
        assert status in (401, 403) and "jobs" not in body, (status, body)
        status, body, _headers = serving.call(path, method=method, body={} if method == "POST" else None,
                                              headers={"Host": "attacker.example:80"})
        assert status == 403, (status, body)
    monkeypatch.setattr(serving.ui, "founded", lambda: True)
    assert serving.call("/ui/schedule")[0] == 401 and serving.call(cancel, method="POST", body={})[0] == 401
    assert record.load(job)["state"] == "waiting"
    cookie = serving.ticket_session()[2]["Set-Cookie"].split(";")[0]
    assert serving.call("/ui/schedule", cookie=cookie)[0] == 200


@pytest.mark.slow
@pytest.mark.parametrize("width", [1280, 390])
def test_the_page_shows_when_a_job_starts_why_it_waits_and_cancels_it(serving, playwright, width):
    job = waiting()
    with playwright.chromium.launch(headless=True) as browser:
        page = browser.new_page(viewport={"width": width, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        ticket, _ = serving.ui.sessions.mint_ticket("launch-secret")
        page.goto(f"http://127.0.0.1:{serving.port}/ui/?launch_ticket={ticket}#schedule")
        card = page.locator(f"schedule-view [data-job='{job}']")
        expect(page.locator("schedule-view").get_by_role("heading", level=1)).to_have_text("Scheduled jobs")
        expect(card).to_contain_text("Waiting: port 8080 is in use")
        expect(card).to_contain_text("latest start")
        card.get_by_text("End of the output").click()
        expect(card.locator("pre")).to_contain_text("waiting")
        assert page.locator("schedule-view img").count() == 0 and page.title() != "owned"
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        card.get_by_role("button", name="Cancel").click()
        expect(card).to_contain_text("cancelled by the person in the app")
        expect(card.get_by_role("button", name="Cancel")).to_be_hidden()
        if kept := os.environ.get("POOLHOUSE_SCREENSHOTS"):
            page.screenshot(path=f"{kept}/scheduled-{width}.png", full_page=True)
        assert not errors, errors
    assert record.load(job)["state"] == "cancelled"
