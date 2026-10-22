/* Side by side with the naive controller: plain policy code that chases the demand, never a model. */
window.RB = window.RB || {};
(function (RB) {
  'use strict';

  const C = { root: null, left: null, right: null };
  RB.compare = C;
  const NAIVE_TEXT = 'Naive controller: plain declared policy code. It withdraws a rod when the average power reading is under demand and inserts one when over; it reads no text and asks no question.';

  C.build = function (root) {
    C.root = root;
    root.replaceChildren();
    C.go = RB.el('button', { class: 'btn primary', text: 'Run the naive controller on this scenario', onclick: C.start });
    C.cvL = RB.el('canvas', { class: 'core mini' });
    C.cvR = RB.el('canvas', { class: 'core mini' });
    C.left = new RB.CoreView(C.cvL); C.right = new RB.CoreView(C.cvR);
    C.titleL = RB.el('h4'); C.titleR = RB.el('h4');
    C.sumL = RB.el('p', { class: 'muted' }); C.sumR = RB.el('p', { class: 'muted' });
    C.cv = { power: RB.el('canvas', { class: 'chart' }), hazard: RB.el('canvas', { class: 'chart' }) };
    root.appendChild(RB.el('h3', { text: 'Side by side with a naive controller' }));
    root.appendChild(RB.el('p', { class: 'muted', text: NAIVE_TEXT }));
    root.appendChild(C.go);
    root.appendChild(RB.el('div', { class: 'sidebyside' }, [RB.el('div', {}, [C.titleL, C.cvL, C.sumL]), RB.el('div', {}, [C.titleR, C.cvR, C.sumR])]));
    root.appendChild(RB.el('div', { class: 'chartpair' }, [C.cv.power, C.cv.hazard]));
  };

  C.start = async function () {
    const main = RB.state.main;
    if (!main) { RB.fail('Start or load a run first.'); return; }
    try {
      const job = await RB.api('/api/run', { scenario: main.scenario.key, controller: 'naive', seed: main.scenario.seed });
      RB.state.naive = new RB.Run('naive'); RB.state.naive.follow(job.id); RB.state.banner = ''; RB.notify();
    } catch (e) { RB.fail('Could not start the naive controller: ' + e.message); }
  };

  C.summary = function (run) {
    if (!run) { return ''; }
    const o = run.doc && run.doc.outcome, s = run.doc && run.doc.score;
    if (!o) { return run.live ? 'running...' : ''; }
    return o.verdict + ': ' + o.why + '. Peak ' + o.peak_MW + ' MW' + (s ? ', score ' + s.total + ' of 100' : '') + '.';
  };

  C.draw = function (cursor) {
    const main = RB.state.main, naive = RB.state.naive;
    C.titleL.textContent = main ? (main.controller === 'model' ? 'Decision model ' + (main.model || '') : main.controller === 'naive' ? 'Naive controller (policy code, not a model)' : 'Crew script (not a model)') : '';
    C.titleR.textContent = naive ? 'Naive controller (plain policy code, not a model)' : '';
    C.sumL.textContent = C.summary(main); C.sumR.textContent = naive ? C.summary(naive) : 'Not run yet.';
    if (main && main.trace.length) {
      const f = RB.frameAt(main, cursor);
      C.left.draw(main.header.rods, f, { paused: !RB.state.playing, highlight: new Set() });
      if (naive && naive.trace.length) { C.right.draw(naive.header.rods, RB.frameAt(naive, RB.indexAtTime(naive, f.t_s)), { paused: !RB.state.playing, highlight: new Set() }); }
      else { C.right.draw([], null); }
    }
    if (!(main && naive && main.trace.length && naive.trace.length)) { return; }
    const t = RB.frameAt(main, cursor).t_s;
    const mk = function (run, get, label, color) { return { label: label, color: color, pts: run.trace.map(function (r) { return [r.t_s / 60, get(r)]; }) }; };
    const hi = Math.max(main.trace[main.trace.length - 1].t_s, naive.trace[naive.trace.length - 1].t_s) / 60;
    [['power', 'Power', '% of nominal', function (r) { return r.P * 100; }, 120, 'trip 120'], ['hazard', 'Hazard index', '1 = a limit', function (r) { return r.hazard; }, 1, 'limit']].forEach(function (c) {
      RB.drawChart(C.cv[c[0]], { title: c[1] + ': model and naive controller', unit: c[2], x0: 0, x1: hi, y0: 0, cursor: t / 60, xfmt: function (v) { return v + ' min'; },
        limits: [{ y: c[4], label: c[5], color: RB.cssVar('--bad') }], series: [mk(main, c[3], main.controller === 'model' ? 'model' : main.controller, RB.cssVar('--c1')), mk(naive, c[3], 'naive controller', RB.cssVar('--c2'))] });
    });
  };
})(window.RB);
