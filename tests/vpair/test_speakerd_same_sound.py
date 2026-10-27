"""speakerd sounds like the complete build (step 2 of docs/remote-plugin-host.md).

speakerd runs on the left virtual speaker with the headless build of loudness, from a
graph.json written by hand: the input's FL through loudness to both amplifier channels, with
parameter values by parameter ID that are not the defaults. Mode is Auto, so the level
detector chooses the setting: the setting for -32 dBFS and up (+10 dB below 90 Hz, -4 dB
below 400 Hz) for tones at -20 dBFS peak, the one for -50 dBFS and up (+6 dB below 150 Hz)
for a tone at -40 dBFS peak. Steady tones from 40 Hz to 6 kHz go through speakerd live, and
the same document, pointed at the complete build, is rendered offline with speakerd --render:
what the plugin outside speakerd outputs.

Pass when:

- speakerd plays layer current (graph.json), revision 5, and the render ran the same layer;
- at every tone, the gain on each amplifier channel, live, is within 0.05 dB of the render's,
  and the capture differs from the render by less than -80 dBFS there (PipeWire converts
  samples with 24-bit precision, which leaves about -91 dBFS: speakerd's README);
- the render's gain differs from 0 dB by more than 1 dB at one frequency at least, and the
  80 Hz tones at the two levels differ by more than 1 dB, so the comparison is of the plugin
  at work, level detector included, not of a wire.

    python3 -m unittest -v tests.vpair.test_speakerd_same_sound
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from harness import (RATE, Pair, Speakerd, align, built, graph_document, lv2_uri, render,
                     rms_dbfs, silence, state_dir, tone, with_plugin)

import numpy as np

PARAMS = {
    # Auto mode (the default) with the level choosing the setting:
    "s1_thr": -50.0, "s1_e0_gain": 6.0, "s1_e0_freq": 150.0,
    "s2_thr": -32.0, "s2_e0_gain": 10.0, "s2_e0_freq": 90.0, "s2_e0_q": 0.9,
    "s2_e1_gain": -4.0, "s2_e1_freq": 400.0,
    # quicker ballistics and glide than the defaults
    "atk": 50.0, "rel": 200.0, "glide": 60.0,
}
DOCUMENT = graph_document(lv2_uri("loudness_headless"), PARAMS, revision=5)
LAYER = "current (graph.json), revision 5"

# (frequency, peak level): low ones where the shelves act, a few above, and 80 Hz again at a
# level that selects another setting.
TONES = [(40.0, -20.0), (80.0, -20.0), (120.0, -20.0), (250.0, -20.0), (1000.0, -20.0),
         (6000.0, -20.0), (80.0, -40.0)]
TONE_SECONDS, GAP_SECONDS = 1.0, 0.25
# Where each tone is measured: after the detector's attack and the glide, before its end.
WINDOW = (0.45, 0.95)

TOLERANCE_DB = 0.05
RESIDUAL_DBFS = -80.0


def test_signal():
    """The tones one after another, with silence between them, and where each one starts."""
    parts, starts, at = [], [], 0
    for freq, level in TONES:
        starts.append(at)
        parts += [tone(freq, level, TONE_SECONDS), silence(GAP_SECONDS)]
        at += int((TONE_SECONDS + GAP_SECONDS) * RATE)
    return np.concatenate(parts), starts


class SameSoundAsTheCompleteBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.headless = built("loudness", "headless")
        cls.complete = built("loudness", "complete")
        built("speakerd")
        cls.tmp = Path(tempfile.mkdtemp(prefix="vpair-same-sound-"))
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)
        cls.pair = Pair().__enter__()
        cls.addClassCleanup(cls.pair.__exit__, None, None, None)

    def test_live_gain_matches_the_complete_build(self):
        signal, starts = test_signal()

        reference_state = state_dir(self.tmp / "reference",
                                    current=with_plugin(DOCUMENT, lv2_uri("loudness")))
        reference, log = render(reference_state, signal, bundles=[self.complete])
        self.assertIn(f", layer {LAYER}, written to ", log,
                      f"the render did not run the document:\n{log}")

        speaker = self.pair["left"]
        with Speakerd(speaker, state_dir(self.tmp / "live", current=DOCUMENT),
                      bundles=[self.headless]) as sd:
            self.assertTrue(sd.lines_starting(f"using layer {LAYER}"), sd.log())
            self.assertTrue(sd.lines_starting(f"playing: layer {LAYER}, 48000 Hz"), sd.log())
            capture = speaker.play_and_record(signal, target="speakerd", record_target="amplifier")

        lag = align(capture, reference)
        self.assertLessEqual(lag + len(reference), len(capture),
                             f"the capture ({len(capture)} samples) does not hold the whole "
                             f"signal ({len(reference)} samples) from {lag} on")
        live = capture[lag:lag + len(reference)]

        rows, failures, residuals = [], [], []
        render_gains = {}
        for (freq, level), start in zip(TONES, starts):
            window = slice(start + int(WINDOW[0] * RATE), start + int(WINDOW[1] * RATE))
            source = rms_dbfs(signal[window])
            for channel, name in ((0, "woofer"), (1, "tweeter")):
                live_gain = rms_dbfs(live[window, channel]) - source
                render_gain = rms_dbfs(reference[window, channel]) - source
                difference = live_gain - render_gain
                residual = 20 * np.log10(max(np.max(np.abs(live[window, channel]
                                                            - reference[window, channel])), 1e-12))
                residuals.append(residual)
                render_gains[(freq, level, name)] = render_gain
                row = (f"{freq:6.0f} Hz at {level:+.0f} dBFS, {name:7s}: live {live_gain:+.3f} dB, "
                       f"complete build {render_gain:+.3f} dB, difference {difference:+.4f} dB, "
                       f"residual {residual:.1f} dBFS")
                rows.append(row)
                if abs(difference) > TOLERANCE_DB or residual > RESIDUAL_DBFS:
                    failures.append(row)

        table = "\n  ".join(rows)
        print(f"\n  capture aligned at {lag} samples; worst residual {max(residuals):.1f} dBFS\n  {table}")

        self.assertEqual(failures, [],
                         f"live and the complete build differ by more than {TOLERANCE_DB} dB, or "
                         f"by a residual above {RESIDUAL_DBFS} dBFS, at:\n  " + "\n  ".join(failures)
                         + f"\nall tones:\n  {table}")

        largest = max(render_gains.values(), key=abs)
        self.assertGreater(abs(largest), 1.0,
                           f"the plugin does nothing at any frequency (largest gain {largest:+.3f} dB): "
                           f"the test would prove nothing\n  {table}")
        loud, quiet = render_gains[(80.0, -20.0, "woofer")], render_gains[(80.0, -40.0, "woofer")]
        self.assertGreater(abs(loud - quiet), 1.0,
                           f"80 Hz has the same gain at -20 dBFS ({loud:+.3f} dB) and at -40 dBFS "
                           f"({quiet:+.3f} dB): the level detector chose the same setting\n  {table}")


if __name__ == "__main__":
    unittest.main()
