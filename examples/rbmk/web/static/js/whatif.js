/* What-if: ask the real model about the same state again, force another action, and show where the run departs. */
window.RB = window.RB || {};
(function (RB) {
  'use strict';

  const W = { branch: null, cv: {}, root: null };
  RB.whatif = W;

  function source(run) {
    if (run.origin === 'live' && run.jobId) { return { job: run.jobId }; }
    if (run.recordingName) { return { recording: run.recordingName }; }
    return null;
  }

  function option(select, value, text) { select.appendChild(RB.el('option', { value: value, text: text })); }

  W.build = function (root) {
    W.root = root;
    root.replaceChildren();
    W.note = RB.el('p', { class: 'muted' });
    W.cls = RB.el('select', { onchange: W.fillTargets });
    W.target = RB.el('select');
    W.step = RB.el('select');
    ['1', '2', '3'].forEach(function (s) { option(W.step, s, 'step ' + s); });
    W.step.value = '2';
    W.horizon = RB.el('input', { type: 'number', min: '1', max: '400', value: '20', class: 'num' });
    W.toEnd = RB.el('input', { type: 'checkbox' });
    W.go = RB.el('button', { class: 'btn primary', text: 'Re-ask the model and run on', onclick: W.start });
    W.out = RB.el('div', { class: 'whatif-out' });
    W.cv.power = RB.el('canvas', { class: 'chart' });
    W.cv.hazard = RB.el('canvas', { class: 'chart' });
    root.appendChild(RB.el('h3', { text: 'What if the model had been made to act differently?' }));
    root.appendChild(RB.el('p', { class: 'muted', text: 'This runs the real decision model again on the saved state at the chosen decision, then applies your action in place of its choice and lets the model carry on. If the model cannot run, the run halts with the error.' }));
    root.appendChild(W.note);
    root.appendChild(RB.el('div', { class: 'form' }, [RB.el('label', {}, ['Action class ', W.cls]), RB.el('label', {}, ['Target ', W.target]),
      RB.el('label', {}, ['Step ', W.step]), RB.el('label', {}, ['Run on for ', W.horizon, ' ticks']), RB.el('label', {}, [W.toEnd, ' to the end of the scenario']), W.go]));
    root.appendChild(W.out);
    root.appendChild(RB.el('div', { class: 'chartpair' }, [W.cv.power, W.cv.hazard]));
    Object.keys(RB.state.info.classes).forEach(function (c) { option(W.cls, c, c + ': ' + RB.state.info.classes[c]); });
    W.cls.value = 'hold';
  };

  W.decision = function () {
    const run = RB.state.main;
    const d = run && run.decisions[RB.state.selected];
    return d && d.kind === 'decision' ? d : null;
  };

  W.refresh = function () {
    if (!W.root) { return; }
    const run = RB.state.main, d = W.decision(), src = run && source(run);
    let msg = '';
    if (!run) { msg = 'Load or start a run first.'; }
    else if (!d) { msg = 'Select a decision in the log, then come back here.'; }
    else if (!src) { msg = 'A what-if needs a run from this server (live, or a file in the recordings folder), not an uploaded file.'; }
    else if (run.controller !== 'model') { msg = 'Only a decision-model run can be re-asked.'; }
    else if (run.live) { msg = 'Wait until the run has finished.'; }
    else if (RB.state.mode === 'recorded') { msg = 'Decision ' + d.i + ' at tick ' + d.tick + ' of a recording. The model that answers the what-if is the live one, not the recorded one.'; }
    else { msg = 'Decision ' + d.i + ' at tick ' + d.tick + '. The model chose ' + RB.fmtAction(d.applied) + '.'; }
    W.note.textContent = msg;
    W.go.disabled = !(run && d && src && run.controller === 'model' && !run.live);
    W.fillTargets(false);
    W.results();
  };

  W.fillTargets = async function (force) {
    const run = RB.state.main, d = W.decision();
    const key = d && run ? (run.jobId || run.recordingName) + ':' + d.i + ':' + W.cls.value : '';
    if (!key || (key === W.targetKey && force !== true && W.target.options.length)) { return; }
    W.targetKey = key;
    const s = source(run);
    if (!s) { return; }
    try {
      const q = (s.job ? 'job=' + s.job : 'recording=' + encodeURIComponent(s.recording)) + '&index=' + d.i + '&cls=' + W.cls.value;
      const list = await RB.api('/api/candidates?' + q);
      W.target.replaceChildren();
      Object.keys(list).forEach(function (k) { option(W.target, k, k + ': ' + list[k]); });
    } catch (e) { RB.fail('Could not list targets: ' + e.message); }
  };

  W.start = async function () {
    const run = RB.state.main, d = W.decision();
    try {
      const job = await RB.api('/api/whatif', { source: source(run), index: d.i, forced: { cls: W.cls.value, target: W.target.value, step: W.step.value },
                                                 horizon: W.toEnd.checked ? null : +W.horizon.value });
      W.branch = new RB.Run('branch'); W.branch.follow(job.id); W.forkRun = run; RB.state.banner = ''; RB.notify();
    } catch (e) { RB.fail('What-if refused: ' + e.message); }
  };

  W.results = function () {
    const b = W.branch, run = W.forkRun;
    W.out.replaceChildren();
    if (!b) { return; }
    const status = b.status === 'running' ? 'running the model...' : b.status === 'done' ? 'finished' : b.status.toUpperCase() + ': ' + b.error;
    W.out.appendChild(RB.el('p', { class: b.status === 'error' ? 'banner bad' : 'muted', text: 'Branch: ' + b.label + ' [' + status + ']' }));
    const first = b.decisions.find(function (x) { return x && x.kind === 'decision'; });
    const fork = b.header && b.header.fork_tick;
    const og = run && run.decisions.find(function (x) { return x && x.kind === 'decision' && x.tick === fork; });
    if (first && og) {
      const same = JSON.stringify(first.model_choice) === JSON.stringify(og.model_choice);
      const dp = Math.max.apply(null, Object.keys(og.answers).map(function (k) {
        const a = og.answers[k], c = first.answers[k];
        return c ? Math.max.apply(null, Object.keys(a.scores).map(function (o) { return Math.abs(a.scores[o] - c.scores[o]); })) : 0;
      }));
      W.out.appendChild(RB.el('p', { text: 'Asked again on the same state: the model ' + (same ? 'gave the same choice' : 'gave a different choice') +
        ' (largest probability difference ' + dp.toFixed(4) + '). Original ' + RB.fmtAction(og.model_choice || og.requested) + '; this time ' + RB.fmtAction(first.model_choice || first.requested) + '. Forced: ' +
        RB.fmtAction(first.applied) + (first.interlocks.length ? ', after interlock ' + first.interlocks.map(function (i) { return i.id; }).join(', ') : '') + '.' }));
    }
    const div = b.doc && b.doc.branch && b.doc.branch.divergence;
    if (div) {
      const rows = [['first tick where power departs', div.first_divergence_tick === null ? 'never' : div.first_divergence_tick],
        ['largest power difference', (div.max_abs_dP * 100).toFixed(1) + ' points'],
        ['peak power, original / branch', (div.peak_P.original * 100).toFixed(0) + '% / ' + (div.peak_P.branch * 100).toFixed(0) + '%'],
        ['hazard at the end, original / branch', div.end_hazard.original + ' / ' + div.end_hazard.branch],
        ['scram, original / branch', div.scram.original + ' / ' + div.scram.branch], ['damage, original / branch', div.damaged.original + ' / ' + div.damaged.branch]];
      const t = RB.el('table', { class: 'grid' });
      rows.forEach(function (r) { t.appendChild(RB.el('tr', {}, [RB.el('th', { text: r[0] }), RB.el('td', { text: String(r[1]) })])); });
      W.out.appendChild(t);
    }
    W.draw();
  };

  W.draw = function () {
    const b = W.branch, run = W.forkRun;
    if (!b || !run || !b.trace.length) { return; }
    const lo = b.trace[0].tick - 2, hi = Math.max(b.trace[b.trace.length - 1].tick, lo + 5);
    const mk = function (tr, get, label, color, dash) {
      return { label: label, color: color, dash: dash, pts: tr.filter(function (r) { return r.tick >= lo && r.tick <= hi + 1; }).map(function (r) { return [r.tick, get(r)]; }) };
    };
    [['power', 'Power, original and branch', '% of nominal', function (r) { return r.P * 100; }, 120, 'trip 120'],
     ['hazard', 'Hazard index, original and branch', '1 = a limit', function (r) { return r.hazard; }, 1, 'limit']].forEach(function (c) {
      RB.drawChart(W.cv[c[0]], { title: c[1], unit: c[2], x0: lo, x1: hi, y0: 0, limits: [{ y: c[4], label: c[5], color: RB.cssVar('--bad') }], cursor: undefined,
        series: [mk(run.trace, c[3], 'original', RB.cssVar('--c1')), mk(b.trace, c[3], 'branch', RB.cssVar('--c2'))],
        marks: [{ x: b.header ? b.header.fork_tick : lo + 2, color: RB.cssVar('--text') }] });
    });
  };
})(window.RB);
