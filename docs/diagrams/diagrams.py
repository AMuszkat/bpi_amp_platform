#!/usr/bin/env python3
"""The README's system diagrams, as placed HTML elements.

Three diagrams: the system from the listener's side (overview), the software inside one
speaker (speaker) and how the platform is developed, by a person and a team of AI agents
(agents). Each is one root <div> holding cards, bands and one small <svg> per
straight arrow segment, all at absolute pixel positions: the form a design canvas can edit
card by card, and what docs/diagrams/render.js turns into docs/images/*.png.

    python3 docs/diagrams/diagrams.py                 # writes docs/diagrams/<name>.html
    python3 docs/diagrams/diagrams.py --artboards DIR # also writes DIR/<Name>.dc.html

The visual language (also in each diagram's legend), after the "Lab Report" style of Claude
Design: a ruled ink frame is a device you can touch, a solid ink card a physical component, a
pale blue card software of this repository, a dashed card third-party software, a double-ruled
card a kernel module, a folded-corner card a file on disk. Blue arrows carry audio, dashed
orange ones settings, dotted grey ones data read once. The agents diagram keeps the cards
(the person solid ink, the agents pale blue, a test plain white, the boards a ruled frame)
and two arrow kinds, each labelled where it runs, so it needs no legend: solid ink, the work
moving on (and the report back to the person); dashed orange, the work sent back to be fixed.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# The "Lab Report" style (a Claude Design template): paper ground, one ink, serif text, monospace
# names, square corners. Physical things are solid ink; software is a pale blue card outlined in
# ink; third-party software is dashed; a kernel module has a double rule; a file has a folded corner.
FONT_SANS = "'IBM Plex Serif', Georgia, serif"
FONT_MONO = "'IBM Plex Mono', ui-monospace, 'SF Mono', Menlo, monospace"
FONT_LINK = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
             'family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Serif:ital,wght@0,400;0,500;0,700;1,400;1,500&display=swap">')

PAPER = "#EEF2F6"
INK = "#14223A"
MUTED = "#515C6F"     # ink 72 % over the paper
LABEL = "#3B475C"     # ink 82 % over the paper
TINT = "#E3E8EE"      # ink 5 % over the paper

# Card styles: (background, border, text colour, radius)
STYLES = {
    "ours":   ("#D6E2F0", f"1px solid {INK}", INK, 0),
    "third":  (PAPER, f"1px dashed {INK}", INK, 0),
    "kernel": ("#FFFFFF", f"3px double {INK}", INK, 0),
    "hw":     (INK, "none", PAPER, 0),
    "file":   ("#FFFFFF", "none", INK, 0),
    "label":  ("transparent", "none", INK, 0),
    "plain":  ("#FFFFFF", f"1px solid {INK}", INK, 0),
}

# Arrow styles: (stroke, width, dasharray or None)
ARROWS = {
    "audio":   ("#1F5FBF", 2, None),
    "control": ("#C0532B", 1.5, "6 4"),
    "once":    (MUTED, 1.5, "2 4"),
    "wire":    (INK, 1, None),
    "flow":    (INK, 2, None),
}

_marker_n = [0]


def px(v):
    return f"{int(v)}px"


def root(w, h, inner):
    return (f'<div style="position: relative; width: {px(w)}; height: {px(h)}; background: {PAPER}; '
            f'font-family: {FONT_SANS}; color: {INK}; overflow: hidden">\n' + inner + "</div>\n")


def title(x, y, text, sub=None):
    out = (f'<div style="position: absolute; left: {px(x)}; top: {px(y)}; font-size: 34px; line-height: 36px; '
           f'font-weight: 500; letter-spacing: -0.015em; color: {INK}">{text}</div>\n')
    if sub:
        out += (f'<div style="position: absolute; left: {px(x)}; top: {px(y + 40)}; font-size: 16px; '
                f'line-height: 22px; font-style: italic; color: {MUTED}">{sub}</div>\n')
    return out


def band(x, y, w, h, fill, name=None, border="none", radius=0, name_color=MUTED, name_dx=16):
    out = (f'<div style="position: absolute; left: {px(x)}; top: {px(y)}; width: {px(w)}; height: {px(h)}; '
           f'box-sizing: border-box; background: {fill}; border: {border}; border-radius: {px(radius)}"></div>\n')
    if name:
        out += (f'<div style="position: absolute; left: {px(x + name_dx)}; top: {px(y + 12)}; font-size: 11.5px; '
                f'line-height: 16px; font-family: {FONT_MONO}; font-weight: 500; letter-spacing: 0.16em; '
                f'text-transform: uppercase; color: {name_color}">{name}</div>\n')
    return out


def device(x, y, w, h, name, name_dx=16):
    """A physical device: a ruled ink frame around the software it runs."""
    return band(x, y, w, h, "transparent", name, border=f"1.5px solid {INK}", name_color=INK, name_dx=name_dx)


def card(x, y, w, h, html, kind, size=14):
    bg, border, color, radius = STYLES[kind]
    extra = ""
    if kind == "file":
        extra = " clip-path: polygon(0 0, calc(100% - 16px) 0, 100% 16px, 100% 100%, 0 100%);"
    return (f'<div style="position: absolute; left: {px(x)}; top: {px(y)}; width: {px(w)}; height: {px(h)}; '
            f'box-sizing: border-box; padding: 8px 12px; display: flex; align-items: center; '
            f'justify-content: center; text-align: center; background: {bg}; border: {border}; '
            f'border-radius: {px(radius)}; font-size: {size}px; line-height: {size + 6}px; color: {color};{extra}">'
            f'<span>{html}</span></div>\n')


def name(text):
    """A program's or a file's name, in the monospace face."""
    return f'<b style="font-family: {FONT_MONO}; font-weight: 600">{text}</b>'


def label(cx, top, text, w):
    return (f'<div style="position: absolute; left: {px(cx - w / 2)}; top: {px(top)}; width: {px(w)}; '
            f'text-align: center; font-size: 14px; line-height: 16px; font-style: italic; color: {LABEL}">{text}</div>\n')


def side_label(x, top, text, w):
    """A label beside a vertical segment, left-aligned at x."""
    return (f'<div style="position: absolute; left: {px(x)}; top: {px(top)}; width: {px(w)}; '
            f'font-size: 14px; line-height: 16px; font-style: italic; color: {LABEL}">{text}</div>\n')


def arrow(x1, y1, x2, y2, kind, head=True):
    """One straight segment from (x1, y1) to (x2, y2), the tip at (x2, y2) when it has a head.
    The svg spans the segment, 8px thick, the line along its middle."""
    stroke, width, dash = ARROWS[kind]
    length = max(11.0, width * 5.5)     # the head, in px
    lu = length / width                 # the head, in stroke widths (the marker's units)
    h = round(length / 2, 2) if head else 0
    style = (f"position: absolute; overflow: visible; fill: none; stroke: {stroke}; stroke-width: {width}; "
             f"stroke-linecap: butt; stroke-linejoin: miter")
    if dash:
        style += f"; stroke-dasharray: {dash}"
    defs = marker_end = ""
    if head:
        _marker_n[0] += 1
        mid = f"dc-arrow-head-filled-{_marker_n[0]}"
        defs = (f'<defs><marker id="{mid}" orient="auto" markerWidth="{lu:g}" markerHeight="{lu:g}" refX="{lu / 2:g}" '
                f'refY="{lu * 0.31:g}" overflow="visible"><path d="M0 0 L{lu:g} {lu * 0.31:g} L0 {lu * 0.62:g} Z" '
                f'fill="{stroke}" stroke="none" style="stroke: none; fill: {stroke}; fill: context-stroke"/></marker></defs>')
        marker_end = f' marker-end="url(#{mid})"'
    if y1 == y2:
        n = abs(x2 - x1)
        left, top = min(x1, x2), y1 - 4
        path = f"M 0 4 L {n - h} 4" if x2 > x1 else f"M {n} 4 L {h} 4"
        size = f'width="{n}" height="8" viewBox="0 0 {n} 8"'
        dims = f"left: {px(left)}; top: {px(top)}; width: {px(n)}; height: 8px"
    elif x1 == x2:
        n = abs(y2 - y1)
        left, top = x1 - 4, min(y1, y2)
        path = f"M 4 0 L 4 {n - h}" if y2 > y1 else f"M 4 {n} L 4 {h}"
        size = f'width="8" height="{n}" viewBox="0 0 8 {n}"'
        dims = f"left: {px(left)}; top: {px(top)}; width: 8px; height: {px(n)}"
    else:
        raise ValueError(f"segments are horizontal or vertical: ({x1},{y1})-({x2},{y2})")
    return (f'<svg {size} preserveAspectRatio="none" style="{dims}; {style}">{defs}'
            f'<path d="{path}"{marker_end}></path></svg>\n')


def legend(x, y, items, arrows):
    """Swatches with their meaning in one row, arrow samples in a second."""
    out = ""
    for i, (kind, text) in enumerate(items):
        sx = x + i * 256
        if kind == "device":
            bg, border, radius = "transparent", f"1.5px solid {INK}", 0
        else:
            bg, border, _, radius = STYLES[kind]
        extra = ""
        if kind == "file":
            extra = " clip-path: polygon(0 0, calc(100% - 8px) 0, 100% 8px, 100% 100%, 0 100%);"
        out += (f'<div style="position: absolute; left: {px(sx)}; top: {px(y)}; width: 24px; height: 24px; '
                f'box-sizing: border-box; background: {bg}; border: {border}; border-radius: {px(radius)};{extra}"></div>\n')
        out += (f'<div style="position: absolute; left: {px(sx + 36)}; top: {px(y)}; font-size: 13px; '
                f'line-height: 24px; color: {INK}">{text}</div>\n')
    for i, (kind, text) in enumerate(arrows):
        sx = x + i * 256
        out += arrow(sx, y + 60, sx + 56, y + 60, kind)
        out += (f'<div style="position: absolute; left: {px(sx + 68)}; top: {px(y + 48)}; font-size: 13px; '
                f'line-height: 24px; color: {INK}">{text}</div>\n')
    return out


def overview():
    """The system from the listener's side: 1600 x 1120."""
    o = ""
    o += title(64, 56, "The system, from the listener's side",
               "Two speakers, each a board with two amplifiers, playing one stream and tuned from one computer.")

    # The music source, above the speakers.
    o += device(560, 150, 480, 122, "A computer or a phone")
    o += card(592, 184, 416, 64, "<b>Snapcast server</b>, or an AirPlay sender<br>streams the music to both speakers", "third")

    # The two speakers.
    o += device(80, 360, 688, 328, "Left speaker: the hub", name_dx=192)
    o += device(832, 360, 688, 328, "Right speaker", name_dx=192)
    for dx, hub in ((0, True), (752, False)):
        o += card(112 + dx, 408, 272, 80, f"{name('speakerd')}<br>the headless plugin host:<br>crossover · firConv · loudness", "ours")
        o += card(432 + dx, 408, 160, 80, f"<b>2 × MAX98397</b><br>class-D amplifiers", "hw")
        o += card(672 + dx, 410, 64, 28, "woofer", "hw", size=13)
        o += card(672 + dx, 458, 64, 28, "tweeter", "hw", size=13)
        if hub:
            o += card(112 + dx, 560, 272, 80, f"{name('ampd')}<br>the network agent and the hub:<br>keeps both speakers in step", "ours")
        else:
            o += card(112 + dx, 560, 272, 80, f"{name('ampd')}<br>the network agent:<br>follows the hub", "ours")
        # speakerd -> amplifiers (audio), amplifiers -> woofer and tweeter (wires, one to many)
        o += arrow(384 + dx, 448, 431 + dx, 448, "audio")
        o += arrow(592 + dx, 448, 632 + dx, 448, "wire", head=False)
        o += arrow(632 + dx, 424, 632 + dx, 472, "wire", head=False)
        o += arrow(632 + dx, 424, 671 + dx, 424, "wire")
        o += arrow(632 + dx, 472, 671 + dx, 472, "wire")
        # ampd -> speakerd (settings, over the local socket)
        o += arrow(248 + dx, 560, 248 + dx, 489, "control")
        o += side_label(260 + dx, 516, "local socket", 96)

    # The stream: one source, two speakers.
    o += arrow(800, 248, 800, 328, "audio", head=False)
    o += arrow(248, 328, 1000, 328, "audio", head=False)
    o += arrow(248, 328, 248, 407, "audio")
    o += arrow(1000, 328, 1000, 407, "audio")
    o += label(520, 300, "stereo stream, over the network", 232)

    # The hub fans every change out to the other speaker.
    o += arrow(384, 600, 863, 600, "control")
    o += label(624, 572, "fan-out: every edit, every knob", 232)

    # Your computer: the controller and the tuning tools; the microphone beside it.
    o += device(80, 776, 688, 160, "Your computer")
    o += card(112, 824, 272, 80, f"{name('controller')}<br>a plugin host GUI: add plugins,<br>wire them, turn the knobs", "ours")
    o += card(432, 824, 304, 80, "<b>measurement and tuning GUIs</b><br>design the filters<br>the plugins compile in", "ours")
    o += card(848, 824, 176, 80, "<b>microphone and<br>audio interface</b>", "hw")
    o += arrow(248, 824, 248, 641, "control")
    o += side_label(260, 724, "edits, knobs; meters back", 180)
    o += arrow(848, 864, 737, 864, "audio")
    o += label(808, 836, "sweeps", 56)

    o += legend(80, 984,
                [("device", "a device you can touch"), ("hw", "a physical component"),
                 ("ours", "software of this repository"), ("third", "third-party software")],
                [("audio", "audio"), ("control", "settings and control"), ("wire", "wires")])
    return root(1600, 1120, o)


def speaker():
    """Inside a speaker: user space, kernel space, hardware. 1472 x 1080."""
    o = ""
    o += title(64, 56, "Inside a speaker",
               "One board. What runs in user space, what the kernel does, and what is hardware.")

    # The three bands.
    o += band(64, 150, 1344, 390, TINT, "User space", name_color=INK)
    o += band(64, 564, 1344, 144, TINT, "Kernel space", name_color=INK)
    o += band(64, 732, 1344, 168, TINT, "Hardware", name_color=INK)

    # The network, at the left of user space.
    o += band(80, 196, 120, 324, PAPER, "Network", border=f"1px dashed {INK}")
    o += (f'<div style="position: absolute; left: 96px; top: 240px; width: 92px; font-size: 12px; line-height: 18px; '
          f'font-style: italic; color: {LABEL}">the Snapcast server, the hub, the controller</div>\n')

    # User space, row 1: the audio path through PipeWire.
    o += band(560, 160, 808, 160, "transparent", "PipeWire, the audio server: its graph", border=f"1px dashed {INK}")
    o += card(320, 196, 192, 80, f"{name('snapclient')}<br>plays the stream<br>into the sink", "third")
    o += card(592, 196, 160, 80, f"<b>speakerd sink</b><br>the sink players<br>play into", "third")
    o += card(800, 196, 288, 80, f"{name('speakerd')}<br>the headless plugin host:<br>crossover · firConv · loudness", "ours")
    o += card(1152, 196, 184, 80, f"<b>amplifier node</b><br>PipeWire's ALSA sink", "third")
    o += arrow(200, 236, 319, 236, "audio")
    o += label(260, 208, "stereo stream", 104)
    o += arrow(512, 236, 591, 236, "audio")
    o += arrow(752, 236, 799, 236, "audio")
    o += arrow(1088, 236, 1151, 236, "audio")
    o += arrow(1244, 276, 1244, 587, "audio")
    o += side_label(1256, 423, "ALSA PCM", 80)

    # User space, row 2: ampd, the state files, the network.
    o += card(800, 384, 176, 96, f"{name('ampd')}<br>the network agent,<br>and the hub on the<br>left speaker", "ours")
    o += card(1048, 392, 144, 80, f"<b>state files</b><br>{name('graph.json')}<br>{name('device.json')}", "file", size=13)
    o += arrow(888, 384, 888, 277, "control")
    o += side_label(900, 322, "local socket", 96)
    o += arrow(976, 432, 1047, 432, "control")
    o += label(1012, 444, "writes", 56)
    o += arrow(1120, 392, 1120, 340, "once", head=False)
    o += arrow(1120, 340, 992, 340, "once", head=False)
    o += arrow(992, 340, 992, 277, "once")
    o += label(1056, 352, "read at boot", 96)
    o += arrow(800, 432, 201, 432, "control")
    o += label(500, 404, "the controller and the other speaker: TCP, OSC/UDP", 384)

    # Kernel space.
    o += card(320, 596, 416, 80, "<b>device tree</b><br>declares the sound card, the TDM bus<br>and the two amplifiers on I²C", "kernel")
    o += card(1108, 588, 272, 96, "<b>ALSA sound card</b><br>the axg sound-card driver<br>and the max98396 codec driver<br>(kernel modules)", "kernel")
    o += arrow(736, 636, 1107, 636, "once")
    o += label(921, 608, "configures", 88)
    o += arrow(1244, 684, 1244, 775, "audio")
    o += side_label(1256, 722, "drives", 56)

    # Hardware.
    o += card(96, 768, 272, 96, "<b>power</b><br>boost converter for<br>the amplifiers, 5 V buck<br>for the module", "hw")
    o += card(420, 760, 136, 40, "woofer", "hw")
    o += card(420, 832, 136, 40, "tweeter", "hw")
    o += card(676, 776, 272, 80, "<b>2 × MAX98397</b><br>class-D amplifiers on the<br>speaker amp board", "hw")
    o += card(1108, 776, 272, 80, "<b>BPI-CM4 module</b><br>Amlogic A311D, on the<br>base board", "hw")
    o += arrow(1108, 816, 949, 816, "wire")
    o += label(1028, 788, "TDM audio + I²C", 128)
    o += arrow(676, 816, 616, 816, "wire", head=False)
    o += arrow(616, 780, 616, 852, "wire", head=False)
    o += arrow(616, 780, 557, 780, "wire")
    o += arrow(616, 852, 557, 852, "wire")

    o += legend(80, 948,
                [("ours", "software of this repository"), ("third", "third-party software"),
                 ("kernel", "kernel module"), ("hw", "hardware"), ("file", "files on disk")],
                [("audio", "audio"), ("control", "settings and control"),
                 ("once", "read once: at boot, at build"), ("wire", "wires on the board")])
    return root(1472, 1080, o)


def step(x, y, w, h, head, line, kind, head_size=22, line_size=15, icon="", pad=14):
    """A card with a title and at most one short line under it, both centred."""
    bg, border, color, radius = STYLES[kind]
    sub = PAPER if kind == "hw" else LABEL
    body = icon + (f'<div style="font-size: {head_size}px; line-height: {head_size + 6}px; font-weight: 500">{head}</div>')
    if line:
        body += (f'<div style="margin-top: 4px; font-size: {line_size}px; line-height: {line_size + 5}px; '
                 f'color: {sub}">{line}</div>')
    return (f'<div style="position: absolute; left: {px(x)}; top: {px(y)}; width: {px(w)}; height: {px(h)}; '
            f'box-sizing: border-box; padding: 8px {pad}px; display: flex; flex-direction: column; '
            f'align-items: center; justify-content: center; text-align: center; background: {bg}; '
            f'border: {border}; border-radius: {px(radius)}; color: {color}">{body}</div>\n')


def heading(x, y, w, text, line=None, size=22):
    """A group's title, centred in its band, with an optional italic line under it."""
    out = (f'<div style="position: absolute; left: {px(x)}; top: {px(y)}; width: {px(w)}; text-align: center; '
           f'font-size: {size}px; line-height: {size + 6}px; font-weight: 500; color: {INK}">{text}</div>\n')
    if line:
        out += (f'<div style="position: absolute; left: {px(x)}; top: {px(y + size + 8)}; width: {px(w)}; '
                f'text-align: center; font-size: 15px; line-height: 20px; font-style: italic; color: {LABEL}">{line}</div>\n')
    return out


def kicker(cx, y, text, w=200):
    """The step number above a column."""
    return (f'<div style="position: absolute; left: {px(cx - w / 2)}; top: {px(y)}; width: {px(w)}; text-align: center; '
            f'font-size: 12px; line-height: 16px; font-family: {FONT_MONO}; font-weight: 500; letter-spacing: 0.16em; '
            f'text-transform: uppercase; color: {MUTED}">{text}</div>\n')


PERSON = (f'<svg width="34" height="34" viewBox="0 0 34 34" style="display: block; margin: 0 auto 6px; fill: {PAPER}">'
          '<circle cx="17" cy="10" r="7"></circle><path d="M3 34 C3 24 9 19 17 19 C25 19 31 24 31 34 Z"></path></svg>')


def agents():
    """How the platform is developed: a person and a team of AI agents, left to right, with the
    fix loop (a failed test or a review sends the work back) and the report back to the person.
    1600 x 848."""
    o = ""
    o += title(64, 56, "How the platform is developed",
               "A person sets the goal; a team of AI agents plans, builds, tests and ships it.")

    mid = 386          # the row's centre line: every step is centred on it
    top, bot = 176, 596  # the two groups' bands

    # 1. You.
    o += kicker(148, 148, "Step 1")
    o += step(64, 286, 168, 200, "You", "set the goal, approve the result", "hw", head_size=24, icon=PERSON)
    o += arrow(232, mid, 279, mid, "flow")

    # 2. The orchestrator.
    o += kicker(384, 148, "Step 2")
    o += step(280, 296, 208, 180, "Orchestrator", "plans the work and splits it between the specialists", "ours")

    # 3. The specialists, in parallel: the orchestrator's work fans out to each.
    s3x, s3w = 536, 360
    o += kicker(s3x + s3w / 2, 148, "Step 3")
    o += band(s3x, top, s3w, bot - top, TINT)
    o += heading(s3x, top + 16, s3w, "Specialist agents", "in parallel, each in its own copy of the code")
    specialists = [
        ("Plugins", "the sound-processing plugins"),
        ("Speaker engine", f"{name('speakerd')}, runs the plugins on a speaker"),
        ("Controller app", "tunes the speakers from a computer"),
        ("Board software", f"{name('ampd')}, the installers, the virtual speakers"),
        ("Pictures", "board renders and these diagrams"),
    ]
    cy0, ch, gap = 254, 58, 10
    cx1, cx2 = s3x + 20, s3x + s3w - 20      # the cards' edges
    join = s3x + s3w + 24                    # where their work joins again
    for i, (head, line) in enumerate(specialists):
        y = cy0 + i * (ch + gap)
        o += step(cx1, y, cx2 - cx1, ch, head, line, "ours", head_size=17, line_size=14)
        o += arrow(512, y + ch // 2, cx1 - 1, y + ch // 2, "flow")
        o += arrow(cx2, y + ch // 2, join, y + ch // 2, "flow", head=False)
    first, last = cy0 + ch // 2, cy0 + 4 * (ch + gap) + ch // 2
    o += arrow(488, mid, 512, mid, "flow", head=False)
    o += arrow(512, first, 512, last, "flow", head=False)
    o += arrow(join, first, join, last, "flow", head=False)

    # 4. Tested and reviewed, in order.
    s4x, s4w = join + 24, 312
    s4c = s4x + s4w // 2
    o += arrow(join, mid, s4x - 1, mid, "flow")
    o += kicker(s4c, 148, "Step 4")
    o += band(s4x, top, s4w, bot - top, TINT)
    o += heading(s4x, top + 16, s4w, "Tested and reviewed")
    checks = [
        ("Two virtual speakers", "every change runs on one computer", "plain"),
        ("Reviewer agent", "reads every change", "ours"),
        ("Checked again online", "after the push, by CI", "plain"),
    ]
    for i, (head, line, kind) in enumerate(checks):
        y = 232 + i * 116
        o += step(s4x + 16, y, s4w - 32, 84, head, line, kind, head_size=17, line_size=14)
        if i:
            o += arrow(s4c, y - 32, s4c, y - 1, "flow")

    # 5. The real speakers.
    s5x = s4x + s4w + 36
    s5w = 1536 - s5x
    s5c = s5x + s5w // 2
    o += arrow(s4x + s4w, mid, s5x - 1, mid, "flow")
    o += kicker(s5c, 148, "Step 5")
    o += device(s5x, 226, s5w, 320, None)
    o += heading(s5x, 242, s5w, "Real speakers")
    o += step(s5x + 12, 290, s5w - 24, 108, "Board operations agent", "deploys to the boards and checks them", "ours",
              head_size=17, line_size=14, pad=8)
    o += arrow(s5c, 398, s5c, 443, "flow")
    o += step(s5x + 12, 444, s5w - 24, 76, "two boards", "left and right speaker", "hw", head_size=17, line_size=14)

    # The fix loop: a failed test or a review comment sends the work back to the specialists.
    fix = 624
    o += arrow(s4c, bot, s4c, fix, "control", head=False)
    o += arrow(s4c, fix, s3x + s3w // 2, fix, "control", head=False)
    o += arrow(s3x + s3w // 2, fix, s3x + s3w // 2, bot + 1, "control")
    o += label((s3x + s3w // 2 + s4c) / 2, fix + 10, "a failed test or a review sends it back to be fixed", 440)

    # The report, back to the person.
    rep = 680
    o += arrow(s5c, 546, s5c, rep, "flow", head=False)
    o += arrow(s5c, rep, 148, rep, "flow", head=False)
    o += arrow(148, rep, 148, 487, "flow")
    o += label(332, rep - 26, "report: the results come back to you", 320)

    # The guardrails, under everything.
    o += band(64, 724, 1472, 76, TINT, "Guardrails, always on", name_color=INK)
    rails = [
        "No private data ever goes in the repository",
        "Nothing reaches the main branch without a review",
        "While the boards are on the bench, a lab lock keeps the agents off them",
    ]
    o += (f'<div style="position: absolute; left: {px(80)}; top: {px(758)}; width: {px(1440)}; display: flex; '
          f'justify-content: space-between; font-size: 16px; line-height: 22px; color: {INK}">'
          + "".join(f'<span><span style="color: {MUTED}">&#9632;&nbsp;</span>{text}</span>' for text in rails)
          + '</div>\n')
    return root(1600, 848, o)


DIAGRAMS = {
    "overview": (overview, 1600, 1120, "The system, from the listener's side"),
    "speaker": (speaker, 1472, 1080, "Inside a speaker"),
    "agents": (agents, 1600, 848, "How the platform is developed"),
}


def standalone(body, w, h, doc_title):
    return (f'<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<title>{doc_title}</title>\n'
            f'{FONT_LINK}\n<style>body{{margin:0;background:#FFFFFF}}</style>\n</head>\n<body>\n{body}</body>\n</html>\n')


def artboard(body, w, h, doc_title):
    """The same diagram as a Design canvas artboard (a Design Component page)."""
    return (f'<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<title>{doc_title}</title>\n'
            f'<script src="./support.js"></script>\n</head>\n<body>\n<x-dc>\n<helmet>\n{FONT_LINK}\n'
            f'<style>\nbody{{margin:0}}\n</style>\n</helmet>\n{body}</x-dc>\n'
            f'<script type="text/x-dc" data-dc-script data-props=\'{{"$preview":{{"width":{w},"height":{h}}}}}\'>\n'
            f'class Component extends DCLogic {{\nrenderVals() {{\nreturn {{}};\n}}\n}}\n</script>\n</body>\n</html>\n')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--artboards", metavar="DIR", help="also write the Design canvas artboards into DIR")
    args = ap.parse_args()
    for key, (fn, w, h, doc_title) in DIAGRAMS.items():
        _marker_n[0] = 0
        body = fn()
        path = os.path.join(HERE, f"{key}.html")
        with open(path, "w") as f:
            f.write(standalone(body, w, h, doc_title))
        print(f"wrote {os.path.relpath(path)}")
        if args.artboards:
            os.makedirs(args.artboards, exist_ok=True)
            ab = os.path.join(args.artboards, f"{key.capitalize()}.dc.html")
            with open(ab, "w") as f:
                f.write(artboard(body, w, h, doc_title))
            print(f"wrote {ab}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
