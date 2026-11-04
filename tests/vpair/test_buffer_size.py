"""buffer_size (docs/autonomous-development.md, section 4): the controller chooses the speakers'
PipeWire buffer size (the quantum), and both speakers run it, or neither.

The size is the chain document's top-level `quantum` (docs/protocol.md, "The buffer size"): a
power of two from 64 to 2048, or none (PipeWire's own default). It is part of the revision, so
the hub applies it on both speakers or on neither, and graph.json keeps it. speakerd forces it
with the settings metadata's `clock.force-quantum` (0 for a document with none) whenever a
document comes into force, and tells its ampd the quantum it runs, which the hub gives every
controller in `state.speakers`.

Both virtual speakers run the real speakerd and ampd (the left the hub, the right its agent) on
revision 1: the headless loudness forced to its first setting (+12 dB shelf at 120 Hz) on both
amplifier channels, as test_speakerd_quantum_change's document; each plays a 100 Hz tone at
-20 dBFS into speakerd. The vpair's speaker.conf has default.clock.quantum 1024, min-quantum 32 and
no max-quantum (PipeWire's 2048), so each speakerd accepts 64 to 2048. The harness's controller
(harness.Controller) proposes the revisions; the reference gain is speakerd's render of the
same document through the complete build. device.json has no limit and the tweeter on in
TheChosenSize, so the gain is checked on both channels; the other scenarios mute the tweeter, as
the step 3 scenarios do.

Pass when:

- at start every speaker of the hub's state says rate 48000, quantum 1024 (speaker.conf's
  default), quantum_range [64, 2048] and quantum_default 1024;
- quantum 256 proposed: applied; on both speakers speakerd says `quantum: revision 2 asks for 256:
  clock.force-quantum 256` and `pipewire: the quantum changed to 256` (within 10 s),
  `pw-metadata -n settings` shows clock.force-quantum 256, the hub's state says quantum 256, and the
  amplifier gets the tone with the render's gain within 0.05 dB on both channels (woofer and
  tweeter);
- 2048, then no `quantum` (the default): both run 2048 (force-quantum 2048), then 1024, with
  force-quantum 0 and speakerd saying `quantum: revision 4 asks for none: clock.force-quantum 0
  (PipeWire's default, 1024)`;
- 4096, 100 and 48 are rejected by the hub with `quantum <v>: a power of two from 64 to 2048`, and
  nothing changes on either speaker: the revision stays 4, force-quantum 0, quantum 1024 in the
  state, and no speakerd says a quantum line after them (1 s);
- 256 again (revision 5), proposed as `256.0` (a whole JSON number with no fraction, which the hub
  and speakerd accept): applied, both speakers say `quantum: revision 5 asks for 256:
  clock.force-quantum 256` and run 256 (force-quantum 256, the state); then each speakerd stopped, clock.force-quantum set back to 0 by hand and
  speakerd started again on graph.json: it forces 256 again (its log line, pw-metadata, the state),
  on both; then each speaker's PipeWire restarted (vpair down and up: speakerd exits with status 3,
  as it does when it loses PipeWire, and is started again on graph.json): the same on both, on a
  PipeWire that never had a quantum forced;
- a size within the hub's shape but outside one speaker's PipeWire (OutsideOneSpeakersRange: the
  right runs with max-quantum 1024, so its state says quantum_range [64, 1024]; 2048 proposed): the
  hub rejects it with `right: quantum 2048: not a power of two from 64 to 1024 (what this speaker's
  PipeWire allows)` and rolls the left back to revision 1; afterwards, on both, the revision is 1,
  clock.force-quantum 0, quantum 1024 (state), and graph.json revision 1 without a quantum; the
  right's speakerd never said a quantum change after the proposal;
- the real controller (TheRealController, `built("controller")`, its script test mode as
  test_controller_end_to_end runs it): `buffer-size 256`, `expect-applied`, `expect-buffer-size
  left 256`, `expect-buffer-size right 256` pass, and then both speakers have clock.force-quantum 256
  and graph.json quantum 256; `buffer-size default` brings both back to 1024 with force-quantum 0
  and no quantum in graph.json; the script ends with `pass`. Skipped, saying why, when the
  controller does not build.

It needs the speakerd that knows the buffer size (docs/protocol.md: hello's `quantum_range`), and
step 3's speakerd (the local socket, 0.3.0): with an older one it fails, saying so.

    python3 -m unittest -v tests.vpair.test_buffer_size
"""

import json
import re
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from harness import (HERE, NO_LIMITS, Controller, ControllerScript, Player, Recorder, ScriptedController, SpeakerPair, active,
                     built, lv2_uri, render, require_speakerd_local_link, revision_document, rms_dbfs, state_dir,
                     steady, tone, with_plugin)
from test_speakerd_quantum_change import DOCUMENT as QUANTUM_DOCUMENT, RECORD_SECONDS, TOLERANCE_DB, TONE, gains

# What this module's built() calls need, also through the harness (Speakerd, render, SpeakerPair,
# ScriptedController): tests/vpair/pool.py builds them before any test runs (README.md, "The pool").
POOL_BUILDS = ("speakerd", "loudness:headless", "loudness:complete", "controller?")

DOCUMENT = revision_document(QUANTUM_DOCUMENT, 1)
STREAM = tone(100.0, -20.0, 240.0)   # longer than any part of the scenario: stopped at its end
RATE = 48000
SPEAKER_DEFAULT = int(re.search(r"(?m)^\s*default\.clock\.quantum\s*=\s*(\d+)",
                                (HERE / "speaker.conf").read_text()).group(1))
RANGE = [64, 2048]
OUTSIDE = (4096, 100, 48)            # above the range, not a power of two, below it
NARROW = 1024                        # the right's max-quantum in OutsideOneSpeakersRange
WAIT = 10.0                          # for a quantum line, the metadata and the state
NAMES = ("left", "right")


def sized(quantum):
    """Revision 1's document with `quantum` (None: without the field), for a proposal."""
    doc = json.loads(json.dumps(QUANTUM_DOCUMENT))
    doc.pop("revision", None)
    if quantum is not None:
        doc["quantum"] = quantum
    return doc


def speakers_of(state):
    return {entry["name"]: entry for entry in state["speakers"]}


class BufferSize:
    """The render's gain (setUpClass) and the checks the scenarios share."""

    @classmethod
    def setUpClass(cls):
        cls.headless = built("loudness", "headless")
        complete = built("loudness", "complete")
        built("speakerd")
        cls.tmp = Path(tempfile.mkdtemp(prefix="vpair-size-"))
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)
        state = state_dir(cls.tmp / "reference", current=with_plugin(DOCUMENT, lv2_uri("loudness")))
        output, log = render(state, TONE, bundles=[complete])
        if "layer current (graph.json)" not in log:
            raise AssertionError(f"the render did not run the document:\n{log}")
        cls.reference = rms_dbfs(steady(active(output))[:, 0]) - rms_dbfs(steady(TONE))

    def setUp(self):
        require_speakerd_local_link(self)

    # What a speaker runs -----------------------------------------------------------------------

    def wait_state(self, ctl, predicate, after, what, timeout=WAIT):
        return ctl.wait(lambda m: m["op"] == "state" and predicate(speakers_of(m)), timeout, what, after)

    def wait_forced(self, pair, name, forced, timeout=WAIT):
        """Waits until `name`'s clock.force-quantum is `forced`; fails with what it is."""
        deadline = time.monotonic() + timeout
        while True:
            now = pair[name].forced_quantum()
            if now == forced:
                return
            if time.monotonic() > deadline:
                raise AssertionError(f"{name}: clock.force-quantum is {now}, not {forced}, {timeout:g} s on\n"
                                     f"{pair.speakerd[name].log()}")
            time.sleep(0.1)

    def check_runs(self, pair, ctl, quantum, forced, marks, ctl_mark, revision=None):
        """Both speakers run `quantum` with clock.force-quantum `forced`: speakerd's lines (from
        `marks`), the metadata and the hub's state (from `ctl_mark`)."""
        for name in NAMES:
            sd = pair.speakerd[name]
            if revision is not None:
                said = (f"quantum: revision {revision} asks for {forced}: clock.force-quantum {forced}" if forced else
                        f"quantum: revision {revision} asks for none: clock.force-quantum 0 "
                        f"(PipeWire's default, {SPEAKER_DEFAULT})")
                self.assertEqual(sd.wait_for(said, timeout=WAIT, after=marks[name]), f"speakerd: {said}", sd.log())
            print(f"\n  {name}: " + sd.wait_for(f"pipewire: the quantum changed to {quantum}", timeout=WAIT,
                                               after=marks[name])[len("speakerd: "):], end="")
            self.wait_forced(pair, name, forced)
        self.wait_state(ctl, lambda s: all(s[n]["quantum"] == quantum for n in NAMES), ctl_mark,
                        f"a state with both speakers at quantum {quantum}")

    def check_gain(self, speaker, when):
        with Recorder(speaker, "amplifier", fmt="f32") as recorder:
            time.sleep(RECORD_SECONDS)
            capture = recorder.stop()
        live = gains(capture)
        summary = (f"{speaker.name}, {when}: woofer {live[0]:+.3f} dB, tweeter {live[1]:+.3f} dB, "
                   f"complete build {self.reference:+.3f} dB")
        print(f"\n  {summary}", end="")
        for channel, gain in zip(("woofer", "tweeter"), live):
            self.assertAlmostEqual(gain, self.reference, delta=TOLERANCE_DB, msg=f"{channel}: {summary}")

    def propose(self, pair, ctl, quantum, expect="applied"):
        """Proposes `quantum` and returns (the answer, the speakerd log marks, the controller's
        mark) from before it."""
        marks = {name: pair.speakerd[name].mark() for name in NAMES}
        ctl_mark = ctl.mark()
        answer = ctl.propose(sized(quantum))
        print(f"\n  quantum {quantum}: {answer['op']}" + (f": {answer['reason']}" if "reason" in answer else ""), end="")
        self.assertEqual(answer["op"], expect, answer)
        return answer, marks, ctl_mark

    def quantum_lines(self, sd, after):
        return sd.lines_starting("quantum: ", after) + sd.lines_starting("pipewire: the quantum changed", after)


class TheChosenSize(BufferSize, unittest.TestCase):
    def test_both_speakers_run_the_documents_size(self):
        tmp = self.tmp / "pair"
        # No limit, the tweeter on: the gain is checked on both channels, as in the render.
        with SpeakerPair(tmp, {name: DOCUMENT for name in NAMES}, [self.headless],
                         limits={name: NO_LIMITS for name in NAMES}) as pair, \
                Controller("ctl-size") as ctl:
            with Player(pair["left"], STREAM, target="speakerd"), Player(pair["right"], STREAM, target="speakerd"):
                start = self.wait_state(ctl, lambda s: all(s[n]["quantum"] == SPEAKER_DEFAULT for n in NAMES), 0,
                                        "a state with both speakers running")
                for name, entry in speakers_of(start).items():
                    self.assertEqual({k: entry.get(k) for k in ("rate", "quantum", "quantum_range", "quantum_default")},
                                     {"rate": RATE, "quantum": SPEAKER_DEFAULT, "quantum_range": RANGE,
                                      "quantum_default": SPEAKER_DEFAULT}, name)

                # 256, and the sound is the render's.
                _, marks, ctl_mark = self.propose(pair, ctl, 256)
                self.check_runs(pair, ctl, 256, 256, marks, ctl_mark, revision=2)
                for name in NAMES:
                    self.check_gain(pair[name], "quantum 256")

                # 2048, then the default.
                _, marks, ctl_mark = self.propose(pair, ctl, 2048)
                self.check_runs(pair, ctl, 2048, 2048, marks, ctl_mark, revision=3)
                _, marks, ctl_mark = self.propose(pair, ctl, None)
                self.check_runs(pair, ctl, SPEAKER_DEFAULT, 0, marks, ctl_mark, revision=4)

                # Outside the shape: the hub rejects it, nothing changes anywhere.
                marks = {name: pair.speakerd[name].mark() for name in NAMES}
                for bad in OUTSIDE:
                    answer, _, _ = self.propose(pair, ctl, bad, expect="rejected")
                    self.assertEqual(answer["reason"], f"quantum {bad}: a power of two from 64 to 2048")
                time.sleep(1.0)
                self.assertEqual(ctl.current_revision(), 4)
                for name in NAMES:
                    self.assertEqual(pair[name].forced_quantum(), 0, name)
                    self.assertEqual(self.quantum_lines(pair.speakerd[name], marks[name]), [], name)
                self.assertEqual({n: e["quantum"] for n, e in speakers_of(ctl.last_state()).items()},
                                 {n: SPEAKER_DEFAULT for n in NAMES})

                # 256 again, written 256.0 (a whole JSON number, of the valid shape: the hub and
                # speakerd take it as 256); then each speakerd restarted on graph.json, the metadata
                # reset by hand.
                _, marks, ctl_mark = self.propose(pair, ctl, 256.0)
                self.check_runs(pair, ctl, 256, 256, marks, ctl_mark, revision=5)
                for name in NAMES:
                    pair.speakerd[name].stop()
                    pair[name].set_quantum(0)
                    self.assertEqual(pair[name].forced_quantum(), 0)
                    ctl_mark = ctl.mark()
                    sd = pair.start_speakerd(name)
                    self.restored(pair, ctl, name, sd, ctl_mark, "after a speakerd restart")

            # Each PipeWire restarted: speakerd exits with status 3 and starts again on graph.json.
            for name in NAMES:
                old = pair.speakerd[name]
                old.exit_status = 3
                pair.pair.down(name)
                self.assertEqual(old.wait_exit(timeout=15), 3, old.log())
                pair.pair.up(name)
                self.assertEqual(pair[name].forced_quantum(), 0, f"{name}: a new PipeWire has a quantum forced")
                ctl_mark = ctl.mark()
                sd = pair.start_speakerd(name)
                with Player(pair[name], STREAM, target="speakerd"):
                    self.restored(pair, ctl, name, sd, ctl_mark, "after a PipeWire restart")
            for name in NAMES:
                graph = pair.graph(name)
                self.assertEqual((graph["revision"], graph.get("quantum")), (5, 256), name)

    def restored(self, pair, ctl, name, sd, ctl_mark, when):
        said = "quantum: revision 5 asks for 256: clock.force-quantum 256"
        self.assertEqual(sd.wait_for(said, timeout=WAIT), f"speakerd: {said}", sd.log())
        self.wait_forced(pair, name, 256)
        self.wait_state(ctl, lambda s: s[name]["online"] and s[name]["quantum"] == 256, ctl_mark,
                        f"a state with {name} at quantum 256 {when}")
        print(f"\n  {name}: quantum 256 again {when}", end="")


class OutsideOneSpeakersRange(BufferSize, unittest.TestCase):
    def test_a_size_one_speaker_refuses_changes_neither(self):
        # speaker.conf with max-quantum 1024 for the right: its speakerd accepts 64 to 1024.
        conf = HERE / "speaker.conf"
        text, n = re.subn(r"(?m)^(\s*)default\.clock\.quantum(\s*)=.*$",
                          rf"\g<0>\n\g<1>default.clock.max-quantum\g<2>= {NARROW}", conf.read_text())
        if n != 1:
            raise AssertionError("speaker.conf has no single default.clock.quantum")
        narrow = self.tmp / "speaker-narrow.conf"
        narrow.write_text(text)
        with SpeakerPair(self.tmp / "narrow", {name: DOCUMENT for name in NAMES}, [self.headless],
                         setup={"right": {"speaker_conf": narrow}}) as pair, Controller("ctl-narrow") as ctl, \
                Player(pair["left"], STREAM, target="speakerd"), Player(pair["right"], STREAM, target="speakerd"):
            start = self.wait_state(ctl, lambda s: all(s[n]["quantum"] == SPEAKER_DEFAULT for n in NAMES), 0,
                                    "a state with both speakers running")
            self.assertEqual({n: e["quantum_range"] for n, e in speakers_of(start).items()},
                             {"left": RANGE, "right": [64, NARROW]})

            answer, marks, ctl_mark = self.propose(pair, ctl, 2048, expect="rejected")
            self.assertEqual(answer["reason"], f"right: quantum 2048: not a power of two from 64 to {NARROW} "
                                               "(what this speaker's PipeWire allows)")
            pair.ampd["left"].wait_for("revision 2: rolling back to revision 1 on left")
            said = (f"quantum: revision 1 asks for none: clock.force-quantum 0 (PipeWire's default, "
                    f"{SPEAKER_DEFAULT})")
            pair.speakerd["left"].wait_for(said, timeout=WAIT, after=marks["left"])
            self.wait_forced(pair, "left", 0)
            # The left may have run 2048 for a moment: its state comes back to 1024.
            deadline = time.monotonic() + WAIT
            while speakers_of(ctl.last_state())["left"]["quantum"] != SPEAKER_DEFAULT and time.monotonic() < deadline:
                time.sleep(0.1)
            time.sleep(1.0)
            self.assertEqual(ctl.current_revision(), 1)
            self.assertEqual({n: e["quantum"] for n, e in speakers_of(ctl.last_state()).items()},
                             {n: SPEAKER_DEFAULT for n in NAMES})
            for name in NAMES:
                self.assertEqual(pair[name].forced_quantum(), 0, name)
                graph = pair.graph(name)
                self.assertEqual((graph["revision"], "quantum" in graph), (1, False), name)
            right = pair.speakerd["right"]
            self.assertEqual(right.lines_starting("pipewire: the quantum changed", marks["right"]), [], right.log())


class TheRealController(BufferSize, unittest.TestCase):
    def test_the_controller_chooses_the_size(self):
        try:
            built("controller")
        except RuntimeError as e:
            self.skipTest(f"the controller is not built here, so its buffer-size step cannot run: {e}")
        s, n = ControllerScript(), {}
        s.add("wait-synced 15")
        s.add("buffer-size 256")
        n["applied"] = s.add("expect-applied")
        s.add("expect-buffer-size left 256")
        n["right_256"] = s.add("expect-buffer-size right 256")
        s.add("state")
        s.add("buffer-size default")
        s.add("expect-applied")
        s.add(f"expect-buffer-size left {SPEAKER_DEFAULT}")
        s.add(f"expect-buffer-size right {SPEAKER_DEFAULT}")
        s.add("state")
        with SpeakerPair(self.tmp / "controller", {name: DOCUMENT for name in NAMES}, [self.headless]) as pair, \
                Player(pair["left"], STREAM, target="speakerd"), Player(pair["right"], STREAM, target="speakerd"):
            with ScriptedController(s, name="ctl-size") as ctl:
                ctl.step(n["right_256"], timeout=60)
                for name in NAMES:
                    self.wait_forced(pair, name, 256)
                    self.assertEqual(pair.graph(name).get("quantum"), 256, name)
                status, result = ctl.finish(timeout=120)
                self.assertEqual((status, result and result.get("result")), (0, "pass"),
                                 ctl.report("the buffer-size script failed"))
            for name in NAMES:
                self.wait_forced(pair, name, 0)
                self.assertNotIn("quantum", pair.graph(name), name)
            print("\n  the controller chose 256, then the default, on both speakers", end="")


if __name__ == "__main__":
    unittest.main()
