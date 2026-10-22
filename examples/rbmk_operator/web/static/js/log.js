/* The decision log: one row per decision, and the full record of what the model saw and said for one. */
window.RB = window.RB || {};
(function (RB) {
  'use strict';

  const CALL_TITLES = { neutron: 'Neutron panel', coolant: 'Coolant panel', rods: 'Rods panel', instruments: 'Whole console',
                        decision: 'Decision: risk and action class', target: 'Target', step: 'Step size' };
  /* The tick whose console the model read, and the tick its action took effect (the decision tick when the run does not say). */
  RB.snapTick = function (d) { return d.snapshot_tick !== undefined ? d.snapshot_tick : d.tick; };
  RB.appliedTick = function (d) { return d.applied_tick !== undefined ? d.applied_tick : d.tick; };
  RB.fmtAction = function (a) {
    if (!a) { return 'none'; }
    return a.cls + (a.target && a.target !== 'none' ? ' ' + a.target : '') + (a.cls.endsWith('_rod') && a.target !== 'none' ? ' (step ' + a.step + ')' : '');
  };

  function bars(ans) {
    const keys = Object.keys(ans.scores), box = RB.el('div', { class: 'bars' });
    keys.forEach(function (k) {
      const p = ans.scores[k], chosen = k === ans.choice;
      const desc = ans.options && ans.options[k] && ans.options[k] !== k ? ans.options[k] : '';
      box.appendChild(RB.el('div', { class: 'brow' + (chosen ? ' chosen' : '') }, [
        RB.el('div', { class: 'bname', text: k, title: desc }),
        RB.el('div', { class: 'btrack' }, [RB.el('div', { class: 'bfill', style: 'width:' + (p * 100).toFixed(1) + '%' })]),
        RB.el('div', { class: 'bval', text: p.toFixed(3) }),
        desc && keys.length <= 12 ? RB.el('div', { class: 'bdesc', text: desc }) : null]));
    });
    return box;
  }

  function answerBlock(name, ans) {
    const head = [RB.el('b', { text: name }), ' ' + ans.question];
    const meta = ['kind ' + ans.kind, 'certainty ' + ans.certainty.toFixed(3), ans.latency_ms.toFixed(0) + ' ms'];
    if (ans.expected !== null && ans.expected !== undefined) { meta.push('expected ' + ans.expected.toFixed(2)); }
    const box = RB.el('div', { class: 'answer' + (ans.abstained ? ' abstained' : '') }, [RB.el('div', { class: 'q' }, head)]);
    box.appendChild(bars(ans));
    box.appendChild(RB.el('div', { class: 'ameta' }, [meta.join('  |  '), ans.abstained ? RB.el('span', { class: 'flag warn', text: 'ABSTAINED' }) : null,
                                                    RB.el('span', { class: 'muted', text: '  ' + ans.model })]));
    return box;
  }

  RB.renderDetail = function (root, d, onWhatIf) {
    root.replaceChildren();
    if (!d) { root.appendChild(RB.el('p', { class: 'muted', text: 'Select a decision to see what the model saw and every probability it gave.' })); return; }
    root.appendChild(RB.el('h3', { text: 'Decision ' + d.i + ' at tick ' + d.tick + ' (' + RB.fmtClock(d.t_s) + ')' }));
    if (d.kind === 'trip') {
      root.appendChild(RB.el('p', { text: 'Automatic trip. No question was asked: ' + d.interlocks.map(function (x) { return x.id + ' (' + x.detail + ')'; }).join('; ') }));
      return;
    }
    root.appendChild(RB.el('p', {}, ['Requested: ', RB.el('b', { text: RB.fmtAction(d.requested) }), '  Applied: ', RB.el('b', { text: RB.fmtAction(d.applied) }),
                                       '  Source: ', d.source, d.note ? '. ' + d.note : '']));
    if (onWhatIf) { root.appendChild(RB.el('button', { class: 'btn', text: 'What if, from this state...', onclick: function () { onWhatIf(d.i); } })); }
    (d.calls || []).forEach(function (call) {
      const sec = RB.el('section', { class: 'call' }, [RB.el('h4', { text: (CALL_TITLES[call.key] || call.key) + '  (call: ' + call.key + ')' })]);
      const base = (d.calls.find(function (c) { return c.key === 'decision'; }) || {}).state_text || '';
      const text = (call.key === 'target' || call.key === 'step') && call.state_text.startsWith(base) ? call.state_text.slice(base.length).trim() : call.state_text;
      const label = (call.key === 'target' || call.key === 'step') ? 'Added to the console text for this question' : 'The text the model read';
      sec.appendChild(RB.el('details', {}, [RB.el('summary', { text: label + ' (' + call.state_text.length + ' characters)' }), RB.el('pre', { class: 'state', text: text })]));
      Object.keys(call.answers).forEach(function (n) { sec.appendChild(answerBlock(n, call.answers[n])); });
      root.appendChild(sec);
    });
  };

  RB.renderLog = function (root, run, onPick) {
    root.replaceChildren();
    const rows = (run && run.decisions || []).filter(Boolean);
    if (!rows.length) { root.appendChild(RB.el('p', { class: 'muted', text: run && run.live ? 'Waiting for the first decision...' : 'No decisions.' })); return; }
    rows.forEach(function (d) {
      const row = RB.el('div', { class: 'drow ' + d.kind, 'data-i': d.i, 'data-tick': RB.appliedTick(d), 'data-snap': RB.snapTick(d), onclick: function () { onPick(d); } });
      const conf = d.answers && d.answers.class ? d.answers.class.scores[d.answers.class.choice] : null;
      const same = JSON.stringify(d.requested) === JSON.stringify(d.applied);
      if (d.kind === 'busy') {
        row.appendChild(RB.el('div', { class: 'banner warn', text: 'MODEL BUSY at tick ' + d.tick + ': ' + (d.note || 'a decision is still in flight, so nothing new was asked') }));
        root.appendChild(row);
        return;
      }
      row.appendChild(RB.el('div', { class: 'dhead' }, [
        RB.el('span', { class: 'dtick', text: '#' + d.i + '  tick ' + RB.snapTick(d) }), RB.el('span', { class: 'dclock muted', text: RB.fmtClock(d.t_s) }),
        RB.el('span', { class: 'src ' + d.source, text: d.kind === 'trip' ? 'AUTOMATIC TRIP' : d.source }),
        RB.el('span', { class: 'act', text: d.kind === 'trip' ? 'AZ-5' : (same ? RB.fmtAction(d.applied) : RB.fmtAction(d.requested) + '  ->  ' + RB.fmtAction(d.applied)) }),
        conf !== null ? RB.el('span', { class: 'conf', text: 'confidence ' + conf.toFixed(2) }) : null,
        d.model_risk !== null && d.model_risk !== undefined ? RB.el('span', { class: 'conf', text: 'risk ' + d.model_risk.toFixed(1) }) : null,
        d.latency_ms ? RB.el('span', { class: 'muted', text: d.latency_ms.toFixed(0) + ' ms' }) : null]));
      if (RB.appliedTick(d) > RB.snapTick(d)) {
        row.appendChild(RB.el('div', { class: 'delay', text: 'The model saw tick ' + RB.snapTick(d) + ' and its action took effect at tick ' + RB.appliedTick(d) +
          ' (' + (RB.appliedTick(d) - RB.snapTick(d)) + ' ticks later' + (d.latency_ms ? ', ' + (d.latency_ms / 1000).toFixed(1) + ' s to answer' : '') + ').' }));
      }
      if (d.abstained) { row.appendChild(RB.el('div', { class: 'banner warn', text: 'ABSTAINED. ' + d.note })); }
      else if (d.source === 'forced') { row.appendChild(RB.el('div', { class: 'banner info', text: d.note })); }
      (d.interlocks || []).forEach(function (x) {
        row.appendChild(RB.el('div', { class: 'banner bad', text: 'INTERLOCK OVERRIDE ' + x.id + ': ' + x.rule + ' (' + x.detail + ')' }));
      });
      (d.flags || []).forEach(function (x) {
        row.appendChild(RB.el('div', { class: 'banner loud', text: 'SAFETY SYSTEM DISABLED BY THIS CHOICE: ' + x.detail + '. ' + x.rule }));
      });
      if (d.outcome) {
        const o = d.outcome;
        row.appendChild(RB.el('div', { class: 'outcome ' + o.verdict, text: 'Tick ' + o.tick + ': ' + o.verdict + '. Hazard ' + o.hazard_before.toFixed(2) + ' -> ' + o.hazard.toFixed(2) +
                                                        ', power ' + (o.P_before * 100).toFixed(0) + '% -> ' + (o.P * 100).toFixed(0) + '%' }));
      }
      root.appendChild(row);
    });
  };

  /* Mark the row whose decision tick is the latest one at or before ``tick``; scroll it into view when asked. */
  RB.markLog = function (root, tick, selected, scroll) {
    let active = null;
    root.querySelectorAll('.drow').forEach(function (r) {
      const t = +r.getAttribute('data-tick'), seen = +r.getAttribute('data-snap');
      r.classList.toggle('selected', +r.getAttribute('data-i') === selected);
      r.classList.remove('active');
      r.classList.toggle('flight', seen <= tick && tick < t);
      if (t <= tick) { active = r; }
    });
    if (active) {
      active.classList.add('active');
      if (scroll) { root.scrollTop = Math.max(0, active.offsetTop - root.offsetTop - 8); }
    }
  };
})(window.RB);
