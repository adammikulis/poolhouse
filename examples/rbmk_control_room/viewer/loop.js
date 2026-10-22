// A frame loop that runs only while something changed or a needle is moving, and never while the tab is hidden.
export class Loop {
  constructor(step) {
    this.step = step;
    this.raf = 0;
    this.last = 0;
    this.stats = { frames: 0, ms: 0, max: 0 };
  }

  request() {
    if (this.raf || document.hidden) return;
    this.raf = requestAnimationFrame((now) => this.frame(now));
  }

  frame(now) {
    this.raf = 0;
    const dt = this.last ? Math.min((now - this.last) / 1000, 0.1) : 1 / 60;
    this.last = now;
    const t0 = performance.now();
    const more = this.step(dt);
    const ms = performance.now() - t0;
    this.stats.frames += 1;
    this.stats.ms += ms;
    this.stats.max = Math.max(this.stats.max, ms);
    if (more) this.request();
    else this.last = 0;
  }

  get running() {
    return this.raf !== 0;
  }
}
