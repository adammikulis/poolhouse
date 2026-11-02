"""The room shell: floor, walls, ceiling, light panels, glazing onto the turbine hall, doors, camera marks."""

from __future__ import annotations

import math

from mathutils import Matrix, Vector

from roomkit import Batch, Solid, empty, frame, material, named_material

R_WALL = 9.0
THETA0, THETA1 = 35.0, 145.0
CEIL = 3.7
X_EDGE, Y_SOUTH = 11.0, -8.0


def outline() -> list[tuple[float, float]]:
    """The floor polygon, counter-clockwise: the arc wall, the wings and the straight walls."""
    pts = []
    n = 44
    for i in range(n + 1):
        t = math.radians(THETA0 + (THETA1 - THETA0) * i / n)
        pts.append((R_WALL * math.cos(t), R_WALL * math.sin(t)))
    y_wing = pts[0][1]
    return [*pts, (-X_EDGE, y_wing), (-X_EDGE, Y_SOUTH), (X_EDGE, Y_SOUTH), (X_EDGE, y_wing)]


def _wall_between(batch: Batch, a, b, height: float, thick: float, key: str, z0: float = 0.0) -> None:
    ax, ay = a
    bx, by = b
    length = math.hypot(bx - ax, by - ay)
    ux, uy = (bx - ax) / length, (by - ay) / length
    T = frame(((ax + bx) / 2 + uy * thick / 2, (ay + by) / 2 - ux * thick / 2, z0 + height / 2), (ux, uy, 0), (-uy, ux, 0), (0, 0, 1))
    batch[key].box(T, length, thick, height)


def shell(batch: Batch) -> list[tuple[float, float]]:
    """Floor, ceiling and the walls other than the instrument wall; returns the floor outline."""
    pts = outline()
    floor, ceil = Solid(), Solid()
    floor.poly(pts, 3.2)
    ceil.poly(pts, 2.4, flip=True, z=CEIL)
    floor.build("FLOOR", material("M_floor", (0.95, 0.93, 0.88), 0.4))
    ceil.build("CEILING", material("M_ceiling", (0.88, 0.88, 0.84), 0.9))
    wing = pts[-5:]
    for a, b in zip(wing, wing[1:], strict=False):
        if a[1] == b[1] == Y_SOUTH:
            continue
        _wall_between(batch, a, b, CEIL, 0.3, "wall")
    _wall_between(batch, pts[-1], pts[0], CEIL, 0.3, "wall")
    return pts


def light_panels(batch: Batch) -> list[tuple[float, float]]:
    """Fluorescent panels in the suspended ceiling; returns their centres (the viewer lights the room from them)."""
    spots = []
    for ix in range(-3, 4):
        for iy in range(-2, 4):
            x, y = ix * 2.7, iy * 2.4 + 0.6
            if math.hypot(x, y) > 8.1 and y > 0:
                continue
            batch["light_panel"].box(frame((x, y, CEIL - 0.035), (1, 0, 0), (0, 1, 0), (0, 0, 1)), 1.25, 0.6, 0.03)
            batch["ceiling"].box(frame((x, y, CEIL - 0.012), (1, 0, 0), (0, 1, 0), (0, 0, 1)), 1.4, 0.72, 0.03)
            spots.append((x, y))
    return spots


def hall_windows(batch: Batch) -> None:
    """A glazed band in the south wall looking onto the turbine hall: frames, mullions, glow and machine shapes."""
    z0, z1, x0, x1 = 1.0, 2.9, -7.0, 7.0
    y = Y_SOUTH
    wall_t = 0.3
    ident = ((1, 0, 0), (0, 1, 0), (0, 0, 1))
    for x_a, x_b, za, zb in ((-X_EDGE, x0, 0, CEIL), (x1, X_EDGE, 0, CEIL), (x0, x1, 0, z0), (x0, x1, z1, CEIL)):
        batch["wall"].box(frame(((x_a + x_b) / 2, y - wall_t / 2, (za + zb) / 2), *ident), x_b - x_a, wall_t, zb - za)
    for xm in [x0 + i * 1.4 for i in range(11)]:
        batch["paint_dark"].box(frame((xm, y + 0.16, (z0 + z1) / 2), (1, 0, 0), (0, 1, 0), (0, 0, 1)), 0.1, 0.1, z1 - z0)
    for z in (z0, (z0 + z1) / 2, z1):
        batch["paint_dark"].box(frame(((x0 + x1) / 2, y + 0.16, z), (1, 0, 0), (0, 1, 0), (0, 0, 1)), x1 - x0, 0.1, 0.08)
    batch["hall_glow"].box(frame(((x0 + x1) / 2, y - 0.6, (z0 + z1) / 2), (1, 0, 0), (0, 1, 0), (0, 0, 1)), x1 - x0, 0.05, z1 - z0)
    for i, xm in enumerate((-5.0, -1.6, 2.0, 5.2)):
        batch["pipe"].cyl(frame((xm, y - 0.5, 1.8), (1, 0, 0), (0, 0, 1), (0, -1, 0)), 0.55 + 0.1 * (i % 2), 1.6, 18)
    batch["pipe"].box(frame((0, y - 0.45, 1.25), (1, 0, 0), (0, 1, 0), (0, 0, 1)), 13.0, 0.12, 0.25)


def doors(batch: Batch) -> None:
    """Two panelled doors, one in each wing wall, with frames and handles."""
    for sx in (-1, 1):
        x = sx * (X_EDGE - 0.02)
        T = frame((x, 0.5, 1.05), (0, sx, 0), (0, 0, 1), (-sx, 0, 0))
        batch["wood"].box(T, 1.1, 0.06, 2.1)
        batch["paint_dark"].box(frame((x - sx * 0.01, 0.5, 1.12), (0, 1, 0), (0, 0, 1), (1, 0, 0)), 1.3, 0.04, 2.3)
        batch["chrome"].box(frame((x - sx * 0.07, 0.0, 1.0), (0, 1, 0), (0, 0, 1), (1, 0, 0)), 0.14, 0.03, 0.03)


def pillars(batch: Batch) -> None:
    """Two concrete columns that break up the floor."""
    for x, y in ((-5.0, -3.0), (5.0, -3.0)):
        batch["wall"].box(frame((x, y, CEIL / 2), (1, 0, 0), (0, 1, 0), (0, 0, 1)), 0.7, 0.7, CEIL)


def lights_and_cameras(spots, marks: dict[str, tuple[Vector, Vector]]) -> None:
    """Empties the viewer reads: LIGHT_n at ceiling panels, CAM_<name> and CAMTARGET_<name> for the bookmarks."""
    for i, (x, y) in enumerate(spots):
        if (i % 3) == 0:
            empty(f"LIGHT_{i}", Matrix.Translation((x, y, CEIL - 0.3)))
    for name, (pos, target) in marks.items():
        empty(f"CAM_{name}", Matrix.Translation(pos))
        empty(f"CAMTARGET_{name}", Matrix.Translation(target))


def hall_material() -> None:
    """Make sure the shell's named materials exist."""
    for key in ("wall", "ceiling", "wood", "paint_dark", "chrome", "pipe", "light_panel", "hall_glow"):
        named_material(key)
