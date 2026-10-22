// The demo's rod order: the 211 cells of the 17 by 17 grid nearest the centre, by distance then (i, j).
export const ROD_ORDER = (() => {
  const cells = [];
  for (let i = 0; i < 17; i++) for (let j = 0; j < 17; j++) cells.push([i, j]);
  const dist = (c) => (c[0] - 8) * (c[0] - 8) + (c[1] - 8) * (c[1] - 8);
  cells.sort((a, b) => dist(a) - dist(b) || a[0] - b[0] || a[1] - b[1]);
  return cells.slice(0, 211).map(([i, j]) => `R-${String(i).padStart(2, '0')}-${String(j).padStart(2, '0')}`);
})();
