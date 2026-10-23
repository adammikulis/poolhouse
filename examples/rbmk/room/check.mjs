// Prints what every lamp, tile and dial shows for each row of a feed: node check.mjs <instruments.json> <feed.json>
import { readFileSync } from 'node:fs';
import { evalRule, dialValue, crewRow } from './rules.js';
import { ROD_ORDER } from './rods.js';

const [specPath, feedPath] = process.argv.slice(2);
const spec = JSON.parse(readFileSync(specPath, 'utf8'));
const feed = JSON.parse(readFileSync(feedPath, 'utf8'));
const rows = feed.trace.map((src) => {
  let dec = null;
  for (const d of feed.decisions) {
    if (d.tick <= src.tick) dec = d;
    else break;
  }
  const risk = dec && dec.answers && dec.answers.risk ? dec.answers.risk.expected : null;
  const row = crewRow(src, risk);
  const lit = {};
  const dials = {};
  for (const ins of spec.instruments) {
    if (ins.type === 'lamp' || ins.type === 'ann') lit[ins.id] = evalRule(ins.rule, row, dec);
    if (ins.type === 'dial') dials[ins.id] = dialValue(ins, row);
  }
  return { tick: row.tick, lit, dials };
});
const fields = feed.trace.length ? Object.keys(crewRow(feed.trace[0], null)) : [];
process.stdout.write(JSON.stringify({ rows, rodOrder: ROD_ORDER, fields }));
