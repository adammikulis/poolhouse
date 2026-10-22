// Canvas textures for the 3D room: the shared instrument faces wrapped as three.js textures, plus the floor and ceiling.
import * as THREE from 'three';
import { canvas, lampColor, paintDialFace, paintTileFace, paintRecorderFace, paintMimicFace } from './faces.js';

export { canvas, lampColor };

export function texture(c, repeat = false) {
  const t = new THREE.CanvasTexture(c);
  t.flipY = false;
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 8;
  if (repeat) t.wrapS = t.wrapT = THREE.RepeatWrapping;
  return t;
}

export const paintDial = (ins) => texture(paintDialFace(ins));
export const paintTile = (ins, lit) => texture(paintTileFace(ins, lit));
export const paintRecorder = (ins, rows) => texture(paintRecorderFace(ins, rows));
export const paintMimic = (variant) => texture(paintMimicFace(variant));

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
