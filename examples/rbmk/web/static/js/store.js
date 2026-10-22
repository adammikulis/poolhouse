/* State of the page: runs, the server calls, the event stream, and frames between trace rows. */
window.RB = window.RB || {};
(function (RB) {
  'use strict';

  RB.state = { info: null, mode: 'live', main: null, naive: null, cursor: 0, playing: false, speed: 2,
               selected: -1, dirty: true, banner: '', follow: true, pauseAtCaptions: false, listeners: [] };
  RB.touch = function () { RB.state.dirty = true; };
  RB.onChange = function (fn) { RB.state.listeners.push(fn); };
  RB.notify = function () { RB.state.listeners.forEach(function (fn) { fn(); }); RB.touch(); };

  RB.fail = function (message) { RB.state.banner = message; RB.notify(); };

  RB.api = async function (path, body) {
    const opts = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                             body: JSON.stringify(body) };
    let res;
    try { res = await fetch(path, opts); } catch (e) { throw new Error('cannot reach the server: ' + e.message); }
    let data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (!res.ok) { throw new Error((data && data.error) || ('server answered ' + res.status)); }
    return data;
  };

  class Run {
    constructor(role) {
      this.role = role; this.header = null; this.trace = []; this.rows = []; this.decisions = []; this.events = [];
      this.captions = []; this.status = 'idle'; this.error = ''; this.jobId = null; this.model = ''; this.label = '';
      this.doc = null; this.asking = null; this.askedAt = 0; this.startedAt = performance.now(); this.origin = 'live'; this.stream = null;
    }
    get rods() { return this.header ? this.header.rods : []; }
    get scenario() { return this.header ? this.header.scenario : null; }
    get controller() { return this.header ? this.header.controller : ''; }
    get memory() { return this.header && this.header.memory ? this.header.memory : 'none'; }
    addRow(row) {
      this.rows[row.i] = row;
      if (row.kind === 'decision') { this.decisions.push(row); } else { this.events.push(row); }
    }
    ingest(ev) {
      if (ev.type === 'header') { this.header = ev; this.label = ev.label; }
      else if (ev.type === 'trace') { this.trace.push(ev.row); this.asking = null; }
      else if (ev.type === 'row') { this.addRow(ev.row); }
      else if (ev.type === 'landed') { Object.assign(this.rows[ev.i], { applied_tick: ev.applied_tick, applied_t_s: ev.applied_t_s, log_line: ev.log_line }); }
      else if (ev.type === 'caption') { this.captions.push(ev.row); }
      else if (ev.type === 'asking') { this.asking = ev.tick; this.askedAt = performance.now(); }
      else if (ev.type === 'model') { this.model = ev.id; RB.state.info.model.id = ev.id; }
      else if (ev.type === 'document') { this.fetchDocument(); }
      else if (ev.type === 'status') { this.status = ev.status; this.error = ev.error; this.asking = null; this.endedAt = performance.now(); }
      RB.touch();
    }
    async fetchDocument() {
      const data = await RB.api('/api/jobs/' + this.jobId + '/document');
      this.adoptDocument(data);
    }
    adoptDocument(doc) {
      this.doc = doc;
      this.captions = doc.captions || this.captions;
      RB.notify();
    }
    loadDocument(doc, label, origin) {
      const scn = doc.meta.scenario;
      this.header = { scenario: { key: scn.key, title: doc.display.title, description: doc.display.description, group: scn.group, seed: scn.seed, ticks: scn.ticks },
                      controller: doc.meta.controller, memory: doc.meta.memory, label: label, rods: doc.rods };
      this.trace = doc.trace; this.rows = doc.rows.slice(); this.decisions = doc.rows.filter(function (r) { return r.kind === 'decision'; });
      this.events = doc.rows.filter(function (r) { return r.kind !== 'decision'; });
      this.captions = doc.captions || []; this.doc = doc; this.label = label; this.origin = origin || 'recorded';
      this.model = doc.meta.backend.model || ''; this.status = doc.meta.error ? 'error' : 'done';
      this.error = doc.meta.error || '';
      RB.notify();
    }
    /* Seconds the decision now in flight has been waiting for the model; 0 when none is. */
    pendingFor() { return this.asking === null || !this.live ? 0 : (performance.now() - this.askedAt) / 1000; }
    /* Wall seconds this run has been going (live), or the model time its decisions took (recorded). */
    wallSeconds() {
      if (this.live || this.origin === 'live') { return ((this.endedAt || performance.now()) - this.startedAt) / 1000; }
      return this.decisions.reduce(function (s, d) { return s + (d && d.latency_ms ? d.latency_ms : 0); }, 0) / 1000;
    }
    follow(jobId) {
      this.jobId = jobId; this.status = "running"; this.startedAt = performance.now(); this.endedAt = 0;
      let after = 0;
      const open = () => {
        const es = new EventSource('/api/jobs/' + jobId + '/events?after=' + after);
        this.stream = es;
        es.onmessage = (m) => {
          const ev = JSON.parse(m.data); after = ev.seq + 1; this.ingest(ev);
          if (ev.type === 'status') { es.close(); }
        };
        es.onerror = () => {
          if (this.status !== 'running') { es.close(); return; }
          es.close();
          setTimeout(() => { if (this.status === 'running') { open(); } }, 1000);
        };
      };
      open();
    }
    stop() { if (this.stream) { this.stream.close(); } this.stream = null; }
    get last() { return this.trace.length - 1; }
    get live() { return this.status === 'running'; }
  }
  RB.Run = Run;

  const NUM = ['P', 'X', 'I', 'void', 'orm', 'orm_printed', 'f', 'Tf', 'p', 'rho', 'level', 'chA', 'chB',
               't_s', 'demand', 'throttle', 'tip'];
  const ARR = ['rods', 'pumps'];

  /* The row at fractional index c: numbers and arrays between rows, everything else from the row before. */
  RB.frameAt = function (run, c) {
    const n = run.trace.length;
    if (!n) { return null; }
    const x = Math.min(Math.max(c, 0), n - 1), i = Math.floor(x), f = x - i;
    const a = run.trace[i], b = run.trace[Math.min(i + 1, n - 1)];
    const out = Object.assign({}, a);
    out.i = i; out.f = f;
    NUM.forEach(function (k) { if (typeof a[k] === 'number' && typeof b[k] === 'number') { out[k] = a[k] + (b[k] - a[k]) * f; } });
    ARR.forEach(function (k) {
      if (a[k] && b[k]) { out[k] = a[k].map(function (v, j) { return v + (b[k][j] - v) * f; }); }
    });
    return out;
  };

  /* Fractional index in ``run`` of plant time ``t`` seconds (for runs shown side by side). */
  RB.indexAtTime = function (run, t) {
    const tr = run.trace;
    if (!tr.length) { return 0; }
    if (t <= tr[0].t_s) { return 0; }
    if (t >= tr[tr.length - 1].t_s) { return tr.length - 1; }
    let lo = 0, hi = tr.length - 1;
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (tr[mid].t_s <= t) { lo = mid; } else { hi = mid; } }
    const span = tr[hi].t_s - tr[lo].t_s;
    return lo + (span > 0 ? (t - tr[lo].t_s) / span : 0);
  };

  RB.fmtClock = function (t) {
    const h = Math.floor(t / 3600), m = Math.floor(t % 3600 / 60), s = Math.floor(t % 60);
    return h + 'h' + String(m).padStart(2, '0') + 'm' + String(s).padStart(2, '0') + 's';
  };

  RB.cssVar = function (name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); };

  RB.el = function (tag, attrs, children) {
    const e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'text') { e.textContent = attrs[k]; } else if (k === 'class') { e.className = attrs[k]; }
      else if (k.startsWith('on')) { e.addEventListener(k.slice(2), attrs[k]); } else { e.setAttribute(k, attrs[k]); }
    });
    (children || []).forEach(function (c) { if (c) { e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); } });
    return e;
  };

  RB.header = function () {
    const run = RB.state.main;
    return run && run.header;
  };
})(window.RB);
