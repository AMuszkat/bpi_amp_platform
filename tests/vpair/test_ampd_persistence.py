"""ampd_persistence (docs/autonomous-development.md, section 5): after parameter changes and a
speakerd restart, speakerd boots on graph.json with the changed values; graph.lkg.json follows
once a revision has played long enough with no speakerd restart.

Both virtual speakers run speakerd and ampd (the left the hub) on revision 1: loudness_headless
(Force 1, its first shelf at 1 kHz, 0 dB, so flat) between the stream and both amplifier channels.
ampd runs with --lkg-after 4 (its documented option; 60 s on a board), so the scenario takes
seconds. A harness controller drags the shelf's gain over OSC, 25 values a second for 2 s, up to
+9 dB, and sends the last value over the control link.

Then both ampds are stopped (so that nothing gives speakerd the values again) and the left's
speakerd is restarted: it boots on graph.json. Its capture of a 200 Hz tone is compared with
speakerd's own render of that graph.json through the complete build (the reference of the other
speakerd scenarios) and with the render of revision 1 as it was.

Pass when:

- during the drag graph.json is replaced at most 3 times on the hub (at most once a second), and
  1.2 s after the last value both speakers' graph.json hold it (+9 dB) in revision 1;
- the restarted speakerd says it uses layer current (graph.json), revision 1, and its capture's
  200 Hz gain is the render of that graph.json's within 0.05 dB and at least 3 dB from the render
  of revision 1's original values;
- graph.lkg.json: revision 1 is promoted on both speakers once it has played 4 s; after revision
  2 is applied and the left's speakerd restarted 2 s later (ampd running), the right promotes
  revision 2 about 4 s after it was applied and the left not before 4 s after its restart.

It needs step 3's speakerd (the local socket, 0.3.0): with an older one it fails, saying so.

    python3 -m unittest -v tests.vpair.test_ampd_persistence
"""

import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from harness import (Controller, active, SpeakerPair, built, graph_document, lv2_uri, render,
                     require_speakerd_local_link, revision_document, sine_amplitudes_dbfs, state_dir, steady, tone,
                     with_plugin)
from test_graph_edit import chained

FREQ, LEVEL = 200.0, -20.0
PARAMS = {"mode": "Force 1", "s0_e0_freq": 1000.0, "s0_e0_gain": 0.0, "glide": 1.0}
DOCUMENT = revision_document(graph_document(lv2_uri("loudness_headless"), PARAMS), 1)


def gain_200(capture):
    """The 200 Hz gain of a capture or render: its loud part (a capture starts before the player
    and ends after it), without the first and last 0.3 s."""
    return sine_amplitudes_dbfs(steady(active(capture)[:, 0]), [FREQ])[0] - LEVEL


class AmpdPersistence(unittest.TestCase):
    def test_graph_json_and_the_last_known_good(self):
        require_speakerd_local_link(self)
        tmp = Path(tempfile.mkdtemp(prefix="vpair-persist-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        bundles = [built("loudness", "headless")]
        with SpeakerPair(tmp, {"left": DOCUMENT, "right": DOCUMENT}, bundles, ampd_args=["--lkg-after", "4"]) as pair:
            for name in ("left", "right"):
                pair.ampd[name].wait_for("promoted graph.json to graph.lkg.json: revision 1", timeout=10)
            graph = pair.state["left"] / "graph.json"
            with Controller("ctl-persist") as ctl:
                inodes, value = {graph.stat().st_ino}, 0.0
                ctl.gesture(16, "s0_e0_gain", "begin")
                start = time.monotonic()
                while value < 9.0:
                    value = round(value + 0.18, 2)
                    ctl.osc_param(16, "s0_e0_gain", min(value, 9.0))
                    time.sleep(0.04)
                    inodes.add(graph.stat().st_ino)
                ctl.param(16, "s0_e0_gain", 9.0)
                ctl.gesture(16, "s0_e0_gain", "end")
                dragged = time.monotonic() - start
                writes = len(inodes) - 1
                time.sleep(1.2)
            saved = {name: pair.graph(name) for name in ("left", "right")}
            print(f"\n  a drag of {dragged:.1f} s: graph.json replaced {writes} times during it; then "
                  + ", ".join(f"{n} revision {g['revision']}, s0_e0_gain {g['nodes'][0]['params']['s0_e0_gain']}"
                              for n, g in saved.items()))
            self.assertLessEqual(writes, 3, "graph.json was written more than once a second")
            for name, g in saved.items():
                self.assertEqual((g["revision"], g["nodes"][0]["params"]["s0_e0_gain"]), (1, 9.0), name)

            # speakerd boots on graph.json: nothing gives it the values again.
            for name in ("left", "right"):
                pair.ampd[name].stop()
            pair.speakerd["left"].stop()
            sd = pair.start_speakerd("left")
            self.assertTrue(sd.lines_starting("using layer current (graph.json), revision 1"), sd.log())
            x = tone(FREQ, LEVEL, 2.0)
            live = gain_200(pair["left"].play_and_record(x, target="speakerd", record_target="amplifier"))
            booted = json.loads(graph.read_text())
            changed, _ = render(state_dir(tmp / "ref-changed", current=with_plugin(booted, lv2_uri("loudness"))), x,
                                bundles=[built("loudness", "complete")])
            original, _ = render(state_dir(tmp / "ref-original", current=with_plugin(DOCUMENT, lv2_uri("loudness"))), x,
                                 bundles=[built("loudness", "complete")])
            print(f"  restarted on graph.json: 200 Hz gain {live:+.3f} dB; the render of graph.json "
                  f"{gain_200(changed):+.3f} dB, of revision 1 as it was {gain_200(original):+.3f} dB")
            self.assertLess(abs(live - gain_200(changed)), 0.05)
            self.assertGreater(abs(live - gain_200(original)), 3.0)

            # graph.lkg.json: a speakerd restart starts the 4 s again.
            for name in ("left", "right"):
                pair.start_ampd(name)
            pair.ampd["right"].wait_for("hub: connected")
            with Controller("ctl-persist-2") as ctl:
                self.assertEqual(ctl.propose(chained(6.0))["op"], "applied")
            applied = time.monotonic()
            time.sleep(2.0)
            pair.speakerd["left"].stop()
            pair.start_speakerd("left")
            restarted = time.monotonic()
            pair.ampd["right"].wait_for("promoted graph.json to graph.lkg.json: revision 2", timeout=10)
            right_after = time.monotonic() - applied
            pair.ampd["left"].wait_for("promoted graph.json to graph.lkg.json: revision 2", timeout=12)
            left_after = time.monotonic() - restarted
            print(f"  revision 2 promoted on the right {right_after:.2f} s after it was applied, on the left "
                  f"{left_after:.2f} s after its speakerd restarted")
            self.assertGreaterEqual(right_after, 3.9)
            self.assertLess(right_after, 6.5)
            self.assertGreaterEqual(left_after, 3.9)
            for name in ("left", "right"):
                lkg = json.loads((pair.state[name] / "graph.lkg.json").read_text())
                self.assertEqual(lkg["revision"], 2, name)


if __name__ == "__main__":
    unittest.main()
