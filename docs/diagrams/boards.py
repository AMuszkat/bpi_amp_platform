#!/usr/bin/env python3
"""The README's hardware picture: a KiCad render of the two boards, and a wipe from it to the photo.

    python3 docs/diagrams/boards.py render && node docs/diagrams/render.js boards-render
    python3 docs/diagrams/boards.py slider

1. Copies hardware/ into build/boards/ and gives the copies' stackups the boards' real colours
   (the base board green, the amplifier board black, ENIG pads, white silk), which the projects
   themselves do not record; the projects in hardware/ are not touched.
2. Renders each board with `kicad-cli pcb render` (KiCad 9 or later) into build/boards/.
3. Writes docs/diagrams/boards-render.html, which render.js turns into docs/images/boards-render.png:
   the two renders on paper, laid out like the photo all.jpeg.
4. Writes docs/images/boards.svg: the render and the photo, one over the other, with a divider
   that slides across and back. GitHub shows a README's images with no script and no pointer
   events, so the slider moves by itself (SMIL, which runs in an <img>); both pictures are
   embedded as JPEG data, since an SVG in an <img> loads nothing from outside.

Parts whose 3D model is a vendor download (the MAX98397, the DF40 connectors, the boost
inductor and MOSFETs, the USB-C socket) come out only as their pads unless BPI_AMP_3DMODELS
points at the models (hardware/README.md, "3D models").
"""
import base64
import io
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.join(ROOT, "build", "boards")
HERE = os.path.dirname(os.path.abspath(__file__))

W, H = 1600, 939          # the photo's aspect, 2048 x 1202
PAPER = "#EEF2F6"
INK = "#14223A"
MUTED = "#5B6678"
FONT_SERIF = "'IBM Plex Serif', Georgia, serif"
FONT_MONO = "'IBM Plex Mono', ui-monospace, Menlo, monospace"

BOARDS = {
    # name: (mask colour, how it sits in the picture: KiCad's --rotate)
    "base_board": ("#0B6636F5", "-38,0,-28"),
    "speaker_amp": ("#141414F5", "-38,0,-28"),
}


def colour_stackup(pcb, mask):
    """Adds colours to a stackup that has none: the copy only, never hardware/."""
    with open(pcb) as f:
        s = f.read()
    for layer, colour in (("F.Mask", mask), ("B.Mask", mask), ("F.SilkS", "White"), ("B.SilkS", "White")):
        s = re.sub(r'(\(layer "%s"\s*\n\s*\(type "[^"]*"\))' % re.escape(layer),
                   r'\1 (color "%s")' % colour, s, count=1)
    s = s.replace('(copper_finish "None")', '(copper_finish "ENIG")')
    with open(pcb, "w") as f:
        f.write(s)


def render():
    if not shutil.which("kicad-cli"):
        sys.exit("kicad-cli is missing: install KiCad 9 or later")
    src = os.path.join(OUT, "hardware")
    shutil.rmtree(src, ignore_errors=True)
    shutil.copytree(os.path.join(ROOT, "hardware"), src)
    defines = ["-D", "KICAD8_3DMODEL_DIR=" + os.environ.get("KICAD8_3DMODEL_DIR", "/usr/share/kicad/3dmodels")]
    if os.environ.get("BPI_AMP_3DMODELS"):
        defines += ["-D", "BPI_AMP_3DMODELS=" + os.environ["BPI_AMP_3DMODELS"]]
    for name, (mask, rotate) in BOARDS.items():
        pcb = os.path.join(src, name, name + ".kicad_pcb")
        colour_stackup(pcb, mask)
        out = os.path.join(OUT, name + ".png")
        subprocess.run(["kicad-cli", "pcb", "render", *defines, "-o", out, "-w", "2400", "-h", "1600",
                        "--quality", "high", "--background", "transparent", "--use-board-stackup-colors",
                        "--perspective", "--rotate", rotate, "--zoom", "0.78",
                        "--light-top", "0.45", "--light-side", "0.5", "--light-camera", "0.35", pcb],
                       check=True, stdout=subprocess.DEVNULL)
        print(f"rendered {os.path.relpath(out, ROOT)}")


def page():
    """The two renders on paper, laid out like the photo: the base board left, the amplifier right."""
    def img(name, left, top, width):
        path = os.path.join(OUT, name + ".png")
        return (f'<img src="file://{path}" style="position: absolute; left: {left}px; top: {top}px; '
                f'width: {width}px; filter: drop-shadow(0 18px 22px rgba(20,34,58,0.22))">\n')

    def note(x, y, kicker, line):
        return (f'<div style="position: absolute; left: {x}px; top: {y}px; font-family: {FONT_MONO}; font-size: 13px; '
                f'font-weight: 600; letter-spacing: 0.16em; color: {MUTED}">{kicker}</div>\n'
                f'<div style="position: absolute; left: {x}px; top: {y + 22}px; font-family: {FONT_SERIF}; '
                f'font-style: italic; font-size: 17px; color: {INK}">{line}</div>\n')

    body = (img("base_board", -150, -10, 1180) + img("speaker_amp", 560, 90, 1180)
            + note(70, 70, "BASE BOARD", "carries the BPI-CM4 module, USB-C and the debug UART")
            + note(980, 70, "SPEAKER AMP BOARD", "two MAX98397 amplifiers, the boost and the 5 V buck"))
    from diagrams import FONT_LINK
    html = ("<!DOCTYPE html>\n<html><head><meta charset=\"utf-8\">\n" + FONT_LINK + "\n"
            "<style>body { margin: 0 }</style></head><body>\n"
            f'<div style="position: relative; width: {W}px; height: {H}px; overflow: hidden; '
            f'background: radial-gradient(ellipse at 55% 45%, #FFFFFF 0%, {PAPER} 70%)">\n' + body + "</div>\n</body></html>\n")
    with open(os.path.join(HERE, "boards-render.html"), "w") as f:
        f.write(html)
    print("wrote docs/diagrams/boards-render.html")


def jpeg_b64(path, width):
    """A JPEG of the picture at the slider's width, as base64 (Pillow when it is there, else ImageMagick)."""
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB")
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=84, optimize=True, progressive=True)
        data = buf.getvalue()
    except ImportError:
        data = subprocess.run(["convert", path, "-resize", f"{width}x", "-quality", "84", "jpeg:-"],
                              check=True, stdout=subprocess.PIPE).stdout
    return base64.b64encode(data).decode()


def slider():
    """The render first, then the divider sweeps across to the photo and back, pausing at each end."""
    w, h = 1280, 751
    render_png = os.path.join(ROOT, "docs", "images", "boards-render.png")
    photo = os.path.join(ROOT, "all.jpeg")
    if not os.path.exists(render_png):
        sys.exit("docs/images/boards-render.png is missing: run node docs/diagrams/render.js boards-render first")
    a, b = jpeg_b64(render_png, w), jpeg_b64(photo, w)
    # One 12 s cycle: the render 3 s, a 2.5 s sweep to the photo, the photo 3 s, a 2.5 s sweep back.
    times = "0;0.25;0.458;0.708;0.917;1"
    spline = "0 0 1 1;0.65 0 0.35 1;0 0 1 1;0.65 0 0.35 1;0 0 1 1"
    xs = f"{w};{w};0;0;{w};{w}"
    anim = (f'calcMode="spline" keyTimes="{times}" keySplines="{spline}" dur="12s" repeatCount="indefinite"')
    tag = ('font-family="IBM Plex Mono, Menlo, monospace" font-size="13" font-weight="600" '
           'letter-spacing="2" fill="#FFFFFF"')
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{w}" height="{h}" viewBox="0 0 {w} {h}">
<title>The boards: the KiCad render, then the photo</title>
<defs><clipPath id="left"><rect x="0" y="0" width="{w}" height="{h}"><animate attributeName="width" values="{xs}" {anim}/></rect></clipPath></defs>
<image width="{w}" height="{h}" preserveAspectRatio="xMidYMid slice" xlink:href="data:image/jpeg;base64,{b}"/>
<g clip-path="url(#left)"><image width="{w}" height="{h}" preserveAspectRatio="xMidYMid slice" xlink:href="data:image/jpeg;base64,{a}"/></g>
<g>
  <animateTransform attributeName="transform" type="translate" values="{xs.replace(';', ',0;')},0" {anim}/>
  <rect x="-1.5" y="0" width="3" height="{h}" fill="#FFFFFF"/>
  <circle cx="0" cy="{h / 2}" r="22" fill="#FFFFFF" stroke="{INK}" stroke-opacity="0.15"/>
  <path d="M-6 {h / 2 - 7} L-13 {h / 2} L-6 {h / 2 + 7} M6 {h / 2 - 7} L13 {h / 2} L6 {h / 2 + 7}" fill="none" stroke="{INK}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>
</g>
<g><rect x="16" y="{h - 44}" width="96" height="28" rx="14" fill="{INK}" fill-opacity="0.7"/><text x="64" y="{h - 25}" text-anchor="middle" {tag}>RENDER</text></g>
<g><rect x="{w - 112}" y="{h - 44}" width="96" height="28" rx="14" fill="{INK}" fill-opacity="0.7"/><text x="{w - 64}" y="{h - 25}" text-anchor="middle" {tag}>PHOTO</text></g>
</svg>
'''
    out = os.path.join(ROOT, "docs", "images", "boards.svg")
    with open(out, "w") as f:
        f.write(svg)
    print(f"wrote docs/images/boards.svg ({len(svg) // 1024} KiB)")


if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else "all"
    if step in ("all", "render"):
        render()
        page()
    if step in ("all", "slider"):
        slider()
