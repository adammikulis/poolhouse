// The 2D room: fixed-viewpoint stills of the 3D room with the live instruments drawn over them from the app's run feed.
import { describe, fillCard } from './cards.js';
import { RoomState } from './state.js';
import { Overlay } from './overlay.js';
import { Loop } from './loop.js';
import { bench } from './bench.js';

const WHOLE = ['plan', 'front'];

// Returns {show, hide, onState, goView}; ``app`` is the object the page and the tests read as window.__room2d.
export function start2d({ RB, feed, spec, $, app }) {
  const state = new RoomState(spec, feed);
  const overlay = new Overlay($('overlay'), spec, state);
  const loop = new Loop((dt) => {
    const moving = overlay.update(false, dt);
    overlay.draw();
    return moving;
  });
  Object.assign(app, { ready: false, view: '', views: null, spec, feed, state, overlay, loop, stats: loop.stats, drawStats: overlay.stats,
    bench: (n) => bench(app, n) });
  let active = false;

  const panelOf = (el) => {
    if (el.kind === 'rod') return 'rodpanel';
    const ins = el.ins;
    if (!ins) return null;
    return Object.keys(spec.panels).find((id) => {
      const p = spec.panels[id];
      return (p.zone && p.zone === ins.zone) || (p.ids || []).includes(ins.id);
    }) || null;
  };

  const showCard = (el) => {
    app.card = el;
    overlay.selected = el.id;
    fillCard($, describe(el.id, spec, state, feed));
    const panel = panelOf(el);
    const open = $('card-open');
    open.hidden = !panel || panel === app.view;
    if (panel) open.textContent = `Open ${spec.panels[panel].label}`;
    open.dataset.panel = panel || '';
    $('card-core').hidden = !(el.kind === 'rod');
    overlay.update();
    loop.request();
  };

  const closeCard = () => {
    $('card').hidden = true;
    app.card = null;
    overlay.selected = '';
    overlay.update();
    loop.request();
  };

  const pointer = (e) => {
    const r = $('overlay').getBoundingClientRect();
    return [((e.clientX - r.left) / r.width) * app.viewData.width, ((e.clientY - r.top) / r.height) * app.viewData.height];
  };

  const onMove = (e) => {
    const hit = overlay.hit(...pointer(e));
    let changed = false;
    for (const el of overlay.elements) {
      if (el.kind !== 'marker') continue;
      const on = el === hit;
      changed = changed || el.hover !== on;
      el.hover = on;
    }
    $('overlay').style.cursor = hit ? 'pointer' : 'default';
    if (changed) {
      overlay.update();
      loop.request();
    }
  };

  const onClick = (e) => {
    const hit = overlay.hit(...pointer(e));
    if (!hit) return closeCard();
    if (hit.kind === 'marker') return goView(hit.panel);
    return showCard(hit);
  };

  const layout = () => {
    const v = app.viewData;
    if (!v || !active || !overlay.view) return;
    const box = $('scroller');
    if (box.clientWidth < 2) return;
    const fit = Math.min(box.clientWidth / v.width, box.clientHeight / v.height);
    const s = Math.max(fit, box.clientWidth < 760 ? 1000 / v.width : 0);
    const frame = $('frame');
    frame.style.width = `${v.width * s}px`;
    frame.style.height = `${v.height * s}px`;
    overlay.setScale(s * Math.min(devicePixelRatio, 2));
    overlay.update(true);
    loop.request();
  };

  const goView = async (name) => {
    const v = app.views[name];
    if (!v) return RB.fail(`The stills have no view ${name}. Rebuild them (README).`);
    app.view = name;
    app.viewData = { ...v, name };
    const img = $('still');
    img.src = new URL(`./stills/${v.image}`, import.meta.url).href;
    try {
      await img.decode();
    } catch (e) {
      return RB.fail(`The still ${v.image} could not be loaded (${e.message}). Build it as the README says.`);
    }
    for (const b of document.querySelectorAll('#views2d button')) b.classList.toggle('on', b.dataset.view === name);
    closeCard();
    overlay.setView(app.viewData, 1, WHOLE.includes(name));
    history.replaceState(null, '', `#${name}`);
    layout();
    const box = $('scroller');
    box.scrollLeft = (box.scrollWidth - box.clientWidth) / 2;
    app.shown = name;
    return undefined;
  };

  const buildUi = () => {
    const box = $('views2d');
    const entries = [['plan', 'Floor plan'], ['front', 'Front wall'], ...Object.entries(spec.panels).map(([id, p]) => [id, p.label])];
    for (const [id, label] of entries) {
      const b = document.createElement('button');
      b.textContent = label;
      b.className = 'btn';
      b.dataset.view = id;
      b.onclick = () => goView(id);
      box.appendChild(b);
    }
    $('card-open').onclick = () => goView($('card-open').dataset.panel);
    $('overlay').addEventListener('pointermove', onMove);
    $('overlay').addEventListener('click', onClick);
    addEventListener('keydown', (e) => { if (e.key === 'Escape' && active) closeCard(); });
    addEventListener('resize', layout);
    new ResizeObserver(layout).observe($('scroller'));
    document.addEventListener('visibilitychange', () => {
      if (document.hidden || !active) return;
      state.refresh();
      overlay.update(true);
      loop.request();
    });
  };

  const onState = () => {
    if (!active || !app.ready) return;
    state.refresh();
    overlay.update();
    if (app.card && !$('card').hidden) fillCard($, describe(app.card.id, spec, state, feed));
    loop.request();
  };

  return {
    names: ['plan', 'front', ...Object.keys(spec.panels)],
    async load() {
      const res = await fetch(new URL('./stills/views.json', import.meta.url));
      if (!res.ok) throw new Error(`stills/views.json answered HTTP ${res.status}; build the stills as the README says`);
      app.views = (await res.json()).views;
      buildUi();
    },
    async show(name) {
      active = true;
      $('card').hidden = true;
      await goView(app.views[name] ? name : 'front');
      state.refresh();
      overlay.update(true);
      app.ready = true;
      loop.request();
    },
    hide() {
      active = false;
      app.ready = false;
      app.card = null;
      overlay.selected = '';
    },
    onState,
    goView,
  };
}
