// Navigation: fly (WASD + drag to look + Q/E) or orbit (touch), bookmark tweens and click-to-teleport.
import * as THREE from 'three';
import { OrbitControls } from 'poolhouse-assets/OrbitControls.js';

const EYE = 1.65;

function inside(poly, x, y) {
  let c = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) c = !c;
  }
  return c;
}

export class Navigator {
  constructor(camera, dom, outline, onPick) {
    this.camera = camera;
    this.dom = dom;
    this.outline = outline;   // plan polygon in Blender x, y
    this.onPick = onPick;
    this.mode = 'fly';
    this.yaw = Math.PI;
    this.pitch = 0;
    this.keys = new Set();
    this.tween = null;
    this.orbit = new OrbitControls(camera, dom);
    this.orbit.enableDamping = true;
    this.orbit.enabled = false;
    this.bind();
  }

  // Blender (x, y, z) maps to three (x, z, -y).
  allowed(p) {
    return p.y > 0.25 && p.y < 3.4 && inside(this.outline, p.x, -p.z);
  }

  setMode(mode) {
    this.mode = mode;
    this.orbit.enabled = mode === 'orbit';
    if (mode === 'orbit') {
      const dir = new THREE.Vector3();
      this.camera.getWorldDirection(dir);
      this.orbit.target.copy(this.camera.position).addScaledVector(dir, 3);
    } else {
      const d = new THREE.Vector3();
      this.camera.getWorldDirection(d);
      this.yaw = Math.atan2(-d.x, -d.z);
      this.pitch = Math.asin(d.y);
    }
  }

  look() {
    this.camera.rotation.set(this.pitch, this.yaw, 0, 'YXZ');
  }

  flyTo(pos, target, seconds = 0.9) {
    const from = this.camera.position.clone();
    const dir = target.clone().sub(pos);
    const yaw = Math.atan2(-dir.x, -dir.z);
    const pitch = Math.atan2(dir.y, Math.hypot(dir.x, dir.z));
    this.tween = { from, to: pos.clone(), t: 0, dur: seconds, yaw0: this.yaw, yaw1: yaw, p0: this.pitch, p1: pitch, target: target.clone() };
    if (this.mode === 'orbit') this.orbit.target.copy(target);
  }

  teleport(point) {
    const to = new THREE.Vector3(point.x, EYE, point.z);
    const dir = new THREE.Vector3();
    this.camera.getWorldDirection(dir);
    this.flyTo(to, to.clone().add(new THREE.Vector3(dir.x, 0, dir.z).multiplyScalar(2)).setY(EYE + dir.y * 2), 0.6);
  }

  bind() {
    const dom = this.dom;
    let down = null;
    dom.addEventListener('pointerdown', (e) => {
      down = { x: e.clientX, y: e.clientY, t: performance.now(), moved: 0 };
      dom.setPointerCapture(e.pointerId);
    });
    dom.addEventListener('pointermove', (e) => {
      if (!down) return;
      const dx = e.clientX - down.x, dy = e.clientY - down.y;
      down.moved += Math.abs(dx) + Math.abs(dy);
      down.x = e.clientX;
      down.y = e.clientY;
      if (this.mode === 'fly' && e.buttons) {
        this.tween = null;
        this.yaw -= dx * 0.004;
        this.pitch = Math.max(-1.4, Math.min(1.4, this.pitch - dy * 0.004));
      }
    });
    dom.addEventListener('pointerup', (e) => {
      if (down && down.moved < 6 && performance.now() - down.t < 500) this.onPick(e.clientX, e.clientY);
      down = null;
    });
    window.addEventListener('keydown', (e) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement) return;
      this.keys.add(e.key.toLowerCase());
    });
    window.addEventListener('keyup', (e) => this.keys.delete(e.key.toLowerCase()));
    window.addEventListener('blur', () => this.keys.clear());
  }

  update(dt) {
    if (this.tween) {
      const t = this.tween;
      t.t = Math.min(1, t.t + dt / t.dur);
      const k = t.t * t.t * (3 - 2 * t.t);
      this.camera.position.lerpVectors(t.from, t.to, k);
      this.yaw = t.yaw0 + Math.atan2(Math.sin(t.yaw1 - t.yaw0), Math.cos(t.yaw1 - t.yaw0)) * k;
      this.pitch = t.p0 + (t.p1 - t.p0) * k;
      if (t.t >= 1) this.tween = null;
      if (this.mode === 'orbit') this.orbit.update();
    }
    if (this.mode === 'orbit') {
      this.orbit.update();
      return;
    }
    const k = this.keys;
    const speed = (k.has('shift') ? 5 : 2.2) * dt;
    const f = (k.has('w') || k.has('arrowup') ? 1 : 0) - (k.has('s') || k.has('arrowdown') ? 1 : 0);
    const s = (k.has('d') || k.has('arrowright') ? 1 : 0) - (k.has('a') || k.has('arrowleft') ? 1 : 0);
    const u = (k.has('e') ? 1 : 0) - (k.has('q') ? 1 : 0);
    if (f || s || u) {
      this.tween = null;
      const next = this.camera.position.clone();
      next.x += (-Math.sin(this.yaw) * f + Math.cos(this.yaw) * s) * speed;
      next.z += (-Math.cos(this.yaw) * f - Math.sin(this.yaw) * s) * speed;
      next.y += u * speed;
      if (this.allowed(next)) this.camera.position.copy(next);
    }
    this.look();
  }
}
