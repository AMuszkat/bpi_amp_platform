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

A capture that an xrun broke (a 50 Hz period of the woofer more than 1 dB quieter than the others,
as a machine loaded with other test runs sometimes does) is played again, up to three times, and
said: the level is then measured on a whole capture. An xrun only makes samples quieter, so every
attempt is first checked for what it cannot do: a tweeter sample that is not 0, a woofer sample
above the cap, a period more than 1 dB louder than the others each fail at once. A speakerd that
broke its own output would break every attempt, and the module fails.

Pass when, for role left, right and mono, each with every plugin removed and with no layer:

- speakerd says PASSTHROUGH and that it plays layer passthrough with the right channel;
- the tweeter capture is digital silence: every sample of it is exactly 0. The capture is
  32-bit integer with no dither and speakerd sends exact zeros, so a click of one least
  significant bit fails this;
- the woofer is the speaker's own channel at -20 dB: its level is the own channel's minus
  20 dB within 0.05 dB, it holds the own channel's tone at -20 dB within 0.05 dB and not the
  other channel's (below -100 dBFS), and its peak is not above -20 dBFS;

for role left and role right with the input above full scale:

- the input reaches speakerd above full scale: the speakerd sink's monitor, recorded as
  32-bit float, peaks above 0 dBFS on the own channel. So it is speakerd's cap that holds the
  woofer at -20 dBFS, not PipeWire clipping the input at full scale on the way;
- the tweeter is exactly 0, the woofer still plays its own channel and not the other one, and
  its peak never exceeds the -20 dBFS cap (by more than the 0.01 dB a float's rounding leaves);

and, deterministically, for role left, right and mono, speakerd's offline render (--render)
with no layer, of the input above full scale:

- the woofer is the own channel at -20 dB clamped to -20 dBFS, sample for sample (within
  1e-6, the rounding of floats and of the 32-bit output): it peaks at exactly -20 dBFS
  (within 1e-6 dB), where it would peak at -14 to -17 dBFS without the clamp;
- the tweeter is exactly 0.

The render reads the float WAV unclipped (JUCE's WAV reader). The mono case shows it: the mix
clamped after the sum differs by up to 0.05 from the mix of inputs clipped at full scale
first. The render writes 32-bit integer PCM, which clips at full scale, but even without the
clamp the woofer would stay below -14 dBFS, so the output format hides nothing here.

    python3 -m unittest -v tests.vpair.test_passthrough_safety
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from harness import (RATE, Pair, Speakerd, align, built, graph_document, lv2_uri, peak_dbfs,
                     block_levels, render, rms_dbfs, sine_amplitudes_dbfs, state_dir, tone)

import numpy as np

CAP_DB = -20.0
ROUNDING_DB = 0.01
TOLERANCE_DB = 0.05
ATTEMPTS = 3                 # plays of a capture an xrun broke (see play())
ABSENT_DBFS = -100.0
# The render's woofer against the clamped own channel: float rounding and the 32-bit output.
RENDER_SAMPLE_TOLERANCE = 1e-6
RENDER_PEAK_TOLERANCE_DB = 1e-6
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


def nonzero(x):
    """How many samples of `x` are not exactly 0, and the loudest of them."""
    return int(np.count_nonzero(x)), peak_dbfs(x)


class PassthroughSafety(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        built("speakerd")
        cls.tmp = Path(tempfile.mkdtemp(prefix="vpair-passthrough-"))
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)
        cls.pair = Pair().__enter__()
        cls.addClassCleanup(cls.pair.__exit__, None, None, None)

    def play(self, role, speaker_name, layers, case, source, record_input=False):
        """speakerd in passthrough on a speaker, the source played into it: the woofer capture,
        the woofer and the own channel over the same steady samples, the components found, a
        description of what was measured, and, with `record_input`, what the speakerd sink's
        monitor received, recorded as floats."""
        channel, own, components = woofer_content(role, source)
        speaker = self.pair[speaker_name]
        state = state_dir(self.tmp / f"{case.replace(' ', '-')}-{role}", role=role, **layers)
        records = [("amplifier", "s32")] + ([("speakerd", "f32")] if record_input else [])

        with Speakerd(speaker, state) as sd:   # no --lv2-bundle, an empty HOME
            self.assertTrue(sd.lines_starting("*** PASSTHROUGH: no state layer loaded ***"), sd.log())
            self.assertTrue(sd.lines_starting(
                f"using layer passthrough: woofer = {channel} at -20 dB, tweeter muted"), sd.log())
            self.assertTrue(sd.lines_starting("playing: layer passthrough, "), sd.log())
            # A capture an xrun broke (a machine loaded with other test runs) is played again, up
            # to three times: what is measured is speakerd, not the machine's load. An xrun only
            # makes samples quieter (missing): so every attempt is first checked for what an xrun
            # cannot do, a tweeter sample that is not 0, a woofer above the cap, or a period louder
            # than the others, and fails on it; only a quieter period is played again. A speakerd
            # that broke its output itself would break every attempt, and fail.
            for attempt in range(1, ATTEMPTS + 1):
                captures = speaker.play_and_record_many(source["samples"], "speakerd", records)
                capture = captures[0]
                woofer, tweeter = capture[:, 0], capture[:, 1]
                # The woofer and the own channel over the same samples, past the edges of the
                # tone, a whole number of 50 Hz periods long.
                lag, skip = align(woofer, own), int(0.1 * RATE)
                length = (len(own) - 2 * skip) // PERIOD * PERIOD
                part, own_part = woofer[lag + skip:lag + skip + length], own[skip:skip + length]
                loud, tweeter_peak = nonzero(tweeter)
                self.assertEqual(loud, 0, f"attempt {attempt}: the tweeter is not digital silence: {loud} samples "
                                          f"not 0, peak {tweeter_peak:.1f} dBFS ({role}, {case})")
                self.assertLessEqual(peak_dbfs(woofer), CAP_DB + ROUNDING_DB,
                                     f"attempt {attempt}: the woofer is above the cap: peak {peak_dbfs(woofer):.4f} "
                                     f"dBFS ({role}, {case})")
                blocks = block_levels(part, PERIOD / RATE)
                louder = float(np.max(blocks - np.median(blocks))) if len(blocks) else 0.0
                self.assertLess(louder, 1.0, f"attempt {attempt}: a 50 Hz period of the woofer is {louder:.2f} dB "
                                             f"louder than the others, which no xrun does ({role}, {case})")
                worst = float(np.max(np.median(blocks) - blocks)) if len(blocks) else 0.0
                if len(part) == length and worst < 1.0:
                    break
                print(f"\n  attempt {attempt}: the capture was interrupted (a 50 Hz period {worst:.1f} dB quieter than "
                      "the others: an xrun?)" + (": playing it again" if attempt < ATTEMPTS else ""), end="")
        amplitudes = sine_amplitudes_dbfs(part, [freq for freq, _ in components])
        found = [(freq, amplitude, level) for (freq, level), amplitude in zip(components, amplitudes)]
        loud, tweeter_peak = nonzero(tweeter)
        summary = (f"{role} on the {speaker_name} speaker, {case}: tweeter {loud} of "
                   f"{len(tweeter)} samples not 0, peak {tweeter_peak:.1f} dBFS; woofer "
                   f"{rms_dbfs(part):.4f} dBFS rms "
                   f"(own channel {channel} {CAP_DB:+.0f} dB: {rms_dbfs(own_part) + CAP_DB:.4f}), "
                   f"peak {peak_dbfs(woofer):.4f} dBFS; "
                   + ", ".join(f"{freq:.0f} Hz at {level:.3f} dBFS" for freq, level, _ in found))
        if record_input:
            summary += ("; speakerd's input peaks at FL " + ", FR ".join(
                f"{peak_dbfs(captures[1][:, c]):+.3f}" for c in (0, 1)) + " dBFS")
        print(f"\n  {summary}")

        self.assertEqual(len(part), length, f"the capture does not hold the whole tone: {summary}")
        self.assertEqual(loud, 0, f"the tweeter is not digital silence: {summary}")
        return woofer, part, own_part, found, summary, captures[1:]

    def check(self, role, speaker_name, layers, case):
        woofer, part, own, found, summary, _ = self.play(role, speaker_name, layers, case, NORMAL)
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
        woofer, part, own, found, summary, (received,) = self.play(
            role, speaker_name, EVERY_PLUGIN_REMOVED, "input above full scale", ABOVE_FULL_SCALE,
            record_input=True)
        (_, own_level, _), (_, other_level, _) = found
        own_channel = 0 if role == "left" else 1
        self.assertGreater(peak_dbfs(received[:, own_channel]), 0.0,
                           f"the input did not reach speakerd above full scale, so this proves "
                           f"nothing about the cap: {summary}")
        self.assertGreater(own_level, CAP_DB - 10.0, f"the woofer does not play its own channel: {summary}")
        self.assertLess(other_level, ABSENT_DBFS, f"the woofer carries the other channel: {summary}")
        self.assertLessEqual(peak_dbfs(woofer), CAP_DB + ROUNDING_DB,
                             f"the woofer exceeds the -20 dBFS cap: {summary}")

    def check_render_above_full_scale(self, role):
        """The render of the input above full scale with no layer, against the own channel at
        -20 dB clamped to -20 dBFS, computed here from the same float samples."""
        source = ABOVE_FULL_SCALE["samples"]
        output, log = render(state_dir(self.tmp / f"render-{role}", role=role), source)
        self.assertIn(", layer passthrough, written to ", log, log)

        gain = float(np.float32(10 ** (CAP_DB / 20)))
        played = source.astype(np.float32).astype(np.float64)   # what the float WAV holds
        _, own, _ = woofer_content(role, {**ABOVE_FULL_SCALE, "samples": played})
        unclamped = gain * own
        expected = np.clip(unclamped, -gain, gain)
        woofer, tweeter = output[:, 0], output[:, 1]
        self.assertEqual(len(output), len(source), log)

        error = float(np.max(np.abs(woofer - expected)))
        loud, tweeter_peak = nonzero(tweeter)
        summary = (f"render, {role}, no layer, input above full scale: woofer peak "
                   f"{peak_dbfs(woofer):.6f} dBFS (unclamped it would be {peak_dbfs(unclamped):.2f} "
                   f"dBFS), largest difference from the clamped own channel {error:.2e}; tweeter "
                   f"{loud} samples not 0, peak {tweeter_peak:.1f} dBFS")
        if role == "mono":
            clipped_first = gain * np.clip(played, -1.0, 1.0).mean(axis=1)
            summary += (f"; the mix of inputs clipped at full scale would differ by up to "
                        f"{np.max(np.abs(woofer - clipped_first)):.3f}")
        print(f"\n  {summary}")

        self.assertLess(error, RENDER_SAMPLE_TOLERANCE,
                        f"the woofer is not the own channel at -20 dB, clamped: {summary}")
        self.assertAlmostEqual(peak_dbfs(woofer), CAP_DB, delta=RENDER_PEAK_TOLERANCE_DB,
                               msg=f"the woofer does not peak at exactly -20 dBFS: {summary}")
        self.assertEqual(loud, 0, f"the tweeter is not digital silence: {summary}")

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

    def test_mono_with_no_layer(self):
        self.check("mono", "right", NO_LAYER, "no layer")

    def test_left_input_above_full_scale_stays_capped(self):
        self.check_above_full_scale("left", "left")

    def test_right_input_above_full_scale_stays_capped(self):
        self.check_above_full_scale("right", "right")

    def test_render_left_input_above_full_scale_is_clamped(self):
        self.check_render_above_full_scale("left")

    def test_render_right_input_above_full_scale_is_clamped(self):
        self.check_render_above_full_scale("right")

    def test_render_mono_input_above_full_scale_is_clamped(self):
        self.check_render_above_full_scale("mono")


if __name__ == "__main__":
    unittest.main()
