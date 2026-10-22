// The run the room shows: the control room app's one run state (window.RB.state), read through the feed interface the instruments use.
const OPTIONAL = ['model_risk', 'tip'];

export class RunFeed {
  constructor(RB, traceFields) {
    this.RB = RB;
    this.required = traceFields.filter((k) => !OPTIONAL.includes(k));
    this._run = null;
    this._sig = '';
    this._data = null;
    this._serial = 0;
    this._row = { index: -1, stamp: '', value: null };
  }

  get S() {
    return this.RB.state;
  }

  get run() {
    return this.S.main;
  }

  get status() {
    const r = this.run;
    if (!r) return 'error';
    if (!r.trace.length) return 'loading';
    return this.data ? 'ok' : 'error';
  }

  get error() {
    return this.run ? this.RB.roomFault || '' : 'no run is open';
  }

  // Changes whenever the run is replaced or grows, so a painted screen knows when to repaint.
  get stamp() {
    this.data;
    return `${this._serial}:${this._sig}`;
  }

  get data() {
    const r = this.run;
    if (!r || !r.trace.length) {
      this._run = null;
      this._data = null;
      return null;
    }
    const sig = `${r.trace.length}:${r.decisions.length}:${r.events.length}:${r.status}:${r.model}`;
    if (this._run !== r) this._serial += 1;
    if (this._run !== r || this._sig !== sig) {
      this._run = r;
      this._sig = sig;
      this._data = {
        trace: r.trace,
        decisions: r.decisions.filter(Boolean),
        events: r.captions,
        finished: !r.live,
        controller: r.controller,
        scenario: r.scenario ? r.scenario.title : '',
        meta: { mode: r.live ? 'live' : 'replay', model: r.model, rods: r.rods.map((x) => ({ id: x.id, kind: x.kind })) },
      };
      const bad = this.validate(this._data);
      this.RB.roomFault = bad ? `the run cannot be shown in the room: ${bad}` : '';
      if (bad) this._data = null;
    }
    return this._data;
  }

  validate(d) {
    const missing = this.required.filter((k) => !(k in d.trace[0]));
    return missing.length ? `trace rows lack the fields ${missing.join(', ')}` : '';
  }

  get live() {
    return !!this.run && this.run.live;
  }

  get finished() {
    return !!this.run && !this.run.live;
  }

  get stalled() {
    return false;
  }

  get playing() {
    return this.S.playing;
  }

  set playing(v) {
    this.S.playing = !!v;
    this.RB.touch();
  }

  get speed() {
    return this.S.speed;
  }

  set speed(v) {
    this.S.speed = v;
  }

  get index() {
    const r = this.run;
    if (!r || !r.trace.length) return 0;
    return Math.max(0, Math.min(r.trace.length - 1, Math.floor(this.S.cursor)));
  }

  setIndex(i) {
    const r = this.run;
    if (!r || !r.trace.length) return;
    this.S.cursor = Math.max(0, Math.min(r.trace.length - 1, Math.round(i)));
    this.RB.touch();
  }

  tick() {}

  // The row at the playhead, with the mean rod insertion the dials read.
  get row() {
    if (this.status !== 'ok') return null;
    const i = this.index;
    const src = this.run.trace[i];
    const stamp = this.stamp;
    if (this._row.index !== i || this._row.stamp !== stamp || this._row.src !== src) {
      const rods = src.rods;
      const mean = Array.isArray(rods) && rods.length ? rods.reduce((a, b) => a + b, 0) / rods.length : null;
      const d = this._decisionAt(src.tick);
      const risk = d && d.answers && d.answers.risk ? d.answers.risk.expected : null;
      this._row = { index: i, stamp, src, value: { ...src, rods_mean: mean, model_risk: risk } };
    }
    return this._row.value;
  }

  window(n) {
    return this.status === 'ok' ? this.run.trace.slice(Math.max(0, this.index - n + 1), this.index + 1) : [];
  }

  _decisionAt(tick) {
    let best = null;
    for (const d of this.data.decisions) {
      if (d.tick <= tick) best = d;
      else break;
    }
    return best;
  }

  get decision() {
    return this.status === 'ok' ? this._decisionAt(this.row.tick) : null;
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
    return this.status === 'ok' ? this.data.events.filter((e) => e.tick <= this.row.tick) : [];
  }

  get modelId() {
    if (this.status !== 'ok') return '';
    for (const d of this.data.decisions) {
      for (const a of Object.values(d.answers || {})) if (a.model) return a.model;
    }
    return '';
  }
}
