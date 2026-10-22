"""The instrument wall and the desk uprights: panels, mimic windows, dials, lamps, tiles, recorders, screens, the rod map."""

from __future__ import annotations

import math

from mathutils import Matrix

from roomkit import Batch, Solid, empty, material, wall_frame
from room_shell import R_WALL, THETA0, THETA1

ROD_PITCH, ROD_GRID, ROD_COUNT = 0.14, 17, 211
WALL_Z = 3.3
R_UPRIGHT = 7.45
ROD_CENTER = (90.0, 2.0)


def rod_cells() -> list[tuple[int, int]]:
    """The 211 occupied cells of the 17 by 17 grid: the ones nearest the centre, in the demo's ordering."""
    cells = sorted(((i, j) for i in range(ROD_GRID) for j in range(ROD_GRID)),
                   key=lambda c: (math.hypot(c[0] - 8, c[1] - 8), c))
    return cells[:ROD_COUNT]


def panels(batch: Batch) -> None:
    """The faceted wall: painted panel bodies, plinth, cornice."""
    step = 2.5
    n = round((THETA1 - THETA0) / step)
    chord = 2 * R_WALL * math.sin(math.radians(step / 2)) * 1.03
    for i in range(n):
        th = THETA0 + step * (i + 0.5)
        batch["paint"].box(wall_frame(th, WALL_Z / 2, R_WALL, -0.1), chord, WALL_Z, 0.2)
        batch["plinth"].box(wall_frame(th, 0.16, R_WALL, 0.02), chord, 0.32, 0.12)
        batch["paint_dark"].box(wall_frame(th, 3.5, R_WALL, 0.05), chord, 0.4, 0.34)
    for th in (THETA0, THETA1):
        batch["paint_dark"].box(wall_frame(th, 1.85, R_WALL, 0.05), 0.08, 3.7, 0.3)


def _grid_pos(zone: dict, slot: int, rows: int, radius: float):
    cols = zone["cols"]
    col, row = slot % cols, slot // cols
    x = (col - (cols - 1) / 2) * zone["pitch"]
    th = zone["theta"] + math.degrees(x / radius)
    return th, zone.get("z", 1.4) - (row - (rows - 1) / 2) * zone.get("rowpitch", 0.0)


def _zone_rows(instruments: list[dict], zone_name: str, cols: int) -> int:
    slots = [i["slot"] for i in instruments if i["zone"] == zone_name]
    return (max(slots) // cols + 1) if slots else 1


def _plate(batch: Batch, zone: dict, rows: int, radius: float, w_extra: float, h_extra: float) -> None:
    cols = zone["cols"]
    width = cols * zone["pitch"] + w_extra
    height = rows * zone.get("rowpitch", 0.2) + h_extra
    batch["paint_dark"].box(wall_frame(zone["theta"], zone.get("z", 1.4), radius, 0.0), width, height, 0.16)


def _dial(batch: Batch, ins: dict, T: Matrix) -> None:
    batch["case"].cyl(T @ Matrix.Translation((0, 0, 0.025)), 0.175, 0.05, 20)
    batch["chrome"].cyl(T @ Matrix.Translation((0, 0, 0.052)), 0.18, 0.012, 20, 0.17)
    root = empty("INS_" + ins["id"], T)
    face = Solid()
    face.quad(Matrix.Identity(4), 0.31, 0.31)
    face.build(f"INS_{ins['id']}__face", material(f"M_face_{ins['id']}", (0.9, 0.9, 0.86), 0.6), root,
               Matrix.Translation((0, 0, 0.0585)))
    needle = Solid()
    needle.box(Matrix.Translation((0, 0.05, 0)), 0.009, 0.14, 0.004)
    needle.cyl(Matrix.Identity(4), 0.016, 0.006, 12)
    needle.build(f"INS_{ins['id']}__needle", material("M_needle", (0.9, 0.12, 0.08), 0.5), root,
                 Matrix.Translation((0, 0, 0.064)))


def _tile(batch: Batch, ins: dict, T: Matrix, w: float, h: float) -> None:
    batch["case"].box(T @ Matrix.Translation((0, 0, 0.012)), w + 0.012, h + 0.012, 0.024)
    root = empty("INS_" + ins["id"], T)
    face = Solid()
    face.quad(Matrix.Identity(4), w, h)
    face.build(f"INS_{ins['id']}__face", material(f"M_face_{ins['id']}", (0.2, 0.2, 0.2), 0.4), root,
               Matrix.Translation((0, 0, 0.026)))


def _recorder(batch: Batch, ins: dict, T: Matrix) -> None:
    batch["case"].box(T @ Matrix.Translation((0, 0, 0.04)), 0.5, 0.34, 0.08)
    batch["chrome"].box(T @ Matrix.Translation((0, 0, 0.082)), 0.47, 0.30, 0.004)
    root = empty("INS_" + ins["id"], T)
    face = Solid()
    face.quad(Matrix.Identity(4), 0.44, 0.26)
    face.build(f"INS_{ins['id']}__face", material(f"M_face_{ins['id']}", (0.9, 0.9, 0.8), 0.5), root,
               Matrix.Translation((0, 0, 0.086)))


def _screen(batch: Batch, ins: dict, T: Matrix, w: float, h: float, depth: float = 0.08) -> None:
    batch["plate"].box(T @ Matrix.Translation((0, 0, depth / 2 - 0.05)), w + 0.1, h + 0.1, depth + 0.1)
    root = empty("INS_" + ins["id"], T)
    face = Solid()
    face.quad(Matrix.Identity(4), w, h)
    face.build(f"INS_{ins['id']}__face", material(f"M_face_{ins['id']}", (0.02, 0.05, 0.05), 0.3), root,
               Matrix.Translation((0, 0, depth + 0.004)))


def instruments(batch: Batch, spec: dict) -> None:
    """Place every wall and upright instrument of instruments.json."""
    zones, items = spec["zones"], spec["instruments"]
    rows_of = {name: _zone_rows(items, name, z["cols"]) for name, z in zones.items()}
    for name, z in zones.items():
        if name in ("annunciator", "reactor_dials"):
            _plate(batch, z, rows_of[name], R_WALL, 0.14, 0.14)
        if name in ("desk_lamps", "desk_recorders"):
            batch["paint"].box(wall_frame(z["theta"], 1.4, R_UPRIGHT, -0.04), z["cols"] * z["pitch"] + 0.2, 0.62, 0.08)
            batch["desk_kick"].box(wall_frame(z["theta"], 1.04, R_UPRIGHT, -0.04), z["cols"] * z["pitch"] + 0.24, 0.1, 0.12)
    for ins in items:
        zone = zones.get(ins["zone"])
        if zone is None:
            continue
        radius = R_UPRIGHT if zone["kind"] == "desk" else R_WALL
        th, z = _grid_pos(zone, ins["slot"], rows_of[ins["zone"]], radius)
        T = wall_frame(th, z, radius, 0.06 if zone["kind"] == "wall" and ins["type"] == "screen" else 0.085)
        kind = ins["type"]
        if kind == "dial":
            _dial(batch, ins, T)
        elif kind == "ann":
            _tile(batch, ins, T, 0.235, 0.13)
        elif kind == "lamp":
            _tile(batch, ins, T, 0.14, 0.08)
        elif kind == "recorder":
            _recorder(batch, ins, T)
        elif kind == "screen":
            _screen(batch, ins, T, 2.4, 1.4)


def rod_map(batch: Batch) -> None:
    """The round rod-position map: a disc plate carrying 211 cells named ROD_R-ii-jj."""
    th, zc = ROD_CENTER
    T = wall_frame(th, zc, R_WALL, 0.0)
    batch["plate"].cyl(T @ Matrix.Translation((0, 0, 0.03)), 1.27, 0.2, 40)
    root = empty("INS_RODMAP", T)
    for i, j in rod_cells():
        cell = Solid()
        cell.box(Matrix.Identity(4), 0.112, 0.112, 0.022)
        cell.build(f"ROD_R-{i:02d}-{j:02d}", material("M_rod", (0.8, 0.62, 0.08), 0.5), root,
                   Matrix.Translation(((i - 8) * ROD_PITCH, (j - 8) * ROD_PITCH, 0.14)))


def mimic_windows(batch: Batch) -> None:
    """Static mimic-diagram windows in the upper band and the turbine and electrical sections."""
    k = 0
    blocks = [(th, 3.08, 1) for th in range(37, 80, 6)] + [(th, 3.08, 1) for th in range(102, 146, 6)]
    blocks += [(th, 2.2, 2) for th in (38, 44, 50, 56)] + [(th, 2.2, 2) for th in (141.5,)]
    for th, z, rows in blocks:
        if 80 < th < 100 or (rows == 1 and 58 < th < 76):
            continue
        for r in range(rows):
            for c in range(3):
                th_i = th + (c - 1) * 1.45
                zz = z - r * 0.55
                T = wall_frame(th_i, zz, R_WALL, 0.0)
                batch["paint_dark"].box(T @ Matrix.Translation((0, 0, 0.012)), 0.34, 0.5 if rows > 1 else 0.4, 0.024)
                q = Solid()
                q.quad(Matrix.Identity(4), 0.31, 0.47 if rows > 1 else 0.37)
                ob = q.build(f"MIMIC_{k}", material(f"M_mimic{k % 4}", (0.1, 0.15, 0.15), 0.3))
                ob.matrix_basis = T @ Matrix.Translation((0, 0, 0.026))
                k += 1
    for th in list(range(37, 80, 2)) + list(range(102, 146, 2)):
        for zz in (1.1, 1.2):
            if 80 < th < 100:
                continue
            batch["switch"].box(wall_frame(th, zz, R_WALL, 0.0) @ Matrix.Translation((0, 0, 0.03)), 0.03, 0.03, 0.06)
