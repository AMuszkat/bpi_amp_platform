#!/usr/bin/env python3
"""A photographic render of the two boards, in Blender, from the KiCad projects.

    tools/render.sh [--quality preview|final] [--out FILE]      # what to run (fetches Blender)
    python3 hardware/render/render.py --blender PATH [...]       # what it runs

1. Copies hardware/ into build/boards/src/ and fixes the copies' 3D models: KiCad's library now
   ships STEP files only, so every `.wrl` becomes `.step`; `${KICAD8_3DMODEL_DIR}` becomes the
   library's folder; a vendor model that is not here and has a library twin (`SUBSTITUTES`)
   becomes the twin. The projects in hardware/ are never touched (KiCad rewrites a project's
   `.kicad_prl` when it opens it).
2. Exports each board with `kicad-cli pcb export glb` (KiCad 9 or later): the board, copper,
   pads, vias, solder mask, silkscreen and every component with a model, as separate meshes.
3. Writes build/boards/parts.json: every footprint whose model is still missing, with its
   position, rotation, side and pads, and the kind of body Blender builds for it (`KINDS`).
4. Runs Blender (hardware/render/scene.py) on the two GLB files and parts.json: materials,
   the stand-in bodies, a studio set, Cycles, into build/boards/. With --publish, a 2400 px JPEG
   of it goes where the README takes it (docs/images/boards-render.jpg: the PNG is 6 MB).

BPI_AMP_3DMODELS, when set, points at the vendor models (hardware/README.md, "3D models"):
a part whose model is found there is rendered from it, not from a stand-in.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
WORK = os.path.join(ROOT, "build", "boards")
BOARDS = ("base_board", "speaker_amp")
KICAD_MODELS = os.environ.get("KICAD8_3DMODEL_DIR", "/usr/share/kicad/3dmodels")

# Vendor models (BPI_AMP_3DMODELS) with a twin in KiCad's library: the twin, its rotation.
SUBSTITUTES = {
    "DPY0002A.stp": ("Diode_SMD.3dshapes/D_SOD-523.step", (0, 0, 0)),
    "EEEFK1C470P.step": ("Capacitor_SMD.3dshapes/CP_Elec_6.3x5.8.step", (0, 0, 0)),
}

# Footprints with no model anywhere: the body scene.py builds (by the footprint's name).
KINDS = [
    (r"WLP", "wlp"),                       # MAX98397: a wafer-level package, bare silicon on top
    (r"QFN-16", "qfn"),                     # LTC3786
    (r"DPY0002A", "qfn"),                   # the USB ESD diodes: a tiny black body
    (r"PQFN", "pqfn"),                      # the boost MOSFETs
    (r"HCM1A1307", "inductor"),             # the boost inductor
    (r"B\dP-VH", "vh"),                     # JST VH power connectors
    (r"TSW-1\d\d", "header"),               # the PVDD jumper header
    (r"DF40", "df40"),                      # Hirose DF40 board-to-board receptacles
    (r"10152803|USB.?C", "usbc"),          # USB-C receptacle
]


# --- a small s-expression reader, enough for .kicad_pcb ------------------------------------------

def parse(text):
    tokens = re.findall(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+', text)
    pos = 0

    def read():
        nonlocal pos
        tok = tokens[pos]
        pos += 1
        if tok == "(":
            out = []
            while tokens[pos] != ")":
                out.append(read())
            pos += 1
            return out
        return tok[1:-1] if tok.startswith('"') else tok
    return read()


def child(node, name):
    return next((c for c in node if isinstance(c, list) and c and c[0] == name), None)


def children(node, name):
    return [c for c in node if isinstance(c, list) and c and c[0] == name]


# --- step 1: the copies --------------------------------------------------------------------------

def expand(path, project_dir):
    path = path.replace("${KICAD8_3DMODEL_DIR}", KICAD_MODELS).replace("${KIPRJMOD}", project_dir)
    if os.environ.get("BPI_AMP_3DMODELS"):
        path = path.replace("${BPI_AMP_3DMODELS}", os.environ["BPI_AMP_3DMODELS"])
    return path


def fix_models(pcb):
    project_dir = os.path.dirname(pcb)
    with open(pcb) as f:
        text = f.read()

    def one(match):
        block = match.group(0)
        path = re.match(r'\(model "([^"]*)"', block).group(1)
        new = path.replace("${KICAD8_3DMODEL_DIR}", KICAD_MODELS)
        if new.endswith(".wrl"):
            new = new[:-4] + ".step"
        name = os.path.basename(new)
        if not os.path.exists(expand(new, project_dir)) and name in SUBSTITUTES:
            twin, rot = SUBSTITUTES[name]
            new = os.path.join(KICAD_MODELS, twin)
            block = re.sub(r"\(offset\s*\(xyz [^)]*\)\s*\)", "(offset (xyz 0 0 0))", block)
            block = re.sub(r"\(rotate\s*\(xyz [^)]*\)\s*\)", "(rotate (xyz %d %d %d))" % rot, block)
        return block.replace(f'(model "{path}"', f'(model "{new}"', 1)

    text = re.sub(r'\(model "[^"]*"(?:[^()]|\((?:[^()]|\([^()]*\))*\))*\)', one, text)
    with open(pcb, "w") as f:
        f.write(text)


# --- step 3: what has no model -------------------------------------------------------------------

def board_centre(tree):
    xs, ys = [], []
    for kind in ("gr_rect", "gr_line", "gr_arc", "gr_poly"):
        for g in children(tree, kind):
            layer = child(g, "layer")
            if not layer or layer[1] != "Edge.Cuts":
                continue
            for key in ("start", "end", "mid"):
                p = child(g, key)
                if p:
                    xs.append(float(p[1]))
                    ys.append(float(p[2]))
            pts = child(g, "pts")
            for p in children(pts or [], "xy"):
                xs.append(float(p[1]))
                ys.append(float(p[2]))
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2


def missing_parts(pcb):
    project_dir = os.path.dirname(pcb)
    with open(pcb) as f:
        tree = parse(f.read())
    cx, cy = board_centre(tree)
    parts, unknown = [], []
    for fp in children(tree, "footprint"):
        models = [m[1] for m in children(fp, "model")]
        if any(os.path.exists(expand(m, project_dir)) for m in models):
            continue
        name = fp[1]
        kind = next((k for pattern, k in KINDS if re.search(pattern, name)), None)
        ref = next((p[2] for p in children(fp, "property") if p[1] == "Reference"), "?")
        attr = child(fp, "attr") or []
        if kind is None:
            # Footprints that are only copper (test points, logos, mounting holes) have no body.
            if models or "smd" in attr or "through_hole" in attr:
                unknown.append(f"{ref} ({name})")
            continue
        at = child(fp, "at")
        pads = []
        for pad in children(fp, "pad"):
            pat, size = child(pad, "at"), child(pad, "size")
            pads.append({"x": float(pat[1]), "y": float(pat[2]),
                         "w": float(size[1]), "h": float(size[2]),
                         "rot": float(pat[3]) if len(pat) > 3 else 0.0,
                         "shape": pad[3], "through": pad[2] == "thru_hole"})
        parts.append({"ref": ref, "footprint": name, "kind": kind,
                      "x": float(at[1]) - cx, "y": float(at[2]) - cy,
                      "rot": float(at[3]) if len(at) > 3 else 0.0,
                      "bottom": child(fp, "layer")[1].startswith("B."), "pads": pads})
    return parts, unknown


# --- the whole run -------------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--blender", required=True, help="the Blender program (tools/blender.sh fetches it)")
    ap.add_argument("--quality", choices=("preview", "final"), default="final")
    ap.add_argument("--out", default=os.path.join(WORK, "boards-render.png"), help="Blender's PNG")
    ap.add_argument("--publish", help="also write a 2400 px JPEG of it here (Pillow)")
    ap.add_argument("--keep-export", action="store_true", help="reuse build/boards/*.glb if they are there")
    ap.add_argument("--blend", help="also save the Blender scene to this file")
    args = ap.parse_args()

    if not shutil.which("kicad-cli"):
        sys.exit("kicad-cli is missing: install KiCad 9 or later")
    src = os.path.join(WORK, "src")
    os.makedirs(WORK, exist_ok=True)
    manifest = {"boards": {}}
    for board in BOARDS:
        glb = os.path.join(WORK, board + ".glb")
        pcb = os.path.join(src, board, board + ".kicad_pcb")
        if not (args.keep_export and os.path.exists(glb) and os.path.exists(pcb)):
            if board == BOARDS[0]:
                shutil.rmtree(src, ignore_errors=True)
                shutil.copytree(os.path.join(ROOT, "hardware"), src,
                                ignore=shutil.ignore_patterns("render", "*.kicad_prl", "*-backups"))
            fix_models(pcb)
            env = dict(os.environ, KICAD8_3DMODEL_DIR=KICAD_MODELS)
            run = subprocess.run(["kicad-cli", "pcb", "export", "glb", "--force", "--subst-models",
                                  "--include-tracks", "--include-pads", "--include-zones",
                                  "--include-silkscreen", "--include-soldermask", "--cut-vias-in-body",
                                  "-o", glb, pcb], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            if run.returncode != 0 or not os.path.exists(glb):
                sys.exit(f"kicad-cli could not export {board}:\n{run.stdout[-2000:]}")
            print(f"exported {os.path.relpath(glb, ROOT)}")
        parts, unknown = missing_parts(pcb)
        for u in unknown:
            print(f"warning: {board}: no model and no stand-in for {u}: it is left out")
        print(f"{board}: {len(parts)} stand-in bodies ({', '.join(sorted({p['kind'] for p in parts})) or 'none'})")
        manifest["boards"][board] = {"glb": glb, "parts": parts}
    manifest_path = os.path.join(WORK, "parts.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=1)

    scene = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scene.py")
    cmd = [args.blender, "--background", "--factory-startup", "--python-exit-code", "1",
           "--python", scene, "--", "--manifest", manifest_path, "--quality", args.quality, "--out", os.path.abspath(args.out)]
    if args.blend:
        cmd += ["--blend", os.path.abspath(args.blend)]
    print("rendering with Blender (" + args.quality + ") ...", flush=True)
    run = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines = [l for l in run.stdout.splitlines() if l.startswith(("scene:", "Error", "Traceback", "  File")) or "Error" in l]
    print("\n".join(lines))
    if run.returncode != 0 or not os.path.exists(args.out):
        sys.exit("Blender failed:\n" + run.stdout[-3000:])
    print(f"wrote {os.path.relpath(args.out, ROOT)}")
    if args.publish:
        from PIL import Image
        im = Image.open(args.out).convert("RGB")
        if im.width > 2400:
            im = im.resize((2400, round(im.height * 2400 / im.width)), Image.LANCZOS)
        im.save(args.publish, "JPEG", quality=90, optimize=True, progressive=True)
        print(f"wrote {os.path.relpath(args.publish, ROOT)} ({os.path.getsize(args.publish) // 1024} KiB)")


if __name__ == "__main__":
    main()
