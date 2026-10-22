// Canvas painters for the faces the GLB leaves blank: dials, tiles, recorders, screens, floor, ceiling, mimic windows.
import * as THREE from 'three';

export function canvas(w, h) {
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  return c;
}

export function texture(c, repeat = false) {
  const t = new THREE.CanvasTexture(c);
  t.flipY = false;
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 8;
  if (repeat) t.wrapS = t.wrapT = THREE.RepeatWrapping;
  return t;
}

const COLORS = { red: '#d8362c', amber: '#e8a317', green: '#35b55a' };
export const lampColor = (name) => COLORS[name] || '#cccccc';

export function paintDial(ins) {
  const c = canvas(320, 320);
  const g = c.getContext('2d');
  g.clearRect(0, 0, 320, 320);
  g.fillStyle = '#ecebe2';
  g.beginPath();
  g.arc(160, 160, 150, 0, Math.PI * 2);
  g.fill();
  const a0 = (135 + 90) * Math.PI / 180;  // canvas angle of the minimum: lower left
  const sweep = 270 * Math.PI / 180;
  const pos = (v, r) => {
    const f = (v - ins.min) / (ins.max - ins.min);
    const a = a0 + f * sweep;
    return [160 + r * Math.cos(a), 160 + r * Math.sin(a)];
  };
  if (ins.red) {
    g.strokeStyle = '#c83228';
    g.lineWidth = 9;
    const fa = (v) => a0 + ((v - ins.min) / (ins.max - ins.min)) * sweep;
    const ranges = Array.isArray(ins.red[0]) ? ins.red : [ins.red];
    for (const [lo, hi] of ranges) {
      g.beginPath();
      g.arc(160, 160, 128, fa(lo), fa(hi));
      g.stroke();
    }
  }
  g.strokeStyle = '#1b1d1e';
  g.fillStyle = '#1b1d1e';
  g.font = 'bold 28px sans-serif';
  g.textAlign = 'center';
  g.textBaseline = 'middle';
  for (let i = 0; i <= 10; i++) {
    const v = ins.min + (ins.max - ins.min) * i / 10;
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
    const [x, y] = pos(v, 88);
    g.fillText(fmt(v), x, y);
  }
  g.font = 'bold 30px sans-serif';
  g.fillText(ins.label, 160, 214, 250);
  g.font = 'bold 26px sans-serif';
  g.fillText(ins.unit, 160, 252);
  return texture(c);
}

export function paintTile(ins, lit) {
  const c = canvas(256, 128);
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
  const lines = words.length > 2 ? [words.slice(0, Math.ceil(words.length / 2)).join(' '), words.slice(Math.ceil(words.length / 2)).join(' ')] : [ins.label];
  g.font = `bold ${lines.length > 1 || ins.label.length > 9 ? 34 : 42}px sans-serif`;
  lines.forEach((t, i) => g.fillText(t, 128, 64 + (i - (lines.length - 1) / 2) * 40, 236));
  return texture(c);
}

export function paintRecorder(ins, rows, scale) {
  const c = canvas(440, 260);
  const g = c.getContext('2d');
  g.fillStyle = '#0d1a1a';
  g.fillRect(0, 0, 440, 260);
  g.strokeStyle = '#2b4a4a';
  g.lineWidth = 1;
  for (let i = 1; i < 6; i++) {
    g.beginPath();
    g.moveTo(0, i * 260 / 6);
    g.lineTo(440, i * 260 / 6);
    g.stroke();
  }
  g.font = 'bold 18px sans-serif';
  g.fillStyle = '#cfe8e8';
  g.fillText(`${ins.label}  (${ins.min}-${ins.max} ${ins.unit})`, 8, 20);
  if (!rows.length) {
    g.fillText('NO DATA', 170, 140);
    return texture(c);
  }
  ins.series.forEach(([field, mul, color], k) => {
    g.strokeStyle = color;
    g.lineWidth = 2.5;
    g.beginPath();
    rows.forEach((r, i) => {
      const v = r[field];
      if (v === null || v === undefined) return;
      const x = rows.length > 1 ? 8 + (424 * i) / (rows.length - 1) : 8;
      const y = 250 - Math.min(Math.max(((v * mul - ins.min) / (ins.max - ins.min)), 0), 1) * 215;
      if (i === 0) g.moveTo(x, y);
      else g.lineTo(x, y);
    });
    g.stroke();
    g.fillStyle = color;
    g.fillText(field, 300 + k * 44, 20);
  });
  return texture(c);
}

export function paintFloor() {
  const n = 320;
  const c = canvas(n, n);
  const g = c.getContext('2d');
  const img = g.createImageData(n, n);
  let seed = 7;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  const pts = Array.from({ length: 80 }, () => [rnd() * n, rnd() * n, 0.9 + 0.1 * rnd()]);
  for (let y = 0; y < n; y++) {
    for (let x = 0; x < n; x++) {
      let d1 = 1e9, d2 = 1e9, k = 0;
      for (let i = 0; i < pts.length; i++) {
        for (const ox of [-n, 0, n]) {
          for (const oy of [-n, 0, n]) {
            const dx = pts[i][0] + ox - x, dy = pts[i][1] + oy - y;
            const d = dx * dx + dy * dy;
            if (d < d1) { d2 = d1; d1 = d; k = i; } else if (d < d2) d2 = d;
          }
        }
      }
      const edge = Math.sqrt(d2) - Math.sqrt(d1) < 2.5;
      const v = edge ? 110 : 190 * pts[k][2] + 14 * Math.sin(x * 0.07 + y * 0.03);
      const o = (y * n + x) * 4;
      img.data[o] = v;
      img.data[o + 1] = v - (edge ? 0 : 4);
      img.data[o + 2] = v - (edge ? 0 : 12);
      img.data[o + 3] = 255;
    }
  }
  g.putImageData(img, 0, 0);
  return texture(c, true);
}

export function paintCeiling() {
  const c = canvas(256, 256);
  const g = c.getContext('2d');
  g.fillStyle = '#e4e3dc';
  g.fillRect(0, 0, 256, 256);
  g.strokeStyle = '#a8a89f';
  g.lineWidth = 3;
  g.strokeRect(0, 0, 256, 256);
  g.fillStyle = '#c9c8c0';
  for (let i = 0; i < 90; i++) g.fillRect((i * 53) % 250, (i * 97) % 250, 2, 2);
  return texture(c, true);
}

export function paintMimic(variant) {
  const c = canvas(128, 160);
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
  return texture(c);
}
