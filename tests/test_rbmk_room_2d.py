"""The RBMK room's low-power 2D view: fixed stills of the 3D room, the projected instrument rects, and the live overlay."""

import json
import re

import pytest
from test_rbmk_app import start_run
from test_rbmk_room import ROOM, SPEC, run_check, source_hash

from examples.rbmk.room import shoot

STILLS = ROOM / 'stills'
DOC = json.loads((STILLS / 'views.json').read_text())
VIEWS = DOC['views']
SETTLED = '!window.RB.state.dirty && !window.__room2d.loop.running'

MASK_CHECK = """async ([views, maskIds, shift]) => {
  const out = {};
  for (const [name, v] of Object.entries(views)) {
    if (name === 'plan') continue;
    const img = new Image();
    img.src = `/room/stills/masks/${name}.png`;
    await img.decode();
    const c = document.createElement('canvas');
    c.width = img.width; c.height = img.height;
    const g = c.getContext('2d', { willReadFrequently: true });
    g.drawImage(img, 0, 0);
    const sx = img.width / v.width, sy = img.height / v.height;
    const idx = (x, y) => {
      const d = g.getImageData(Math.round(x * sx), Math.round(y * sy), 1, 1).data;
      return (Math.round(d[0] / 36) << 6) | (Math.round(d[1] / 36) << 3) | Math.round(d[2] / 36);
    };
    let ok = 0, total = 0;
    const bad = [];
    const check = (key, q) => {
      const cx = (q[0] + q[2] + q[4] + q[6]) / 4, cy = (q[1] + q[3] + q[5] + q[7]) / 4;
      if (cx < 0 || cy < 0 || cx >= v.width || cy >= v.height) return;
      total += 1;
      const w = Math.hypot(q[2] - q[0], q[3] - q[1]);
      const hit = idx(cx + shift * w, cy);
      if (hit === maskIds[key]) ok += 1; else bad.push(key);
    };
    for (const [id, q] of Object.entries(v.items)) check(id, q);
    for (const [id, q] of Object.entries(v.rods)) check(`ROD_${id}`, q);
    for (const [id, q] of Object.entries(v.mimics)) check(`MIMIC_${id}`, q);
    out[name] = { ok, total, bad: bad.slice(0, 5) };
  }
  return out;
}"""


def open2d(pw, origin, run=False, view='front', size=(1280, 800)):
    """The app opened in a Chromium of its own, then switched to a 2D view; returns (browser, page, errors)."""
    browser, page, errors = shoot.open_page(pw, origin, None, size[0], size[1])
    if run:
        start_run(page)
        page.evaluate('window.RB.closeDrawer()')
    page.evaluate('(v) => window.__room.setMode("2d", v)', view)
    page.wait_for_function(f"window.__room.ready && window.__room2d.shown === '{view}'", timeout=30000)
    page.wait_for_function(SETTLED, timeout=10000)
    return browser, page, errors


def test_stills_are_current_and_within_the_file_limit():
    assert DOC['build_hash'] == source_hash(), 'the stills are older than the generator; rebuild them (README)'
    for name, view in VIEWS.items():
        still = STILLS / view['image']
        assert still.stat().st_size < 1_000_000, name
        assert still.read_bytes()[8:12] == b'WEBP', name
        if name != 'plan':
            assert (STILLS / 'masks' / f'{name}.png').stat().st_size < 1_000_000, name


def test_every_feed_instrument_has_a_rect_in_the_front_view():
    ids = {i['id'] for i in SPEC['instruments'] if i['type'] != 'rodmap'}
    assert ids <= set(VIEWS['front']['items']), ids - set(VIEWS['front']['items'])
    for view in VIEWS.values():
        for q in [*view['items'].values(), *view['rods'].values()]:
            assert len(q) == 8


def test_the_rod_panel_shows_every_rod():
    from examples.rbmk.plant.rods import Rods

    rods = {r.id for r in Rods().rods}
    assert set(VIEWS['rodpanel']['rods']) == rods
    assert set(VIEWS['front']['rods']) <= rods and len(VIEWS['front']['rods']) > 190


def test_the_panels_cover_the_bookmarks_and_each_has_a_view():
    panels = SPEC['panels']
    for key, label in SPEC['bookmarks'].items():
        if key != 'overview':
            assert panels[key]['label'] == label
    for pid in panels:
        assert pid in VIEWS and pid in VIEWS['plan']['panels'] and pid in VIEWS['front']['panels'], pid
        assert pid in VIEWS[pid]['panels']
    zones = {i['zone'] for i in SPEC['instruments']}
    assert all(p['zone'] in zones for p in panels.values() if 'zone' in p)


def read(name):
    return (ROOM / name).read_text()


def test_both_views_share_one_definition_and_the_2d_view_needs_no_webgl():
    for page in ('main.js', 'room2d.js', 'room3d.js'):
        assert 'instruments.json' not in read(page) or page == 'main.js', page
    for module in ('room3d.js', 'room2d.js'):
        assert "'./cards.js'" in read(module), module
    assert "'./faces.js'" in read('paint.js') and "'./faces.js'" in read('paint2d.js')
    assert "'./rods.js'" in read('drive.js') and "'./rods.js'" in read('paint2d.js')
    assert "'./screens.js'" in read('drive.js') and "'./screens.js'" in read('paint2d.js')
    assert "'three'" not in ''.join(read(f) for f in ('main.js', 'room2d.js', 'overlay.js', 'paint2d.js', 'faces.js', 'state.js', 'loop.js', 'feed.js'))
    assert re.search(r"import\('\./room3d\.js'\)", read('main.js')), 'the 3D room is loaded only when it is shown'


def test_the_stills_and_their_masks_are_served_with_their_types(tmp_path):
    import http.client
    import threading

    from examples.rbmk.web import server
    from examples.rbmk.web.hub import Hub

    srv = server.serve(Hub(tmp_path), 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def get(path):
        conn = http.client.HTTPConnection('127.0.0.1', srv.server_address[1], timeout=30)
        conn.request('GET', path)
        res = conn.getresponse()
        res.read()
        conn.close()
        return res.status, res.getheader('Content-Type', '')

    try:
        assert get('/room/stills/front.webp') == (200, 'image/webp')
        assert get('/room/stills/masks/front.png') == (200, 'image/png')
        assert get('/room/stills/views.json') == (200, 'application/json')
        assert get('/room/stills/../../web/server.py')[0] == 404
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.slow
def test_projected_rects_match_the_render(playwright, tmp_path):
    with shoot.serving(tmp_path) as origin:
        browser, page, _ = open2d(playwright, origin)
        try:
            args = [VIEWS, DOC['mask_ids']]
            exact = page.evaluate(MASK_CHECK, [*args, 0])
            shifted = page.evaluate(MASK_CHECK, [*args, 1.6])
        finally:
            browser.close()
    assert set(exact) == set(VIEWS) - {'plan'}
    for name, got in exact.items():
        assert got['total'] > 0 and got['ok'] == got['total'], (name, got)
        assert shifted[name]['ok'] < 0.1 * shifted[name]['total'], (name, 'a shifted rect must miss its instrument')


@pytest.mark.slow
def test_the_2d_view_shows_the_apps_run_in_three_modes_and_opens_cards(playwright, tmp_path):
    with shoot.serving(tmp_path) as origin:
        browser, page, errors = open2d(playwright, origin, True)
        try:
            assert page.inner_text('#modestate') == 'LIVE run finished'
            doc = page.evaluate('() => ({ trace: window.RB.state.main.trace, decisions: window.RB.state.main.decisions.filter(Boolean) })')
            feed = tmp_path / 'feed.json'
            feed.write_text(json.dumps(doc))
            checked = run_check(feed)['rows']
            row = next(i for i, r in enumerate(checked) if any(r['lit'].values()))
            page.evaluate('(r) => { window.__room.feed.playing = false; window.__room.feed.setIndex(r); }', row)
            page.wait_for_function(SETTLED, timeout=10000)
            got = page.evaluate("""() => { const a = window.__room2d; return {
                power: a.state.nodes.get('D_POWER_A').value,
                needle: a.overlay.elements.find((e) => e.id === 'D_POWER_A').cur,
                lit: a.overlay.elements.filter((e) => (e.kind === 'ann' || e.kind === 'lamp') && e.lit).map((e) => e.id).sort(),
                rods: a.state.rods.filter((r) => r.value !== undefined).length } }""")
            want = doc['trace'][row]
            assert got['power'] == pytest.approx(want['crew']['readings']['chA'])
            assert got['needle'] == pytest.approx(want['crew']['readings']['chA'] / 150)
            assert got['lit'] == sorted(k for k, v in checked[row]['lit'].items() if v)
            assert got['rods'] == 211
            page.click("#views2d button[data-view='dials']")
            page.wait_for_function("window.__room2d.shown === 'dials'")
            box = page.evaluate("""() => { const a = window.__room2d; const q = a.overlay.elements.find((e) => e.id === 'D_POWER_A').q;
                const r = document.getElementById('overlay').getBoundingClientRect(); const v = a.viewData;
                return [r.left + ((q[0] + q[4]) / 2) / v.width * r.width, r.top + ((q[1] + q[5]) / 2) / v.height * r.height]; }""")
            page.mouse.click(*box)
            assert page.inner_text('#card-title') == 'POWER CH A'
            assert '%' in page.inner_text('#card-now')
            page.click("#views2d button[data-view='plan']")
            page.wait_for_function("window.__room2d.shown === 'plan'")
            rect = page.evaluate("""() => { const a = window.__room2d; const m = a.overlay.elements.find((e) => e.panel === 'decision');
                const r = document.getElementById('overlay').getBoundingClientRect(); const v = a.viewData;
                return [r.left + (m.rect[0] + m.rect[2] / 2) / v.width * r.width, r.top + (m.rect[1] + m.rect[3] / 2) / v.height * r.height]; }""")
            page.mouse.click(*rect)
            page.wait_for_function("window.__room2d.shown === 'decision'")
            assert page.evaluate("document.getElementById('still').naturalWidth") > 0
        finally:
            browser.close()
        assert not errors


@pytest.mark.slow
def test_the_keys_and_the_display_of_the_desk_work_in_the_2d_view(playwright, tmp_path):
    with shoot.serving(tmp_path) as origin:
        browser, page, errors = open2d(playwright, origin, False, 'runpanel')
        try:
            def click(ident):
                box = page.evaluate("""(id) => { const a = window.__room2d; const q = a.overlay.elements.find((e) => e.id === id).q;
                    const r = document.getElementById('overlay').getBoundingClientRect(); const v = a.viewData;
                    return [r.left + ((q[0] + q[4]) / 2) / v.width * r.width, r.top + ((q[1] + q[5]) / 2) / v.height * r.height]; }""", ident)
                page.mouse.click(*box)

            screen = page.evaluate("() => window.__room2d.overlay.elements.find((e) => e.id === 'SCREEN_RUN').kind")
            assert screen == 'run'
            click('K_CONTROLLER_NEXT')
            assert page.evaluate("window.RB.desk.options().controller") == 'naive'
            assert 'CONTROLLER naive' in page.evaluate("window.RB.desk.lines().map((l) => l.label + ' ' + l.value).join('\\n')")
            click('K_START')
            page.wait_for_function("window.RB.state.main && window.RB.state.main.status === 'done'", timeout=60000)
            page.wait_for_function(SETTLED, timeout=10000)
            assert page.evaluate("window.__room2d.overlay.elements.find((e) => e.id === 'K_START').lit") is False
            click('K_SOURCE')
            page.wait_for_function("window.RB.state.mode === 'recorded' && window.__room2d.overlay.elements.find((e) => e.id === 'K_SOURCE').lit === true")
        finally:
            browser.close()
        assert not errors


@pytest.mark.slow
def test_a_decision_picked_in_the_log_moves_the_2d_view_too(playwright, tmp_path):
    with shoot.serving(tmp_path) as origin:
        browser, page, errors = open2d(playwright, origin, True, 'dials')
        try:
            page.click('[data-drawer=log]')
            rows = page.query_selector_all('#log .drow.decision')
            rows[3].click()
            page.wait_for_function(
                """() => { const a = window.__room2d, f = window.__room.feed;
                    return a.state.nodes.get('D_POWER_A').value === window.RB.state.main.trace[f.index].crew.readings.chA && !a.loop.running; }""",
                timeout=15000)
            assert page.evaluate('window.__room.feed.decision.i') == page.evaluate('window.RB.state.selected')
        finally:
            browser.close()
        assert not errors


@pytest.mark.slow
def test_the_2d_view_without_a_run_says_so_and_shows_nothing(playwright, tmp_path):
    with shoot.serving(tmp_path) as origin:
        browser, page, errors = open2d(playwright, origin)
        try:
            assert 'NO RUN' in page.inner_text('#banner')
            got = page.evaluate("""() => { const a = window.__room2d;
                return [a.state.nodes.get('D_POWER_A').value, a.state.rods.filter((r) => r.value !== undefined).length,
                  a.overlay.elements.filter((e) => (e.kind === 'ann' || e.kind === 'lamp' || e.kind === 'button' || e.kind === 'key') && e.lit).length,
                  a.overlay.elements.filter((e) => e.kind === 'dial' && e.cur !== null).length] }""")
            assert got == [None, 0, 0, 0]
        finally:
            browser.close()
        assert not errors


@pytest.mark.slow
def test_a_page_opened_on_a_2d_view_never_loads_the_3d_room_and_can_switch_to_it(playwright, tmp_path):
    with shoot.serving(tmp_path) as origin:
        browser = playwright.chromium.launch(headless=True, args=shoot.GL_ARGS)
        page = browser.new_page(viewport={'width': 1280, 'height': 800})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        try:
            page.goto(f'{origin}/#rodpanel')
            page.wait_for_function("window.__room && window.__room.ready && window.__room2d.shown === 'rodpanel'", timeout=30000)
            loaded = page.evaluate("performance.getEntriesByType('resource').map((r) => r.name)")
            assert not [n for n in loaded if n.endswith(('room.glb.gz', 'three.module.js', 'room3d.js'))], loaded
            assert page.evaluate("window.__room.mode") == '2d' and page.is_hidden('#view')
            page.click('#view3d')
            page.wait_for_function("window.__room.mode === '3d' && window.__room.ready && window.__room.state.ready", timeout=60000)
            assert page.is_visible('#view') and page.is_hidden('#scroller')
            page.click('#view2d')
            page.wait_for_function("window.__room.mode === '2d' && window.__room.ready", timeout=30000)
        finally:
            browser.close()
        assert not errors


@pytest.mark.slow
def test_the_2d_view_redraws_only_what_changed_and_stops_when_idle_or_hidden(playwright, tmp_path):
    with shoot.serving(tmp_path) as origin:
        browser, page, _ = open2d(playwright, origin, True)
        try:
            page.evaluate('() => { window.__room.feed.playing = false; window.__room.feed.setIndex(10); }')
            page.wait_for_function(SETTLED, timeout=10000)

            def draws():
                return page.evaluate('window.__room2d.drawStats.draws')

            settled = draws()
            page.wait_for_timeout(600)
            assert draws() == settled, 'an idle view must not redraw'
            before = page.evaluate('window.__room2d.drawStats.elements')
            page.evaluate('() => window.__room.feed.setIndex(11)')
            page.wait_for_function(f'window.__room2d.drawStats.draws > {settled}')
            page.wait_for_function(SETTLED, timeout=10000)
            redrawn = page.evaluate('window.__room2d.drawStats.elements') - before
            assert 0 < redrawn < 0.5 * page.evaluate('window.__room2d.overlay.elements.length')
            page.evaluate("""() => { Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
                document.dispatchEvent(new Event('visibilitychange')); }""")
            settled = draws()
            page.evaluate('() => window.__room.feed.setIndex(40)')
            page.wait_for_timeout(500)
            assert draws() == settled and not page.evaluate('window.__room2d.loop.running'), 'a hidden page must not draw'
            page.evaluate("""() => { Object.defineProperty(document, 'hidden', { configurable: true, get: () => false });
                document.dispatchEvent(new Event('visibilitychange')); }""")
            page.wait_for_function(f'window.__room2d.drawStats.draws > {settled}')
            cost = page.evaluate('window.__room2d.bench(40)')
            assert cost['elements'] > 300 and cost['fullRedrawMs']['mean'] < 50
        finally:
            browser.close()
