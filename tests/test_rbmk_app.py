"""The RBMK control room app in a real headless Chromium: one page, one run state shared by the room and its tools.

Runs of the plain-code naive controller (declared as not a model) drive the checks of the plumbing. The tests marked
real_model start the real decision model with the keys of the desk and open its stored answers as a recording; a missing
model fails them naming the cause. A run is started and watched with the keys and the display of the desk, in the room.
"""

from __future__ import annotations

import http.client
import re
import threading
from pathlib import Path

import pytest
from rbmk_run import HOLD_WAIT_S, already_held, sync_recording

from examples.rbmk.room import shoot
from examples.rbmk.web import server
from examples.rbmk.web.hub import Hub

pytest_plugins = ["real_model"]

SERVED = ["/", "/js/app.js", "/js/desk.js", "/js/store.js", "/js/log.js", "/js/panel.js", "/js/whatif.js", "/js/history.js", "/js/dist.js",
          "/js/compare.js", "/js/core.js", "/js/charts.js", "/css/app.css", "/room/main.js", "/room/feed.js", "/room/room3d.js",
          "/room/room2d.js", "/room/screens.js", "/room/cards.js", "/room/drive.js", "/room/fly.js", "/room/instruments.json"]
BANNED = re.compile(r"recovered|failed|better|worse|score|confidence|SAFETY SYSTEM DISABLED|DISABLED BY THIS CHOICE", re.IGNORECASE)
SETTLED = "!window.RB.state.dirty"


CHOOSE = """([scenario, controller]) => {
  const d = window.RB.desk;
  for (let i = 0; i < d.scenarios().length && d.scenarioInfo().key !== scenario; i++) { d.press('scenario_next'); }
  for (let i = 0; i < 3 && d.options().controller !== controller; i++) { d.press('controller_next'); }
  return d.options();
}"""


def press(page, action: str) -> None:
    """Press a key of the desk's run panel through the same function a click on it calls."""
    page.evaluate("(a) => window.RB.desk.press(a)", action)


def choose(page, scenario: str, controller: str) -> None:
    """Set the scenario and the controller with the desk keys."""
    assert page.evaluate(CHOOSE, [scenario, controller])["controller"] == controller


def start_run(page, scenario: str = "pump_trip", controller: str = "naive", timeout: int = 60000) -> None:
    """Choose a scenario and a controller with the desk keys, press START and wait until the run is finished with its document."""
    choose(page, scenario, controller)
    press(page, "start")
    page.wait_for_function("window.RB.state.main && window.RB.state.main.status === 'done' && window.RB.state.main.doc", timeout=timeout)
    page.wait_for_function("document.getElementById('modestate').textContent === 'LIVE run finished'", timeout=10000)


def screen_point(page, ident: str) -> list[float]:
    """Where, on the page, the middle of an instrument's face is in the 3D room's current view."""
    return page.evaluate("""(id) => {
      const s = window.__room.state, o = s.root.getObjectByName(`INS_${id}__face`), T = s.THREE;
      const box = new T.Box3().setFromObject(o), c = box.getCenter(new T.Vector3()).project(s.nav.camera);
      const r = document.getElementById('view').getBoundingClientRect();
      return [r.left + (c.x + 1) / 2 * r.width, r.top + (1 - c.y) / 2 * r.height];
    }""", ident)


def room_dial(page, ident: str):
    return page.evaluate("(i) => window.__room.state.driver.nodes.get(i).value", ident)


def desk_text(page) -> str:
    return page.evaluate("window.RB.desk.lines().map((l) => l.label + ' ' + l.value).join('\\n')")


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
    assert "pane-run" not in page and "Run console" not in page
    assert not re.search(r"operator", "".join(texts.values()), re.IGNORECASE), "no screen, label or script is called operator"


@pytest.mark.slow
def test_without_a_run_the_room_is_dark_and_says_so(playwright, tmp_path):
    with shoot.serving(tmp_path) as origin:
        browser, page, errors = shoot.open_page(playwright, origin)
        try:
            assert page.inner_text("#modestate") == "LIVE: no run"
            assert "NO RUN" in page.inner_text("#banner")
            assert page.evaluate("window.__room.feed.status") == "error"
            assert room_dial(page, "D_POWER_A") is None
            assert page.is_hidden("#drawer") and page.query_selector("#start") is None, "the run panel is on the desk, not in a drawer"
            text = desk_text(page)
            assert "SOURCE LIVE RUN" in text and "DECIDER Strands 2B (strands-decider-2B-hobson-v19)" in text and "MEMORY none" in text
            assert "RUN none" in text
            press(page, "memory_next")
            assert "MEMORY history" in desk_text(page)
            press(page, "source")
            assert "SOURCE RECORDED RUN" in desk_text(page) and "RECORDING none in the run store" in desk_text(page)
            press(page, "open")
            page.wait_for_function("document.getElementById('banner').textContent.includes('There is no recording')", timeout=5000)
        finally:
            browser.close()
        assert not errors


@pytest.mark.slow
def test_a_run_started_with_the_desk_keys_drives_the_room_and_a_picked_decision_moves_it(playwright, tmp_path):
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
                        return d.nodes.get('D_POWER_A').value === window.RB.state.main.trace[f.index].crew.readings.chA; }""")
                got = page.evaluate("""() => {
                    const S = window.RB.state, f = window.__room.feed, row = S.main.trace[f.index], d = window.__room.state.driver;
                    return { selected: S.selected, cursor: S.cursor, index: f.index, tick: row.tick, snap: S.main.rows[S.selected].tick,
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
            for name in ("log", "charts", "core", "readouts", "whatif", "history", "dist", "compare"):
                page.evaluate("(n) => window.RB.openDrawer(n)", name)
                page.wait_for_function(SETTLED)
                assert not BANNED.search(page.inner_text("#drawer")), name
            assert not BANNED.search(desk_text(page))
        finally:
            browser.close()
        assert not errors


@pytest.mark.slow
def test_a_phone_sees_the_whole_app_without_sideways_scrolling(playwright, tmp_path):
    with shoot.serving(tmp_path) as origin:
        browser, page, errors = shoot.open_page(playwright, origin, None, 390, 800, touch=True)
        try:
            start_run(page)
            for name in ("log", "charts", "core", "readouts", "whatif", "history", "dist", "compare"):
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
@pytest.mark.timeout(1500)
def test_a_real_model_run_shows_its_answers_in_the_log_and_on_the_wall_and_opens_again_from_the_run_store(playwright, tmp_path, real):
    with real.pointer(), shoot.serving(tmp_path, wait_s=HOLD_WAIT_S, hold=already_held) as origin:
        browser, page, errors = shoot.open_page(playwright, origin)
        try:
            choose(page, "fixture_short", "model")
            press(page, "start")
            page.wait_for_function("window.RB.state.main && ['done', 'error', 'stopped'].includes(window.RB.state.main.status)", timeout=1_200_000)
            assert page.evaluate("window.RB.state.main.status") == "done", page.inner_text("#banner")
            model = page.evaluate("window.RB.state.main.model")
            assert model and page.inner_text("#modelid") == model
            page.click("[data-drawer=log]")
            assert "probability of the top option" in page.inner_text("#log")
            page.query_selector_all("#log .drow.decision")[0].click()
            page.wait_for_function("window.__room.feed.modelDecision !== null")
            assert page.evaluate("window.__room.feed.modelDecision.i") == page.evaluate("window.RB.state.selected")
            assert page.evaluate("window.__room.feed.modelId") == model
            saved = (tmp_path / model / "none" / "fixture_short.json").is_file()
            assert saved or "was not saved" in page.inner_text("#banner"), "a finished model run is saved to the run store, or the banner says why"
            name = sync_recording(tmp_path)
            press(page, "source")
            page.wait_for_function(f"window.RB.desk.recordings().some((r) => r.name === {name!r})")
            page.evaluate("(n) => { const d = window.RB.desk; for (let i = 0; i < 99 && d.recordings()[d.recording].name !== n; i++) { d.press('recording_next'); } }", name)
            press(page, "open")
            page.wait_for_function("window.RB.state.main && window.RB.state.main.origin === 'recorded'", timeout=60000)
            page.wait_for_function("document.getElementById('modestate').textContent === 'RECORDED'")
            assert page.inner_text("#modelid") == model and page.evaluate("window.__room.feed.modelId") == model
        finally:
            browser.close()
        assert not errors


@pytest.mark.slow
def test_a_real_click_on_the_keys_of_the_desk_in_the_3d_room_starts_a_run(playwright, tmp_path):
    with shoot.serving(tmp_path) as origin:
        browser, page, errors = shoot.open_page(playwright, origin, None, 1280, 800)
        try:
            page.evaluate("() => window.__room.setMode('3d')")
            page.wait_for_function("window.__room.ready && window.__room.state.ready && window.__room.mode === '3d'", timeout=60000)
            page.evaluate("() => window.__room.goTo('runpanel')")
            page.wait_for_timeout(700)
            choose(page, "pump_trip", "model")
            page.mouse.click(*screen_point(page, "K_CONTROLLER_NEXT"))
            assert page.evaluate("window.RB.desk.options().controller") == "naive"
            page.mouse.click(*screen_point(page, "K_START"))
            page.wait_for_function("window.RB.state.main && window.RB.state.main.status === 'done'", timeout=60000)
            page.wait_for_function("window.__room.state.driver.nodes.get('D_POWER_A').value !== null")
            assert "RUN finished" in desk_text(page)
            page.mouse.click(*screen_point(page, "K_SOURCE"))
            assert page.evaluate("window.RB.state.mode") == "recorded"
            page.wait_for_function("window.__room.state.driver.nodes.get('K_SOURCE').lit === true")
        finally:
            browser.close()
        assert not errors


@pytest.mark.slow
@pytest.mark.real_model
@pytest.mark.timeout(2400)
def test_the_batch_key_runs_poisoned_starts_with_the_real_model_and_pressed_again_resumes(playwright, tmp_path, real):
    with real.pointer(), shoot.serving(tmp_path, wait_s=HOLD_WAIT_S, hold=already_held) as origin:
        browser, page, errors = shoot.open_page(playwright, origin)
        try:
            page.evaluate("() => { window.RB.desk.batchCount = 1; window.RB.desk.batchMemories = ['none']; }")
            press(page, "batch")
            page.wait_for_function("window.RB.desk.batch && window.RB.desk.batch.status !== 'running'", timeout=2_000_000)
            assert page.evaluate("window.RB.desk.batch.status") == "finished", desk_text(page)
            assert page.evaluate("window.RB.desk.batch.done") == 1 and "BATCH finished, 1 of 1" in desk_text(page)
            assert len(list(tmp_path.glob("*/none/start_1_0.json"))) == 1
            first = page.evaluate("window.RB.desk.batch.jobId")
            press(page, "batch")
            page.wait_for_function(f"window.RB.desk.batch.jobId !== {first!r} && window.RB.desk.batch.status === 'finished'", timeout=600_000)
            assert page.evaluate("[window.RB.desk.batch.done, window.RB.desk.batch.total, window.RB.desk.batch.last === undefined]") == [1, 1, True], \
                "a start already in the run store is not run again"
        finally:
            browser.close()
        assert not errors
