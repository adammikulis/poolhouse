/* The log: one row per decision and per plant event, and the full record of what the model saw and said for one decision. */
window.RB = window.RB || {};
(function (RB) {
  'use strict';

  const CALL_TITLES = { decision: 'Decision: risk rating and action class', target: 'Target', step: 'Step size' };
  const NOT_YET = 1e9;
  /* The tick whose console the model read, and the tick its action took effect (never, while the answer is still to land). */
  RB.snapTick = function (d) { return d.tick; };
  RB.appliedTick = function (d) { return d.applied_tick === null || d.applied_tick === undefined ? NOT_YET : d.applied_tick; };
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
    const box = RB.el('div', { class: 'answer' }, [RB.el('div', { class: 'q' }, head)]);
    box.appendChild(bars(ans));
    box.appendChild(RB.el('div', { class: 'ameta' }, [meta.join('  |  '), RB.el('span', { class: 'muted', text: '  ' + ans.model })]));
    return box;
  }

  RB.renderDetail = function (root, d, onWhatIf) {
    root.replaceChildren();
    if (!d) { root.appendChild(RB.el('p', { class: 'muted', text: 'Select a decision to see what the model saw and every probability it gave.' })); return; }
    if (d.kind !== 'decision') { root.appendChild(RB.el('p', { text: RB.eventText(d) })); return; }
    root.appendChild(RB.el('h3', { text: 'Request ' + d.n + ' at tick ' + d.tick + ' (' + RB.fmtClock(d.t_s) + ')' }));
    root.appendChild(RB.el('p', {}, ['Chosen: ', RB.el('b', { text: RB.fmtAction(d.choice) }),
                                       d.applied_tick === null || d.applied_tick === undefined ? '  Not applied yet.' : '  Applied at tick ' + d.applied_tick + (d.log_line ? ': ' + d.log_line + '.' : ' with no effect on the desk.')]));
    if (onWhatIf) { root.appendChild(RB.el('button', { class: 'btn', text: 'What if, from this state...', onclick: function () { onWhatIf(d.i); } })); }
    (d.calls || []).forEach(function (call) {
      const sec = RB.el('section', { class: 'call' }, [RB.el('h4', { text: (CALL_TITLES[call.key] || call.key) + '  (call: ' + call.key + ')' })]);
      sec.appendChild(RB.el('details', {}, [RB.el('summary', { text: 'The text the model read (' + call.state_text.length + ' characters)' }), RB.el('pre', { class: 'state', text: call.state_text })]));
      Object.keys(call.answers).forEach(function (n) { sec.appendChild(answerBlock(n, call.answers[n])); });
      root.appendChild(sec);
    });
  };

  RB.eventText = function (e) {
    if (e.event === 'scenario_event') { return 'Scenario: ' + e.scenario_kind + (e.value !== undefined ? ' (' + e.value + ')' : ''); }
    if (e.event === 'protection_trip') { return 'Protection trip: ' + e.protection; }
    if (e.event === 'protection_switch') { return 'Protection ' + e.protection + ' switched ' + e.state; }
    if (e.event === 'request_ignored') { return 'A request had no effect: ' + e.reason; }
    return e.event;
  };

  RB.renderLog = function (root, run, onPick) {
    root.replaceChildren();
    const rows = (run && run.rows || []).filter(Boolean);
    if (!rows.length) { root.appendChild(RB.el('p', { class: 'muted', text: run && run.live ? 'Waiting for the first decision...' : 'No decisions.' })); return; }
    rows.forEach(function (d) {
      const row = RB.el('div', { class: 'drow ' + d.kind, 'data-i': d.i, 'data-tick': d.kind === 'decision' ? RB.appliedTick(d) : d.tick, 'data-snap': d.tick, onclick: function () { onPick(d); } });
      if (d.kind !== 'decision') {
        row.appendChild(RB.el('div', { class: 'dhead' }, [RB.el('span', { class: 'dtick', text: 'tick ' + d.tick }), RB.el('span', { class: 'dclock muted', text: RB.fmtClock(d.t_s) }),
                                                          RB.el('span', { class: 'act', text: RB.eventText(d) })]));
        root.appendChild(row);
        return;
      }
      const cls = d.answers && d.answers.class ? d.answers.class : null;
      const top = cls ? cls.scores[cls.choice] : null, risk = d.answers && d.answers.risk ? d.answers.risk.expected : null;
      row.appendChild(RB.el('div', { class: 'dhead' }, [
        RB.el('span', { class: 'dtick', text: '#' + d.n + '  tick ' + d.tick }), RB.el('span', { class: 'dclock muted', text: RB.fmtClock(d.t_s) }),
        RB.el('span', { class: 'act', text: RB.fmtAction(d.choice) }),
        top !== null ? RB.el('span', { class: 'conf', text: 'probability of the top option ' + top.toFixed(2) }) : null,
        risk !== null && risk !== undefined ? RB.el('span', { class: 'conf', text: 'risk rating ' + risk.toFixed(1) }) : null,
        d.latency_ms ? RB.el('span', { class: 'muted', text: d.latency_ms.toFixed(0) + ' ms' }) : null]));
      if (RB.appliedTick(d) !== NOT_YET) {
        row.appendChild(RB.el('div', { class: 'delay', text: 'The model saw tick ' + d.tick + ' and its action took effect at tick ' + d.applied_tick +
          ' (' + (d.applied_tick - d.tick) + ' ticks later, ' + (d.delay_s || 0).toFixed(1) + ' plant seconds after it was read).' }));
        row.appendChild(RB.el('div', { class: 'logline', text: d.log_line ? 'Desk: ' + d.log_line : 'No change on the desk.' }));
      } else {
        row.appendChild(RB.el('div', { class: 'delay', text: 'The answer has not taken effect yet.' }));
      }
      root.appendChild(row);
    });
  };

  /* Mark the row whose action took effect last at or before ``tick``; scroll it into view when asked. */
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
