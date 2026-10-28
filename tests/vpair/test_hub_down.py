"""hub_down (docs/autonomous-development.md, section 5): the right speaker keeps playing its
revision when the left is stopped, and switches to the L+R mix if configured.

Both virtual speakers run speakerd and ampd (the left the hub). A harness controller makes revision
2 current (a second loudness_headless in the chain, +12 dB at 200 Hz), so the right plays a
revision it got from the hub, not its boot layer. The right plays a steady 200 Hz tone at -20 dBFS,
its amplifier recorded as floats; then the whole left speaker stops: its ampd killed (SIGKILL),
its speakerd stopped, its PipeWire, WirePlumber and D-Bus taken down.

Pass when:

- the right's level in the 2 s after the left stopped is its level before within 0.05 dB, no 10 ms
  block of its capture from 0.5 s before the stop to the end is more than 1 dB under that level,
  and none is exact zeros;
- the right's ampd says it lost the hub and keeps running, its speakerd keeps running (no
  `stopping`), and its graph.json and ampd's status still say revision 2.

The L+R mix on partner loss (`partner-lost = sum` in device.json) is planned, not in device.json
yet (speakerd rejects keys it does not know), so that half is skipped, saying so.

It needs step 3's speakerd (the local socket, 0.3.0): with an older one it fails, saying so.

    python3 -m unittest -v tests.vpair.test_hub_down
"""

import shutil
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from harness import (RATE, Controller, Player, SpeakerPair, TimedRecorder, block_levels, built,
                     require_speakerd_local_link, rms_dbfs, tone)
from test_graph_edit import DOCUMENT, chained

FREQ, LEVEL = 200.0, -20.0


class HubDown(unittest.TestCase):
    def test_the_right_keeps_playing_its_revision(self):
        require_speakerd_local_link(self)
        tmp = Path(tempfile.mkdtemp(prefix="vpair-hubdown-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        with SpeakerPair(tmp, {"left": DOCUMENT, "right": DOCUMENT}, [built("loudness", "headless")]) as pair:
            with Controller("ctl-hubdown") as ctl:
                self.assertEqual(ctl.propose(chained(12.0))["op"], "applied")
            with Player(pair["right"], tone(FREQ, LEVEL, 30.0), target="speakerd"):
                recorder = TimedRecorder(pair["right"]).start()
                try:
                    time.sleep(2.0)
                    stopped = time.monotonic()
                    pair.ampd["left"].kill()
                    pair.speakerd["left"].stop()
                    pair.pair.down("left")
                    down = time.monotonic()
                    time.sleep(3.0)
                    end = time.monotonic()
                finally:
                    x, t0 = recorder.stop()
            index = lambda t: int((t - t0) * RATE)
            before = rms_dbfs(x[index(stopped - 1.5):index(stopped - 0.1), 0])
            after = rms_dbfs(x[index(down + 0.2):index(end - 0.2), 0])
            watched = x[index(stopped - 0.5):index(end - 0.2), 0]
            blocks = block_levels(watched, 0.01)
            zeros = int(np.sum(np.all(watched[:len(watched) // 480 * 480].reshape(-1, 480) == 0, axis=1)))
            print(f"\n  the left stopped in {down - stopped:.2f} s; the right before {before:.3f}, after {after:.3f} dBFS, "
                  f"quietest 10 ms block {np.min(blocks):.2f} dBFS, blocks of zeros {zeros}")
            self.assertLess(abs(after - before), 0.05, "the right's level changed when the left stopped")
            self.assertGreater(float(np.min(blocks)), before - 1.0, "the right's capture dropped when the left stopped")
            self.assertEqual(zeros, 0, "the right's capture has blocks of zeros")

            right = pair.ampd["right"]
            right.wait_for("hub: lost the connection")
            self.assertTrue(right.running(), right.log())
            self.assertTrue(pair.speakerd["right"].process.poll() is None)
            self.assertFalse(pair.speakerd["right"].lines_starting("stopping"))
            self.assertEqual(pair.graph("right")["revision"], 2)
            status = right.status()
            self.assertEqual((status["revision"], status["speakerd"]["connected"], status["hub"]["connected"]), (2, True, False))

    def test_the_right_plays_left_and_right_when_its_partner_is_lost(self):
        self.skipTest("the L+R mix on partner loss (partner-lost = sum) is planned, not in device.json yet: "
                      "speakerd rejects keys it does not know (docs/remote-plugin-host.md, section 8)")


if __name__ == "__main__":
    unittest.main()
