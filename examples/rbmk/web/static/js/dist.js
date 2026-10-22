/* The poisoned starts: what each run ended with (damage, shutdown, peak power, margin), and each run to open. */
window.RB = window.RB || {};
(function (RB) {
  'use strict';

  const D = { root: null, batch: null };
  RB.dist = D;

  D.build = function (root) {
    D.root = root;
    root.replaceChildren();
    D.repeats = RB.el('input', { type: 'number', min: '1', max: '20', value: '1', class: 'num' });
    D.run = RB.el('button', { class: 'btn primary', text: 'Run every poisoned start with the model', onclick: D.start });
    D.stop = RB.el('button', { class: 'btn', text: 'Stop', onclick: D.halt });
    D.body = RB.el('div', { class: 'dist-body' });
    root.appendChild(RB.el('h3', { text: 'What each poisoned start ended with' }));
    root.appendChild(RB.el('p', { class: 'muted', text: 'Eight start states close to the one before the 1986 test: low power, xenon above equilibrium, a thin rod margin, four protections switched off. Each row is what one run ended with; open a row to see it in the room.' }));
    root.appendChild(RB.el('div', { class: 'form' }, [RB.el('label', {}, ['Runs per start ', D.repeats]), D.run, D.stop]));
    root.appendChild(D.body);
  };

  D.start = async function () {
    const keys = RB.state.info.scenarios.filter(function (s) { return s.group === 'poisoned'; }).map(function (s) { return s.key; });
    try {
      const job = await RB.api('/api/batch', { scenarios: keys, repeats: +D.repeats.value });
      D.batch = { jobId: job.id, items: [], plan: [], status: 'running', error: '', model: '', current: null };
      const es = new EventSource('/api/jobs/' + job.id + '/events');
      es.onmessage = function (m) {
        const ev = JSON.parse(m.data), b = D.batch;
        if (ev.type === 'plan') { b.plan = ev.items; } else if (ev.type === 'item_start') { b.current = ev; }
        else if (ev.type === 'progress') { b.progress = ev; } else if (ev.type === 'item_done') { b.items[ev.index] = ev; b.model = ev.model; }
        else if (ev.type === 'status') { b.status = ev.status; b.error = ev.error; b.current = null; es.close(); }
        RB.touch();
      };
      es.onerror = function () { if (D.batch.status !== 'running') { es.close(); } };
      RB.state.banner = ''; RB.notify();
    } catch (e) { RB.fail('Batch refused: ' + e.message); }
  };

  D.halt = function () { if (D.batch && D.batch.status === 'running') { RB.api('/api/stop', { job: D.batch.jobId }).catch(function (e) { RB.fail(e.message); }); } };

  D.rows = function () {
    if (RB.state.mode === 'recorded') {
      return (RB.state.recordings || []).filter(function (r) { return r.ok && r.controller === 'model' && r.group === 'poisoned'; })
        .map(function (r) { return { title: r.title, scenario: r.scenario, seed: r.seed, damaged: r.damaged, scram: r.scram, margin: r.final_orm, power: r.final_P, peak: r.peak_MW, n: r.decisions, abst: '', model: r.model, open: function () { RB.openRecording(r.name); } }; });
    }
    const b = D.batch;
    if (!b) { return []; }
    return b.items.filter(Boolean).map(function (it) {
      return { title: it.scenario, scenario: it.scenario, seed: it.seed, damaged: it.outcome.damaged, scram: it.outcome.scram, margin: it.outcome.final_orm, power: it.outcome.final_P, peak: it.outcome.peak_MW, n: it.decisions, abst: it.abstained,
               model: it.model, open: async function () { const doc = await RB.api('/api/jobs/' + b.jobId + '/document?index=' + it.index); RB.openDocument(doc, 'live batch run, model ' + it.model, 'live'); } };
    });
  };

  D.draw = function () {
    const live = RB.state.mode === 'live', rows = D.rows(), b = D.batch;
    D.run.style.display = D.stop.style.display = D.repeats.parentNode.style.display = live ? '' : 'none';
    D.run.disabled = !!(b && b.status === 'running');
    D.body.replaceChildren();
    const bad = rows.filter(function (r) { return r.damaged; }).length, ok = rows.length - bad, down = rows.filter(function (r) { return r.scram; }).length;
    if (live && b) {
      const done = b.items.filter(Boolean).length;
      D.body.appendChild(RB.el('p', { class: b.status === 'error' ? 'banner bad' : 'muted', text: 'Batch ' + b.status + ': ' + (b.plan.length ? done + ' of ' + b.plan.length + ' runs done' : 'waiting for the GPU hold and the model') +
        (b.current ? '; running ' + b.current.scenario + ' (seed ' + b.current.seed + ')' + (b.progress ? ', tick ' + b.progress.tick + ' of ' + b.progress.ticks : '') : '') + (b.error ? '. ' + b.error : '') }));
    }
    if (!rows.length) { D.body.appendChild(RB.el('p', { class: 'muted', text: live ? (b && b.status === 'running' ? 'The first run has not finished yet.' : 'No batch has run yet.') : 'No poisoned-start model recordings in the recordings folder.' })); return; }
    const total = rows.length, bar = RB.el('div', { class: 'distbar' }, [
      ok ? RB.el('div', { class: 'ok', style: 'width:' + (ok / total * 100) + '%', text: 'core intact ' + ok }) : null,
      bad ? RB.el('div', { class: 'bad', style: 'width:' + (bad / total * 100) + '%', text: 'core damaged ' + bad }) : null]);
    D.body.appendChild(RB.el('p', { text: 'Model ' + (rows[0].model || '') + ': core damaged in ' + bad + ' of ' + total + ' runs, reactor shut down in ' + down + ' of ' + total + '.' }));
    D.body.appendChild(bar);
    const t = RB.el('table', { class: 'grid dist' }, [RB.el('tr', {}, ['Start', 'Seed', 'Core damaged', 'Shut down', 'Final margin', 'Final power', 'Peak power (MW)', 'Decisions', 'Abstained'].map(function (h) { return RB.el('th', { text: h }); }))]);
    rows.forEach(function (r) {
      const tr = RB.el('tr', { class: 'clickable', onclick: function () { Promise.resolve(r.open()).catch(function (e) { RB.fail(e.message); }); } },
        [r.title, r.seed, r.damaged ? 'yes' : 'no', r.scram ? 'yes' : 'no', r.margin === null || r.margin === undefined ? '' : r.margin.toFixed(1), r.power === null || r.power === undefined ? '' : (r.power * 100).toFixed(0) + '%', r.peak, r.n, r.abst].map(function (v) { return RB.el('td', { text: String(v) }); }));
      t.appendChild(tr);
    });
    D.body.appendChild(t);
  };
})(window.RB);
