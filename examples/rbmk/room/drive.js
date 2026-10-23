// Drives every instrument of the room from the feed: needles, lamps, tiles, recorders, the rod map, the screens.
import * as THREE from 'three';
import { paintDial, paintTile, paintRecorder, texture, canvas, lampColor } from './paint.js';
import { paintDecision, paintOrm } from './screens.js';
import { ROD_ORDER, rodRGB } from './rods.js';
import { evalRule, dialValue } from './rules.js';


function rodColor(f) {
  const c = rodRGB(f);
  return new THREE.Color(c[0], c[1], c[2]);
}

export class Driver {
  constructor(root, spec, feed) {
    this.spec = spec;
    this.feed = feed;
    this.byId = new Map(spec.instruments.map((i) => [i.id, i]));
    this.nodes = new Map();
    this.rods = [];
    this.shown = new Map();
    this.screens = {};
    root.traverse((o) => {
      let m = /^INS_(.+?)(__(.+))?$/.exec(o.name);
      if (m && this.byId.has(m[1])) {
        const slot = this.nodes.get(m[1]) || {};
        slot[m[3] || 'root'] = o;
        this.nodes.set(m[1], slot);
      }
      m = /^ROD_(R-\d\d-\d\d)$/.exec(o.name);
      if (m) this.rods.push({ id: m[1], node: o });
    });
    this.rodMats = Array.from({ length: 21 }, (_, i) => new THREE.MeshStandardMaterial({
      color: rodColor(i / 20), emissive: rodColor(i / 20), emissiveIntensity: 0.35, roughness: 0.5 }));
    this.offMat = new THREE.MeshStandardMaterial({ color: 0x222222, roughness: 0.7 });
    this.setup();
  }

  setup() {
    for (const ins of this.spec.instruments) {
      const n = this.nodes.get(ins.id);
      if (!n) continue;
      if (ins.type === 'dial') this.face(n, { map: paintDial(ins), roughness: 0.5, alphaTest: 0.5 });
      if (ins.type === 'ann' || ins.type === 'lamp') {
        n.tiles = [paintTile(ins, false), paintTile(ins, true)];
        this.face(n, { map: n.tiles[0] });
      }
      if (ins.type === 'recorder') this.face(n, { map: paintRecorder(ins, [], 1), emissive: 0xffffff, emissiveIntensity: 0.18 });
      if (ins.type === 'screen') this.screens[ins.id] = { n, canvas: canvas(1024, 600), key: '' };
    }
    const orm = this.nodes.get('SCREEN_ORM');
    if (orm) this.screens.SCREEN_ORM = { n: orm, canvas: canvas(300, 220), key: '' };
    const az = this.nodes.get('AZ5_BUTTON');
    if (az) {
      az.capY = az.cap.position.y;
      const c = canvas(200, 100);
      const g = c.getContext('2d');
      g.fillStyle = '#e8e6da';
      g.fillRect(0, 0, 200, 100);
      g.fillStyle = '#b3261e';
      g.font = 'bold 64px sans-serif';
      g.textAlign = 'center';
      g.textBaseline = 'middle';
      g.fillText('AZ-5', 100, 52);
      az.label.material = new THREE.MeshStandardMaterial({ map: texture(c), roughness: 0.5 });
    }
  }

  face(n, params) {
    const obj = n.face;
    if (!obj) return;
    obj.material = new THREE.MeshStandardMaterial({ ...params });
    obj.userData.instrument = true;
  }

  dark(mat, lit, color) {
    mat.emissive.set(lit ? color : 0x000000);
    mat.emissiveIntensity = lit ? 1.1 : 0;
  }

  update(dt) {
    const f = this.feed;
    const row = f.status === 'ok' ? f.row : null;
    const dec = f.status === 'ok' ? f.decision : null;
    for (const ins of this.spec.instruments) {
      const n = this.nodes.get(ins.id);
      if (!n) continue;
      if (ins.type === 'dial') this.dial(n, ins, row, dt);
      else if (ins.type === 'ann' || ins.type === 'lamp') this.tile(n, ins, row, dec);
      else if (ins.type === 'button') this.az5(n, row);
    }
    this.recorders(row);
    this.rodmap(row);
    this.screen('SCREEN_DECISION', () => paintDecision(this.screens.SCREEN_DECISION.canvas, f), `${f.status}|${f.stamp}|${f.index}`);
    this.screen('SCREEN_ORM', () => paintOrm(this.screens.SCREEN_ORM.canvas, row), `${f.status}|${f.stamp}|${f.index}`);
  }

  dial(n, ins, row, dt) {
    const v = dialValue(ins, row);
    const frac = v === null ? 0 : Math.min(Math.max((v - ins.min) / (ins.max - ins.min), 0), 1);
    const cur = n.needle.userData.frac ?? frac;
    const next = cur + (frac - cur) * Math.min(1, dt * 10);
    n.needle.userData.frac = next;
    n.needle.rotation.y = ((135 - 270 * next) * Math.PI) / 180;
    n.needle.visible = v !== null;
    n.value = v;
  }

  tile(n, ins, row, dec) {
    const lit = !!row && evalRule(ins.rule, row, dec);
    n.lit = lit;
    if (this.shown.get(ins.id) === lit) return;
    this.shown.set(ins.id, lit);
    const mat = n.face.material;
    mat.map = n.tiles[lit ? 1 : 0];
    mat.emissiveMap = lit ? n.tiles[1] : null;
    mat.emissive.set(lit ? 0xffffff : 0x000000);
    mat.emissiveIntensity = lit ? 1.0 : 0;
    mat.needsUpdate = true;
  }

  az5(n, row) {
    const scram = !!row && row.scram === true;
    n.cover.rotation.x = scram ? -1.7 : 0;
    n.cap.position.y = n.capY - (scram ? 0.012 : 0);
    n.lit = scram;
    const mat = n.cap.material;
    mat.emissive.set(scram ? 0xff2200 : 0x000000);
    mat.emissiveIntensity = scram ? 1.2 : 0;
  }

  recorders(row) {
    const key = this.feed.status === 'ok' ? `${this.feed.stamp}|${this.feed.index}` : -1;
    if (this._recKey === key) return;
    this._recKey = key;
    for (const ins of this.spec.instruments) {
      if (ins.type !== 'recorder') continue;
      const n = this.nodes.get(ins.id);
      if (!n) continue;
      const old = n.face.material.map;
      n.face.material.map = paintRecorder(ins, this.feed.window(120), 1);
      n.face.material.emissiveMap = n.face.material.map;
      n.face.material.needsUpdate = true;
      if (old) old.dispose();
    }
  }

  rodmap(row) {
    const key = row ? `${this.feed.stamp}|${this.feed.index}|${this.feed.status}` : 'none';
    if (this._rodKey === key) return;
    this._rodKey = key;
    const rods = row && Array.isArray(row.rods) && row.rods.length === ROD_ORDER.length ? row.rods : null;
    for (const rod of this.rods) {
      const v = rods ? rods[ROD_ORDER.indexOf(rod.id)] / 100 : undefined;
      rod.value = v;
      rod.node.material = v === undefined ? this.offMat : this.rodMats[Math.round(Math.min(Math.max(v, 0), 1) * 20)];
    }
  }

  screen(id, paint, key) {
    const s = this.screens[id];
    if (!s || s.key === key) return;
    s.key = key;
    paint();
    const mat = s.n.face.material;
    if (!(mat.map && mat.map.image === s.canvas)) {
      mat.map = texture(s.canvas);
      mat.emissiveMap = mat.map;
      mat.emissive.set(0xffffff);
      mat.emissiveIntensity = 0.9;
      mat.roughness = 0.95;
      mat.metalness = 0;
      mat.needsUpdate = true;
    } else mat.map.needsUpdate = true;
  }
}

export { lampColor };
