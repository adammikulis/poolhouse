"""Renders the fixed-viewpoint stills of the room and writes the image-space position of every live instrument.

Run from the repository root:
    blender --background --factory-startup --python examples/rbmk_control_room/generator/render_views.py -- --out examples/rbmk_control_room/viewer/stills
"""

from __future__ import annotations

import json
import math
import os
import sys
from functools import partial
from pathlib import Path

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Matrix, Vector

HERE = Path(os.path.abspath(__file__)).parent
sys.path.insert(0, str(HERE))

import build_room  # noqa: E402
from views import panel_views, whole_views  # noqa: E402

DYNAMIC = ("__needle", "__cap", "__cover", "__label", "__face")


def set_hidden(ob, hidden: bool) -> None:
    """Hide or show an object for both the render and the ray tests."""
    ob.hide_render = hidden
    ob.hide_viewport = hidden


def hide_dynamic() -> None:
    """Hide the parts the page draws live: instrument faces, needles, the AZ-5 cap, cover and label, and the rod cells."""
    for ob in bpy.data.objects:
        if ob.name.startswith(("ROD_", "MIMIC_")) or any(ob.name.endswith(s) for s in DYNAMIC):
            set_hidden(ob, True)


def stone_floor() -> None:
    """Give the floor an irregular stone tiling: random cell tones with dark joints."""
    nt = bpy.data.materials["M_floor"].node_tree
    nodes, links = nt.nodes, nt.links
    bsdf = nodes["Principled BSDF"]
    coord = nodes.new("ShaderNodeTexCoord")
    cells, joints = nodes.new("ShaderNodeTexVoronoi"), nodes.new("ShaderNodeTexVoronoi")
    cells.voronoi_dimensions = joints.voronoi_dimensions = "3D"
    cells.inputs["Scale"].default_value = joints.inputs["Scale"].default_value = 2.0
    joints.feature = "DISTANCE_TO_EDGE"
    tone, mortar = nodes.new("ShaderNodeValToRGB"), nodes.new("ShaderNodeValToRGB")
    grey = nodes.new("ShaderNodeRGBToBW")
    tone.color_ramp.elements[0].color, tone.color_ramp.elements[1].color = (0.78, 0.77, 0.73, 1), (1, 1, 1, 1)
    mortar.color_ramp.elements[0].position, mortar.color_ramp.elements[1].position = 0.0, 0.05
    mortar.color_ramp.elements[0].color, mortar.color_ramp.elements[1].color = (0.35, 0.34, 0.32, 1), (1, 1, 1, 1)
    mix = nodes.new("ShaderNodeMix")
    mix.data_type, mix.blend_type = "RGBA", "MULTIPLY"
    mix.inputs[0].default_value = 1.0
    links.new(coord.outputs["Object"], cells.inputs["Vector"])
    links.new(coord.outputs["Object"], joints.inputs["Vector"])
    links.new(cells.outputs["Color"], grey.inputs["Color"])
    links.new(grey.outputs["Val"], tone.inputs["Fac"])
    links.new(joints.outputs["Distance"], mortar.inputs["Fac"])
    links.new(tone.outputs["Color"], mix.inputs[6])
    links.new(mortar.outputs["Color"], mix.inputs[7])
    links.new(mix.outputs[2], bsdf.inputs["Base Color"])


def lighting(plan: bool) -> None:
    """World light and a few soft area lights; the plan drops the ceiling and uses a sun."""
    world = bpy.data.worlds.new("W") if not bpy.context.scene.world else bpy.context.scene.world
    bpy.context.scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (0.5, 0.52, 0.55, 1.0)
    bg.inputs["Strength"].default_value = 0.7 if plan else 0.35
    bpy.context.scene.view_settings.exposure = -0.4 if plan else -0.3
    for ob in [o for o in bpy.data.objects if o.type == "LIGHT"]:
        bpy.data.objects.remove(ob)
    if plan:
        sun = bpy.data.lights.new("sun", "SUN")
        sun.energy = 1.6
        ob = bpy.data.objects.new("sun", sun)
        ob.rotation_euler = (math.radians(35), math.radians(10), math.radians(25))
        bpy.context.scene.collection.objects.link(ob)
        return
    for x, y in ((-4.5, 5.0), (0.0, 5.0), (4.5, 5.0), (-2.0, 1.0), (2.0, 1.0)):
        area = bpy.data.lights.new("fill", "AREA")
        area.energy, area.size, area.size_y, area.shape = 110.0, 3.0, 1.5, "RECTANGLE"
        ob = bpy.data.objects.new("fill", area)
        ob.location = (x, y, 3.45)
        ob.visible_camera = False
        bpy.context.scene.collection.objects.link(ob)


def hide_ceiling(hidden: bool) -> None:
    """Show or hide the ceiling and its light panels."""
    for ob in bpy.data.objects:
        if ob.name in ("CEILING", "STATIC_light_panel", "STATIC_ceiling"):
            set_hidden(ob, hidden)


def configure(scale: float, samples: int) -> None:
    """Render settings: Cycles on the CPU, a flat view transform, WebP output."""
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = samples
    sc.cycles.use_denoising = True
    sc.cycles.max_bounces = 4
    sc.view_settings.view_transform = "Standard"
    sc.render.film_transparent = False
    sc.render.image_settings.file_format = "WEBP"
    sc.render.image_settings.quality = 84
    sc.render.resolution_percentage = round(scale * 100)


class Shot:
    """One orthographic camera: placement, projection of world points to image pixels, and a visibility test."""

    def __init__(self, name: str, size: tuple[int, int], aim: tuple[Vector, Vector],
                 mid: tuple[float, float], span: float, cut: Vector, dist: float = 40.0) -> None:
        self.name, (self.w, self.h) = name, size
        forward, up_hint = aim
        u_mid, v_mid = mid
        self.forward = forward.normalized()
        self.right = self.forward.cross(up_hint).normalized()
        self.up = self.right.cross(self.forward).normalized()
        data = bpy.data.cameras.new(name)
        data.type, data.ortho_scale, data.sensor_fit = "ORTHO", span, "HORIZONTAL"
        self.loc = self.right * u_mid + self.up * v_mid - self.forward * dist
        data.clip_start = max(0.01, (cut - self.loc).dot(self.forward))
        data.clip_end = dist + 40.0
        self.cam = bpy.data.objects.new(name, data)
        rot = Matrix(((self.right.x, self.up.x, -self.forward.x), (self.right.y, self.up.y, -self.forward.y),
                      (self.right.z, self.up.z, -self.forward.z)))
        self.cam.matrix_world = Matrix.Translation(self.loc) @ rot.to_4x4()
        bpy.context.scene.collection.objects.link(self.cam)

    def project(self, co: Vector) -> tuple[float, float]:
        """Image pixel (x right, y down) of a world point."""
        v = world_to_camera_view(bpy.context.scene, self.cam, co)
        return v.x * self.w, (1.0 - v.y) * self.h

    def inside(self, pt: tuple[float, float]) -> bool:
        """Whether a pixel lies in the image."""
        return 0 <= pt[0] <= self.w and 0 <= pt[1] <= self.h

    def exposed(self, co: Vector, normal: Vector) -> bool:
        """Whether the surface point faces the camera and nothing is in front of it."""
        if normal.dot(self.forward) > -0.04:
            return False
        back = (co - self.loc).dot(self.forward) - self.cam.data.clip_start
        origin = co - self.forward * min(max(back, 0.0), 80.0)
        hit, loc, _n, _i, _o, _m = bpy.context.scene.ray_cast(bpy.context.view_layer.depsgraph, origin, self.forward, distance=80.0)
        return (not hit) or (co - loc).dot(self.forward) < 0.02

    def select(self) -> None:
        """Make this the scene camera at its image size (projection reads both)."""
        sc = bpy.context.scene
        sc.camera = self.cam
        sc.render.resolution_x, sc.render.resolution_y = self.w, self.h
        bpy.context.view_layer.update()

    def render(self, out: Path) -> None:
        """Render this camera to ``out``."""
        sc = bpy.context.scene
        self.select()
        sc.render.filepath = str(out)
        bpy.ops.render.render(write_still=True)


def face_corners(ob) -> list[Vector] | None:
    """World corners (top-left, top-right, bottom-right, bottom-left) of an object's front face."""
    box = [Vector(c) for c in ob.bound_box]
    xs, ys = [c.x for c in box], [c.y for c in box]
    z = max(c.z for c in box)
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    return [ob.matrix_world @ Vector(p) for p in ((x0, y1, z), (x1, y1, z), (x1, y0, z), (x0, y0, z))]


def measure(shot: Shot, corners: list[Vector], normal: Vector) -> list[float] | None:
    """The corners as eight image numbers, or None when the face is not shown in this shot."""
    centre = sum(corners, Vector()) / 4
    pts = [shot.project(c) for c in corners]
    if not any(shot.inside(p) for p in pts) or not shot.exposed(centre, normal):
        return None
    return [round(v, 1) for p in pts for v in p]


def normal_of(ob) -> Vector:
    """World normal of the object's local +Z axis."""
    return (ob.matrix_world.to_3x3() @ Vector((0, 0, 1))).normalized()


def instruments_in(shot: Shot, spec: dict) -> dict[str, list[float]]:
    """Image corners of every visible instrument face, keyed by instrument id."""
    out: dict[str, list[float]] = {}
    for ins in spec["instruments"]:
        ob = bpy.data.objects.get(f"INS_{ins['id']}__face")
        if ins["type"] == "button":
            root = bpy.data.objects[f"INS_{ins['id']}"]
            m = root.matrix_world
            corners = [m @ Vector(p) for p in ((-0.07, 0.07, 0.06), (0.07, 0.07, 0.06), (0.07, -0.07, 0.06), (-0.07, -0.07, 0.06))]
            q = measure(shot, corners, (m.to_3x3() @ Vector((0, 0, 1))).normalized())
        elif ob is not None:
            q = measure(shot, face_corners(ob), normal_of(ob))
        else:
            continue
        if q:
            out[ins["id"]] = q
    return out


def cells_in(shot: Shot, prefix: str) -> dict[str, list[float]]:
    """Image corners of every visible object whose name starts with ``prefix``, keyed by the rest of the name."""
    out: dict[str, list[float]] = {}
    for ob in bpy.data.objects:
        if ob.name.startswith(prefix):
            q = measure(shot, face_corners(ob), normal_of(ob))
            if q:
                out[ob.name[len(prefix):]] = q
    return out


def rect_of(shot: Shot, points: list[Vector], pad: float) -> list[float] | None:
    """The padded bounding box (x, y, w, h) of world points in the image, clipped to it."""
    pts = [shot.project(p) for p in points]
    x0, x1 = max(0.0, min(p[0] for p in pts) - pad), min(float(shot.w), max(p[0] for p in pts) + pad)
    y0, y1 = max(0.0, min(p[1] for p in pts) - pad), min(float(shot.h), max(p[1] for p in pts) + pad)
    return [round(x0, 1), round(y0, 1), round(x1 - x0, 1), round(y1 - y0, 1)] if x1 > x0 and y1 > y0 else None


def world_pts(ob) -> list[Vector]:
    """The eight bound-box corners of an object in world space."""
    return [ob.matrix_world @ Vector(c) for c in ob.bound_box]


def panel_points(spec: dict, panel: str) -> list[Vector]:
    """World points that enclose a panel's instruments."""
    obs: list = []
    zone = spec["panels"][panel].get("zone")
    if panel == "rodpanel":
        obs = [o for o in bpy.data.objects if o.name.startswith("ROD_")]
    elif panel == "az5":
        obs = [bpy.data.objects["INS_AZ5_BUTTON"]]
    elif panel == "orm":
        obs = [bpy.data.objects["INS_SCREEN_ORM__face"]]
    else:
        obs = [bpy.data.objects[f"INS_{i['id']}__face"] for i in spec["instruments"] if i["zone"] == zone]
    pts = [p for o in obs for p in world_pts(o)]
    if panel == "az5":
        c = obs[0].matrix_world.translation
        pts = [c + Vector(d) for d in ((-0.1, -0.1, 0), (0.1, 0.1, 0.1))]
    return pts


def describe(shot: Shot, spec: dict, image: str, whole: bool) -> dict:
    """The JSON record of one shot: size, image, panel rects, and instrument corners where the page overlays them."""
    panels = {}
    for pid, panel in spec["panels"].items():
        r = rect_of(shot, panel_points(spec, pid), 10.0 if whole else 0.0)
        if r:
            panels[pid] = {"label": panel["label"], "rect": r}
    rec: dict = {"image": image, "width": shot.w, "height": shot.h, "panels": panels, "items": {}, "rods": {}, "mimics": {}}
    if shot.name != "plan":
        rec["items"] = instruments_in(shot, spec)
        rec["rods"] = cells_in(shot, "ROD_")
        rec["mimics"] = cells_in(shot, "MIMIC_")
    return rec


def build_variant(chairs: bool) -> dict:
    """Make a fresh scene with the dynamic parts hidden; returns the instrument list."""
    build_room.make_scene(chairs=chairs)
    spec = json.loads(build_room.SPEC_PATH.read_text())
    bpy.context.view_layer.update()
    hide_dynamic()
    stone_floor()
    hide_ceiling(True)
    bpy.context.view_layer.update()
    return spec


MASK_STEP = 36


def mask_rgb(index: int) -> tuple[int, int, int]:
    """The 8-bit colour of mask index ``index`` (three base-8 digits spread over the channels)."""
    return ((index >> 6) & 7) * MASK_STEP, ((index >> 3) & 7) * MASK_STEP, (index & 7) * MASK_STEP


def srgb_to_linear(v: float) -> float:
    """The linear value that displays as ``v`` (0 to 1) under the Standard view transform."""
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def mask_setup(spec: dict) -> dict[str, int]:
    """Switch the scene to flat, unlit object colours: each instrument face, rod cell and mimic window gets its own colour, everything else black."""
    targets = {}
    for ins in spec["instruments"]:
        ob = bpy.data.objects.get("STATIC_metal_box" if ins["type"] == "button" else f"INS_{ins['id']}__face")
        if ob is not None:
            targets[ins["id"]] = ob
    for ob in bpy.data.objects:
        if ob.name.startswith(("ROD_", "MIMIC_")):
            targets[ob.name] = ob
    ids = {name: i + 1 for i, name in enumerate(sorted(targets))}
    for ob in bpy.data.objects:
        ob.color = (0.0, 0.0, 0.0, 1.0)
    for name, ob in targets.items():
        ob.color = (*(srgb_to_linear(c / 255) for c in mask_rgb(ids[name])), 1.0)
        set_hidden(ob, False)
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.display.shading.light, sc.display.shading.color_type = "FLAT", "OBJECT"
    sc.display.shading.show_object_outline = sc.display.shading.show_cavity = False
    sc.display.render_aa = "OFF"
    sc.view_settings.exposure = 0.0
    sc.render.dither_intensity = 0.0
    sc.world = sc.world or bpy.data.worlds.new("W")
    sc.world.color = (0.0, 0.0, 0.0)
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGB"
    return ids


def main() -> None:
    """Render every view and write views.json."""
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else HERE.parent / "viewer" / "stills"
    scale = float(argv[argv.index("--scale") + 1]) if "--scale" in argv else 1.0
    samples = int(argv[argv.index("--samples") + 1]) if "--samples" in argv else 96
    only = set(argv[argv.index("--only") + 1].split(",")) if "--only" in argv else None
    keep_images = "--keep-images" in argv
    out.mkdir(parents=True, exist_ok=True)
    kept = out / "views.json"
    before = json.loads(kept.read_text()) if only and kept.exists() else {}
    views: dict[str, dict] = before.get("views", {})
    mask_ids: dict[str, int] = before.get("mask_ids", {})
    (out / "masks").mkdir(exist_ok=True)
    for chairs in (True, False):
        spec = build_variant(chairs)
        configure(scale, samples)
        done: list[Shot] = []
        shots = whole_views(Shot, spec, chairs) if chairs else [*whole_views(Shot, spec, chairs), *panel_views(Shot, spec, partial(panel_points, spec))]
        for name, shot, plan in shots:
            if only and name not in only:
                continue
            shot.select()
            if not keep_images:
                lighting(plan)
                shot.render(out / f"{name}.webp")
            views[name] = describe(shot, spec, f"{name}.webp", plan or name == "front")
            done.append(shot)
            sys.stdout.write(f"rendered {name}\n")
        mask_ids.update(mask_setup(spec))
        for shot in done:
            if shot.name != "plan":
                shot.render(out / "masks" / f"{shot.name}.png")
    doc = {"format": 1, "build_hash": build_room.source_hash(), "mask_ids": mask_ids, "views": views}
    (out / "views.json").write_text(json.dumps(doc, separators=(",", ":")))
    sys.stdout.write(f"wrote {out / 'views.json'}\n")


if __name__ == "__main__":
    main()
