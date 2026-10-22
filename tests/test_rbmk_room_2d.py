"""The RBMK 2D room: fixed stills of the 3D room, the projected instrument rects, and the live overlay page."""
import json
import re

import pytest
from test_rbmk_control_room import FEED, SPEC, VIEWER, run_check, source_hash

STILLS = VIEWER / 'stills'
DOC = json.loads((STILLS / 'views.json').read_text())
VIEWS = DOC['views']
PAGE = '/examples/rbmk_control_room/viewer/room2d.html'
READY = "window.__room2d && window.__room2d.ready && window.__room2d.feed.status !== 'loading'"

MASK_CHECK = """async ([views, maskIds, shift]) => {
  const out = {};
  for (const [name, v] of Object.entries(views)) {
    if (name === 'plan') continue;
    const img = new Image();
    img.src = `./stills/masks/${name}.png`;
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


def open2d(pw, origin, feed='./feed/replay.json', view='front', size=(1280, 800)):
    browser = pw.chromium.launch(headless=True)
    page = browser.new_context(viewport={'width': size[0], 'height': size[1]}).new_page()
    errors = []
    page.on('console', lambda m: errors.append(m.text) if m.type == 'error' and 'Failed to load resource' not in m.text else None)
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(f'{origin}{PAGE}?feed={feed}#{view}')
    page.wait_for_function(READY, timeout=30000)
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
    from examples.rbmk_operator.rods import Rods

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
    return (VIEWER / name).read_text()


def test_both_viewers_read_the_one_definition():
    for page in ('main.js', 'room2d.js'):
        assert re.search(r"import \{[^}]*loadSpec[^}]*\} from './header.js'", read(page)), page
        assert 'instruments.json' not in read(page)
    for module in ('main.js', 'room2d.js'):
        for shared in ("'./feed.js'", "'./cards.js'"):
            assert shared in read(module), (module, shared)
    assert "'./faces.js'" in read('paint.js') and "'./faces.js'" in read('paint2d.js')
    assert "'./rods.js'" in read('drive.js') and "'./rods.js'" in read('paint2d.js')
    assert "'./screens.js'" in read('drive.js') and "'./screens.js'" in read('paint2d.js')
    assert "'three'" not in ''.join(read(f) for f in ('room2d.js', 'overlay.js', 'paint2d.js', 'faces.js', 'state.js', 'loop.js'))


def test_the_operator_page_links_to_the_room_and_serves_only_the_room(tmp_path):
    import http.client
    import threading

    from examples.rbmk_operator.web import server
    from examples.rbmk_operator.web.hub import Hub

    srv = server.serve(Hub(tmp_path), 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def get(path):
        conn = http.client.HTTPConnection('127.0.0.1', srv.server_address[1], timeout=30)
        conn.request('GET', path)
        res = conn.getresponse()
        body = res.read()
        conn.close()
        return res.status, res.getheader('Content-Type', ''), body

    try:
        status, _, page = get('/')
        assert status == 200 and b'/room/examples/rbmk_control_room/viewer/room2d.html' in page
        room = '/room/examples/rbmk_control_room/'
        assert get(f'{room}viewer/room2d.html')[0] == 200
        assert get(f'{room}viewer/stills/front.webp')[1] == 'image/webp'
        assert get(f'{room}viewer/room.glb.gz')[1] == 'application/gzip'
        assert get(f'{room}instruments.json')[0] == 200
        assert get('/room/src/poolhouse/ui/assets/three.module.js')[0] == 200
        for path in ('/room/../../../etc/passwd', '/room/pyproject.toml', '/room/examples/rbmk_operator/web/server.py',
                     '/room/examples/rbmk_control_room/../rbmk_operator/web/server.py', '/room/src/poolhouse/serve/gpu.py'):
            assert get(path)[0] == 404, path
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.slow
def test_projected_rects_match_the_render(playwright):
    import shoot

    with shoot.serving() as origin:
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
def test_page_shows_the_feed_in_three_modes_and_opens_cards(playwright):
    import shoot

    with shoot.serving() as origin:
        browser, page, errors = open2d(playwright, origin)
        try:
            page.wait_for_function("document.getElementById('badge').textContent === 'REPLAY'", timeout=30000)
            assert FEED['meta']['model'] in page.inner_text('#model')
            row = next(i for i, r in enumerate(run_check()['rows']) if any(r['lit'].values()))
            page.evaluate('(r) => { const a = window.__room2d; a.feed.playing = false; a.feed.setIndex(r); }', row)
            page.wait_for_function('!window.__room2d.loop.running', timeout=10000)
            got = page.evaluate("""() => { const a = window.__room2d; return {
                fuel: a.state.nodes.get('D_FUELT').value,
                needle: a.overlay.elements.find((e) => e.id === 'D_FUELT').cur,
                lit: a.overlay.elements.filter((e) => (e.kind === 'ann' || e.kind === 'lamp') && e.lit).map((e) => e.id).sort(),
                rods: a.state.rods.filter((r) => r.value !== undefined).length } }""")
            want = FEED['trace'][row]
            assert got['fuel'] == pytest.approx(want['Tf'])
            assert got['needle'] == pytest.approx((want['Tf'] - 200) / 1400)
            assert got['lit'] == sorted(k for k, v in run_check()['rows'][row]['lit'].items() if v)
            assert got['rods'] == 211
            page.click("#views button[data-view='dials']")
            page.wait_for_function("window.__room2d.shown === 'dials'")
            box = page.evaluate("""() => { const a = window.__room2d; const q = a.overlay.elements.find((e) => e.id === 'D_FUELT').q;
                const r = document.getElementById('overlay').getBoundingClientRect(); const v = a.viewData;
                return [r.left + ((q[0] + q[4]) / 2) / v.width * r.width, r.top + ((q[1] + q[5]) / 2) / v.height * r.height]; }""")
            page.mouse.click(*box)
            assert page.inner_text('#card-title') == 'FUEL TEMP'
            assert 'C' in page.inner_text('#card-now')
            page.click("#views button[data-view='plan']")
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
def test_page_without_a_feed_says_so_and_shows_nothing(playwright):
    import shoot

    with shoot.serving() as origin:
        browser, page, errors = open2d(playwright, origin, './feed/missing.json')
        try:
            page.wait_for_function("document.getElementById('badge').textContent === 'NO FEED'", timeout=30000)
            assert 'NO FEED' in page.inner_text('#banner')
            got = page.evaluate("""() => { const a = window.__room2d;
                return [a.state.nodes.get('D_FUELT').value, a.state.rods.filter((r) => r.value !== undefined).length,
                  a.overlay.elements.filter((e) => (e.kind === 'ann' || e.kind === 'lamp' || e.kind === 'button') && e.lit).length,
                  a.overlay.elements.filter((e) => e.kind === 'dial' && e.cur !== null).length] }""")
            assert got == [None, 0, 0, 0]
        finally:
            browser.close()
        assert not errors


@pytest.mark.slow
def test_page_redraws_only_what_changed_and_stops_when_idle_or_hidden(playwright):
    import shoot

    with shoot.serving() as origin:
        browser, page, _ = open2d(playwright, origin)
        try:
            page.evaluate('() => { window.__room2d.feed.playing = false; window.__room2d.feed.setIndex(10); }')
            page.wait_for_function('!window.__room2d.loop.running', timeout=10000)
            def draws():
                return page.evaluate('window.__room2d.drawStats.draws')

            settled = draws()
            page.wait_for_timeout(600)
            assert draws() == settled, 'an idle page must not redraw'
            before = page.evaluate('window.__room2d.drawStats.elements')
            page.evaluate('() => window.__room2d.feed.setIndex(11)')
            page.wait_for_function(f'window.__room2d.drawStats.draws > {settled}')
            page.wait_for_function('!window.__room2d.loop.running', timeout=10000)
            redrawn = page.evaluate('window.__room2d.drawStats.elements') - before
            assert 0 < redrawn < 0.5 * page.evaluate('window.__room2d.overlay.elements.length')
            page.evaluate("""() => { Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
                document.dispatchEvent(new Event('visibilitychange')); }""")
            settled = draws()
            page.evaluate('() => window.__room2d.feed.setIndex(40)')
            page.wait_for_timeout(500)
            assert draws() == settled and not page.evaluate('window.__room2d.loop.running'), 'a hidden page must not draw'
            page.evaluate("""() => { Object.defineProperty(document, 'hidden', { configurable: true, get: () => false });
                document.dispatchEvent(new Event('visibilitychange')); }""")
            page.wait_for_function(f'window.__room2d.drawStats.draws > {settled}')
            cost = page.evaluate('window.__room2d.bench(40)')
            assert cost['elements'] > 300 and cost['fullRedrawMs']['mean'] < 50
        finally:
            browser.close()
