// How each live element of the 2D views is drawn into its local box (needle, tile, strip chart, screen, AZ-5, rod cell, mimic).
import { DIAL_SIZE, MIMIC_SIZE, RECORDER_SIZE, TILE_SIZE, canvas, paintDialFace, paintMimicFace, paintRecorderFace, paintTileFace } from './faces.js';
import { paintDecision, paintOrm } from './screens.js';
import { rodCss } from './rods.js';

const SCREEN_SIZE = [1024, 600];
const ORM_SIZE = [300, 220];
const BUTTON_SIZE = [140, 140];
const ACCENT = '#4aa3df';
const ALERT = ['#4aa3df', '#e8a317', '#d8362c'];

const faceCache = new Map();
const cached = (key, make) => {
  if (!faceCache.has(key)) faceCache.set(key, make());
  return faceCache.get(key);
};

function blank(c) {
  const g = c.getContext('2d');
  g.fillStyle = '#061214';
  g.fillRect(0, 0, c.width, c.height);
}

function needle(g, frac) {
  g.save();
  g.translate(160, 160);
  g.rotate(((frac * 270 - 135) * Math.PI) / 180);
  g.fillStyle = '#e5251b';
  g.beginPath();
  g.moveTo(-5, 28);
  g.lineTo(-3, -112);
  g.lineTo(0, -122);
  g.lineTo(3, -112);
  g.lineTo(5, 28);
  g.closePath();
  g.fill();
  g.fillStyle = '#222';
  g.beginPath();
  g.arc(0, 0, 13, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = '#e5251b';
  g.beginPath();
  g.arc(0, 0, 7, 0, Math.PI * 2);
  g.fill();
  g.restore();
}

function az5(g, el) {
  const pressed = el.lit;
  g.fillStyle = pressed ? '#ff3b1f' : '#9c1f18';
  g.strokeStyle = '#2a0b08';
  g.lineWidth = 5;
  g.beginPath();
  g.arc(70, 76, 34, 0, Math.PI * 2);
  g.fill();
  g.stroke();
  if (pressed) {
    g.strokeStyle = '#ff9a7a';
    g.lineWidth = 6;
    g.beginPath();
    g.arc(70, 76, 44, 0, Math.PI * 2);
    g.stroke();
  }
  const top = pressed ? -14 : 4;
  const h = pressed ? 12 : 132;
  g.fillStyle = pressed ? '#8a9090' : 'rgba(120,128,128,0.93)';
  g.fillRect(6, top, 128, h);
  if (!pressed) {
    g.fillStyle = '#e8e6da';
    g.fillRect(30, 52, 80, 40);
    g.fillStyle = '#b3261e';
    g.font = 'bold 30px sans-serif';
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    g.fillText('AZ-5', 70, 73);
  }
}

// Local painters keyed by instrument type; each draws into the box given by its size.
export const KINDS = {
  dial: { size: DIAL_SIZE, make: (el) => { el.face = cached(el.ins.id, () => paintDialFace(el.ins)); }, paint: (g, el) => {
    g.drawImage(el.face, 0, 0, ...DIAL_SIZE);
    if (el.cur !== null) needle(g, el.cur);
  } },
  ann: { size: TILE_SIZE, make: (el) => { el.tiles = cached(el.ins.id, () => [paintTileFace(el.ins, false), paintTileFace(el.ins, true)]); },
    paint: (g, el) => g.drawImage(el.tiles[el.lit ? 1 : 0], 0, 0, ...TILE_SIZE) },
  recorder: { size: RECORDER_SIZE, make: (el) => { el.bmp = canvas(...RECORDER_SIZE); },
    refresh: (el, st) => { el.bmp = paintRecorderFace(el.ins, st.feed.window(120)); },
    paint: (g, el) => g.drawImage(el.bmp, 0, 0, ...RECORDER_SIZE) },
  screen: { size: SCREEN_SIZE, make: (el) => { el.bmp = canvas(...SCREEN_SIZE); },
    refresh: (el, st) => (st.feed.status === 'loading' ? blank(el.bmp) : paintDecision(el.bmp, st.feed)),
    paint: (g, el) => g.drawImage(el.bmp, 0, 0, ...SCREEN_SIZE) },
  orm: { size: ORM_SIZE, make: (el) => { el.bmp = canvas(...ORM_SIZE); },
    refresh: (el, st) => (st.feed.status === 'loading' ? blank(el.bmp) : paintOrm(el.bmp, st.feed.status === 'ok' ? st.feed.row : null)),
    paint: (g, el) => g.drawImage(el.bmp, 0, 0, ...ORM_SIZE) },
  button: { size: BUTTON_SIZE, paint: az5 },
  mimic: { size: MIMIC_SIZE, make: (el) => { el.face = cached(`mimic${el.variant}`, () => paintMimicFace(el.variant)); },
    paint: (g, el) => g.drawImage(el.face, 0, 0, ...MIMIC_SIZE) },
};
KINDS.lamp = KINDS.ann;

// Raw painters draw in image pixels (the rod cells and the panel markers).
export function paintRod(g, el) {
  g.fillStyle = el.value === undefined ? '#222' : rodCss(Math.round(Math.min(Math.max(el.value, 0), 1) * 20) / 20);
  g.beginPath();
  g.moveTo(el.q[0], el.q[1]);
  for (let i = 2; i < 8; i += 2) g.lineTo(el.q[i], el.q[i + 1]);
  g.closePath();
  g.fill();
}

function chip(g, text, x, y, s) {
  g.font = `600 ${13 / s.k}px system-ui, sans-serif`;
  const tw = g.measureText(text).width + 10 / s.k;
  const cx = Math.min(Math.max(x - tw / 2, 4 / s.k), s.view.width - tw - 4 / s.k);
  g.fillStyle = 'rgba(16,22,24,0.86)';
  g.fillRect(cx, y, tw, 18 / s.k);
  g.fillStyle = '#e6ebe8';
  g.textBaseline = 'middle';
  g.fillText(text, cx + 5 / s.k, y + 9 / s.k);
}

export function paintMarker(g, el, s) {
  const [x, y, w, h] = el.rect;
  const col = ALERT[el.alert];
  if (el.alert) {
    g.globalAlpha = 0.28;
    g.fillStyle = col;
    g.fillRect(x, y, w, h);
    g.globalAlpha = 1;
  }
  if (!el.always && !el.hover && !el.alert) return;
  g.strokeStyle = el.alert ? col : ACCENT;
  g.lineWidth = (el.hover ? 3 : 2) / s.k;
  g.setLineDash([8 / s.k, 5 / s.k]);
  g.strokeRect(x, y, w, h);
  g.setLineDash([]);
  if (!el.always && !el.hover) return;
  if (el.always) {
    const ty = (8 + el.row * 24) / s.k;
    g.beginPath();
    g.moveTo(x + w / 2, ty + 18 / s.k);
    g.lineTo(x + w / 2, y);
    g.stroke();
    chip(g, el.label, x + w / 2, ty, s);
  } else chip(g, el.label, x, y > 24 / s.k ? y - 20 / s.k : y + h + 2 / s.k, s);
}
