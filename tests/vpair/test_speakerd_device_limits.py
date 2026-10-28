"""speakerd_device_limits (docs/autonomous-development.md, section 5): device.json's limits
hold, live and in the render, as a ceiling whatever the input or the graph, and a device.json
that does not say them plays capped all the same.

The limits are `{"gain_db": <dB, 0 or below>, "tweeter": "muted"|"on"}`: the output is
attenuated by gain_db after the graph and clamped at +-10^(gain_db/20), and a muted tweeter
gets exact zeros. A device.json without limits, with a key speakerd does not know, broken or
missing, plays with the fail-safe limits, -20 dB and the tweeter muted (and, when the file is
unusable, speakerd's default role and amplifier, `bpi-alsa-playback-pcm`, which a null sink of
that name stands for here). "No limit" has to be written: `{"gain_db": 0, "tweeter": "on"}`.

The document is loudness's headless build forced to its first setting on both amplifier
channels, +12 dB at 120 Hz (a boost document has +20 dB). Captures are 32-bit float.

Pass when:

- live, with `{"gain_db": -20, "tweeter": "muted"}`: every sample of the tweeter capture is 0,
  and the woofer's gain for a 100 Hz tone at -20 dBFS is the unlimited capture's (limits
  `{"gain_db": 0, "tweeter": "on"}`) minus 20 dB within 0.05 dB; the same in the render;
- the limit is a ceiling: a +6 dBFS input through the document, and a -1 dBFS input through the
  boost document (whose render without limits peaks above -10 dBFS), never put a woofer sample
  above -20 dBFS (by more than a float's rounding, 0.01 dB), live or in the render;
- passthrough (no layer) with the same limits peaks at no more than -40 dBFS on the woofer, and
  plays (above -50 dBFS), with the tweeter at exact zeros, for a +6 dBFS input;
- a device.json with no `limits` says it plays the fail-safe limits; one with a misspelt
  `"limts"`, one that is not valid JSON and a missing one each say `device.json: FAILED`; and
  each plays the boost document at -1 dBFS with the tweeter at exact zeros and the woofer at
  no more than -20 dBFS (and above -30 dBFS: it plays).

These need the fail-closed limits of speakerd that came after commit 2cb3278 (and after
b10e23b for the ceiling and the fail-closed file): against an older speakerd they fail, and say
so. The wording matched for the fail-safe line is FAIL_SAFE below.

    python3 -m unittest -v tests.vpair.test_speakerd_device_limits
"""

import re
import shutil
import tempfile
import unittest
from pathlib import Path

from harness import (NO_LIMITS, Pair, Speakerd, active, built, graph_document, lv2_uri, peak_dbfs, render,
                     rms_dbfs, state_dir, steady, tone)

import numpy as np

CAP = {"gain_db": -20, "tweeter": "muted"}
DOCUMENT = graph_document(lv2_uri("loudness_headless"),
                          {"mode": "Force 1", "s0_e0_gain": 12.0, "s0_e0_freq": 120.0})
BOOST = graph_document(lv2_uri("loudness_headless"),
                       {"mode": "Force 1", "s0_e0_gain": 20.0, "s0_e0_freq": 120.0})
TONE = tone(100.0, -20.0, 1.0)
HOT = tone(100.0, 6.0, 1.0)
LOUD = tone(100.0, -1.0, 1.0)
DEFAULT_AMPLIFIER = "bpi-alsa-playback-pcm"
TOLERANCE_DB = 0.05
ROUNDING_DB = 0.01
FAIL_SAFE = re.compile(r"(?i)^speakerd: device.*fail.?safe")
FAILED = "speakerd: device.json: FAILED"
HINT = ("\n  (this needs speakerd's fail-closed device.json limits, which came after 2cb3278: an older "
        "speakerd ignores them)")


def woofer_gain(capture, source):
    part = steady(active(capture))
    return rms_dbfs(part[:, 0]) - rms_dbfs(steady(source))


class DeviceLimits(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.headless = built("loudness", "headless")
        built("speakerd")
        cls.tmp = Path(tempfile.mkdtemp(prefix="vpair-limits-"))
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)
        cls.pair = Pair().__enter__()
        cls.addClassCleanup(cls.pair.__exit__, None, None, None)
        cls.speaker = cls.pair["left"]
        # speakerd's default amplifier, for a device.json it cannot use.
        cls.speaker.create_sink(DEFAULT_AMPLIFIER)

    def live(self, state, samples, amplifier="amplifier", bundles=None):
        """speakerd on the left speaker with `state`: its log, and what `amplifier` received for
        `samples` (each in a list), as floats."""
        with Speakerd(self.speaker, state, bundles=[self.headless] if bundles is None else bundles) as sd:
            captures = [self.speaker.play_and_record(x, target="speakerd", record_target=amplifier, fmt="f32")
                        for x in samples]
            log = sd.log()
        return log, captures

    def check_capped(self, what, capture, ceiling_dbfs=-20.0, floor_dbfs=None):
        woofer, tweeter = capture[:, 0], capture[:, 1]
        summary = (f"{what}: woofer peak {peak_dbfs(woofer):.2f} dBFS (at most {ceiling_dbfs:g}), tweeter "
                   f"{np.count_nonzero(tweeter)} samples not 0")
        print(f"\n  {summary}")
        self.assertEqual(np.count_nonzero(tweeter), 0, summary + HINT)
        self.assertLessEqual(peak_dbfs(woofer), ceiling_dbfs + ROUNDING_DB, summary + HINT)
        if floor_dbfs is not None:
            self.assertGreater(peak_dbfs(woofer), floor_dbfs, f"it does not play: {summary}")

    # ---------------------------------------------------------------------------------------

    def test_minus_20_db_and_the_tweeter_muted(self):
        unlimited = state_dir(self.tmp / "unlimited", current=DOCUMENT, limits=NO_LIMITS)
        limited = state_dir(self.tmp / "limited", current=DOCUMENT, limits=CAP)
        _, (open_capture,) = self.live(unlimited, [TONE])
        log, (capture, hot) = self.live(limited, [TONE, HOT])
        reference, capped = woofer_gain(open_capture, TONE), woofer_gain(capture, TONE)
        summary = f"live: unlimited {reference:+.3f} dB, limited {capped:+.3f} dB (expected {reference - 20:+.3f})"
        print(f"\n  {summary}")
        self.assertEqual(np.count_nonzero(capture[:, 1]), 0, f"the tweeter is not muted: {summary}{HINT}")
        self.assertAlmostEqual(capped, reference - 20.0, delta=TOLERANCE_DB, msg=summary + HINT)
        self.check_capped("live, +6 dBFS in", hot)

        rendered_open, _ = render(unlimited, TONE, bundles=[self.headless])
        rendered, _ = render(limited, TONE, bundles=[self.headless])
        rendered_hot, _ = render(limited, HOT, bundles=[self.headless])
        reference, capped = woofer_gain(rendered_open, TONE), woofer_gain(rendered, TONE)
        summary = f"render: unlimited {reference:+.3f} dB, limited {capped:+.3f} dB"
        print(f"\n  {summary}")
        self.assertEqual(np.count_nonzero(rendered[:, 1]), 0, summary + HINT)
        self.assertAlmostEqual(capped, reference - 20.0, delta=TOLERANCE_DB, msg=summary + HINT)
        self.check_capped("render, +6 dBFS in", rendered_hot)

    def test_a_ceiling_whatever_the_graph(self):
        unlimited = state_dir(self.tmp / "boost-open", current=BOOST, limits=NO_LIMITS)
        limited = state_dir(self.tmp / "boost", current=BOOST, limits=CAP)
        open_render, _ = render(unlimited, LOUD, bundles=[self.headless])
        print(f"\n  the boost document without limits peaks at {peak_dbfs(open_render[:, 0]):.2f} dBFS")
        self.assertGreater(peak_dbfs(open_render[:, 0]), -10.0, "the boost document does not boost")
        _, (capture,) = self.live(limited, [LOUD])
        self.check_capped("live, boost document, -1 dBFS in", capture)
        rendered, _ = render(limited, LOUD, bundles=[self.headless])
        self.check_capped("render, boost document, -1 dBFS in", rendered)

    def test_passthrough(self):
        state = state_dir(self.tmp / "passthrough", limits=CAP)
        log, (capture,) = self.live(state, [HOT], bundles=[])
        self.assertIn("speakerd: *** PASSTHROUGH: no state layer loaded ***", log.splitlines())
        self.check_capped("live passthrough, +6 dBFS in", capture, ceiling_dbfs=-40.0, floor_dbfs=-50.0)
        rendered, _ = render(state, HOT)
        self.check_capped("render passthrough, +6 dBFS in", rendered, ceiling_dbfs=-40.0, floor_dbfs=-50.0)

    def test_fail_safe(self):
        good = {"version": 1, "role": "left", "amplifier": {"node": "amplifier"}}
        cases = [
            ("no limits", good, "amplifier", FAIL_SAFE),
            ("misspelt limts", dict(good, limts=NO_LIMITS), DEFAULT_AMPLIFIER, FAILED),
            ("not JSON", '{"version": 1, "role": "left", "limits": {', DEFAULT_AMPLIFIER, FAILED),
            ("missing", False, DEFAULT_AMPLIFIER, FAILED),
        ]
        for name, device, amplifier, said in cases:
            with self.subTest(name):
                state = state_dir(self.tmp / f"fail-safe-{name.replace(' ', '-')}", current=BOOST, device=device)
                log, (capture,) = self.live(state, [LOUD], amplifier=amplifier)
                lines = log.splitlines()
                if isinstance(said, str):
                    found = any(line.startswith(said) for line in lines)
                else:
                    found = any(said.search(line) for line in lines)
                device_lines = [line for line in lines if line.startswith("speakerd: device")]
                self.assertTrue(found, f"{name}: speakerd did not say {getattr(said, 'pattern', said)!r}; it said "
                                       f"{device_lines}{HINT}")
                self.check_capped(f"{name}, into {amplifier}", capture, floor_dbfs=-30.0)


if __name__ == "__main__":
    unittest.main()
