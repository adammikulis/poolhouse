/* Scrolling time series on canvas: a generic line chart and the standard set for one run. */
window.RB = window.RB || {};
(function (RB) {
  'use strict';

  const PALETTE = ['--c1', '--c2', '--c3', '--c4'];

  function niceStep(span, n) {
    const raw = span / n, mag = Math.pow(10, Math.floor(Math.log10(raw || 1))), f = raw / mag;
    return (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * mag;
  }

  /* cfg: {title, unit, x0, x1, y0, y1, series:[{label,color,dash,step,pts}], cursor, limits, marks} */
  RB.drawChart = function (cv, cfg) {
    const dpr = window.devicePixelRatio || 1, w = cv.clientWidth, h = cv.clientHeight;
    if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
    const ctx = cv.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, w, h);
    const text = RB.cssVar('--text'), muted = RB.cssVar('--muted'), grid = RB.cssVar('--grid');
    const L = 44, Rr = 8, T = 22, B = 18, pw = w - L - Rr, ph = h - T - B;
    let lo = Infinity, hi = -Infinity;
    cfg.series.forEach(function (s) { s.pts.forEach(function (p) { if (p[1] !== null && isFinite(p[1])) { lo = Math.min(lo, p[1]); hi = Math.max(hi, p[1]); } }); });
    (cfg.limits || []).forEach(function (l) { if (cfg.fitLimits !== false) { lo = Math.min(lo, l.y); hi = Math.max(hi, l.y); } });
    if (cfg.y0 !== undefined) { lo = cfg.y0; }
    if (cfg.y1 !== undefined) { hi = Math.max(cfg.y1, hi); }
    if (!isFinite(lo)) { lo = 0; hi = 1; }
    if (hi - lo < 1e-9) { hi = lo + 1; }
    const pad = (hi - lo) * 0.06; if (cfg.y0 === undefined) { lo -= pad; } hi += pad;
    const X = function (x) { return L + (x - cfg.x0) / Math.max(cfg.x1 - cfg.x0, 1e-9) * pw; };
    const Y = function (y) { return T + ph - (y - lo) / (hi - lo) * ph; };
    ctx.font = '11px system-ui, sans-serif'; ctx.lineWidth = 1;
    const step = niceStep(hi - lo, 4);
    ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
    for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
      ctx.strokeStyle = grid; ctx.beginPath(); ctx.moveTo(L, Y(v)); ctx.lineTo(L + pw, Y(v)); ctx.stroke();
      ctx.fillStyle = muted; ctx.fillText(Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(Math.max(0, Math.min(4, -Math.floor(Math.log10(step))))), L - 4, Y(v));
    }
    ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    const xs = niceStep(cfg.x1 - cfg.x0, 5);
    for (let v = Math.ceil(cfg.x0 / xs) * xs; v <= cfg.x1; v += xs) { ctx.fillStyle = muted; ctx.fillText(cfg.xfmt ? cfg.xfmt(v) : v, X(v), T + ph + 3); }
    (cfg.limits || []).forEach(function (l) {
      ctx.strokeStyle = l.color || RB.cssVar('--bad'); ctx.setLineDash([5, 4]); ctx.beginPath();
      ctx.moveTo(L, Y(l.y)); ctx.lineTo(L + pw, Y(l.y)); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = l.color || RB.cssVar('--bad'); ctx.textAlign = 'right'; ctx.textBaseline = 'bottom';
      ctx.fillText(l.label, L + pw - 2, Y(l.y) - 1);
    });
    ctx.save(); ctx.beginPath(); ctx.rect(L, T, pw, ph); ctx.clip();
    (cfg.marks || []).forEach(function (m) {
      ctx.strokeStyle = m.color || muted; ctx.globalAlpha = 0.55; ctx.setLineDash([2, 3]); ctx.beginPath();
      ctx.moveTo(X(m.x), T); ctx.lineTo(X(m.x), T + ph); ctx.stroke(); ctx.setLineDash([]); ctx.globalAlpha = 1;
    });
    cfg.series.forEach(function (s, k) {
      ctx.strokeStyle = s.color || RB.cssVar(PALETTE[k % 4]); ctx.lineWidth = s.width || 1.7; ctx.setLineDash(s.dash || []);
      ctx.beginPath();
      let pen = false, prev = null;
      s.pts.forEach(function (p) {
        if (p[1] === null || !isFinite(p[1])) { pen = false; return; }
        if (!pen) { ctx.moveTo(X(p[0]), Y(p[1])); pen = true; }
        else { if (s.step && prev) { ctx.lineTo(X(p[0]), Y(prev[1])); } ctx.lineTo(X(p[0]), Y(p[1])); }
        prev = p;
      });
      ctx.stroke(); ctx.setLineDash([]);
    });
    if (cfg.cursor !== undefined && cfg.cursor >= cfg.x0 && cfg.cursor <= cfg.x1) {
      ctx.strokeStyle = text; ctx.lineWidth = 1.2; ctx.beginPath(); ctx.moveTo(X(cfg.cursor), T); ctx.lineTo(X(cfg.cursor), T + ph); ctx.stroke();
    }
    ctx.restore();
    ctx.textBaseline = 'top'; ctx.textAlign = 'left'; ctx.font = '600 12px system-ui, sans-serif'; ctx.fillStyle = text;
    ctx.fillText(cfg.title + (cfg.unit ? ' (' + cfg.unit + ')' : ''), L, 4);
    ctx.font = '11px system-ui, sans-serif'; ctx.textAlign = 'right';
    let lx = w - Rr;
    for (let k = cfg.series.length - 1; k >= 0; k--) {
      const s = cfg.series[k], col = s.color || RB.cssVar(PALETTE[k % 4]), tw = ctx.measureText(s.label).width;
      ctx.fillStyle = muted; ctx.fillText(s.label, lx, 5); ctx.fillStyle = col; ctx.fillRect(lx - tw - 12, 9, 8, 3); lx -= tw + 18;
    }
  };

  function pts(trace, xkey, get, a, b) {
    const out = [];
    for (let i = 0; i < trace.length; i++) {
      const r = trace[i], x = r[xkey];
      if (x < a || x > b) { continue; }
      const v = get(r); out.push([x, v === undefined ? null : v]);
    }
    return out;
  }
  const gauge = function (name, scale) { return function (r) { return r.gauges && r.gauges[name] ? r.gauges[name][0] * (scale || 1) : undefined; }; };

  /* Series definitions of the standard set; ``kinds`` maps rod index to type for the insertion chart. */
  RB.chartDefs = function (kinds) {
    const mean = function (kind) {
      const idx = []; kinds.forEach(function (k, i) { if (k === kind) { idx.push(i); } });
      return function (r) { return idx.length ? idx.reduce(function (s, i) { return s + r.rods[i]; }, 0) / idx.length : undefined; };
    };
    return [
      { id: 'power', title: 'Power', unit: '% of nominal', y0: 0, limits: [{ y: 120, label: 'trip 120' }], series: [
        { label: 'actual', get: (r) => r.P * 100, color: '--c1' }, { label: 'channel A', get: (r) => r.chA * 100, color: '--c2', width: 1 },
        { label: 'channel B', get: (r) => r.chB * 100, color: '--c3', width: 1 }, { label: 'demand', get: (r) => r.setpoint * 100, dash: [4, 3], color: '--muted' }] },
      { id: 'xenon', title: 'Xenon and iodine', unit: 'index', y0: 0, series: [
        { label: 'xenon', get: (r) => r.X, color: '--c1' }, { label: 'iodine', get: (r) => r.I, color: '--c2' }] },
      { id: 'orm', title: 'Reactivity margin (ORM)', unit: 'rod-eq', y0: 0, limits: [{ y: 15, label: 'minimum 15' }], series: [
        { label: 'actual', get: (r) => r.orm, color: '--c1' }, { label: 'printout', get: (r) => r.orm_printed, step: true, color: '--c3', dash: [3, 2] }] },
      { id: 'rods', title: 'Rods inserted, by type', unit: '%', y0: 0, y1: 100, series: [
        { label: 'manual', get: mean('MR'), color: '#3aa0ff' }, { label: 'auto', get: mean('AR'), color: '#34c759' },
        { label: 'short', get: mean('USP'), color: '#ffb020' }, { label: 'emerg.', get: mean('EP'), color: '#ff5d73' }] },
      { id: 'void', title: 'Steam void fraction', unit: '%', y0: 0, series: [{ label: 'void', get: (r) => r.void * 100, color: '--c1' }],
        limits: [{ y: 25.3, label: 'nominal 25', color: '--muted' }] },
      { id: 'pressure', title: 'Drum pressure', unit: 'MPa', limits: [{ y: 8, label: 'trip 8.0' }], series: [
        { label: 'actual', get: (r) => r.p, color: '--c1' }, { label: 'left gauge', get: gauge('press_l'), color: '--c3', width: 1 }] },
      { id: 'drums', title: 'Drum level', unit: 'cm', series: [
        { label: 'actual', get: (r) => r.level, color: '--c1' }, { label: 'left', get: gauge('level_l'), color: '--c2', width: 1 },
        { label: 'right', get: gauge('level_r'), color: '--c3', width: 1 }] },
      { id: 'flow', title: 'Coolant flow', unit: '% of nominal', y0: 0, limits: [{ y: 55, label: 'trip 55 at power', color: '--warn' }], series: [
        { label: 'actual', get: (r) => r.f * 100, color: '--c1' }, { label: 'meters', get: gauge('flow'), color: '--c3', width: 1 },
        { label: 'feedwater', get: gauge('feedwater'), color: '--c2', width: 1 }] },
      { id: 'temps', title: 'Temperatures', unit: 'C', limits: [{ y: 1200, label: 'fuel limit 1200' }], series: [
        { label: 'fuel', get: (r) => r.Tf, color: '--c1' }, { label: 'fuel gauge', get: gauge('fuel'), color: '--c3', width: 1 },
        { label: 'graphite', get: gauge('graphite'), color: '--c2', width: 1 }] },
      { id: 'hazard', title: 'Hazard index', unit: '1 = a limit', y0: 0, limits: [{ y: 1, label: 'limit' }], series: [
        { label: 'hazard', get: (r) => r.hazard, color: '--c1' }] },
      { id: 'risk', title: "Model's risk score", unit: '1 safe, 5 about to be damaged', y0: 1, y1: 5, fitLimits: false, series: [
        { label: 'risk', get: (r) => (r.model_risk === null || r.model_risk === undefined ? null : r.model_risk), step: true, color: '--c2' }] },
    ];
  };

  /* Draw every chart of the set onto its canvas for the main run at ``cursor`` (a fractional row index). */
  RB.drawChartSet = function (canvases, run, cursor, defs) {
    if (!run || !run.trace.length) { return; }
    const sc = run.header.scenario, total = sc.ticks, W = 100, last = run.trace[run.trace.length - 1].tick;
    const first = run.trace[0].tick, cur = RB.frameAt(run, cursor).tick + RB.frameAt(run, cursor).f;
    const x1 = total <= 120 ? Math.max(total, last) : Math.min(Math.max(total, last), Math.max(first + W, cur + 15));
    const x0 = total <= 120 ? first : x1 - W;
    const marks = run.events.map(function (e) { return { x: e.tick }; });
    const dmarks = [];
    run.decisions.filter(function (d) { return d && d.kind === 'decision'; }).forEach(function (d) {
      dmarks.push({ x: RB.snapTick(d), color: d.abstained ? RB.cssVar('--warn') : d.source === 'interlock' ? RB.cssVar('--bad') : RB.cssVar('--grid') });
      if (RB.appliedTick(d) !== RB.snapTick(d)) { dmarks.push({ x: RB.appliedTick(d), color: RB.cssVar('--c2') }); }
    });
    defs.forEach(function (def) {
      const cv = canvases[def.id];
      if (!cv) { return; }
      const series = def.series.map(function (s) {
        return { label: s.label, dash: s.dash, step: s.step, width: s.width, color: s.color && s.color.startsWith('--') ? RB.cssVar(s.color) : s.color,
                 pts: pts(run.trace, 'tick', s.get, x0 - 1, x1 + 1) };
      }).filter(function (s) { return s.pts.some(function (p) { return p[1] !== null; }); });
      RB.drawChart(cv, { title: def.title, unit: def.unit, x0: x0, x1: x1, y0: def.y0, y1: def.y1, series: series, cursor: cur,
                         limits: (def.limits || []).map(function (l) { return { y: l.y, label: l.label, color: l.color && l.color.startsWith('--') ? RB.cssVar(l.color) : l.color }; }),
                         fitLimits: def.fitLimits, marks: marks.concat(dmarks) });
    });
  };
})(window.RB);
