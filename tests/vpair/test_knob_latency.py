"""knob_latency (docs/autonomous-development.md, section 5): a parameter sent to the hub is audible
in both captures within 60 ms, and the two captures differ by less than 5 ms.

Both virtual speakers run speakerd (loudness_headless between the stream and both amplifier
channels: Force 1, its first shelf at 1 kHz, 0 dB, a 1 ms glide) and ampd, the left the hub, the
right its agent. Each plays a steady 200 Hz tone at -20 dBFS while its amplifier is recorded by a
harness.TimedRecorder, which knows when each sample was in the graph (time.monotonic(), from
when its chunks arrived). A harness controller sends the shelf's gain to the hub over OSC, as a
moving knob does, alternately +12 dB and 0 dB, five times; the time of each send is taken just
before it. In each capture the change is the first sample that leaves the 200 Hz sine fitted to
the 0.5 s before the send by more than 5 % of its amplitude; its time is when the cycle it was
produced in was delivered to the recorder (at most a fraction of a millisecond late).

The speakers' PipeWire graphs run on independent clocks, so a value reaches each speakerd at the
same moment but takes effect at each one's next cycle: the two captures can differ by up to one
quantum (21.3 ms at the boards' 1024) whatever ampd does. So the whole criterion is checked at a
quantum of 128 (2.7 ms), where it measures ampd and speakerd rather than the phase of two clocks;
at the boards' quantum, 1024, the 60 ms is checked and the difference printed.

Pass when, for every one of the five changes:

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

from harness import (RATE, Controller, Player, SpeakerPair, TimedRecorder, built, graph_document, lv2_uri,
                     require_speakerd_local_link, revision_document, speaker_conf_with, tone)

FREQ, LEVEL = 200.0, -20.0
PARAMS = {"mode": "Force 1", "s0_e0_freq": 1000.0, "s0_e0_gain": 0.0, "glide": 1.0}
DOCUMENT = revision_document(graph_document(lv2_uri("loudness_headless"), PARAMS), 1)
CHANGES = [12.0, 0.0, 12.0, 0.0, 12.0]


def onset(x, t0, quantum, t_send):
    """The time the capture leaves the 200 Hz sine it played in the 0.5 s before `t_send`:
    (seconds after t_send, the residual before it relative to the tone)."""
    index = lambda t: int(round((t - t0) * RATE))
    a, b = index(t_send - 0.6), index(t_send - 0.1)
    n = np.arange(len(x))
    basis = np.stack([np.sin(2 * np.pi * FREQ * n / RATE), np.cos(2 * np.pi * FREQ * n / RATE)], axis=1)
    c = np.linalg.lstsq(basis[a:b], x[a:b], rcond=None)[0]
    amplitude = float(np.hypot(*c))
    residual = np.abs(x - basis @ c)
    threshold = 0.05 * amplitude
    quiet = float(np.max(residual[a:index(t_send - 0.03)])) / amplitude
    after = np.nonzero(residual[b:] > threshold)[0]
    if len(after) == 0:
        return None, quiet
    i = b + int(after[0])
    # The sample was produced in the cycle of `quantum` samples it is in, and that cycle's samples
    # were delivered together at its end, t0 + (end of the cycle) / RATE: when the change was in
    # the graph, or a fraction of a millisecond after (the cycle's processing).
    delivered = t0 + ((i // quantum) + 1) * quantum / RATE
    return delivered - t_send, quiet


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
                Player(pair["left"], tone(FREQ, LEVEL, 20.0), target="speakerd"), \
                Player(pair["right"], tone(FREQ, LEVEL, 20.0), target="speakerd"):
            recorders = {name: TimedRecorder(pair[name], latency=quantum).start() for name in ("left", "right")}
            try:
                time.sleep(1.5)
                sends = []
                for value in CHANGES:
                    sends.append(time.monotonic())
                    ctl.osc_param(16, "s0_e0_gain", value)
                    time.sleep(1.2)
            finally:
                captures = {name: r.stop() for name, r in recorders.items()}
        for k, t_send in enumerate(sends):
            row = {}
            for name, (x, t0) in captures.items():
                latency, quiet = onset(x[:, 0], t0, quantum, t_send)
                self.assertIsNotNone(latency, f"quantum {quantum}, change {k + 1} ({CHANGES[k]:+g} dB): the {name} "
                                              "capture did not change within the second after the send")
                row[name] = (latency, quiet)
            results.append(row)
            print(f"\n  quantum {quantum}, change {k + 1} to {CHANGES[k]:+g} dB: left {1000 * row['left'][0]:.2f} ms, "
                  f"right {1000 * row['right'][0]:.2f} ms, apart {1000 * abs(row['left'][0] - row['right'][0]):.2f} ms "
                  f"(residual before it: {20 * np.log10(max(row['left'][1], 1e-9)):.0f} / "
                  f"{20 * np.log10(max(row['right'][1], 1e-9)):.0f} dB)", end="")
        return results

    def check_latency(self, quantum, results):
        for k, row in enumerate(results):
            for name, (latency, quiet) in row.items():
                self.assertLess(quiet, 0.05, f"quantum {quantum}, change {k + 1}: the {name} capture was not steady "
                                             f"before the send (residual {quiet:.3f} of the tone)")
                self.assertGreater(latency, -0.001, f"quantum {quantum}, change {k + 1}: the {name} capture changed "
                                                    f"{-1000 * latency:.2f} ms before the send: the time mapping is wrong")
                self.assertLess(latency, 0.060, f"quantum {quantum}, change {k + 1}: the {name} capture changed "
                                                f"{1000 * latency:.2f} ms after the send, not within 60 ms")

    def test_at_quantum_128(self):
        results = self.measure(128)
        self.check_latency(128, results)
        for k, row in enumerate(results):
            apart = abs(row["left"][0] - row["right"][0])
            self.assertLess(apart, 0.005, f"quantum 128, change {k + 1}: the captures changed {1000 * apart:.2f} ms "
                                          "apart, not within 5 ms")

    def test_at_the_boards_quantum_1024(self):
        results = self.measure(1024)
        self.check_latency(1024, results)
        print(f"\n  quantum 1024: the captures changed at most "
              f"{1000 * max(abs(r['left'][0] - r['right'][0]) for r in results):.2f} ms apart (up to one quantum, "
              "21.3 ms, is the two clocks' phase)")


if __name__ == "__main__":
    unittest.main()
