/* The console readings: what the decider was shown at the decision in force, beside the plant's true value at the playhead. */
window.RB = window.RB || {};
(function (RB) {
  'use strict';

  /* name, label, unit, digits, the true value for comparison */
  const READINGS = [
    ['chA', 'Power channel A', '%', 1, (r) => r.P * 100], ['chB', 'Power channel B', '%', 1, (r) => r.P * 100],
    ['heat', 'Heat-balance power', '%', 0, (r) => r.P * 100], ['flow', 'Core flow meters', '%', 0, (r) => r.f * 100],
    ['press_l', 'Left drum pressure', 'MPa', 2, (r) => r.p], ['press_r', 'Right drum pressure', 'MPa', 2, (r) => r.p],
    ['header', 'Steam header', 'MPa', 2, null], ['level_l', 'Left drum level', 'cm', 0, (r) => r.level], ['level_r', 'Right drum level', 'cm', 0, (r) => r.level],
    ['feedwater', 'Feedwater flow', '%', 0, null]];

  class Panel {
    constructor(root) { this.root = root; this.key = ''; }

    update(r, run) {
      const d = RB.decisionAt(run, r);
      const key = r ? r.tick + ':' + (d ? d.i : -1) : '';
      if (key === this.key) { return; }
      this.key = key;
      this.root.replaceChildren();
      if (!r || !run) { return; }
      if (!d) { this.root.appendChild(RB.el('p', { class: 'muted', text: 'No decision has been asked yet, so no console has been shown to the decider.' })); return; }
      const v = d.view, tiles = [];
      tiles.push(RB.el('p', { class: 'small muted', text: 'The console as the decider read it at tick ' + d.tick + ' (request ' + d.n + '). Plant values are the truth at the playhead, which the decider is not shown.' }));
      READINGS.forEach(function (x) {
        const rd = v.readings[x[0]];
        if (!rd) { return; }
        tiles.push(RB.el('div', { class: 'tile' }, [RB.el('div', { class: 'tlabel', text: x[1] }), RB.el('div', { class: 'val', text: rd.value.toFixed(x[3]) + ' ' + x[2] }),
          RB.el('div', { class: 'actual', text: x[4] ? 'plant now ' + x[4](r).toFixed(x[3]) + ' ' + x[2] + ', sample ' + rd.age_s.toFixed(0) + ' s old' : 'sample ' + rd.age_s.toFixed(0) + ' s old' })]));
      });
      tiles.push(RB.el('div', { class: 'tile' }, [RB.el('div', { class: 'tlabel', text: 'Reactivity margin printout' }), RB.el('div', { class: 'val', text: v.orm_printout.toFixed(1) + ' rod-eq' }),
        RB.el('div', { class: 'actual', text: 'plant now ' + r.orm.toFixed(1) + ' rod-eq, printed ' + (v.orm_age_s / 60).toFixed(0) + ' min ago' })]));
      const grid = RB.el('div', { class: 'tiles' }, tiles.slice(1));
      this.root.appendChild(tiles[0]); this.root.appendChild(grid);
      const chip = (text) => RB.el('span', { class: 'chip', text: text });
      const strip = RB.el('div', { class: 'status-strip' });
      v.pumps.forEach(function (p) { strip.appendChild(chip('P' + p.n + ' ' + (p.running ? p.speed_pct + '%' : 'stopped'))); });
      Object.keys(v.switches).forEach(function (k) { strip.appendChild(chip(k.replace('_', ' ') + ' ' + (v.switches[k] ? 'on' : 'off'))); });
      strip.appendChild(chip('regulator ' + (v.regulator_on ? 'on' : 'off') + ', demand ' + v.demand_pct.toFixed(0) + '%'));
      strip.appendChild(chip('turbine test: ' + v.turbine_test));
      if (v.az5) { strip.appendChild(chip('AZ-5 pressed')); }
      this.root.appendChild(strip);
      if (v.log.length) { this.root.appendChild(RB.el('pre', { class: 'state', text: 'Desk log:\n' + v.log.join('\n') })); }
    }
  }
  RB.Panel = Panel;

  /* The decision whose console was read most recently at or before the frame's tick. */
  RB.decisionAt = function (run, frame) {
    if (!run || !frame) { return null; }
    let d = null;
    run.decisions.forEach(function (x) { if (x && x.tick <= frame.tick) { d = x; } });
    return d;
  };
})(window.RB);
