"""ampd_protocol (docs/autonomous-development.md, section 5): ampd, hub and agent, does what
docs/protocol.md says, against stand-in speakerds (tests/vpair/stand_in_speakerd.py: the local link
and nothing else) that answer as each test tells them, failing included. No PipeWire: what
reaches the audio is the other step 3 scenarios' (knob_latency, graph_edit, ...).

Pass when:

- parameters: a value a controller sends over OSC reaches both speakerds (seq 0 on the local
  link) and comes back to every controller over OSC, stamped with the hub's seq; one sent over the
  control link also comes back over it; a parameter the current revision does not have, and a
  monitor (mon_), are answered with an error on the control link and reach no speakerd; the agent
  applies only values newer than the last it applied (an older seq, and seq 0, from anyone, do
  nothing);
- revisions: a proposal with a stale base, one that does not parse, and one naming a plugin no
  speaker has loaded are rejected, each with its reason, and reach no speakerd; a good one is
  applied on both and then sent to every controller as the graph; one the right speaker fails is
  rejected with "right: <its reason>" and the left gets the previous revision back; one the right
  does not answer within 10 s is rejected the same way, and when the right applies it late the
  hub sends it the current revision again;
- an offline speaker: while the right agent is down the hub applies on the left alone and says
  the right is offline; when it comes back it gets the current revision and the current values,
  and writes them to its graph.json;
- a newer revision on the agent (by time) is fetched with get and adopted by the hub, applied on
  its own speakerd and sent to the controllers;
- gestures: a begin reaches the other controllers (control and OSC), a controller that connects
  mid-drag finds it in the state, an end removes it, and a gesture with no end ends by itself
  after 5 s (not before 4.5 s), sent to every controller with the dragger's origin;
- the local link: a speakerd that is busy with another client is tried again every second; a
  speakerd that restarts under ampd gets every value again; a speakerd that comes back on
  another layer and revision is followed (ampd takes that layer's document and says so); a
  speakerd whose current layer is an older revision than ampd's gets ampd's;
- monitoring: two subscribers of one stream make one subscription to speakerd at the higher rate
  (60; 100, 0, -1 and 0.05 Hz are refused with an error, never clamped), each gets values at its own rate (within 30 %), the rate falls back when
  the faster one leaves and an unsubscribe goes to speakerd when the last one does; a stream that
  is not output/level or node/<uid>/mon_<id> is answered with an error;
- persistence: while values change 25 times a second for 3 s, graph.json is replaced at most 4
  times and holds the last value within 1.1 s of it; a new revision is written at once (within
  0.5 s); graph.lkg.json appears once the revision has played --lkg-after seconds (2 here), not
  before, and a speakerd restart starts that time again;
- the clarified points (docs/protocol.md, "Clarifications"): every speaker in the state has its
  monitor address; a speaker going off- or online brings every controller a whole state; the
  proposer gets applied before the graph; a document whose own revision is not the proposal's is
  rejected; keys ampd does not know (a node's "ui") reach both speakerds and the state as they
  were; the hub goes on from its seq after a restart, says it in its hello, and the agent applies
  its values again;
- the pair's secret (docs/protocol.md, "Authentication"): a controller or a monitor client whose
  hello does not prove it gets an error and nothing else; OSC is dropped and counted without a
  token (protocol 1's `,isfis`), with a wrong one, with an authenticated controller's token from
  another address, and with the token of a session that is over (4 dropped), and taken with the
  controller's own; the controller's echoes carry its token; agents ignore OSC (even from the hub's
  address, with a token: dropped and counted); an agent that proves the secret replaces a connected
  one of its name, and without a secret in ampd.json the hub welcomes with no proof and a second
  "right" is refused while the connected agent stays;
- at most 16 connections per link in their handshake: the 17th, on the control and on the monitor
  link, gets "16 connections in their handshake already", and a client gets in once they are gone;
- an agent believes nothing from a hub that does not prove the secret: a fake hub whose welcome
  has a wrong proof, a proof under another secret, or no welcome at all, then a value and an apply,
  is refused ("did not prove the pair's secret", "not a welcome"), and the agent's speakerd gets
  neither;
- limits: a document with more than 64 nodes or 512 connections is rejected; a 400-digit number
  is answered with an error and the next value goes through; a stream that is not exactly
  output/level or node/<16 and up>/mon_<1 to 64 characters> is refused, the 33rd subscription of
  a client is refused, and speakerd's refusal of a subscription reaches the subscriber;
- a revision time that is not protocol.md's format counts as no time (not newer);
- a value set while a revision applies (the right's speakerd takes 1.5 s) is kept by the new
  revision: in the graph sent to the controllers, both graph.json, every controller's values, and
  given again to both speakerds when they restart;
- the tweeter rule: with the right's tweeter on (device.json), a revision feeding the tweeter
  straight is failed by the right, never reaches its speakerd, is rejected with "right: the tweeter
  is on here (device.json) and ..." and rolled back on the left; with the hub's on, it is rejected
  before any speaker gets it;
- a line that is not JSON closes the connection; an unknown op is ignored.

    python3 -m unittest -v tests.vpair.test_ampd_protocol
"""

import json
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from harness import (ADDRESS, KNOB_PORT, TWEETER_MUTED, Ampd, Controller, MonitorClient, StandInPair, StandInSpeakerd,
                     MonitorRefused, ampd_proof, check_welcome, client_hello, graph_document, lv2_uri, osc_encode,
                     revision_document, state_dir)

HEADLESS = lv2_uri("loudness_headless")
PARAMS = {"mode": "Force 1", "s0_e0_gain": 0.0, "s0_e1_gain": 0.0}
DOCUMENT = graph_document(HEADLESS, PARAMS)


def documents(revision=3, right=None):
    left = revision_document(DOCUMENT, revision)
    return {"left": left, "right": right or left}


def with_node(document, uid=17, plugin=HEADLESS):
    doc = json.loads(json.dumps(document))
    for key in ("revision", "time", "origin"):
        doc.pop(key, None)
    doc["nodes"].append({"uid": uid, "plugin": {"format": "LV2", "identifier": plugin}, "params": {"mode": "Force 2"}})
    doc["connections"].append({"src": {"node": 16, "channel": 0}, "dst": {"node": uid, "channel": 0}})
    return doc


def params_seen(sd, value):
    return [e for e in sd.events("param", "in") if e.get("value") == value]


class Protocol(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vpair-protocol-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_parameters(self):
        with StandInPair(self.tmp, documents()) as pair, Controller("ctl-a") as a, Controller("ctl-b") as b:
            self.assertEqual({s["name"]: s["address"] for s in a.state["speakers"]}, ADDRESS)
            sent = time.monotonic()
            a.osc_param(16, "s0_e0_gain", 4.5)
            for name in ("left", "right"):
                got = pair.sd[name].wait_event(lambda e: e.get("op") == "param" and e.get("value") == 4.5, what="the value")
                print(f"\n  OSC to the hub -> {name}'s speakerd: {1000 * (got['t'] - sent):.2f} ms")
                self.assertEqual(got["seq"], 0, "ampd sends seq 0 on the local link")
                self.assertEqual(got["origin"], "ctl-a")
            for c in (a, b):
                echo = c.wait(lambda m: m["op"] == "param" and m["value"] == 4.5, what="the OSC echo")
                self.assertEqual(echo["via"], "osc")
                self.assertEqual(echo["seq"], 1)

            mark_b = b.mark()
            a.param(16, "s0_e1_gain", 3.0)
            echo = b.wait(lambda m: m["op"] == "param" and m["value"] == 3.0 and m["via"] == "control", after=mark_b,
                          what="the echo over the control link")
            self.assertEqual(echo["seq"], 2)
            b.wait(lambda m: m["op"] == "param" and m["value"] == 3.0 and m["via"] == "osc", after=mark_b,
                   what="the echo over OSC")
            pair.sd["right"].wait_event(lambda e: e.get("op") == "param" and e.get("value") == 3.0, what="3.0 on the right")

            for node, pid, why in ((16, "s1_e0_gain", "has no parameter"), (99, "s0_e0_gain", "no node 99"),
                                   (16, "mon_levelDb", "monitor")):
                mark = a.mark()
                a.param(node, pid, -7.25)
                error = a.wait(lambda m: m["op"] == "error" and m.get("about") == "param", after=mark,
                               what=f"the error for {node}/{pid}")
                self.assertIn(why, error["reason"])
            a.osc_param(16, "s1_e0_gain", -7.25)
            time.sleep(0.3)
            for name in ("left", "right"):
                self.assertEqual(params_seen(pair.sd[name], -7.25), [], f"a refused value reached {name}'s speakerd")

            # The agent orders by the hub's seq: older values and seq 0 do nothing.
            # Agents ignore OSC: even from the hub's own address, with a token, a datagram is dropped.
            udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp.bind((ADDRESS["left"], 0))
            udp.sendto(osc_encode("/bpi/param", "isfiss", 16, "s0_e0_gain", 11.0, 1000, "hub", a.token),
                       (ADDRESS["right"], KNOB_PORT))
            udp.close()
            line = pair.ampd["right"].wait_for("knobs: dropped a datagram from 127.0.1.1")
            print(f"\n  {line}")
            self.assertEqual(params_seen(pair.sd["right"], 11.0), [], "the agent applied an OSC value")
            pair.ampd["right"].wait_status(lambda st: st.get("osc_dropped") == 1, what="1 datagram dropped")

    def test_revisions(self):
        with StandInPair(self.tmp, documents()) as pair, Controller("ctl-a") as a, Controller("ctl-b") as b:
            for base, document, why in ((2, with_node(DOCUMENT), "stale"),
                                        (3, {"version": 1, "nodes": "no"}, "does not parse"),
                                        (3, with_node(DOCUMENT, plugin=lv2_uri("nowhere")), "no speaker has loaded"),
                                        (3, dict(with_node(DOCUMENT), revision=7), "is not the proposal's")):
                answer = a.propose(document, base=base)
                self.assertEqual(answer["op"], "rejected", answer)
                self.assertIn(why, answer["reason"])
            for name in ("left", "right"):
                self.assertEqual(pair.sd[name].events("apply", "in"), [], f"a rejected revision reached {name}")

            mark = a.mark()
            answer = a.propose(with_node(DOCUMENT))
            self.assertEqual(answer["op"], "applied", answer)
            ops = [m["op"] for m in a.messages[mark:] if m["op"] in ("applied", "graph")]
            self.assertEqual(ops[:2], ["applied", "graph"], "the proposer gets applied first, then the graph")
            graph = b.wait(lambda m: m["op"] == "graph" and m["revision"] == 4, what="the graph of revision 4")
            self.assertEqual([n["uid"] for n in graph["document"]["nodes"]], [16, 17])
            for name in ("left", "right"):
                self.assertEqual([e["revision"] for e in pair.sd[name].events("apply", "in")], [4])

            # The right fails: rejected, and the left gets revision 4 back.
            pair.sd["right"].set_control({"fail": {"5": "plugin xyz is not installed here"}})
            answer = a.propose(with_node(with_node(DOCUMENT), uid=18))
            self.assertEqual(answer["op"], "rejected", answer)
            self.assertEqual(answer["reason"], "right: plugin xyz is not installed here")
            pair.sd["left"].wait_event(lambda e: e.get("op") == "apply" and e["revision"] == 4
                                       and len(pair.sd["left"].events("apply", "in")) == 3, what="the roll back")
            self.assertEqual([e["revision"] for e in pair.sd["left"].events("apply", "in")], [4, 5, 4])
            self.assertEqual([e["revision"] for e in pair.sd["right"].events("apply", "in")], [4, 5])

            # The right does not answer within 10 s: rejected; its late answer brings revision 4 back.
            pair.sd["right"].set_control({"delay": 11.0})
            started = time.monotonic()
            answer = a.propose(with_node(with_node(DOCUMENT), uid=18))
            took = time.monotonic() - started
            print(f"\n  a silent right: rejected after {took:.2f} s: {answer.get('reason')}")
            self.assertEqual(answer["op"], "rejected", answer)
            self.assertEqual(answer["reason"], "right: no answer within 10 s")
            self.assertGreaterEqual(took, 9.9)
            self.assertLess(took, 11.0)
            pair.sd["left"].wait_event(lambda e: e.get("op") == "apply" and len(pair.sd["left"].events("apply", "in")) == 5,
                                       what="the second roll back")
            self.assertEqual([e["revision"] for e in pair.sd["left"].events("apply", "in")], [4, 5, 4, 5, 4])
            pair.sd["right"].set_control({})
            pair.sd["right"].wait_event(lambda e: e.get("op") == "apply" and e["revision"] == 4
                                        and len(pair.sd["right"].events("apply", "in")) == 4, timeout=15,
                                        what="revision 4 again after the late answer")
            pair.ampd["left"].wait_for("agent right: applied revision 5 after it was given up")
            self.assertEqual(a.current_revision(), 4)

    def test_an_offline_speaker_is_caught_up(self):
        with StandInPair(self.tmp, documents()) as pair, Controller("ctl-a") as a:
            right = pair.ampd["right"]
            right.stop()
            pair.ampd["left"].wait_for("agent right disconnected: offline")
            state = a.wait(lambda m: m["op"] == "state" and not m["speakers"][1]["online"], what="a state with right offline")
            self.assertEqual(state["speakers"][1]["name"], "right")
            self.assertEqual(a.propose(with_node(DOCUMENT))["op"], "applied")
            a.param(16, "s0_e0_gain", -3.5)
            pair.sd["left"].wait_event(lambda e: e.get("op") == "param" and e.get("value") == -3.5, what="the value")
            with Controller("ctl-late") as late:
                self.assertFalse([s for s in late.state["speakers"] if s["name"] == "right"][0]["online"])

            pair.start_ampd("right")
            pair.ampd["left"].wait_for("agent right: plays revision 3, not revision 4")
            pair.sd["right"].wait_event(lambda e: e.get("op") == "apply" and e["revision"] == 4, what="revision 4")
            pair.sd["right"].wait_event(lambda e: e.get("op") == "param" and e.get("value") == -3.5, what="the value")
            time.sleep(0.6)
            graph = json.loads((pair.state["right"] / "graph.json").read_text())
            self.assertEqual(graph["revision"], 4)
            self.assertEqual(graph["nodes"][0]["params"]["s0_e0_gain"], -3.5)
            a.wait(lambda m: m["op"] == "state" and m["speakers"][1]["online"], what="a state with right online again")

    def test_the_hub_adopts_a_newer_revision(self):
        newer = revision_document(with_node(DOCUMENT), 9, origin="ctl-old", rev_time="2026-06-01T12:00:00Z")
        with StandInPair(self.tmp, documents(3, right=newer)) as pair, Controller("ctl-a") as a:
            pair.ampd["left"].wait_for("revision 9 of right adopted: current")
            self.assertTrue(pair.sd["right"].events("get", "in") == [])   # get goes to the agent, not speakerd
            pair.sd["left"].wait_event(lambda e: e.get("op") == "apply" and e["revision"] == 9, what="revision 9")
            self.assertEqual(a.current_revision() if a.state["revision"] == 9 else
                             a.wait(lambda m: m["op"] == "graph", what="the graph")["revision"], 9)
            time.sleep(0.3)
            graph = json.loads((pair.state["left"] / "graph.json").read_text())
            self.assertEqual((graph["revision"], graph["time"], graph["origin"]), (9, "2026-06-01T12:00:00Z", "ctl-old"))

    def test_gestures(self):
        with StandInPair(self.tmp, documents()), Controller("ctl-a") as a, Controller("ctl-b") as b:
            a.gesture(16, "s0_e0_gain", "begin")
            for via in ("control", "osc"):
                got = b.wait(lambda m: m["op"] == "gesture" and m["via"] == via, what=f"the begin over {via}")
                self.assertEqual((got["state"], got["origin"]), ("begin", "ctl-a"))
            for value in (1.0, 2.0, 3.0):
                a.osc_param(16, "s0_e0_gain", value)
                time.sleep(0.02)
            with Controller("ctl-mid") as mid:
                self.assertEqual(mid.state["gestures"], [{"node": 16, "id": "s0_e0_gain", "origin": "ctl-a"}])
                a.param(16, "s0_e0_gain", 3.0)
                a.gesture(16, "s0_e0_gain", "end")
                mid.wait(lambda m: m["op"] == "gesture" and m["state"] == "end", what="the end")
            with Controller("ctl-after") as after:
                self.assertEqual(after.state["gestures"], [])

            # No end: ended by the hub after 5 s, refreshed by the dragger's values.
            mark = b.mark()
            begun = time.monotonic()
            a.gesture(16, "s0_e1_gain", "begin", osc=True)
            time.sleep(2.0)
            a.osc_param(16, "s0_e1_gain", 1.0)
            refreshed = time.monotonic()
            end = b.wait(lambda m: m["op"] == "gesture" and m["state"] == "end" and m["id"] == "s0_e1_gain",
                         timeout=10, after=mark, what="the end the hub makes")
            print(f"\n  a gesture with no end ended {end['arrived'] - refreshed:.2f} s after the dragger's last value "
                  f"({end['arrived'] - begun:.2f} s after its begin)")
            self.assertEqual(end["origin"], "ctl-a")
            self.assertGreaterEqual(end["arrived"] - refreshed, 4.5)
            self.assertLess(end["arrived"] - refreshed, 6.0)

    def test_the_local_link(self):
        with StandInPair(self.tmp, documents()) as pair, Controller("ctl-a") as a:
            # A restart of speakerd under ampd: the values again.
            a.param(16, "s0_e0_gain", 6.5)
            pair.sd["right"].wait_event(lambda e: e.get("op") == "param" and e.get("value") == 6.5, what="the value")
            for name in ("left", "right"):
                sd = pair.restart_speakerd(name)
                sd.wait_event(lambda e: e.get("op") == "param" and e.get("value") == 6.5, what=f"the value on {name} again")
            self.assertEqual(len(pair.ampd["left"].lines_starting("speakerd: connected")), 2)

            # Busy: another client holds the socket; ampd tries every second until it is free.
            pair.sd["left"].stop()
            pair.ampd["left"].wait_for("speakerd: lost the connection")
            mark = pair.ampd["left"].mark()
            pair.sd["left"] = pair._stack.enter_context(type(pair.sd["right"])(pair.env["left"], pair.state["left"]))
            squatter = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            squatter.connect(str(pair.sd["left"].socket))
            pair.ampd["left"].wait_for(("speakerd: busy", "speakerd: connected"), after=mark)
            self.assertTrue(pair.ampd["left"].lines_starting("speakerd: busy", after=mark),
                            pair.ampd["left"].log())
            squatter.close()
            pair.ampd["left"].wait_for("speakerd: connected", after=mark, timeout=5)

            # speakerd comes back on another layer: ampd follows it.
            lkg = revision_document(DOCUMENT, 2, origin="older")
            (pair.state["right"] / "graph.lkg.json").write_text(json.dumps(lkg))
            (pair.state["right"] / "graph.json").write_text("{ half a file")
            mark = pair.ampd["right"].mark()
            pair.restart_speakerd("right")
            line = pair.ampd["right"].wait_for("speakerd: plays layer", after=mark)
            self.assertEqual(line, "ampd: speakerd: plays layer last-known-good, revision 2, not revision 3: following it")
            # It says hello to the hub again, which sends it revision 3 back.
            pair.ampd["left"].wait_for("agent right: plays revision 2, not revision 3")

    def test_an_outdated_current_layer_gets_the_revision(self):
        run = self.tmp / "run"
        run.mkdir()
        env = dict(os.environ, XDG_RUNTIME_DIR=str(run))
        state = state_dir(self.tmp / "state", role="left", current=revision_document(DOCUMENT, 2), limits=TWEETER_MUTED)
        with StandInSpeakerd(env, state) as sd:
            # speakerd loaded revision 2 as its current layer; graph.json has moved on to 3.
            (state / "graph.json").write_text(json.dumps(revision_document(DOCUMENT, 3)))
            with Ampd(env, state, "left") as hub:
                hub.wait_for("speakerd: plays revision 2 of the current layer, not revision 3: applying revision 3")
                sd.wait_event(lambda e: e.get("op") == "apply" and e["revision"] == 3, what="revision 3")
                hub.wait_for("revision 3: applied")

    def test_monitoring(self):
        with StandInPair(self.tmp, documents()) as pair:
            sd = pair.sd["right"]
            with MonitorClient(ADDRESS["right"]) as slow, MonitorClient(ADDRESS["right"]) as fast:
                slow.subscribe("output/level", 10)
                sd.wait_event(lambda e: e.get("op") == "subscribe", what="a subscribe")
                # rate_hz must be 0.1 to 60: outside is refused with an error, never clamped.
                for rate in (100, 0, -1, 0.05):
                    fast.subscribe("output/level", rate)
                deadline = time.monotonic() + 5
                while len(fast.errors) < 4 and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertEqual([e["reason"] for e in fast.errors], ["rate_hz must be 0.1 to 60"] * 4, fast.errors)
                fast.subscribe("output/level", 60)
                sd.wait_event(lambda e: e.get("op") == "subscribe" and e["rate_hz"] == 60, what="the rate of 60 Hz")
                fast.subscribe("node/16/mon_levelDb", 20)
                start = time.monotonic()
                time.sleep(3.0)
                rates = {name: len(c.of("output/level", since=start)) / 3.0 for name, c in (("slow", slow), ("fast", fast))}
                level = len(fast.of("node/16/mon_levelDb", since=start)) / 3.0
                print(f"\n  output/level: {rates['slow']:.1f} Hz asked 10, {rates['fast']:.1f} Hz asked 100 (60); "
                      f"node/16/mon_levelDb {level:.1f} Hz asked 20")
                self.assertLess(abs(rates["slow"] - 10) / 10, 0.3)
                self.assertLess(abs(rates["fast"] - 60) / 60, 0.3)
                self.assertLess(abs(level - 20) / 20, 0.3)
                value = fast.of("output/level")[-1][1]
                self.assertEqual(value["v"], [-12.0, -15.0, -120.0, -120.0])
                fast.close()
                sd.wait_event(lambda e: e.get("op") == "subscribe" and e["rate_hz"] == 10 and e["t"] > start + 3.0,
                              what="the rate back at 10 Hz")
                sd.wait_event(lambda e: e.get("op") == "unsubscribe" and e["stream"] == "node/16/mon_levelDb",
                              what="the unsubscribe of the stream only the fast one had")
                slow.subscribe("node/x/mon_y")
                deadline = time.monotonic() + 5
                while not slow.errors and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(slow.errors and slow.errors[0]["about"] == "subscribe", slow.errors)
            sd.wait_event(lambda e: e.get("op") == "unsubscribe" and e["stream"] == "output/level",
                          what="the unsubscribe when the last subscriber left")

    def test_persistence(self):
        with StandInPair(self.tmp, documents(), ampd_args=["--lkg-after", "2"]) as pair, Controller("ctl-a") as a:
            graph, lkg = pair.state["left"] / "graph.json", pair.state["left"] / "graph.lkg.json"
            pair.ampd["left"].wait_for("promoted graph.json to graph.lkg.json: revision 3")
            self.assertEqual(json.loads(lkg.read_text())["revision"], 3)

            inodes, value = {graph.stat().st_ino}, 0.0
            start = time.monotonic()
            while time.monotonic() - start < 3.0:
                value = round(value + 0.25, 2)
                a.osc_param(16, "s0_e0_gain", value)
                time.sleep(0.04)
                inodes.add(graph.stat().st_ino)
            last = time.monotonic()
            while time.monotonic() - last < 1.1:
                inodes.add(graph.stat().st_ino)
                if json.loads(graph.read_text())["nodes"][0]["params"]["s0_e0_gain"] == value:
                    break
                time.sleep(0.01)
            held = time.monotonic() - last
            writes = len(inodes) - 1
            print(f"\n  25 values a second for 3 s: graph.json written {writes} times, the last value in it "
                  f"{held:.2f} s after it was sent")
            self.assertLessEqual(writes, 4)
            self.assertEqual(json.loads(graph.read_text())["nodes"][0]["params"]["s0_e0_gain"], value)
            self.assertLess(held, 1.1)

            proposed = time.monotonic()
            self.assertEqual(a.propose(with_node(DOCUMENT))["op"], "applied")
            while json.loads(graph.read_text())["revision"] != 4 and time.monotonic() - proposed < 0.5:
                time.sleep(0.01)
            self.assertEqual(json.loads(graph.read_text())["revision"], 4, "a new revision was not written at once")

            # graph.lkg.json: revision 4 after 2 s of play, and a speakerd restart starts that again.
            time.sleep(1.0)
            pair.restart_speakerd("left")
            restarted = time.monotonic()
            time.sleep(1.5)
            self.assertEqual(json.loads(lkg.read_text())["revision"], 3, "promoted although speakerd restarted")
            pair.ampd["left"].wait_for("promoted graph.json to graph.lkg.json: revision 4", timeout=5)
            promoted = time.monotonic() - restarted
            print(f"  revision 4 promoted {promoted:.2f} s after the speakerd restart (--lkg-after 2)")
            self.assertGreaterEqual(promoted, 1.9)
            self.assertEqual(json.loads(lkg.read_text())["revision"], 4)

    def test_the_hub_goes_on_from_its_seq(self):
        with StandInPair(self.tmp, documents()) as pair:
            with Controller("ctl-a") as a:
                for value in (1.0, 2.0, 3.0):
                    a.param(16, "s0_e0_gain", value)
                last = a.wait(lambda m: m["op"] == "param" and m["value"] == 3.0 and m["via"] == "control")["seq"]
            time.sleep(1.2)
            pair.ampd["left"].stop()
            pair.start_ampd("left")
            pair.ampd["left"].wait_for("agent right: plays revision 3")
            with Controller("ctl-b") as b:
                print(f"\n  seq {last} before the hub restarted, {b.hub_hello['seq']} in its hello after")
                self.assertGreaterEqual(b.hub_hello["seq"], last)
                self.assertEqual(b.state["seq"], b.hub_hello["seq"])
                b.param(16, "s0_e0_gain", 4.0)
                self.assertEqual(b.wait(lambda m: m["op"] == "param" and m["value"] == 4.0)["seq"], last + 1)
            pair.sd["right"].wait_event(lambda e: e.get("op") == "param" and e.get("value") == 4.0,
                                        what="the value after the hub's restart on the agent")

    def test_unknown_keys_are_kept(self):
        with StandInPair(self.tmp, documents()) as pair, Controller("ctl-a") as a:
            doc = with_node(DOCUMENT)
            doc["nodes"][1]["ui"] = {"x": 120, "y": 40}
            doc["canvas"] = {"zoom": 1.5}
            self.assertEqual(a.propose(doc)["op"], "applied")
            for name in ("left", "right"):
                applied = [e for e in pair.sd[name].events("apply", "in")][-1]["document"]
                self.assertEqual((applied["nodes"][1]["ui"], applied["canvas"]), ({"x": 120, "y": 40}, {"zoom": 1.5}), name)
            with Controller("ctl-b") as b:
                self.assertEqual(b.state["document"]["nodes"][1]["ui"], {"x": 120, "y": 40})

    def test_the_pairs_secret(self):
        with StandInPair(self.tmp, documents()) as pair, Controller("ctl-a") as a:
            hub = pair.ampd["left"]
            # A controller and a monitor client that cannot prove the secret are refused.
            wrong = Controller("ctl-wrong", secret="not-the-pairs-secret-at-all", expect_state=False)
            error = wrong.wait(lambda m: m["op"] == "error", what="the refusal")
            self.assertEqual(error["about"], "hello")
            time.sleep(0.2)
            self.assertFalse([m for m in wrong.messages if m["op"] in ("welcome", "state")])
            wrong.close()
            with self.assertRaises(MonitorRefused):
                MonitorClient(ADDRESS["left"], secret="not-the-pairs-secret-at-all", timeout=3)

            # OSC needs the token of an authenticated controller connected from that address.
            udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            send = lambda tags, *args, source=udp: source.sendto(osc_encode("/bpi/param", tags, 16, "s0_e0_gain", *args),
                                                                  (ADDRESS["left"], KNOB_PORT))
            send("isfis", 9.25, 0, "no-token")                       # protocol 1's form, no token
            send("isfiss", 9.5, 0, "wrong", "0" * 32)                # a token, not this session's
            elsewhere = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            elsewhere.bind(("127.0.0.5", 0))
            send("isfiss", 9.75, 0, "elsewhere", a.token, source=elsewhere)   # ctl-a's token, another address
            elsewhere.close()
            # A token from a session that is over (ctl-b, gone) is refused, also from its address.
            b = Controller("ctl-b")
            old = b.token
            b.close()
            hub.wait_for("controller ctl-b disconnected")
            send("isfiss", 10.0, 0, "replay", old)
            a.osc_param(16, "s0_e0_gain", 2.5)                       # ctl-a's own token: applied
            pair.sd["left"].wait_event(lambda e: e.get("op") == "param" and e.get("value") == 2.5, what="ctl-a's value")
            udp.close()
            for value in (9.25, 9.5, 9.75, 10.0):
                self.assertEqual(params_seen(pair.sd["left"], value), [], f"the datagram with {value} was applied")
            dropped = hub.wait_status(lambda st: st.get("osc_dropped", 0) >= 4, what="4 datagrams dropped")["osc_dropped"]
            print(f"\n  {hub.lines_starting('knobs: dropped a datagram')[0]}\n  dropped: {dropped}; ctl-a's echoes "
                  f"carry its token, it ignored {a.osc_ignored} datagram(s) without it", end="")
            self.assertEqual(dropped, 4)
            self.assertEqual(a.osc_ignored, 0)

            # An agent called right that proves the secret replaces the first (a restart of its ampd).
            conn = socket.create_connection((ADDRESS["left"], 7450), timeout=5)
            f = conn.makefile("rb")
            server = json.loads(f.readline())
            hello, nc = client_hello(server, "agent", "right", revision=3)
            conn.sendall(json.dumps(hello).encode() + b"\n")
            self.assertTrue(check_welcome(server, json.loads(f.readline()), nc))
            hub.wait_for("agent right: a new connection replaces the old one")
            f.close()
            conn.close()

    def test_handshakes_are_capped(self):
        with StandInPair(self.tmp, documents()) as pair:
            for port, link in ((7450, "control"), (7452, "monitor")):
                waiting = [socket.create_connection((ADDRESS["left"], port), timeout=5) for _ in range(16)]
                for sock in waiting:
                    sock.makefile("rb").readline()          # the server's hello; no answer
                extra = socket.create_connection((ADDRESS["left"], port), timeout=5)
                answer = json.loads(extra.makefile("rb").readline())
                print(f"\n  {link}: the 17th connection in its handshake: {answer}", end="")
                self.assertEqual((answer["op"], answer["reason"]), ("error", "16 connections in their handshake already"))
                for sock in waiting + [extra]:
                    sock.close()
                time.sleep(0.3)
                with (Controller("ctl-after") if link == "control" else MonitorClient(ADDRESS["left"])):
                    pass                                   # once they are gone, a client gets in
            self.assertTrue(pair.ampd["left"].running())

    def test_a_hub_that_cannot_prove_the_secret_is_not_believed(self):
        run = self.tmp / "run"
        run.mkdir()
        env = dict(os.environ, XDG_RUNTIME_DIR=str(run))
        state = state_dir(self.tmp / "state", role="right", current=revision_document(DOCUMENT, 3), limits=TWEETER_MUTED)
        listener = socket.create_server((ADDRESS["left"], 7450), reuse_port=False)
        listener.settimeout(15)
        orders = [{"op": "param", "node": 16, "id": "s0_e0_gain", "value": 8.5, "seq": 1, "origin": "fake"},
                  {"op": "apply", "revision": 4, "document": dict(DOCUMENT, revision=4)}]
        with StandInSpeakerd(env, state) as sd, Ampd(env, state, "right") as agent:
            for welcome in ({"op": "welcome", "auth": "0" * 64},                   # a wrong proof
                            {"op": "welcome", "auth": ampd_proof("server", "0" * 32, "1" * 32, "hub", "left",
                                                                 "a-secret-it-does-not-have")},
                            orders[0]):                                          # no welcome at all
                conn, _ = listener.accept()
                f = conn.makefile("rb")
                conn.sendall(json.dumps({"op": "hello", "role": "hub", "name": "left", "revision": 9, "seq": 0,
                                         "nonce": os.urandom(16).hex(), "protocol": 1}).encode() + b"\n")
                json.loads(f.readline())                       # the agent's hello
                for message in [welcome] + orders:
                    try:
                        conn.sendall(json.dumps(message).encode() + b"\n")
                    except OSError:
                        break
                time.sleep(0.5)
                f.close()
                conn.close()
            line = agent.wait_for("hub: ")
            print(f"\n  {line}")
            time.sleep(0.5)
            self.assertFalse(agent.lines_starting("hub: connected"), agent.log())
            self.assertTrue(agent.lines_starting("hub: the hub at 127.0.1.1:7450 did not prove the pair's secret"),
                            agent.log())
            self.assertEqual(sd.events("apply", "in"), [], "a fake hub applied a revision")
            self.assertEqual(params_seen(sd, 8.5), [], "a fake hub set a value")
            self.assertFalse(agent.status()["hub"]["connected"])
        listener.close()

    def test_without_a_secret_an_agent_is_not_displaced(self):
        with StandInPair(self.tmp, documents(), secret=None) as pair:
            pair.ampd["left"].wait_for("*** no secret in ampd.json")
            conn = socket.create_connection((ADDRESS["left"], 7450), timeout=5)
            f = conn.makefile("rb")
            server = json.loads(f.readline())
            hello, _ = client_hello(server, "agent", "right", secret=None, revision=3)
            conn.sendall(json.dumps(hello).encode() + b"\n")
            self.assertEqual(json.loads(f.readline()), {"op": "welcome", "auth": None})
            error = json.loads(f.readline())
            self.assertEqual(error["op"], "error")
            self.assertIn("an agent called right is connected", error["reason"])
            f.close()
            conn.close()
            time.sleep(0.3)
            self.assertFalse(pair.ampd["left"].lines_starting("agent right: a new connection replaces"))
            self.assertTrue(pair.ampd["right"].status()["hub"]["connected"])

    def test_limits_and_bad_values(self):
        with StandInPair(self.tmp, documents(), control={"left": {"refuse_streams": ["node/17/mon_levelDb"]}}) as pair, \
                Controller("ctl-a") as a:
            big = with_node(DOCUMENT)
            big["nodes"] += [{"uid": 100 + i, "plugin": big["nodes"][0]["plugin"]} for i in range(63)]
            answer = a.propose(big)
            self.assertEqual(answer["op"], "rejected")
            self.assertIn("at most 64", answer["reason"])
            many = with_node(DOCUMENT)
            many["connections"] = many["connections"] * 200
            answer = a.propose(many)
            self.assertIn("at most 512", answer["reason"])
            # A 400-digit number is no value, and ends nothing.
            mark = a.mark()
            a.send({"op": "param", "node": 16, "id": "s0_e0_gain", "value": int("9" * 400), "seq": 0, "origin": "ctl-a"})
            self.assertEqual(a.wait(lambda m: m["op"] == "error", after=mark)["about"], "param")
            a.param(16, "s0_e0_gain", 1.75)
            pair.sd["left"].wait_event(lambda e: e.get("op") == "param" and e.get("value") == 1.75, what="the next value")
            with MonitorClient(ADDRESS["left"]) as meter:
                for stream in ("node/3/mon_levelDb", "node/16/mon_" + "x" * 65, "output/levels", "output/level\n"):
                    meter.subscribe(stream)
                meter.subscribe("node/17/mon_levelDb")             # the stand-in refuses it: relayed
                for k in range(40):
                    meter.subscribe(f"node/{20 + k}/mon_x")
                deadline = time.monotonic() + 5
                while len(meter.errors) < 13 and time.monotonic() < deadline:
                    time.sleep(0.05)
                refused = sorted({e["stream"] for e in meter.errors})
                print(f"\n  refused subscriptions: {len(meter.errors)}")
                for stream in ("node/3/mon_levelDb", "output/levels", "output/level\n", "node/17/mon_levelDb"):
                    self.assertIn(stream, refused)
                self.assertTrue([e for e in meter.errors if "at most 32" in e["reason"]], meter.errors)

    def test_times_are_checked(self):
        garbage = revision_document(with_node(DOCUMENT), 9, origin="zzz", rev_time="9999-99-99 not a time")
        with StandInPair(self.tmp, documents(3, right=garbage)) as pair:
            pair.ampd["left"].wait_for("agent right: plays revision 9, not revision 3")
            self.assertFalse(pair.ampd["left"].lines_starting("agent right: revision 9 is newer"))

    def test_values_set_while_a_revision_applies(self):
        with StandInPair(self.tmp, documents(), control={"right": {"delay": 1.5}}) as pair, \
                Controller("ctl-a") as a, Controller("ctl-b") as b:
            proposal = threading.Thread(target=lambda: self.__dict__.setdefault("answer", b.propose(with_node(DOCUMENT))))
            proposal.start()
            pair.ampd["left"].wait_for("revision 4 from ctl-b: applying on")
            a.gesture(16, "s0_e0_gain", "begin")
            for value in (1.0, 2.0, 3.0):
                a.osc_param(16, "s0_e0_gain", value)
                time.sleep(0.05)
            a.param(16, "s0_e0_gain", 7.25)                    # the last value, while revision 4 applies
            a.gesture(16, "s0_e0_gain", "end")
            proposal.join(timeout=20)
            self.assertEqual(self.answer["op"], "applied", self.answer)
            pair.ampd["left"].wait_for("revision 4: keeps 1 value(s) changed while it was applied")
            graph = b.wait(lambda m: m["op"] == "graph" and m["revision"] == 4, what="the graph of revision 4")
            self.assertEqual(graph["document"]["nodes"][0]["params"]["s0_e0_gain"], 7.25)
            time.sleep(1.2)
            for name in ("left", "right"):
                saved = json.loads((pair.state[name] / "graph.json").read_text())
                self.assertEqual((saved["revision"], saved["nodes"][0]["params"]["s0_e0_gain"]), (4, 7.25), name)
            for c in (a, b):
                self.assertEqual(c.values()[(16, "s0_e0_gain")], 7.25, c.name)
            with Controller("ctl-c") as c:
                self.assertEqual(c.values()[(16, "s0_e0_gain")], 7.25)
            # A speakerd restart gets 7.25 again, not revision 4's 0.
            for name in ("left", "right"):
                sd = pair.restart_speakerd(name)
                sd.wait_event(lambda e: e.get("op") == "param" and e.get("id") == "s0_e0_gain", what="the value again")
                self.assertEqual([e["value"] for e in sd.events("param", "in") if e["id"] == "s0_e0_gain"], [7.25], name)

    def test_the_tweeter_rule(self):
        direct = {"format": "speakerd-graph", "version": 1, "nodes": [],
                  "connections": [{"src": {"node": 1, "channel": 2}, "dst": {"node": 2, "channel": 1}}]}
        on = {"gain_db": 0, "tweeter": "on"}
        # The agent's tweeter on: it fails the revision, the hub rejects it and rolls the left back.
        with StandInPair(self.tmp / "agent", documents(), limits={"right": on}) as pair, Controller("ctl-a") as a:
            answer = a.propose(direct)
            print(f"\n  the right's tweeter on: {answer['reason']}")
            self.assertEqual(answer["op"], "rejected")
            self.assertTrue(answer["reason"].startswith("right: the tweeter is on here (device.json) and the stream "
                                                        "reaches the tweeter other than through a crossover's high band"))
            self.assertEqual(pair.sd["right"].events("apply", "in"), [], "the right's speakerd got the revision")
            pair.sd["left"].wait_event(lambda e: e.get("op") == "apply" and len(pair.sd["left"].events("apply", "in")) == 2,
                                       what="the roll back on the left")
            self.assertEqual([e["revision"] for e in pair.sd["left"].events("apply", "in")], [4, 3])
        # The hub's tweeter on: rejected before anything is applied.
        with StandInPair(self.tmp / "hub", documents(), limits={"left": on}) as pair, Controller("ctl-a") as a:
            answer = a.propose(direct)
            self.assertEqual(answer["op"], "rejected")
            self.assertTrue(answer["reason"].startswith("left: the tweeter is on here"), answer["reason"])
            for name in ("left", "right"):
                self.assertEqual(pair.sd[name].events("apply", "in"), [])

    def test_bad_lines(self):
        with StandInPair(self.tmp, documents()):
            for line in (b'{"op": "nonsense", "x": 1}\n', b"not json\n"):
                conn = socket.create_connection((ADDRESS["left"], 7450), timeout=5)
                f = conn.makefile("rb")
                server = json.loads(f.readline())
                self.assertEqual(server["op"], "hello")
                hello, nc = client_hello(server, "controller", "bad")
                conn.sendall(json.dumps(hello).encode() + b"\n")
                self.assertTrue(check_welcome(server, json.loads(f.readline()), nc))
                self.assertEqual(json.loads(f.readline())["op"], "state")
                conn.sendall(line)
                conn.settimeout(1.0)
                try:
                    closed = f.readline() == b""
                except socket.timeout:
                    closed = False
                self.assertEqual(closed, line == b"not json\n", line)
                f.close()
                conn.close()


if __name__ == "__main__":
    unittest.main()
