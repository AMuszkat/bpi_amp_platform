#!/usr/bin/env python3
"""The controller's bridge against the fake hub (fake_hub.py): connection and state, parameters
and their echoes, gestures over OSC at 50 Hz, proposals applied and rejected (undone), revisions
from other controllers, monitor subscriptions. Each test runs the controller in its test mode
(--script) and checks both what it printed and what the hub received. Standard library only.

    python3 -m unittest discover -s dsp/juce_apps/controller/tests -v

The controller is build/controller/controller_artefacts/Release/controller at the repository
root (tools/build.sh controller), or $CONTROLLER. The hub listens on 127.0.3.1 and the right
speaker's monitor link on 127.0.3.2 (any 127.x address is the loopback).
"""

import copy
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest

from fake_hub import FakeHub, LOUDNESS_URI, default_document

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
CONTROLLER = os.environ.get("CONTROLLER", os.path.join(ROOT, "build", "controller", "controller_artefacts", "Release", "controller"))
LEFT, RIGHT = "127.0.3.1", "127.0.3.2"
CROSSOVER_URI = "https://github.com/AMuszkat/bpi_amp_dsp_tuning/crossover_headless"


def report(name, **numbers):
    """The numbers of a test, on one line, for the run's log."""
    print("\n    [%s] %s" % (name, ", ".join("%s=%s" % kv for kv in numbers.items())), end=" ")


class ControllerTest(unittest.TestCase):
    hub = None

    def setUp(self):
        if not os.access(CONTROLLER, os.X_OK):
            self.skipTest("no controller at %s: tools/build.sh controller" % CONTROLLER)

    def tearDown(self):
        if self.hub is not None:
            self.hub.stop()
            self.hub = None

    def start_hub(self, **kwargs):
        self.hub = FakeHub(LEFT, RIGHT, **kwargs).start()
        return self.hub

    def run_script(self, script, extra=(), during=None, timeout=60):
        """Runs the controller's test mode; returns (exit status, the steps it printed, stderr)."""
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write(script)
            path = f.name
        args = [CONTROLLER, "--hub", LEFT, "--name", "ctl-test", "--script", path, "--trace", *extra]
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        thread = None
        if during is not None:
            thread = threading.Thread(target=during, daemon=True)
            thread.start()
        try:
            out, err = process.communicate(timeout=timeout)
        finally:
            if process.poll() is None:
                process.kill()
            os.unlink(path)
        if thread is not None:
            thread.join(10)
        steps = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
        return process.returncode, steps, err

    def assertPassed(self, code, steps, err):
        failed = [s for s in steps if s.get("ok") is False]
        self.assertEqual(code, 0, "controller failed: %s\n%s" % (failed, err[-3000:]))
        self.assertEqual(steps[-1].get("result"), "pass")

    def wait_for_controller(self, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.hub.lock:
                if self.hub.controllers:
                    return True
            time.sleep(0.02)
        return False

    @staticmethod
    def step(steps, cmd, index=0):
        return [s for s in steps if s.get("cmd") == cmd][index]

    # ---------------------------------------------------------------------------------------
    def test_01_connect_and_state(self):
        """hello, then the canvas is the hub's state: nodes, values, connections, speakers."""
        hub = self.start_hub()
        code, steps, err = self.run_script("wait-synced\nstate\n")
        self.assertPassed(code, steps, err)

        hello = hub.received_ops("control", "hello")
        self.assertEqual(len(hello), 1)
        hello = hello[0][0]
        self.assertEqual((hello["role"], hello["name"], hello["protocol"]), ("controller", "ctl-test", 1))
        self.assertGreater(hello["osc_port"], 0)

        state = self.step(steps, "state")["state"]
        self.assertTrue(state["synced"])
        self.assertEqual(state["revision"], 7)
        node = [n for n in state["nodes"] if n["uid"] == 16][0]
        self.assertEqual(node["kind"], "control")
        self.assertEqual(node["identifier"], LOUDNESS_URI)
        self.assertEqual(node["params"]["s0_e0_gain"], 12.0)
        self.assertEqual(node["params"]["mode"], 1)
        self.assertEqual(sorted(map(tuple, state["connections"])), [(1, 0, 16, 0), (16, 0, 2, 0)])
        self.assertEqual([(s["name"], s["role"], s["online"]) for s in state["speakers"]],
                         [("left", "left", True), ("right", "right", True)])
        # Nothing the controller had is pushed: no param, no graph before or after the state.
        self.assertEqual(hub.received_ops("control", "graph"), [])
        self.assertEqual(hub.received_ops("control", "param"), [])
        report("state", revision=state["revision"], nodes=len(state["nodes"]), parameters=len(node["params"]),
               connections=len(state["connections"]), speakers=len(state["speakers"]))

    def test_02_parameter_without_gesture_goes_over_control(self):
        hub = self.start_hub()
        code, steps, err = self.run_script("wait-synced\nset 16 s0_e0_gain 4.5\nwait-echo 16 s0_e0_gain 4.5\n"
                                           "set 16 mode \"Force 3\"\nwait-echo 16 mode 3\nsleep 200\n")
        self.assertPassed(code, steps, err)

        params = [m for m, _ in hub.received_ops("control", "param")]
        self.assertEqual(params[0], {"op": "param", "node": 16, "id": "s0_e0_gain", "value": 4.5, "seq": 0, "origin": "ctl-test"})
        self.assertEqual(params[1]["id"], "mode")
        self.assertEqual(params[1]["value"], 3)
        self.assertEqual(hub.received_ops("knobs"), [], "a change without a gesture goes over the control link only")
        echo = self.step(steps, "wait-echo")
        self.assertEqual(echo["seq"], 1001)
        report("set", control_params=len(params), osc_params=0, echo_seq=echo["seq"], echo_ms=echo["ms"])

    def test_03_gesture_over_osc_at_50_hz_and_last_value_over_control(self):
        hub = self.start_hub()
        hub.echo_delay = 0.08   # our own echoes come back late, while later values are already out
        code, steps, err = self.run_script("wait-synced\ndrag 16 s0_e0_gain 0 6 120 1200\nsleep 400\n"
                                           "wait-param 16 s0_e0_gain 6 0.1\n")
        self.assertPassed(code, steps, err)

        osc = [(m, t) for m, t in hub.received_ops("knobs", "/bpi/param")]
        times = [t for _, t in osc]
        self.assertTrue(all(m[1] == ",isfis" for m, _ in osc))
        self.assertTrue(all(m[2][3] == 0 and m[2][4] == "ctl-test" for m, _ in osc))
        # At most 50 a second for one parameter: count the densest second, and the shortest gap.
        densest = max(sum(1 for u in times if t <= u < t + 1.0) for t in times)
        gaps = [b - a for a, b in zip(times, times[1:])]
        # (Arrival times at the hub, not send times: two datagrams sent 20 ms apart can be read
        # 2 ms apart when the hub's thread was late, so the shortest gap is reported, not checked.)
        self.assertLessEqual(densest, 51)
        self.assertGreaterEqual(len(osc), 40, "the drag should have sent most of its 1.2 s at 50 Hz")

        control = [m for m, _ in hub.received_ops("control") if m.get("op") in ("param", "gesture")]
        ops = [(m["op"], m.get("state"), m.get("value")) for m in control]
        self.assertEqual(ops[0], ("gesture", "begin", None))
        self.assertEqual(ops[-2], ("param", None, 6.0), "the gesture's last value over the control link")
        self.assertEqual(ops[-1], ("gesture", "end", None))
        self.assertEqual(len(ops), 3)
        report("drag", values=120, duration_s=1.2, osc_sent=len(osc), densest_second=densest,
               min_gap_ms=round(min(gaps) * 1000, 1), control_lines=len(ops), final=6.0)

    def test_04_echo_of_another_controller_is_applied(self):
        hub = self.start_hub()

        def other():
            self.wait_for_controller()
            time.sleep(0.3)
            hub.change_param(16, "s0_e0_gain", -3.0)
            time.sleep(0.1)
            hub.change_param(16, "mode", "Force 2")

        t0 = time.monotonic()
        code, steps, err = self.run_script("wait-synced\nwait-param 16 s0_e0_gain -3\nwait-param 16 mode 2\n", during=other)
        self.assertPassed(code, steps, err)
        self.assertEqual(hub.received_ops("control", "param"), [], "an echo is applied, never sent back")
        report("echo", applied=2, origin="ctl-other", wait_ms=self.step(steps, "wait-param")["ms"],
               run_s=round(time.monotonic() - t0, 2))

    def test_05_proposals_applied(self):
        hub = self.start_hub()
        code, steps, err = self.run_script(
            "wait-synced\n"
            "add loudness 0.6 0.6\nexpect-applied\nexpect-revision 8\n"
            "connect-pins 16:0 $new:0\nexpect-applied\nexpect-revision 9\n"
            "bypass $new on\nexpect-applied\nexpect-revision 10\n"
            "disconnect 16:0 2:0\nexpect-applied\nexpect-revision 11\nstate\n")
        self.assertPassed(code, steps, err)

        proposals = [m for m, _ in hub.received_ops("control", "graph")]
        self.assertEqual([(p["revision"], p["base"]) for p in proposals], [(8, 7), (9, 8), (10, 9), (11, 10)])
        self.assertTrue(all(p["origin"] == "ctl-test" and p["time"].endswith("Z") for p in proposals))
        doc = hub.document
        self.assertEqual(doc["revision"], 11)
        new = [n for n in doc["nodes"] if n["uid"] == 17][0]
        self.assertEqual(new["plugin"], {"format": "LV2", "identifier": LOUDNESS_URI})
        self.assertEqual(len(new["params"]), 35)   # every parameter of loudness: 4 x 7 per setting, and 7 more
        self.assertFalse(any(k.startswith("mon_") for k in new["params"]))
        self.assertTrue(new["bypassed"])
        connections = sorted((c["src"]["node"], c["src"]["channel"], c["dst"]["node"], c["dst"]["channel"]) for c in doc["connections"])
        self.assertEqual(connections, [(1, 0, 16, 0), (16, 0, 17, 0)])
        old = [n for n in doc["nodes"] if n["uid"] == 16][0]
        self.assertEqual(old["params"]["s0_e0_gain"], 12.0, "values travel with the revision")
        report("applied", proposals=len(proposals), revision=doc["revision"], new_node_params=len(new["params"]),
               connections=len(connections))

    def test_06_proposal_rejected_is_undone(self):
        hub = self.start_hub()
        hub.reject_next = "right: failed: no plugin loudness_headless 9.9"
        code, steps, err = self.run_script(
            "wait-synced\nset 16 s0_e0_gain 3\nwait-echo 16 s0_e0_gain 3\n"
            "add loudness\nexpect-rejected no plugin\nexpect-no-node $new\nexpect-revision 7\n"
            "wait-param 16 s0_e0_gain 3 0.1\n"   # the undo keeps the values echoed since the state
            "connect-pins 1:2 16:0\nexpect-applied\nexpect-revision 8\nstate\n")
        self.assertPassed(code, steps, err)
        rejected = self.step(steps, "expect-rejected")
        self.assertIn("rejected 8: right: failed", rejected["outcome"])
        self.assertIn("undone", rejected["status"])
        state = self.step(steps, "state")["state"]
        self.assertEqual(sorted(n["uid"] for n in state["nodes"]), [1, 2, 16])
        self.assertIn([1, 2, 16, 0], state["connections"])
        report("rejected", rejected_revision=8, undone=True, next_revision=state["revision"],
               nodes_after=len(state["nodes"]))

    def test_07_stale_proposal_is_undone(self):
        hub = self.start_hub()

        def silently_newer():
            self.wait_for_controller()
            time.sleep(0.3)
            with hub.lock:
                hub.revision = 9    # the hub moved on without telling this controller

        code, steps, err = self.run_script("wait-synced\nsleep 600\nremove 16\nexpect-rejected stale\nexpect-node 16\n",
                                           during=silently_newer)
        self.assertPassed(code, steps, err)
        report("stale", proposed=8, hub_revision=9, outcome=self.step(steps, "expect-rejected")["outcome"])

    def test_08_revision_from_another_controller(self):
        hub = self.start_hub()
        hub.known_plugins.add(CROSSOVER_URI)
        doc = default_document()
        doc["nodes"].append({"uid": 30, "name": "loudness", "plugin": {"format": "LV2", "identifier": LOUDNESS_URI},
                             "params": {"s0_e0_gain": -7.5}})
        doc["nodes"].append({"uid": 31, "name": "crossover", "plugin": {"format": "LV2", "identifier": CROSSOVER_URI},
                             "params": {"xover_hz": 2200.0}, "state": "AAEC", "future_key": {"kept": True}})
        doc["connections"] += [{"src": {"node": 16, "channel": 0}, "dst": {"node": 30, "channel": 0}},
                               {"src": {"node": 30, "channel": 0}, "dst": {"node": 31, "channel": 0}},
                               {"src": {"node": 31, "channel": 1}, "dst": {"node": 2, "channel": 1}}]

        def other():
            self.wait_for_controller()
            time.sleep(0.3)
            hub.new_revision(doc)

        code, steps, err = self.run_script(
            "wait-synced\nexpect-revision 8\nexpect-node 30\nwait-param 30 s0_e0_gain -7.5\n"
            "expect-connection 30:0 31:0\nexpect-connection 31:1 2:1\n"
            "add loudness\nexpect-applied\nstate\n", during=other)
        self.assertPassed(code, steps, err)
        state = self.step(steps, "state")["state"]
        placeholder = [n for n in state["nodes"] if n["uid"] == 31][0]
        self.assertEqual(placeholder["kind"], "placeholder")
        # The controller's own next revision keeps what it cannot show, untouched.
        kept = [n for n in hub.document["nodes"] if n["uid"] == 31][0]
        self.assertEqual(kept["params"], {"xover_hz": 2200.0})
        self.assertEqual(kept["state"], "AAEC")
        self.assertEqual(kept["future_key"], {"kept": True})
        self.assertEqual(hub.document["revision"], 9)
        report("other", revision_seen=8, placeholder_uid=31, next_revision=hub.document["revision"],
               kept_keys=len(kept))

    def test_09_monitor_subscriptions(self):
        hub = self.start_hub(document=default_document(probe=True))
        code, steps, err = self.run_script(
            "wait-synced\nopen 20\nwait-monitor 20 mon_level -20.5\n"
            "monitor-speaker 20 right\nwait-monitor 20 mon_level -30.25\n"
            "close 20\nsleep 300\n"
            "output-levels on\nwait-value left output/level\nwait-value right output/level\nsleep 500\nstate\n",
            extra=["--test-probe", "--speaker", "right=" + RIGHT])
        self.assertPassed(code, steps, err)

        left = [(m["op"], m["stream"], m.get("rate_hz")) for m, _ in hub.received_ops("monitor left")]
        right = [(m["op"], m["stream"], m.get("rate_hz")) for m, _ in hub.received_ops("monitor right")]
        self.assertEqual(left[:2], [("subscribe", "node/20/mon_level", 30), ("unsubscribe", "node/20/mon_level", None)])
        self.assertEqual(right[:2], [("subscribe", "node/20/mon_level", 30), ("unsubscribe", "node/20/mon_level", None)])
        self.assertIn(("subscribe", "output/level", 30), left)
        self.assertIn(("subscribe", "output/level", 30), right)
        state = self.step(steps, "state")["state"]
        levels = {s["name"]: (s["level"], s["level_count"]) for s in state["speakers"]}
        self.assertEqual(levels["left"][0], [-6.0, -12.0, -60.0, -66.0])
        self.assertEqual(levels["right"][0], [-9.0, -15.0, -70.0, -76.0])
        # About 30 values a second: count the output level lines over the last 0.5 s sleep.
        rate = {name: count for name, (_, count) in levels.items()}
        self.assertGreater(min(rate.values()), 8)
        report("monitor", left_lines=len(left), right_lines=len(right), mon_left=-20.5, mon_right=-30.25,
               level_values_left=rate["left"], level_values_right=rate["right"])

    def test_10_no_hub_no_edit(self):
        """Without the hub's state an edit is undone and nothing is sent anywhere."""
        code, steps, err = self.run_script("add loudness\nsleep 300\nexpect-no-node $new\nstate\n")
        self.assertPassed(code, steps, err)
        state = self.step(steps, "state")["state"]
        self.assertFalse(state["synced"])
        self.assertEqual(sorted(n["uid"] for n in state["nodes"]), [1, 2])
        report("no hub", nodes=len(state["nodes"]), synced=state["synced"])

    def test_11_reconnect_takes_the_hubs_state(self):
        hub = self.start_hub()
        newer = default_document(revision=12)
        newer["nodes"][0]["params"]["s0_e0_gain"] = -2.0

        def restart():
            self.wait_for_controller()
            time.sleep(0.4)
            hub.stop()
            time.sleep(0.5)
            self.hub = FakeHub(LEFT, RIGHT, document=newer).start()

        code, steps, err = self.run_script("wait-synced\nset 16 s0_e0_gain 9\nexpect-revision 12 15\n"
                                           "wait-param 16 s0_e0_gain -2\n", during=restart)
        self.assertPassed(code, steps, err)
        self.assertEqual(self.hub.received_ops("control", "graph"), [], "the controller never pushes its copy")
        self.assertEqual(self.hub.received_ops("control", "param"), [])
        report("reconnect", old_revision=7, new_revision=12, pushed=0,
               reconnect_ms=self.step(steps, "expect-revision")["ms"])


    def stalled_run(self, script, extra=(), resume_after=None):
        """Runs a script while the hub stops reading the controller's first control connection
        0.5 s after it connected; it reads again `resume_after` seconds later (or never)."""
        hub = self.start_hub(control_rcvbuf=4096)

        def stall():
            self.wait_for_controller()
            time.sleep(0.5)
            hub.stall_controllers()
            if resume_after is not None:
                time.sleep(resume_after)
                hub.resume_controllers()

        return self.run_script(script, extra=extra, during=stall)

    def test_12_hub_stops_reading_the_ui_keeps_running(self):
        """A hub that stops reading never blocks the message thread; the queued lines go out when
        it reads again, in order."""
        code, steps, err = self.stalled_run(
            "wait-synced\nsweep 16 3\nstate\nset 16 s0_e0_gain 5.5\nwait-echo 16 s0_e0_gain 5.5 30\nstate\n",
            resume_after=3.0)
        self.assertPassed(code, steps, err)
        sweep = self.step(steps, "sweep")
        during = self.step(steps, "state", 0)["state"]
        after = self.step(steps, "state", 1)["state"]
        ticks = max(s["max_tick_ms"] for s in steps if "max_tick_ms" in s)
        self.assertGreater(during["queued_bytes"], 64 * 1024, "the hub's stall should have backed lines up past the kernel's buffers")
        self.assertLess(ticks, 250, "the message thread stalled")
        self.assertEqual(after["connections_made"], 1, "the stall alone is no reason to reconnect")
        self.assertEqual(after["queued_bytes"], 0)
        params = [m for m, _ in self.hub.received_ops("control", "param")]
        self.assertEqual(params[-1]["value"], 5.5, "the queue went out in order, the last line last")
        report("stall", sweep_ticks=sweep["ticks"], values_set=sweep["values"], queued_kib=during["queued_bytes"] // 1024,
               longest_tick_gap_ms=ticks, lines_received=len(params), recovered_ms=self.step(steps, "wait-echo")["ms"])

    def test_13_hub_that_never_reads_again_is_dropped_and_reconnected(self):
        """Past the queue's bound the link drops the connection and connects again, and the new
        connection starts from the hub's state."""
        code, steps, err = self.stalled_run(
            "wait-synced\nsweep 16 3\nwait-synced 10\nset 16 s0_e0_gain 5.5\nwait-echo 16 s0_e0_gain 5.5 15\nstate\n",
            extra=["--send-queue-limit", "65536"])
        self.assertPassed(code, steps, err)
        state = self.step(steps, "state")["state"]
        ticks = max(s["max_tick_ms"] for s in steps if "max_tick_ms" in s)
        self.assertLess(ticks, 250, "the message thread stalled")
        self.assertEqual(state["connections_made"], 2)
        self.assertEqual(len(self.hub.received_ops("control", "hello")), 2)
        self.assertIn("stopped reading", err)
        report("dropped", queue_limit_kib=64, connections=state["connections_made"], longest_tick_gap_ms=ticks,
               recovered_ms=self.step(steps, "wait-echo")["ms"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
