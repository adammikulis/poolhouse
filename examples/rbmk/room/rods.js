// The demo's rod order: the 211 cells of the 17 by 17 grid nearest the centre, by distance then (i, j).
export const ROD_ORDER = (() => {
  const cells = [];
  for (let i = 0; i < 17; i++) for (let j = 0; j < 17; j++) cells.push([i, j]);
  const dist = (c) => (c[0] - 8) * (c[0] - 8) + (c[1] - 8) * (c[1] - 8);
  cells.sort((a, b) => dist(a) - dist(b) || a[0] - b[0] || a[1] - b[1]);
  return cells.slice(0, 211).map(([i, j]) => `R-${String(i).padStart(2, '0')}-${String(j).padStart(2, '0')}`);
})();

const STOPS = [[0.85, 0.7, 0.1], [0.2, 0.55, 0.25], [0.75, 0.12, 0.1]];

// Colour of a rod cell for an insertion fraction: yellow (withdrawn), green, red (inserted), as [r, g, b] in 0..1.
export function rodRGB(f) {
  const k = Math.min(Math.max(f, 0), 1) * 2;
  const i = Math.min(Math.floor(k), 1);
  const t = k - i;
  return STOPS[i].map((a, j) => a + (STOPS[i + 1][j] - a) * t);
}

export function rodCss(f) {
  const [r, g, b] = rodRGB(f).map((x) => Math.round(x * 255));
  return `rgb(${r},${g},${b})`;
}
