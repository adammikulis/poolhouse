// What every instrument shows for the feed row at the playhead: dial values, lit lamps and tiles, rod positions, panel alerts.
import { evalRule, dialValue } from './rules.js';
import { ROD_ORDER } from './rods.js';

const RANK = { red: 2, amber: 1 };

function inRed(ins, v) {
  if (!ins.red || v === null) return false;
  const ranges = Array.isArray(ins.red[0]) ? ins.red : [ins.red];
  return ranges.some(([lo, hi]) => v >= lo && v <= hi);
}

export class RoomState {
  constructor(spec, feed) {
    this.spec = spec;
    this.feed = feed;
    this.nodes = new Map(spec.instruments.map((i) => [i.id, {}]));
    this.rods = ROD_ORDER.map((id) => ({ id, value: undefined }));
    this.rodIndex = new Map(ROD_ORDER.map((id, i) => [id, i]));
  }

  refresh() {
    const f = this.feed;
    const row = f.status === 'ok' ? f.row : null;
    const dec = f.status === 'ok' ? f.decision : null;
    for (const ins of this.spec.instruments) {
      const n = this.nodes.get(ins.id);
      if (ins.type === 'dial') n.value = dialValue(ins, row);
      else if (ins.type === 'ann' || ins.type === 'lamp') n.lit = !!row && evalRule(ins.rule, row, dec);
      else if (ins.type === 'key') n.lit = f.desk.lit(ins.id);
      else if (ins.type === 'button') n.lit = !!row && row.az5 === true;
    }
    const rods = row && Array.isArray(row.rods) && row.rods.length === ROD_ORDER.length ? row.rods : null;
    this.rods.forEach((r, i) => { r.value = rods ? rods[i] / 100 : undefined; });
  }

  // 0 quiet, 1 amber, 2 red: the worst thing lit or out of range in a panel.
  alert(panel) {
    const f = this.feed;
    if (f.status !== 'ok') return 0;
    const ids = [...(panel.ids || []), ...(panel.alert_ids || [])];
    let worst = 0;
    for (const ins of this.spec.instruments) {
      if (!(ins.zone === panel.zone || ids.includes(ins.id))) continue;
      const n = this.nodes.get(ins.id);
      if ((ins.type === 'ann' || ins.type === 'lamp') && n.lit && ins.color !== 'green') worst = Math.max(worst, RANK[ins.color] || 0);
      if (ins.type === 'dial' && inRed(ins, n.value)) worst = Math.max(worst, 2);
      if (ins.type === 'button' && n.lit) worst = 2;
      if (ins.id === 'SCREEN_ORM' && f.row.orm_printout < 15) worst = 2;
    }
    return worst;
  }
}
