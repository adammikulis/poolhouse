"""The Delegates view: the routes it reads, behind the page's own sign-in and for this computer alone, and the page in a real browser engine.

PLUMBING ONLY: the daemon, its sign-in, the page and the record on disk (the test's own state root) are real; the run in it is written by the test,
no model and no harness ran, and every word standing in for a model's says it is `[CANNED RESPONSE, NOT A MODEL]`.
"""

from __future__ import annotations

import os
import time

import pytest
from browser_expect import expect
from test_fleet_ui import Serving

from poolhouse import delegate_record, delegate_store as store, jobs

CANNED = "[CANNED RESPONSE, NOT A MODEL]"
HOSTILE = "<img src=x onerror=\"document.title='owned'\">"
NAME = "claude-5e11ed"


def a_live_run() -> str:
    """A run as its runner leaves it while a command runs: its record, a status, a live process record and the events so far."""
    store.save(store.Delegate(NAME, "lead-1", "board", "make the PLUMBING tests pass", "/tmp/work", "Plumbing-Q4.gguf", edit=True,
                              permissions="guarded", branch=f"delegate/{NAME}"))
    jobs.record(delegate_record.JOB, pid=os.getpid(), home=store.folder(NAME))
    store.set_status(NAME, "working", "alias")
    tests = {"passed": 12, "failed": 1, "errors": 0, "skipped": 0, "exit": 1, "ok": False, "setup": "", "at": time.time() - 30}
    store.note(NAME, turns=27, tests=tests, heard=time.time(), spent={"input_tokens": 100, "cache_read_tokens": 300, "output_tokens": 42},
               now={"doing": "tool", "what": "shell `scripts/test all tests/test_x.py`", "since": time.time() - 245})
    add = lambda kind, **fields: delegate_record.append(NAME, kind, **fields)   # noqa: E731 - one short writer
    add("run", lead="lead-1", model="Plumbing-Q4.gguf", tier="guarded", task="make the PLUMBING tests pass")
    add("attempt", phase="work", round=0, attempt=0)
    add("text", text=f"{CANNED} reading {HOSTILE}")
    add("call", tool="shell", command="scripts/test all tests/test_x.py", denied=False)
    add("result", tool="shell", command="scripts/test all tests/test_x.py", status="completed", exit=1, seconds=61.0,
        output=f"PLUMBING OUTPUT {HOSTILE}\n1 failed, 12 passed in 60.0s")
    add("test", command="scripts/test all tests/test_x.py", **{key: value for key, value in tests.items() if key != "at"})
    add("attempt_end", phase="work", round=0, attempt=0, seconds=3180, turns=27, why="provider: 429", interrupted="provider: 429")
    add("attempt", phase="work", round=0, attempt=1)
    add("call", tool="shell", command="scripts/test all tests/test_x.py", denied=False)
    return NAME


@pytest.fixture
def serving(tmp_path):
    server = Serving(tmp_path)
    server.ui.settings.setup_done = True
    yield server
    server.close()


def test_the_delegate_routes_answer_this_computer_with_the_runs_and_one_runs_events(serving):
    name = a_live_run()
    status, body, _headers = serving.call("/ui/delegates")
    assert status == 200, body
    [run] = body["runs"]
    assert (run["name"], run["state"], run["turns"], run["live"]) == (name, "working", 27, True), run
    assert "running shell `scripts/test all tests/test_x.py` for 4m" in run["doing"] and "12 passed, 1 failed" in run["tests_said"], run
    status, body, _headers = serving.call(f"/ui/delegates/{name}")
    assert status == 200 and [each["kind"] for each in body["events"]][-1] == "call" and body["more"] is False, body
    following = body["next"]
    delegate_record.append(name, "step", turn=28)
    status, body, _headers = serving.call(f"/ui/delegates/{name}?after={following}")
    assert status == 200 and [each["kind"] for each in body["events"]] == ["step"], body
    for path, expected in (("/ui/delegates/nobody", 404), ("/ui/delegates/..", 404)):
        assert serving.call(path)[0] == expected, path
    assert serving.call(f"/ui/delegates/{name}", method="POST", body={})[0] == 405


def test_the_delegate_routes_keep_the_pages_own_checks_and_show_nothing_to_another_computer(serving, monkeypatch):
    name = a_live_run()
    assert serving.call("/ui/delegates", ui_header=False)[0] == 403
    for path in ("/ui/delegates", f"/ui/delegates/{name}"):
        status, body, _headers = serving.call(path, host="192.0.2.9")
        assert status in (401, 403) and "runs" not in body and "events" not in body, (status, body)
        status, body, _headers = serving.call(path, headers={"Host": "attacker.example:80"})
        assert status == 403 and "runs" not in body, (status, body)
    monkeypatch.setattr(serving.ui, "founded", lambda: True)
    status, body, _headers = serving.call("/ui/delegates")
    assert status == 401 and "runs" not in body, (status, body)
    signed = serving.ticket_session()
    cookie = signed[2]["Set-Cookie"].split(";")[0]
    assert serving.call("/ui/delegates", cookie=cookie)[0] == 200
    status, body, _headers = serving.call("/ui/delegates", cookie=cookie, host="192.0.2.9")
    assert status in (401, 403) and "runs" not in body, "a signed-in session of another computer is shown nothing"


@pytest.mark.slow
def test_a_run_with_no_step_record_says_so_and_shows_why_it_failed_and_what_was_asked(serving, playwright):
    """A run from before records were kept has its task, state and answer and no ``events.jsonl``."""
    store.save(store.Delegate(NAME, "lead-1", "board", "make the PLUMBING tests pass", "/tmp/work", "Plumbing-Q4.gguf", edit=True))
    why = "interrupted: PLUMBING the harness reported an error; all 3 attempt(s) are used"
    store.set_status(NAME, "failed", why)
    (delegate_record.folder(NAME) / delegate_record.FILE).unlink()
    store.save_result(NAME, {"state": "failed", "text": f"{CANNED} half way", "error": why})
    with playwright.chromium.launch(headless=True) as browser:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(f"http://127.0.0.1:{serving.port}/ui/#delegates")
        view = page.locator("delegates-view")
        expect(view.locator(".delegate-run")).to_contain_text("PLUMBING the harness reported an error")
        expect(view.locator("#delegate-why")).to_have_text(why)
        expect(view.locator("#delegate-empty")).to_contain_text("No steps are recorded for this run")
        expect(view.locator(".delegate-tests")).to_have_text("No test run recorded.")
        expect(view.locator("#delegate-facts")).to_be_visible()
        expect(view.locator("#delegate-facts")).to_contain_text(f"{CANNED} half way")
        view.locator("#delegate-about summary").click()
        page.wait_for_timeout(2500)
        expect(view.locator("#delegate-facts")).to_be_hidden()
        delegate_record.append(NAME, "step", turn=1)
        expect(view.locator(".delegate-event")).to_have_count(1, timeout=10000)
        expect(view.locator("#delegate-empty")).to_be_hidden()


@pytest.mark.slow
@pytest.mark.parametrize("width", [1280, 390])
def test_the_delegates_view_lists_the_runs_follows_one_live_and_shows_box_text_as_text(serving, playwright, tmp_path, width):
    name = a_live_run()
    with playwright.chromium.launch(headless=True) as browser:
        page = browser.new_page(viewport={"width": width, "height": 900})
        problems = []
        page.on("pageerror", lambda error: problems.append(str(error)))
        page.goto(f"http://127.0.0.1:{serving.port}/ui/#delegates")
        view = page.locator("delegates-view")
        row = view.locator(".delegate-run").filter(has_text=name)
        expect(row).to_contain_text("running shell `scripts/test all tests/test_x.py` for 4m")
        expect(row).to_contain_text("12 passed, 1 failed")
        expect(view.locator(".delegate-now")).to_contain_text("Now: running shell")
        timeline = view.locator(".delegate-timeline")
        expect(timeline).to_contain_text("shell exit 1 after 1m 1s")
        expect(timeline).to_contain_text("PLUMBING OUTPUT <img src=x")
        expect(timeline).to_contain_text("Tests failed: 12 passed, 1 failed")
        expect(timeline).to_contain_text("Ended: work pass, attempt 0 after 53m 0s, 27 turn(s) · interrupted")
        expect(timeline).to_contain_text("Restart: work pass, attempt 1")
        assert page.locator("delegates-view img").count() == 0 and page.title() != "owned"
        view.locator("#delegate-about summary").click()
        expect(view.locator("#delegate-facts")).to_contain_text("75%")
        shown = view.locator(".delegate-event").count()
        view.locator(".delegate-event[data-kind=result] summary").first.click()
        delegate_record.append(name, "text", text=f"{CANNED} added while the page is open")
        expect(timeline).to_contain_text("added while the page is open", timeout=10000)
        assert view.locator(".delegate-event").count() == shown + 1, "only what is new is added"
        assert view.locator(".delegate-event[data-kind=result] details").first.get_attribute("open") is None, "what was closed stays closed"
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "no sideways scroll"
        expect(page.get_by_role("link", name="Delegates")).to_be_visible()
        if kept := os.environ.get("PLUMBING_SCREENSHOTS"):
            page.screenshot(path=f"{kept}/delegates-{width}.png", full_page=True)
        store.set_status(name, "failed", "the harness reported an error: provider: 429")
        delegate_record.append(name, "end", state="failed", seconds=3300, detail="provider: 429")
        expect(view.locator(".delegate-now")).to_contain_text("Ended", timeout=10000)
        expect(timeline).to_contain_text("Run ended failed after 55m 0s")
        assert problems == []
