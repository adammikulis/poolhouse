/* The 1986 timeline: the historical record beside the 1986 crew script and the run on screen, aligned at the test start. */
window.RB = window.RB || {};
(function (RB) {
  'use strict';

  /* Seconds after the turbine rundown test began (01:23:04), from secondary sources (World Nuclear Association, Wikipedia). */
  const RECORD = [[0, '01:23:04 test begins'], [36, '01:23:40 AZ-5 pressed']];
  const H = { root: null, crew: null };
  RB.history = H;

  function testStart(run) {
    const row = run.trace.find(function (r) { return r.test === 'running'; });
    return row ? row.t_s : null;
  }

  H.build = function (root) {
    H.root = root;
    root.replaceChildren();
    H.cv = RB.el('canvas', { class: 'timeline' });
    H.note = RB.el('p', { class: 'muted' });
    H.crewBtn = RB.el('button', { class: 'btn', text: 'Add the 1986 crew script run (no model)', onclick: H.runCrew });
    root.appendChild(RB.el('h3', { text: '26 April 1986 beside this run' }));
    root.appendChild(RB.el('p', { class: 'muted', text: 'Time is seconds from the start of the turbine rundown test. The top lane is the historical sequence from secondary sources, not simulation output. The crew lane is a script that follows the crew\'s sequence in this toy; it is not a model.' }));
    root.appendChild(H.crewBtn); root.appendChild(H.note); root.appendChild(H.cv);
    H.cv.addEventListener('click', H.click);
  };

  H.runCrew = async function () {
    try {
      const job = await RB.api('/api/run', { scenario: 'historic_1986', controller: 'crew' });
      H.crew = new RB.Run('crew'); H.crew.follow(job.id); H.crew.laneLabel = '1986 crew script in the toy (not a model)';
    } catch (e) { RB.fail('Could not start the crew script: ' + e.message); }
  };

  H.lanes = function () {
    const main = RB.state.main, lanes = [];
    if (main && main.trace.length) { lanes.push({ run: main, label: (main.controller === 'model' ? 'Model ' + (main.model || '') : main.controller === 'naive' ? 'Naive controller (policy code)' : 'Crew script (not a model)') + ': ' + main.scenario.title }); }
    if (H.crew && H.crew.trace.length && H.crew !== main) { lanes.push({ run: H.crew, label: H.crew.laneLabel }); }
    return lanes;
  };

  H.draw = function () {
    const cv = H.cv, dpr = window.devicePixelRatio || 1, w = cv.clientWidth, lanes = H.lanes(), laneH = 110, top = 96;
    const h = top + lanes.length * laneH + 24;
    cv.style.height = h + 'px'; cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
    const ctx = cv.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, w, h);
    const text = RB.cssVar('--text'), muted = RB.cssVar('--muted'), grid = RB.cssVar('--grid');
    const starts = lanes.map(function (l) { return testStart(l.run); });
    H.note.textContent = lanes.length && starts.every(function (s) { return s === null; }) ? 'No turbine test was started in this run; pick a poisoned start or the 1986 replay.' : '';
    let t0 = -20, t1 = 60;
    lanes.forEach(function (l, i) {
      if (starts[i] === null) { return; }
      const last = l.run.trace[l.run.trace.length - 1].t_s - starts[i];
      t1 = Math.max(t1, Math.min(last, 600)); t0 = Math.min(t0, l.run.trace[0].t_s - starts[i]);
    });
    t0 = Math.max(t0, -60);
    const L = 150, R = 12, X = function (t) { return L + (t - t0) / (t1 - t0) * (w - L - R); };
    H.map = { X: X, lanes: lanes, starts: starts, top: top, laneH: laneH };
    ctx.font = '11px system-ui, sans-serif'; ctx.textBaseline = 'top';
    const step = (t1 - t0) > 300 ? 60 : (t1 - t0) > 100 ? 20 : 10;
    for (let t = Math.ceil(t0 / step) * step; t <= t1; t += step) {
      ctx.strokeStyle = grid; ctx.beginPath(); ctx.moveTo(X(t), 24); ctx.lineTo(X(t), h - 18); ctx.stroke();
      ctx.fillStyle = muted; ctx.textAlign = 'center'; ctx.fillText(t + ' s', X(t), h - 16);
    }
    ctx.textAlign = 'left'; ctx.fillStyle = text; ctx.font = '600 12px system-ui, sans-serif'; ctx.fillText('Historical sequence', 6, 28);
    RECORD.forEach(function (m, k) {
      const x = X(m[0]); ctx.fillStyle = RB.cssVar('--bad'); ctx.beginPath(); ctx.arc(x, 34, 5, 0, 6.283); ctx.fill();
      ctx.font = '11px system-ui, sans-serif'; ctx.fillStyle = text; ctx.textAlign = x > w - 190 ? 'right' : 'left'; ctx.fillText(m[1], x + (x > w - 190 ? -12 : 8), 30 + k * 14);
    });
    lanes.forEach(function (l, i) { H.lane(ctx, l, starts[i], top + i * laneH, laneH, X, w); });
  };

  H.lane = function (ctx, lane, start, y, laneH, X, w) {
    const text = RB.cssVar('--text'), muted = RB.cssVar('--muted');
    ctx.textAlign = 'left'; ctx.fillStyle = text; ctx.font = '600 12px system-ui, sans-serif';
    ctx.fillText(lane.label.length > 24 ? lane.label.slice(0, 24) + '...' : lane.label, 6, y + 4);
    ctx.font = '11px system-ui, sans-serif'; ctx.fillStyle = muted; ctx.fillText(lane.label.length > 24 ? lane.label.slice(24, 60) : '', 6, y + 18);
    if (start === null) { ctx.fillText('no test start in this run', 156, y + 30); return; }
    const tr = lane.run.trace, pmax = Math.max(1.2, Math.max.apply(null, tr.map(function (r) { return r.P; })));
    ctx.strokeStyle = RB.cssVar('--c1'); ctx.lineWidth = 1.6; ctx.beginPath();
    tr.forEach(function (r, k) { const px = X(r.t_s - start), py = y + laneH - 30 - (r.P / pmax) * (laneH - 56); if (k) { ctx.lineTo(px, py); } else { ctx.moveTo(px, py); } });
    ctx.stroke(); ctx.fillStyle = muted; ctx.fillText('power (peak ' + (pmax * 100).toFixed(0) + '%)', w - 150, y + 4);
    lane.hits = [];
    let before = null;
    lane.run.decisions.filter(Boolean).forEach(function (d) {
      const px = X(d.t_s - start), cls = d.choice.cls, show = cls !== 'hold' && cls !== before;
      before = cls;
      ctx.fillStyle = cls === 'hold' ? muted : RB.cssVar('--c2');
      ctx.fillRect(px - 3, y + laneH - 24, 6, 14);
      if (show) { ctx.fillStyle = text; ctx.save(); ctx.translate(px + 3, y + laneH - 26); ctx.rotate(-0.9); ctx.fillText(cls + (d.choice.target !== 'none' ? ' ' + d.choice.target : ''), 0, 0); ctx.restore(); }
      lane.hits.push({ x: px, d: d });
    });
  };

  H.click = function (ev) {
    const m = H.map; if (!m) { return; }
    const rect = H.cv.getBoundingClientRect(), x = ev.clientX - rect.left, y = ev.clientY - rect.top;
    m.lanes.forEach(function (l, i) {
      if (l.run !== RB.state.main || y < m.top + i * m.laneH) { return; }
      const hit = (l.hits || []).reduce(function (b, h) { return Math.abs(h.x - x) < Math.abs(b.x - x) ? h : b; }, { x: 1e9 });
      if (hit.d) { RB.pick(hit.d); }
    });
  };
})(window.RB);
