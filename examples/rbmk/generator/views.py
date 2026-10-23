"""The fixed cameras of the stills: the floor plan, the front wall, and a close-up of each panel."""

from __future__ import annotations

import math
from collections.abc import Iterator

from mathutils import Vector

import room_desk
from room_wall import ROD_CENTER

PITCH = math.radians(15.0)
PANEL_SIZE = (1600, 1000)
MIN_SPAN = {"az5": 0.5, "orm": 1.1, "rodpanel": 3.2}


def whole_views(shot_cls, spec: dict, with_chairs: bool) -> Iterator[tuple[str, object, bool]]:
    """The floor plan (from above, north up; with the chairs) or the front wall (straight on, tilted down to show the desk)."""
    if with_chairs:
        yield "plan", shot_cls("plan", (1800, 1640), (Vector((0, 0, -1)), Vector((0, 1, 0))), (0.0, 1.95), 23.0, Vector((0, 0, 5))), True
        return
    forward = Vector((0, math.cos(PITCH), -math.sin(PITCH)))
    yield "front", shot_cls("front", (3200, 1160), (forward, Vector((0, 0, 1))), (0.0, 3.1), 16.0, Vector((0, 1.2, 0))), False


def extent(points: list[Vector], right: Vector, up: Vector) -> tuple[float, float, float, float]:
    """Centre and size of the points along the right and up axes: (u_mid, v_mid, width, height)."""
    us, vs = [p.dot(right) for p in points], [p.dot(up) for p in points]
    return (min(us) + max(us)) / 2, (min(vs) + max(vs)) / 2, max(us) - min(us), max(vs) - min(vs)


def wall_direction(theta_deg: float) -> Vector:
    """Horizontal unit vector from the room toward the arc wall at the angle."""
    t = math.radians(theta_deg)
    return Vector((math.cos(t), math.sin(t), 0.0))


def panel_views(shot_cls, spec: dict, points_of) -> Iterator[tuple[str, object, bool]]:
    """One orthographic close-up per panel, facing it; ``points_of(panel)`` gives the panel's world points."""
    up_hint = Vector((0, 0, 1))
    zones = spec["zones"]
    facing = {pid: wall_direction(zones[p["zone"]]["theta"]) for pid, p in spec["panels"].items() if "zone" in p}
    down = math.radians(38.0)
    facing["rodpanel"] = wall_direction(ROD_CENTER[0]) * math.cos(down) + Vector((0, 0, -math.sin(down)))
    facing["orm"] = Vector((0, 1, 0))
    az = room_desk.desk_frame(*room_desk.AZ5_AT)
    normal = (az.to_3x3() @ Vector((0, 0, 1))).normalized()
    toward_crew = -wall_direction(room_desk.AZ5_AT[0])
    tilt = math.radians(30.0)
    facing["az5"] = -(normal * math.cos(tilt) + toward_crew * math.sin(tilt))
    for pid in spec["panels"]:
        f = facing[pid].normalized()
        right = f.cross(up_hint).normalized()
        up = right.cross(f).normalized()
        pts = points_of(pid)
        u, v, w, h = extent(pts, right, up)
        span = max(w * 1.25, h * 1.25 * PANEL_SIZE[0] / PANEL_SIZE[1], MIN_SPAN.get(pid, 0.0))
        mid = sum(pts, Vector()) / len(pts)
        yield pid, shot_cls(pid, PANEL_SIZE, (f, up_hint), (u, v), span, mid - f * 3.0), False
