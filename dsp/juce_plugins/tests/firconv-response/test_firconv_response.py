"""firConv's response, two builds compared, through speakerd (dsp/juce_plugins/README.md, "The
response test"). Run by run.sh, which builds first (builds.py) and sets FRESP_OUT:

    dsp/juce_plugins/tests/firconv-response/run.sh [--base REF]

For each plugin of PLUGINS it renders one input (response.Excitation: a sweep and white noise)
with `speakerd --render` through four builds: the current tree and the base revision, each for
x86_64 and aarch64 (under qemu-aarch64-static), compares three pairs of them, writes one SVG
figure and one CSV per pair, and asserts the limits of the plugin's table entry:

    aarch64, current against base     the change the board will hear (the main figure)
    x86_64, current against base      bit for bit, where the table says so
    current, aarch64 against x86_64   the architecture's own difference, for context

and, for each of the four builds, that it matches the design (the plugin's coefficients as the
tuning GUI graphs them, with scipy), that its high-pass is where the coefficients put it, and
that nothing clips. How the spectra are estimated: response.py.

A second plugin is a PLUGINS entry: its graph parameters, the amplifier inputs its outputs go to,
its design function, its expectations and its limits (crossover's design would be freqz of its
two FIRs, one per output).
"""

import csv
import json
import os
import unittest
from pathlib import Path

import numpy as np

import response as r
import svgplot

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OUT = Path(os.environ.get("FRESP_OUT", ROOT / "build" / "tests" / "firconv-response"))
LANDMARKS_HZ = (20.0, 25.0, 50.0, 100.0, 1000.0, 10000.0)


def firconv_design(src_dir, freqs, params):
    """firConv's design at `freqs`: one response per output, the parts the corner check and the
    figures use (response.firconv_design), and a note on the GUI's direct form of the high-pass,
    which the design does not use."""
    d = r.firconv_design(src_dir, freqs, params["gain"])
    design = {"outputs": [d["chain"]], "outputs_f32": [d["chain_f32"]],
              "corner_part": d["hp_sos"], "corner_rest": d["firs"], "hp_ba": d["hp_ba"], "notes": []}
    if len(freqs) > 100:     # on the FFT bins, not at a few landmarks
        den = r.header_arrays(Path(src_dir) / "filters.hpp")["HPreHpDen"]
        with np.errstate(divide="ignore", invalid="ignore"):      # both are 0 at DC
            apart = np.abs(20.0 * np.log10(np.abs(d["hp_ba"]) / np.abs(d["hp_sos"])))
        apart[~np.isfinite(apart)] = 0.0
        k25 = int(np.argmin(np.abs(np.asarray(freqs) - 25.0)))
        above = float(np.asarray(freqs)[np.flatnonzero(apart > 0.01)[-1]]) if np.any(apart > 0.01) else 0.0
        design["notes"].append(
            f"The GUI's direct form of the high-pass from the same filters.hpp (HPreHpNum/HPreHpDen) is not "
            f"the design: in its 8 decimals the largest pole is at |z| = {np.max(np.abs(np.roots(den))):.6f}, "
            f"and it reads {apart[k25]:+.1f} dB against HPreHpSos at 25 Hz, within 0.01 dB only above "
            f"{above:.0f} Hz. The plugin runs HPreHpSos.")
    return design


PLUGINS = {
    "firConv": {
        # A fixed gain rather than the default: firConv declares its gain's default as 0.5 dB,
        # outside its -60..0 dB range (its state starts at 0 dB, the clamped value); -12 dB is the
        # gain of the measurement firConv/README.md states P1's tolerance from, and leaves
        # headroom for the chain's +17.5 dB at 28 Hz. "freq", unused, keeps its default.
        "params": {"gain": -12.0},
        "outputs": [0],                     # its one output to the woofer, the tweeter muted
        "output_names": ["out"],
        # Peak -9 dBFS: the output peaks at -3.3 dBFS. (-6 dBFS, the documented measurement's
        # level, peaks at -0.29 dBFS.)
        "excitation": {"peak_dbfs": -9.0},
        "design": firconv_design,
        "design_label": "design: scipy, float64, from filters.hpp",
        "corner": {"what": "the high-pass (HPreHpSos)", "level_db": -10.0 * np.log10(2.0), "range_hz": (12.0, 60.0)},
        # The base versions against which the current version's x86_64 output is meant to be the
        # same bit for bit (P1, 0.0.2, against 0.0.1: firConv/README.md). Against another base an
        # x86_64 difference is reported, with its numbers, as a skipped test: it passes neither
        # silently nor as identical.
        "x86_64_bit_identical_to": ("0.0.1",),
        "limits": {
            # Time-domain peak of the difference, dB re the base output's peak, aarch64 current
            # against base. Measured -55.03 dB on this input (0.0.2 against 0.0.1); -55.0 to
            # -55.7 dB at -9, -12 and -20 dBFS peak and three noise seeds, and 0.0.1's own
            # aarch64 build differs from its x86_64 build by -54.9 dB here. Margin 2 dB. Not the
            # -59 dB firConv/README.md states: that was measured at -6 dBFS peak, where these
            # builds differ by -58.8 to -59.0 dB; at the other levels the float32 biquads'
            # noise (README, "Downstream" in fir-filter-rounding.md) moves the peak 4 dB higher.
            "peak_db": -53.0,
            "peak_note": "firConv/README.md states -59 dB, measured at -6 dBFS peak",
            # The difference below the output in every 1/6-octave band, aarch64 current against
            # base, as (from Hz, to Hz, at most dB re the output). Measured on this input: worst
            # -76.85 dB (the band at 101 Hz) from 100 Hz to 20 kHz and -89.75 dB (the band at
            # 19.9 kHz) from 1 to 20 kHz; over 13 inputs (levels -6 to -21 dBFS, three seeds) -75.9 to
            # -77.9 and -89.7 to -90.0 dB. Margins 3.85 and 3.75 dB from this input's numbers.
            "bands": [(100.0, 20000.0, -73.0), (1000.0, 20000.0, -86.0)],
            # The two builds' magnitude responses, aarch64 current against base, 1/6-octave
            # bands, from 30 Hz (above the high-pass corner) to 17 kHz (below the pre-LP's):
            # measured at most 0.0011 dB (near 30 Hz; 0.00012 dB above 100 Hz). Limit 0.01 dB.
            "magnitude_passband_hz": (30.0, 17000.0),
            "magnitude_db": 0.01,
            # Each build against the design, 1/6-octave bands, 20 Hz to 20 kHz: measured at most
            # 0.0152 dB (aarch64 0.0.2 at 20 Hz; x86_64 0.0128 dB), most of it the rounding of
            # the coefficients to float32 (the float32 design is within 0.004 dB there). Limit
            # 0.05 dB. Below 20 Hz the biquads' noise adds up to 0.06 dB at 10 Hz; above 22.5 kHz
            # the output's floor (-80 dB re the input, the design is at -89) departs by 9 dB.
            "design_passband_hz": (20.0, 20000.0),
            "design_db": 0.05,
            # The measured high-pass's -3.01 dB frequency against the design's, both smoothed
            # over 1/24 octave alike: measured 25.026 to 25.038 Hz against the design's 25.030 Hz,
            # within 0.034 % (the x86_64 builds; the unsmoothed design is at 25.00 Hz). Limit 0.5 %.
            "corner_relative": 0.005,
            # Every build's output peak, dBFS: measured -3.28 dBFS. Limit -1 dBFS: no sample is
            # near the render's clamp at full scale.
            "peak_dbfs": -1.0,
        },
    },
}

PAIRS = [
    # (file name, A, B, what): d = A - B, relative to B's output
    ("aarch64-current-vs-base", ("current", "aarch64"), ("base", "aarch64"), "the change the board will hear"),
    ("x86_64-current-vs-base", ("current", "x86_64"), ("base", "x86_64"), "expected bit-identical"),
    ("current-aarch64-vs-x86_64", ("current", "aarch64"), ("current", "x86_64"), "the architecture's difference, for context"),
]
BUILDS = [("current", "x86_64"), ("current", "aarch64"), ("base", "x86_64"), ("base", "aarch64")]


def band_edges(fc, width=r.SMOOTHING_OCTAVES):
    return fc * 2.0 ** (-width / 2), fc * 2.0 ** (width / 2)


def hz(f):
    return f"{f:.1f} Hz" if f < 1000.0 else f"{f / 1000.0:.2f} kHz"


def band_text(fc, width=r.SMOOTHING_OCTAVES):
    lo, hi = band_edges(fc, width)
    return f"the band at {hz(fc)} ({hz(lo)} to {hz(hi)})"


class Measurement:
    """Everything one plugin's test asserts and plots, computed once."""

    def __init__(self, name, spec, manifest):
        self.name, self.spec = name, spec
        self.entry = manifest["plugins"][name]
        self.speakerd = manifest["speakerd"]
        self.dir = OUT / name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.excitation = r.Excitation(**spec["excitation"])
        wav = self.dir / "input.wav"
        r.write_wav(wav, self.excitation.x)
        self.x = r.read_wav_int32(wav)[:, 0] / r.FULL_SCALE      # what speakerd reads

        document = r.graph_document(self.entry["current"]["uri"], spec["params"], spec["outputs"])
        started = {build: r.start_render(self.speakerd[build[1]], build[1], self.entry[build[0]]["bundles"][build[1]],
                                         document, wav, self.dir / "renders" / f"{build[0]}-{build[1]}")
                   for build in BUILDS}
        self.raw = {build: r.finish_render(s) for build, s in started.items()}

        self.freqs = r.bin_frequencies()
        X = r.spectra(self.x, self.excitation.segments)
        self.Sxx = r.power(X)
        self.bands = r.Bands(r.grid())
        self.centres = self.bands.centres
        self.Sxx_b = self.bands.sum(self.Sxx)
        self.y, self.Y, self.Syy_b, self.H = {}, {}, {}, {}
        for build, raw in self.raw.items():
            for ch, out in enumerate(spec["outputs"]):
                y = raw[:, out] / r.FULL_SCALE
                Y = r.spectra(y, self.excitation.segments)
                self.y[build, ch], self.Y[build, ch] = y, Y
                self.Syy_b[build, ch] = self.bands.sum(r.power(Y))
                self.H[build, ch] = sum(np.conj(a) * b for a, b in zip(X, Y)) / self.Sxx

        self.design_error = None
        try:
            self.design = spec["design"](self.entry["current"]["src"], self.freqs, spec["params"])
            self.design_base = spec["design"](self.entry["base"]["src"], self.freqs, spec["params"])
            self.landmark_design = spec["design"](self.entry["current"]["src"], np.array(LANDMARKS_HZ), spec["params"])
        except AssertionError as error:      # scipy is missing: the design tests fail with this
            self.design = self.design_base = self.landmark_design = None
            self.design_error = str(error)

        self.corners = self.find_corners() if self.design is not None else {}
        self.pairs = {}
        for file_name, a, b, what in PAIRS:
            for ch in range(len(spec["outputs"])):
                self.pairs[file_name, ch] = self.compare(a, b, ch)
        self.figures = []
        for file_name, a, b, what in PAIRS:
            for ch in range(len(spec["outputs"])):
                self.figures += self.write_figure(file_name, a, b, what, ch)
        self.write_landmarks()

    # -- the builds ------------------------------------------------------------------------

    def describe(self, build):
        side, arch = build
        info = self.entry[side]
        return f"{self.name} {info['version']} (source {info['source']}, {arch})"

    def short(self, build):
        return f"{self.entry[build[0]]['version']} {build[1]}"

    def design_for(self, build):
        return self.design if build[0] == "current" else self.design_base

    def gain_db(self, build, ch):
        return r.db_power(self.Syy_b[build, ch] / self.Sxx_b)

    def design_db(self, build, ch, key="outputs"):
        d = self.design_for(build)
        return r.db_power(self.bands.sum(np.abs(d[key][ch]) ** 2 * self.Sxx) / self.Sxx_b)

    def deviation_db(self, build, ch):
        return self.gain_db(build, ch) - self.design_db(build, ch)

    def find_corners(self):
        """{(build, ch): (measured, design)}: the frequency where the part of the design named by
        the table's "corner" (firConv: its high-pass), measured as the build's response over the
        design's other parts, crosses the corner's level, and where the design's part does; both
        smoothed over 1/24 octave alike, weighted by the input through the other parts."""
        corner = self.spec["corner"]
        fine = r.Bands(r.grid(*corner["range_hz"], 192), width=1.0 / 24.0)
        corners = {}
        for build in BUILDS:
            d = self.design_for(build)
            weight = np.abs(d["corner_rest"]) ** 2 * self.Sxx
            reference = fine.sum(weight)
            want = r.crossing(fine.centres, r.db_power(fine.sum(np.abs(d["corner_part"]) ** 2 * weight) / reference),
                              corner["level_db"])
            for ch in range(len(self.spec["outputs"])):
                got = r.crossing(fine.centres, r.db_power(fine.sum(r.power(self.Y[build, ch])) / reference),
                                 corner["level_db"])
                corners[build, ch] = (got, want)
        return corners

    # -- a pair ----------------------------------------------------------------------------

    def compare(self, a, b, ch):
        out = self.spec["outputs"][ch]
        raw_a, raw_b = self.raw[a][:, out], self.raw[b][:, out]
        identical = np.array_equal(raw_a, raw_b)
        d = self.y[a, ch] - self.y[b, ch]
        D = [p - q for p, q in zip(self.Y[a, ch], self.Y[b, ch])]
        Sdd = r.power(D)
        total = Sdd.sum()
        result = {
            "identical": identical,
            "differing": int(np.count_nonzero(raw_a != raw_b)),
            "first_difference_s": (int(np.argmax(raw_a != raw_b)) / r.RATE) if not identical else None,
            "peak_db": r.peak_db(d) - r.peak_db(self.y[b, ch]) if not identical else -np.inf,
            "peak_at_s": int(np.argmax(np.abs(d))) / r.RATE,
            "rms_dbfs": r.db_power(np.mean(d ** 2)),
            "Sdd_b": self.bands.sum(Sdd),
            "below_50": float(Sdd[self.freqs < 50.0].sum() / total) if total > 0 else None,
            "below_100": float(Sdd[self.freqs < 100.0].sum() / total) if total > 0 else None,
        }
        result["esr_db"] = r.db_power(result["Sdd_b"] / self.Syy_b[b, ch])
        result["magnitude_db"] = self.gain_db(a, ch) - self.gain_db(b, ch)
        return result

    def worst(self, values, f_lo, f_hi, absolute=False):
        """(value, centre) of the largest value (or magnitude) over the bands from f_lo to f_hi."""
        sel = (self.centres >= f_lo) & (self.centres <= f_hi)
        v = np.abs(values) if absolute else values
        i = np.flatnonzero(sel)[np.argmax(v[sel])]
        return float(values[i]), float(self.centres[i])

    # -- the figure and the CSV --------------------------------------------------------------

    def write_figure(self, file_name, a, b, what, ch):
        spec, limits, pair = self.spec, self.spec["limits"], self.pairs[file_name, ch]
        suffix = "" if len(spec["outputs"]) == 1 else f"-{spec['output_names'][ch]}"
        svg, csv_path = self.dir / f"{file_name}{suffix}.svg", self.dir / f"{file_name}{suffix}.csv"
        main = file_name == PAIRS[0][0]
        base = self.entry["base"]
        if pair["identical"]:
            peak = "the outputs are identical bit for bit (32-bit integer render)"
        else:
            peak = (f"time-domain peak difference {pair['peak_db']:.1f} dB re peak (at {pair['peak_at_s']:.2f} s"
                    + (f"; limit {limits['peak_db']:g} dB" if main else "")
                    + (f"; {limits['peak_note']}" if main and limits.get("peak_note") else "")
                    + f"), RMS {pair['rms_dbfs']:.1f} dBFS, {100 * pair['below_50']:.2f} % of its energy below 50 Hz")
        figure = svgplot.Figure(
            f"{self.describe(a)}  against  {self.describe(b)}",
            [f"{what[0].upper() + what[1:]}: {peak}.",
             f"Input: {self.excitation.describe()}; gain {spec['params']['gain']:g} dB. Rendered by "
             f"{self.speakerd['version']} --render, 48 kHz, block {r.BLOCK}"
             + (", aarch64 under qemu-aarch64-static" if "aarch64" in (a[1], b[1]) else "")
             + ". Curves: 1/6-octave power sums (response.py)."]
            + ([f"Base: {base['commit'][:12]}, {self.name} {base['version']}, source {base['source']}"
                + (" (from git; a build from the export carries none)" if base["source_from"] == "git" else "") + "."]
               if "base" in (a[0], b[0]) else []),
            r.F_MIN, r.F_MAX)
        c = self.centres
        colors = {b: "#1f77b4", a: "#d62728"}
        gains = {build: self.gain_db(build, ch) for build in (a, b)}
        design = None if self.design is None else self.design_db(a, ch)

        p = figure.panel("Magnitude response", "dB", -90, 10, height=250)
        p.curve(c, gains[b], self.describe(b), colors[b], width=2.6)
        p.curve(c, gains[a], self.describe(a), colors[a], width=1.3)
        if design is not None:
            p.curve(c, design, spec["design_label"], "#000000", width=1.4, dash="6,4")
            other = self.design_db(b, ch)
            if np.max(np.abs(other - design)) > 1e-6:      # the base has other coefficients
                p.curve(c, other, f"the design of {self.short(b)}", "#7f7f7f", width=1.4, dash="6,4")
            for note in self.design["notes"]:
                p.note(note)
        else:
            p.note(f"No design curve: {self.design_error}")

        out_dbfs = r.band_power_dbfs(self.Syy_b[b, ch], self.excitation.excitation_samples)
        diff_dbfs = r.band_power_dbfs(pair["Sdd_b"], self.excitation.excitation_samples)
        finite = diff_dbfs[np.isfinite(diff_dbfs)]
        lo = min(-140.0, 20.0 * np.floor((finite.min() if len(finite) else -140.0) / 20.0))
        p = figure.panel("Output and difference, band power (the gap is the error-to-signal ratio)", "dBFS",
                         max(lo, -200.0), 0.0, height=230)
        p.curve(c, out_dbfs, f"output of {self.describe(b)}", colors[b], width=1.8)
        if pair["identical"]:
            p.note("No difference: the outputs are identical bit for bit.")
        else:
            p.curve(c, diff_dbfs, f"difference, {self.short(a)} - {self.short(b)}", "#9467bd", width=1.8)

        p = figure.panel("Difference relative to the output, per band", "dB re output", -120.0, 10.0, height=220)
        if pair["identical"]:
            p.center("the outputs are identical bit for bit: no difference to show")
        else:
            p.curve(c, pair["esr_db"], "difference re output", "#9467bd", width=1.8)
        if main:
            for f_lo, f_hi, limit in limits["bands"]:
                p.hline(limit, f"limit {limit:g} dB, {f_lo:g} Hz-{f_hi / 1000:g} kHz", f_lo=f_lo, f_hi=f_hi)
        p.vline(50.0, "50 Hz")

        lim = limits["design_db"]
        span = 4.0 * lim
        p = figure.panel("Deviation from the design", "dB", -span, span, height=220)
        if self.design is None:
            p.center("no design curve")
            p.note(self.design_error)
        else:
            for build in (b, a):
                p.curve(c, self.deviation_db(build, ch), self.describe(build), colors[build],
                        width=2.6 if build == b else 1.3)
            p.curve(c, self.design_db(a, ch, "outputs_f32") - design, "the design with its coefficients in float32, "
                    "as the plugin stores them", "#000000", width=1.4, dash="2,3")
            f_lo, f_hi = limits["design_passband_hz"]
            p.hline(lim, f"limit +/-{lim:g} dB, {f_lo:g} Hz-{f_hi / 1000:g} kHz", f_lo=f_lo, f_hi=f_hi)
            p.hline(-lim, None, f_lo=f_lo, f_hi=f_hi)
            p.vline(50.0, "50 Hz")
            above = np.flatnonzero(c > f_hi)
            deviations = {build: np.abs(self.deviation_db(build, ch)) for build in (a, b)}
            far, at, build = max((float(deviations[bd][k]), int(k), bd) for bd in (a, b) for k in above)
            if far > span:
                p.note(f"Off scale above {f_hi / 1000:g} kHz: up to {far:.1f} dB, at {c[at] / 1000:.1f} kHz, where "
                       f"{self.short(build)} is at {self.gain_db(build, ch)[at]:.1f} dB and the design at "
                       f"{design[at]:.1f} dB.")
        figure.write(svg)

        with open(csv_path, "w", newline="") as f:
            f.write(f"# {self.describe(a)} (A) against {self.describe(b)} (B): {what}\n")
            f.write(f"# input: {self.excitation.describe()}; gain {spec['params']['gain']:g} dB; "
                    f"{self.speakerd['version']} --render, 48 kHz, block {r.BLOCK}\n")
            f.write("# bands of 1/6 octave centred on frequency_hz; dB of power sums (response.py)\n")
            w = csv.writer(f)
            w.writerow(["frequency_hz", "design_db", "design_float32_db", "a_db", "b_db", "a_minus_design_db",
                        "b_minus_design_db", "a_minus_b_db", "output_b_dbfs", "difference_dbfs", "difference_re_output_db"])
            nan = np.full(len(c), np.nan)
            columns = [c, design if design is not None else nan,
                       self.design_db(a, ch, "outputs_f32") if design is not None else nan,
                       gains[a], gains[b],
                       self.deviation_db(a, ch) if design is not None else nan,
                       self.deviation_db(b, ch) if design is not None else nan,
                       pair["magnitude_db"], out_dbfs, diff_dbfs, pair["esr_db"]]
            for row in zip(*columns):
                w.writerow([f"{row[0]:.3f}"] + [("" if not np.isfinite(v) else f"{v:.6f}") for v in row[1:]])
        return [svg, csv_path]

    def write_landmarks(self):
        """The design and each build at LANDMARKS_HZ, unsmoothed: the design exactly, a build at
        its nearest FFT bin (0.09 Hz apart)."""
        rows = []
        where = [f / (r.RATE / r.N_FFT) for f in LANDMARKS_HZ]
        for ch in range(len(self.spec["outputs"])):
            if self.landmark_design is not None:
                lm = self.landmark_design
                rows.append(["design (scipy, float64)", ch] + [f"{v:.4f}" for v in 20 * np.log10(np.abs(lm["outputs"][ch]))])
                if "hp_ba" in lm:
                    rows.append(["design, high-pass as HPreHpNum/HPreHpDen", ch]
                                + [f"{v:.4f}" for v in 20 * np.log10(np.abs(lm["corner_rest"] * lm["hp_ba"]))])
            for build in BUILDS:
                db = 20 * np.log10(np.abs(self.H[build, ch]))
                # between the two bins around the frequency, linearly in dB (0.09 Hz apart)
                values = [db[int(k)] + (k - int(k)) * (db[int(k) + 1] - db[int(k)]) for k in where]
                rows.append([self.describe(build), ch] + [f"{v:.4f}" for v in values])
        self.landmarks = rows
        with open(self.dir / "landmarks.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["response", "output"] + [f"{f:g} Hz (dB)" for f in LANDMARKS_HZ])
            w.writerows(rows)

    def summary(self):
        lines = [f"{self.name}: renders, figures and curves in {self.dir}"]
        for (file_name, ch), pair in self.pairs.items():
            if pair["identical"]:
                lines.append(f"  {file_name}: identical bit for bit")
                continue
            esr, at = self.worst(np.where(np.isfinite(pair["esr_db"]), pair["esr_db"], -np.inf), 100.0, 20000.0)
            lines.append(f"  {file_name}: peak {pair['peak_db']:.2f} dB re peak at {pair['peak_at_s']:.2f} s, RMS "
                         f"{pair['rms_dbfs']:.1f} dBFS, {100 * pair['below_50']:.3f} % of the energy below 50 Hz, "
                         f"{100 * pair['below_100']:.3f} % below 100 Hz; worst band 100 Hz-20 kHz {esr:.2f} dB re output "
                         f"at {at:.4g} Hz; {pair['differing']} samples differ")
        if self.landmark_design is not None:
            lines.append("  landmarks, dB at " + ", ".join(f"{f:g}" for f in LANDMARKS_HZ) + " Hz:")
            lines += ["    " + f"{row[0]:<52s}" + " ".join(f"{v:>9s}" for v in row[2:]) for row in self.landmarks]
            lines += ["  " + note for note in self.design["notes"]]
        corner = self.spec["corner"]
        for (build, ch), (got, want) in self.corners.items():
            lines.append(f"  {self.describe(build)}: {corner['what']} at {corner['level_db']:.2f} dB at "
                         + (f"{got:.3f} Hz" if got else "no frequency") + ", the design at "
                         + (f"{want:.3f} Hz" if want else "no frequency"))
        return "\n".join(lines)


_measured = {}


def measured(name):
    if name not in _measured:
        manifest_path = OUT / "builds.json"
        if not manifest_path.exists():
            raise unittest.SkipTest(f"no {manifest_path}: run run.sh, which builds first")
        _measured[name] = Measurement(name, PLUGINS[name], json.loads(manifest_path.read_text()))
        print("\n" + _measured[name].summary(), flush=True)
    return _measured[name]


class ResponseTest:
    """The assertions, for the plugin `NAME` of a subclass."""

    NAME = None

    @classmethod
    def setUpClass(cls):
        cls.m = measured(cls.NAME)
        cls.spec, cls.limits = PLUGINS[cls.NAME], PLUGINS[cls.NAME]["limits"]

    def channels(self):
        return range(len(self.spec["outputs"]))

    def name_of(self, ch):
        return "" if len(self.spec["outputs"]) == 1 else f" ({self.spec['output_names'][ch]})"

    def test_x86_64_current_vs_base_bit_identical(self):
        """x86_64: bit for bit against the bases x86_64_bit_identical_to names."""
        # Against another base a difference is not judged: the test is skipped, with its size.
        m, base = self.m, self.m.entry["base"]["version"]
        stated = base in self.spec["x86_64_bit_identical_to"]
        for ch in self.channels():
            pair = m.pairs["x86_64-current-vs-base", ch]
            if pair["identical"]:
                continue
            numbers = (f"{m.describe(('current', 'x86_64'))} and {m.describe(('base', 'x86_64'))}{self.name_of(ch)} "
                       f"differ in {pair['differing']} samples, from {pair['first_difference_s']:.4f} s, by up to "
                       f"{pair['peak_db']:.1f} dB re peak at {pair['peak_at_s']:.2f} s")
            if stated:
                self.fail(f"{numbers}: they are meant to be the same bit for bit (x86_64_bit_identical_to)")
            stated_for = ", ".join(self.spec["x86_64_bit_identical_to"]) or "no version"
            self.skipTest(f"{numbers}. Bit identity on x86_64 is stated against {stated_for} "
                          f"(x86_64_bit_identical_to in {Path(__file__).name}), not against {base}, so this "
                          f"difference is not judged here: see x86_64-current-vs-base.svg")

    def test_aarch64_current_vs_base_peak_difference(self):
        limit = self.limits["peak_db"]
        for ch in self.channels():
            pair = self.m.pairs["aarch64-current-vs-base", ch]
            self.assertLessEqual(pair["peak_db"], limit,
                                 f"aarch64{self.name_of(ch)}: the outputs differ by up to {pair['peak_db']:.2f} dB re "
                                 f"peak, at {pair['peak_at_s']:.3f} s; the limit is {limit:g} dB")

    def test_aarch64_current_vs_base_bands(self):
        for ch in self.channels():
            pair = self.m.pairs["aarch64-current-vs-base", ch]
            esr = np.where(np.isfinite(pair["esr_db"]), pair["esr_db"], -np.inf)
            for f_lo, f_hi, limit in self.limits["bands"]:
                with self.subTest(band=f"{f_lo:g}-{f_hi:g} Hz"):
                    worst, at = self.m.worst(esr, f_lo, f_hi)
                    self.assertLessEqual(worst, limit,
                                         f"aarch64{self.name_of(ch)}: the difference is only {-worst:.2f} dB below the output "
                                         f"in {band_text(at)}; the limit is {-limit:g} dB below from {hz(f_lo)} to {hz(f_hi)}")

    def test_aarch64_current_vs_base_magnitude(self):
        f_lo, f_hi = self.limits["magnitude_passband_hz"]
        limit = self.limits["magnitude_db"]
        for ch in self.channels():
            pair = self.m.pairs["aarch64-current-vs-base", ch]
            worst, at = self.m.worst(pair["magnitude_db"], f_lo, f_hi, absolute=True)
            self.assertLessEqual(abs(worst), limit,
                                 f"aarch64{self.name_of(ch)}: the magnitude responses differ by {worst:+.4f} dB in "
                                 f"{band_text(at)}; the limit is {limit:g} dB from {hz(f_lo)} to {hz(f_hi)}")

    def test_builds_match_the_design(self):
        if self.m.design is None:
            self.fail(self.m.design_error)
        f_lo, f_hi = self.limits["design_passband_hz"]
        limit = self.limits["design_db"]
        for build in BUILDS:
            for ch in self.channels():
                with self.subTest(build=self.m.describe(build) + self.name_of(ch)):
                    worst, at = self.m.worst(self.m.deviation_db(build, ch), f_lo, f_hi, absolute=True)
                    self.assertLessEqual(abs(worst), limit,
                                         f"{self.m.describe(build)}{self.name_of(ch)} departs from the design by "
                                         f"{worst:+.4f} dB in {band_text(at)}; the limit is {limit:g} dB from "
                                         f"{hz(f_lo)} to {hz(f_hi)}")

    def test_corner_where_the_coefficients_put_it(self):
        """The measured high-pass's corner is where the design's is (Measurement.corners)."""
        if self.m.design is None:
            self.fail(self.m.design_error)
        corner, limit = self.spec["corner"], self.limits["corner_relative"]
        for (build, ch), (got, want) in self.m.corners.items():
            with self.subTest(build=self.m.describe(build) + self.name_of(ch)):
                f_lo, f_hi = corner["range_hz"]
                self.assertIsNotNone(want, f"the design of {self.m.describe(build)}: {corner['what']} never "
                                           f"reaches {corner['level_db']:.2f} dB between {hz(f_lo)} and {hz(f_hi)}")
                self.assertIsNotNone(got, f"{self.m.describe(build)}: {corner['what']} never reaches "
                                          f"{corner['level_db']:.2f} dB between {hz(f_lo)} and {hz(f_hi)}")
                self.assertLessEqual(abs(got / want - 1.0), limit,
                                     f"{self.m.describe(build)}{self.name_of(ch)}: {corner['what']} is at "
                                     f"{corner['level_db']:.2f} dB at {got:.3f} Hz, the design at {want:.3f} Hz "
                                     f"({100 * (got / want - 1):+.3f} %); the limit is {100 * limit:g} %")

    def test_nothing_clips(self):
        limit = self.limits["peak_dbfs"]
        for build in BUILDS:
            for ch in self.channels():
                with self.subTest(build=self.m.describe(build) + self.name_of(ch)):
                    peak = r.peak_db(self.m.y[build, ch])
                    self.assertLessEqual(peak, limit, f"{self.m.describe(build)}{self.name_of(ch)} peaks at "
                                                      f"{peak:.2f} dBFS; the limit is {limit:g} dBFS")


# One TestCase per plugin of the table.
for _name in PLUGINS:
    globals()[f"{_name}ResponseTest"] = type(f"{_name}ResponseTest", (ResponseTest, unittest.TestCase), {"NAME": _name})
del _name


if __name__ == "__main__":
    unittest.main()
