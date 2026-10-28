"""controller_end_to_end (docs/autonomous-development.md, section 5): the real controller
(dsp/juce_apps/controller, built by tools/build.sh, in its script test mode) against the real pair
(both speakers with speakerd and ampd, the left the hub, the right its agent, the pair's secret
harness.TEST_SECRET), with nothing of the harness's own controller in between.

Both speakers start on revision 1: the Mine pin (input channel 2) through loudness_headless (node
16: Force 1, its first shelf at 1 kHz, 0 dB, a 1 ms glide) to both amplifier channels, the tweeter
muted in device.json (the scenarios' default). Both play the same stereo 200 Hz tone, the left
channel at -20 dBFS and the right at -32 dBFS, so each speaker plays its own side, 12 dB apart. Each
amplifier is recorded by a harness.TimedRecorder (which knows when each sample was in the graph) and
watched by pw-profiler (harness.Profiler). The controller runs with its own HOME, the secret in a
file of mode 600, `--name ctl-e2e` and `--hub-name left`; a test reads its JSON lines as they come,
each stamped with when it arrived. The time of a `set` is when its line arrived (the controller
prints it after the value has left the bridge for the control link), of a `drag` its line's arrival
less its `ms`. The reference levels are speakerd's own render (`--render`) of the same document with
the same tone.

Pass when:

(a) Connect. The controller says the hub is synced and proven, on revision 1, named left; its
    canvas has node 16 (a control build of loudness_headless) with the document's values and the
    document's three connections; both speakers are online on layer current; and once values came
    from both, both speakers' monitor links are connected with no refusal, and neither ampd logged a
    refusal.
(b) Knob. Nine `set 16 s0_e0_gain` (+12 dB and 0 dB alternately, 1.2 s apart, over the control
    link) are each echoed back to the controller (`wait-echo`); each change is heard in both captures
    within 60 ms of the send, and not before it, measured and set aside exactly as knob_latency does
    (its onset(), and pw-profiler's xruns and late cycles), at least 5 of them measured; both
    graph.json hold the last value (12) within 2 s. A `drag` from 12 to 3.5 dB (20 values over 1 s, a
    gesture: OSC, the last value over the control link) shows on both speakers: at least 10 of the
    20 ms blocks during it have a 200 Hz level more than 0.5 dB inside the two ends' levels; its last
    value is echoed; the 200 Hz level after it is the render's at 3.5 dB within 0.1 dB on both; and
    after the run both graph.json, the hub's state and the controller hold 3.5.
(c) Edit. `add loudness` is applied (revision 2) and both graph.json have the new node, its
    headless URI and every parameter; a `set` of its mode is echoed; `disconnect 16:0 2:0` is applied
    (revision 3) and heard: both woofers silent (no sample above -100 dBFS) from 0.2 s to 1.2 s after;
    `connect-pins 16:0 <new>:0` and `connect-pins <new>:0 2:0` are applied (revisions 4 and 5) and
    heard: both woofers back at the render's 200 Hz level (the new node is flat) within 0.1 dB; both
    graph.json are revision 5 with the controller's connections; the controller's state (nodes, their
    values, connections) is the hub's. Then, with the right's device.json saying the tweeter is on
    (ampd reads it at every revision; speakerd plays on muted), a second controller run's
    `connect-pins 1:0 2:1` (the stream straight to the tweeter) is rejected with "right: the tweeter
    is on here (device.json)", undone in the controller (no such connection, revision 5), the left
    rolls back to revision 5, both graph.json stay on revision 5, and the controller's state is the
    hub's.
(d) Monitors. With node 16's window open (`open 16`, the left speaker first), the medians of 8
    `node/16/mon_levelDb` values from the left are the left's input level as loudness's detector
    reads it (JUCE's BallisticsFilter, 100 ms attack, 300 ms release, computed here for the 200 Hz
    tone) within 1 dB; after `monitor-speaker 16 right`, those from the right are the right's within
    1 dB, and the subscription has moved (the left's list lacks node/16/mon_levelDb, the right's has
    it). `output/level` from each speaker: the woofer's peak is its own side's level (-20 and -32 dBFS)
    and its RMS 3.01 dB under, within 1 dB, the left's peak 12 dB over the right's within 1 dB (the
    Mine pin), and the muted tweeters under -100 dBFS.
(e) Refusal. A controller with another secret's file is refused: after 2.5 s its state is not proven,
    not synced, with the refusal in `refused`, on revision 0 with no plugin node; the hub logs `refused
    <address>: its hello does not prove the pair's secret`; and neither secret appears in anything
    the controller printed.
(f) GUI. One `--gui` run under Xvfb and openbox (harness.VirtualDisplay), the tone stepping between
    -30 and -12 dBFS (left) every 0.8 s: the controller synced, both monitor links connected with
    output levels arriving (each speaker's woofer level above -60 dBFS), node 16's window open with
    `mon_levelDb` moving (9 values spanning more than 6 dB); its `screenshot` (JUCE's rendering of
    every window) and the X screen are written to build/e2e-controller/ (controller-juce.png,
    controller-screen.png), each a PNG that is not one flat colour.

It needs step 3's speakerd (the local socket, the Mine pin: 0.3.0) and loudness's mon_ monitors.

    python3 -m unittest -v tests.vpair.test_controller_end_to_end
"""

import json
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from harness import (ADDRESS, BUILD, NO_LIMITS, RATE, TEST_SECRET, Controller, ControllerScript, Player, Profiler,
                     ScriptedController, SpeakerPair, TimedRecorder, VirtualDisplay, built, graph_document, lv2_uri,
                     render, require_speakerd_local_link, revision_document, sine_amplitudes_dbfs, state_dir, tone)
from test_knob_latency import onset

FREQ = 200.0                        # test_knob_latency's, whose onset() this uses
LEFT, RIGHT = -20.0, -32.0          # each side of the stereo tone: what each speaker's Mine carries
LOUDNESS = lv2_uri("loudness_headless")
PARAMS = {"mode": "Force 1", "s0_e0_freq": 1000.0, "s0_e0_gain": 0.0, "glide": 1.0}
DOCUMENT = revision_document(graph_document(LOUDNESS, PARAMS, input_channel=2), 1)
CHANGES = [12.0, 0.0] * 4 + [12.0]  # the knob's sets, alternately; the last one 12
AT_LEAST = 5
DRAG_TO = 3.5
WRONG_SECRET = "vpair-another-pairs-secret-123"
OUT = BUILD / "e2e-controller"
QUANTUM = 1024
CHOICES = {"mode": ["Auto", "Force 1", "Force 2", "Force 3", "Force 4"], "det": ["RMS", "Peak"]}


def stereo(left_db, right_db, seconds):
    return np.stack([tone(FREQ, left_db, seconds), tone(FREQ, right_db, seconds)], axis=1)


def detector_offset_db(freq=FREQ, attack_ms=100.0, release_ms=300.0, rate=RATE):
    """What loudness's detector settles to for a full-scale sine of `freq`, in dB: JUCE's
    BallisticsFilter in RMS mode, as test_meter_values computes it (for its 1 kHz), here for any
    frequency: y = x^2 + cte (y - x^2), the attack's cte when x^2 rises above y and the release's
    otherwise, cte = exp(-2 pi 1000 / (rate * ms)), its level sqrt(y); the median of the last half
    second, as the monitor samples it at arbitrary moments."""
    attack, release = (np.exp(-2 * np.pi * 1000.0 / (rate * ms)) for ms in (attack_ms, release_ms))
    x2 = np.sin(2 * np.pi * freq * np.arange(rate) / rate) ** 2
    y, out = 0.0, np.empty(len(x2))
    for i, v in enumerate(x2):
        y = v + (attack if v > y else release) * (y - v)
        out[i] = y
    return 10 * np.log10(np.median(out[rate // 2:]))


def same(a, b, pid=None):
    """Two parameter values in the document's units: numbers within 1e-3, a choice's text as its
    index, a switch as 0 or 1."""
    def number(v):
        if isinstance(v, bool):
            return float(v)
        if isinstance(v, str) and pid in CHOICES and v in CHOICES[pid]:
            return float(CHOICES[pid].index(v))
        return float(v) if isinstance(v, (int, float)) else v
    x, y = number(a), number(b)
    if isinstance(x, float) and isinstance(y, float):
        return abs(x - y) <= 1e-3 * max(1.0, abs(x), abs(y))
    return x == y


def connections(document):
    return sorted((c["src"]["node"], c["src"]["channel"], c["dst"]["node"], c["dst"]["channel"])
                  for c in document["connections"])


def differences(ctl_state, document):
    """Where the controller's state differs from a document (the hub's): its plugin nodes, their
    plugins and values, its connections. [] when it is the same."""
    out = []
    mine = {n["uid"]: n for n in ctl_state["nodes"] if n.get("kind") in ("control", "placeholder")}
    theirs = {n["uid"]: n for n in document["nodes"]}
    if set(mine) != set(theirs):
        return [f"plugin nodes {sorted(mine)} in the controller, {sorted(theirs)} in the hub's document"]
    for uid, node in theirs.items():
        if mine[uid].get("identifier") != node["plugin"]["identifier"]:
            out.append(f"node {uid}: {mine[uid].get('identifier')} in the controller, {node['plugin']['identifier']} at the hub")
        for pid, value in (node.get("params") or {}).items():
            have = (mine[uid].get("params") or {}).get(pid)
            if have is None or not same(have, value, pid):
                out.append(f"node {uid} {pid}: {have} in the controller, {value} at the hub")
    if sorted(map(tuple, ctl_state["connections"])) != connections(document):
        out.append(f"connections {sorted(map(tuple, ctl_state['connections']))} in the controller, "
                   f"{connections(document)} at the hub")
    return out


class ControllerEndToEnd(unittest.TestCase):
    def setUp(self):
        require_speakerd_local_link(self)
        self.tmp = Path(tempfile.mkdtemp(prefix="vpair-e2e-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.headless = built("loudness", "headless")
        built("controller")
        OUT.mkdir(parents=True, exist_ok=True)
        self.failures = []

    # Helpers -----------------------------------------------------------------------------------

    def check(self, ok, what):
        """A criterion that does not stop the scenario: said at the end, all of them."""
        if not ok:
            self.failures.append(what)
            print(f"\n  FAILED: {what}", end="")

    def reference(self, gain_db):
        """speakerd's render of revision 1's document with s0_e0_gain `gain_db`: the 200 Hz level of
        each speaker's woofer, {"left": dBFS, "right": dBFS}."""
        document = graph_document(LOUDNESS, dict(PARAMS, s0_e0_gain=gain_db), input_channel=2)
        out = {}
        for name in ("left", "right"):
            state = state_dir(self.tmp / f"ref-{name}-{gain_db:g}", role=name, current=document)
            y, _ = render(state, stereo(LEFT, RIGHT, 1.5), bundles=[self.headless])
            out[name] = sine_amplitudes_dbfs(y[int(0.5 * RATE):int(1.4 * RATE), 0], [FREQ])[0]
        return out

    @staticmethod
    def segment(capture, a, b):
        """The woofer channel of a capture ((x, recorder, t0) from TimedRecorder.stop()) from time
        `a` to `b` (time.monotonic()), mapped by the recorder's chunks around `a`."""
        x, recorder, t0 = capture
        before, after = recorder.local_t0(a)
        base = after if after is not None else (before if before is not None else t0)
        i, j = int(round((a - base) * RATE)), int(round((b - base) * RATE))
        if i < 0 or j > len(x):
            raise AssertionError(f"not recorded from {a:.2f} to {b:.2f}")
        return x[i:j, 0]

    def level(self, capture, a, b):
        return sine_amplitudes_dbfs(self.segment(capture, a, b), [FREQ])[0]

    def wait_graphs(self, pair, predicate, timeout=2.0):
        """Both graph.json once `predicate(graph)` holds for each (ampd writes on its file thread,
        at most once a second while values change), or as they are after `timeout`."""
        deadline = time.monotonic() + timeout
        while True:
            graphs = {name: pair.graph(name) for name in ("left", "right")}
            if all(predicate(g) for g in graphs.values()) or time.monotonic() > deadline:
                return graphs
            time.sleep(0.05)

    def hub_document(self):
        with Controller("ctl-e2e-check") as check:
            return check.state["document"], check.state

    # The scenario ------------------------------------------------------------------------------

    def test_the_controller_against_the_pair(self):
        offset = detector_offset_db()
        refs = {gain: self.reference(gain) for gain in (12.0, DRAG_TO)}
        print(f"\n  loudness's detector reads a steady {FREQ:g} Hz sine {offset:+.2f} dB from its peak; the render's "
              f"200 Hz woofer level at +12 dB: left {refs[12.0]['left']:.2f}, right {refs[12.0]['right']:.2f} dBFS; "
              f"at {DRAG_TO:+g} dB: left {refs[DRAG_TO]['left']:.2f}, right {refs[DRAG_TO]['right']:.2f}", end="")
        with SpeakerPair(self.tmp / "pair", {"left": DOCUMENT, "right": DOCUMENT}, [self.headless]) as pair:
            mark = {name: pair.ampd[name].mark() for name in ("left", "right")}
            with Player(pair["left"], stereo(LEFT, RIGHT, 120.0), target="speakerd"), \
                    Player(pair["right"], stereo(LEFT, RIGHT, 120.0), target="speakerd"):
                self.main_run(pair, offset, refs)
                for name in ("left", "right"):
                    refused = pair.ampd[name].lines_starting("refused ", after=mark[name])
                    self.check(not refused, f"(a) {name}'s ampd refused a hello of the controller's: {refused}")
                self.refusal_run(pair)
                self.wrong_secret_run(pair)
            self.gui_run(pair)
        self.assertEqual(self.failures, [], "\n".join(self.failures))

    def main_run(self, pair, offset, refs):
        s, n = ControllerScript(), {}
        n["synced"] = s.add("wait-synced 15")
        n["state0"] = s.add("state")
        s.add("expect-speaker left online")
        s.add("expect-speaker right online")
        # (d) Monitors: node 16's window, on the left first, and the speakers panel's levels.
        s.add("output-levels on")
        s.add("open 16")
        s.add("sleep 1000")
        n["mon_left"] = [s.add("wait-value left node/16/mon_levelDb") for _ in range(8)]
        n["level_left"] = [s.add("wait-value left output/level") for _ in range(8)]
        n["level_right"] = [s.add("wait-value right output/level") for _ in range(8)]
        n["state_left"] = s.add("state")
        s.add("monitor-speaker 16 right")
        s.add("sleep 1000")
        n["mon_right"] = [s.add("wait-value right node/16/mon_levelDb") for _ in range(8)]
        n["state_right"] = s.add("state")
        s.add("close 16")
        s.add("output-levels off")
        # (b) The knob: sets over the control link, then a drag.
        s.add("sleep 1000")
        n["sets"], n["echoes"] = [], []
        for value in CHANGES:
            n["sets"].append(s.add(f"set 16 s0_e0_gain {value:g}"))
            n["echoes"].append(s.add(f"wait-echo 16 s0_e0_gain {value:g}"))
            s.add("sleep 1150")
        s.add("sleep 1000")
        n["drag"] = s.add(f"drag 16 s0_e0_gain {CHANGES[-1]:g} {DRAG_TO:g} 20 1000")
        n["drag_echo"] = s.add(f"wait-echo 16 s0_e0_gain {DRAG_TO:g}")
        s.add("sleep 1500")
        # (c) Edits: a node added, then put in the woofer's path.
        n["add"] = s.add("add loudness 0.55 0.3")
        n["add_applied"] = s.add("expect-applied")
        s.add("expect-node $new")
        n["mode"] = s.add("set $new mode 1")
        n["mode_echo"] = s.add("wait-echo $new mode 1")
        s.add("sleep 300")
        s.add("disconnect 16:0 2:0")
        n["disconnect_applied"] = s.add("expect-applied")
        s.add("sleep 1500")
        s.add("connect-pins 16:0 $new:0")
        n["through_applied"] = s.add("expect-applied")
        s.add("connect-pins $new:0 2:0")
        n["back_applied"] = s.add("expect-applied")
        s.add("expect-revision 5")
        s.add("sleep 1500")
        n["state_end"] = s.add("state")

        recorders = {name: TimedRecorder(pair[name], latency=QUANTUM).start() for name in ("left", "right")}
        profilers = {name: Profiler(pair[name]).__enter__() for name in ("left", "right")}
        try:
            with ScriptedController(s, name="ctl-e2e", args=["--trace"]) as ctl:
                _, last_echo = ctl.step(n["echoes"][-1])
                # Both graph.json hold the last set's value (12; the one before was 0) within 2 s.
                graphs = self.wait_graphs(pair, lambda g: same(self.graph_value_of(g, 16, "s0_e0_gain"), CHANGES[-1]))
                for name, g in graphs.items():
                    self.check(same(self.graph_value_of(g, 16, "s0_e0_gain"), CHANGES[-1]),
                               f"(b) {name}'s graph.json has s0_e0_gain {self.graph_value_of(g, 16, 's0_e0_gain')} "
                               f"2 s after the last set's echo, not {CHANGES[-1]:g}")
                _, add_applied = ctl.step(n["add_applied"])
                # The added node in both graph.json, with its headless URI and every parameter.
                new = ctl.step(n["add"])[1]["uid"]
                graphs = self.wait_graphs(pair, lambda g: g.get("revision") == 2)
                for name, g in graphs.items():
                    nodes = {node["uid"]: node for node in g["nodes"]}
                    self.check(g.get("revision") == 2 and new in nodes, f"(c) {name}'s graph.json: revision {g.get('revision')}, "
                                                                        f"nodes {sorted(nodes)} after the add was applied")
                    if new in nodes:
                        self.check(nodes[new]["plugin"]["identifier"] == LOUDNESS and len(nodes[new].get("params", {})) == 35,
                                   f"(c) {name}'s graph.json: the new node is {nodes[new]['plugin']['identifier']} with "
                                   f"{len(nodes[new].get('params', {}))} parameters, not {LOUDNESS} with 35")
                status, result = ctl.finish(timeout=120)
                lines = {obj["step"]: (t, obj) for t, obj in ctl.steps()}
                self.assertEqual((status, result and result.get("result")), (0, "pass"), ctl.report("the main script failed"))
                trace = ctl.stderr_path.read_text(errors="replace").splitlines()
        finally:
            captures = {}
            for name, recorder in recorders.items():
                x, t0 = recorder.stop()
                captures[name] = (x, recorder, t0)
            xruns = {name: p.events(["speakerd-dsp", recorders[name].name]) for name, p in profilers.items()}
            for p in profilers.values():
                p.__exit__(None, None, None)

        self.check_connect(pair, lines, n)
        self.check_conformance(pair, trace, lines, n)
        self.check_monitors(lines, n, offset)
        self.check_knob(captures, xruns, lines, n)
        self.check_drag(pair, captures, lines, n, refs)
        self.check_edits(pair, captures, lines, n, refs, new)

    @staticmethod
    def graph_value_of(graph, uid, pid):
        nodes = {n["uid"]: n for n in graph["nodes"]}
        return (nodes.get(uid, {}).get("params") or {}).get(pid)

    # (a) ---------------------------------------------------------------------------------------

    def check_connect(self, pair, lines, n):
        state = lines[n["state0"]][1]["state"]
        print(f"\n  (a) synced {state['synced']}, proven {state['proven']}, hub {state['hub']}, revision {state['revision']}, "
              f"speakers " + ", ".join(f"{s['name']} {s['role']} {'online' if s['online'] else 'offline'} {s['layer']}"
                                       for s in state["speakers"]), end="")
        self.check(state["synced"] and state["proven"] and state["revision"] == 1 and state["hub"] == "left"
                   and not state["insecure"] and not state["refused"],
                   f"(a) the controller's first state: synced {state['synced']}, proven {state['proven']}, revision "
                   f"{state['revision']}, hub {state['hub']!r}, insecure {state['insecure']}, refused {state['refused']!r}")
        self.check(differences(state, DOCUMENT) == [], f"(a) the controller's canvas is not revision 1: {differences(state, DOCUMENT)}")
        node = next((x for x in state["nodes"] if x["uid"] == 16), {})
        self.check(node.get("kind") == "control", f"(a) node 16 is {node.get('kind')}, not a control build")
        speakers = {s["name"]: s for s in state["speakers"]}
        for name in ("left", "right"):
            s = speakers.get(name, {})
            self.check(s.get("online") and s.get("layer") == "current" and s.get("address") == ADDRESS[name],
                       f"(a) {name} in the controller's state: {s}")
        later = {s["name"]: s for s in lines[n["state_left"]][1]["state"]["speakers"]}
        for name in ("left", "right"):
            s = later.get(name, {})
            print(f"\n  (a) {name}'s monitor link: connected {s.get('monitor_connected')}, refusal {s.get('monitor_refusal')!r}", end="")
            self.check(s.get("monitor_connected") and not s.get("monitor_refusal"),
                       f"(a) {name}'s monitor link: connected {s.get('monitor_connected')}, refusal {s.get('monitor_refusal')!r}")

    def check_conformance(self, pair, trace, lines, n):
        """Nothing either side said it could not take: no `error` from the hub or a speaker to the
        controller (its --trace), nothing ampd ignored, dropped or relayed as speakerd's error, one
        control connection all along, no OSC echo without this connection's token."""
        errors = [line for line in trace if line.startswith("[bridge] < ") and re.search(r'"op":\s*"error"', line)]
        self.check(not errors, "(a-c) the controller got errors:\n  " + "\n  ".join(errors[:10]))
        # Its own remarks (not a message in or out): a link that failed or reconnected says so.
        remarks = [line for line in trace if not line.startswith(("[bridge] < ", "[bridge] > "))]
        for line in remarks[:10]:
            print(f"\n  (a-c) the controller said: {line[:200]}", end="")
        retried = [line for line in remarks if "trying again" in line or "reconnecting" in line]
        self.check(not retried, "(a-c) a link of the controller's failed:\n  " + "\n  ".join(retried[:10]))
        for name in ("left", "right"):
            bad = [line for line in pair.ampd[name].lines
                   if line.startswith(("ampd: ignored a message", "ampd: knobs:", "ampd: speakerd: error:",
                                       "ampd: dropped a connection", "ampd: refused "))]
            self.check(not bad, f"(a-c) {name}'s ampd:\n  " + "\n  ".join(bad[:10]))
        state = lines[n["state_end"]][1]["state"]
        print(f"\n  (a-c) the controller's trace: {len(trace)} lines, {len(errors)} errors; connections made "
              f"{state['connections_made']}, OSC echoes dropped {state['osc_dropped']}", end="")
        self.check(state["connections_made"] == 1 and state["osc_dropped"] == 0,
                   f"(a-c) the controller made {state['connections_made']} control connections and dropped "
                   f"{state['osc_dropped']} OSC echoes")

    # (d) ---------------------------------------------------------------------------------------

    def check_monitors(self, lines, n, offset):
        for name, level, key in (("left", LEFT, "mon_left"), ("right", RIGHT, "mon_right")):
            values = [float(lines[k][1]["v"]) for k in n[key]]
            m = float(np.median(values))
            want = level + offset
            print(f"\n  (d) node/16/mon_levelDb from the {name}: median {m:.2f} dBFS of {len(values)} "
                  f"({min(values):.2f} to {max(values):.2f}), the detector's {want:.2f}", end="")
            self.check(abs(m - want) <= 1.0, f"(d) mon_levelDb from the {name}: {m:.2f}, not {want:.2f} within 1 dB")
        peaks = {}
        for name, level, key in (("left", LEFT, "level_left"), ("right", RIGHT, "level_right")):
            wp, wr, tp, tr = np.median(np.array([lines[k][1]["v"] for k in n[key]], dtype=float), axis=0)
            peaks[name] = wp
            print(f"\n  (d) output/level from the {name}: woofer {wp:.2f} / {wr:.2f}, tweeter {tp:.2f} / {tr:.2f} dBFS "
                  f"(peak / RMS)", end="")
            for what, got, want in (("woofer peak", wp, level), ("woofer RMS", wr, level - 3.01)):
                self.check(abs(got - want) <= 1.0, f"(d) {name}'s output/level {what} {got:.2f}, not {want:.2f} within 1 dB")
            self.check(max(tp, tr) < -100.0, f"(d) {name}'s muted tweeter reads {tp:.2f} / {tr:.2f} dBFS")
        apart = peaks["left"] - peaks["right"]
        print(f"\n  (d) the left's woofer {apart:.2f} dB over the right's (the Mine pin: {LEFT - RIGHT:g} dB)", end="")
        self.check(abs(apart - (LEFT - RIGHT)) <= 1.0, f"(d) the left's woofer is {apart:.2f} dB over the right's, "
                                                       f"not {LEFT - RIGHT:g} within 1 dB")
        for key, chosen, other in (("state_left", "left", "right"), ("state_right", "right", "left")):
            state = lines[n[key]][1]["state"]
            subs = {s["name"]: s["subscriptions"] for s in state["speakers"]}
            node = next(x for x in state["nodes"] if x["uid"] == 16)
            self.check(node.get("monitor_speaker") == chosen and "node/16/mon_levelDb" in subs.get(chosen, [])
                       and "node/16/mon_levelDb" not in subs.get(other, []),
                       f"(d) with the window on the {chosen}: monitor_speaker {node.get('monitor_speaker')}, subscriptions {subs}")

    # (b) ---------------------------------------------------------------------------------------

    def check_knob(self, captures, xruns, lines, n):
        sends = [lines[k][0] - lines[k][1]["ms"] / 1000.0 for k in n["sets"]]
        for k, e in enumerate(n["echoes"]):
            echo = lines[e][1]
            self.check(same(echo["value"], CHANGES[k]), f"(b) set {k + 1}: echoed {echo['value']}, not {CHANGES[k]:g}")
        measured, aside, failures = [], [], []
        for k, t_send in enumerate(sends):
            row = {name: onset(cap[0][:, 0], cap[1], QUANTUM, t_send, CHANGES[k]) for name, cap in captures.items()}
            set_aside, failed = [], []
            for name, r in row.items():
                if r["kind"] == "aside":
                    set_aside.append(f"{name}: {r['problem']}")
                elif r["kind"] == "change":
                    seen = [what for t, what in xruns[name] if t_send - 0.7 <= t <= t_send + 1.0]
                    if seen:
                        set_aside.append(f"{name}: {r['problem']}, with {seen[0]} recorded there (pw-profiler)")
                    else:
                        failed.append(f"{name}: {r['problem']}, and pw-profiler recorded no xrun or late cycle there")
            if failed:
                failures.append(f"(b) set {k + 1} ({CHANGES[k]:+g} dB): " + "; ".join(failed))
                continue
            if set_aside:
                aside.append(f"set {k + 1}: " + "; ".join(set_aside))
                print(f"\n  (b) set {k + 1}: set aside: {'; '.join(set_aside)}", end="")
                continue
            missing = [name for name, r in row.items() if r["latency"] is None]
            if missing:
                failures.append(f"(b) set {k + 1} ({CHANGES[k]:+g} dB): not heard on {', '.join(missing)} within a second")
                continue
            latency = {name: r["latency"] for name, r in row.items()}
            measured.append(latency)
            print(f"\n  (b) set {k + 1} to {CHANGES[k]:+g} dB: left {1000 * latency['left']:.2f} ms, right "
                  f"{1000 * latency['right']:.2f} ms after the send, echo {lines[n['echoes'][k]][1].get('link')}", end="")
            for name, value in latency.items():
                if not -0.001 < value < 0.060:
                    failures.append(f"(b) set {k + 1}: the {name} capture changed {1000 * value:.2f} ms after the send, "
                                    "not within 0 to 60 ms")
        for f in failures:
            self.check(False, f)
        self.check(len(measured) >= AT_LEAST, f"(b) {len(measured)} of {len(sends)} sets measured, at least {AT_LEAST} "
                                              "must be; set aside:\n  " + "\n  ".join(aside))

    def check_drag(self, pair, captures, lines, n, refs):
        t_line, drag = lines[n["drag"]]
        start, end = t_line - drag["ms"] / 1000.0, t_line
        for name, cap in captures.items():
            before = self.level(cap, start - 0.6, start - 0.05)
            after = self.level(cap, end + 0.3, end + 1.3)
            x = self.segment(cap, start, end)
            block = int(0.02 * RATE)
            levels = [sine_amplitudes_dbfs(x[i:i + block], [FREQ])[0] for i in range(0, len(x) - block + 1, block)]
            low, high = min(before, after), max(before, after)
            inside = sum(low + 0.5 < v < high - 0.5 for v in levels)
            print(f"\n  (b) the drag on the {name}: {before:.2f} -> {after:.2f} dBFS at 200 Hz, {inside} of {len(levels)} "
                  f"20 ms blocks in between; the render at {DRAG_TO:+g} dB: {refs[DRAG_TO][name]:.2f}", end="")
            self.check(inside >= 10, f"(b) the drag on the {name}: {inside} blocks between its ends' levels, not 10 or more")
            self.check(abs(after - refs[DRAG_TO][name]) <= 0.1,
                       f"(b) after the drag the {name}'s 200 Hz level is {after:.2f}, not the render's {refs[DRAG_TO][name]:.2f} "
                       "within 0.1 dB")
        self.check(same(lines[n["drag_echo"]][1]["value"], DRAG_TO), f"(b) the drag's echo: {lines[n['drag_echo']][1]}")

    # (c) ---------------------------------------------------------------------------------------

    def check_edits(self, pair, captures, lines, n, refs, new):
        for key, revision in (("add_applied", 2), ("disconnect_applied", 3), ("through_applied", 4), ("back_applied", 5)):
            obj = lines[n[key]][1]
            print(f"\n  (c) {lines[n[key] - 1][1]['line']}: {obj['outcome']}, {obj['ms']} ms after the edit", end="")
            self.check(obj["outcome"] == f"applied {revision}", f"(c) step {obj['step']}: {obj['outcome']}, not applied {revision}")
        self.check(same(lines[n["mode_echo"]][1]["value"], 1), f"(c) the new node's mode echo: {lines[n['mode_echo']][1]}")
        t_off = lines[n["disconnect_applied"]][0]
        t_back = lines[n["back_applied"]][0]
        for name, cap in captures.items():
            silent = self.segment(cap, t_off + 0.2, t_off + 1.2)
            peak = 20 * np.log10(max(float(np.max(np.abs(silent))), 1e-12))
            back = self.level(cap, t_back + 0.3, t_back + 1.3)
            print(f"\n  (c) {name}: the woofer peaks at {peak:.1f} dBFS after the disconnect, at {back:.2f} dBFS (200 Hz) "
                  f"through the new node, the render's {refs[DRAG_TO][name]:.2f}", end="")
            self.check(peak < -100.0, f"(c) {name}: the woofer is at {peak:.1f} dBFS after 16:0 -> 2:0 was disconnected")
            self.check(abs(back - refs[DRAG_TO][name]) <= 0.1,
                       f"(c) {name}: the woofer through the new node is {back:.2f}, not the render's {refs[DRAG_TO][name]:.2f} within 0.1 dB")
        want = sorted([(1, 2, 16, 0), (16, 0, 2, 1), (16, 0, new, 0), (new, 0, 2, 0)])
        graphs = self.wait_graphs(pair, lambda g: g.get("revision") == 5 and same(self.graph_value_of(g, 16, "s0_e0_gain"), DRAG_TO))
        for name, g in graphs.items():
            self.check(g.get("revision") == 5 and connections(g) == want,
                       f"(c) {name}'s graph.json: revision {g.get('revision')}, connections {connections(g)}, not 5 and {want}")
            self.check(same(self.graph_value_of(g, 16, "s0_e0_gain"), DRAG_TO) and same(self.graph_value_of(g, new, "mode"), 1),
                       f"(b, c) {name}'s graph.json: node 16 s0_e0_gain {self.graph_value_of(g, 16, 's0_e0_gain')}, "
                       f"node {new} mode {self.graph_value_of(g, new, 'mode')}, not {DRAG_TO:g} and 1")
        document, hub = self.hub_document()
        state = lines[n["state_end"]][1]["state"]
        self.check(hub["revision"] == 5 and state["revision"] == 5, f"(c) revision {hub['revision']} at the hub, {state['revision']} "
                                                                    "in the controller, not 5")
        self.check(differences(state, document) == [], f"(c) the controller's state is not the hub's: {differences(state, document)}")
        self.check(same(self.graph_value_of(document, 16, "s0_e0_gain"), DRAG_TO),
                   f"(b) the hub's state has s0_e0_gain {self.graph_value_of(document, 16, 's0_e0_gain')}, not {DRAG_TO:g}")

    def refusal_run(self, pair):
        """(c) A proposal the right must refuse: its device.json turns the tweeter on."""
        device = pair.state["right"] / "device.json"
        original = device.read_text()
        device.write_text(json.dumps(dict(json.loads(original), limits=NO_LIMITS), indent=2))
        try:
            s, n = ControllerScript(), {}
            s.add("wait-synced 15")
            s.add("expect-revision 5")
            s.add("connect-pins 1:0 2:1")
            n["rejected"] = s.add("expect-rejected right: the tweeter is on here (device.json)")
            s.add("expect-no-connection 1:0 2:1")
            s.add("expect-revision 5")
            n["state"] = s.add("state")
            mark = pair.ampd["left"].mark()
            with ScriptedController(s, name="ctl-e2e-2") as ctl:
                status, result = ctl.finish(timeout=60)
                lines = {obj["step"]: obj for _, obj in ctl.steps()}
                self.assertEqual((status, result and result.get("result")), (0, "pass"), ctl.report("the refusal script failed"))
            print(f"\n  (c) the tweeter on at the right, 1:0 -> 2:1: {lines[n['rejected']]['outcome'][:120]}", end="")
            pair.ampd["left"].wait_for("revision 6: rolling back to revision 5 on left", timeout=10, after=mark)
            graphs = self.wait_graphs(pair, lambda g: g.get("revision") == 5)
            self.check({name: g.get("revision") for name, g in graphs.items()} == {"left": 5, "right": 5},
                       f"(c) after the rejection graph.json: left {graphs['left'].get('revision')}, right {graphs['right'].get('revision')}")
            document, hub = self.hub_document()
            self.check(hub["revision"] == 5, f"(c) the hub is on revision {hub['revision']} after the rejection")
            self.check(differences(lines[n["state"]]["state"], document) == [],
                       f"(c) after the rejection the controller's state is not the hub's: "
                       f"{differences(lines[n['state']]['state'], document)}")
        finally:
            device.write_text(original)

    # (e) ---------------------------------------------------------------------------------------

    def wrong_secret_run(self, pair):
        s = ControllerScript()
        s.add("sleep 2500")
        k = s.add("state")
        mark = pair.ampd["left"].mark()
        with ScriptedController(s, name="ctl-e2e-wrong", secret=WRONG_SECRET) as ctl:
            status, result = ctl.finish(timeout=30)
            lines = {obj["step"]: obj for _, obj in ctl.steps()}
            printed = json.dumps([obj for _, obj in ctl.lines]) + ctl.stderr_path.read_text(errors="replace")
            self.assertEqual((status, result and result.get("result")), (0, "pass"), ctl.report("the wrong secret's script failed"))
        state = lines[k]["state"]
        plugins = [x["uid"] for x in state["nodes"] if x.get("kind") in ("control", "placeholder")]
        print(f"\n  (e) another secret: proven {state['proven']}, synced {state['synced']}, revision {state['revision']}, "
              f"refused {state['refused']!r}, status {state['status']!r}", end="")
        self.check(not state["proven"] and not state["synced"] and state["refused"] and state["revision"] == 0 and not plugins,
                   f"(e) with another secret the controller's state: proven {state['proven']}, synced {state['synced']}, "
                   f"refused {state['refused']!r}, revision {state['revision']}, plugin nodes {plugins}")
        logged = pair.ampd["left"].wait_for("refused ", timeout=5, after=mark, required=False)
        print(f"\n  (e) the hub: {logged}", end="")
        self.check(logged is not None and "does not prove the pair's secret" in logged,
                   f"(e) the hub did not log the refusal: {pair.ampd['left'].lines[mark:]}")
        for secret in (TEST_SECRET, WRONG_SECRET):
            self.check(secret not in printed, "(e) a secret appears in what the controller printed")

    # (f) ---------------------------------------------------------------------------------------

    def gui_run(self, pair):
        steps = []
        for _ in range(20):
            steps += [(-30.0, 0.8), (-12.0, 0.8)]
        left = np.concatenate([tone(FREQ, level, seconds) for level, seconds in steps])
        right = np.concatenate([tone(FREQ, level - (LEFT - RIGHT), seconds) for level, seconds in steps])
        x = np.stack([left, right], axis=1)
        juce_png, screen_png = OUT / "controller-juce.png", OUT / "controller-screen.png"
        for f in (juce_png, screen_png):
            f.unlink(missing_ok=True)
        s, n = ControllerScript(), {}
        s.add("main-window 0 0 1040 720")
        s.add("wait-synced 15")
        s.add("expect-speaker right online")
        s.add("output-levels on")
        s.add("open 16 1060 30")
        n["mon"] = [s.add("wait-value left node/16/mon_levelDb")]
        for _ in range(8):
            s.add("sleep 300")
            n["mon"].append(s.add("wait-value left node/16/mon_levelDb"))
        s.add(f"screenshot {juce_png}")
        n["capture"] = s.add("print capture")
        s.add("sleep 3000")
        n["state"] = s.add("state")
        with Player(pair["left"], x, target="speakerd"), Player(pair["right"], x, target="speakerd"), \
                VirtualDisplay() as display, \
                ScriptedController(s, name="ctl-e2e-gui", args=["--gui"], env=display.env) as ctl:
            ctl.step(n["capture"], timeout=60)
            display.screenshot(screen_png)
            status, result = ctl.finish(timeout=60)
            lines = {obj["step"]: obj for _, obj in ctl.steps()}
            self.assertEqual((status, result and result.get("result")), (0, "pass"), ctl.report("the GUI script failed"))
        values = [float(lines[k]["v"]) for k in n["mon"]]
        state = lines[n["state"]]["state"]
        speakers = {sp["name"]: sp for sp in state["speakers"]}
        print(f"\n  (f) mon_levelDb in the window: " + ", ".join(f"{v:.1f}" for v in values) + "; levels "
              + ", ".join(f"{name} {sp['level'][0]:.1f} dBFS ({sp['level_count']} values)" for name, sp in speakers.items()), end="")
        self.check(state["synced"], "(f) the GUI run is not synced")
        self.check(max(values) - min(values) > 6.0, f"(f) mon_levelDb did not move: {values}")
        for name in ("left", "right"):
            sp = speakers.get(name, {})
            self.check(sp.get("monitor_connected") and sp.get("level_count", 0) > 0 and sp.get("level", [-120])[0] > -60.0,
                       f"(f) {name} in the GUI run: {sp}")
        for png in (juce_png, screen_png):
            if not png.exists():
                self.check(False, f"(f) no {png}")
                continue
            info = subprocess.run(["identify", "-format", "%m %w %h %[fx:standard_deviation]", str(png)],
                                  capture_output=True, text=True, timeout=30).stdout.split()
            print(f"\n  (f) {png}: {' '.join(info)}", end="")
            self.check(len(info) == 4 and info[0] == "PNG" and float(info[3]) > 0.01, f"(f) {png} is {info}")


if __name__ == "__main__":
    unittest.main()
