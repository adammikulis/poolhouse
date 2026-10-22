// The two screens: the decision display (the model's answers as probability bars) and the ORM terminal.
const BG = '#061214';
const FG = '#b7f5e4';
const DIM = '#5f8a82';
const COL_W = 490;

function wrap(g, text, x, y, width, lineH, maxLines) {
  const words = String(text).split(' ');
  let line = '';
  let n = 0;
  for (const w of words) {
    const t = line ? `${line} ${w}` : w;
    if (g.measureText(t).width > width && line) {
      g.fillText(line, x, y + n * lineH);
      n += 1;
      line = w;
      if (n >= maxLines) return;
    } else line = t;
  }
  if (line && n < maxLines) g.fillText(line, x, y + n * lineH);
}

function bar(g, x, y, w, h, p, color, label, right) {
  g.fillStyle = '#12302c';
  g.fillRect(x, y, w, h);
  g.fillStyle = color;
  g.fillRect(x, y, Math.max(0, Math.min(1, p)) * w, h);
  g.fillStyle = FG;
  g.textBaseline = 'middle';
  g.textAlign = 'left';
  g.fillText(label, x + 6, y + h / 2, w * 0.7);
  g.textAlign = 'right';
  g.fillText(right, x + w - 6, y + h / 2);
  g.textAlign = 'left';
  g.textBaseline = 'alphabetic';
}

function banner(g, text, color) {
  g.fillStyle = color;
  g.font = 'bold 40px sans-serif';
  g.textAlign = 'center';
  g.fillText(text, 512, 300);
  g.textAlign = 'left';
}

// Draws one answer; returns the height used.
function answer(g, name, a, x, y) {
  g.font = '17px sans-serif';
  g.fillStyle = a.abstained ? '#e8c870' : DIM;
  const q = `${name}: ${a.question}${a.abstained ? '  (abstained)' : ''}`;
  g.fillText(q.length > 70 ? `${q.slice(0, 69)}...` : q, x, y + 13);
  const keys = Object.keys(a.scores);
  const pct = (p) => `${(p * 100).toFixed(1)} %`;
  g.font = '17px sans-serif';
  if (keys.length === 2 && 'yes' in a.scores) {
    bar(g, x, y + 20, COL_W, 28, a.scores.yes, '#2fa58a', 'P(yes)', pct(a.scores.yes));
    return 58;
  }
  if (keys.every((k) => /^\d+$/.test(k))) {
    const w = COL_W / keys.length;
    keys.forEach((k, i) => {
      const p = a.scores[k];
      g.fillStyle = '#12302c';
      g.fillRect(x + i * w + 2, y + 22, w - 4, 40);
      g.fillStyle = k === a.choice ? '#e0b030' : '#2f6f9a';
      g.fillRect(x + i * w + 2, y + 22 + 40 * (1 - p), w - 4, 40 * p);
      g.fillStyle = FG;
      g.textAlign = 'center';
      g.fillText(`${k}: ${(p * 100).toFixed(0)}%`, x + i * w + w / 2, y + 78);
    });
    g.textAlign = 'left';
    return 88;
  }
  const top = Object.entries(a.scores).sort((p, q) => q[1] - p[1]).slice(0, 3);
  if (!top.some(([k]) => k === a.choice)) top[2] = [a.choice, a.scores[a.choice]];
  top.forEach(([k, p], i) => bar(g, x, y + 20 + i * 31, COL_W, 28, p, k === a.choice ? '#e0b030' : '#2f6f9a', k, pct(p)));
  return 20 + top.length * 31 + 8;
}

function answers(g, dec) {
  const col = [66, 66];
  for (const [name, a] of Object.entries(dec.answers)) {
    const k = col[0] <= col[1] ? 0 : 1;
    col[k] += answer(g, name, a, 20 + k * (COL_W + 24), col[k]);
  }
}

const act = (a) => (a ? `${a.cls}${a.target && a.target !== 'none' ? ` ${a.target}` : ''}${a.cls.includes('rod') ? ` step ${a.step}` : ''}` : 'none');

function footer(g, dec) {
  g.font = 'bold 20px sans-serif';
  g.fillStyle = FG;
  g.fillText(`requested: ${act(dec.requested)}`, 20, 448);
  g.fillText(`applied: ${act(dec.applied)}     source: ${dec.source}`, 20, 474);
  g.font = '16px sans-serif';
  g.fillStyle = DIM;
  const il = (dec.interlocks || []).map((i) => i.id).join(', ');
  const fl = (dec.flags || []).map((i) => i.id).join(', ');
  g.fillText(`${il ? `interlocks: ${il}` : 'no interlock fired'}${fl ? `   flags: ${fl}` : ''}`, 520, 448, 484);
  if (dec.note) wrap(g, dec.note, 520, 472, 484, 20, 2);
}

function events(g, feed) {
  g.font = '16px sans-serif';
  g.fillStyle = '#e8c870';
  const ev = feed.events.slice(-3);
  g.fillText(ev.length ? 'EVENTS' : 'no scenario event yet', 20, 506);
  ev.forEach((e, i) => wrap(g, `tick ${e.tick}: ${e.text}`, 20, 528 + i * 22, 984, 20, 1));
}

export function paintDecision(c, feed) {
  const g = c.getContext('2d');
  g.fillStyle = BG;
  g.fillRect(0, 0, 1024, 600);
  g.font = 'bold 24px sans-serif';
  g.textBaseline = 'alphabetic';
  g.fillStyle = FG;
  if (feed.status !== 'ok') {
    g.fillText('DECIDER', 20, 40);
    return banner(g, 'NO RUN: NO SIGNAL', '#e0574a');
  }
  const d = feed.data;
  const dec = feed.modelDecision;
  const model = feed.modelId || `none (controller: ${d.controller || 'unknown'})`;
  g.fillText(`DECIDER   model ${model}`, 20, 36);
  g.font = '16px sans-serif';
  g.fillStyle = DIM;
  g.fillText(`${d.scenario || ''}   tick ${feed.row.tick}   plant time ${feed.row.t_s} s`, 20, 58);
  if (!dec) {
    const any = feed.decision;
    if (!any) return banner(g, 'WAITING FOR THE FIRST DECISION', '#e8c870');
    g.fillStyle = '#e8c870';
    g.font = 'bold 30px sans-serif';
    g.fillText('NO MODEL ANSWERS IN THIS RUN', 20, 200);
    g.font = '22px sans-serif';
    g.fillStyle = FG;
    g.fillText(`The controller is "${d.controller}". Source of the last action: ${any.source}.`, 20, 250);
    g.fillText(`requested: ${act(any.requested)}   applied: ${act(any.applied)}`, 20, 290);
    if (any.note) wrap(g, any.note, 20, 330, 984, 26, 3);
    events(g, feed);
    return null;
  }
  answers(g, dec);
  footer(g, dec);
  events(g, feed);
  g.fillStyle = DIM;
  g.font = '14px sans-serif';
  g.fillText(`decision #${dec.i}  tick ${dec.tick}  model latency ${dec.latency_ms} ms`, 20, 594);
  return null;
}

export function paintOrm(c, row) {
  const g = c.getContext('2d');
  g.fillStyle = '#031208';
  g.fillRect(0, 0, c.width, c.height);
  g.fillStyle = '#5cf08a';
  g.font = 'bold 22px monospace';
  g.textBaseline = 'alphabetic';
  g.fillText('ORM  REACTIVITY MARGIN', 10, 30);
  if (!row) {
    g.font = 'bold 26px monospace';
    g.fillText('NO RUN', 100, 120);
    return;
  }
  g.font = '19px monospace';
  g.fillText(`PRINTED     ${row.orm_printed.toFixed(1)} R.E.`, 10, 66);
  g.fillText(`TRUE        ${row.orm.toFixed(1)} R.E.`, 10, 92);
  g.fillText('MINIMUM     15.0', 10, 118);
  g.fillText(`SCRAM       ${row.scram ? 'ACTIVE' : 'no'}`, 10, 144);
  g.strokeStyle = '#5cf08a';
  g.strokeRect(10, 164, 280, 22);
  g.fillRect(10, 164, 280 * Math.min(Math.max(row.orm / 30, 0), 1), 22);
  g.fillStyle = '#ff6a5a';
  g.fillRect(10 + 140 - 1, 160, 3, 30);
  if (row.orm < 15) {
    g.font = 'bold 20px monospace';
    g.fillText('BELOW MINIMUM', 70, 214);
  }
}
