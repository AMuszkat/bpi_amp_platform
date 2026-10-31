"""firConv's magnitude response: the design, and the plugin built for x86_64 and for aarch64
(dsp/juce_plugins/README.md, "The response test"). run.sh builds, then runs this module:

    dsp/juce_plugins/tests/firconv-response/run.sh

The design is filters.hpp as the tuning GUI graphs it, with scipy in float64 (the high-pass as
the HPreHpSos cascade the plugin runs), times the render's gain. Each build renders one
exponential sweep with `speakerd --render` at 48 kHz in blocks of 1024 (aarch64 under
qemu-aarch64-static); the sweep and the silence after it are transformed whole, so the output's
spectrum over the input's is the build's exact response, with no window. Every curve is a power
average over 1/6-octave bands, the design weighted by the sweep like the builds. Writes
firConv/response.svg, response.csv and landmarks.txt in FRESP_OUT.
"""

import csv
import json
import os
import re
import shutil
import subprocess
import unittest
import wave
from pathlib import Path

import numpy as np

import svgplot

ROOT = Path(__file__).resolve().parents[4]
SRC = ROOT / "dsp/juce_plugins/firConv"
OUT = Path(os.environ.get("FRESP_OUT", ROOT / "build/tests/firconv-response")) / "firConv"
RATE, BLOCK, N_FFT = 48000, 1024, 1 << 19      # the FFT holds the sweep and its silence, 9.25 s
GAIN_DB = -12.0      # fixed, not the default (0.5 dB, outside -60..0): headroom for the chain's +17.5 dB at 28 Hz
PEAK_DBFS = -9.0     # the sweep's peak
F1, F2, SWEEP_S, TAIL_S = 5.0, 24000.0, 8.0, 1.0
OCTAVES = 1.0 / 6.0
LANDMARKS_HZ = (20.0, 25.0, 50.0, 100.0, 1000.0, 10000.0)
ARCHES = ("x86_64", "aarch64")

# Each build within DESIGN_DB of the design in every 1/6-octave band from 20 Hz to 20 kHz.
# Measured worst: x86_64 +0.0153 dB at 20 Hz, aarch64 +0.0138 dB at 20.3 Hz, most of it the
# rounding of the coefficients to float32 (against the design with float32 coefficients, 0.004
# dB). Limit 0.05 dB, 3x that. Outside it: up to 0.022 dB below 20 Hz, the float32 high-pass's
# noise, and up to 13 dB above 22.5 kHz, where the output's floor is above the design's -90 dB.
DESIGN_BAND_HZ, DESIGN_DB = (20.0, 20000.0), 0.05
# The output's peak: measured -3.32 dBFS. Limit -1 dBFS: no sample near the render's clamp.
PEAK_LIMIT_DBFS = -1.0

SCIPY_MISSING = ("scipy is missing: the design is computed with scipy.signal (freqz, sosfreqz). "
                 "Install python3-scipy (Debian bookworm's package; it is in dsp/juce_plugins/tools/Containerfile)")


def sweep():
    """0.25 s of silence, the exponential sweep from F1 to F2 at PEAK_DBFS (a cycle's fade in,
    5 ms out), TAIL_S of silence."""
    n, fade_in, fade_out = int(SWEEP_S * RATE), int(RATE / F1), int(0.005 * RATE)
    rate = SWEEP_S / np.log(F2 / F1)
    x = np.sin(2 * np.pi * F1 * rate * (np.exp(np.arange(n) / RATE / rate) - 1))
    x[:fade_in] *= 0.5 - 0.5 * np.cos(np.pi * np.arange(fade_in) / fade_in)
    x[n - fade_out:] *= 0.5 + 0.5 * np.cos(np.pi * np.arange(1, fade_out + 1) / fade_out)
    return 10 ** (PEAK_DBFS / 20) * np.concatenate([np.zeros(RATE // 4), x, np.zeros(int(TAIL_S * RATE))])


def read_wav(path):
    """The first channel of a 32-bit integer WAV (speakerd's output: the woofer), full scale 1.0."""
    with wave.open(str(path)) as w:
        if w.getsampwidth() != 4 or w.getframerate() != RATE:
            raise ValueError(f"{path}: not 32-bit at {RATE} Hz")
        return np.frombuffer(w.readframes(w.getnframes()), "<i4").reshape(-1, w.getnchannels())[:, 0] / 2.0 ** 31


def write_wav(path, x):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(4)
        w.setframerate(RATE)
        w.writeframes(np.round(x * (2 ** 31 - 1)).astype("<i4").tobytes())


def start_render(arch, wav):
    """speakerd --render of `wav` through firConv alone, to the woofer, with the build's bundle
    and an empty HOME (so that lilv loads that bundle and no other)."""
    build = ROOT / "build" / ("aarch64" if arch == "aarch64" else "")
    bundle = build / "firConv/firConv_artefacts/Release/LV2/firConv.lv2"
    work = OUT / "renders" / arch
    shutil.rmtree(work, ignore_errors=True)
    (work / "state").mkdir(parents=True)
    (work / "home").mkdir()
    uri = re.search(r"<([^>]+)>\s+a\s+lv2:Plugin", (bundle / "manifest.ttl").read_text()).group(1)
    (work / "state/device.json").write_text(json.dumps(       # no limit: the plugin's output as it is
        {"version": 1, "role": "left", "amplifier": "amplifier", "limits": {"gain_db": 0, "tweeter": "muted"}}))
    (work / "state/factory.json").write_text(json.dumps({
        "format": "speakerd-graph", "version": 1, "revision": 1,
        "nodes": [{"uid": 16, "plugin": {"format": "LV2", "identifier": uri}, "params": {"gain": GAIN_DB}}],
        "connections": [{"src": {"node": 1, "channel": 0}, "dst": {"node": 16, "channel": 0}},
                        {"src": {"node": 16, "channel": 0}, "dst": {"node": 2, "channel": 0}}]}))
    command = [str(build / "speakerd/speakerd_artefacts/Release/speakerd"), "--state-dir", str(work / "state"),
               "--render", str(wav), str(work / "out.wav"), "--block", str(BLOCK), "--lv2-bundle", str(bundle)]
    command = (["qemu-aarch64-static"] if arch == "aarch64" else []) + command
    with open(work / "speakerd.log", "w") as log:
        process = subprocess.Popen(command, env=dict(os.environ, HOME=str(work / "home")),
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    return process, work, bundle


def finish_render(process, work, bundle):
    """The render's output, once speakerd exited 0 and logged that it ran `bundle`."""
    status, log = process.wait(timeout=600), (work / "speakerd.log").read_text(errors="replace")
    ran = re.findall(r"plugin node 16: .*, bundle (.*)$", log, re.MULTILINE)
    if status != 0 or [os.path.realpath(p) for p in ran] != [os.path.realpath(bundle)]:
        raise AssertionError(f"the render of {bundle} failed (status {status}); log {work / 'speakerd.log'}:\n{log}")
    return read_wav(work / "out.wav")


def design(freqs):
    """filters.hpp's chain at `freqs` in float64, as dsp/python_dsp_tools/tuning/dsp_gui.py
    graphs it, the high-pass as the plugin runs it (sosfreqz of HPreHpSos), times the gain."""
    try:
        from scipy import signal
    except ImportError:
        raise AssertionError(SCIPY_MISSING) from None
    text = (SRC / "filters.hpp").read_text()
    c = {name: np.array([float(v.strip().rstrip("f")) for v in body.split(",") if v.strip()])
         for name, body in re.findall(r"float\s+(\w+)\s*\[\d+\]\s*=\s*\{([^}]*)\}", text)}
    on_bins = len(freqs) == N_FFT // 2 + 1       # the FFT's bins: freqz's FFT path, at the same frequencies
    fir = {"worN": len(freqs), "include_nyquist": True} if on_bins else {"worN": freqs}
    h = signal.freqz(c["InvFilter"], 1, fs=RATE, **fir)[1] * signal.freqz(c["HPreLpTaps"], 1, fs=RATE, **fir)[1]
    return 10 ** (GAIN_DB / 20) * h * signal.sosfreqz(c["HPreHpSos"].reshape(-1, 6), worN=freqs, fs=RATE)[1]


def build_identity():
    info = (ROOT / "build/firConv/firConv_artefacts/Release/LV2/firConv.lv2/buildinfo.ttl").read_text()
    return re.search(r'bpi:version "([^"]*)"', info).group(1), re.search(r'bpi:sourceId "([^"]*)"', info).group(1)


def db(power):
    with np.errstate(divide="ignore"):
        return 10 * np.log10(power)


class FirConvResponse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        design(np.array([1000.0]))       # without scipy, fail at once, naming the package
        OUT.mkdir(parents=True, exist_ok=True)
        x = sweep()
        write_wav(OUT / "input.wav", x)
        started = [start_render(arch, OUT / "input.wav") for arch in ARCHES]   # both at once
        cls.y = {arch: finish_render(*s) for arch, s in zip(ARCHES, started)}
        x = read_wav(OUT / "input.wav")                                        # as speakerd read it

        freqs = np.arange(N_FFT // 2 + 1) * RATE / N_FFT
        X = np.fft.rfft(x, N_FFT)
        H = {arch: np.fft.rfft(y, N_FFT) / X for arch, y in cls.y.items()}
        H["design"] = design(freqs)
        # 1/6-octave bands, 48 per octave, from 10 Hz to 24 kHz: sum |H|^2 |X|^2 / sum |X|^2
        cls.centres = 10.0 * 2 ** (np.arange(int(48 * np.log2(2400)) + 1) / 48)
        edges = np.column_stack([np.ceil(cls.centres * 2 ** (s * OCTAVES / 2) / (RATE / N_FFT)) for s in (-1, 1)])
        edges = np.minimum(edges.astype(int), len(freqs)).ravel()
        weight = np.abs(X) ** 2
        band = lambda v: np.add.reduceat(np.append(v, 0.0), edges)[::2]          # noqa: E731
        cls.db = {key: db(band(np.abs(h) ** 2 * weight) / band(weight)) for key, h in H.items()}

        version, source = build_identity()
        cls.label = {"design": "design: scipy, float64, from filters.hpp",
                     "x86_64": "plugin, x86_64 (speakerd render)", "aarch64": "plugin, aarch64 (speakerd render, qemu)"}
        figure = svgplot.Figure(
            f"firConv {version} (source {source}): magnitude response, designed and rendered",
            [f"Design: scipy, float64, from filters.hpp (the high-pass as HPreHpSos), times the gain, {GAIN_DB:g} dB. "
             f"Plugin: speakerd --render of an exponential sweep {F1:g} Hz-{F2 / 1000:g} kHz at {PEAK_DBFS:g} dBFS peak, "
             f"48 kHz, block {BLOCK}. Curves: 1/6-octave power averages."], 10.0, 24000.0)
        panel = figure.panel("Magnitude response", "dB", -90, 10, height=330)
        colors = {"x86_64": ("#1f77b4", 2.6, None), "aarch64": ("#d62728", 1.3, None), "design": ("#000000", 1.4, "6,4")}
        for key in ("x86_64", "aarch64", "design"):
            panel.curve(cls.centres, cls.db[key], cls.label[key], *colors[key])
        panel = figure.panel("Deviation from the design", "dB", -4 * DESIGN_DB, 4 * DESIGN_DB, height=200)
        for arch in ARCHES:
            panel.curve(cls.centres, cls.db[arch] - cls.db["design"], f"{arch} - design", *colors[arch])
        panel.hline(DESIGN_DB, f"limit +/-{DESIGN_DB:g} dB, 20 Hz-20 kHz", *DESIGN_BAND_HZ)
        panel.hline(-DESIGN_DB, None, *DESIGN_BAND_HZ)
        figure.write(OUT / "response.svg")

        with open(OUT / "response.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["frequency_hz", "design_db", "x86_64_db", "aarch64_db"])
            w.writerows([f"{v:.3f}", *(f"{cls.db[k][i]:.4f}" for k in ("design", "x86_64", "aarch64"))]
                        for i, v in enumerate(cls.centres))

        # The landmarks unsmoothed: the design exactly, a build between its two nearest bins.
        k = np.array(LANDMARKS_HZ) / (RATE / N_FFT)
        at = {arch: np.interp(k, np.arange(len(h)), 20 * np.log10(np.abs(h))) for arch, h in H.items() if arch != "design"}
        at["design"] = 20 * np.log10(np.abs(design(np.array(LANDMARKS_HZ))))
        lines = [f"firConv {version} (source {source}), gain {GAIN_DB:g} dB: magnitude response, dB",
                 f"{'Hz':>8} {'design':>10} {'x86_64':>10} {'aarch64':>10}"]
        lines += [f"{f:>8g} " + " ".join(f"{at[key][i]:>10.4f}" for key in ("design", "x86_64", "aarch64"))
                  for i, f in enumerate(LANDMARKS_HZ)]
        for arch in ARCHES:
            worst, fc = cls.worst(arch)
            lines.append(f"{arch}: at most {worst:+.4f} dB from the design from 20 Hz to 20 kHz, at {fc:.4g} Hz "
                         f"(1/6 octave; limit {DESIGN_DB:g} dB)")
        (OUT / "landmarks.txt").write_text("\n".join(lines) + "\n")

    @classmethod
    def worst(cls, arch):
        """(deviation, band centre) of the band from 20 Hz to 20 kHz where `arch` departs most."""
        inside = np.flatnonzero((cls.centres >= DESIGN_BAND_HZ[0]) & (cls.centres <= DESIGN_BAND_HZ[1]))
        deviation = cls.db[arch] - cls.db["design"]
        i = inside[np.argmax(np.abs(deviation[inside]))]
        return deviation[i], cls.centres[i]

    def test_builds_match_the_design(self):
        for arch in ARCHES:
            with self.subTest(arch=arch):
                worst, fc = self.worst(arch)
                self.assertLessEqual(abs(worst), DESIGN_DB,
                                     f"{arch}: {self.db[arch][self.centres == fc][0]:.4f} dB against the design's "
                                     f"{self.db['design'][self.centres == fc][0]:.4f} dB in the 1/6-octave band at "
                                     f"{fc:.4g} Hz ({worst:+.4f} dB); the limit is {DESIGN_DB:g} dB from 20 Hz to 20 kHz")

    def test_nothing_clips(self):
        for arch in ARCHES:
            with self.subTest(arch=arch):
                peak = 20 * np.log10(np.max(np.abs(self.y[arch])))
                self.assertLessEqual(peak, PEAK_LIMIT_DBFS, f"{arch}: the output peaks at {peak:.2f} dBFS, "
                                                            f"at {np.argmax(np.abs(self.y[arch])) / RATE:.3f} s; "
                                                            f"the limit is {PEAK_LIMIT_DBFS:g} dBFS")


if __name__ == "__main__":
    unittest.main()
