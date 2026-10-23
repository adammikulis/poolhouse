// Canvas painters for the instrument faces, shared by the 3D room (as textures) and the 2D views (as overlays).
export function canvas(w, h) {
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  return c;
}

const COLORS = { red: '#d8362c', amber: '#e8a317', green: '#35b55a' };
export const lampColor = (name) => COLORS[name] || '#cccccc';

export const DIAL_SIZE = [320, 320];
export const TILE_SIZE = [256, 128];
export const RECORDER_SIZE = [440, 260];
export const MIMIC_SIZE = [128, 160];

// The scale starts at the lower left and sweeps 270 degrees clockwise over the top, as the needle does.
const DIAL_START = (135 * Math.PI) / 180;
const DIAL_SWEEP = (270 * Math.PI) / 180;

export function dialAngle(ins, v) {
  return DIAL_START + ((v - ins.min) / (ins.max - ins.min)) * DIAL_SWEEP;
}

export function paintDialFace(ins) {
  const c = canvas(...DIAL_SIZE);
  const g = c.getContext('2d');
  g.fillStyle = '#ecebe2';
  g.beginPath();
  g.arc(160, 160, 150, 0, Math.PI * 2);
  g.fill();
  const pos = (v, r) => {
    const a = dialAngle(ins, v);
    return [160 + r * Math.cos(a), 160 + r * Math.sin(a)];
  };
  if (ins.red) {
    g.strokeStyle = '#c83228';
    g.lineWidth = 9;
    const ranges = Array.isArray(ins.red[0]) ? ins.red : [ins.red];
    for (const [lo, hi] of ranges) {
      g.beginPath();
      g.arc(160, 160, 128, dialAngle(ins, lo), dialAngle(ins, hi));
      g.stroke();
    }
  }
  g.strokeStyle = '#1b1d1e';
  g.fillStyle = '#1b1d1e';
  g.font = 'bold 28px sans-serif';
  g.textAlign = 'center';
  g.textBaseline = 'middle';
  for (let i = 0; i <= 10; i++) {
    const v = ins.min + ((ins.max - ins.min) * i) / 10;
    const [x1, y1] = pos(v, i % 5 === 0 ? 108 : 118);
    const [x2, y2] = pos(v, 136);
    g.lineWidth = i % 5 === 0 ? 4 : 2;
    g.beginPath();
    g.moveTo(x1, y1);
    g.lineTo(x2, y2);
    g.stroke();
  }
  const fmt = (v) => (Math.abs(v) >= 100 || Number.isInteger(v) ? String(Math.round(v)) : v.toFixed(1));
  for (const f of [0, 0.5, 1]) {
    const v = ins.min + (ins.max - ins.min) * f;
    const [x, y] = pos(v, 90);
    g.fillText(fmt(v), x, y);
  }
  g.font = 'bold 28px sans-serif';
  g.fillText(ins.label, 160, 190, 240);
  g.font = 'bold 24px sans-serif';
  g.fillText(ins.unit, 160, 252, 90);
  return c;
}

export function paintTileFace(ins, lit) {
  const c = canvas(...TILE_SIZE);
  const g = c.getContext('2d');
  const col = lampColor(ins.color);
  g.fillStyle = lit ? col : '#2a2723';
  g.fillRect(0, 0, 256, 128);
  g.strokeStyle = lit ? '#fff6' : '#0006';
  g.lineWidth = 6;
  g.strokeRect(3, 3, 250, 122);
  g.fillStyle = lit ? '#101010' : '#8c8478';
  g.textAlign = 'center';
  g.textBaseline = 'middle';
  const words = ins.label.split(' ');
  const half = Math.ceil(words.length / 2);
  const lines = words.length > 2 ? [words.slice(0, half).join(' '), words.slice(half).join(' ')] : [ins.label];
  g.font = `bold ${lines.length > 1 || ins.label.length > 9 ? 34 : 42}px sans-serif`;
  lines.forEach((t, i) => g.fillText(t, 128, 64 + (i - (lines.length - 1) / 2) * 40, 236));
  return c;
}

export function paintRecorderFace(ins, rows) {
  const [w, h] = RECORDER_SIZE;
  const c = canvas(w, h);
  const g = c.getContext('2d');
  g.fillStyle = '#0d1a1a';
  g.fillRect(0, 0, w, h);
  g.strokeStyle = '#2b4a4a';
  g.lineWidth = 1;
  for (let i = 1; i < 6; i++) {
    g.beginPath();
    g.moveTo(0, (i * h) / 6);
    g.lineTo(w, (i * h) / 6);
    g.stroke();
  }
  g.font = 'bold 17px sans-serif';
  g.fillStyle = '#cfe8e8';
  g.fillText(`${ins.label}  (${ins.min}-${ins.max} ${ins.unit})`, 8, 20, 424);
  if (!rows.length) {
    g.fillText('NO DATA', 170, 140);
    return c;
  }
  ins.series.forEach(([field, mul, color], k) => {
    g.strokeStyle = color;
    g.lineWidth = 2.5;
    g.beginPath();
    rows.forEach((r, i) => {
      const v = r[field];
      if (v === null || v === undefined) return;
      const x = rows.length > 1 ? 8 + (424 * i) / (rows.length - 1) : 8;
      const y = 250 - Math.min(Math.max((v * mul - ins.min) / (ins.max - ins.min), 0), 1) * 190;
      if (i === 0) g.moveTo(x, y);
      else g.lineTo(x, y);
    });
    g.stroke();
    g.fillStyle = color;
    g.font = 'bold 16px sans-serif';
    g.fillText(field, 8 + k * 90, 44);
  });
  return c;
}

export function paintMimicFace(variant) {
  const c = canvas(...MIMIC_SIZE);
  const g = c.getContext('2d');
  g.fillStyle = '#12302f';
  g.fillRect(0, 0, 128, 160);
  g.strokeStyle = '#8fd1b4';
  g.fillStyle = '#8fd1b4';
  g.lineWidth = 3;
  let seed = 31 + variant * 17;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  for (let i = 0; i < 7; i++) {
    const x = 12 + Math.floor(rnd() * 100), y = 12 + Math.floor(rnd() * 130);
    g.strokeRect(x - 7, y - 6, 14, 12);
    g.beginPath();
    g.moveTo(x + 7, y);
    g.lineTo(x + 7 + 10 + rnd() * 30, y);
    g.lineTo(x + 7 + 10 + rnd() * 30, y + 8 + rnd() * 40);
    g.stroke();
  }
  g.strokeStyle = '#243f3e';
  g.lineWidth = 2;
  for (let i = 0; i < 4; i++) g.strokeRect(0, i * 40, 128, 40);
  return c;
}
