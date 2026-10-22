"""The RBMK control room app in a real headless Chromium: one page, one run state shared by the room and its tools.

The recording opened here is a real run of the released decision model (`examples/rbmk/recordings/`); the live run uses the
plain-code naive controller, which is declared as not a model. No model runs and none is imitated.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from examples.rbmk.room import shoot
from examples.rbmk.web import server

APP = Path(__file__).resolve().parents[1] / "examples" / "rbmk"
RECORDING = "pump_trip-strands-decider-2B-hobson-v19-2026-10-22.json"
SERVED = ["/", "/js/app.js", "/js/store.js", "/js/log.js", "/js/panel.js", "/js/whatif.js", "/js/history.js", "/js/dist.js",
          "/js/compare.js", "/js/core.js", "/js/charts.js", "/css/app.css", "/room/main.js", "/room/feed.js", "/room/screens.js",
          "/room/cards.js", "/room/drive.js", "/room/fly.js", "/room/instruments.json"]


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    shutil.copy(APP / "recordings" / RECORDING, tmp_path / RECORDING)
    return tmp_path


def test_the_page_has_one_room_and_names_nothing_an_operator_view(tmp_path: Path):
    import http.client
    import threading

    from examples.rbmk.web.hub import Hub

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


def wait_room(page) -> None:
    page.wait_for_function("window.__room && window.__room.state && window.__room.state.ready && window.RB.state.info", timeout=60000)


def room_dial(page, ident: str):
    return page.evaluate("(i) => window.__room.state.driver.nodes.get(i).value", ident)


@pytest.mark.slow
def test_without_a_run_the_room_is_dark_and_says_so(playwright, folder):
    with shoot.serving(folder) as origin:
        browser, page, errors = shoot.open_page(playwright, origin)
        try:
            assert page.inner_text("#modestate") == "LIVE: no run"
            assert "NO RUN" in page.inner_text("#banner")
            assert page.evaluate("window.__room.feed.status") == "error"
            assert room_dial(page, "D_FUELT") is None
            assert page.is_visible("#drawer") and page.inner_text("#drawer-title") == "Run console"
        finally:
            browser.close()
        assert not errors


@pytest.mark.slow
def test_a_decision_picked_in_the_log_is_the_state_the_room_shows(playwright, folder):
    with shoot.serving(folder) as origin:
        browser, page, errors = shoot.open_page(playwright, origin)
        try:
            page.click("#modeRec")
            page.select_option("#recording", RECORDING)
            page.click("#openrec")
            page.wait_for_function("window.__room.feed.status === 'ok'", timeout=60000)
            assert page.inner_text("#modestate") == "RECORDED"
            model = page.inner_text("#modelid")
            assert model.startswith("strands-decider") and page.evaluate("window.__room.feed.modelId") == model
            assert all(page.inner_text(f"#{c}").strip() not in ("", "-") for c in ("clkSim", "clkWall", "clkDec"))
            page.click("[data-drawer=log]")
            rows = page.query_selector_all("#log .drow.decision")
            assert len(rows) >= 4
            for pick in (3, len(rows) - 1, 1):
                rows[pick].click()
                page.wait_for_function("""() => { const d = window.__room.state.driver, f = window.__room.feed;
                    return Math.abs(d.nodes.get('D_FUELT').value - window.RB.state.main.trace[f.index].Tf) < 1e-9; }""")
                got = page.evaluate("""() => {
                    const S = window.RB.state, f = window.__room.feed, row = S.main.trace[f.index];
                    return { selected: S.selected, cursor: S.cursor, index: f.index, tick: row.tick, snap: S.main.decisions[S.selected].snapshot_tick,
                             shown: f.modelDecision && f.modelDecision.i, fuel: window.__room.state.driver.nodes.get('D_FUELT').value, truth: row.Tf,
                             rods: window.__room.state.driver.rods.map((r) => r.value).filter((v) => v !== undefined).length,
                             scram: window.__room.state.driver.nodes.get('L_SCRAM').lit, wantScram: !!row.scram };
                }""")
                assert got["cursor"] == got["index"] and got["tick"] == got["snap"]
                assert got["shown"] == got["selected"], "the wall display shows the picked decision"
                assert got["fuel"] == pytest.approx(got["truth"]) and got["rods"] == 211 and got["scram"] == got["wantScram"]
            page.evaluate("window.__room.feed.setIndex(5)")
            page.wait_for_function("document.querySelector('#log .drow.active') !== null")
            assert page.evaluate("window.RB.state.cursor") == 5
            tick = page.evaluate("window.RB.state.main.trace[5].tick")
            active = int(page.get_attribute("#log .drow.active", "data-tick"))
            assert active <= tick, "the log marks the decision in force at the tick the room shows"
        finally:
            browser.close()
        assert not errors


@pytest.mark.slow
def test_a_live_run_started_in_the_console_drives_the_room(playwright, folder):
    with shoot.serving(folder) as origin:
        browser, page, errors = shoot.open_page(playwright, origin)
        try:
            page.select_option("#scenario", "fixture_short")
            page.select_option("#controller", "naive")
            page.click("#start")
            page.wait_for_function("window.RB.state.main && window.RB.state.main.status === 'done' && window.RB.state.main.doc", timeout=60000)
            page.wait_for_function("document.getElementById('modestate').textContent === 'LIVE run finished'", timeout=10000)
            assert page.evaluate("window.__room.feed.status") == "ok" and page.evaluate("window.__room.feed.modelId") == ""
            page.evaluate("window.__room.feed.setIndex(3)")
            assert room_dial(page, "D_FUELT") == pytest.approx(page.evaluate("window.RB.state.main.trace[3].Tf"))
            page.click("[data-drawer=core]")
            assert page.is_visible("#core")
        finally:
            browser.close()
        assert not errors


@pytest.mark.slow
def test_a_phone_sees_the_whole_app_without_sideways_scrolling(playwright, folder):
    with shoot.serving(folder) as origin:
        browser, page, errors = shoot.open_page(playwright, origin, RECORDING, 390, 800, touch=True)
        try:
            for name in ("run", "log", "charts", "core", "readouts", "whatif", "history", "dist", "compare"):
                page.evaluate("(n) => window.RB.openDrawer(n)", name)
                page.wait_for_timeout(200)
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), name
                assert page.evaluate("document.getElementById('drawer').getBoundingClientRect().right <= window.innerWidth + 1"), name
            assert page.is_visible("#dock") and page.is_visible("#top")
        finally:
            browser.close()
        assert not errors


BANNED = re.compile(r"recovered|failed|better|worse|steady|score|confidence|SAFETY SYSTEM DISABLED|DISABLED BY THIS CHOICE", re.IGNORECASE)


@pytest.mark.slow
def test_nothing_is_graded_or_given_away_before_it_happens(playwright, folder):
    with shoot.serving(folder) as origin:
        browser, page, errors = shoot.open_page(playwright, origin, RECORDING)
        try:
            page.evaluate("window.__room.feed.setIndex(0)")
            page.click("[data-drawer=core]")
            page.wait_for_timeout(300)
            early = page.inner_text("#captions")
            page.evaluate("window.__room.feed.setIndex(window.RB.state.main.last)")
            page.wait_for_timeout(300)
            late = page.inner_text("#captions")
            assert early.count("tick ") == len(
                [c for c in page.evaluate("window.RB.state.main.captions") if c["tick"] <= 0]), "only captions of moments already reached are listed"
            assert late.count("tick ") == len(page.evaluate("window.RB.state.main.captions")) and late.count("tick ") > early.count("tick ")
            for name in ("run", "log", "charts", "core", "readouts", "whatif", "history", "dist", "compare"):
                page.evaluate("(n) => window.RB.openDrawer(n)", name)
                page.wait_for_timeout(150)
                assert not BANNED.search(page.inner_text("#drawer")), name
            page.evaluate("window.RB.openDrawer('log')")
            page.evaluate("window.__room.feed.setIndex(0)")
            page.wait_for_function("[...document.querySelectorAll('#log .outcome')].some((o) => o.style.display === 'none')", timeout=15000)
            assert "probability of the top option" in page.inner_text("#log")
        finally:
            browser.close()
        assert not errors
