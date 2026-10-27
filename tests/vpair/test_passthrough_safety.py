"""passthrough_safety (docs/autonomous-development.md, section 5): with every plugin removed,
the tweeter capture is silent and the woofer capture is capped.

speakerd plays passthrough when no state layer loads (docs/remote-plugin-host.md, section 8):
the speaker's own channel (FL on a left speaker, FR on a right one, (FL + FR) / 2 on a mono
one) to the woofer only, 20 dB down, so that nothing in passthrough is louder than -20 dBFS,
and digital silence to the tweeter. Two ways there: every plugin removed (three valid layers
naming the headless build of loudness, and no plugin to be found: no --lv2-bundle, an empty
HOME), and no layer at all (no layer files). The source is stereo and its channels differ,
FL 250 Hz at -1 dBFS peak and FR 400 Hz at -7 dBFS peak, so the woofer shows which channel
speakerd chose. A second source drives the input above full scale, FL at +6 dBFS and FR at
+3 dBFS peak (a float WAV), as a player with its volume above 100 % would.

Pass when, for role left and role right in both ways, and for role mono with every plugin
removed:

- speakerd says PASSTHROUGH and that it plays layer passthrough with the right channel;
- the tweeter capture is digital silence: below -120 dBFS over the whole capture;
- the woofer is the speaker's own channel at -20 dB: its level is the own channel's minus
  20 dB within 0.05 dB, it holds the own channel's tone at -20 dB within 0.05 dB and not the
  other channel's (below -100 dBFS), and its peak is not above -20 dBFS;

and, for role left and role right with the input above full scale, the tweeter is digital
silence, the woofer still plays its own channel and not the other one, and its peak never
exceeds the -20 dBFS cap (by more than the 0.01 dB a float's rounding leaves).

    python3 -m unittest -v tests.vpair.test_passthrough_safety
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from harness import (RATE, Pair, Speakerd, align, built, graph_document, lv2_uri, peak_dbfs,
                     rms_dbfs, sine_amplitudes_dbfs, state_dir, tone)

import numpy as np

CAP_DB = -20.0
ROUNDING_DB = 0.01
TOLERANCE_DB = 0.05
SILENCE_DBFS = -120.0
ABSENT_DBFS = -100.0
# Both tones are harmonics of 50 Hz: over a whole number of 50 Hz periods, a fit of one does
# not leak into the other, whatever harmonics a clipped tone has.
PERIOD = RATE // 50


def stereo(fl, fr):
    """(frequency, peak dBFS) for FL and FR, one second."""
    return {"FL": fl, "FR": fr, "samples": np.stack([tone(*fl, 1.0), tone(*fr, 1.0)], axis=1)}


NORMAL = stereo((250.0, -1.0), (400.0, -7.0))
ABOVE_FULL_SCALE = stereo((250.0, 6.0), (400.0, 3.0))

DOCUMENT = graph_document(lv2_uri("loudness_headless"),
                          {"mode": "Force 1", "s0_e0_gain": 12.0, "s0_e0_freq": 120.0})
EVERY_PLUGIN_REMOVED = {"current": DOCUMENT, "lkg": DOCUMENT, "factory": DOCUMENT}
NO_LAYER = {}


def woofer_content(role, source):
    """What the woofer should carry: the channel speakerd names, the signal, and the source's
    components in it as (frequency, peak dBFS at the woofer, or None where it must be absent)."""
    fl, fr, x = source["FL"], source["FR"], source["samples"]
    if role == "left":
        return "FL", x[:, 0], [(fl[0], fl[1] + CAP_DB), (fr[0], None)]
    if role == "right":
        return "FR", x[:, 1], [(fr[0], fr[1] + CAP_DB), (fl[0], None)]
    return ("(FL + FR) / 2", x.mean(axis=1),
            [(fl[0], fl[1] - 6.0206 + CAP_DB), (fr[0], fr[1] - 6.0206 + CAP_DB)])


class PassthroughSafety(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        built("speakerd")
        cls.tmp = Path(tempfile.mkdtemp(prefix="vpair-passthrough-"))
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)
        cls.pair = Pair().__enter__()
        cls.addClassCleanup(cls.pair.__exit__, None, None, None)

    def play(self, role, speaker_name, layers, case, source):
        """speakerd in passthrough on a speaker, the source played into it: the woofer capture,
        the woofer and the own channel over the same steady samples, the components found, and
        a description of what was measured."""
        channel, own, components = woofer_content(role, source)
        speaker = self.pair[speaker_name]
        state = state_dir(self.tmp / f"{case.replace(' ', '-')}-{role}", role=role, **layers)

        with Speakerd(speaker, state) as sd:   # no --lv2-bundle, an empty HOME
            self.assertTrue(sd.lines_starting("*** PASSTHROUGH: no state layer loaded ***"), sd.log())
            self.assertTrue(sd.lines_starting(
                f"using layer passthrough: woofer = {channel} at -20 dB, tweeter muted"), sd.log())
            self.assertTrue(sd.lines_starting("playing: layer passthrough, "), sd.log())
            capture = speaker.play_and_record(source["samples"], target="speakerd",
                                              record_target="amplifier")

        woofer, tweeter = capture[:, 0], capture[:, 1]
        # The woofer and the own channel over the same samples, past the edges of the tone,
        # a whole number of 50 Hz periods long.
        lag, skip = align(woofer, own), int(0.1 * RATE)
        length = (len(own) - 2 * skip) // PERIOD * PERIOD
        part, own_part = woofer[lag + skip:lag + skip + length], own[skip:skip + length]
        amplitudes = sine_amplitudes_dbfs(part, [freq for freq, _ in components])
        found = [(freq, amplitude, level) for (freq, level), amplitude in zip(components, amplitudes)]
        summary = (f"{role} on the {speaker_name} speaker, {case}: tweeter {rms_dbfs(tweeter):.1f} dBFS "
                   f"rms, peak {peak_dbfs(tweeter):.1f} dBFS; woofer {rms_dbfs(part):.4f} dBFS rms "
                   f"(own channel {channel} {CAP_DB:+.0f} dB: {rms_dbfs(own_part) + CAP_DB:.4f}), "
                   f"peak {peak_dbfs(woofer):.4f} dBFS; "
                   + ", ".join(f"{freq:.0f} Hz at {level:.3f} dBFS" for freq, level, _ in found))
        print(f"\n  {summary}")

        self.assertEqual(len(part), length, f"the capture does not hold the whole tone: {summary}")
        self.assertLess(rms_dbfs(tweeter), SILENCE_DBFS, f"the tweeter is not silent: {summary}")
        return woofer, part, own_part, found, summary

    def check(self, role, speaker_name, layers, case):
        woofer, part, own, found, summary = self.play(role, speaker_name, layers, case, NORMAL)
        self.assertAlmostEqual(rms_dbfs(part), rms_dbfs(own) + CAP_DB, delta=TOLERANCE_DB,
                               msg=f"the woofer is not the own channel at -20 dB: {summary}")
        for freq, level, wanted in found:
            if wanted is None:
                self.assertLess(level, ABSENT_DBFS, f"the woofer carries the other channel: {summary}")
            else:
                self.assertAlmostEqual(level, wanted, delta=TOLERANCE_DB,
                                       msg=f"the woofer's {freq:.0f} Hz is not at {wanted:.3f} dBFS: {summary}")
        self.assertLessEqual(peak_dbfs(woofer), CAP_DB + ROUNDING_DB,
                             f"the woofer is above the cap: {summary}")

    def check_above_full_scale(self, role, speaker_name):
        woofer, part, own, found, summary = self.play(role, speaker_name, EVERY_PLUGIN_REMOVED,
                                                      "input above full scale", ABOVE_FULL_SCALE)
        (_, own_level, _), (_, other_level, _) = found
        self.assertGreater(own_level, CAP_DB - 10.0, f"the woofer does not play its own channel: {summary}")
        self.assertLess(other_level, ABSENT_DBFS, f"the woofer carries the other channel: {summary}")
        self.assertLessEqual(peak_dbfs(woofer), CAP_DB + ROUNDING_DB,
                             f"the woofer exceeds the -20 dBFS cap: {summary}")

    def test_left_with_every_plugin_removed(self):
        self.check("left", "left", EVERY_PLUGIN_REMOVED, "every plugin removed")

    def test_right_with_every_plugin_removed(self):
        self.check("right", "right", EVERY_PLUGIN_REMOVED, "every plugin removed")

    def test_left_with_no_layer(self):
        self.check("left", "left", NO_LAYER, "no layer")

    def test_right_with_no_layer(self):
        self.check("right", "right", NO_LAYER, "no layer")

    def test_mono_with_every_plugin_removed(self):
        self.check("mono", "left", EVERY_PLUGIN_REMOVED, "every plugin removed")

    def test_left_input_above_full_scale_stays_capped(self):
        self.check_above_full_scale("left", "left")

    def test_right_input_above_full_scale_stays_capped(self):
        self.check_above_full_scale("right", "right")


if __name__ == "__main__":
    unittest.main()
