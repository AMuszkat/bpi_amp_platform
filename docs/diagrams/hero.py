#!/usr/bin/env python3
"""The README's opening picture: what the platform is, in one scene, with no block diagram.

    python3 docs/diagrams/hero.py && node docs/diagrams/render.js hero

Writes docs/diagrams/hero.html, an inline SVG that render.js turns into docs/images/hero.png.
A phone and a laptop on the left (the laptop runs the controller), the Wi-Fi in the middle, and
on the right two speakers, speaker 1 and speaker 2: each the board of this repository, the DSP
plugins it runs and a small loudspeaker beside it. No plugin is named: any DSP plugin runs there.
Same type as the diagrams (IBM Plex Serif and Mono, from
diagrams.py), on paper, with the audio blue and the settings orange a little softer than the diagrams'.
"""
import math
import os

from diagrams import FONT_LINK, FONT_MONO, FONT_SANS

W, H = 1600, 900
INK = "#14223A"
MUTED = "#5B6678"
DEEP = "#34435E"     # the devices' bodies
SCREEN = "#1E2B45"
PAPER = "#EEF2F6"
SOFT = "#B9C6D8"
BLUE = "#6B9BE8"
BLUE_DIM = "#4A77C9"
ORANGE = "#E8946A"
GREEN = "#7DAE92"
GREEN_LT = "#D8EBDF"
CARD = "#FFFFFF"
CABINET = "#DCE4EE"
BAFFLE = "#F7F9FC"
CONE = "#C9D4E3"


def text(x, y, s, size=16, fill=PAPER, mono=False, weight=400, italic=False, anchor="start", spacing=None):
    style = f"font-family: {FONT_MONO if mono else FONT_SANS}; font-size: {size}px; font-weight: {weight}"
    if italic:
        style += "; font-style: italic"
    if spacing:
        style += f"; letter-spacing: {spacing}"
    return f'<text x="{x}" y="{y}" fill="{fill}" text-anchor="{anchor}" style="{style}">{s}</text>\n'


def caption(x, y, kicker, line):
    return (text(x, y, kicker, 14, BLUE_DIM, mono=True, weight=600, anchor="middle", spacing="0.16em")
            + text(x, y + 32, line, 23, INK, anchor="middle"))


def transport(cx, cy, kind, line, color, fill):
    """A transport line's label: a pill saying what it carries and over which protocol."""
    w = 44 + 11.2 * len(kind) + 12 + 9.6 * len(line)
    o = (f'<rect x="{cx - w / 2:.0f}" y="{cy - 20}" width="{w:.0f}" height="40" rx="20" fill="{fill}" '
         f'stroke="{color}" stroke-width="1.5"/>\n')
    o += (f'<text x="{cx}" y="{cy + 6}" text-anchor="middle" style="font-family: {FONT_MONO}; font-size: 16px">'
          f'<tspan fill="{color}" font-weight="600" letter-spacing="0.1em">{kind}</tspan>'
          f'<tspan fill="{INK}" dx="12">{line}</tspan></text>\n')
    return o


def phone(x, y):
    w, h = 150, 300
    o = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="24" fill="{DEEP}"/>\n'
    o += f'<rect x="{x+10}" y="{y+12}" width="{w-20}" height="{h-24}" rx="16" fill="{SCREEN}"/>\n'
    o += f'<rect x="{x+60}" y="{y+22}" width="30" height="5" rx="2.5" fill="{INK}"/>\n'
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
    o = f'<rect x="{x}" y="{y}" width="{sw}" height="{sh}" rx="10" fill="{DEEP}"/>\n'
    o += f'<rect x="{x+12}" y="{y+12}" width="{sw-24}" height="{sh-24}" rx="4" fill="{PAPER}"/>\n'
    # the controller: a canvas with the plugin graph and a speakers panel
    cx, cy = x + 12, y + 12
    o += f'<rect x="{cx}" y="{cy}" width="{sw-24}" height="22" fill="#E3E8EE"/>\n'
    o += text(cx + 10, cy + 15, "BPI Controller", 10, INK, mono=True, weight=600)
    o += text(cx + 120, cy + 15, "2 speakers online", 9.5, "#515C6F", mono=True)
    nodes = [(cx + 14, cy + 48, 62, "Stream in"), (cx + 96, cy + 48, 62, "DSP plugin"),
             (cx + 178, cy + 48, 66, "DSP plugin"), (cx + 96, cy + 110, 62, "DSP plugin"),
             (cx + 178, cy + 150, 78, "Amplifier out")]
    for nx, ny, nw, name in nodes:
        o += f'<rect x="{nx}" y="{ny}" width="{nw}" height="30" rx="3" fill="#E3E8EE" stroke="{INK}" stroke-width="1"/>\n'
        o += text(nx + nw / 2, ny + 19, name, 9, INK, mono=True, anchor="middle")
    wire = f'fill="none" stroke="{BLUE_DIM}" stroke-width="1.6"'
    o += f'<path d="M{cx+76} {cy+63} L{cx+96} {cy+63}" {wire}/>\n'
    o += f'<path d="M{cx+158} {cy+63} L{cx+178} {cy+63}" {wire}/>\n'
    o += f'<path d="M{cx+200} {cy+78} C{cx+200} {cy+100} {cx+158} {cy+105} {cx+158} {cy+125}" {wire}/>\n'
    o += f'<path d="M{cx+158} {cy+140} C{cx+158} {cy+160} {cx+200} {cy+150} {cx+200} {cy+150}" {wire}/>\n'
    o += f'<path d="M{cx+230} {cy+78} L{cx+230} {cy+150}" {wire}/>\n'
    # a knob
    o += f'<circle cx="{cx+45}" cy="{cy+150}" r="22" fill="{PAPER}" stroke="{INK}" stroke-width="1.5"/>\n'
    o += f'<path d="M{cx+45} {cy+150} L{cx+33} {cy+134}" stroke="{ORANGE}" stroke-width="2.5" stroke-linecap="round"/>\n'
    o += text(cx + 45, cy + 188, "gain", 8.5, INK, mono=True, anchor="middle")
    # the speakers panel
    px0 = cx + 272
    o += f'<rect x="{px0}" y="{cy+22}" width="{sw-24-272}" height="{sh-24-22}" fill="#E3E8EE"/>\n'
    for i, (name, lvl) in enumerate((("speaker 1", 0.72), ("speaker 2", 0.66))):
        py = cy + 40 + i * 80
        o += f'<circle cx="{px0+12}" cy="{py+5}" r="4" fill="#5FB08A"/>\n'
        o += text(px0 + 22, py + 9, name, 10, INK, mono=True, weight=600)
        for j, f in enumerate((lvl, lvl - 0.25)):
            by = py + 20 + j * 16
            o += f'<rect x="{px0+10}" y="{by}" width="84" height="8" fill="{PAPER}"/>\n'
            o += f'<rect x="{px0+10}" y="{by}" width="{84*f:.0f}" height="8" fill="{BLUE}"/>\n'
        o += text(px0 + 10, py + 64, "cpu 9 %", 8.5, "#515C6F", mono=True)
    # base
    o += (f'<path d="M{x-30} {y+sh+34} L{x+sw+30} {y+sh+34} L{x+sw+6} {y+sh} L{x-6} {y+sh} Z" '
          f'fill="{DEEP}" stroke-linejoin="round"/>\n')
    o += f'<rect x="{x+sw/2-50}" y="{y+sh+8}" width="100" height="6" rx="3" fill="{SCREEN}"/>\n'
    return o


def wifi(cx, cy):
    o = ""
    for r, op in ((26, 1), (52, 0.7), (78, 0.45)):
        o += (f'<path d="M{cx-r*0.78:.1f} {cy-r*0.62:.1f} A{r} {r} 0 0 1 {cx+r*0.78:.1f} {cy-r*0.62:.1f}" '
              f'fill="none" stroke="{BLUE}" stroke-width="6" stroke-linecap="round" opacity="{op}"/>\n')
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


def loudspeaker(x, y, size=1.0):
    """The loudspeaker symbol: a magnet and a cone, with sound coming out of it."""
    def p(px_, py_):
        return f"{x + px_ * size:.1f} {y + py_ * size:.1f}"
    o = (f'<path d="M{p(0, -12)} L{p(12, -12)} L{p(12, 12)} L{p(0, 12)} Z" fill="{DEEP}"/>\n'
         f'<path d="M{p(12, -12)} L{p(32, -30)} L{p(32, 30)} L{p(12, 12)} Z" fill="{CONE}" stroke="{DEEP}" '
         f'stroke-width="{2 * size:.1f}" stroke-linejoin="round"/>\n')
    for r, op in ((14, 0.9), (26, 0.6), (38, 0.35)):
        o += (f'<path d="M{p(40 + r * 0.25, -r * 0.8)} A{r * size:.1f} {r * size:.1f} 0 0 1 {p(40 + r * 0.25, r * 0.8)}" '
              f'fill="none" stroke="{BLUE}" stroke-width="{2.5 * size:.1f}" stroke-linecap="round" opacity="{op}"/>\n')
    return o


def pcb(x, y):
    w, h = 220, 290
    o = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{GREEN}" stroke="{INK}" stroke-width="1"/>\n'
    for hx, hy in ((x + 14, y + 14), (x + w - 14, y + 14), (x + 14, y + h - 14), (x + w - 14, y + h - 14)):
        o += f'<circle cx="{hx}" cy="{hy}" r="5" fill="{BAFFLE}" stroke="{INK}" stroke-width="1"/>\n'
    # traces
    tr = f'fill="none" stroke="{GREEN_LT}" stroke-width="1.5" opacity="0.8"'
    o += f'<path d="M{x+60} {y+150} L{x+60} {y+190} L{x+90} {y+190} L{x+90} {y+210}" {tr}/>\n'
    o += f'<path d="M{x+180} {y+150} L{x+180} {y+190} L{x+150} {y+190} L{x+150} {y+210}" {tr}/>\n'
    o += f'<path d="M{x+120} {y+150} L{x+120} {y+250}" {tr}/>\n'
    o += f'<path d="M{x+36} {y+230} L{x+36} {y+270} L{x+204} {y+270} L{x+204} {y+230}" {tr}/>\n'
    # the BPI-CM4 module
    mx, my, mw, mh = x + 30, y + 34, 180, 116
    o += f'<rect x="{mx}" y="{my}" width="{mw}" height="{mh}" rx="4" fill="{DEEP}"/>\n'
    o += f'<rect x="{mx+56}" y="{my+30}" width="68" height="56" rx="2" fill="{SCREEN}" stroke="{SOFT}" stroke-width="0.8"/>\n'
    o += text(mx + 90, my + 54, "A311D", 9, SOFT, mono=True, anchor="middle")
    o += text(mx + 90, my + 68, "Linux", 9, SOFT, mono=True, anchor="middle")
    for i in range(14):
        o += f'<rect x="{mx+10+i*11.5:.1f}" y="{my+mh-9}" width="6" height="5" fill="#E2B64A"/>\n'
    o += text(mx + 8, my + 16, "BPI-CM4", 10.5, PAPER, mono=True, weight=600)
    o += f'<rect x="{mx+8}" y="{my+30}" width="40" height="56" rx="2" fill="{SCREEN}"/>\n'
    o += text(mx + 28, my + 62, "eMMC", 8, SOFT, mono=True, anchor="middle")
    # two amplifiers
    for ax, label in ((x + 44, "amp 1"), (x + 134, "amp 2")):
        o += f'<rect x="{ax}" y="{y+200}" width="56" height="48" rx="3" fill="{DEEP}"/>\n'
        o += text(ax + 28, y + 218, "MAX", 9, PAPER, mono=True, weight=600, anchor="middle")
        o += text(ax + 28, y + 231, "98397", 9, PAPER, mono=True, weight=600, anchor="middle")
        o += text(ax + 28, y + 243, label, 7.5, SOFT, mono=True, anchor="middle")
    # output terminals
    for i, c in enumerate((BLUE, BLUE, ORANGE, ORANGE)):
        o += f'<circle cx="{x+72+i*32}" cy="{y+275}" r="7" fill="{c}" stroke="{INK}" stroke-width="1"/>\n'
    return o


def dsp_card(x, y, w=232):
    """The DSP plugins a speaker runs, as three generic blocks (an EQ, a filter, a dynamics curve), built with JUCE."""
    h = 88
    o = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{CARD}" stroke="{INK}" stroke-width="1"/>\n'
    o += text(x + 12, y + 20, "JUCE DSP PLUGINS", 9.5, MUTED, mono=True, weight=600, spacing="0.14em")
    bw, gap = (w - 24 - 2 * 14) / 3, 14
    curves = (
        lambda bx, by: f"M{bx+6} {by+22} C{bx+18} {by+22} {bx+20} {by+8} {bx+bw/2} {by+8} C{bx+bw-20} {by+8} {bx+bw-18} {by+26} {bx+bw-6} {by+26}",
        lambda bx, by: f"M{bx+6} {by+10} L{bx+bw/2-4} {by+10} C{bx+bw/2+6} {by+10} {bx+bw/2+8} {by+28} {bx+bw-6} {by+28}",
        lambda bx, by: f"M{bx+6} {by+28} L{bx+bw/2} {by+14} L{bx+bw-6} {by+10}",
    )
    bx = x + 12
    for i, curve in enumerate(curves):
        by = y + 34
        o += f'<rect x="{bx:.1f}" y="{by}" width="{bw:.1f}" height="38" rx="3" fill="{BLUE_DIM}"/>\n'
        o += f'<path d="{curve(bx, by)}" fill="none" stroke="{PAPER}" stroke-width="2" stroke-linecap="round"/>\n'
        if i < 2:
            o += f'<path d="M{bx+bw:.1f} {by+19} L{bx+bw+gap-3:.1f} {by+19}" stroke="{INK}" stroke-width="1.5"/>\n'
        bx += bw + gap
    return o


def speaker_unit(x, y, number):
    """One speaker: its frame and name, the DSP plugins, the board, and the loudspeaker it drives."""
    w, h = 272, 412
    o = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="18" fill="#FFFFFF" fill-opacity="0.55" '
         f'stroke="{INK}" stroke-width="1.2"/>\n')
    o += text(x + 20, y + 30, f"SPEAKER {number}", 13, INK, mono=True, weight=600, spacing="0.16em")
    o += dsp_card(x + 20, y + 48, w - 40)
    bx, by, scale = x + 20, y + 156, 0.8
    o += f'<g transform="translate({bx} {by}) scale({scale})">\n' + pcb(0, 0) + '</g>\n'
    # the board's outputs to the loudspeaker
    sx, sy = x + w - 58, by + 150
    o += (f'<path d="M{bx + 220 * scale:.1f} {sy} L{sx} {sy}" fill="none" stroke="{BLUE_DIM}" stroke-width="2"/>\n')
    o += loudspeaker(sx, sy, 0.95)
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
    o += (f'<radialGradient id="bg" cx="0.62" cy="0.45" r="0.8"><stop offset="0" stop-color="#FFFFFF"/>'
          f'<stop offset="1" stop-color="#E2E9F1"/></radialGradient>\n')
    o += (f'<linearGradient id="art" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{ORANGE}"/>'
          f'<stop offset="1" stop-color="#D98CB0"/></linearGradient>\n')
    o += '</defs>\n'
    o += f'<rect width="{W}" height="{H}" fill="url(#bg)"/>\n'
    # faint grid, a nod to the diagrams
    o += f'<path d="{" ".join(f"M{gx} 0 V{H}" for gx in range(0, W, 80))}" stroke="{INK}" stroke-width="0.5" opacity="0.05"/>\n'
    o += f'<path d="{" ".join(f"M0 {gy} H{W}" for gy in range(0, H, 80))}" stroke="{INK}" stroke-width="0.5" opacity="0.05"/>\n'

    # title
    o += text(80, 96, "BPI AMP Platform", 46, INK, weight=500, spacing="-0.015em")
    o += text(80, 132, "Stream from anything. Tune the pair from one app. Each speaker runs its own DSP.",
              20, MUTED, italic=True)

    # your devices: the phone above, the controller below, in a frame of their own drawn like the
    # speakers', so the page reads as three boxes joined by two lines
    fx, fy, fw, fh = 80, 180, 400, 600
    o += (f'<rect x="{fx}" y="{fy}" width="{fw}" height="{fh}" rx="18" fill="#FFFFFF" fill-opacity="0.55" '
          f'stroke="{INK}" stroke-width="1.2"/>\n')
    o += text(fx + 20, fy + 30, "YOUR DEVICES", 13, INK, mono=True, weight=600, spacing="0.16em")
    o += f'<g transform="translate({fx + 140} {fy + 52}) scale(0.72)">\n' + phone(0, 0) + '</g>\n'
    o += text(fx + 70, fy + 170, "any player", 15, MUTED, italic=True, anchor="middle")
    o += f'<g transform="translate({fx + 30} {fy + 300}) scale(0.78)">\n' + laptop(24, 0) + '</g>\n'
    o += text(fx + 60, fy + 290, "the controller", 15, MUTED, italic=True, anchor="start")
    x_out = fx + fw + 2    # where both lines leave the frame

    # the music: out of the frame at the phone's height, straight into the speakers' DSP plugins
    o += wave(x_out, 966, 330, 34, 5.0, width=7, color=BLUE, opacity=0.18)
    o += wave(x_out, 966, 330, 26, 5.0, width=4.5, color=BLUE)
    o += transport(640, 330, "AUDIO", "Snapcast · TCP", BLUE_DIM, "#E6EEFB")
    o += text(640, 274, "the music, to both speakers in sync", 19, MUTED, italic=True, anchor="middle")

    # network, between the two lines
    o += wifi(720, 470)
    o += text(720, 512, "WI-FI", 14, MUTED, mono=True, weight=600, anchor="middle", spacing="0.16em")
    # settings: out of the frame at the controller's height, into the speakers' boards
    o += (f'<path d="M{x_out} 640 C {x_out + 100} 668, 800 610, 958 578" fill="none" stroke="{ORANGE}" '
          f'stroke-width="3" stroke-dasharray="2 9" stroke-linecap="round"/>\n')
    o += arrow_head(966, 576, -12, ORANGE, 16)
    o += transport(735, 700, "CONTROL", "settings + meters TCP · knobs UDP", ORANGE, "#FBEDE6")
    o += text(735, 752, "settings and knobs, on both speakers within milliseconds", 19, MUTED, italic=True,
              anchor="middle")

    # the two speakers, each a board with its DSP plugins and a loudspeaker
    o += speaker_unit(968, 214, 1)
    o += speaker_unit(1268, 214, 2)
    o += text(1254, 656, "the boards of this repository: BPI-CM4 · two MAX98397 amplifiers", 14, MUTED,
              mono=True, anchor="middle")

    # captions
    o += caption(280, 830, "YOUR MUSIC", "Play from a phone or a computer")
    o += caption(770, 830, "OVER THE NETWORK", "Snapcast or AirPlay, to both speakers")
    o += caption(1265, 830, "IN EACH SPEAKER", "A Linux board runs JUCE DSP plugins")
    return o


def main():
    html = ("<!DOCTYPE html>\n<html><head><meta charset=\"utf-8\">\n" + FONT_LINK + "\n"
            "<style>body { margin: 0; background: #E2E9F1 }</style></head><body>\n"
            f'<div style="width: {W}px; height: {H}px">\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">\n'
            + scene() + "</svg>\n</div>\n</body></html>\n")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hero.html")
    with open(out, "w") as f:
        f.write(html)
    print(f"wrote {os.path.relpath(out)}")


if __name__ == "__main__":
    main()
