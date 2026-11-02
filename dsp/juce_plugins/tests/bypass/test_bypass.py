"""A bypassed firConv or crossover keeps its gain stage (dsp/juce_plugins/README.md, "The bypass
test"). run.sh builds, then runs this module:

    dsp/juce_plugins/tests/bypass/run.sh

Every render is `speakerd --render` of a document with the plugin's complete build as node 16,
its gain in `params` and, for the bypassed renders, `"bypassed": true`: the path a bypass takes
on a speaker (GraphBuilder's setBypassed, the TimedNode, JUCE's LV2 hosting setting its bypass
parameter, which is the plugin's lv2:enabled port, JUCE's LV2 wrapper calling the plugin's
processBlockBypassed). 48 kHz, blocks of 1024, after speakerd's second of silent settling.

- firConv bypassed outputs its input times the gain, delayed by its two linear-phase FIRs'
  delay, (61 - 1) / 2 + (2001 - 1) / 2 = 1030 samples: at -12 dB and at 0 dB, within
  EXACT_DB of the input's peak, sample for sample.
- crossover bypassed outputs its input times the gain on its low band (output 1), delayed by its
  FIR's (259 - 1) / 2 = 129 samples, and silence on its high band (output 2, the tweeter's).
- Processed (not bypassed), each plugin's output is the one of its version before the bypass
  (firConv 0.0.3, crossover 0.0.2, built by run.sh from commit PREVIOUS into BYPASS_PREVIOUS),
  rendered the same way, bit for bit on every output, at both gains: at a constant gain the
  bypass's dry path and fade leave the processed path as it was.
- Neither plugin declares a parameter of its own for the bypass (its bundle's dsp.ttl names only
  gain and freq), so no saved state of the plugin can carry one: the graph document's `bypassed`
  is the one truth. The bypass a host sees is the lv2:enabled port JUCE's LV2 wrapper adds.
- The level of each processed and bypassed render of pink noise and of a 1 kHz sine is written to
  levels.txt in BYPASS_OUT, the table the README gives.
"""

import json
import os
import re
import shutil
import subprocess
import unittest
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[4]
PLUGINS = ROOT / "dsp/juce_plugins"
OUT = Path(os.environ.get("BYPASS_OUT", ROOT / "build/tests/bypass"))
PREVIOUS = Path(os.environ["BYPASS_PREVIOUS"]) if "BYPASS_PREVIOUS" in os.environ else None
RATE, BLOCK = 48000, 1024
GAINS_DB = (-12.0, 0.0)
# The bypassed output against the input times the gain, delayed: the worst sample at most this far
# below the input's peak. What is left is the float32 multiply by the gain and the 32-bit WAVs'
# rounding: speakerd reads the input into float32. Measured (firConv 0.0.4, crossover 0.0.3):
# -147 dB at 0 dB, -154 dB at -12 dB, the same for both plugins.
EXACT_DB = -120.0
# The level window: from 1 s (the high-pass's settling, under 0.5 s; the FIRs' 21 ms) to 0.25 s
# before the end.
WINDOW_S = (1.0, 0.25)

SIGNALS = {
    "pink": "pink noise, -30 dBFS RMS, 8 s",
    "sine": "1 kHz sine, -20 dBFS peak, 4 s",
}


def taps(header, name):
    """The number of taps of float array `name` in `header`."""
    text = (PLUGINS / header).read_text()
    match = re.search(r"float\s+" + name + r"\s*\[(\d+)\]", text)
    if match is None:
        raise AssertionError(f"{header}: no float {name}[N]")
    return int(match.group(1))


# The delay of each plugin's processed path: its FIRs are linear phase (symmetric taps).
FIRCONV_DELAY = (taps("firConv/filters.hpp", "HPreLpTaps") - 1) // 2 + (taps("firConv/filters.hpp", "InvFilter") - 1) // 2
CROSSOVER_DELAY = (taps("crossover/crossover_coefs.hpp", "lp") - 1) // 2


def signal(kind):
    rng = np.random.default_rng(20261101)
    if kind == "pink":
        n = 8 * RATE
        spectrum = np.fft.rfft(rng.standard_normal(n))
        f = np.arange(len(spectrum)) * RATE / n
        spectrum[1:] /= np.sqrt(f[1:])
        spectrum[0] = 0.0
        x = np.fft.irfft(spectrum, n)
        return x * 10 ** (-30 / 20) / np.sqrt(np.mean(x ** 2))
    t = np.arange(4 * RATE) / RATE
    return 10 ** (-20 / 20) * np.sin(2 * np.pi * 1000.0 * t)


def write_wav(path, x):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(4)
        w.setframerate(RATE)
        w.writeframes(np.round(x * (2 ** 31 - 1)).astype("<i4").tobytes())


def read_wav(path):
    """Every channel of a 32-bit integer WAV, full scale 1.0, as rows: (channels, samples)."""
    with wave.open(str(path)) as w:
        if w.getsampwidth() != 4 or w.getframerate() != RATE:
            raise ValueError(f"{path}: not 32-bit at {RATE} Hz")
        return (np.frombuffer(w.readframes(w.getnframes()), "<i4").reshape(-1, w.getnchannels()) / 2.0 ** 31).T


def bundle(plugin, previous=False):
    """The complete build's bundle: the current one, or the previous version's (run.sh)."""
    base = PREVIOUS if previous else ROOT / "build"
    return base / f"{plugin}/{plugin}_artefacts/Release/LV2/{plugin}.lv2"


def render(plugin, wav, gain_db, bypassed, name, previous=False):
    """speakerd --render of `wav` through `plugin` as node 16, its gain at `gain_db`: firConv to the
    woofer; crossover's low band to the woofer and its high band to the tweeter (the tweeter on).
    `previous`: the version before the bypass. Returns (woofer, tweeter)."""
    b = bundle(plugin, previous)
    work = OUT / "renders" / name
    shutil.rmtree(work, ignore_errors=True)
    (work / "state").mkdir(parents=True)
    (work / "home").mkdir()
    uri = re.search(r"<([^>]+)>\s+a\s+lv2:Plugin", (b / "manifest.ttl").read_text()).group(1)
    (work / "state/device.json").write_text(json.dumps(       # no limit: the plugin's output as it is
        {"version": 1, "role": "left", "amplifier": "amplifier", "limits": {"gain_db": 0, "tweeter": "on"}}))
    node = {"uid": 16, "plugin": {"format": "LV2", "identifier": uri}, "params": {"gain": gain_db}}
    if bypassed:
        node["bypassed"] = True
    connections = [{"src": {"node": 1, "channel": 0}, "dst": {"node": 16, "channel": 0}},
                   {"src": {"node": 16, "channel": 0}, "dst": {"node": 2, "channel": 0}}]
    if plugin == "crossover":
        connections.append({"src": {"node": 16, "channel": 1}, "dst": {"node": 2, "channel": 1}})
    (work / "state/factory.json").write_text(json.dumps({
        "format": "speakerd-graph", "version": 1, "revision": 1, "nodes": [node], "connections": connections}))
    command = [str(ROOT / "build/speakerd/speakerd_artefacts/Release/speakerd"), "--state-dir", str(work / "state"),
               "--render", str(wav), str(work / "out.wav"), "--block", str(BLOCK), "--lv2-bundle", str(b)]
    with open(work / "speakerd.log", "w") as log:
        status = subprocess.run(command, env=dict(os.environ, HOME=str(work / "home")), stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT, timeout=600).returncode
    text = (work / "speakerd.log").read_text(errors="replace")
    ran = re.findall(r"plugin node 16: .*, bundle (.*)$", text, re.MULTILINE)
    if status != 0 or [os.path.realpath(p) for p in ran] != [os.path.realpath(b)]:
        raise AssertionError(f"the render of {b} failed (status {status}); log {work / 'speakerd.log'}:\n{text}")
    out = read_wav(work / "out.wav")
    return out[0], out[1]


def rms_db(x):
    a, b = int(WINDOW_S[0] * RATE), len(x) - int(WINDOW_S[1] * RATE)
    return 10 * np.log10(np.mean(x[a:b] ** 2) + 1e-30)


def delayed(x, d):
    return np.concatenate([np.zeros(d), x[:len(x) - d]])


def version(plugin, previous=False):
    base = PREVIOUS / "src/dsp/juce_plugins" if previous else PLUGINS
    text = (base / plugin / "CMakeLists.txt").read_text()
    return re.search(r"project\s*\(\s*\w+\s+VERSION\s+([0-9.]+)", text).group(1)


class Bypass(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        OUT.mkdir(parents=True, exist_ok=True)
        cls.x, cls.y = {}, {}
        for kind in SIGNALS:
            write_wav(OUT / f"{kind}.wav", signal(kind))
            cls.x[kind] = read_wav(OUT / f"{kind}.wav")[0]          # as speakerd reads it
        for plugin in ("firConv", "crossover"):
            for kind in SIGNALS:
                for gain in GAINS_DB:
                    for bypassed in (False, True):
                        name = f"{plugin}-{kind}-{gain:g}dB-{'bypassed' if bypassed else 'processed'}"
                        cls.y[plugin, kind, gain, bypassed] = render(plugin, OUT / f"{kind}.wav", gain, bypassed, name)
                    if PREVIOUS is not None:
                        name = f"{plugin}-{version(plugin, True)}-{kind}-{gain:g}dB-processed"
                        cls.y[plugin, kind, gain, "previous"] = render(plugin, OUT / f"{kind}.wav", gain, False, name, True)

        lines = [f"Levels, speakerd --render at {RATE} Hz in blocks of {BLOCK}, after its second of silent settling; "
                 f"RMS from {WINDOW_S[0]:g} s to {WINDOW_S[1]:g} s before the end, dBFS.",
                 "Signals: " + "; ".join(SIGNALS.values()) + "."]
        for plugin in ("firConv", "crossover"):
            lines.append("")
            lines.append(f"{plugin} {version(plugin)}, output 1 (" + ("the only output" if plugin == "firConv" else "low band")
                         + "), and output 2 (high band) for the crossover")
            lines.append(f"{'signal':>6} {'gain':>6} {'input':>8} {'processed':>10} {'bypassed':>9} {'bypassed - processed':>21}"
                         + ("" if plugin == "firConv" else f" {'out 2 proc.':>11} {'out 2 byp.':>10}"))
            for kind in SIGNALS:
                for gain in GAINS_DB:
                    p, b = cls.y[plugin, kind, gain, False], cls.y[plugin, kind, gain, True]
                    row = (f"{kind:>6} {gain:>6g} {rms_db(cls.x[kind]):>8.2f} {rms_db(p[0]):>10.2f} {rms_db(b[0]):>9.2f} "
                           f"{rms_db(b[0]) - rms_db(p[0]):>+21.2f}")
                    if plugin == "crossover":
                        row += f" {rms_db(p[1]):>11.2f} {rms_db(b[1]):>10.2f}"
                    lines.append(row)
        if PREVIOUS is not None:
            lines.append("")
            lines.append("Processed against the previous version, every output, both signals and gains: "
                         + "; ".join(f"{plugin} {version(plugin)} against {version(plugin, True)}: "
                                     + ("identical" if all(np.array_equal(cls.y[plugin, k, g, False], cls.y[plugin, k, g, "previous"])
                                                           for k in SIGNALS for g in GAINS_DB) else "DIFFERENT")
                                     for plugin in ("firConv", "crossover")) + ".")
        (OUT / "levels.txt").write_text("\n".join(lines) + "\n")

    def check_exact(self, plugin, kind, gain, out, delay):
        x = self.x[kind]
        expected = np.float32(10 ** (gain / 20)) * delayed(x, delay)
        worst = np.max(np.abs(out - expected))
        worst_db = 20 * np.log10(worst / np.max(np.abs(x)) + 1e-30)
        i = int(np.argmax(np.abs(out - expected)))
        self.assertLessEqual(worst_db, EXACT_DB,
                             f"{plugin} bypassed, {kind}, gain {gain:g} dB: the output differs from the input times "
                             f"the gain delayed by {delay} samples by {worst_db:.1f} dB re the input's peak (limit "
                             f"{EXACT_DB:g}), at sample {i}: {out[i]:.6g} against {expected[i]:.6g}. Its level is "
                             f"{rms_db(out):.2f} dBFS against the input's {rms_db(x):.2f} dBFS")

    def test_firconv_bypassed_is_the_input_times_the_gain(self):
        for kind in SIGNALS:
            for gain in GAINS_DB:
                with self.subTest(kind=kind, gain=gain):
                    self.check_exact("firConv", kind, gain, self.y["firConv", kind, gain, True][0], FIRCONV_DELAY)

    def test_crossover_bypassed_is_the_input_times_the_gain_on_its_low_band(self):
        for kind in SIGNALS:
            for gain in GAINS_DB:
                with self.subTest(kind=kind, gain=gain):
                    self.check_exact("crossover", kind, gain, self.y["crossover", kind, gain, True][0], CROSSOVER_DELAY)

    def test_processed_is_the_previous_versions_output(self):
        if PREVIOUS is None:
            self.skipTest("no BYPASS_PREVIOUS: run.sh builds the previous versions")
        expected = {"firConv": "0.0.3", "crossover": "0.0.2"}
        for plugin in ("firConv", "crossover"):
            self.assertEqual(version(plugin, True), expected[plugin], f"{plugin}: the version run.sh built as the previous one")
            for kind in SIGNALS:
                for gain in GAINS_DB:
                    with self.subTest(plugin=plugin, kind=kind, gain=gain):
                        now, before = self.y[plugin, kind, gain, False], self.y[plugin, kind, gain, "previous"]
                        for channel, (a, b) in enumerate(zip(now, before), start=1):
                            if np.array_equal(a, b):
                                continue
                            i = int(np.argmax(a != b))
                            worst = np.max(np.abs(a - b))
                            self.fail(f"{plugin} {version(plugin)} processed, {kind}, gain {gain:g} dB, output {channel}: "
                                      f"not bit for bit {version(plugin, True)}'s. {np.count_nonzero(a != b)} samples "
                                      f"differ, the first at {i} ({a[i]:.9g} against {b[i]:.9g}), the worst by "
                                      f"{20 * np.log10(worst / np.max(np.abs(b)) + 1e-30):.1f} dB re its peak")

    def test_no_plugin_bypass_parameter(self):
        for plugin in ("firConv", "crossover"):
            with self.subTest(plugin=plugin):
                ttl = (bundle(plugin) / "dsp.ttl").read_text()
                params = sorted(set(re.findall(r"^plug:(\w+)\s*\n\s*a lv2:Parameter", ttl, re.MULTILINE)))
                self.assertEqual(params, ["freq", "gain"], f"{plugin}: the parameters its dsp.ttl declares")
                self.assertIn("lv2:designation lv2:enabled", ttl, f"{plugin}: no lv2:enabled port, so no host bypass")

    def test_crossover_bypassed_high_band_is_silent(self):
        for kind in SIGNALS:
            for gain in GAINS_DB:
                with self.subTest(kind=kind, gain=gain):
                    high = self.y["crossover", kind, gain, True][1]
                    self.assertFalse(np.any(high), f"crossover bypassed, {kind}, gain {gain:g} dB: its high band "
                                                   f"(the tweeter's) is not silent: peak {np.max(np.abs(high)):.3g}")


if __name__ == "__main__":
    unittest.main()
