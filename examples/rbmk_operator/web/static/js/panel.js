/* The instrument panel: one tile per console reading, with the flags the operator is not told in numbers. */
window.RB = window.RB || {};
(function (RB) {
  'use strict';

  const g = function (name, scale) {
    return function (r) { const v = r.gauges && r.gauges[name]; return v ? v[0] * (scale || 1) : null; };
  };
  const age = function (name) { return function (r) { const v = r.gauges && r.gauges[name]; return v ? v[1] : null; }; };
  const lagging = function (shown, actual, tol) { return shown !== null && Math.abs(shown - actual) > tol; };

  /* label, unit, digits, scale range, limit marks, reading, actual value, flags */
  const TILES = [
    { k: 'chA', label: 'Power channel A', unit: '%', d: 1, max: 130, bad: 120, get: (r) => g('chA')(r) ?? r.chA * 100, act: (r) => r.P * 100, gauge: 'chA' },
    { k: 'chB', label: 'Power channel B', unit: '%', d: 1, max: 130, bad: 120, get: (r) => g('chB')(r) ?? r.chB * 100, act: (r) => r.P * 100, gauge: 'chB' },
    { k: 'heat', label: 'Heat-balance power', unit: '%', d: 0, max: 130, bad: 120, get: g('heat'), act: (r) => r.P * 100, gauge: 'heat',
      lag: (r) => { const avg = (r.chA + r.chB) / 2, h = (g('heat')(r) || 0) / 100; return avg > 0.02 && Math.abs(h - avg) > 0.25 * Math.max(avg, 0.02) + 0.03; } },
    { k: 'orm', label: 'Reactivity margin (printout)', unit: 'rod-eq', d: 1, max: 40, warn: 20, bad: 15, low: true, get: (r) => r.orm_printed, act: (r) => r.orm,
      extra: (r) => (r.orm_age !== undefined ? ['printed ' + (r.orm_age / 60).toFixed(0) + ' min ago'] : []) },
    { k: 'fuel', label: 'Hottest fuel channel', unit: 'C', d: 0, max: 1500, warn: 1000, bad: 1200, get: g('fuel'), act: (r) => r.Tf, gauge: 'fuel' },
    { k: 'graphite', label: 'Hottest graphite', unit: 'C', d: 0, max: 800, get: g('graphite'), gauge: 'graphite' },
    { k: 'press_l', label: 'Left drum pressure', unit: 'MPa', d: 2, max: 10, warn: 7.6, bad: 8, get: g('press_l'), act: (r) => r.p, gauge: 'press_l' },
    { k: 'press_r', label: 'Right drum pressure', unit: 'MPa', d: 2, max: 10, warn: 7.6, bad: 8, get: g('press_r'), gauge: 'press_r' },
    { k: 'header', label: 'Steam header', unit: 'MPa', d: 2, max: 10, get: g('header'), gauge: 'header' },
    { k: 'level_l', label: 'Left drum level', unit: 'cm', d: 0, min: -60, max: 60, bad: 45, get: g('level_l'), act: (r) => r.level, gauge: 'level_l' },
    { k: 'level_r', label: 'Right drum level', unit: 'cm', d: 0, min: -60, max: 60, bad: 45, get: g('level_r'), gauge: 'level_r' },
    { k: 'flow', label: 'Core flow meters', unit: '%', d: 0, max: 150, bad: 55, low: true, get: g('flow'), act: (r) => r.f * 100, gauge: 'flow',
      lag: (r) => { const v = g('flow')(r); return v !== null && Math.abs(v - r.f * 100) > 10; } },
    { k: 'feedwater', label: 'Feedwater flow', unit: '%', d: 0, max: 150, get: g('feedwater'), gauge: 'feedwater' },
    { k: 'subcool', label: 'Inlet subcooling', unit: 'K', d: 0, max: 40, get: g('subcool'), gauge: 'subcool' },
    { k: 'void', label: 'Void fraction (computer)', unit: '%', d: 0, max: 70, warn: 35, get: (r) => r.void * 100 },
    { k: 'xenon', label: 'Xenon (true, not on the console)', unit: 'index', d: 2, max: 6, get: (r) => r.X, hidden: true },
  ];

  function flags(tile, r) {
    const out = [];
    const a = tile.gauge && r.gauges ? r.gauges[tile.gauge] : null;
    if (a && a[2]) { out.push(['FAULT ' + a[2].toUpperCase() + ': reading does not follow the plant', 'bad']); }
    if (a && a[3]) { out.push(['STALE: ' + a[1].toFixed(0) + ' s old', 'warn']); }
    if (tile.get(r) === null) { out.push(['not recorded in this file', 'info']); }
    else if (tile.lag && tile.lag(r)) { out.push(['LAGGING: actual ' + tile.act(r).toFixed(0) + tile.unit, 'warn']); }
    else if (!tile.lag && tile.act && a && !a[2]) {
      const v = tile.get(r), t = tile.act(r);
      if (v !== null && Math.abs(v - t) > Math.max(Math.abs(t) * 0.12, tile.d === 2 ? 0.2 : 2)) { out.push(['LAGGING: actual ' + t.toFixed(tile.d) + tile.unit, 'warn']); }
    }
    if (tile.k === 'orm') {
      if (Math.abs(r.orm_printed - r.orm) >= 1) { out.push(['OUT OF DATE: actual ' + r.orm.toFixed(1), 'warn']); }
      if (r.orm < 15 && !r.scram) { out.push(['BELOW 15: shut-down rule', 'bad']); }
    }
    if ((tile.k === 'chA' || tile.k === 'chB') && Math.abs(r.chA - r.chB) > 0.15) { out.push(['INCONSISTENT: A and B differ by ' + (Math.abs(r.chA - r.chB) * 100).toFixed(0) + ' points', 'bad']); }
    (tile.extra ? tile.extra(r) : []).forEach(function (t) { out.push([t, 'info']); });
    return out;
  }

  function level(tile, v) {
    if (v === null) { return ''; }
    if (tile.low) { return v < tile.bad ? 'bad' : tile.warn && v < tile.warn ? 'warn' : ''; }
    const m = Math.abs(v);
    return tile.bad !== undefined && m > tile.bad ? 'bad' : tile.warn !== undefined && m > tile.warn ? 'warn' : '';
  }

  class Panel {
    constructor(root) {
      this.root = root; this.tiles = {};
      TILES.forEach((t) => {
        const val = RB.el('div', { class: 'val' }), bar = RB.el('div', { class: 'fill' }), fl = RB.el('div', { class: 'flags' });
        const act = RB.el('div', { class: 'actual' });
        const mark = (v) => (v === undefined ? null : RB.el('i', { class: 'mark', style: 'left:' + (((v - (t.min || 0)) / (t.max - (t.min || 0))) * 100) + '%' }));
        const el = RB.el('div', { class: 'tile' + (t.hidden ? ' hidden-truth' : ''), 'data-k': t.k },
          [RB.el('div', { class: 'tlabel', text: t.label }), val, RB.el('div', { class: 'bar' }, [bar, mark(t.warn), mark(t.bad)]), act, fl]);
        root.appendChild(el);
        this.tiles[t.k] = { el: el, val: val, bar: bar, fl: fl, act: act, def: t };
      });
      this.status = RB.el('div', { class: 'status-strip' });
      root.appendChild(this.status);
    }

    update(r) {
      TILES.forEach((t) => {
        const ui = this.tiles[t.k];
        if (!r) { ui.val.textContent = '-'; ui.fl.textContent = ''; ui.bar.style.width = '0'; return; }
        const v = t.get(r), lv = level(t, v), fl = flags(t, r);
        ui.val.textContent = v === null ? 'no reading' : v.toFixed(t.d) + ' ' + t.unit;
        const lo = t.min || 0;
        ui.bar.style.width = v === null ? '0' : Math.max(0, Math.min(100, (v - lo) / (t.max - lo) * 100)) + '%';
        const worst = fl.some((f) => f[1] === 'bad') ? 'bad' : fl.some((f) => f[1] === 'warn') ? 'warn' : lv;
        ui.el.className = 'tile ' + worst + (t.hidden ? ' hidden-truth' : '');
        ui.act.textContent = t.act && v !== null ? 'actual ' + t.act(r).toFixed(t.d) + ' ' + t.unit : '';
        ui.fl.replaceChildren.apply(ui.fl, fl.map(function (f) { return RB.el('span', { class: 'flag ' + f[1], text: f[0] }); }));
      });
      this.strip(r);
    }

    strip(r) {
      const root = this.status;
      root.replaceChildren();
      if (!r) { return; }
      const chip = (text, cls) => root.appendChild(RB.el('span', { class: 'chip ' + cls, text: text }));
      (r.pumps || []).forEach(function (s, i) { chip('P' + (i + 1) + ' ' + Math.round(s * 100) + '%', s > 0.9 ? 'ok' : s < 0.05 ? 'off' : 'warn'); });
      Object.keys(r.prot || {}).forEach(function (k) { chip(k.replace('_', ' ') + (r.prot[k] ? ' active' : ' DEFEATED'), r.prot[k] ? 'ok' : 'bad'); });
      chip('regulator ' + (r.ar_on ? 'on' : 'off') + ', demand ' + (r.setpoint * 100).toFixed(0) + '%', r.ar_on ? 'ok' : 'warn');
      chip('turbine test: ' + r.test, r.test === 'running' ? 'warn' : 'off');
      if (r.scram) { chip('AZ-5 pressed', 'warn'); }
    }
  }
  RB.Panel = Panel;
})(window.RB);
