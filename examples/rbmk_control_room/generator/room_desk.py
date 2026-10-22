"""The control desk, its static controls, the AZ-5 box, chairs, the engineer's desks and the ORM terminal."""

from __future__ import annotations

import math

from mathutils import Matrix, Vector

from roomkit import Batch, Solid, empty, frame, material
from room_wall import rod_cells

R_FRONT, R_BACK = 6.3, 7.5
H_FRONT, SLOPE = 0.75, 0.25
DESK_THETA0, DESK_THETA1, DESK_STEP = 50.0, 130.0, 5.0
AZ5_AT = (88.0, 6.62)
KEYS_AT = (73.0, 6.78)
ORM_DESK = (-3.6, 2.4)


def top_z(r: float) -> float:
    """Height of the sloped desk top at plan radius ``r``."""
    return H_FRONT + SLOPE * (r - R_FRONT)


def desk_frame(theta: float, r: float, lift: float = 0.0) -> Matrix:
    """Frame on the sloped top at (theta, r): X to the right, Y up the slope, Z the surface normal."""
    t = math.radians(theta)
    c, s = math.cos(t), math.sin(t)
    sl = Vector((c, s, SLOPE)).normalized()
    x = Vector((s, -c, 0.0))
    z = x.cross(sl)
    return frame((r * c, r * s, top_z(r) + lift), x, sl, z)


def _sector(a: float, b: float, r1: float, r2: float, z_bot, z_top):
    pts = []
    for r, th in ((r1, a), (r1, b), (r2, b), (r2, a)):
        t = math.radians(th)
        pts.append((r * math.cos(t), r * math.sin(t), z_bot(r)))
    top = []
    for r, th in ((r1, a), (r1, b), (r2, b), (r2, a)):
        t = math.radians(th)
        top.append((r * math.cos(t), r * math.sin(t), z_top(r)))
    return pts, top


def desk(batch: Batch) -> None:
    """The long angled desk in 5 degree sections: cream body, blue-grey top, dark kick, edge strip."""
    n = round((DESK_THETA1 - DESK_THETA0) / DESK_STEP)
    ident = Matrix.Identity(4)
    for i in range(n):
        a = DESK_THETA0 + i * DESK_STEP
        b = a + DESK_STEP * 0.985
        for key, r1, r2, zb, zt in (("desk_kick", R_FRONT + 0.15, R_BACK - 0.1, lambda r: 0.0, lambda r: 0.09),
                                    ("desk_side", R_FRONT, R_BACK, lambda r: 0.09, lambda r: top_z(r) - 0.035),
                                    ("desk_top", R_FRONT, R_BACK, lambda r: top_z(r) - 0.035, lambda r: top_z(r))):
            bot, top = _sector(a, b, r1, r2, zb, zt)
            batch[key].prism(ident, bot, top)
        bot, top = _sector(a, b, R_FRONT - 0.03, R_FRONT, lambda r: top_z(R_FRONT) - 0.05, lambda r: top_z(R_FRONT) + 0.012)
        batch["chrome"].prism(ident, bot, top)


def static_controls(batch: Batch) -> None:
    """Static switches, knobs and push buttons along the desk, and the rod key matrix (none of them wired to the demo)."""
    palette = ["key_white"] * 5 + ["key_yellow", "key_green", "key_red"]
    th0, r0 = KEYS_AT
    base = desk_frame(th0, r0, 0.004)
    for i, j in rod_cells():
        k = (i * 7 + j * 13) % 9
        key = palette[k % 8] if k != 8 else "key_white"
        batch[key].box(base @ Matrix.Translation(((i - 8) * 0.037, (j - 8) * 0.037, 0.012)), 0.027, 0.027, 0.024)
    batch["plate"].box(base @ Matrix.Translation((0, 0, 0.002)), 0.68, 0.68, 0.004)
    for th in [DESK_THETA0 + 2 + 2.2 * i for i in range(35)]:
        if abs(th - th0) < 5.5:
            continue
        for r, h in ((6.5, 0.02), (6.62, 0.016), (6.95, 0.02)):
            kind = "switch" if int(th * 10) % 3 else "key_white"
            batch[kind].box(desk_frame(th, r, 0.0) @ Matrix.Translation((0, 0, 0.015)), 0.028, 0.028, 0.03)
    for th in [DESK_THETA0 + 4 + 5.5 * i for i in range(14)]:
        batch["plate"].box(desk_frame(th, 6.78, 0.0) @ Matrix.Translation((0, 0, 0.004)), 0.4, 0.14, 0.008)
        for c in range(6):
            batch["key_red" if (c + int(th)) % 5 == 0 else "key_white"].box(
                desk_frame(th + (c - 2.5) * 0.7, 6.78, 0.0) @ Matrix.Translation((0, 0, 0.012)), 0.034, 0.034, 0.024)


def az5(batch: Batch) -> None:
    """The AZ-5 box: metal housing, red button cap, hinged guard cover."""
    T = desk_frame(*AZ5_AT)
    batch["metal_box"].box(T @ Matrix.Translation((0, 0, 0.03)), 0.14, 0.14, 0.06)
    root = empty("INS_AZ5_BUTTON", T)
    cap = Solid()
    cap.cyl(Matrix.Identity(4), 0.032, 0.03, 16)
    cap.build("INS_AZ5_BUTTON__cap", material("M_az5_cap", (0.75, 0.04, 0.03), 0.4), root, Matrix.Translation((0, 0, 0.073)))
    cover = Solid()
    cover.box(Matrix.Translation((0, -0.07, 0)), 0.13, 0.14, 0.012)
    hinge = cover.build("INS_AZ5_BUTTON__cover", material("M_az5_cover", (0.42, 0.44, 0.44), 0.4, 0.8), root,
                        Matrix.Translation((0, 0.07, 0.106)))
    label = Solid()
    label.quad(Matrix.Identity(4), 0.1, 0.05)
    label.build("INS_AZ5_BUTTON__label", material("M_az5_label", (0.9, 0.9, 0.86), 0.5), hinge,
                Matrix.Translation((0, -0.07, 0.0065)))
    batch["paint_dark"].box(T @ Matrix.Translation((0, 0.09, 0.012)), 0.16, 0.018, 0.024)


def chair(batch: Batch, x: float, y: float, yaw_deg: float) -> None:
    """A swivel chair at (x, y) facing yaw_deg (0 = toward +Y)."""
    a = math.radians(yaw_deg)
    f = (-math.sin(a), math.cos(a), 0.0)
    r = (f[1], -f[0], 0.0)
    T = frame((x, y, 0), r, f, (0, 0, 1))
    for k in range(5):
        ang = 2 * math.pi * k / 5
        batch["chair"].box(T @ Matrix.Rotation(ang, 4, "Z") @ Matrix.Translation((0.14, 0, 0.06)), 0.28, 0.04, 0.025)
        batch["chrome"].cyl(T @ Matrix.Translation((0.27 * math.cos(ang), 0.27 * math.sin(ang), 0.03)), 0.025, 0.05, 8)
    batch["chrome"].cyl(T @ Matrix.Translation((0, 0, 0.27)), 0.025, 0.4, 8)
    batch["chair"].box(T @ Matrix.Translation((0, 0, 0.5)), 0.46, 0.44, 0.07)
    batch["chair"].box(T @ Matrix.Translation((0, -0.22, 0.8)) @ Matrix.Rotation(0.12, 4, "X"), 0.44, 0.05, 0.42)
    batch["chrome"].box(T @ Matrix.Translation((0, -0.2, 0.6)), 0.05, 0.05, 0.2)


def chairs(batch: Batch) -> None:
    """Operators' chairs at the desk and two at the engineer's desks."""
    for th, yaw in ((62, 6), (74, -10), (86, 14), (97, -4), (109, 12), (121, -8)):
        t = math.radians(th)
        chair(batch, 5.45 * math.cos(t), 5.45 * math.sin(t), math.degrees(t) - 90 + yaw)
    chair(batch, ORM_DESK[0] - 0.1, ORM_DESK[1] - 1.0, 5)
    chair(batch, 3.4, 1.4, 190)


def white_desk(batch: Batch, x: float, y: float, w: float = 1.6, d: float = 0.8) -> None:
    """A white writing desk with a dark pedestal."""
    ident = ((1, 0, 0), (0, 1, 0), (0, 0, 1))
    batch["key_white"].box(frame((x, y, 0.74), *ident), w, d, 0.04)
    batch["desk_kick"].box(frame((x + w / 2 - 0.22, y, 0.36), *ident), 0.4, d - 0.05, 0.7)
    batch["desk_side"].box(frame((x - w / 2 + 0.03, y, 0.36), *ident), 0.04, d - 0.05, 0.7)


def orm_terminal(batch: Batch) -> None:
    """The ORM engineer's desk: CRT terminal (screen INS_SCREEN_ORM), keyboard, phone, gooseneck lamp."""
    x, y = ORM_DESK
    white_desk(batch, x, y)
    T = frame((x - 0.15, y + 0.12, 0.76), (1, 0, 0), (0, 0, 1), (0, -1, 0))
    batch["case"].box(T @ Matrix.Translation((0, 0.17, 0.2)), 0.42, 0.36, 0.4)
    batch["case"].box(T @ Matrix.Translation((0, 0.2, -0.005)), 0.26, 0.02, 0.26)
    batch["plate"].box(T @ Matrix.Translation((0, 0.17, 0.403)), 0.36, 0.31, 0.01)
    root = empty("INS_SCREEN_ORM", frame((x - 0.15, y - 0.29, 0.93), (1, 0, 0), (0, 0, 1), (0, -1, 0)))
    face = Solid()
    face.quad(Matrix.Identity(4), 0.3, 0.22)
    face.build("INS_SCREEN_ORM__face", material("M_face_SCREEN_ORM", (0.02, 0.05, 0.05), 0.3), root)
    batch["plate"].box(frame((x - 0.15, y - 0.36, 0.775), (1, 0, 0), (0, 1, 0), (0, 0, 1)), 0.46, 0.12, 0.025)
    batch["key_white"].box(frame((x + 0.5, y - 0.15, 0.775), (1, 0, 0), (0, 1, 0), (0, 0, 1)), 0.2, 0.22, 0.06)
    batch["chair"].cyl(frame((x + 0.62, y + 0.25, 0.77), (1, 0, 0), (0, 1, 0), (0, 0, 1)), 0.06, 0.05, 12)
    batch["chair"].box(frame((x + 0.62, y + 0.25, 0.95), (1, 0, 0), (0, 1, 0), (0, 0, 1)), 0.03, 0.03, 0.4)
    batch["chair"].cyl(frame((x + 0.62, y + 0.15, 1.13), (1, 0, 0), (0, 1, 0), (0, 0, 1)), 0.08, 0.1, 12, 0.03)


def furniture(batch: Batch) -> None:
    """Remaining engineers' desks and a round table."""
    white_desk(batch, 3.5, 2.3)
    white_desk(batch, 0.4, 3.6, 1.8, 0.9)
    batch["key_white"].cyl(frame((-1.4, -0.6, 0.74), (1, 0, 0), (0, 1, 0), (0, 0, 1)), 0.6, 0.04, 24)
    batch["chrome"].cyl(frame((-1.4, -0.6, 0.37), (1, 0, 0), (0, 1, 0), (0, 0, 1)), 0.05, 0.72, 10)
    batch["chair"].cyl(frame((-1.4, -0.6, 0.012), (1, 0, 0), (0, 1, 0), (0, 0, 1)), 0.3, 0.025, 20)
    batch["plate"].box(frame((3.5, 2.3, 0.77), (1, 0, 0), (0, 1, 0), (0, 0, 1)), 0.45, 0.35, 0.03)
    batch["key_white"].box(frame((0.1, 3.6, 0.762), (1, 0, 0), (0, 1, 0), (0, 0, 1)), 0.4, 0.3, 0.004)
