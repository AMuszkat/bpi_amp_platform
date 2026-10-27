"""speakerd falls back through its state layers (docs/remote-plugin-host.md, section 8, rule 1).

Each layer holds loudness forced to its first setting with its own shelf gain at 120 Hz, so
each sounds different at 100 Hz: graph.json +12 dB (revision 3), graph.lkg.json +6 dB
(revision 2), factory.json -6 dB (revision 1). A layer is broken by being half written (cut
off mid-file, as by a power cut) or by naming a plugin that is not installed. speakerd runs on
the left virtual speaker with the headless build of loudness.

Pass when, for a broken graph.json, speakerd says that graph.json FAILED and that it plays
graph.lkg.json, and with graph.json and graph.lkg.json both broken, that both FAILED and it
plays factory.json; and in each case the gain of a 100 Hz tone, on both amplifier channels,
is within 0.05 dB of the render of the layer it says it plays (complete build) and at least
1 dB away from every other layer's and from passthrough's (-20 dB): the layer is identified by
its sound as well as by the log.

    python3 -m unittest -v tests.vpair.test_speakerd_state_layers
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from harness import (Pair, Speakerd, active, built, graph_document, lv2_uri, render, rms_dbfs,
                     state_dir, steady, tone, with_plugin)


def layer(gain_db, revision):
    return graph_document(lv2_uri("loudness_headless"),
                          {"mode": "Force 1", "s0_e0_gain": gain_db, "s0_e0_freq": 120.0},
                          revision=revision)


CURRENT, LKG, FACTORY = layer(12.0, 3), layer(6.0, 2), layer(-6.0, 1)
NAMES = {"current": "current (graph.json), revision 3",
         "last-known-good": "last-known-good (graph.lkg.json), revision 2",
         "factory": "factory (factory.json), revision 1"}
FILES = {"current": "graph.json", "last-known-good": "graph.lkg.json", "factory": "factory.json"}


def half_written(document):
    """The document cut off a third of the way through."""
    text = json.dumps(document, indent=2)
    return text[:len(text) // 3]


def not_installed(document):
    return with_plugin(document, lv2_uri("loudness_not_installed"))


TONE = tone(100.0, -20.0, 1.0)
PASSTHROUGH_GAIN_DB = -20.0
TOLERANCE_DB = 0.05
DISTINCT_DB = 1.0


def gains(output, source=TONE):
    """The steady gain of each amplifier channel, in dB, for a tone that starts and ends in
    `output` at the level of its loud part."""
    part = steady(active(output))
    level = rms_dbfs(steady(source))
    return [rms_dbfs(part[:, channel]) - level for channel in (0, 1)]


class StateLayers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.headless = built("loudness", "headless")
        complete = built("loudness", "complete")
        built("speakerd")
        cls.tmp = Path(tempfile.mkdtemp(prefix="vpair-state-layers-"))
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)

        # What each layer sounds like: its render with the complete build.
        cls.reference = {}
        for name, document in (("current", CURRENT), ("last-known-good", LKG), ("factory", FACTORY)):
            state = state_dir(cls.tmp / f"reference-{name}",
                              current=with_plugin(document, lv2_uri("loudness")))
            output, log = render(state, TONE, bundles=[complete])
            if "layer current (graph.json)" not in log:
                raise AssertionError(f"the render of {name} did not run its document:\n{log}")
            cls.reference[name] = gains(output)[0]

        cls.pair = Pair().__enter__()
        cls.addClassCleanup(cls.pair.__exit__, None, None, None)

    def check(self, case, expected, failed, **layers):
        speaker = self.pair["left"]
        state = state_dir(self.tmp / case, **layers)
        with Speakerd(speaker, state, bundles=[self.headless]) as sd:
            for name in failed:
                self.assertTrue(sd.lines_starting(f"layer {name} ({FILES[name]}): FAILED: "),
                                f"speakerd did not say that {FILES[name]} failed:\n{sd.log()}")
            self.assertTrue(sd.lines_starting(f"using layer {NAMES[expected]}"),
                            f"speakerd does not use {FILES[expected]}:\n{sd.log()}")
            self.assertTrue(sd.lines_starting(f"playing: layer {NAMES[expected]}, "), sd.log())
            capture = speaker.play_and_record(TONE, target="speakerd", record_target="amplifier")
            reasons = sd.lines_starting("layer ")

        live = gains(capture)
        others = {name: gain for name, gain in self.reference.items() if name != expected}
        others["passthrough"] = PASSTHROUGH_GAIN_DB
        summary = (f"live: woofer {live[0]:+.3f} dB, tweeter {live[1]:+.3f} dB; renders: "
                   + ", ".join(f"{name} {gain:+.3f} dB" for name, gain in self.reference.items())
                   + f"; passthrough {PASSTHROUGH_GAIN_DB:+.3f} dB")
        print(f"\n  {case}: plays {expected}; {summary}\n    " + "\n    ".join(reasons))

        for channel, gain in zip(("woofer", "tweeter"), live):
            self.assertAlmostEqual(gain, self.reference[expected], delta=TOLERANCE_DB,
                                   msg=f"{channel} does not sound like {expected}: {summary}")
            for name, other in others.items():
                self.assertGreater(abs(gain - other), DISTINCT_DB,
                                   f"{channel} sounds like {name} too: {summary}")

    def test_half_written_current_plays_last_known_good(self):
        self.check("half-written-current", "last-known-good", ["current"],
                   current=half_written(CURRENT), lkg=LKG, factory=FACTORY)

    def test_current_with_a_missing_plugin_plays_last_known_good(self):
        self.check("missing-plugin-current", "last-known-good", ["current"],
                   current=not_installed(CURRENT), lkg=LKG, factory=FACTORY)

    def test_current_and_last_known_good_broken_plays_factory(self):
        self.check("both-broken", "factory", ["current", "last-known-good"],
                   current=half_written(CURRENT), lkg=not_installed(LKG), factory=FACTORY)


if __name__ == "__main__":
    unittest.main()
