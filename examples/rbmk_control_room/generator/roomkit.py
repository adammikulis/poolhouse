"""Solid, material and frame helpers for the control-room generator. Runs inside Blender (bpy)."""

from __future__ import annotations

import math

import bpy
from mathutils import Matrix, Vector

COLORS = {
    "paint": (0.50, 0.56, 0.53), "paint_dark": (0.30, 0.34, 0.33), "plinth": (0.22, 0.25, 0.25),
    "desk_top": (0.36, 0.44, 0.52), "desk_side": (0.80, 0.79, 0.72), "desk_kick": (0.08, 0.09, 0.10),
    "plate": (0.12, 0.13, 0.14), "chrome": (0.62, 0.64, 0.66), "case": (0.74, 0.75, 0.72),
    "black": (0.02, 0.02, 0.025), "glass_dark": (0.02, 0.05, 0.06), "wall": (0.80, 0.80, 0.76),
    "ceiling": (0.86, 0.86, 0.82), "wood": (0.34, 0.20, 0.10), "chair": (0.03, 0.03, 0.035),
    "key_white": (0.82, 0.82, 0.78), "key_yellow": (0.80, 0.62, 0.08), "key_green": (0.08, 0.45, 0.22),
    "key_red": (0.65, 0.07, 0.06), "switch": (0.04, 0.04, 0.05), "metal_box": (0.38, 0.40, 0.40),
    "hall": (0.55, 0.62, 0.66), "pipe": (0.35, 0.40, 0.42), "tile_label": (0.90, 0.88, 0.70),
}
EMISSIVE = {"light_panel": ((1.0, 0.98, 0.92), 6.0), "hall_glow": ((0.70, 0.82, 0.90), 1.6)}

_MATS: dict[str, bpy.types.Material] = {}


def material(name: str, rgb=(0.5, 0.5, 0.5), rough: float = 0.6, metal: float = 0.0,
             emit=None, strength: float = 1.0) -> bpy.types.Material:
    """Create (once) a Principled material that exports to glTF."""
    if name in _MATS:
        return _MATS[name]
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*(c ** 2.2 for c in rgb), 1.0)
    bsdf.inputs["Roughness"].default_value = rough
    bsdf.inputs["Metallic"].default_value = metal
    if emit is not None:
        bsdf.inputs["Emission Color"].default_value = (*(c ** 2.2 for c in emit), 1.0)
        bsdf.inputs["Emission Strength"].default_value = strength
    _MATS[name] = mat
    return mat


def named_material(key: str) -> bpy.types.Material:
    """The palette material called ``key`` (a COLORS or EMISSIVE entry)."""
    if key in EMISSIVE:
        rgb, strength = EMISSIVE[key]
        return material("M_" + key, rgb, 0.4, 0.0, rgb, strength)
    metal = 0.8 if key in ("chrome", "metal_box") else 0.0
    rough = 0.35 if key in ("chrome", "glass_dark") else 0.7
    return material("M_" + key, COLORS[key], rough, metal)


def frame(pos, x, y, z) -> Matrix:
    """A 4x4 transform with the given origin and axis vectors as columns."""
    x, y, z = Vector(x), Vector(y), Vector(z)
    return Matrix([[x.x, y.x, z.x, pos[0]], [x.y, y.y, z.y, pos[1]], [x.z, y.z, z.z, pos[2]], [0, 0, 0, 1]])


def wall_frame(theta_deg: float, z: float, radius: float, lift: float = 0.0) -> Matrix:
    """Frame on the arc wall at angle ``theta_deg``: X to the viewer's right, Y up, Z into the room."""
    t = math.radians(theta_deg)
    c, s = math.cos(t), math.sin(t)
    return frame((radius * c - lift * c, radius * s - lift * s, z), (s, -c, 0), (0, 0, 1), (-c, -s, 0))


class Solid:
    """Accumulates vertices, faces and per-corner UVs; ``build`` turns it into one Blender object."""

    def __init__(self) -> None:
        self.v: list[tuple[float, float, float]] = []
        self.f: list[tuple[int, ...]] = []
        self.uv: list[list[tuple[float, float]]] = []

    def _add(self, T: Matrix, pts, faces, uvs=None) -> None:
        base = len(self.v)
        self.v += [tuple(T @ Vector(p)) for p in pts]
        for i, f in enumerate(faces):
            self.f.append(tuple(base + k for k in f))
            self.uv.append(uvs[i] if uvs else [(0.0, 0.0)] * len(f))

    def box(self, T: Matrix, sx: float, sy: float, sz: float) -> None:
        """A box centred on the origin of ``T``."""
        x, y, z = sx / 2, sy / 2, sz / 2
        pts = [(-x, -y, -z), (x, -y, -z), (x, y, -z), (-x, y, -z), (-x, -y, z), (x, -y, z), (x, y, z), (-x, y, z)]
        faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (2, 3, 7, 6), (1, 2, 6, 5), (3, 0, 4, 7)]
        self._add(T, pts, faces)

    def prism(self, T: Matrix, pts_bottom, pts_top) -> None:
        """A closed hull between two quads (given counter-clockwise seen from above)."""
        pts = list(pts_bottom) + list(pts_top)
        faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
        self._add(T, pts, faces)

    def cyl(self, T: Matrix, r: float, h: float, n: int = 14, r_top: float | None = None) -> None:
        """A cylinder (or cone frustum) along local Z, centred on the origin of ``T``."""
        rt = r if r_top is None else r_top
        pts = [(r * math.cos(2 * math.pi * i / n), r * math.sin(2 * math.pi * i / n), -h / 2) for i in range(n)]
        pts += [(rt * math.cos(2 * math.pi * i / n), rt * math.sin(2 * math.pi * i / n), h / 2) for i in range(n)]
        faces = [tuple(range(n - 1, -1, -1)), tuple(range(n, 2 * n))]
        faces += [(i, (i + 1) % n, n + (i + 1) % n, n + i) for i in range(n)]
        self._add(T, pts, faces)

    def quad(self, T: Matrix, w: float, h: float) -> None:
        """A quad in the local XY plane facing +Z, with UV (0,0) bottom left to (1,1) top right."""
        pts = [(-w / 2, -h / 2, 0), (w / 2, -h / 2, 0), (w / 2, h / 2, 0), (-w / 2, h / 2, 0)]
        self._add(T, pts, [(0, 1, 2, 3)], [[(0, 0), (1, 0), (1, 1), (0, 1)]])

    def poly(self, pts, uv_scale: float, flip: bool = False, z: float = 0.0) -> None:
        """A horizontal fan polygon around the origin with world-planar UVs."""
        n = len(pts)
        pts3 = [(0.0, 0.0, z)] + [(p[0], p[1], z) for p in pts]
        faces = []
        for i in range(n):
            a, b = 1 + i, 1 + (i + 1) % n
            faces.append((0, b, a) if flip else (0, a, b))
        uvs = [[(p[0] / uv_scale, p[1] / uv_scale) for p in (pts3[k] for k in f)] for f in faces]
        self._add(Matrix.Identity(4), pts3, faces, uvs)

    def build(self, name: str, mat: bpy.types.Material, parent=None, local: Matrix | None = None):
        """Create the Blender object (None when empty)."""
        if not self.f:
            return None
        me = bpy.data.meshes.new(name)
        me.from_pydata(self.v, [], self.f)
        me.update()
        uv = me.uv_layers.new(name="UVMap")
        k = 0
        for poly_uvs in self.uv:
            for u in poly_uvs:
                uv.data[k].uv = u
                k += 1
        me.materials.append(mat)
        ob = bpy.data.objects.new(name, me)
        bpy.context.scene.collection.objects.link(ob)
        if parent is not None:
            ob.parent = parent
            ob.matrix_basis = local if local is not None else Matrix.Identity(4)
        return ob


class Batch:
    """Static geometry grouped by material key; each group becomes one object."""

    def __init__(self) -> None:
        self.groups: dict[str, Solid] = {}

    def __getitem__(self, key: str) -> Solid:
        return self.groups.setdefault(key, Solid())

    def flush(self) -> None:
        """Create one object per material."""
        for key, solid in self.groups.items():
            solid.build("STATIC_" + key, named_material(key))


def empty(name: str, T: Matrix, parent=None):
    """An Empty with the given world (or parent-local) transform."""
    ob = bpy.data.objects.new(name, None)
    ob.empty_display_size = 0.05
    bpy.context.scene.collection.objects.link(ob)
    if parent is not None:
        ob.parent = parent
    ob.matrix_basis = T
    return ob
