"""A self-contained SVG figure: stacked panels on one logarithmic frequency axis, written with
numpy and the standard library only (the development container has no plotting package).

    figure = Figure("title", ["subtitle line", ...], f_min=10, f_max=24000)
    panel = figure.panel("Magnitude response", "dB", y_min=-40, y_max=20, height=240)
    panel.curve(freqs, values_db, "label", color="#1f77b4", width=1.6, dash=None)
    panel.hline(-73, "limit", f_lo=100, f_hi=20000)
    panel.vline(50, "50 Hz")
    panel.note("a remark, under the legend")
    panel.center("a message in the middle of an empty plot")
    figure.write("out.svg")

Every panel's legend and notes are to the right of its plot, never over it; curves are clipped
to their panel; a value that is not finite breaks its curve. Subtitle lines are wrapped to the
figure's width.
"""

import math
from xml.sax.saxutils import escape

import numpy as np

FONT = "DejaVu Sans, Verdana, Arial, sans-serif"
WIDTH = 1200
LEFT, RIGHT = 84, 330          # the y labels' margin; the legends' column
TOP_PAD, GAP, BOTTOM = 18, 40, 56
LEGEND_CHARS = 40              # wrap a legend label beyond this many characters
SUBTITLE_CHARS = 160           # wrap a subtitle line beyond this many characters (13 px)
LINE = 16                      # a legend line


def _num(v):
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _wrap(text, chars):
    words, lines, line = text.split(), [], ""
    for word in words:
        if line and len(line) + 1 + len(word) > chars:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}" if line else word
    return lines + ([line] if line else [])


def _ticks(lo, hi, count=6):
    span = hi - lo
    raw = span / max(count, 1)
    magnitude = 10.0 ** math.floor(math.log10(raw))
    step = min((m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw), default=10 * magnitude)
    first = math.ceil(lo / step - 1e-9) * step
    ticks, v = [], first
    while v <= hi + 1e-9 * span:
        ticks.append(0.0 if abs(v) < 1e-12 * span else v)
        v += step
    return ticks


def _freq_label(f):
    return f"{f / 1000:g}k" if f >= 1000 else f"{f:g}"


class Panel:
    def __init__(self, figure, title, y_label, y_min, y_max, height):
        self.figure, self.title, self.y_label = figure, title, y_label
        self.y_min, self.y_max, self.height = y_min, y_max, height
        self.items, self.notes, self.centred = [], [], []

    def curve(self, freqs, values, label, color, width=1.5, dash=None, opacity=1.0):
        self.items.append(("curve", np.asarray(freqs, float), np.asarray(values, float), label, color, width, dash, opacity))

    def hline(self, y, label, f_lo=None, f_hi=None, color="#444444", width=1.2, dash="7,4"):
        self.items.append(("hline", y, f_lo, f_hi, label, color, width, dash))

    def vline(self, f, label, color="#777777"):
        self.items.append(("vline", f, label, color))

    def note(self, text):
        """A remark in the legend's column, under the legend."""
        self.notes.append(text)

    def center(self, text):
        """A line of text in the middle of the plot (when there is no curve to show)."""
        self.centred.append(text)


class Figure:
    def __init__(self, title, subtitle_lines, f_min, f_max, x_label="Frequency (Hz)"):
        self.title, self.f_min, self.f_max = title, f_min, f_max
        self.subtitle = [line for text in subtitle_lines for line in _wrap(text, SUBTITLE_CHARS)]
        self.x_label = x_label
        self.panels = []

    def panel(self, title, y_label, y_min, y_max, height=220):
        panel = Panel(self, title, y_label, y_min, y_max, height)
        self.panels.append(panel)
        return panel

    # -- geometry --------------------------------------------------------------------------

    def _x(self, f):
        width = WIDTH - LEFT - RIGHT
        return LEFT + width * (math.log10(f) - math.log10(self.f_min)) / (math.log10(self.f_max) - math.log10(self.f_min))

    def write(self, path):
        out = []
        width = WIDTH - LEFT - RIGHT
        header = TOP_PAD + 22 + 18 * len(self.subtitle) + 10
        height = header + sum(GAP + p.height for p in self.panels) + BOTTOM
        out.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
                   f'viewBox="0 0 {WIDTH} {height}" font-family="{FONT}">')
        out.append(f'<rect x="0" y="0" width="{WIDTH}" height="{height}" fill="#ffffff"/>')
        out.append(f'<text x="{LEFT}" y="{TOP_PAD + 16}" font-size="17" font-weight="bold">{escape(self.title)}</text>')
        for i, line in enumerate(self.subtitle):
            out.append(f'<text x="{LEFT}" y="{TOP_PAD + 22 + 18 * (i + 1)}" font-size="13">{escape(line)}</text>')

        majors = [f for f in (10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000) if self.f_min <= f <= self.f_max]
        minors = [m * 10 ** e for e in range(0, 6) for m in range(1, 10)
                  if self.f_min <= m * 10 ** e <= self.f_max and m * 10 ** e not in majors]
        top = header
        for n, panel in enumerate(self.panels):
            top += GAP
            bottom = top + panel.height
            last = n == len(self.panels) - 1

            def y_of(v, panel=panel, top=top):
                return top + panel.height * (panel.y_max - v) / (panel.y_max - panel.y_min)

            out.append(f'<text x="{LEFT}" y="{top - 9}" font-size="14" font-weight="bold">{escape(panel.title)}</text>')
            out.append(f'<rect x="{LEFT}" y="{top}" width="{width}" height="{panel.height}" fill="#ffffff" stroke="none"/>')
            for f in minors:
                x = self._x(f)
                out.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{bottom}" stroke="#ececec" stroke-width="1"/>')
            for f in majors:
                x = self._x(f)
                out.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{bottom}" stroke="#cfcfcf" stroke-width="1"/>')
                if last:
                    out.append(f'<text x="{x:.1f}" y="{bottom + 17}" font-size="12" text-anchor="middle">{_freq_label(f)}</text>')
            for v in _ticks(panel.y_min, panel.y_max, max(3, panel.height // 40)):
                y = y_of(v)
                out.append(f'<line x1="{LEFT}" y1="{y:.1f}" x2="{LEFT + width}" y2="{y:.1f}" stroke="#dedede" stroke-width="1"/>')
                out.append(f'<text x="{LEFT - 6}" y="{y + 4:.1f}" font-size="12" text-anchor="end">{_num(v)}</text>')
            middle = (top + bottom) / 2
            out.append(f'<text x="22" y="{middle:.1f}" font-size="13" text-anchor="middle" '
                       f'transform="rotate(-90 22 {middle:.1f})">{escape(panel.y_label)}</text>')

            clip = f"clip{n}"
            out.append(f'<clipPath id="{clip}"><rect x="{LEFT}" y="{top}" width="{width}" height="{panel.height}"/></clipPath>')
            out.append(f'<g clip-path="url(#{clip})">')
            legend = []
            for item in panel.items:
                kind = item[0]
                if kind == "curve":
                    _, freqs, values, label, color, w, dash, opacity = item
                    keep = (freqs >= self.f_min) & (freqs <= self.f_max)
                    freqs, values = freqs[keep], values[keep]
                    # Values far outside the panel are pulled to just beyond its edge, so that a
                    # line leaving it still leaves at the right angle and is clipped there.
                    span = panel.y_max - panel.y_min
                    values = np.clip(values, panel.y_min - span, panel.y_max + span)
                    runs, run = [], []
                    for f, v in zip(freqs, values):
                        if np.isfinite(v):
                            run.append(f"{self._x(f):.1f},{y_of(v):.1f}")
                        elif run:
                            runs.append(run)
                            run = []
                    if run:
                        runs.append(run)
                    style = f'fill="none" stroke="{color}" stroke-width="{w}" stroke-opacity="{opacity}" stroke-linejoin="round"'
                    if dash:
                        style += f' stroke-dasharray="{dash}"'
                    for r in runs:
                        out.append(f'<polyline {style} points="{" ".join(r)}"/>')
                    legend.append((label, color, w, dash, opacity))
                elif kind == "hline":
                    _, y, f_lo, f_hi, label, color, w, dash = item
                    x1 = self._x(f_lo) if f_lo else LEFT
                    x2 = self._x(f_hi) if f_hi else LEFT + width
                    out.append(f'<line x1="{x1:.1f}" y1="{y_of(y):.1f}" x2="{x2:.1f}" y2="{y_of(y):.1f}" stroke="{color}" '
                               f'stroke-width="{w}" stroke-dasharray="{dash}"/>')
                    if f_lo:
                        out.append(f'<line x1="{x1:.1f}" y1="{y_of(y) - 4:.1f}" x2="{x1:.1f}" y2="{y_of(y) + 4:.1f}" stroke="{color}" stroke-width="{w}"/>')
                    if f_hi:
                        out.append(f'<line x1="{x2:.1f}" y1="{y_of(y) - 4:.1f}" x2="{x2:.1f}" y2="{y_of(y) + 4:.1f}" stroke="{color}" stroke-width="{w}"/>')
                    if label:
                        legend.append((label, color, w, dash, 1.0))
            out.append("</g>")
            for item in panel.items:
                if item[0] == "vline":
                    _, f, label, color = item
                    x = self._x(f)
                    out.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{bottom}" stroke="{color}" stroke-width="1.2" stroke-dasharray="2,3"/>')
                    out.append(f'<rect x="{x + 3:.1f}" y="{top + 3}" width="{7 * len(label) + 6}" height="16" fill="#ffffff" fill-opacity="0.85"/>')
                    out.append(f'<text x="{x + 6:.1f}" y="{top + 15}" font-size="12" fill="#555555">{escape(label)}</text>')
            for i, text in enumerate(panel.centred):
                out.append(f'<text x="{LEFT + width / 2:.1f}" y="{middle + 18 * i:.1f}" font-size="14" text-anchor="middle" '
                           f'fill="#333333">{escape(text)}</text>')
            out.append(f'<rect x="{LEFT}" y="{top}" width="{width}" height="{panel.height}" fill="none" stroke="#555555" stroke-width="1"/>')

            lx, ly = LEFT + width + 16, top + 14
            for label, color, w, dash, opacity in legend:
                lines = _wrap(label, LEGEND_CHARS)
                if ly + LINE * (len(lines) - 1) > bottom + GAP - 22:
                    raise ValueError(f"the legend of '{panel.title}' does not fit beside its panel")
                style = f'stroke="{color}" stroke-width="{max(w, 1.5)}" stroke-opacity="{opacity}"'
                if dash:
                    style += f' stroke-dasharray="{dash}"'
                out.append(f'<line x1="{lx}" y1="{ly - 4}" x2="{lx + 28}" y2="{ly - 4}" {style}/>')
                for k, text in enumerate(lines):
                    out.append(f'<text x="{lx + 36}" y="{ly + LINE * k}" font-size="12">{escape(text)}</text>')
                ly += LINE * len(lines) + 6
            for text in panel.notes:
                lines = _wrap(text, LEGEND_CHARS + 2)
                if ly + LINE * (len(lines) - 1) > bottom + GAP - 22:
                    raise ValueError(f"the notes of '{panel.title}' do not fit beside its panel")
                for k, line in enumerate(lines):
                    out.append(f'<text x="{lx}" y="{ly + LINE * k}" font-size="12" font-style="italic" fill="#444444">{escape(line)}</text>')
                ly += LINE * len(lines) + 6
            top = bottom

        out.append(f'<text x="{LEFT + width / 2:.1f}" y="{top + 42}" font-size="13" text-anchor="middle">{escape(self.x_label)}</text>')
        out.append("</svg>")
        with open(path, "w") as f:
            f.write("\n".join(out) + "\n")
