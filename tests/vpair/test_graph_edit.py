"""graph_edit (docs/autonomous-development.md, section 5): a new revision with a node and a
connection added changes both captures, with no gap longer than the fade and no click above a
threshold; a revision one speaker fails is rejected and rolled back on the other.

Both virtual speakers run speakerd and ampd (the left the hub) on revision 1: loudness_headless
(node 16, flat) between the stream and both amplifier channels. Each plays a steady 200 Hz tone at
-20 dBFS, its amplifier recorded as floats (harness.TimedRecorder). A harness controller proposes
revision 2: a second loudness_headless (node 17, Force 1, its first shelf at 1 kHz, +12 dB) and the
connection 16 -> 17 that puts it in the chain. In each capture:

- the change is the level after the hub said applied minus the level before the proposal;
- a gap is a run of 1 ms blocks more than 40 dB under the quieter of the two levels; the fade is
  speakerd's 30 ms (its README: a fade-out, the swap, a fade-in);
- a click is what the capture has above 4 kHz (harness.high_band) from the proposal to 0.3 s after
  applied, relative to the louder tone; a 200 Hz tone through shelving filters has nothing there,
  a step of 1 % of the tone is -40 dB.

Then a revision one speaker fails, twice:

- the right speaker does not answer (its ampd is stopped with SIGSTOP), which docs/protocol.md
  counts as failing after 10 s; revision 3 (node 17 at -6 dB) plays on the left, and when the hub
  rejects it the left plays revision 2 again; when the right's ampd goes on (SIGCONT), it applies
  revision 3 late and the hub sends it revision 2 again;
- the right speaker says failed: there the right speaker is a stand-in speakerd that fails
  revision 2 (tests/vpair/stand_in_speakerd.py), the left a real one. (A real speakerd that fails
  one speaker's revision only would need a plugin the hub has seen loaded and that speaker lacks,
  which the step 4 catalogue is for: the hub refuses a plugin no speaker has loaded.)

Pass when:

- both captures change by more than 6 dB (the shelf gives about +12 at 200 Hz), with no gap longer
  than 30 ms and nothing above 4 kHz louder than -40 dB under the tone;
- the silent right: the hub rejects revision 3 with "right: no answer within 10 s", the left's
  level under revision 3 differs from revision 2's by more than 6 dB and is back within 0.5 dB of
  it after the roll back, and the right ends on revision 2 (its graph.json and the hub's state);
- the failing right: the hub rejects revision 2 with "right: <the stand-in's reason>", the left
  changes and comes back within 0.5 dB of revision 1's level, and the hub's revision stays 1.

It needs step 3's speakerd (the local socket, 0.3.0): with an older one it fails, saying so.

    python3 -m unittest -v tests.vpair.test_graph_edit
"""

import json
import shutil
import signal
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from harness import (RATE, TWEETER_MUTED, Ampd, Controller, Pair, Player, SpeakerPair, Speakerd, StandInSpeakerd, TimedRecorder,
                     block_levels, built, graph_document, high_band, longest_run, lv2_uri, require_speakerd_local_link,
                     revision_document, rms_dbfs, state_dir, tone)

HEADLESS = lv2_uri("loudness_headless")
FREQ, LEVEL = 200.0, -20.0
FLAT = {"mode": "Force 1", "s0_e0_freq": 1000.0, "s0_e0_gain": 0.0, "glide": 1.0}
DOCUMENT = revision_document(graph_document(HEADLESS, FLAT), 1)


def chained(gain_db):
    """Revision 1 with node 17 (+`gain_db` at 200 Hz, about) after node 16: a node and a connection."""
    doc = graph_document(HEADLESS, FLAT)
    doc.pop("revision")
    doc["nodes"].append({"uid": 17, "plugin": {"format": "LV2", "identifier": HEADLESS},
                         "params": dict(FLAT, s0_e0_gain=gain_db)})
    doc["connections"] = [
        {"src": {"node": 1, "channel": 0}, "dst": {"node": 16, "channel": 0}},
        {"src": {"node": 16, "channel": 0}, "dst": {"node": 17, "channel": 0}},
        {"src": {"node": 17, "channel": 0}, "dst": {"node": 2, "channel": 0}},
        {"src": {"node": 17, "channel": 0}, "dst": {"node": 2, "channel": 1}},
    ]
    return doc


def level(x, t0, start, end):
    """The RMS level of the woofer capture between two times (time.monotonic())."""
    a, b = int((start - t0) * RATE), int((end - t0) * RATE)
    return rms_dbfs(x[max(a, 0):b, 0])


def edit_measures(x, t0, proposed, applied, end):
    before = level(x, t0, proposed - 1.0, proposed - 0.1)
    after = level(x, t0, applied + 0.4, end - 0.2)
    a, b = int((proposed - t0) * RATE), int((applied + 0.3 - t0) * RATE)
    blocks = block_levels(x[a:b, 0], 0.001)
    gap_ms = longest_run(blocks < min(before, after) - 40.0)
    hf = high_band(x[:, 0])
    tone_peak = 10 ** (max(before, after) / 20) * np.sqrt(2)
    click_db = 20 * np.log10(max(float(np.max(np.abs(hf[a:b]))), 1e-12) / tone_peak)
    steady_db = 20 * np.log10(max(float(np.max(np.abs(hf[int((applied + 0.5 - t0) * RATE):int((end - 0.2 - t0) * RATE)]))),
                                  1e-12) / tone_peak)
    return before, after, gap_ms, click_db, steady_db


class GraphEdit(unittest.TestCase):
    def setUp(self):
        require_speakerd_local_link(self)
        self.tmp = Path(tempfile.mkdtemp(prefix="vpair-edit-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_an_edit_on_both_and_a_rejected_one(self):
        bundles = [built("loudness", "headless")]
        with SpeakerPair(self.tmp, {"left": DOCUMENT, "right": DOCUMENT}, bundles) as pair, Controller("ctl-edit") as ctl, \
                Player(pair["left"], tone(FREQ, LEVEL, 60.0), target="speakerd"), \
                Player(pair["right"], tone(FREQ, LEVEL, 60.0), target="speakerd"):
            recorders = {name: TimedRecorder(pair[name]).start() for name in ("left", "right")}
            try:
                time.sleep(1.5)
                proposed = time.monotonic()
                answer = ctl.propose(chained(12.0))
                applied = time.monotonic()
                self.assertEqual(answer["op"], "applied", answer)
                time.sleep(2.0)
                end = time.monotonic()

                # The right does not answer: SIGSTOP on its ampd.
                pair.ampd["right"].signal(signal.SIGSTOP)
                proposed3 = time.monotonic()
                answer3 = ctl.propose(chained(-6.0), base=2)
                rejected3 = time.monotonic()
                time.sleep(2.5)
                end3 = time.monotonic()
                mark = pair.ampd["right"].mark()
                pair.ampd["right"].signal(signal.SIGCONT)
                pair.ampd["left"].wait_for("agent right: applied revision 3 after it was given up", timeout=20)
                pair.ampd["right"].wait_for("revision 2: applied", after=mark, timeout=20)
            finally:
                pair.ampd["right"].signal(signal.SIGCONT)
                captures = {name: r.stop() for name, r in recorders.items()}
            print(f"\n  revision 2 applied {applied - proposed:.2f} s after the proposal")
            for name, (x, t0) in captures.items():
                before, after, gap, click, steady = edit_measures(x, t0, proposed, applied, end)
                print(f"  {name}: {before:.2f} -> {after:.2f} dBFS ({after - before:+.2f} dB), longest gap {gap} ms, "
                      f"above 4 kHz {click:.1f} dB under the tone ({steady:.1f} dB steady)")
                self.assertGreater(after - before, 6.0, f"{name}: revision 2 did not change the capture")
                self.assertLessEqual(gap, 30, f"{name}: a gap of {gap} ms, longer than the 30 ms fade")
                self.assertLess(click, -40.0, f"{name}: a click {click:.1f} dB under the tone (threshold -40 dB)")

            # Revision 3, which the right did not answer.
            print(f"  revision 3: {answer3['op']} after {rejected3 - proposed3:.2f} s: {answer3.get('reason')}")
            self.assertEqual(answer3["op"], "rejected", answer3)
            self.assertEqual(answer3["reason"], "right: no answer within 10 s")
            x, t0 = captures["left"]
            r2 = level(x, t0, proposed3 - 1.0, proposed3 - 0.1)
            r3 = level(x, t0, rejected3 - 6.0, rejected3 - 0.2)
            back = level(x, t0, end3 - 0.8, end3 - 0.1)
            print(f"  left: revision 2 {r2:.2f}, revision 3 {r3:.2f}, rolled back {back:.2f} dBFS")
            self.assertGreater(abs(r3 - r2), 6.0, "the left did not play revision 3 while the right was silent")
            self.assertLess(abs(back - r2), 0.5, "the left did not come back to revision 2")
            time.sleep(0.5)
            self.assertEqual(pair.graph("right")["revision"], 2)
            self.assertEqual(pair.graph("left")["revision"], 2)
            with Controller("ctl-after") as after:
                self.assertEqual(after.state["revision"], 2)

    def test_a_revision_the_right_fails_is_rolled_back_on_the_left(self):
        with Pair() as pair:
            # The tweeter muted, as the other step 3 scenarios run (the document feeds it straight).
            left_state = state_dir(self.tmp / "left", role="left", current=DOCUMENT, limits=TWEETER_MUTED)
            right_state = state_dir(self.tmp / "right", role="right", current=DOCUMENT, limits=TWEETER_MUTED)
            reason = "loudness_headless did not load"
            with Speakerd(pair["left"], left_state, bundles=[built("loudness", "headless")]), \
                    StandInSpeakerd(pair["right"].env, right_state, {"fail": {"2": reason}}), \
                    Ampd(pair["left"].env, left_state, "left") as hub, \
                    Ampd(pair["right"].env, right_state, "right") as agent, \
                    Player(pair["left"], tone(FREQ, LEVEL, 30.0), target="speakerd"):
                agent.wait_for("hub: connected")
                hub.wait_for(("agent right: plays revision", "agent right: revision"))
                with Controller("ctl-fail") as ctl:
                    recorder = TimedRecorder(pair["left"]).start()
                    try:
                        time.sleep(1.5)
                        proposed = time.monotonic()
                        answer = ctl.propose(chained(12.0))
                        rejected = time.monotonic()
                        hub.wait_for("revision 2: rolling back to revision 1 on left")
                        time.sleep(2.5)
                        end = time.monotonic()
                    finally:
                        x, t0 = recorder.stop()
                    self.assertEqual(answer["op"], "rejected", answer)
                    self.assertEqual(answer["reason"], f"right: {reason}")
                    r1 = level(x, t0, proposed - 1.0, proposed - 0.1)
                    tried = np.max(block_levels(x[int((proposed - t0) * RATE):int((rejected + 0.5 - t0) * RATE), 0], 0.05))
                    back = level(x, t0, end - 0.8, end - 0.1)
                    print(f"\n  left: revision 1 {r1:.2f}, while revision 2 was tried up to {tried:.2f}, "
                          f"rolled back {back:.2f} dBFS; {answer['reason']}")
                    self.assertGreater(tried - r1, 6.0, "the left never played revision 2")
                    self.assertLess(abs(back - r1), 0.5, "the left did not come back to revision 1")
                    self.assertEqual(ctl.current_revision(), 1)
                    self.assertEqual(json.loads((left_state / "graph.json").read_text())["revision"], 1)


if __name__ == "__main__":
    unittest.main()
