"""Builds room.glb: the RBMK-1000 unit control room, an interpretive reconstruction.

Run from the repository root:
    blender --background --python examples/rbmk/generator/build_room.py -- --out examples/rbmk/room/room.glb.gz
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import sys
from pathlib import Path

import bpy
from mathutils import Vector

HERE = Path(os.path.abspath(__file__)).parent
sys.path.insert(0, str(HERE))

import room_desk  # noqa: E402
import room_shell  # noqa: E402
import room_wall  # noqa: E402
from roomkit import Batch, reset_materials  # noqa: E402

SPEC_PATH = HERE.parent / "room" / "instruments.json"


def source_hash() -> str:
    """Digest of the generator sources and the instrument list; stored in the GLB so a stale file is detectable."""
    h = hashlib.sha256()
    for p in sorted(HERE.glob("*.py")) + [SPEC_PATH]:
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def bookmarks() -> dict[str, tuple[Vector, Vector]]:
    """Camera position and target for each named bookmark."""
    az = room_desk.desk_frame(*room_desk.AZ5_AT).translation
    orm = Vector((room_desk.ORM_DESK[0] - 0.15, room_desk.ORM_DESK[1] - 0.29, 0.93))
    return {
        "overview": (Vector((0.0, -1.8, 2.3)), Vector((0.0, 7.0, 1.6))),
        "az5": (Vector((az.x + 0.12, az.y - 0.62, az.z + 0.5)), Vector((az.x, az.y, az.z + 0.06))),
        "rodpanel": (Vector((0.0, 4.6, 1.95)), Vector((0.0, 9.0, 2.05))),
        "orm": (Vector((orm.x + 0.45, orm.y - 1.0, 1.3)), orm),
        "dials": (Vector((2.75, 6.3, 2.1)), Vector((3.66, 8.22, 2.3))),
        "alarms": (Vector((-1.0, 5.9, 2.0)), Vector((-2.9, 8.5, 2.45))),
        "decision": (Vector((-3.75, 4.65, 1.9)), Vector((-5.9, 6.8, 2.2))),
    }


def make_scene(chairs: bool = True) -> list[tuple[float, float]]:
    """Build the whole room in an empty Blender scene (without the chairs when asked); returns the floor outline."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    reset_materials()
    spec = json.loads(SPEC_PATH.read_text())
    batch = Batch()
    room_shell.hall_material()
    outline = room_shell.shell(batch)
    spots = room_shell.light_panels(batch)
    room_shell.hall_windows(batch)
    room_shell.doors(batch)
    room_shell.pillars(batch)
    room_wall.panels(batch)
    room_wall.instruments(batch, spec)
    room_wall.rod_map(batch)
    room_wall.mimic_windows(batch)
    room_desk.desk(batch)
    room_desk.static_controls(batch)
    room_desk.az5(batch)
    if chairs:
        room_desk.chairs(batch)
    room_desk.orm_terminal(batch)
    room_desk.furniture(batch)
    batch.flush()
    room_shell.lights_and_cameras(spots, bookmarks())
    scene = bpy.context.scene
    scene["build_hash"] = source_hash()
    scene["interpretive"] = "Interpretive reconstruction from published descriptions; not a survey."
    scene["outline"] = json.dumps([(round(x, 3), round(y, 3)) for x, y in outline])
    return outline


def build(out: Path) -> None:
    """Build the whole room and export it."""
    make_scene()
    out.parent.mkdir(parents=True, exist_ok=True)
    raw = out.with_suffix("")
    bpy.ops.export_scene.gltf(filepath=str(raw), export_format="GLB", export_yup=True, export_apply=True,
                              export_extras=True, export_cameras=False, export_lights=False,
                              export_image_format="AUTO")
    with gzip.GzipFile(out, "wb", compresslevel=9, mtime=0) as fh:
        fh.write(raw.read_bytes())


def main() -> None:
    """Parse the arguments after ``--`` and build."""
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else HERE.parent / "room" / "room.glb.gz"
    build(out)
    sys.stdout.write(f"wrote {out} ({out.stat().st_size} bytes), hash {source_hash()[:12]}\n")


if __name__ == "__main__":
    main()
