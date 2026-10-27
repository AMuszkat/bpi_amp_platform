"""speakerd_restart (docs/autonomous-development.md, section 5): speakerd stopped while a stream
plays leaves the amplifier silent and the stream on its sink, and the next speakerd takes over
under the running stream.

This is the tweeter's safety between two runs of speakerd (dsp/juce_apps/speakerd/README.md,
"Whose sink"). Were the `speakerd` sink to go with speakerd, WirePlumber would move the stream to
the default sink, which on a speaker is the amplifier: full range, at full level, tweeter
included. So speakerd creates the sink lingering: it stays in PipeWire when speakerd stops, a
stream playing into it stays there, nothing reads its monitor, and the next speakerd adopts it.

speakerd runs on the left virtual speaker, with no `speakerd` sink before it, the headless
build of loudness forced to its first setting (+12 dB shelf at 120 Hz) on both amplifier
channels. One player streams a 100 Hz tone at -20 dBFS peak into the `speakerd` sink from
before the stop to after the restart.

Pass when:

- speakerd says it created the input sink 'speakerd', and the stream reaches the amplifier with
  the gain of the complete build's render of the same document, within 0.05 dB on both
  channels;
- SIGTERM, while the stream plays, ends speakerd with "stopping" as its last line and status 0;
- the amplifier, recorded for 1 s once speakerd has exited (after its fade-out), is silent: its
  peak is below -100 dBFS on the woofer, and the tweeter is digital silence, every sample 0;
- the stream still plays, and pw-link -l shows it linked to the `speakerd` sink on both
  channels and to nothing else, the amplifier least of all;
- a new speakerd, started under the running stream, says
  `pipewire: input sink 'speakerd' exists: using it` and that it plays, and the amplifier gets
  the render's gain again, within 0.05 dB on both channels.

    python3 -m unittest -v tests.vpair.test_speakerd_restart
"""

import contextlib
import shutil
import tempfile
import unittest
from pathlib import Path

from harness import (RATE, Pair, Player, Speakerd, active, built, graph_document, lv2_uri,
                     peak_dbfs, render, rms_dbfs, state_dir, steady, tone, with_plugin)

import numpy as np

DOCUMENT = graph_document(lv2_uri("loudness_headless"),
                          {"mode": "Force 1", "s0_e0_gain": 12.0, "s0_e0_freq": 120.0})
TONE = tone(100.0, -20.0, 1.0)
STREAM = tone(100.0, -20.0, 20.0)   # longer than the test: the player is stopped at its end
TOLERANCE_DB = 0.05
SILENT_DBFS = -100.0
SILENCE_SECONDS = 1.0
# A recording of the stream: its first part (the recorder's start, a fade-in, the plugin's
# transient) is skipped, the rest is measured, a whole number of 100 Hz periods.
RECORD_SECONDS, SKIP, MEASURED = 0.8, int(0.2 * RATE), int(0.5 * RATE)


def gains(capture):
    """The gain of each amplifier channel for the stream, from a recording of it."""
    part = capture[SKIP:SKIP + MEASURED]
    if len(part) < MEASURED:
        raise AssertionError(f"the recording has {len(capture)} samples, fewer than "
                             f"{SKIP + MEASURED}")
    level = rms_dbfs(TONE)
    return [rms_dbfs(part[:, channel]) - level for channel in (0, 1)]


class Restart(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.headless = built("loudness", "headless")
        complete = built("loudness", "complete")
        built("speakerd")
        cls.tmp = Path(tempfile.mkdtemp(prefix="vpair-restart-"))
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)

        state = state_dir(cls.tmp / "reference", current=with_plugin(DOCUMENT, lv2_uri("loudness")))
        output, log = render(state, TONE, bundles=[complete])
        if "layer current (graph.json)" not in log:
            raise AssertionError(f"the render did not run the document:\n{log}")
        part = steady(active(output))
        cls.reference = rms_dbfs(part[:, 0]) - rms_dbfs(steady(TONE))

        cls.pair = Pair().__enter__()
        cls.addClassCleanup(cls.pair.__exit__, None, None, None)

    def check_gain(self, gain, when):
        summary = (f"{when}: woofer {gain[0]:+.3f} dB, tweeter {gain[1]:+.3f} dB, "
                   f"complete build {self.reference:+.3f} dB")
        print(f"\n  {summary}")
        for channel, value in zip(("woofer", "tweeter"), gain):
            self.assertAlmostEqual(value, self.reference, delta=TOLERANCE_DB,
                                   msg=f"{channel}: {summary}")

    def test_the_stream_stays_on_the_sink_and_the_next_speakerd_adopts_it(self):
        speaker = self.pair["left"]
        state = state_dir(self.tmp / "live", current=DOCUMENT)

        with contextlib.ExitStack() as stack:
            first = stack.enter_context(Speakerd(speaker, state, bundles=[self.headless]))
            self.assertTrue(first.lines_starting("pipewire: created the input sink 'speakerd'"),
                            f"the sink was there before speakerd:\n{first.log()}")
            stream = stack.enter_context(Player(speaker, STREAM, target="speakerd"))
            self.check_gain(gains(speaker.record(RECORD_SECONDS)), "before the stop")

            self.assertEqual(first.stop(), 0, first.log())
            silence = speaker.record(SILENCE_SECONDS)
            links = speaker.links()
            woofer, tweeter = silence[:, 0], silence[:, 1]
            summary = (f"after the stop, {len(silence) / RATE:.2f} s recorded: woofer peak "
                       f"{peak_dbfs(woofer):.1f} dBFS, tweeter {np.count_nonzero(tweeter)} samples "
                       f"not 0, peak {peak_dbfs(tweeter):.1f} dBFS; the stream is linked to "
                       + (", ".join(sorted(i for o, i in links if o.startswith(stream.name + ":")))
                          or "nothing"))
            print(f"\n  {summary}")
            self.assertGreaterEqual(len(silence), int(0.9 * SILENCE_SECONDS * RATE), summary)
            self.assertLess(peak_dbfs(woofer), SILENT_DBFS, f"the amplifier is not silent: {summary}")
            self.assertEqual(np.count_nonzero(tweeter), 0, f"the tweeter is not silent: {summary}")

            self.assertTrue(stream.playing(), f"the stream stopped with speakerd: {summary}")
            stream_links = {(o, i) for o, i in links if o.startswith(stream.name + ":")}
            self.assertEqual(stream_links, {(f"{stream.name}:output_{c}", f"speakerd:playback_{c}")
                                            for c in ("FL", "FR")},
                             f"the stream is not on the speakerd sink alone: {summary}")

            second = stack.enter_context(Speakerd(speaker, state, bundles=[self.headless]))
            self.assertTrue(second.lines_starting("pipewire: input sink 'speakerd' exists: using it"),
                            f"the new speakerd did not adopt the sink:\n{second.log()}")
            self.check_gain(gains(speaker.record(RECORD_SECONDS)), "after the restart")


if __name__ == "__main__":
    unittest.main()
