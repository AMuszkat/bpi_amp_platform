"""speakerd_second_instance (docs/autonomous-development.md, section 5): a second speakerd on
the same PipeWire refuses to run, and the first one plays on unaffected.

Two speakerd on one PipeWire with the same node name would both read the `speakerd` sink and
both feed the amplifier: the speaker would play twice as loud (+6 dB), on the tweeter too. So
a speakerd that finds the filter node of another (`<name>-dsp`) already there says so and
stops (dsp/juce_apps/speakerd/README.md, Log lines and Exit status).

speakerd A runs on the left virtual speaker with the default node name and the headless build
of loudness forced to its first setting (+12 dB shelf at 120 Hz) on both amplifier channels.
speakerd B is then started on the same speaker, with the same document.

Pass when:

- B says `pipewire: another speakerd already runs on this PipeWire (node 'speakerd-dsp'
  exists)`, then `stopping`, as its last line, and exits with status 3;
- A, still running, gives a 100 Hz tone played into the `speakerd` sink the gain of the
  complete build's render of the same document within 0.05 dB on both amplifier channels (and
  not 6 dB more, which would be two of them).

    python3 -m unittest -v tests.vpair.test_speakerd_second_instance
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from harness import (Pair, Speakerd, active, built, graph_document, lv2_uri, render, rms_dbfs,
                     state_dir, steady, tone, with_plugin)

DOCUMENT = graph_document(lv2_uri("loudness_headless"),
                          {"mode": "Force 1", "s0_e0_gain": 12.0, "s0_e0_freq": 120.0})
TONE = tone(100.0, -20.0, 1.0)
TOLERANCE_DB = 0.05
REFUSAL = "pipewire: another speakerd already runs on this PipeWire (node 'speakerd-dsp' exists)"
REFUSED_STATUS = 3


def gains(output, source=TONE):
    part = steady(active(output))
    level = rms_dbfs(steady(source))
    return [rms_dbfs(part[:, channel]) - level for channel in (0, 1)]


class SecondInstance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.headless = built("loudness", "headless")
        complete = built("loudness", "complete")
        built("speakerd")
        cls.tmp = Path(tempfile.mkdtemp(prefix="vpair-second-instance-"))
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)

        state = state_dir(cls.tmp / "reference", current=with_plugin(DOCUMENT, lv2_uri("loudness")))
        output, log = render(state, TONE, bundles=[complete])
        if "layer current (graph.json)" not in log:
            raise AssertionError(f"the render did not run the document:\n{log}")
        cls.reference = gains(output)[0]

        cls.pair = Pair().__enter__()
        cls.addClassCleanup(cls.pair.__exit__, None, None, None)

    def test_a_second_speakerd_refuses_and_the_first_plays_on(self):
        speaker = self.pair["left"]
        with Speakerd(speaker, state_dir(self.tmp / "a", current=DOCUMENT),
                      bundles=[self.headless]) as a:
            with Speakerd(speaker, state_dir(self.tmp / "b", current=DOCUMENT),
                          bundles=[self.headless], wait_for=None,
                          exit_status=REFUSED_STATUS) as b:
                # B refuses, or (wrongly) plays beside A: either is said soon after it starts.
                said = b.wait_for((REFUSAL, "playing: "))
                status = b.wait_exit() if said == "speakerd: " + REFUSAL else None

                live = gains(speaker.play_and_record(TONE, target="speakerd",
                                                     record_target="amplifier"))
                summary = (f"B said '{said}' and "
                           + (f"exited with status {status}" if status is not None else "still runs")
                           + f"; A: woofer {live[0]:+.3f} dB, tweeter {live[1]:+.3f} dB, "
                           f"complete build {self.reference:+.3f} dB")
                print(f"\n  {summary}")

                self.assertEqual(said, "speakerd: " + REFUSAL,
                                 f"the second speakerd did not refuse: {summary}\n"
                                 f"B's output:\n{b.log()}")
                self.assertEqual(status, REFUSED_STATUS, f"{summary}\nB's output:\n{b.log()}")
                # Leaving B's block checks that "stopping" was its last line, said once.
            for channel, gain in zip(("woofer", "tweeter"), live):
                self.assertAlmostEqual(gain, self.reference, delta=TOLERANCE_DB,
                                       msg=f"{channel}: {summary}")
            self.assertEqual(a.lines_starting("unlinked from "), [],
                             f"A lost a link while B ran:\n{a.log()}")


if __name__ == "__main__":
    unittest.main()
