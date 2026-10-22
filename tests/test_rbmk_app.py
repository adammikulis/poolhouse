"""The RBMK control room app in a real headless Chromium: one page, one run state shared by the room and its tools.

Runs of the plain-code naive controller (declared as not a model) drive the checks of the plumbing. The tests marked
real_model start the real decision model through the app's Run console and open its stored answers as a recording; a missing
model fails them naming the cause.
"""

from __future__ import annotations

import http.client
import re
import threading
from pathlib import Path

import pytest
from rbmk_run import HOLD_WAIT_S, machine_models, save_recording

from examples.rbmk.room import shoot
from examples.rbmk.web import server
from examples.rbmk.web.hub import Hub

pytest_plugins = ["real_model"]

SERVED = ["/", "/js/app.js", "/js/store.js", "/js/log.js", "/js/panel.js", "/js/whatif.js", "/js/history.js", "/js/dist.js",
          "/js/compare.js", "/js/core.js", "/js/charts.js", "/css/app.css", "/room/main.js", "/room/feed.js", "/room/room3d.js",
          "/room/room2d.js", "/room/screens.js", "/room/cards.js", "/room/drive.js", "/room/fly.js", "/room/instruments.json"]
BANNED = re.compile(r"recovered|failed|better|worse|score|confidence|SAFETY SYSTEM DISABLED|DISABLED BY THIS CHOICE", re.IGNORECASE)
SETTLED = "!window.RB.state.dirty"


def start_run(page, scenario: str = "pump_trip", controller: str = "naive", timeout: int = 60000) -> None:
    """Choose a scenario and a controller in the Run console, start the run and wait until it is finished with its document."""
    page.select_option("#scenario", scenario)
    page.select_option("#controller", controller)
    page.click("#start")
    page.wait_for_function("window.RB.state.main && window.RB.state.main.status === 'done' && window.RB.state.main.doc", timeout=timeout)
    page.wait_for_function("document.getElementById('modestate').textContent === 'LIVE run finished'", timeout=10000)


def room_dial(page, ident: str):
    return page.evaluate("(i) => window.__room.state.driver.nodes.get(i).value", ident)


def test_the_page_has_one_room_and_no_second_view_by_another_name(tmp_path: Path):
    srv = server.serve(Hub(tmp_path), 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        texts = {}
        for path in SERVED:
            conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=30)
            conn.request("GET", path)
            res = conn.getresponse()
            assert res.status == 200, path
            texts[path] = res.read().decode()
            conn.close()
    finally:
        srv.shutdown()
        srv.server_close()
    page = texts["/"]
    assert page.count('<canvas id="view"') == 1 and 'id="drawer"' in page and 'id="dock"' in page
    assert not re.search(r"operator", "".join(texts.values()), re.IGNORECASE), "no screen, label or script is called operator"


@pytest.mark.slow
def test_without_a_run_the_room_is_dark_and_says_so(playwright, tmp_path):
    with shoot.serving(tmp_path) as origin:
        browser, page, errors = shoot.open_page(playwright, origin)
        try:
            assert page.inner_text("#modestate") == "LIVE: no run"
            assert "NO RUN" in page.inner_text("#banner")
            assert page.evaluate("window.__room.feed.status") == "error"
            assert room_dial(page, "D_FUELT") is None
            assert page.is_visible("#drawer") and page.inner_text("#drawer-title") == "Run console"
            assert page.eval_on_selector_all("#decider option", "(os) => os.map((o) => o.textContent)") == [
                "Strands 2B (strands-decider-2B-hobson-v19)"]
            assert page.eval_on_selector_all("#memory option", "(os) => os.map((o) => o.value)") == ["none"]
        finally:
            browser.close()
        assert not errors


@pytest.mark.slow
def test_a_run_started_in_the_console_drives_the_room_and_a_picked_decision_moves_it(playwright, tmp_path):
    with shoot.serving(tmp_path) as origin:
        browser, page, errors = shoot.open_page(playwright, origin)
        try:
            start_run(page)
            assert page.evaluate("window.__room.feed.status") == "ok" and page.evaluate("window.__room.feed.modelId") == ""
            page.click("[data-drawer=log]")
            rows = page.query_selector_all("#log .drow.decision")
            assert len(rows) >= 4
            for pick in (3, len(rows) - 1, 1):
                rows[pick].click()
                page.wait_for_function(
                    """() => { const d = window.__room.state.driver, f = window.__room.feed;
                        return Math.abs(d.nodes.get('D_FUELT').value - window.RB.state.main.trace[f.index].Tf) < 1e-9; }""")
                got = page.evaluate("""() => {
                    const S = window.RB.state, f = window.__room.feed, row = S.main.trace[f.index], d = window.__room.state.driver;
                    return { selected: S.selected, cursor: S.cursor, index: f.index, tick: row.tick, snap: S.main.decisions[S.selected].snapshot_tick,
                             shown: f.decision && f.decision.i, rods: d.rods.map((r) => r.value).filter((v) => v !== undefined).length,
                             scram: d.nodes.get('L_SCRAM').lit, wantScram: !!row.scram };
                }""")
                assert got["cursor"] == got["index"] and got["tick"] == got["snap"] and got["shown"] == got["selected"]
                assert got["rods"] == 211 and got["scram"] == got["wantScram"]
            page.evaluate("window.__room.feed.setIndex(5)")
            page.wait_for_function("document.querySelector('#log .drow.active') !== null")
            assert page.evaluate("window.RB.state.cursor") == 5
            assert int(page.get_attribute("#log .drow.active", "data-tick")) <= page.evaluate("window.RB.state.main.trace[5].tick")
            page.click("[data-drawer=core]")
            assert page.is_visible("#core")
        finally:
            browser.close()
        assert not errors


@pytest.mark.slow
def test_nothing_is_graded_or_given_away_before_it_happens(playwright, tmp_path):
    with shoot.serving(tmp_path) as origin:
        browser, page, errors = shoot.open_page(playwright, origin)
        try:
            start_run(page)
            page.evaluate("window.__room.feed.setIndex(0)")
            page.click("[data-drawer=core]")
            page.wait_for_function(SETTLED)
            early = page.inner_text("#captions")
            page.evaluate("window.__room.feed.setIndex(window.RB.state.main.last)")
            page.wait_for_function(SETTLED)
            late = page.inner_text("#captions")
            captions = page.evaluate("window.RB.state.main.captions")
            assert early.count("tick ") == len([c for c in captions if c["tick"] <= 0]), "only captions of moments already reached are listed"
            assert late.count("tick ") == len(captions) and late.count("tick ") > early.count("tick ")
            for name in ("run", "log", "charts", "core", "readouts", "whatif", "history", "dist", "compare"):
                page.evaluate("(n) => window.RB.openDrawer(n)", name)
                page.wait_for_function(SETTLED)
                assert not BANNED.search(page.inner_text("#drawer")), name
            page.evaluate("window.RB.openDrawer('log')")
            page.evaluate("window.__room.feed.setIndex(0)")
            page.wait_for_function("[...document.querySelectorAll('#log .outcome')].some((o) => o.style.display === 'none')", timeout=15000)
        finally:
            browser.close()
        assert not errors


@pytest.mark.slow
def test_a_phone_sees_the_whole_app_without_sideways_scrolling(playwright, tmp_path):
    with shoot.serving(tmp_path) as origin:
        browser, page, errors = shoot.open_page(playwright, origin, None, 390, 800, touch=True)
        try:
            start_run(page)
            for name in ("run", "log", "charts", "core", "readouts", "whatif", "history", "dist", "compare"):
                page.evaluate("(n) => window.RB.openDrawer(n)", name)
                page.wait_for_timeout(200)
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), name
                assert page.evaluate("document.getElementById('drawer').getBoundingClientRect().right <= window.innerWidth + 1"), name
            assert page.is_visible("#dock") and page.is_visible("#top")
        finally:
            browser.close()
        assert not errors


@pytest.mark.slow
@pytest.mark.real_model
def test_a_real_model_run_shows_its_answers_in_the_log_and_on_the_wall_and_opens_again_as_a_recording(playwright, tmp_path, serve_leases, real, monkeypatch):
    machine_models(real, monkeypatch)
    with shoot.serving(tmp_path, wait_s=HOLD_WAIT_S) as origin:
        browser, page, errors = shoot.open_page(playwright, origin)
        try:
            page.select_option("#scenario", "fixture_short")
            page.select_option("#controller", "model")
            page.click("#start")
            page.wait_for_function("window.RB.state.main && ['done', 'error', 'stopped'].includes(window.RB.state.main.status)", timeout=1_200_000)
            assert page.evaluate("window.RB.state.main.status") == "done", page.inner_text("#banner")
            model = page.evaluate("window.RB.state.main.model")
            assert model and page.inner_text("#modelid") == model
            page.click("[data-drawer=log]")
            assert "probability of the top option" in page.inner_text("#log")
            rows = page.query_selector_all("#log .drow.decision")
            rows[0].click()
            page.wait_for_function("window.__room.feed.modelDecision !== null")
            assert page.evaluate("window.__room.feed.modelDecision.i") == page.evaluate("window.RB.state.selected")
            assert page.evaluate("window.__room.feed.modelId") == model
            doc = page.evaluate("window.RB.state.main.doc")
            name = save_recording(doc, tmp_path)
            page.click("#modeRec")
            page.wait_for_function(f"[...document.querySelectorAll('#recording option')].some((o) => o.value === {name!r})")
            page.select_option("#recording", name)
            page.click("#openrec")
            page.wait_for_function("window.RB.state.main && window.RB.state.main.origin === 'recorded'", timeout=60000)
            page.wait_for_function("document.getElementById('modestate').textContent === 'RECORDED'")
            assert page.inner_text("#modelid") == model and page.evaluate("window.__room.feed.modelId") == model
        finally:
            browser.close()
        assert not errors
