"""Blender side of hardware/render/render.py: the boards on a studio table, rendered with Cycles.

    blender --background --factory-startup --python hardware/render/scene.py -- \
        --manifest build/boards/parts.json --quality preview|final --out FILE [--blend FILE]

Runs inside Blender 4.5 (tools/blender.sh). It imports the GLB that kicad-cli exported for each
board, gives each layer a material that looks like the real thing (the solder mask a clear-coated
film a little translucent over the copper, the pads hot-air levelled solder, the silkscreen
matte ink, the FR4 edge), cleans up the components' materials, builds a stand-in body for every
part with no 3D model (parts.json, `kind`), lays the boards out as in the photo all.jpeg and
renders. `--blend FILE` also saves the scene, for a person to open and adjust.

Units are metres, as glTF's. KiCad's y axis points down; the GLB's (and so these) points up.
"""
import argparse
import colorsys
import json
import math
import sys

import bpy
from mathutils import Euler, Matrix, Vector

MM = 0.001

# How each board looks, and where it lies on the table: x, y (m) and the turn about z (degrees).
BOARDS = {
    "base_board": {"mask": (0.010, 0.110, 0.040), "alpha": 0.9, "place": (-0.043, 0.010, 7.0)},
    "speaker_amp": {"mask": (0.006, 0.006, 0.007), "alpha": 0.985, "place": (0.043, -0.012, -5.0)},
}


# --- materials -----------------------------------------------------------------------------------

def principled(name, color, metallic=0.0, roughness=0.5, coat=0.0, coat_roughness=0.03, alpha=1.0,
               specular=0.5, noise_bump=0.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Specular IOR Level"].default_value = specular
    bsdf.inputs["Coat Weight"].default_value = coat
    bsdf.inputs["Coat Roughness"].default_value = coat_roughness
    bsdf.inputs["Alpha"].default_value = alpha
    if noise_bump:
        # A faint orange-peel, so a large flat surface catches the light unevenly, as a real one does.
        tex = nodes.new("ShaderNodeTexNoise")
        tex.inputs["Scale"].default_value = 1800.0
        tex.inputs["Detail"].default_value = 4.0
        bump = nodes.new("ShaderNodeBump")
        bump.inputs["Strength"].default_value = noise_bump
        bump.inputs["Distance"].default_value = 0.00002
        links.new(tex.outputs["Fac"], bump.inputs["Height"])
        links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
        if coat:
            links.new(bump.outputs["Normal"], bsdf.inputs["Coat Normal"])
    return mat


def board_materials(board):
    look = BOARDS[board]
    return {
        "soldermask": principled(f"{board} solder mask", look["mask"], roughness=0.35, coat=1.0,
                                 coat_roughness=0.06, alpha=look["alpha"], noise_bump=0.15),
        "soldermask_bottom": principled(f"{board} solder mask (bottom)", look["mask"], roughness=0.4, coat=0.8),
        "copper": principled(f"{board} copper", (0.93, 0.58, 0.40), metallic=1.0, roughness=0.32),
        "pad": principled(f"{board} solder (HASL)", (0.80, 0.80, 0.78), metallic=1.0, roughness=0.16, noise_bump=0.4),
        "silkscreen": principled(f"{board} silkscreen", (0.86, 0.86, 0.84), roughness=0.62, specular=0.3),
        "pcb": principled(f"{board} FR4", (0.30, 0.28, 0.17), roughness=0.55),
    }


def layer_of(obj):
    """speaker_amp_soldermask.001 -> ('soldermask', bottom): the layer meshes kicad-cli names."""
    name = obj.data.name
    for layer in ("soldermask", "silkscreen", "copper", "pad", "via", "PCB"):
        if f"_{layer}" in name:
            return layer, name.split(f"_{layer}")[1].startswith(".")
    return None, False


def tune_component_material(mat, cache):
    """kicad-cli marks most STEP colours metallic and rough: decide from the colour what it is."""
    if mat.name in cache or not mat.use_nodes:
        return
    cache.add(mat.name)
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if not bsdf:
        return
    r, g, b, _ = bsdf.inputs["Base Color"].default_value
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if v > 0.88 and s < 0.08:                       # white plastic, the bodies' markings
        metallic, rough = 0.0, 0.45
    elif s < 0.12 and v > 0.45:                     # tin, nickel, aluminium: leads, shields, cans
        metallic, rough = 1.0, 0.24
        bsdf.inputs["Base Color"].default_value = (max(r, 0.62), max(g, 0.62), max(b, 0.62), 1)
    elif 0.02 < h < 0.17 and s > 0.55 and v > 0.4:  # gold, copper wire
        metallic, rough = 1.0, 0.22
    elif v < 0.12 or (s < 0.12 and v <= 0.45):     # black epoxy, dark plastic
        metallic, rough = 0.0, 0.38
        bsdf.inputs["Base Color"].default_value = (0.018, 0.018, 0.02, 1)
    else:                                           # ceramic bodies, coloured plastic
        metallic, rough = 0.0, 0.5
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = rough


# --- stand-in bodies for parts without a model ---------------------------------------------------

def box(name, size, at, mat, bevel=0.0, parent=None):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=at)
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = size
    bpy.ops.object.transform_apply(scale=True)
    if bevel:
        mod = obj.modifiers.new("bevel", "BEVEL")
        mod.width = bevel
        mod.segments = 3
        mod.limit_method = "ANGLE"
    obj.data.materials.append(mat)
    for poly in obj.data.polygons:
        poly.use_smooth = bool(bevel)
    if parent:
        obj.parent = parent
    return obj


def cylinder(name, radius, depth, at, mat, parent=None, verts=48):
    bpy.ops.mesh.primitive_cylinder_add(radius=radius, depth=depth, location=at, vertices=verts)
    obj = bpy.context.active_object
    obj.name = name
    obj.data.materials.append(mat)
    if parent:
        obj.parent = parent
    return obj


class Stock:
    """The materials the stand-ins share."""

    def __init__(self):
        self.epoxy = principled("epoxy", (0.022, 0.022, 0.025), roughness=0.42)
        self.silicon = principled("silicon (die back)", (0.05, 0.055, 0.075), metallic=0.6, roughness=0.12,
                                  coat=0.6)
        self.tin = principled("tin", (0.78, 0.78, 0.76), metallic=1.0, roughness=0.2)
        self.gold = principled("gold", (1.0, 0.77, 0.38), metallic=1.0, roughness=0.18)
        self.nylon = principled("nylon (natural)", (0.78, 0.74, 0.64), roughness=0.5)
        self.lcp = principled("LCP (ivory)", (0.84, 0.80, 0.70), roughness=0.4)
        self.ferrite = principled("ferrite", (0.035, 0.035, 0.038), roughness=0.6)
        self.nickel = principled("nickel shell", (0.74, 0.74, 0.74), metallic=1.0, roughness=0.18)
        self.cavity = principled("cavity", (0.01, 0.01, 0.01), roughness=0.9)
        self.ink = principled("marking ink", (0.75, 0.75, 0.72), roughness=0.6)


def pad_box(part):
    xs, ys = [], []
    for p in part["pads"]:
        rot = math.radians(p["rot"] - part["rot"])
        w = abs(p["w"] * math.cos(rot)) + abs(p["h"] * math.sin(rot))
        h = abs(p["w"] * math.sin(rot)) + abs(p["h"] * math.cos(rot))
        xs += [p["x"] - w / 2, p["x"] + w / 2]
        ys += [p["y"] - h / 2, p["y"] + h / 2]
    return min(xs), min(ys), max(xs), max(ys)


def build_part(part, root, stock, top_z, board_outline):
    """One stand-in, in the footprint's own frame (mm, y up), under an empty placed like KiCad's."""
    empty = bpy.data.objects.new(f"{part['ref']} (stand-in)", None)
    bpy.context.collection.objects.link(empty)
    empty.parent = root
    flip = Matrix.Rotation(math.pi, 4, "Y") if part["bottom"] else Matrix.Identity(4)
    z = top_z if not part["bottom"] else 0.0
    empty.matrix_parent_inverse = Matrix.Identity(4)
    empty.matrix_basis = (Matrix.Translation((part["x"] * MM, -part["y"] * MM, z))
                          @ Matrix.Rotation(math.radians(part["rot"]), 4, "Z") @ flip)
    x0, y0, x1, y1 = pad_box(part)
    # the footprint frame with y up
    cx, cy, w, d = (x0 + x1) / 2, -(y0 + y1) / 2, x1 - x0, y1 - y0
    kind = part["kind"]
    ref = part["ref"]

    def at(x, y, zz):
        return (x * MM, y * MM, zz * MM)

    if kind == "wlp":
        h = 0.5
        box(f"{ref} die", (w * MM + 0.0002, d * MM + 0.0002, h * MM), at(cx, cy, h / 2 + 0.2), stock.silicon,
            parent=empty)
        for p in part["pads"]:
            cylinder(f"{ref} bump", 0.12 * MM, 0.2 * MM, at(p["x"], -p["y"], 0.1), stock.tin, parent=empty, verts=12)
    elif kind in ("qfn", "pqfn"):
        side = max(w, d) - 0.25 if kind == "qfn" else 3.3
        h = 0.75 if kind == "qfn" else 1.0
        sx, sy = (side, side) if kind == "pqfn" else (w - 0.2, d - 0.2)
        box(f"{ref} body", (sx * MM, sy * MM, h * MM), at(cx, cy, h / 2), stock.epoxy, bevel=0.00004, parent=empty)
        cylinder(f"{ref} pin 1", 0.2 * MM, 0.02 * MM, at(cx - sx / 2 + 0.45, cy + sy / 2 - 0.45, h + 0.005),
                 stock.ink, parent=empty, verts=16)
        for p in part["pads"]:
            if p["w"] * p["h"] < 1.5:
                box(f"{ref} lead", (min(p["w"], 0.6) * MM, min(p["h"], 0.6) * MM, 0.2 * MM),
                    at(p["x"], -p["y"], 0.1), stock.tin, parent=empty)
    elif kind == "inductor":
        # A shielded power inductor, 13.5 x 12.6 x 7 mm, its terminals under the pads' long ends.
        long_x = w >= d
        lx, ly = (13.5, 12.6) if long_x else (12.6, 13.5)
        box(f"{ref} body", (lx * MM, ly * MM, 6.8 * MM), at(cx, cy, 3.4), stock.ferrite, bevel=0.0004, parent=empty)
        cylinder(f"{ref} core", 4.2 * MM, 0.05 * MM, at(cx, cy, 6.82), stock.epoxy, parent=empty)
        for p in part["pads"]:
            box(f"{ref} terminal", (p["w"] * MM * 0.8, p["h"] * MM * 0.9, 0.3 * MM), at(p["x"], -p["y"], 0.15),
                stock.tin, parent=empty)
    elif kind == "vh":
        # JST VH, vertical: a natural nylon housing 3.96 mm a contact, open at the top.
        n = len([p for p in part["pads"] if p["through"]]) or len(part["pads"])
        along_x = w >= d
        length = (n - 1) * 3.96 + 3.84
        sx, sy = (length, 5.0) if along_x else (5.0, length)
        hgt = 11.6
        box(f"{ref} housing", (sx * MM, sy * MM, hgt * MM), at(cx, cy, hgt / 2), stock.nylon, bevel=0.0002, parent=empty)
        for p in part["pads"]:
            box(f"{ref} cavity", (3.0 * MM, 3.0 * MM, 0.5 * MM), at(p["x"], -p["y"], hgt - 0.2), stock.cavity,
                parent=empty)
            box(f"{ref} contact", (1.0 * MM, 1.0 * MM, 0.4 * MM), at(p["x"], -p["y"], hgt - 0.3), stock.tin,
                parent=empty)
    elif kind == "header":
        # A 2.54 mm pin header: a black base 2.5 mm high, gold square posts 6 mm above it.
        box(f"{ref} base", ((w + 0.6) * MM, (d + 0.6) * MM, 2.5 * MM), at(cx, cy, 1.25), stock.epoxy, parent=empty)
        for p in part["pads"]:
            box(f"{ref} pin", (0.64 * MM, 0.64 * MM, 8.5 * MM), at(p["x"], -p["y"], 4.25), stock.gold,
                bevel=0.00005, parent=empty)
    elif kind == "df40":
        # Hirose DF40 receptacle: an ivory body around two rows of fine contacts.
        along_x = w >= d
        sx, sy = ((w + 2.8), 3.5) if along_x else (3.5, (d + 2.8))
        box(f"{ref} body", (sx * MM, sy * MM, 1.5 * MM), at(cx, cy, 0.75), stock.lcp, bevel=0.00008, parent=empty)
        sx2, sy2 = (sx - 1.6, 1.3) if along_x else (1.3, sy - 1.6)
        box(f"{ref} slot", (sx2 * MM, sy2 * MM, 0.2 * MM), at(cx, cy, 1.45), stock.cavity, parent=empty)
        for p in part["pads"]:
            if p["w"] * p["h"] < 0.5:
                box(f"{ref} contact", (p["w"] * MM, p["h"] * MM, 0.25 * MM), at(p["x"], -p["y"], 0.12), stock.gold,
                    parent=empty)
    elif kind == "usbc":
        # A USB-C receptacle: an 8.9 x 3.2 mm nickel shell lying along the pads, open at the board edge.
        thru = [p for p in part["pads"] if p["through"]]
        span = [-p["y"] for p in thru] or [cy]
        length = 7.4
        # The opening faces whichever end of the footprint lies nearer the board's edge.
        ends = (cy + length / 2, cy - length / 2)
        bpy.context.view_layer.update()
        world = [empty.matrix_world @ Vector((0, e * MM, 0)) for e in ends]
        dist = [min(abs(p.x - board_outline[0]), abs(p.x - board_outline[2]),
                    abs(p.y - board_outline[1]), abs(p.y - board_outline[3])) for p in world]
        mouth = ends[0] if dist[0] < dist[1] else ends[1]
        sign = 1 if mouth > cy else -1
        cyl = (mouth - sign * length / 2)
        shell = box(f"{ref} shell", (8.94 * MM, length * MM, 3.26 * MM), at(cx, cyl, 1.63), stock.nickel,
                    bevel=0.0012, parent=empty)
        shell.modifiers["bevel"].segments = 8
        box(f"{ref} mouth", (7.6 * MM, 0.2 * MM, 2.0 * MM), at(cx, mouth + sign * 0.01, 1.63), stock.cavity,
            bevel=0.0007, parent=empty)
        box(f"{ref} tongue", (5.6 * MM, 0.25 * MM, 0.6 * MM), at(cx, mouth + sign * 0.06, 1.63), stock.epoxy,
            parent=empty)
        del span
    return empty


# --- the set -------------------------------------------------------------------------------------

def studio(quality):
    world = bpy.data.worlds.new("studio")
    bpy.context.scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (0.78, 0.80, 0.83, 1)
    bg.inputs["Strength"].default_value = 0.12

    # A seamless paper table, large enough that its edge never shows.
    bpy.ops.mesh.primitive_plane_add(size=3.0, location=(0, 0, -0.00001))
    table = bpy.context.active_object
    table.name = "table"
    table.data.materials.append(principled("table paper", (0.80, 0.81, 0.82), roughness=0.62, noise_bump=0.05))

    def area(name, loc, size, energy, color=(1, 1, 1), size_y=None):
        light = bpy.data.lights.new(name, "AREA")
        light.energy = energy
        light.color = color
        light.shape = "RECTANGLE"
        light.size = size
        light.size_y = size_y or size
        obj = bpy.data.objects.new(name, light)
        bpy.context.collection.objects.link(obj)
        obj.location = loc
        obj.rotation_euler = (Vector((0, 0, 0)) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
        return obj

    area("key", (-0.32, -0.28, 0.42), 0.35, 7.0, (1.0, 0.97, 0.93))       # soft box, front left
    area("fill", (0.42, -0.18, 0.22), 0.5, 1.6, (0.92, 0.95, 1.0))         # big and dim, right
    area("rim", (0.10, 0.45, 0.30), 0.25, 3.0, (1.0, 1.0, 1.0), 0.06)      # a strip behind: the edges
    area("top", (0.0, 0.0, 0.6), 0.6, 1.5)                                  # lifts the shadows


def camera(target, quality):
    cam_data = bpy.data.cameras.new("camera")
    cam_data.lens = 62
    cam_data.sensor_width = 36
    cam_data.dof.use_dof = True
    cam_data.dof.aperture_fstop = 16.0
    cam = bpy.data.objects.new("camera", cam_data)
    bpy.context.collection.objects.link(cam)
    elevation, azimuth, distance = math.radians(46), math.radians(-90 + 4), 0.30
    cam.location = Vector(target) + distance * Vector((math.cos(elevation) * math.cos(azimuth),
                                                       math.cos(elevation) * math.sin(azimuth),
                                                       math.sin(elevation)))
    cam.rotation_euler = (Vector(target) - cam.location).to_track_quat("-Z", "Y").to_euler()
    focus = bpy.data.objects.new("focus", None)
    bpy.context.collection.objects.link(focus)
    focus.location = target
    cam_data.dof.focus_object = focus
    bpy.context.scene.camera = cam


def render_settings(quality, out):
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 256 if quality == "final" else 48
    scene.cycles.use_adaptive_sampling = True
    scene.cycles.adaptive_threshold = 0.015
    scene.cycles.use_denoising = True
    scene.cycles.denoiser = "OPENIMAGEDENOISE"
    scene.cycles.max_bounces = 8
    scene.cycles.transparent_max_bounces = 16
    scene.cycles.caustics_reflective = False
    scene.cycles.caustics_refractive = False
    scene.render.resolution_x = 3200 if quality == "final" else 1600
    scene.render.resolution_y = round(scene.render.resolution_x * 1202 / 2048)  # the photo's aspect
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.view_settings.exposure = 0.15
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_depth = "8"
    scene.render.filepath = out


# --- the boards ----------------------------------------------------------------------------------

def import_board(board, spec, stock):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=spec["glb"])
    objs = [o for o in bpy.data.objects if o not in before]
    root = bpy.data.objects.new(board, None)
    bpy.context.collection.objects.link(root)
    for o in objs:
        if o.parent is None:
            o.parent = root

    mats = board_materials(board)
    cache = set()
    top_z, outline = 0.0016, None
    for o in objs:
        if o.type != "MESH":
            continue
        layer, bottom = layer_of(o)
        if layer is None:
            for slot in o.material_slots:
                if slot.material:
                    tune_component_material(slot.material, cache)
            for poly in o.data.polygons:
                poly.use_smooth = True
            o.data.shade_auto_smooth() if hasattr(o.data, "shade_auto_smooth") else None
            continue
        key = {"soldermask": "soldermask_bottom" if bottom else "soldermask", "silkscreen": "silkscreen",
               "copper": "copper", "via": "copper", "pad": "pad", "PCB": "pcb"}[layer]
        o.data.materials.clear()
        o.data.materials.append(mats[key])
        if layer == "PCB":
            corners = [o.matrix_world @ Vector(c) for c in o.bound_box]
            outline = (min(c.x for c in corners), min(c.y for c in corners),
                       max(c.x for c in corners), max(c.y for c in corners))
            top_z = max(c.z for c in corners) + 0.00009
        if layer == "silkscreen" and not bottom:
            o.location.z += 0.000012     # clear of the mask, so the two never fight
    for part in spec["parts"]:
        build_part(part, root, stock, top_z, outline)
    x, y, turn = BOARDS[board]["place"]
    root.location = (x, y, 0.0)
    root.rotation_euler = (0, 0, math.radians(turn))
    return root


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--quality", choices=("preview", "final"), default="final")
    ap.add_argument("--out", required=True)
    ap.add_argument("--blend")
    args = ap.parse_args(argv)
    with open(args.manifest) as f:
        manifest = json.load(f)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    stock = Stock()
    for board, spec in manifest["boards"].items():
        import_board(board, spec, stock)
        print(f"scene: {board}: imported, {len(spec['parts'])} stand-in bodies")
    studio(args.quality)
    camera((0.0, -0.004, 0.002), args.quality)
    render_settings(args.quality, args.out)
    if args.blend:
        bpy.ops.wm.save_as_mainfile(filepath=args.blend)
        print(f"scene: saved {args.blend}")
    bpy.ops.render.render(write_still=True)
    print(f"scene: rendered {args.out}")


main()
