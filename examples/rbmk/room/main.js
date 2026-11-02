// The room in the page: one run feed, and the room shown as a 3D scene or, with no WebGL in use, as fixed 2D views.
import { RunFeed } from './feed.js';
import { start2d } from './room2d.js';

const $ = (id) => document.getElementById(id);
const RB = window.RB;
const state = { ready: false };
const app2d = {};
const room = { state, mode: '', ready: false, app2d };
window.__room = room;
window.__room2d = app2d;

let spec = null;
let view3d = null;
let view2d = null;
let loading3d = null;

const hashView = () => location.hash.slice(1);

function ensure3d() {
  if (!loading3d) {
    loading3d = import('./room3d.js')
      .then((m) => m.start3d({ RB, feed: room.feed, spec, $, state }))
      .then((v) => {
        view3d = v;
        Object.assign(room, { goBookmark: v.goBookmark, goTo: v.goTo, THREE: state.THREE });
        state.ready = true;
        return v;
      });
    loading3d.catch((e) => RB.fail(`The 3D room could not load: ${e.message}`));
  }
  return loading3d;
}

function showTools(mode) {
  $('view').hidden = mode !== '3d';
  $('scroller').hidden = mode !== '2d';
  $('bookmarks').hidden = mode !== '3d';
  $('views2d').hidden = mode !== '2d';
  $('mode').hidden = mode !== '3d';
  $('hint3d').hidden = mode !== '3d';
  $('view3d').classList.toggle('on', mode === '3d');
  $('view2d').classList.toggle('on', mode === '2d');
  $('card').hidden = true;
}

async function setMode(mode, name) {
  if (mode === room.mode && (mode === '3d' || name === app2d.view)) return;
  room.ready = false;
  if (room.mode === '3d' && view3d) view3d.hide();
  if (room.mode === '2d') view2d.hide();
  room.mode = mode;
  showTools(mode);
  if (mode === '3d') {
    history.replaceState(null, '', location.pathname + location.search);
    const v = await ensure3d().catch(() => null);
    if (v && room.mode === '3d') {
      v.show();
      room.ready = true;
    }
  } else {
    await view2d.show(name || 'front');
    room.ready = room.mode === '2d';
  }
  RB.touch();
}

function buildBookmarks() {
  const box = $('bookmarks');
  for (const [id, label] of Object.entries(spec.bookmarks)) {
    const b = document.createElement('button');
    b.textContent = label;
    b.className = 'btn';
    b.dataset.bookmark = id;
    b.onclick = async () => {
      const v = await ensure3d().catch(() => null);
      if (v) v.goBookmark(id);
    };
    box.appendChild(b);
  }
  $('mode').onclick = async () => {
    const v = await ensure3d().catch(() => null);
    if (!v) return;
    const next = state.nav.mode === 'fly' ? 'orbit' : 'fly';
    v.setNavigation(next);
    $('mode').textContent = next === 'fly' ? 'Switch to orbit' : 'Switch to fly';
  };
  $('card-close').onclick = () => { $('card').hidden = true; };
  $('card-core').onclick = () => RB.openDrawer('core');
  $('view3d').onclick = () => setMode('3d');
  $('view2d').onclick = () => setMode('2d', hashView());
  addEventListener('hashchange', () => {
    if (room.mode === '2d' && view2d.names.includes(hashView())) view2d.goView(hashView());
  });
}

async function main() {
  const res = await fetch(new URL('./instruments.json', import.meta.url));
  if (!res.ok) throw new Error(`instruments.json answered HTTP ${res.status}`);
  spec = await res.json();
  room.feed = new RunFeed(RB, spec.feed_trace_fields);
  room.spec = spec;
  room.setMode = setMode;
  view2d = start2d({ RB, feed: room.feed, spec, $, app: app2d });
  await view2d.load();
  RB.onFrame(() => { if (room.mode === '2d') view2d.onState(); });
  buildBookmarks();
  const coarse = matchMedia('(pointer: coarse)').matches || innerWidth < 700;
  if (view2d.names.includes(hashView())) await setMode('2d', hashView());
  else {
    await setMode('3d');
    if (coarse && view3d) $('mode').click();
  }
  room.loaded = true;
}

main().catch((e) => {
  RB.fail(`The room could not load: ${e.message}`);
  throw e;
});
