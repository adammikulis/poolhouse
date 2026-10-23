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
