"""two_controllers (docs/autonomous-development.md, section 5): changes from two harness clients
interleave and both see the same final state, the hub's; one that connects mid-drag sees the
gesture.

Two controllers (harness.Controller: the control link and an OSC socket) drag two parameters of
the hub's revision at once, interleaved: each a gesture begin, values over OSC at 50 a second, its
last value over the control link (as docs/protocol.md asks: UDP may lose it), and an end; then
both drag the same parameter, one after the other's last value. The speakers are the hub and an
agent, each ampd on a stand-in speakerd (tests/vpair/stand_in_speakerd.py): what this scenario
checks is the hub's state, which speakerd plays no part in, so it runs before step 3's speakerd
is merged.

Pass when:

- both controllers end with the same value of every parameter, and it is the hub's: the state a
  third controller gets on connecting, the right speaker's graph.json (ampd's) and the last value
  each stand-in speakerd was given;
- a controller that connects while the first drags finds the gesture, with the dragger's origin,
  in its state, and gets the end when the drag ends.

    python3 -m unittest -v tests.vpair.test_two_controllers
"""

import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

from harness import Controller, StandInPair, graph_document, lv2_uri, revision_document

PARAMS = {"mode": "Force 1", "s0_e0_gain": 0.0, "s0_e1_gain": 0.0, "s0_e0_freq": 100.0}
DOCUMENT = revision_document(graph_document(lv2_uri("loudness_headless"), PARAMS), 1)


def drag(controller, pid, values, rate=50.0):
    """A knob moved: begin, the values over OSC at `rate`, the last over the control link, end."""
    controller.gesture(16, pid, "begin")
    for value in values:
        controller.osc_param(16, pid, value)
        time.sleep(1.0 / rate)
    controller.param(16, pid, values[-1])
    controller.gesture(16, pid, "end")


class TwoControllers(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vpair-two-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_interleaved_changes_end_in_the_hubs_state(self):
        with StandInPair(self.tmp, {"left": DOCUMENT, "right": DOCUMENT}) as pair, \
                Controller("ctl-a") as a, Controller("ctl-b") as b:
            # Different parameters at once, then the same one.
            ramps = {"a": [round(-12 + 0.5 * i, 2) for i in range(40)], "b": [round(20 * 1.05 ** i, 2) for i in range(40)]}
            threads = [threading.Thread(target=drag, args=(a, "s0_e0_gain", ramps["a"])),
                       threading.Thread(target=drag, args=(b, "s0_e0_freq", ramps["b"]))]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            for i in range(3):
                drag(a, "s0_e1_gain", [i * 1.0, i * 1.0 + 0.5])
                drag(b, "s0_e1_gain", [-i * 1.0, -i * 1.0 - 0.5])
            final = {(16, "s0_e0_gain"): ramps["a"][-1], (16, "s0_e0_freq"): ramps["b"][-1], (16, "s0_e1_gain"): -2.5}

            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not (a.values().items() >= final.items() and b.values() == a.values()):
                time.sleep(0.05)
            time.sleep(1.2)       # graph.json follows within a second
            with Controller("ctl-c") as c:
                hub = {(16, k): v for k, v in c.state["document"]["nodes"][0]["params"].items()}
            right = {(16, k): v for k, v in json.loads((pair.state["right"] / "graph.json").read_text())["nodes"][0]["params"].items()}
            speakerd = {}
            for name in ("left", "right"):
                last = {}
                for e in pair.sd[name].events("param", "in"):
                    last[(e["node"], e["id"])] = e["value"]
                speakerd[name] = last
            print(f"\n  hub: {sorted(hub.items())}\n  ctl-a: {sorted(a.values().items())}\n  ctl-b: {sorted(b.values().items())}")
            for key, value in final.items():
                self.assertEqual(hub[key], value, f"the hub's {key}")
            self.assertEqual(a.values(), hub, "ctl-a does not see the hub's state")
            self.assertEqual(b.values(), hub, "ctl-b does not see the hub's state")
            self.assertEqual(right, hub, "the right speaker's graph.json is not the hub's state")
            for name in ("left", "right"):
                for key, value in final.items():
                    self.assertEqual(speakerd[name][key], value, f"{name}'s speakerd was last given another {key}")

    def test_a_controller_connecting_mid_drag_sees_the_gesture(self):
        with StandInPair(self.tmp, {"left": DOCUMENT, "right": DOCUMENT}), Controller("ctl-a") as a:
            a.gesture(16, "s0_e0_gain", "begin")
            for value in (1.0, 2.0, 3.0):
                a.osc_param(16, "s0_e0_gain", value)
                time.sleep(0.02)
            with Controller("ctl-b") as b:
                print(f"\n  ctl-b connected mid-drag: gestures {b.state['gestures']}")
                self.assertEqual([(g["node"], g["id"], g["origin"]) for g in b.state["gestures"]],
                                 [(16, "s0_e0_gain", "ctl-a")])
                a.osc_param(16, "s0_e0_gain", 4.0)
                b.wait(lambda m: m["op"] == "param" and m["value"] == 4.0, what="the drag's next value")
                a.param(16, "s0_e0_gain", 4.0)
                a.gesture(16, "s0_e0_gain", "end")
                end = b.wait(lambda m: m["op"] == "gesture" and m["state"] == "end", what="the end of the drag")
                self.assertEqual(end["origin"], "ctl-a")
                self.assertEqual(b.gestures, {})


if __name__ == "__main__":
    unittest.main()
