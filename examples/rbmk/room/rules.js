// Instrument rules: when a lamp or tile is lit and what a dial reads, as pure functions of a feed row.
const CMP = {
  '<': (a, b) => a < b, '>': (a, b) => a > b, '>=': (a, b) => a >= b, '<=': (a, b) => a <= b,
};

export function evalRule(rule, row, decision) {
  switch (rule.when) {
    case 'trace': {
      const v = row[rule.field];
      if (v === null || v === undefined) return false;
      return 'eq' in rule ? v === rule.eq : CMP[rule.op](v, rule.value);
    }
    case 'diff': return CMP[rule.op](Math.abs(row[rule.a] - row[rule.b]), rule.value);
    case 'diffrow': return CMP[rule.op](row[rule.a] - row[rule.b], rule.value);
    case 'all': return rule.of.every((r) => evalRule(r, row, decision));
    case 'any': return rule.of.some((r) => evalRule(r, row, decision));
    case 'index': return Array.isArray(row[rule.field]) && CMP[rule.op](row[rule.field][rule.index], rule.value);
    case 'prot': return !!row.prot && row.prot[rule.id] === rule.eq;
    case 'anyprot': return !!row.prot && Object.values(row.prot).some((on) => on === false);
    case 'decision': return !!decision && decision[rule.field] === rule.eq;
    default: throw new Error(`unknown rule kind ${rule.when}`);
  }
}

export function dialValue(ins, row) {
  const v = row ? row[ins.field] : null;
  return v === null || v === undefined ? null : v * ins.scale;
}

// The crew row of a trace row: the readings and desk state the crew had at that tick, with the rod indicators (the row's own rods) and the decider's risk rating.
export function crewRow(src, risk) {
  const c = src.crew;
  const rods = src.rods;
  const mean = Array.isArray(rods) && rods.length ? rods.reduce((a, b) => a + b, 0) / rods.length : null;
  return { tick: src.tick, t_s: src.t_s, ...c.readings, demand_pct: c.demand_pct, orm_printout: c.orm_printout, orm_age_s: c.orm_age_s, rods, rods_mean: mean,
    pumps: c.pumps.map((v) => v / 100), ar_on: c.regulator_on, regulator_setpoint_pct: c.regulator_setpoint_pct, prot: c.switches, test: c.turbine_test, az5: c.az5, model_risk: risk };
}
