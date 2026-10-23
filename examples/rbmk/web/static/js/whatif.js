/* What-if: from the state at a decision, each action class applied once and the plant then left alone. */
window.RB = window.RB || {};
(function (RB) {
  'use strict';

  const W = { root: null };
  RB.whatif = W;

  W.build = function (root) {
    W.root = root;
    root.replaceChildren();
    W.note = RB.el('p', { class: 'muted' });
    W.out = RB.el('div', { class: 'whatif-out' });
    W.cv = RB.el('canvas', { class: 'chart' });
    root.appendChild(RB.el('h3', { text: 'What if another action had been taken?' }));
    root.appendChild(RB.el('p', { class: 'muted', text: 'For the selected decision, each action class is applied once, to the first target the desk offers for it, at step 2 where it is a rod move, and the plant is then left alone for 10 ticks. No model is asked: these are simulator branches from the plant state the decider was looking at.' }));
    root.appendChild(W.note);
    root.appendChild(W.out);
    root.appendChild(W.cv);
  };

  W.decision = function () {
    const run = RB.state.main;
    const d = run && run.rows[RB.state.selected];
    return d && d.kind === 'decision' ? d : null;
  };

  W.refresh = function () {
    if (!W.root) { return; }
    const run = RB.state.main, d = W.decision();
    W.out.replaceChildren();
    let branches = null;
    if (!run) { W.note.textContent = 'Load or start a run first.'; }
    else if (!d) { W.note.textContent = 'Select a decision in the log, then come back here.'; }
    else if (!run.doc || !run.doc.whatif) { W.note.textContent = run.live ? 'The branches are computed when the run has finished.' : 'This run has no what-if branches.'; }
    else if (!run.doc.whatif[String(d.n)]) { W.note.textContent = 'No branches for request ' + d.n + '.'; }
    else { branches = run.doc.whatif[String(d.n)]; W.note.textContent = 'Request ' + d.n + ' at tick ' + d.tick + '. The decider chose ' + RB.fmtAction(d.choice) + '.'; }
    if (!branches) { W.draw(null, null); return; }
    const head = ['Action class', 'Target', 'Took effect', 'Peak power after 10 ticks', 'Power at the end', 'Core damaged'];
    const t = RB.el('table', { class: 'grid' }, [RB.el('tr', {}, head.map(function (h) { return RB.el('th', { text: h }); }))]);
    Object.keys(branches).forEach(function (cls) {
      const b = branches[cls], last = b.rows[b.rows.length - 1];
      t.appendChild(RB.el('tr', {}, [cls, b.target, b.applied ? 'yes' : 'no', (b.peak_P * 100).toFixed(1) + '%', (last.P * 100).toFixed(1) + '%', b.damaged ? 'yes' : 'no'].map(function (v) { return RB.el('td', { text: String(v) }); })));
    });
    W.out.appendChild(t);
    W.draw(d, branches);
  };

  W.draw = function (d, branches) {
    if (!d || !branches) { RB.drawChart(W.cv, { title: 'Power after each action', unit: '% of nominal', x0: 0, x1: 1, y0: 0, series: [] }); return; }
    const first = Object.keys(branches)[0], x0 = branches[first].rows[0].tick, x1 = branches[first].rows[branches[first].rows.length - 1].tick;
    const colors = ['--c1', '--c2', '--c3', '--c4', '--muted'];
    const series = Object.keys(branches).map(function (cls, k) {
      return { label: cls, color: RB.cssVar(colors[k % colors.length]), dash: k >= 4 ? [4, 3] : [], width: 1.4, pts: branches[cls].rows.map(function (r) { return [r.tick, r.P * 100]; }) };
    });
    RB.drawChart(W.cv, { title: 'Power after each action', unit: '% of nominal', x0: x0, x1: x1, y0: 0, limits: [{ y: 120, label: 'trip 120', color: RB.cssVar('--bad') }], series: series });
  };
})(window.RB);
