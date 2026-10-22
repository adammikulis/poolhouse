// Measures the CPU time of the overlay's draw: a full redraw of every element, and the row-to-row update with everything live.
const summary = (xs) => {
  const s = [...xs].sort((a, b) => a - b);
  const mean = s.reduce((a, b) => a + b, 0) / s.length;
  return { mean: +mean.toFixed(3), p95: +s[Math.floor(s.length * 0.95)].toFixed(3), max: +s[s.length - 1].toFixed(3) };
};

export function bench(app, n = 120) {
  const { feed, state, overlay } = app;
  if (feed.status !== 'ok') throw new Error('bench needs a loaded feed');
  const keep = feed.onchange;
  feed.onchange = () => {};
  const rows = feed.data.trace.length;
  const full = [];
  const incremental = [];
  const dirty = [];
  for (let i = 0; i < n; i++) {
    feed.setIndex((i * 7) % rows);
    state.refresh();
    const t0 = performance.now();
    overlay.update(true);
    dirty.push(overlay.draw());
    incremental.push(performance.now() - t0);
  }
  for (let i = 0; i < n; i++) {
    const t0 = performance.now();
    overlay.setScale(overlay.k);
    overlay.update(true);
    overlay.draw();
    full.push(performance.now() - t0);
  }
  feed.onchange = keep;
  return { view: app.view, elements: overlay.elements.length, rows: n, fullRedrawMs: summary(full), rowUpdateMs: summary(incremental),
    dirtyPerRow: +(dirty.reduce((a, b) => a + b, 0) / n).toFixed(1) };
}
