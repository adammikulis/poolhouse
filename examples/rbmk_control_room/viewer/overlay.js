// The live layer drawn over a still: every instrument of the view as an element that knows what it shows and when that changes.
import { KINDS, paintMarker, paintRod } from './paint2d.js';

const NEEDLE_EASE = 10;
const NEEDLE_STEP = 0.004;

const boxOf = (q, pad) => {
  const xs = [q[0], q[2], q[4], q[6]], ys = [q[1], q[3], q[5], q[7]];
  const x0 = Math.min(...xs) - pad, y0 = Math.min(...ys) - pad;
  return [x0, y0, Math.max(...xs) + pad - x0, Math.max(...ys) + pad - y0];
};

const overlaps = (a, b) => a[0] < b[0] + b[2] && b[0] < a[0] + a[2] && a[1] < b[1] + b[3] && b[1] < a[1] + a[3];

function inQuad(q, x, y) {
  let sign = 0;
  for (let i = 0; i < 4; i++) {
    const j = (i + 1) % 4;
    const cross = (q[j * 2] - q[i * 2]) * (y - q[i * 2 + 1]) - (q[j * 2 + 1] - q[i * 2 + 1]) * (x - q[i * 2]);
    if (cross === 0) continue;
    if (sign && Math.sign(cross) !== sign) return false;
    sign = Math.sign(cross);
  }
  return true;
}

export class Overlay {
  constructor(canvasEl, spec, state) {
    this.canvas = canvasEl;
    this.g = canvasEl.getContext('2d');
    this.spec = spec;
    this.state = state;
    this.byId = new Map(spec.instruments.map((i) => [i.id, i]));
    this.elements = [];
    this.k = 1;
    this.selected = '';
    this.stats = { draws: 0, ms: 0, max: 0, elements: 0 };
  }

  setView(view, k, whole) {
    this.view = view;
    this.whole = whole;
    this.elements = [];
    for (const [id, q] of Object.entries(view.items)) this.add(this.byId.get(id), id, q);
    for (const [id, q] of Object.entries(view.rods)) this.elements.push(this.make({ kind: 'rod', id: `ROD_${id}`, rodId: id, q, raw: true }));
    for (const [id, q] of Object.entries(view.mimics)) this.elements.push(this.make({ kind: 'mimic', id: 'MIMIC', variant: Number(id) % 4, q }));
    if (whole) this.addMarkers(view);
    this.setScale(k);
    this.update(true);
  }

  add(ins, id, q) {
    const kind = id === 'SCREEN_ORM' ? 'orm' : ins.type;
    this.elements.push(this.make({ kind, id, ins, q }));
  }

  make(el) {
    const kind = KINDS[el.kind];
    el.bbox = boxOf(el.q, 2);
    el.key = null;
    el.cur = null;
    if (kind && kind.make) kind.make(el);
    return el;
  }

  addMarkers(view) {
    const always = view.name === 'plan';
    const ordered = Object.entries(view.panels).sort((a, b) => a[1].rect[0] - b[1].rect[0]);
    ordered.forEach(([id, p], i) => {
      const [x, y, w, h] = p.rect;
      const el = { kind: 'marker', id: `PANEL_${id}`, panel: id, label: p.label, rect: p.rect, raw: true, always, row: i % 3,
        q: [x, y, x + w, y, x + w, y + h, x, y + h], alert: 0, hover: false };
      el.bbox = always ? [0, 0, view.width, y + h + 8] : [x - 6, y - 26, w + 12, h + 54];
      this.elements.push(el);
    });
  }

  setScale(k) {
    this.k = k;
    const W = Math.round(this.view.width * k), H = Math.round(this.view.height * k);
    this.canvas.width = W;
    this.canvas.height = H;
    this.g.setTransform(k, 0, 0, k, 0, 0);
    for (const el of this.elements) el.key = null;
  }

  // Reads the state into every element; returns true while a needle is still travelling.
  update(snap = false, dt = 0) {
    const st = this.state;
    let moving = false;
    for (const el of this.elements) {
      let key;
      if (el.kind === 'dial') {
        const v = st.nodes.get(el.id).value;
        const target = v === null || v === undefined ? null : Math.min(Math.max((v - el.ins.min) / (el.ins.max - el.ins.min), 0), 1);
        el.cur = this.ease(el.cur, target, snap, dt);
        moving = moving || (target !== null && el.cur !== target);
        key = el.cur === null ? 'none' : Math.round(el.cur / NEEDLE_STEP);
      } else if (el.kind === 'ann' || el.kind === 'lamp') {
        el.lit = st.nodes.get(el.id).lit;
        key = el.lit;
      } else if (el.kind === 'button') {
        el.lit = st.nodes.get(el.id).lit;
        key = el.lit;
      } else if (el.kind === 'rod') {
        el.value = st.rods[st.rodIndex.get(el.rodId)].value;
        key = el.value === undefined ? 'off' : Math.round(Math.min(Math.max(el.value, 0), 1) * 20);
      } else if (el.kind === 'marker') {
        const panel = this.spec.panels[el.panel];
        el.alert = st.alert(panel);
        key = `${el.alert}|${el.hover}`;
      } else if (el.kind === 'mimic') key = 'static';
      else key = `${st.feed.status}|${st.feed.index}|${st.feed.data ? st.feed.data.trace.length : 0}`;
      el.next = `${key}|${this.selected === el.id}`;
    }
    return moving;
  }

  ease(cur, target, snap, dt) {
    if (target === null) return null;
    if (cur === null || snap) return target;
    const next = cur + (target - cur) * Math.min(1, dt * NEEDLE_EASE);
    return Math.abs(target - next) < 0.0015 ? target : next;
  }

  // Redraws only the elements whose picture changed, each inside its own clipped box.
  draw() {
    const t0 = performance.now();
    const dirty = this.elements.filter((e) => e.next !== e.key);
    for (const el of dirty) {
      const kind = KINDS[el.kind];
      if (kind && kind.refresh) kind.refresh(el, this.state);
    }
    const g = this.g;
    for (const el of dirty) {
      g.save();
      g.beginPath();
      g.rect(...el.bbox);
      g.clip();
      g.clearRect(...el.bbox);
      for (const o of this.elements) if (o === el || overlaps(o.bbox, el.bbox)) this.paint(o);
      g.restore();
    }
    for (const el of dirty) el.key = el.next;
    const ms = performance.now() - t0;
    if (dirty.length) {
      this.stats.draws += 1;
      this.stats.ms += ms;
      this.stats.max = Math.max(this.stats.max, ms);
      this.stats.elements += dirty.length;
    }
    return dirty.length;
  }

  paint(el) {
    const g = this.g;
    if (el.kind === 'marker') return paintMarker(g, el, this);
    if (el.kind === 'rod') return paintRod(g, el);
    const kind = KINDS[el.kind];
    const [cw, ch] = kind.size;
    const q = el.q;
    g.save();
    g.transform((q[2] - q[0]) / cw, (q[3] - q[1]) / cw, (q[6] - q[0]) / ch, (q[7] - q[1]) / ch, q[0], q[1]);
    kind.paint(g, el);
    g.restore();
    if (this.selected === el.id) {
      g.strokeStyle = '#4aa3df';
      g.lineWidth = 2.5 / this.k;
      g.strokeRect(...boxOf(q, 1));
    }
  }

  // The element under an image point: instruments first, panel markers after.
  hit(x, y) {
    for (let i = this.elements.length - 1; i >= 0; i--) {
      const el = this.elements[i];
      if (el.kind !== 'marker' && inQuad(el.q, x, y)) return el;
    }
    for (const el of this.elements) {
      if (el.kind === 'marker' && x >= el.rect[0] && x <= el.rect[0] + el.rect[2] && y >= el.rect[1] && y <= el.rect[1] + el.rect[3]) return el;
    }
    return null;
  }
}
