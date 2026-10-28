"""meter_values (docs/autonomous-development.md, section 5): a level monitor subscribed on each
speaker tracks a stepped test tone within 1 dB, and reports the left and right levels separately.

Both virtual speakers run speakerd and ampd on one document: the Mine pin (input channel 2)
through loudness_headless (node 16, flat) to both amplifier channels, so each speaker plays its own
side of the stream. Each plays the same stereo test tone, 1 kHz, in steps of 2 s: left -30, -20,
-10 dBFS, right 12 dB under the left (-42, -32, -22), after 0.5 s of silence. A harness monitor
client on each speaker's ampd (the monitor link, straight to that speaker) subscribes to
`output/level` (woofer peak and RMS, tweeter peak and RMS, dBFS) and to loudness's
`node/16/mon_levelDb` (its detector's level of the input), at 30 Hz. Each step is judged on the
values that arrived from 0.8 s to 1.8 s into it (after the detector's ballistics), by their median.

Pass when, on each speaker and at each step:

- output/level's peaks are that speaker's side's level within 1 dB and its RMS values 3.01 dB
  under it within 1 dB, for the woofer and the tweeter: the left speaker's values follow the
  left channel and the right speaker's the right one, 12 dB apart (the Mine pin, and the levels
  reported separately);
- node/16/mon_levelDb is the input level as loudness's detector measures it within 1 dB. The
  detector is JUCE's BallisticsFilter in RMS mode with loudness's default 100 ms attack and 300 ms
  release; with an attack shorter than the release it reads a steady sine above its RMS (by about
  1.3 dB at 1 kHz), so the reference is that filter, by JUCE's formula, run here on the tone.

It needs step 3's speakerd (the local socket, the Mine pin, output/level: 0.3.0) and loudness with
its mon_ monitors: before those are merged it fails, saying what it lacks.

    python3 -m unittest -v tests.vpair.test_meter_values
"""

import shutil
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from harness import (ADDRESS, MonitorClient, Player, SpeakerPair, built, lv2_uri, require_speakerd_local_link,
                     revision_document, silence, tone)

FREQ = 1000.0
LEFT_STEPS = [-30.0, -20.0, -10.0]
RIGHT_OFFSET = -12.0
STEP, LEAD = 2.0, 0.5
DOCUMENT = revision_document({
    "format": "speakerd-graph", "version": 1,
    "nodes": [{"uid": 16, "plugin": {"format": "LV2", "identifier": lv2_uri("loudness_headless")},
               "params": {"mode": "Force 1"}}],
    "connections": [{"src": {"node": 1, "channel": 2}, "dst": {"node": 16, "channel": 0}},
                    {"src": {"node": 16, "channel": 0}, "dst": {"node": 2, "channel": 0}},
                    {"src": {"node": 16, "channel": 0}, "dst": {"node": 2, "channel": 1}}],
}, 1)


def stepped(levels):
    return np.concatenate([silence(LEAD)] + [tone(FREQ, level, STEP) for level in levels] + [silence(0.5)])


def detector_offset_db(attack_ms=100.0, release_ms=300.0, rate=48000):
    """What loudness's detector settles to for a full-scale 1 kHz sine, in dB: JUCE's
    BallisticsFilter (juce_BallisticsFilter.cpp) in RMS mode, y = x^2 + cte (y - x^2) with the
    attack's cte when x^2 rises above y and the release's otherwise, cte = exp(-2 pi 1000 / (rate
    * ms)), its level sqrt(y); the median of the last half second, as the monitor samples it at
    arbitrary moments."""
    attack, release = (np.exp(-2 * np.pi * 1000.0 / (rate * ms)) for ms in (attack_ms, release_ms))
    x2 = np.sin(2 * np.pi * FREQ * np.arange(rate) / rate) ** 2
    y, out = 0.0, np.empty(len(x2))
    for i, v in enumerate(x2):
        y = v + (attack if v > y else release) * (y - v)
        out[i] = y
    return 10 * np.log10(np.median(out[rate // 2:]))


def scalar(v):
    return float(v[0] if isinstance(v, list) else v)


class MeterValues(unittest.TestCase):
    def test_levels_on_each_speaker(self):
        require_speakerd_local_link(self)
        tmp = Path(tempfile.mkdtemp(prefix="vpair-meter-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        steps = {"left": LEFT_STEPS, "right": [level + RIGHT_OFFSET for level in LEFT_STEPS]}
        stereo = np.stack([stepped(steps["left"]), stepped(steps["right"])], axis=1)
        with SpeakerPair(tmp, {"left": DOCUMENT, "right": DOCUMENT}, [built("loudness", "headless")]) as pair:
            clients = {name: MonitorClient(ADDRESS[name]) for name in ("left", "right")}
            try:
                for client in clients.values():
                    client.subscribe("output/level", 30)
                    client.subscribe("node/16/mon_levelDb", 30)
                for name, client in clients.items():
                    client.wait_value("output/level", timeout=5)
                started = {}
                with Player(pair["left"], stereo, target="speakerd"):
                    started["left"] = time.monotonic()
                    with Player(pair["right"], stereo, target="speakerd"):
                        started["right"] = time.monotonic()
                        time.sleep(len(stereo) / 48000.0)
            finally:
                for client in clients.values():
                    client.close()
            offset = detector_offset_db()
            print(f"\n  loudness's detector reads a steady 1 kHz sine {offset:+.2f} dB from its peak "
                  f"({offset + 3.01:+.2f} dB from its RMS)", end="")
            failures = []
            for name, client in clients.items():
                for k, level in enumerate(steps[name]):
                    a = started[name] + LEAD + k * STEP + 0.8
                    b = a + 1.0
                    values = [v["v"] for t, v in client.of("output/level") if a <= t <= b]
                    monitor = [scalar(v["v"]) for t, v in client.of("node/16/mon_levelDb") if a <= t <= b]
                    if not values or not monitor:
                        failures.append(f"{name}, step {k + 1}: no value arrived from {a - started[name]:.1f} to "
                                        f"{b - started[name]:.1f} s (output/level {len(values)}, mon_levelDb {len(monitor)}; "
                                        f"errors {client.errors})")
                        continue
                    wp, wr, tp, tr = np.median(np.array(values, dtype=float), axis=0)
                    m = float(np.median(monitor))
                    print(f"\n  {name}, step {k + 1} at {level:g} dBFS: woofer {wp:.2f} / {wr:.2f}, tweeter {tp:.2f} / "
                          f"{tr:.2f} dBFS (peak / RMS), mon_levelDb {m:.2f} ({len(values)} values)", end="")
                    for what, got, want in (("woofer peak", wp, level), ("woofer RMS", wr, level - 3.01),
                                            ("tweeter peak", tp, level), ("tweeter RMS", tr, level - 3.01),
                                            ("mon_levelDb", m, level + offset)):
                        if abs(got - want) > 1.0:
                            failures.append(f"{name}, step {k + 1}: {what} {got:.2f}, not {want:.2f} within 1 dB")
            self.assertEqual(failures, [], "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
