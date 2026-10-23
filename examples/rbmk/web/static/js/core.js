/* The animated reactor core cross-section: one tile and one rod per channel, glow, bubbles. */
window.RB = window.RB || {};
(function (RB) {
  'use strict';

  const KINDS = { MR: ['manual control', '#3aa0ff'], AR: ['automatic regulation', '#34c759'],
                  LAR: ['local automatic regulation', '#9be15d'], USP: ['shortened absorber', '#ffb020'],
                  EP: ['emergency protection', '#ff5d73'] };
  RB.KINDS = KINDS;
  const TC = 270, TF_LIMIT = 1200;

  function ramp(stops, t) {
    const x = Math.min(Math.max(t, 0), 1) * (stops.length - 1), i = Math.min(Math.floor(x), stops.length - 2), f = x - i;
    const a = stops[i], b = stops[i + 1];
    return 'rgb(' + [0, 1, 2].map(function (k) { return Math.round(a[k] + (b[k] - a[k]) * f); }).join(',') + ')';
  }
  const POWER = [[18, 28, 52], [32, 74, 140], [48, 150, 160], [230, 200, 60], [255, 140, 40], [255, 245, 220]];
  const HEAT = [[24, 40, 90], [40, 110, 150], [60, 170, 120], [230, 200, 60], [240, 110, 40], [255, 40, 60]];

  class CoreView {
    constructor(canvas, opts) {
      this.cv = canvas; this.ctx = canvas.getContext('2d'); this.opts = opts || {};
      this.bubbles = []; this.mode = 'power'; this.layoutKey = ''; this.last = performance.now();
    }

    prepare(rods) {
      const key = rods.length + ':' + (rods[0] ? rods[0].id : '');
      if (key === this.layoutKey) { return; }
      this.layoutKey = key;
      const xs = Array.from(new Set(rods.map(function (r) { return r.x; }))).sort(function (a, b) { return a - b; });
      let gap = 2;
      for (let i = 1; i < xs.length; i++) { gap = Math.min(gap, xs[i] - xs[i - 1]); }
      this.pitch = gap;
      const flux = rods.map(function (r) { return Math.sqrt(Math.max(r.w, 0)); });
      const mean = flux.reduce(function (s, v) { return s + v; }, 0) / flux.length;
      const top = Math.max.apply(null, flux);
      this.rel = flux.map(function (v) { return v / mean; });
      this.hot = flux.map(function (v) { return v / top; });
      this.index = {};
      rods.forEach((r, i) => { this.index[r.id] = i; });
    }

    resize() {
      const dpr = window.devicePixelRatio || 1, w = this.cv.clientWidth;
      if (this.cv.width !== Math.round(w * dpr) || this.cv.height !== Math.round(w * dpr)) {
        this.cv.width = this.cv.height = Math.round(w * dpr);
      }
      return { size: this.cv.width, dpr: dpr };
    }

    syncBubbles(count) {
      while (this.bubbles.length < count) {
        const a = Math.random() * 6.283, r = Math.sqrt(Math.random());
        this.bubbles.push({ x: Math.cos(a) * r, y: Math.sin(a) * r, s: 0.4 + Math.random() * 0.8, z: 0.6 + Math.random() * 1.6 });
      }
      this.bubbles.length = Math.max(count, 0);
    }

    draw(rods, frame, extra) {
      if (this.cv.clientWidth < 2) { return; }
      this.prepare(rods);
      const { size, dpr } = this.resize(), ctx = this.ctx, now = performance.now();
      const dt = extra && extra.paused ? 0 : Math.min((now - this.last) / 1000, 0.1);
      this.last = now;
      const cx = size / 2, cy = size / 2, R = size * 0.47;
      ctx.clearRect(0, 0, size, size);
      const bg = RB.cssVar('--core-bg') || '#0b1020';
      ctx.fillStyle = bg;
      ctx.beginPath(); ctx.arc(cx, cy, R * 1.04, 0, 6.283); ctx.fill();
      if (!frame) { this.empty(ctx, cx, cy, R, dpr); return; }
      const P = Math.max(frame.P, 0), glow = Math.min(P, 1.6) / 1.6, flick = 1 + 0.04 * Math.sin(now / 90);
      const g = ctx.createRadialGradient(cx, cy, R * 0.1, cx, cy, R * 1.25);
      g.addColorStop(0, 'rgba(255,170,60,' + (0.75 * glow * flick).toFixed(3) + ')');
      g.addColorStop(0.6, 'rgba(255,90,30,' + (0.35 * glow).toFixed(3) + ')');
      g.addColorStop(1, 'rgba(255,60,20,0)');
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(cx, cy, R * 1.25, 0, 6.283); ctx.fill();

      const unit = R * 0.93, pitch = this.pitch * unit, half = pitch * 0.47;
      const faults = frame.faults || {}, hl = (extra && extra.highlight) || new Set();
      ctx.save();
      ctx.beginPath(); ctx.arc(cx, cy, R, 0, 6.283); ctx.clip();
      for (let i = 0; i < rods.length; i++) {
        const rod = rods[i], x = cx + rod.x * unit, y = cy + rod.y * unit;
        const t = this.mode === 'power' ? this.rel[i] * P / 1.8 : (Math.max(frame.Tf, TC) - TC) * this.hot[i] / (TF_LIMIT - TC);
        ctx.fillStyle = ramp(this.mode === 'power' ? POWER : HEAT, t);
        ctx.fillRect(x - half, y - half, half * 2, half * 2);
      }
      this.drawBubbles(ctx, cx, cy, R, frame, dt, size);
      for (let i = 0; i < rods.length; i++) { this.drawRod(ctx, rods[i], i, frame, cx, cy, unit, pitch, faults, hl, now); }
      ctx.restore();
      ctx.lineWidth = 2 * dpr; ctx.strokeStyle = RB.cssVar('--core-edge') || '#8aa';
      ctx.beginPath(); ctx.arc(cx, cy, R, 0, 6.283); ctx.stroke();
      this.banners(ctx, frame, cx, cy, R, dpr, now);
    }

    empty(ctx, cx, cy, R, dpr) {
      ctx.fillStyle = RB.cssVar('--muted') || '#999';
      ctx.font = (14 * dpr) + 'px system-ui, sans-serif'; ctx.textAlign = 'center';
      ctx.fillText('no run loaded', cx, cy);
    }

    drawRod(ctx, rod, i, frame, cx, cy, unit, pitch, faults, hl, now) {
      const x = cx + rod.x * unit, y = cy + rod.y * unit, r = Math.min(Math.max(frame.rods[i] / 100, 0), 1);
      const color = (KINDS[rod.kind] || ['', '#ccc'])[1], rad = pitch * 0.36;
      ctx.lineWidth = Math.max(1, pitch * 0.07); ctx.strokeStyle = color;
      ctx.fillStyle = 'rgba(8,12,24,0.78)';
      ctx.beginPath(); ctx.arc(x, y, rad, 0, 6.283); ctx.fill(); ctx.stroke();
      if (r > 0.01) {
        ctx.fillStyle = color; ctx.beginPath(); ctx.arc(x, y, rad * Math.sqrt(r), 0, 6.283); ctx.fill();
      }
      const fault = faults[rod.id];
      if (fault) {
        const pulse = 0.55 + 0.45 * Math.sin(now / 160);
        ctx.strokeStyle = fault === 'stuck' ? 'rgba(255,40,60,' + pulse + ')' : 'rgba(255,190,40,' + pulse + ')';
        ctx.lineWidth = Math.max(1.5, pitch * 0.1);
        ctx.beginPath(); ctx.arc(x, y, rad * 1.25, 0, 6.283); ctx.stroke();
        if (fault === 'stuck') {
          ctx.beginPath(); ctx.moveTo(x - rad, y - rad); ctx.lineTo(x + rad, y + rad);
          ctx.moveTo(x + rad, y - rad); ctx.lineTo(x - rad, y + rad); ctx.stroke();
        }
      }
      if (hl.has(i)) {
        const pulse = 0.6 + 0.4 * Math.sin(now / 110);
        ctx.strokeStyle = 'rgba(255,255,255,' + pulse + ')'; ctx.lineWidth = Math.max(2, pitch * 0.14);
        ctx.beginPath(); ctx.arc(x, y, rad * 1.5, 0, 6.283); ctx.stroke();
        ctx.strokeStyle = 'rgba(0,200,255,' + pulse + ')'; ctx.lineWidth = Math.max(1, pitch * 0.06);
        ctx.beginPath(); ctx.arc(x, y, rad * 1.8, 0, 6.283); ctx.stroke();
      }
    }

    drawBubbles(ctx, cx, cy, R, frame, dt, size) {
      const flow = Math.max(frame.f, 0.05);
      this.syncBubbles(Math.round(Math.min(frame.void, 0.8) * 260));
      ctx.fillStyle = 'rgba(210,235,255,0.42)'; ctx.strokeStyle = 'rgba(255,255,255,0.6)';
      ctx.lineWidth = Math.max(1, size / 500);
      for (const b of this.bubbles) {
        b.y -= b.s * flow * dt * 0.5;
        if (b.x * b.x + b.y * b.y > 1) { b.x = Math.random() * 2 - 1; b.y = Math.sqrt(Math.max(0, 1 - b.x * b.x)); }
        ctx.beginPath(); ctx.arc(cx + b.x * R, cy + b.y * R, b.z * size / 300, 0, 6.283); ctx.fill(); ctx.stroke();
      }
    }

    banners(ctx, frame, cx, cy, R, dpr, now) {
      const label = function (text, color, dy) {
        ctx.font = 'bold ' + (R * 0.12) + 'px system-ui, sans-serif'; ctx.textAlign = 'center';
        ctx.lineWidth = 4 * dpr; ctx.strokeStyle = 'rgba(0,0,0,0.75)'; ctx.strokeText(text, cx, cy + dy);
        ctx.fillStyle = color; ctx.fillText(text, cx, cy + dy);
      };
      if (frame.damaged) {
        ctx.strokeStyle = 'rgba(255,0,40,' + (0.5 + 0.5 * Math.sin(now / 120)) + ')'; ctx.lineWidth = 8 * dpr;
        ctx.beginPath(); ctx.arc(cx, cy, R * 1.02, 0, 6.283); ctx.stroke();
        label('CORE DAMAGED', '#ff4058', -R * 0.1);
      }
      if (frame.scram) { label('AZ-5 SCRAM', '#ffd23f', frame.damaged ? R * 0.1 : -R * 0.05); }
    }
  }
  RB.CoreView = CoreView;

  /* Rods the decision ``d`` moves: the named rod, or the rods of the named group that change over the next ticks. */
  RB.movedRods = function (run, d) {
    const out = new Set();
    if (!d || !run.header || d.applied_tick === null || d.applied_tick === undefined || !d.choice.cls.endsWith('_rod')) { return out; }
    const i = run.trace.findIndex(function (r) { return r.tick === d.applied_tick; });
    if (i < 0) { return out; }
    const next = run.trace[Math.min(i + 2, run.trace.length - 1)], here = run.trace[i], t = d.choice.target;
    run.header.rods.forEach(function (r, k) {
      const grp = t.startsWith('grp:') && t === 'grp:' + r.kind;
      const moved = next && here && next.rods[k] !== here.rods[k];
      if (t === r.id || (grp && moved)) { out.add(k); }
    });
    return out;
  };
})(window.RB);
