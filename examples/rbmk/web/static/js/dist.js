/* The poisoned starts in the local run store: what each recorded run ended with, and each run to open. */
window.RB = window.RB || {};
(function (RB) {
  'use strict';

  const D = { root: null, data: { rows: [], pending: 0 }, timer: 0 };
  RB.dist = D;
  const STATES = [['damaged', 'core damaged', 'bad'], ['margin_below_15', 'margin below 15, no damage', 'warn'], ['margin_at_least_15', 'margin at least 15, no damage', 'ok']];

  D.build = function (root) {
    D.root = root;
    root.replaceChildren();
    D.body = RB.el('div', { class: 'dist-body' });
    root.appendChild(RB.el('h3', { text: 'What each poisoned start ended with' }));
    root.appendChild(RB.el('p', { class: 'muted', text: 'Recordings in the local run store (made with the Run console or `python -m examples.rbmk.run batch`) that start from a xenon-poisoned state: low power, xenon above equilibrium, a thin rod margin, three protections switched off and a turbine test in the programme. Each row is one run replayed with no model; open a row to see it in the room.' }));
    root.appendChild(D.body);
  };

  D.load = async function () {
    try { D.data = await RB.api('/api/summary'); } catch (e) { RB.fail(e.message); return; }
    RB.touch();
    clearTimeout(D.timer);
    if (D.data.pending > 0) { D.timer = setTimeout(D.load, 2000); }
  };

  D.draw = function () {
    if (!D.loaded) { D.loaded = true; D.load(); }
    const rows = D.data.rows;
    D.body.replaceChildren();
    if (D.data.pending > 0) { D.body.appendChild(RB.el('p', { class: 'muted', text: 'Replaying ' + D.data.pending + ' more recordings...' })); }
    if (!rows.length) { if (!D.data.pending) { D.body.appendChild(RB.el('p', { class: 'muted', text: 'The run store has no poisoned-start recordings.' })); } return; }
    const count = function (st, mem) { return rows.filter(function (r) { return r.state === st && (mem === undefined || r.memory === mem); }).length; };
    const memories = Array.from(new Set(rows.map(function (r) { return r.memory; }))).sort();
    memories.forEach(function (mem) {
      const mine = rows.filter(function (r) { return r.memory === mem; });
      D.body.appendChild(RB.el('p', { text: 'Memory ' + mem + ': ' + STATES.map(function (s) { return count(s[0], mem) + ' ' + s[1]; }).join(', ') + ', of ' + mine.length + ' runs.' }));
      D.body.appendChild(RB.el('div', { class: 'distbar' }, STATES.filter(function (s) { return count(s[0], mem); }).map(function (s) {
        return RB.el('div', { class: s[2], style: 'width:' + (count(s[0], mem) / mine.length * 100) + '%', text: s[1] + ' ' + count(s[0], mem) });
      })));
    });
    const head = ['Start', 'Memory', 'Model', 'End state', 'AZ-5 pressed', 'Final margin', 'Final power', 'Peak power (MW)', 'Decisions'];
    const t = RB.el('table', { class: 'grid dist' }, [RB.el('tr', {}, head.map(function (h) { return RB.el('th', { text: h }); }))]);
    rows.forEach(function (r) {
      const st = STATES.find(function (s) { return s[0] === r.state; });
      const cells = [r.scenario, r.memory, r.model || '', st ? st[1] : r.state, r.az5_pressed ? 'yes' : 'no',
                     r.final_margin === undefined ? '' : r.final_margin.toFixed(1), r.final_power === undefined ? '' : (r.final_power * 100).toFixed(0) + '%', r.peak_MW === undefined ? '' : r.peak_MW, r.decisions];
      t.appendChild(RB.el('tr', { class: 'clickable', onclick: function () { RB.openRecording(r.name); } }, cells.map(function (v) { return RB.el('td', { text: String(v) }); })));
    });
    D.body.appendChild(t);
  };
})(window.RB);
