// The 2D room: fixed-viewpoint stills of the 3D room with the live instruments drawn over them from the same feed.
import { Feed } from './feed.js';
import { bindReplay, loadSpec, setHeader } from './header.js';
import { describe, fillCard } from './cards.js';
import { RoomState } from './state.js';
import { Overlay } from './overlay.js';
import { Loop } from './loop.js';
import { bench } from './bench.js';

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const FEED_URL = params.get('feed') || './feed/replay.json';
const WHOLE = ['plan', 'front'];
const app = { ready: false, view: '', views: null, spec: null };
window.__room2d = app;

let feed, state, overlay, loop, timer, housekeeping;

const fail = (message) => {
  $('banner').hidden = false;
  $('banner').textContent = message;
};

function refreshHeader() {
  setHeader(feed, $);
  syncTimers();
}

function syncTimers() {
  const player = feed.playing && !document.hidden;
  if (player && !timer) {
    timer = setInterval(() => {
      feed.tick(0.1);
      if (!feed.playing) refreshHeader();
    }, 100);
  } else if (!player && timer) {
    clearInterval(timer);
    timer = null;
  }
  const clock = feed.live && !feed.finished && !document.hidden;
  if (clock && !housekeeping) housekeeping = setInterval(() => setHeader(feed, $), 1000);
  else if (!clock && housekeeping) {
    clearInterval(housekeeping);
    housekeeping = null;
  }
}

function panelOf(el) {
  if (el.kind === 'rod') return 'rodpanel';
  const ins = el.ins;
  if (!ins) return null;
  return Object.keys(app.spec.panels).find((id) => {
    const p = app.spec.panels[id];
    return (p.zone && p.zone === ins.zone) || (p.ids || []).includes(ins.id);
  }) || null;
}

function showCard(el) {
  app.card = el;
  overlay.selected = el.id;
  fillCard($, describe(el.id, app.spec, state, feed));
  const panel = panelOf(el);
  const open = $('card-open');
  open.hidden = !panel || panel === app.view;
  if (panel) open.textContent = `Open ${app.spec.panels[panel].label}`;
  open.dataset.panel = panel || '';
  overlay.update();
  loop.request();
}

function closeCard() {
  $('card').hidden = true;
  app.card = null;
  overlay.selected = '';
  overlay.update();
  loop.request();
}

function pointer(e) {
  const r = $('overlay').getBoundingClientRect();
  return [((e.clientX - r.left) / r.width) * app.viewData.width, ((e.clientY - r.top) / r.height) * app.viewData.height];
}

function onMove(e) {
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
}

function onClick(e) {
  const hit = overlay.hit(...pointer(e));
  if (!hit) return closeCard();
  if (hit.kind === 'marker') return goView(hit.panel);
  showCard(hit);
}

function layout() {
  const v = app.viewData;
  if (!v) return;
  const box = $('scroller');
  const fit = Math.min(box.clientWidth / v.width, box.clientHeight / v.height);
  const s = Math.max(fit, box.clientWidth < 760 ? 1000 / v.width : 0);
  const frame = $('frame');
  frame.style.width = `${v.width * s}px`;
  frame.style.height = `${v.height * s}px`;
  overlay.setScale(s * Math.min(devicePixelRatio, 2));
  overlay.update(true);
  loop.request();
}

async function goView(name) {
  const v = app.views[name];
  if (!v) return fail(`The stills have no view ${name}. Rebuild them (README).`);
  app.view = name;
  app.viewData = { ...v, name };
  const img = $('still');
  img.src = `./stills/${v.image}`;
  try {
    await img.decode();
  } catch (e) {
    return fail(`The still ${v.image} could not be loaded (${e.message}). Build it as the README says.`);
  }
  for (const b of document.querySelectorAll('#views button')) b.classList.toggle('on', b.dataset.view === name);
  closeCard();
  overlay.setView(app.viewData, 1, WHOLE.includes(name));
  history.replaceState(null, '', `#${name}`);
  layout();
  const box = $('scroller');
  box.scrollLeft = (box.scrollWidth - box.clientWidth) / 2;
  app.shown = name;
}

function buildUi() {
  const box = $('views');
  const entries = [['plan', 'Floor plan'], ['front', 'Front wall'], ...Object.entries(app.spec.panels).map(([id, p]) => [id, p.label])];
  for (const [id, label] of entries) {
    const b = document.createElement('button');
    b.textContent = label;
    b.dataset.view = id;
    b.onclick = () => goView(id);
    box.appendChild(b);
  }
  $('to3d').href = `index.html${location.search}`;
  $('card-close').onclick = closeCard;
  $('card-open').onclick = () => goView($('card-open').dataset.panel);
  $('overlay').addEventListener('pointermove', onMove);
  $('overlay').addEventListener('click', onClick);
  addEventListener('keydown', (e) => { if (e.key === 'Escape') closeCard(); });
  addEventListener('resize', layout);
  bindReplay(feed, $, refreshHeader);
}

function onFeedChange() {
  refreshHeader();
  if (!app.ready) return;
  state.refresh();
  overlay.update();
  if (app.card && !$('card').hidden) fillCard($, describe(app.card.id, app.spec, state, feed));
  loop.request();
}

function onVisible() {
  if (document.hidden) {
    feed.pause();
  } else {
    feed.resume();
    state.refresh();
    overlay.update(true);
    loop.request();
  }
  syncTimers();
}

async function main() {
  const [spec, views] = await Promise.all([loadSpec(), fetch('./stills/views.json').then((r) => {
    if (!r.ok) throw new Error(`stills/views.json answered HTTP ${r.status}; build the stills as the README says`);
    return r.json();
  })]);
  app.spec = spec;
  app.views = views.views;
  feed = new Feed(FEED_URL, spec.feed_trace_fields);
  state = new RoomState(spec, feed);
  overlay = new Overlay($('overlay'), spec, state);
  loop = new Loop((dt) => {
    const moving = overlay.update(false, dt);
    overlay.draw();
    return moving;
  });
  Object.assign(app, { feed, state, overlay, loop, bench: (n) => bench(app, n), stats: loop.stats, drawStats: overlay.stats });
  feed.onchange = onFeedChange;
  buildUi();
  const first = location.hash.slice(1);
  await goView(app.views[first] ? first : 'front');
  state.refresh();
  overlay.update(true);
  feed.start();
  document.addEventListener('visibilitychange', onVisible);
  app.ready = true;
  onFeedChange();
}

main().catch((e) => {
  fail(`The 2D room could not load: ${e.message}`);
  throw e;
});
