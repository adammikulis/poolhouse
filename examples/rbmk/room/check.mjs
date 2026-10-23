// Prints what every lamp, tile and dial shows for each row of a feed: node check.mjs <instruments.json> <feed.json>
import { readFileSync } from 'node:fs';
import { evalRule, dialValue } from './rules.js';
import { ROD_ORDER } from './rods.js';

const [specPath, feedPath] = process.argv.slice(2);
const spec = JSON.parse(readFileSync(specPath, 'utf8'));
const feed = JSON.parse(readFileSync(feedPath, 'utf8'));
for (const r of feed.trace) r.rods_mean = Array.isArray(r.rods) && r.rods.length ? r.rods.reduce((a, b) => a + b, 0) / r.rods.length : null;
const rows = feed.trace.map((row) => {
  let dec = null;
  for (const d of feed.decisions) {
    if (d.tick <= row.tick) dec = d;
    else break;
  }
  const lit = {};
  const dials = {};
  for (const ins of spec.instruments) {
    if (ins.type === 'lamp' || ins.type === 'ann') lit[ins.id] = evalRule(ins.rule, row, dec);
    if (ins.type === 'dial') dials[ins.id] = dialValue(ins, row);
  }
  return { tick: row.tick, lit, dials };
});
process.stdout.write(JSON.stringify({ rows, rodOrder: ROD_ORDER }));
