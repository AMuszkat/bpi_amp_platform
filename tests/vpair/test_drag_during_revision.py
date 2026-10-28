"""drag_during_revision (docs/autonomous-development.md, section 5): a knob moved while another
controller's revision is being applied keeps its last value, in every graph.json and every
controller, and after a speakerd restart.

Both virtual speakers run speakerd and ampd (the left the hub) on revision 1: loudness_headless
(node 16: Force 1, its first shelf at 1 kHz, 0 dB) between the stream and the amplifier.
Controller B proposes revision 2 (a second, flat loudness after node 16, which keeps its 0 dB).
To make the apply last, the right's ampd is held (SIGSTOP) for 1.5 s while it applies; meanwhile
controller A drags node 16's gain over OSC and sends its last value, +6 dB, over the control link,
as a knob does. speakerd carries such a value over into the new graph (docs/protocol.md,
clarification 18), so the speakers play +6 dB; ampd must keep it too, or graph.json, the
controllers and the next speakerd restart would bring the 0 dB of B's document back.

Pass when:

- revision 2 is applied, and the graph the hub sends the controllers, both speakers' graph.json
  (revision 2), controllers A and B and a controller connecting afterwards all have node 16's gain
  at +6 dB;
- after the left's speakerd restarts (ampd giving it the values again), its 200 Hz gain is
  speakerd's render of the left's graph.json (with +6 dB) within 0.05 dB, and at least 3 dB from
  the render of revision 2's document as B sent it (0 dB).

It needs step 3's speakerd (the local socket, 0.3.0): with an older one it fails, saying so.

    python3 -m unittest -v tests.vpair.test_drag_during_revision
"""

import shutil
import signal
import tempfile
import threading
import time
import unittest
from pathlib import Path

from harness import (Controller, SpeakerPair, active, built, graph_document, lv2_uri, render,
                     require_speakerd_local_link, revision_document, sine_amplitudes_dbfs, state_dir, steady, tone,
                     with_plugin)
from test_graph_edit import chained

FREQ, LEVEL = 200.0, -20.0
FLAT = {"mode": "Force 1", "s0_e0_freq": 1000.0, "s0_e0_gain": 0.0, "glide": 1.0}
DOCUMENT = revision_document(graph_document(lv2_uri("loudness_headless"), FLAT), 1)


def gain_200(capture):
    return sine_amplitudes_dbfs(steady(active(capture)[:, 0]), [FREQ])[0] - LEVEL


class DragDuringRevision(unittest.TestCase):
    def test_the_last_value_of_a_drag_during_an_apply(self):
        require_speakerd_local_link(self)
        tmp = Path(tempfile.mkdtemp(prefix="vpair-drag-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        with SpeakerPair(tmp, {"left": DOCUMENT, "right": DOCUMENT}, [built("loudness", "headless")]) as pair, \
                Controller("ctl-a") as a, Controller("ctl-b") as b:
            answer = {}
            proposal = threading.Thread(target=lambda: answer.update(b.propose(chained(0.0), timeout=30)))
            pair.ampd["right"].signal(signal.SIGSTOP)
            try:
                proposal.start()
                pair.ampd["left"].wait_for("revision 2 from ctl-b: applying on")
                a.gesture(16, "s0_e0_gain", "begin")
                for value in (1.0, 2.0, 3.0, 4.0, 5.0):
                    a.osc_param(16, "s0_e0_gain", value)
                    time.sleep(0.04)
                a.param(16, "s0_e0_gain", 6.0)
                a.gesture(16, "s0_e0_gain", "end")
                time.sleep(1.5)
            finally:
                pair.ampd["right"].signal(signal.SIGCONT)
            proposal.join(timeout=30)
            self.assertEqual(answer.get("op"), "applied", answer)
            graph = b.wait(lambda m: m["op"] == "graph" and m["revision"] == 2, what="the graph of revision 2")
            kept = graph["document"]["nodes"][0]["params"]["s0_e0_gain"]
            time.sleep(1.2)
            saved = {name: pair.graph(name) for name in ("left", "right")}
            with Controller("ctl-c") as c:
                late = c.values()[(16, "s0_e0_gain")]
            print(f"\n  revision 2 applied with the right held 1.5 s: the graph sent {kept}, graph.json "
                  + ", ".join(f"{n} revision {g['revision']} {g['nodes'][0]['params']['s0_e0_gain']}" for n, g in saved.items())
                  + f"; ctl-a {a.values()[(16, 's0_e0_gain')]}, ctl-b {b.values()[(16, 's0_e0_gain')]}, ctl-c {late}")
            self.assertEqual(kept, 6.0)
            for name, g in saved.items():
                self.assertEqual((g["revision"], g["nodes"][0]["params"]["s0_e0_gain"]), (2, 6.0), name)
            for who, value in (("ctl-a", a.values()[(16, "s0_e0_gain")]), ("ctl-b", b.values()[(16, "s0_e0_gain")]),
                               ("ctl-c", late)):
                self.assertEqual(value, 6.0, who)

            # A speakerd restart under ampd: the value it is given again is +6 dB.
            mark = pair.ampd["left"].mark()
            pair.speakerd["left"].stop()
            pair.start_speakerd("left")
            pair.ampd["left"].wait_for("speakerd: connected", after=mark)
            time.sleep(0.5)
            x = tone(FREQ, LEVEL, 2.0)
            live = gain_200(pair["left"].play_and_record(x, target="speakerd", record_target="amplifier"))
            kept_render, _ = render(state_dir(tmp / "ref-kept", current=with_plugin(pair.graph("left"), lv2_uri("loudness"))),
                                    x, bundles=[built("loudness", "complete")])
            sent = dict(chained(0.0), revision=2)
            sent_render, _ = render(state_dir(tmp / "ref-sent", current=with_plugin(sent, lv2_uri("loudness"))), x,
                                    bundles=[built("loudness", "complete")])
            print(f"  after the left's speakerd restarted: 200 Hz gain {live:+.3f} dB; the render of its graph.json "
                  f"{gain_200(kept_render):+.3f} dB, of B's document {gain_200(sent_render):+.3f} dB")
            self.assertLess(abs(live - gain_200(kept_render)), 0.05)
            self.assertGreater(abs(live - gain_200(sent_render)), 3.0)


if __name__ == "__main__":
    unittest.main()
