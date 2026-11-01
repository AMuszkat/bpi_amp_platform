#!/usr/bin/env python3
"""Draws p1-result.svg, the figure at the top of docs/p1-block-fir.md.

    python3 docs/images/p1/p1-result.py

writes p1-result.svg next to this script. Standard library only. The numbers are the first
table of docs/p1-block-fir.md; where each comes from is said there, in "How the numbers were
obtained", and for the board in firmware/CLAUDE.md, "The board measured". Change them there
first, then here, and run this again.
"""

from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).with_name("p1-result.svg")

TITLE = "What P1 costs: simulated, measured, and on the board"
SUBTITLE = "Share of one CPU core at 48 kHz, today's code against P1. Lower is better."

# (group, label, sub-label lines, today %, P1 % as (low, high), what the P1 bar's label adds)
# today None: not measured.
ROWS = [
    ("Simulated", "Cortex-A73, 2.2 GHz", ["the four FIRs alone"], 28.2, (1.7, 1.7), "(16× less)"),
    ("Simulated", "Cortex-A53, 1.8 GHz", ["the four FIRs alone"], 61.9, (5.6, 5.6), "(11× less)"),
    ("Measured", "x86_64 development machine", ["the four FIRs alone"], 18.6, (1.3, 1.3), "(15× less)"),
    ("Measured", "The board", ["firConv and crossover nodes,", "the core not known"], None, (5.5, 5.9),
     "together"),
]

NOTES = [
    "Simulated: llvm-mca 18 on the compiled inner loops. LLVM models the A73 with its Cortex-A57 model, so",
    "read the A73 values as ±30–50 %; the ratios hold. x86_64: dsp/juce_plugins/tests/blockfir/run.sh --time,",
    "blocks of 1024. The board: speakerd's own timing after the first deploy (2026-11-01), the mean share of",
    "the 1024-sample cycle over two 60 s windows and the smoke check's. A node's share is all of its processing,",
    "not only its FIRs. Today's code was not measured on the board. The smoke check fails when the graph's",
    "longest cycle takes more than half of it. Drawn by docs/images/p1/p1-result.py.",
]

# The style of the other figures in this folder, light and dark.
STYLE = """text{font-family:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,"DejaVu Sans Mono",monospace}
.bg{fill:#fcfcfb;stroke:#e4e3df}
.t1{fill:#0b0b0b}.t2{fill:#52514e}.t3{fill:#7a7975}
.grid{stroke:#ebeae6;stroke-width:1}.axis{stroke:#c9c8c2;stroke-width:1}.ref{stroke:#7a7975;stroke-width:1}
.today{fill:#eb6834}.p1{fill:#2a78d6}.alt{fill:#b4b3ad}
.todayS{stroke:#eb6834}.p1S{stroke:#2a78d6}.lineS{stroke:#7a7975}
.cell{fill:#f0efec;stroke:#d6d5d0}.cellHi{fill:#d9e7f8;stroke:#2a78d6}.cellOld{fill:#e6e5e1;stroke:#c9c8c2}
.arrow{fill:#7a7975}
@media (prefers-color-scheme: dark){
.bg{fill:#1a1a19;stroke:#383835}
.t1{fill:#f0efec}.t2{fill:#c3c2b7}.t3{fill:#9a9993}
.grid{stroke:#2b2b29}.axis{stroke:#4a4a46}.ref{stroke:#9a9993}
.today{fill:#d95926}.p1{fill:#3987e5}.alt{fill:#5f5e5a}
.todayS{stroke:#d95926}.p1S{stroke:#3987e5}.lineS{stroke:#9a9993}
.cell{fill:#262624;stroke:#44443f}.cellHi{fill:#1d3a5f;stroke:#3987e5}.cellOld{fill:#2f2f2c;stroke:#4a4a46}
.arrow{fill:#9a9993}
}"""

WIDTH = 760
LEFT = 24.0
X0, X100 = 250.0, 690.0          # the axis: 0 % and 100 %
BAR, GAP, ROW = 18.0, 4.0, 58.0  # bar height, between today's and P1's, from one row to the next
GROUP = 30.0                     # the group's heading above its first row
TOP = 106.0                      # where the plot starts


def x_of(percent):
    return X0 + (X100 - X0) * percent / 100.0


def f(v):
    return f"{v:.1f}"


def text(x, y, s, cls, size, anchor="start", weight=None, italic=False):
    extra = f' font-weight="{weight}"' if weight else ""
    extra += ' font-style="italic"' if italic else ""
    return (f'<text x="{f(x)}" y="{f(y)}" class="{cls}" font-size="{size}" '
            f'text-anchor="{anchor}"{extra}>{escape(s)}</text>')


def bar(x1, y, h, cls, title=None, opacity=None):
    """A bar from the axis to x1, its right end rounded, as in the other figures."""
    w = x1 - X0
    r = min(4.0, w / 2.0, h / 2.0)
    d = (f"M{f(X0)},{f(y)} H{f(x1 - r)} A{f(r)},{f(r)} 0 0 1 {f(x1)},{f(y + r)} "
         f"V{f(y + h - r)} A{f(r)},{f(r)} 0 0 1 {f(x1 - r)},{f(y + h)} H{f(X0)} Z")
    op = f' opacity="{opacity}"' if opacity is not None else ""
    inner = f"<title>{escape(title)}</title>" if title else ""
    return f'<path d="{d}" class="{cls}"{op}>{inner}</path>'


def legend_key(x, y, cls):
    return (f'<path d="M{f(x)},{f(y)} H{f(x + 8)} A4.0,4.0 0 0 1 {f(x + 12)},{f(y + 4)} V{f(y + 8)} '
            f'A4.0,4.0 0 0 1 {f(x + 8)},{f(y + 12)} H{f(x)} Z" class="{cls}"></path>')


def draw():
    out = []
    # Rows, top to bottom: a group heading before the first row of each group.
    y = TOP
    placed = []
    group = None
    for row in ROWS:
        if row[0] != group:
            group = row[0]
            y += GROUP
            placed.append(("group", group, y - 10.0))
        placed.append(("row", row, y))
        y += ROW
    plot_bottom = y - ROW + 2 * BAR + GAP + 14.0
    ticks_y = plot_bottom + 18.0
    notes_y = ticks_y + 28.0
    height = notes_y + 15.0 * (len(NOTES) - 1) + 18.0

    desc = ("Share of one CPU core at 48 kHz, today's code (juce::dsp::FIR::Filter) against P1. "
            "Simulated, the four FIRs alone: Cortex-A73 28.2 % and 1.7 %, Cortex-A53 61.9 % and 5.6 %. "
            "Measured on x86_64, the four FIRs alone: 18.6 % and 1.3 %. Measured on the board with P1: "
            "the firConv and crossover nodes together 5.5 to 5.9 % of speakerd's cycle, the core not known; "
            "today's code not measured there.")
    out.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height:.0f}" '
               f'viewBox="0 0 {WIDTH} {height:.0f}" role="img" aria-labelledby="title desc">')
    out.append(f'<title id="title">{escape(TITLE)}</title>')
    out.append(f'<desc id="desc">{escape(desc)}</desc>')
    out.append(f"<style>\n{STYLE}\n</style>")
    out.append(f'<rect class="bg" x="0.5" y="0.5" width="{WIDTH - 1}" height="{height - 1:.0f}" rx="8"/>')
    out.append(text(LEFT, 34, TITLE, "t1", 17, weight=600))
    out.append(text(LEFT, 56, SUBTITLE, "t2", 13))
    out.append(legend_key(LEFT, 70, "today"))
    out.append(text(LEFT + 18, 81, "Today: juce::dsp::FIR::Filter, before 0.0.2", "t2", 12))
    out.append(legend_key(352, 70, "p1"))
    out.append(text(370, 81, "P1: block FIR with GCC vector types, 0.0.2 on", "t2", 12))

    # The grid, the two reference lines and the axis.
    for p in (0, 25, 50, 75, 100):
        cls = "ref" if p in (50, 100) else "grid"
        out.append(f'<line x1="{f(x_of(p))}" y1="{f(TOP)}" x2="{f(x_of(p))}" y2="{f(plot_bottom)}" class="{cls}" />')
        out.append(text(x_of(p), ticks_y, f"{p} %", "t3", 12, anchor="middle"))
    out.append(text(x_of(50), TOP - 6, "smoke check's limit", "t2", 11, anchor="middle"))
    out.append(text(x_of(100), TOP - 6, "the whole cycle", "t2", 11, anchor="end"))

    for kind, item, y in placed:
        if kind == "group":
            out.append(text(LEFT, y, item, "t2", 12, weight=600))
            continue
        _, label, subs, today, (lo, hi), extra = item
        out.append(text(LEFT, y + 14, label, "t1", 13))
        for i, s in enumerate(subs):
            out.append(text(LEFT, y + 31 + 14 * i, s, "t3", 11))
        if today is None:
            out.append(text(X0 + 6, y + 13, "not measured", "t3", 12, italic=True))
        else:
            out.append(bar(x_of(today), y, BAR, "today", f"{label}, today: {today} %"))
            out.append(text(x_of(today) + 6, y + 13, f"{today} %", "t1", 12))
        yp = y + BAR + GAP
        span = f"{lo}–{hi} %" if hi != lo else f"{lo} %"
        if hi != lo:
            out.append(bar(x_of(hi), yp, BAR, "p1", f"{label}, P1: {span}, the range of the windows", opacity=0.45))
        out.append(bar(x_of(lo), yp, BAR, "p1", f"{label}, P1: {span}"))
        out.append(f'<text x="{f(x_of(hi) + 6)}" y="{f(yp + 13)}" class="t1" font-size="12" text-anchor="start">'
                   f'<tspan font-weight="600">{escape(span)}</tspan>'
                   f'<tspan class="t2" dx="10">{escape(extra)}</tspan></text>')

    out.append(f'<line x1="{f(X0)}" y1="{f(TOP)}" x2="{f(X0)}" y2="{f(plot_bottom)}" class="axis" />')
    for i, s in enumerate(NOTES):
        out.append(text(LEFT, notes_y + 15 * i, s, "t3", 11))
    out.append("</svg>")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    OUT.write_text(draw(), encoding="utf-8")
    print(f"wrote {OUT}")
