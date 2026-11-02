#!/usr/bin/env python3
"""The README's opening picture: what the platform is, in one scene, with no block diagram.

    python3 docs/diagrams/hero.py && node docs/diagrams/render.js hero

Writes docs/diagrams/hero.html, an inline SVG that render.js turns into docs/images/hero.png.
A phone and a laptop on the left (the laptop runs the controller), the Wi-Fi in the middle, two
speakers on the right, one of them opened to show the boards of this repository and the plugin
chain speakerd runs on them. Same type as the diagrams (IBM Plex Serif and Mono, from
diagrams.py), on dark ink instead of paper, with the audio blue and the settings orange turned up.
"""
import math
import os

from diagrams import FONT_LINK, FONT_MONO, FONT_SANS

W, H = 1600, 900
INK = "#14223A"
DEEP = "#0B1426"
PAPER = "#EEF2F6"
SOFT = "#9FB3CC"
BLUE = "#3B82F6"
BLUE_DIM = "#1F5FBF"
ORANGE = "#F2763B"
GREEN = "#1E7A4A"
GREEN_LT = "#7CD3A1"
CARD = "#D6E2F0"
CABINET = "#22365C"


def text(x, y, s, size=16, fill=PAPER, mono=False, weight=400, italic=False, anchor="start", spacing=None):
    style = f"font-family: {FONT_MONO if mono else FONT_SANS}; font-size: {size}px; font-weight: {weight}"
    if italic:
        style += "; font-style: italic"
    if spacing:
        style += f"; letter-spacing: {spacing}"
    return f'<text x="{x}" y="{y}" fill="{fill}" text-anchor="{anchor}" style="{style}">{s}</text>\n'


def caption(x, y, kicker, line):
    return (text(x, y, kicker, 12, BLUE, mono=True, weight=600, anchor="middle", spacing="0.16em")
            + text(x, y + 28, line, 19, PAPER, anchor="middle"))


def phone(x, y):
    w, h = 150, 300
    o = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="24" fill="{DEEP}" stroke="{SOFT}" stroke-width="2.5"/>\n'
    o += f'<rect x="{x+10}" y="{y+12}" width="{w-20}" height="{h-24}" rx="16" fill="{INK}"/>\n'
    o += f'<rect x="{x+60}" y="{y+22}" width="30" height="5" rx="2.5" fill="{DEEP}"/>\n'
    # album art
    o += f'<rect x="{x+25}" y="{y+50}" width="100" height="100" rx="8" fill="url(#art)"/>\n'
    o += text(x + 75, y + 112, "&#9835;", 44, PAPER, anchor="middle")
    o += f'<rect x="{x+28}" y="{y+170}" width="94" height="7" rx="3.5" fill="{PAPER}"/>\n'
    o += f'<rect x="{x+42}" y="{y+185}" width="66" height="5" rx="2.5" fill="{SOFT}"/>\n'
    # progress and play
    o += f'<rect x="{x+28}" y="{y+215}" width="94" height="3" rx="1.5" fill="{SOFT}" opacity="0.5"/>\n'
    o += f'<rect x="{x+28}" y="{y+215}" width="40" height="3" rx="1.5" fill="{BLUE}"/>\n'
    o += f'<circle cx="{x+75}" cy="{y+252}" r="19" fill="{BLUE}"/>\n'
    o += f'<path d="M{x+69} {y+242} L{x+84} {y+252} L{x+69} {y+262} Z" fill="{PAPER}"/>\n'
    return o


def laptop(x, y):
    sw, sh = 400, 250
    o = f'<rect x="{x}" y="{y}" width="{sw}" height="{sh}" rx="10" fill="{DEEP}" stroke="{SOFT}" stroke-width="2.5"/>\n'
    o += f'<rect x="{x+12}" y="{y+12}" width="{sw-24}" height="{sh-24}" rx="4" fill="{PAPER}"/>\n'
    # the controller: a canvas with the plugin graph and a speakers panel
    cx, cy = x + 12, y + 12
    o += f'<rect x="{cx}" y="{cy}" width="{sw-24}" height="22" fill="{CARD}"/>\n'
    o += text(cx + 10, cy + 15, "BPI Controller", 10, INK, mono=True, weight=600)
    o += text(cx + 120, cy + 15, "revision 14 from hub left", 9.5, "#515C6F", mono=True)
    nodes = [(cx + 14, cy + 48, 62, "Stream in"), (cx + 96, cy + 48, 62, "firConv"),
             (cx + 178, cy + 48, 66, "crossover"), (cx + 96, cy + 110, 62, "loudness"),
             (cx + 178, cy + 150, 78, "Amplifier out")]
    for nx, ny, nw, name in nodes:
        o += f'<rect x="{nx}" y="{ny}" width="{nw}" height="30" rx="3" fill="{CARD}" stroke="{INK}" stroke-width="1"/>\n'
        o += text(nx + nw / 2, ny + 19, name, 9, INK, mono=True, anchor="middle")
    wire = f'fill="none" stroke="{BLUE_DIM}" stroke-width="1.6"'
    o += f'<path d="M{cx+76} {cy+63} L{cx+96} {cy+63}" {wire}/>\n'
    o += f'<path d="M{cx+158} {cy+63} L{cx+178} {cy+63}" {wire}/>\n'
    o += f'<path d="M{cx+200} {cy+78} C{cx+200} {cy+100} {cx+158} {cy+105} {cx+158} {cy+125}" {wire}/>\n'
    o += f'<path d="M{cx+158} {cy+140} C{cx+158} {cy+160} {cx+200} {cy+150} {cx+200} {cy+150}" {wire}/>\n'
    o += f'<path d="M{cx+230} {cy+78} L{cx+230} {cy+150}" {wire}/>\n'
    # a knob
    o += f'<circle cx="{cx+45}" cy="{cy+150}" r="22" fill="{PAPER}" stroke="{INK}" stroke-width="1.5"/>\n'
    o += f'<path d="M{cx+45} {cy+150} L{cx+33} {cy+134}" stroke="{ORANGE}" stroke-width="3" stroke-linecap="round"/>\n'
    o += text(cx + 45, cy + 188, "gain", 8.5, INK, mono=True, anchor="middle")
    # the speakers panel
    px0 = cx + 272
    o += f'<rect x="{px0}" y="{cy+22}" width="{sw-24-272}" height="{sh-24-22}" fill="{CARD}"/>\n'
    for i, (name, lvl) in enumerate((("left", 0.72), ("right", 0.66))):
        py = cy + 40 + i * 80
        o += f'<circle cx="{px0+12}" cy="{py+5}" r="4" fill="{GREEN_LT}"/>\n'
        o += text(px0 + 22, py + 9, name, 10, INK, mono=True, weight=600)
        for j, f in enumerate((lvl, lvl - 0.25)):
            by = py + 20 + j * 16
            o += f'<rect x="{px0+10}" y="{by}" width="84" height="8" fill="{PAPER}"/>\n'
            o += f'<rect x="{px0+10}" y="{by}" width="{84*f:.0f}" height="8" fill="{BLUE}"/>\n'
        o += text(px0 + 10, py + 64, "cpu 9 %", 8.5, "#515C6F", mono=True)
    # base
    o += (f'<path d="M{x-30} {y+sh+34} L{x+sw+30} {y+sh+34} L{x+sw+6} {y+sh} L{x-6} {y+sh} Z" '
          f'fill="{DEEP}" stroke="{SOFT}" stroke-width="2.5" stroke-linejoin="round"/>\n')
    o += f'<rect x="{x+sw/2-50}" y="{y+sh+8}" width="100" height="6" rx="3" fill="{INK}"/>\n'
    return o


def wifi(cx, cy):
    o = ""
    for r, op in ((26, 1), (52, 0.7), (78, 0.45)):
        o += (f'<path d="M{cx-r*0.78:.1f} {cy-r*0.62:.1f} A{r} {r} 0 0 1 {cx+r*0.78:.1f} {cy-r*0.62:.1f}" '
              f'fill="none" stroke="{BLUE}" stroke-width="7" stroke-linecap="round" opacity="{op}"/>\n')
    o += f'<circle cx="{cx}" cy="{cy}" r="7" fill="{BLUE}"/>\n'
    return o


def wave(x1, x2, y, amp, periods, width=6, color=BLUE, opacity=1):
    pts = []
    n = 160
    for i in range(n + 1):
        t = i / n
        x = x1 + (x2 - x1) * t
        env = math.sin(math.pi * t) ** 0.5
        pts.append(f"{x:.1f} {y + amp * env * math.sin(2 * math.pi * periods * t):.1f}")
    return (f'<path d="M{" L".join(pts)}" fill="none" stroke="{color}" stroke-width="{width}" '
            f'stroke-linecap="round" stroke-linejoin="round" opacity="{opacity}"/>\n')


def speaker(x, y, name):
    w, h = 170, 500
    o = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="{CABINET}" stroke="{SOFT}" stroke-width="2.5"/>\n'
    o += f'<rect x="{x+12}" y="{y+12}" width="{w-24}" height="{h-24}" rx="10" fill="{INK}"/>\n'
    # tweeter
    tx, ty = x + w / 2, y + 90
    o += f'<circle cx="{tx}" cy="{ty}" r="26" fill="{DEEP}" stroke="{ORANGE}" stroke-width="3"/>\n'
    o += f'<circle cx="{tx}" cy="{ty}" r="10" fill="{ORANGE}"/>\n'
    # woofer
    wx, wy = x + w / 2, y + 250
    o += f'<circle cx="{wx}" cy="{wy}" r="62" fill="{DEEP}" stroke="{BLUE}" stroke-width="4"/>\n'
    o += f'<circle cx="{wx}" cy="{wy}" r="44" fill="{CABINET}"/>\n'
    o += f'<circle cx="{wx}" cy="{wy}" r="16" fill="{BLUE}"/>\n'
    o += text(x + w / 2, y + h - 30, name, 13, SOFT, mono=True, weight=600, anchor="middle", spacing="0.16em")
    return o


def pcb(x, y):
    w, h = 220, 290
    o = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{GREEN}" stroke="{GREEN_LT}" stroke-width="2"/>\n'
    for hx, hy in ((x + 14, y + 14), (x + w - 14, y + 14), (x + 14, y + h - 14), (x + w - 14, y + h - 14)):
        o += f'<circle cx="{hx}" cy="{hy}" r="5" fill="{DEEP}" stroke="{GREEN_LT}" stroke-width="1.5"/>\n'
    # traces
    tr = f'fill="none" stroke="{GREEN_LT}" stroke-width="1.5" opacity="0.55"'
    o += f'<path d="M{x+60} {y+150} L{x+60} {y+190} L{x+90} {y+190} L{x+90} {y+210}" {tr}/>\n'
    o += f'<path d="M{x+180} {y+150} L{x+180} {y+190} L{x+150} {y+190} L{x+150} {y+210}" {tr}/>\n'
    o += f'<path d="M{x+120} {y+150} L{x+120} {y+250}" {tr}/>\n'
    o += f'<path d="M{x+36} {y+230} L{x+36} {y+270} L{x+204} {y+270} L{x+204} {y+230}" {tr}/>\n'
    # the BPI-CM4 module
    mx, my, mw, mh = x + 30, y + 34, 180, 116
    o += f'<rect x="{mx}" y="{my}" width="{mw}" height="{mh}" rx="4" fill="{DEEP}" stroke="{SOFT}" stroke-width="1.5"/>\n'
    o += f'<rect x="{mx+56}" y="{my+30}" width="68" height="56" rx="2" fill="{INK}" stroke="{SOFT}" stroke-width="1"/>\n'
    o += text(mx + 90, my + 54, "A311D", 9, SOFT, mono=True, anchor="middle")
    o += text(mx + 90, my + 68, "Linux", 9, SOFT, mono=True, anchor="middle")
    for i in range(14):
        o += f'<rect x="{mx+10+i*11.5:.1f}" y="{my+mh-9}" width="6" height="5" fill="#E2B64A"/>\n'
    o += text(mx + 8, my + 16, "BPI-CM4", 10.5, PAPER, mono=True, weight=600)
    o += f'<rect x="{mx+8}" y="{my+30}" width="40" height="56" rx="2" fill="{INK}"/>\n'
    o += text(mx + 28, my + 62, "eMMC", 8, SOFT, mono=True, anchor="middle")
    # two amplifiers
    for ax, label in ((x + 44, "amp 1"), (x + 134, "amp 2")):
        o += f'<rect x="{ax}" y="{y+200}" width="56" height="48" rx="3" fill="{DEEP}" stroke="{SOFT}" stroke-width="1.5"/>\n'
        o += text(ax + 28, y + 218, "MAX", 9, PAPER, mono=True, weight=600, anchor="middle")
        o += text(ax + 28, y + 231, "98397", 9, PAPER, mono=True, weight=600, anchor="middle")
        o += text(ax + 28, y + 243, label, 7.5, SOFT, mono=True, anchor="middle")
    # output terminals
    for i, c in enumerate((BLUE, BLUE, ORANGE, ORANGE)):
        o += f'<circle cx="{x+72+i*32}" cy="{y+275}" r="7" fill="{c}" stroke="{DEEP}" stroke-width="2"/>\n'
    return o


def dsp_card(x, y):
    w, h = 260, 96
    o = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{CARD}" stroke="{PAPER}" stroke-width="2"/>\n'
    o += text(x + 14, y + 22, "SPEAKERD · THE PLUGIN GRAPH", 9.5, "#515C6F", mono=True,
              weight=600, spacing="0.12em")
    chain = (("firConv", BLUE_DIM), ("crossover", BLUE_DIM), ("loudness", BLUE_DIM))
    bx = x + 14
    for i, (name, c) in enumerate(chain):
        bw = 70
        o += f'<rect x="{bx}" y="{y+38}" width="{bw}" height="34" rx="3" fill="{c}"/>\n'
        o += text(bx + bw / 2, y + 60, name, 10.5, PAPER, mono=True, weight=600, anchor="middle")
        if i < 2:
            o += f'<path d="M{bx+bw} {y+55} L{bx+bw+16} {y+55}" stroke="{INK}" stroke-width="2"/>\n'
            o += f'<path d="M{bx+bw+10} {y+50} L{bx+bw+17} {y+55} L{bx+bw+10} {y+60} Z" fill="{INK}"/>\n'
        bx += bw + 16
    o += wave(x + 14, x + 84, y + 84, 3, 4, width=1.5, color=INK)
    o += wave(x + 176, x + 246, y + 84, 3, 2.2, width=1.5, color=INK)
    return o


def arrow_head(x, y, angle, color, size=12):
    a = math.radians(angle)
    pts = [(0, 0), (-size, -size * 0.45), (-size, size * 0.45)]
    out = []
    for px_, py_ in pts:
        out.append(f"{x + px_*math.cos(a) - py_*math.sin(a):.1f} {y + px_*math.sin(a) + py_*math.cos(a):.1f}")
    return f'<path d="M{" L".join(out)} Z" fill="{color}"/>\n'


def scene():
    o = ""
    o += '<defs>\n'
    o += (f'<radialGradient id="bg" cx="0.62" cy="0.45" r="0.8"><stop offset="0" stop-color="#1C3160"/>'
          f'<stop offset="1" stop-color="{DEEP}"/></radialGradient>\n')
    o += (f'<linearGradient id="art" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{ORANGE}"/>'
          f'<stop offset="1" stop-color="#C0398A"/></linearGradient>\n')
    o += '</defs>\n'
    o += f'<rect width="{W}" height="{H}" fill="url(#bg)"/>\n'
    # faint grid, a nod to the diagrams
    o += f'<path d="{" ".join(f"M{gx} 0 V{H}" for gx in range(0, W, 80))}" stroke="{PAPER}" stroke-width="0.5" opacity="0.045"/>\n'
    o += f'<path d="{" ".join(f"M0 {gy} H{W}" for gy in range(0, H, 80))}" stroke="{PAPER}" stroke-width="0.5" opacity="0.045"/>\n'

    # title
    o += text(80, 96, "BPI AMP Platform", 46, PAPER, weight=500, spacing="-0.015em")
    o += text(80, 132, "Stream from anything. Tune the pair from one app. Each speaker runs its own DSP.",
              20, SOFT, italic=True)

    # the music: out of the phone, across the picture, into the speakers (drawn first: it passes behind them)
    o += wave(270, 1030, 470, 36, 5.5, width=7, color=BLUE, opacity=0.25)
    o += wave(270, 1030, 470, 26, 5.5, width=6, color=BLUE)
    o += text(620, 545, "the music, to both speakers in sync", 15, SOFT, italic=True)

    # sources
    o += phone(130, 170)
    o += laptop(96, 480)

    # network
    o += wifi(760, 370)
    o += text(760, 412, "WI-FI", 11.5, SOFT, mono=True, weight=600, anchor="middle", spacing="0.16em")
    # settings: the controller to the plugin graph
    o += (f'<path d="M496 486 C 560 180, 900 200, 1136 215" fill="none" stroke="{ORANGE}" stroke-width="3" '
          f'stroke-dasharray="10 8" stroke-linecap="round"/>\n')
    o += arrow_head(1142, 215, 2, ORANGE, 16)
    o += text(590, 196, "settings and knobs, within milliseconds", 15, ORANGE, italic=True)

    # speakers, and between them what is inside each: the boards and the plugin graph
    o += speaker(980, 240, "LEFT")
    o += speaker(1400, 240, "RIGHT")
    o += dsp_card(1145, 170)
    o += pcb(1165, 400)
    o += text(1275, 716, "the boards of this repository", 13, SOFT, italic=True, anchor="middle")
    o += text(1275, 734, "BPI-CM4 · two MAX98397 amplifiers", 10.5, SOFT, mono=True, anchor="middle")
    # sound out of the right speaker
    for r, op in ((26, 0.9), (44, 0.6), (62, 0.35)):
        o += (f'<path d="M{1582} {490-r*0.7:.1f} A{r} {r} 0 0 1 {1582} {490+r*0.7:.1f}" fill="none" '
              f'stroke="{PAPER}" stroke-width="3" stroke-linecap="round" opacity="{op}"/>\n')

    # captions
    o += caption(300, 838, "YOUR MUSIC", "Play from a phone or a computer, as always")
    o += caption(760, 838, "OVER THE NETWORK", "Snapcast or AirPlay carry it to both speakers")
    o += caption(1275, 838, "IN EACH SPEAKER", "A Linux board runs the crossover, correction and loudness")
    return o


def main():
    html = ("<!DOCTYPE html>\n<html><head><meta charset=\"utf-8\">\n" + FONT_LINK + "\n"
            "<style>body { margin: 0; background: " + DEEP + " }</style></head><body>\n"
            f'<div style="width: {W}px; height: {H}px">\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">\n'
            + scene() + "</svg>\n</div>\n</body></html>\n")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hero.html")
    with open(out, "w") as f:
        f.write(html)
    print(f"wrote {os.path.relpath(out)}")


if __name__ == "__main__":
    main()
