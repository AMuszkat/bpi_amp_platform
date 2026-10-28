"""knob_latency (docs/autonomous-development.md, section 5): a parameter sent to the hub is audible
in both captures within 60 ms, and the two captures differ by less than 5 ms.

Both virtual speakers run speakerd (loudness_headless between the stream and both amplifier
channels: Force 1, its first shelf at 1 kHz, 0 dB, a 1 ms glide) and ampd, the left the hub, the
right its agent. Each plays a steady 200 Hz tone at -20 dBFS while its amplifier is recorded by a
harness.TimedRecorder, which knows when each sample was in the graph (time.monotonic(), from
when its chunks arrived in the second before and after each send). A harness controller sends the shelf's gain to the hub over OSC, as a
moving knob does, alternately +12 dB and 0 dB, ten times (and more, up to 30, while fewer than 5
can be measured, below); the time of each send is taken just before it. In each capture the change is the first sample that leaves the 200 Hz sine fitted to
the 0.5 s before the send by more than 5 % of its amplitude; its time is when the cycle it was
produced in was delivered to the recorder (at most a fraction of a millisecond late).

The speakers' PipeWire graphs run on independent clocks, so a value reaches each speakerd at the
same moment but takes effect at each one's next cycle: the two captures can differ by up to one
quantum (21.3 ms at the boards' 1024) whatever ampd does. So the whole criterion is checked at a
quantum of 128 (2.7 ms), where it measures ampd and speakerd rather than the phase of two clocks;
at the boards' quantum, 1024, the 60 ms is checked and the difference printed.

A change is set aside (said, and not measured) only on evidence that does not come from the
change itself: the recorder lost samples around the send (its time mapping moved), the capture
was not steady from 0.6 s before the send to the send, or pw-profiler recorded an xrun or a late
cycle for speakerd-dsp or the recorder in that window (harness.Profiler; the late-cycle count of
speakerd's `speakerd/timing` stream will replace it). A change that is not at its new level 3 ms
after it began, drops out in the 30 ms after it, or is not a clean change (steady again at a
level more than 6 dB away, the way the change goes, from 50 ms to 500 ms after it), with no xrun
or late cycle recorded there, is a FAILURE. Up to 30 changes are sent (10 at least), and at least
5 must be measured.

Pass when at least 5 changes are measured, and for every measured change:

- at quantum 128: each capture changes within 60 ms of the send (and not before it), and the two
  changes are less than 5 ms apart;
- at quantum 1024: each capture changes within 60 ms of the send (and not before it).

It needs step 3's speakerd (the local socket, 0.3.0): with an older one it fails, saying so.

    python3 -m unittest -v tests.vpair.test_knob_latency
"""

import shutil
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from harness import (RATE, Controller, Player, Profiler, SpeakerPair, TimedRecorder, block_levels, built, graph_document,
                     lv2_uri, require_speakerd_local_link, revision_document, speaker_conf_with, tone)

FREQ, LEVEL = 200.0, -20.0
PARAMS = {"mode": "Force 1", "s0_e0_freq": 1000.0, "s0_e0_gain": 0.0, "glide": 1.0}
DOCUMENT = revision_document(graph_document(lv2_uri("loudness_headless"), PARAMS), 1)
SENDS, MORE, AT_LEAST = 10, 30, 5    # changes sent, at most when too few are measurable, how many must be
CHANGES = [12.0, 0.0] * (MORE // 2)


def sine_fit(x, a, b):
    """The 200 Hz sine that fits x[a:b] best: (its samples over all of x, its amplitude)."""
    n = np.arange(len(x))
    basis = np.stack([np.sin(2 * np.pi * FREQ * n / RATE), np.cos(2 * np.pi * FREQ * n / RATE)], axis=1)
    c = np.linalg.lstsq(basis[a:b], x[a:b], rcond=None)[0]
    return basis @ c, float(np.hypot(*c))


def aside(why):
    """A problem that does not come from the change: the change is set aside, and said."""
    return {"latency": None, "problem": why, "kind": "aside"}


def broke(latency, why):
    """A problem that may come from the change: a failure, unless PipeWire recorded an xrun or a
    late cycle there."""
    return {"latency": latency, "problem": why, "kind": "change"}


def onset(x, recorder, quantum, t_send, change_db):
    """When the capture leaves the 200 Hz sine it played before `t_send`, and whether the
    capture around it is intact enough to measure: {"latency": seconds after the send or None,
    "problem": None or why not, "kind": "aside" (evidence that does not come from the change: the
    recorder lost samples, the capture was not steady before the send) or "change" (what the change
    itself could have done: a failure unless an xrun was recorded there)}. Intact means: steady from 0.6 s before the send to the send (no
    residual above 5 % of the tone), at its new level 3 ms after the change began (residual under
    10 %), no dropout in the 30 ms after the change (every 5 ms block within 6 dB
    of the quieter of the two levels), and steady again 50 ms to 500 ms after it, at a level that
    moved the way the change goes (by more than 6 dB), and the recording's time mapping the same
    in the second before the send and the second after it (within 1 ms: the recorder lost no
    samples there)."""
    t0, t0_after = recorder.local_t0(t_send)
    if t0 is None or t0_after is None:
        return aside("too few chunks recorded around the send")
    if abs(t0_after - t0) > 0.001:
        return aside(f"the recorder lost samples around the send (its time mapping moved {1000 * (t0_after - t0):+.1f} ms)")
    index = lambda t: int(round((t - t0) * RATE))
    a, b = index(t_send - 0.6), index(t_send - 0.1)
    if a < 0 or index(t_send + 1.6) > len(x):
        return aside("not recorded from 0.6 s before the send to 1.6 s after it")
    fit, amplitude = sine_fit(x, a, b)
    if amplitude < 1e-6:
        return aside("not steady before the send (silent)")
    residual = np.abs(x - fit)
    # Nothing before the send is the change (the mapping is good to a fraction of a millisecond).
    sent = index(t_send - 0.001)
    quiet = float(np.max(residual[a:sent])) / amplitude
    if quiet >= 0.05:
        return aside(f"not steady before the send (residual {quiet:.3f} of the tone)")
    after = np.nonzero(residual[sent:] > 0.05 * amplitude)[0]
    if len(after) == 0:
        return {"latency": None, "problem": None, "kind": None}
    i = sent + int(after[0])
    # The sample was produced in the cycle of `quantum` samples it is in, and that cycle's samples
    # were delivered together at its end, t0 + (end of the cycle) / RATE: when the change was in
    # the graph, or a fraction of a millisecond after (the cycle's processing).
    latency = t0 + ((i // quantum) + 1) * quantum / RATE - t_send
    c, d = i + int(0.05 * RATE), i + int(0.5 * RATE)
    new_fit, new_amplitude = sine_fit(x, c, d)
    moved = 20 * np.log10(max(new_amplitude, 1e-12) / amplitude)
    settled = float(np.max(np.abs(x[c:d] - new_fit[c:d]))) / max(new_amplitude, 1e-12)
    # The change is complete within 3 ms of its first sample (a 1 ms glide): a glitch followed by
    # the change later is not at the new level by then.
    e, f = i + int(0.003 * RATE), i + int(0.05 * RATE)
    early = float(np.max(np.abs(x[e:f] - new_fit[e:f]))) / max(new_amplitude, 1e-12)
    if early >= 0.1:
        return broke(latency, f"not at its new level 3 ms after it began (residual {early:.3f})")
    blocks = block_levels(x[i:i + int(0.03 * RATE)], 0.005)
    floor = 20 * np.log10(min(amplitude, new_amplitude) / np.sqrt(2)) - 6.0
    if len(blocks) and float(np.min(blocks)) < floor:
        return broke(latency, f"a dropout after the change ({float(np.min(blocks)):.1f} dBFS)")
    if settled >= 0.05 or moved * np.sign(change_db - 6.0) <= 6.0:
        return broke(latency, f"not a clean change ({moved:+.1f} dB, residual after it {settled:.3f})")
    return {"latency": latency, "problem": None, "kind": None}


class KnobLatency(unittest.TestCase):
    def setUp(self):
        require_speakerd_local_link(self)
        self.tmp = Path(tempfile.mkdtemp(prefix="vpair-knob-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def measure(self, quantum):
        setup = None
        if quantum != 1024:
            conf = speaker_conf_with(self.tmp / f"speaker-{quantum}.conf", default_clock_quantum=quantum)
            setup = {name: {"speaker_conf": conf} for name in ("left", "right")}
        bundles = [built("loudness", "headless")]
        results = []
        with SpeakerPair(self.tmp / f"q{quantum}", {"left": DOCUMENT, "right": DOCUMENT}, bundles, setup=setup) as pair, \
                Controller("ctl-knob") as ctl, \
                Player(pair["left"], tone(FREQ, LEVEL, 2.0 + 1.2 * MORE + 3.0), target="speakerd"), \
                Player(pair["right"], tone(FREQ, LEVEL, 2.0 + 1.2 * MORE + 3.0), target="speakerd"):
            recorders = {name: TimedRecorder(pair[name], latency=quantum).start() for name in ("left", "right")}
            profilers = {name: Profiler(pair[name]).__enter__() for name in ("left", "right")}
            try:
                time.sleep(1.5)
                sends = []
                for k, value in enumerate(CHANGES):
                    sends.append(time.monotonic())
                    ctl.osc_param(16, "s0_e0_gain", value)
                    time.sleep(1.2)
                    # Ten changes always; more, up to MORE, only while fewer than AT_LEAST of them
                    # could be measured (a loaded machine breaking the captures with xruns).
                    if k + 1 >= SENDS:
                        time.sleep(0.5)
                        so_far = {name: (r.snapshot()[0], r) for name, r in recorders.items()}
                        if sum(self.measurable(so_far, quantum, t, CHANGES[j]) for j, t in enumerate(sends)) >= AT_LEAST:
                            break
            finally:
                captures = {name: (r.stop()[0], r) for name, r in recorders.items()}
                # What PipeWire recorded for speakerd's graph, the recorder, and the cycles.
                xruns = {name: p.events(["speakerd-dsp", recorders[name].name]) for name, p in profilers.items()}
                for p in profilers.values():
                    p.__exit__(None, None, None)
        aside_, failures = [], []
        for k, t_send in enumerate(sends):
            row = {name: onset(x[:, 0], r, quantum, t_send, CHANGES[k]) for name, (x, r) in captures.items()}
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
                failures.append(f"change {k + 1} ({CHANGES[k]:+g} dB): " + "; ".join(failed))
                print(f"\n  quantum {quantum}, change {k + 1}: FAILED: {'; '.join(failed)}", end="")
                continue
            if set_aside:
                aside_.append(f"change {k + 1}: " + "; ".join(set_aside))
                print(f"\n  quantum {quantum}, change {k + 1}: set aside: {'; '.join(set_aside)}", end="")
                continue
            for name, r in row.items():
                self.assertIsNotNone(r["latency"], f"quantum {quantum}, change {k + 1} ({CHANGES[k]:+g} dB): the "
                                                   f"{name} capture did not change within the second after the send")
            results.append({name: r["latency"] for name, r in row.items()})
            print(f"\n  quantum {quantum}, change {k + 1} to {CHANGES[k]:+g} dB: left {1000 * row['left']['latency']:.2f} ms, "
                  f"right {1000 * row['right']['latency']:.2f} ms, "
                  f"apart {1000 * abs(row['left']['latency'] - row['right']['latency']):.2f} ms", end="")
        self.assertEqual(failures, [], f"quantum {quantum}: " + "\n  ".join(failures))
        self.assertGreaterEqual(len(results), AT_LEAST,
                                f"quantum {quantum}: {len(results)} of {len(sends)} changes measured, at least {AT_LEAST} "
                                "must be; set aside:\n  " + "\n  ".join(aside_))
        return results

    @staticmethod
    def measurable(captures, quantum, t_send, change_db):
        return all(onset(x[:, 0], r, quantum, t_send, change_db)["problem"] is None for x, r in captures.values())

    def check_latency(self, quantum, results):
        for k, row in enumerate(results):
            for name, latency in row.items():
                self.assertGreater(latency, -0.001, f"quantum {quantum}, change {k + 1}: the {name} capture changed "
                                                    f"{-1000 * latency:.2f} ms before the send: the time mapping is wrong")
                self.assertLess(latency, 0.060, f"quantum {quantum}, change {k + 1}: the {name} capture changed "
                                                f"{1000 * latency:.2f} ms after the send, not within 60 ms")

    def test_at_quantum_128(self):
        results = self.measure(128)
        self.check_latency(128, results)
        for k, row in enumerate(results):
            apart = abs(row["left"] - row["right"])
            self.assertLess(apart, 0.005, f"quantum 128, change {k + 1}: the captures changed {1000 * apart:.2f} ms "
                                          "apart, not within 5 ms")

    def test_at_the_boards_quantum_1024(self):
        results = self.measure(1024)
        self.check_latency(1024, results)
        print(f"\n  quantum 1024: the captures changed at most "
              f"{1000 * max(abs(r['left'] - r['right']) for r in results):.2f} ms apart (up to one quantum, "
              "21.3 ms, is the two clocks' phase)")


if __name__ == "__main__":
    unittest.main()
