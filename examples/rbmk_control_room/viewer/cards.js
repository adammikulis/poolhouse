// Instrument cards: what a clicked object is, its range, how the demo models it, and its value now.
const STATIC = {
  desk_top: ['Control desk top', 'The sloped working surface of the long, angled control desk, in sections for the reactor, turbine and electrical systems.', 'Static geometry.'],
  desk_side: ['Control desk body', 'The cream desk body under the blue-grey top.', 'Static geometry.'],
  desk_kick: ['Desk kick plate', 'Dark recessed base of the desk.', 'Static geometry.'],
  key_white: ['Desk key or furniture', 'A key cap of the rod-control key matrix, or a white writing desk. Not wired to the demo.', 'Static decor: no state.'],
  key_yellow: ['Rod key cap (yellow)', 'One of the push-button caps in the round rod-control key matrix. Not wired to the demo.', 'Static decor: no state.'],
  key_green: ['Rod key cap (green)', 'One of the push-button caps in the round rod-control key matrix. Not wired to the demo.', 'Static decor: no state.'],
  key_red: ['Rod key cap (red)', 'One of the push-button caps in the round rod-control key matrix. Not wired to the demo.', 'Static decor: no state.'],
  switch: ['Switch or knob', 'A static toggle or selector switch. The demo has no model of operator switches.', 'Static decor: no state.'],
  chair: ['Swivel chair', 'An operator chair on a five-star base.', 'Static decor.'],
  paint: ['Instrument wall', 'The faceted, grey-green painted wall of instrument panels. The painted layout is interpretive.', 'Static geometry.'],
  paint_dark: ['Panel frame', 'Dark panel frame, cornice or label strip.', 'Static geometry.'],
  plate: ['Mounting plate', 'The dark mounting plate behind a group of instruments.', 'Static geometry.'],
  case: ['Instrument case', 'The housing of a dial, tile or recorder.', 'Static geometry.'],
  FLOOR: ['Floor', 'Irregular light stone tiling. Click the floor to walk there.', 'Static geometry.'],
  CEILING: ['Ceiling', 'Suspended panel ceiling with fluorescent light panels.', 'Static geometry.'],
  hall_glow: ['Window to the turbine hall', 'The glazed band that looks onto the turbine hall. The hall is a schematic backdrop.', 'Static geometry.'],
  wall: ['Room wall', 'A plain room wall.', 'Static geometry.'],
  wood: ['Door', 'A panelled door.', 'Static geometry.'],
};

const fmt = (v, d = 2) => (typeof v === 'number' ? v.toFixed(d) : String(v));

export function instrumentOf(object) {
  for (let o = object; o; o = o.parent) {
    const m = /^INS_(.+?)(__.+)?$/.exec(o.name);
    if (m) return m[1];
    if (/^ROD_R-\d\d-\d\d$/.test(o.name)) return o.name;
    if (/^MIMIC_\d+$/.test(o.name)) return 'MIMIC';
    if (/^STATIC_/.test(o.name)) return o.name.slice(7);
    if (o.name === 'FLOOR' || o.name === 'CEILING') return o.name;
  }
  return null;
}

function now(ins, driver, feed) {
  const row = feed.status === 'ok' ? feed.row : null;
  if (!row) return 'No feed: this instrument shows nothing.';
  const n = driver.nodes.get(ins.id) || {};
  switch (ins.type) {
    case 'dial': return n.value === null || n.value === undefined ? 'No value in this feed row; the needle rests.' : `${fmt(n.value)} ${ins.unit}`;
    case 'lamp': case 'ann': return n.lit ? `LIT: ${ins.text}.` : `Dark: ${ins.text} is not true now.`;
    case 'recorder': return ins.series.map(([f]) => `${f} ${fmt(row[f], 3)}`).join(', ');
    case 'button': return row.scram ? 'Shutdown ACTIVE: guard raised, button pressed.' : 'Not pressed: guard down.';
    case 'screen': return 'Shows the feed as it is now.';
    default: return '';
  }
}

export function describe(key, spec, driver, feed) {
  const byId = new Map(spec.instruments.map((i) => [i.id, i]));
  if (byId.has(key)) {
    const ins = byId.get(key);
    const isRule = ins.type === 'lamp' || ins.type === 'ann';
    return {
      title: ins.label, tag: ins.id,
      what: ins.what,
      range: ins.range || (isRule ? `Lit when ${ins.text}.` : ''),
      modelled: ins.modelled || `Evaluated in the viewer on each feed row (${JSON.stringify(ins.rule)}), a limit the demo itself enforces.`,
      now: now(ins, driver, feed),
    };
  }
  const rod = /^ROD_(R-\d\d-\d\d)$/.exec(key);
  if (rod) {
    const r = driver.rods.find((x) => x.id === rod[1]);
    const rodmap = byId.get('RODMAP');
    const v = r && r.value !== undefined ? `${(r.value * 100).toFixed(0)} % inserted` : 'No position in this feed row.';
    const kind = feed.status === 'ok' && feed.data.meta.rods ? (feed.data.meta.rods.find((x) => x.id === rod[1]) || {}).kind : '';
    return { title: `Rod ${rod[1]}${kind ? ` (${kind})` : ''}`, tag: key, what: `One control-rod position cell of the 211-rod map${kind ? `; this rod is of kind ${kind}` : ''}.`, range: rodmap.range,
      modelled: 'rods[i] of the feed row: the demo\'s own position of this rod, in percent inserted.', now: v };
  }
  if (key === 'MIMIC') {
    return { title: 'Mimic diagram window', tag: 'static', what: 'A glazed window with an etched mimic diagram of a plant system.', range: '', modelled: 'Static decor: a schematic, not driven by the demo.', now: '' };
  }
  const s = STATIC[key];
  if (s) return { title: s[0], tag: 'static', what: s[1], range: '', modelled: s[2], now: '' };
  return { title: 'Room geometry', tag: 'static', what: 'Part of the room.', range: '', modelled: 'Static geometry.', now: '' };
}
