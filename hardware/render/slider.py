#!/usr/bin/env python3
"""The README's hardware picture: the Blender render and the photo, one over the other.

    python3 hardware/render/slider.py          # tools/render.sh runs it after a final render

Writes docs/images/boards.svg from docs/images/boards-render.jpg (hardware/render/render.py's)
and all.jpeg: a divider slides across from the render to the photo and back. GitHub shows a
README's images with no script and no pointer events, so the slider moves by itself (SMIL,
which runs in an <img>); both pictures are embedded as JPEG data, since an SVG in an <img>
loads nothing from outside.
"""
import base64
import io
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
INK = "#14223A"


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
    render_png = os.path.join(ROOT, "docs", "images", "boards-render.jpg")
    photo = os.path.join(ROOT, "all.jpeg")
    if not os.path.exists(render_png):
        sys.exit("docs/images/boards-render.jpg is missing: run tools/render.sh first")
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
    slider()
