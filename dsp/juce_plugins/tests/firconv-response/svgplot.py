"""A self-contained SVG figure: stacked panels on one logarithmic frequency axis, written with
numpy and the standard library only (the development container has no plotting package).

    figure = Figure("title", ["subtitle", ...], f_min=10, f_max=24000)
    panel = figure.panel("Magnitude response", "dB", y_min=-90, y_max=10, height=320)
    panel.curve(freqs, values_db, "label", color="#1f77b4", width=1.6, dash=None)
    panel.hline(0.05, "limit", f_lo=20, f_hi=20000)
    figure.write("out.svg")

Each panel's legend is to the right of its plot, never over it; curves are clipped to their
panel; a value that is not finite breaks its curve; long subtitles and labels are wrapped.
"""

import math
from xml.sax.saxutils import escape

import numpy as np

FONT = "DejaVu Sans, Verdana, Arial, sans-serif"
WIDTH, LEFT, RIGHT = 1200, 84, 330     # the figure; the y labels' margin; the legends' column
TOP_PAD, GAP, BOTTOM, LINE = 18, 40, 56, 16
LEGEND_CHARS, SUBTITLE_CHARS = 40, 160


def _wrap(text, chars):
    lines, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > chars:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}" if line else word
    return lines + ([line] if line else [])


def _ticks(lo, hi, count):
    raw = (hi - lo) / count
    magnitude = 10.0 ** math.floor(math.log10(raw))
    step = min(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    return [round(k * step, 9) for k in range(math.ceil(lo / step - 1e-9), math.floor(hi / step + 1e-9) + 1)]


class Panel:
    def __init__(self, title, y_label, y_min, y_max, height):
        self.title, self.y_label, self.y_min, self.y_max, self.height = title, y_label, y_min, y_max, height
        self.items = []

    def curve(self, freqs, values, label, color, width=1.5, dash=None):
        self.items.append(("curve", np.asarray(freqs, float), np.asarray(values, float), label, color, width, dash))

    def hline(self, y, label, f_lo, f_hi, color="#444444", width=1.2, dash="7,4"):
        self.items.append(("hline", y, f_lo, f_hi, label, color, width, dash))


class Figure:
    def __init__(self, title, subtitles, f_min, f_max, x_label="Frequency (Hz)"):
        self.title, self.f_min, self.f_max, self.x_label = title, f_min, f_max, x_label
        self.subtitle = [line for text in subtitles for line in _wrap(text, SUBTITLE_CHARS)]
        self.panels = []

    def panel(self, title, y_label, y_min, y_max, height=220):
        self.panels.append(Panel(title, y_label, y_min, y_max, height))
        return self.panels[-1]

    def _x(self, f):
        span = math.log10(self.f_max) - math.log10(self.f_min)
        return LEFT + (WIDTH - LEFT - RIGHT) * (math.log10(f) - math.log10(self.f_min)) / span

    def write(self, path):
        width = WIDTH - LEFT - RIGHT
        header = TOP_PAD + 32 + 18 * len(self.subtitle)
        height = header + sum(GAP + p.height for p in self.panels) + BOTTOM
        out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
               f'viewBox="0 0 {WIDTH} {height}" font-family="{FONT}">',
               f'<rect x="0" y="0" width="{WIDTH}" height="{height}" fill="#ffffff"/>',
               f'<text x="{LEFT}" y="{TOP_PAD + 16}" font-size="17" font-weight="bold">{escape(self.title)}</text>']
        for i, line in enumerate(self.subtitle):
            out.append(f'<text x="{LEFT}" y="{TOP_PAD + 40 + 18 * i}" font-size="13">{escape(line)}</text>')

        majors = [f for f in (10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000) if self.f_min <= f <= self.f_max]
        minors = [m * 10 ** e for e in range(6) for m in range(1, 10)
                  if self.f_min <= m * 10 ** e <= self.f_max and m * 10 ** e not in majors]
        top = header
        for n, panel in enumerate(self.panels):
            top += GAP
            bottom, middle = top + panel.height, top + panel.height / 2

            def y_of(v, panel=panel, top=top):
                return top + panel.height * (panel.y_max - v) / (panel.y_max - panel.y_min)

            out.append(f'<text x="{LEFT}" y="{top - 9}" font-size="14" font-weight="bold">{escape(panel.title)}</text>')
            for f in minors + majors:
                shade = "#cfcfcf" if f in majors else "#ececec"
                out.append(f'<line x1="{self._x(f):.1f}" y1="{top}" x2="{self._x(f):.1f}" y2="{bottom}" stroke="{shade}"/>')
                if f in majors and n == len(self.panels) - 1:
                    label = f"{f / 1000:g}k" if f >= 1000 else f"{f:g}"
                    out.append(f'<text x="{self._x(f):.1f}" y="{bottom + 17}" font-size="12" text-anchor="middle">{label}</text>')
            for v in _ticks(panel.y_min, panel.y_max, max(3, panel.height // 40)):
                out.append(f'<line x1="{LEFT}" y1="{y_of(v):.1f}" x2="{LEFT + width}" y2="{y_of(v):.1f}" stroke="#dedede"/>')
                out.append(f'<text x="{LEFT - 6}" y="{y_of(v) + 4:.1f}" font-size="12" text-anchor="end">{v:g}</text>')
            out.append(f'<text x="22" y="{middle:.1f}" font-size="13" text-anchor="middle" '
                       f'transform="rotate(-90 22 {middle:.1f})">{escape(panel.y_label)}</text>')

            out.append(f'<clipPath id="clip{n}"><rect x="{LEFT}" y="{top}" width="{width}" height="{panel.height}"/></clipPath>')
            out.append(f'<g clip-path="url(#clip{n})">')
            legend = []
            for item in panel.items:
                if item[0] == "curve":
                    _, freqs, values, label, color, w, dash = item
                    keep = (freqs >= self.f_min) & (freqs <= self.f_max)
                    # far outside the panel: just beyond its edge, so that the line leaves at the
                    # right angle and is clipped there
                    span = panel.y_max - panel.y_min
                    values = np.clip(values[keep], panel.y_min - span, panel.y_max + span)
                    runs, run = [], []
                    for f, v in zip(freqs[keep], values):
                        if np.isfinite(v):
                            run.append(f"{self._x(f):.1f},{y_of(v):.1f}")
                        elif run:
                            runs, run = runs + [run], []
                    style = f'fill="none" stroke="{color}" stroke-width="{w}" stroke-linejoin="round"'
                    style += f' stroke-dasharray="{dash}"' if dash else ""
                    out += [f'<polyline {style} points="{" ".join(r)}"/>' for r in runs + [run] if r]
                else:
                    _, y, f_lo, f_hi, label, color, w, dash = item
                    x1, x2 = self._x(f_lo), self._x(f_hi)
                    out.append(f'<line x1="{x1:.1f}" y1="{y_of(y):.1f}" x2="{x2:.1f}" y2="{y_of(y):.1f}" '
                               f'stroke="{color}" stroke-width="{w}" stroke-dasharray="{dash}"/>')
                    out += [f'<line x1="{x:.1f}" y1="{y_of(y) - 4:.1f}" x2="{x:.1f}" y2="{y_of(y) + 4:.1f}" '
                            f'stroke="{color}" stroke-width="{w}"/>' for x in (x1, x2)]
                if label:
                    legend.append((label, color, w, dash))
            out.append("</g>")
            out.append(f'<rect x="{LEFT}" y="{top}" width="{width}" height="{panel.height}" fill="none" stroke="#555555"/>')

            lx, ly = LEFT + width + 16, top + 14
            for label, color, w, dash in legend:
                lines = _wrap(label, LEGEND_CHARS)
                if ly + LINE * (len(lines) - 1) > bottom + GAP - 22:
                    raise ValueError(f"the legend of '{panel.title}' does not fit beside its panel")
                style = f'stroke="{color}" stroke-width="{max(w, 1.5)}"' + (f' stroke-dasharray="{dash}"' if dash else "")
                out.append(f'<line x1="{lx}" y1="{ly - 4}" x2="{lx + 28}" y2="{ly - 4}" {style}/>')
                out += [f'<text x="{lx + 36}" y="{ly + LINE * k}" font-size="12">{escape(text)}</text>'
                        for k, text in enumerate(lines)]
                ly += LINE * len(lines) + 6
            top = bottom

        out.append(f'<text x="{LEFT + width / 2:.1f}" y="{top + 42}" font-size="13" text-anchor="middle">{escape(self.x_label)}</text>')
        out.append("</svg>")
        with open(path, "w") as f:
            f.write("\n".join(out) + "\n")
