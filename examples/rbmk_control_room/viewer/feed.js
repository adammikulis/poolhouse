// The feed: the RBMK operator demo's exported run (trace rows, decisions, events), loaded loudly and played or followed.
const OPTIONAL = ['model_risk', 'tip'];
const POLL_MS = 1500;
const STALL_S = 12;

export class Feed {
  constructor(url, traceFields) {
    this.url = url;
    this.required = traceFields.filter((k) => !OPTIONAL.includes(k));
    this.status = 'loading';   // loading | ok | error
    this.error = '';
    this.data = null;
    this.index = 0;
    this.playing = false;
    this.speed = 2;            // rows per second in replay
    this.lastChange = 0;
    this._acc = 0;
    this.onchange = () => {};
  }

  fail(message) {
    this.status = 'error';
    this.error = message;
    this.data = null;
    this.onchange();
  }

  validate(d) {
    if (!d || typeof d !== 'object') return 'the feed is not a JSON object';
    if (!Array.isArray(d.trace) || !d.trace.length) return 'the feed has no trace rows';
    if (!Array.isArray(d.decisions)) return 'the feed has no decisions list';
    const missing = this.required.filter((k) => !(k in d.trace[0]));
    if (missing.length) return `trace rows lack the fields ${missing.join(', ')}`;
    if (!d.meta || !['live', 'replay'].includes(d.meta.mode)) return 'the feed does not say whether it is live or a replay (meta.mode)';
    return '';
  }

  async load() {
    let d;
    try {
      const res = await fetch(this.url, { cache: 'no-store' });
      if (!res.ok) return this.fail(`${this.url} answered HTTP ${res.status}`);
      d = await res.json();
    } catch (e) {
      return this.fail(`could not read ${this.url}: ${e.message}`);
    }
    const why = this.validate(d);
    if (why) return this.fail(`${this.url}: ${why}`);
    for (const r of d.trace) r.rods_mean = Array.isArray(r.rods) && r.rods.length ? r.rods.reduce((a, b) => a + b, 0) / r.rods.length : null;
    const grew = !this.data || d.trace.length !== this.data.trace.length || d.decisions.length !== this.data.decisions.length;
    this.data = d;
    this.status = 'ok';
    this.error = '';
    if (grew) this.lastChange = performance.now();
    if (this.live) this.index = d.trace.length - 1;
    else if (this.index >= d.trace.length) this.index = d.trace.length - 1;
    this.onchange();
  }

  get live() {
    return !!this.data && this.data.meta.mode === 'live';
  }

  get finished() {
    return !!this.data && !!this.data.finished;
  }

  get stalled() {
    return this.live && !this.finished && (performance.now() - this.lastChange) / 1000 > STALL_S;
  }

  start() {
    this.load().then(() => this.poll());
  }

  poll() {
    if (this.timer || this.status !== 'ok' || !this.live || this.finished) return;
    this.timer = setInterval(() => this.load().then(() => this.stop_if_done()), POLL_MS);
  }

  pause() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  resume() {
    if (this.status === 'ok' && this.live && !this.finished) this.load().then(() => this.poll());
    else if (this.status === 'error') this.load().then(() => this.poll());
  }

  stop_if_done() {
    if (this.finished && this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  tick(dt) {
    if (this.status !== 'ok' || this.live || !this.playing) return;
    this._acc += dt * this.speed;
    const whole = Math.floor(this._acc);
    if (whole > 0) {
      this._acc -= whole;
      this.setIndex(this.index + whole);
      if (this.index >= this.data.trace.length - 1) this.playing = false;
    }
  }

  setIndex(i) {
    if (this.status !== 'ok') return;
    this.index = Math.max(0, Math.min(this.data.trace.length - 1, Math.round(i)));
    this.onchange();
  }

  get row() {
    return this.status === 'ok' ? this.data.trace[this.index] : null;
  }

  window(n) {
    return this.status === 'ok' ? this.data.trace.slice(Math.max(0, this.index - n + 1), this.index + 1) : [];
  }

  get decision() {
    if (this.status !== 'ok') return null;
    const tick = this.row.tick;
    let best = null;
    for (const d of this.data.decisions) {
      if (d.tick <= tick) best = d;
      else break;
    }
    return best;
  }

  get modelDecision() {
    if (this.status !== 'ok') return null;
    const tick = this.row.tick;
    let best = null;
    for (const d of this.data.decisions) {
      if (d.tick > tick) break;
      if (d.answers && Object.keys(d.answers).length) best = d;
    }
    return best;
  }

  get events() {
    return this.status === 'ok' ? (this.data.events || []).filter((e) => e.tick <= this.row.tick) : [];
  }

  get modelId() {
    if (this.status !== 'ok') return '';
    for (const d of this.data.decisions) {
      for (const a of Object.values(d.answers || {})) if (a.model) return a.model;
    }
    return '';
  }
}
