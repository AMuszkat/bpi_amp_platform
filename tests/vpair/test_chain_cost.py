"""chain_cost (docs/autonomous-development.md, section 5): what the board's chain costs speakerd's
audio thread, measured by speakerd itself, on the virtual pair.

The chain is the board's: the Mine pin -> loudness_headless (16) -> firConv (17) -> crossover
(18), its low band to the woofer and its high band to the tweeter (which keeps the tweeter rule,
so speakerd runs as on a board: the tweeter on, no --allow-tweeter-without-crossover). firConv and
crossover are their complete builds (they have no other). speakerd runs on the left virtual
speaker at the pair's own 48 kHz and quantum 1024 (nothing here changes either), with stereo
noise at a moderate level playing into its sink (-26 dBFS RMS a channel). A client of its local
link subscribes to `speakerd/timing` (docs/protocol.md, "Monitoring": per plugin node, the graph
and the whole cycle, each as a fraction of the cycle's 21.3 ms) and to `output/level`; after 5 s
of settling it averages a 20 s window, weighting each value by the cycles it covers.

Pass when, over the window:

- firConv's mean share of the cycle is under 5 %, and crossover's under 1.5 %.

Why those numbers. On x86_64 in the development container (a 5 GHz Zen 5 laptop core), the
plugins' juce::dsp::FIR::Filter cost firConv 15.05 % and crossover 3.80 % of the cycle; the block
FIR that replaces them (phase A's P1: 61 + 2001 taps in firConv, 259 + 259 in crossover) costs
about 1.0 % and 0.27 % in isolation. 5 % is a third of today's firConv and five times P1's; 1.5 %
is 2.5 times under today's crossover and 5.5 times over P1's. A slower or busier machine (a
GitHub runner: a shared, lower-clocked server core) only makes today's code fail by more, and
leaves P1 passing until it is about five times slower than this one; today's code would pass
only on a machine 2.5 to 3 times faster per core than a 5 GHz Zen 5. The criterion is the mean,
which a stray preemption moves by little; the maxima are printed, not asserted.

The measurement must also be one: the window holds at least 80 % of the cycles 20 s of a 1024
quantum has (937.5), each of the three nodes was measured in at least 80 % of them, the quantum
did not change, and the woofer's level (`output/level`, median RMS over the window) is above
-60 dBFS, so the stream played through the chain. These say the numbers mean something; they
are not the criterion.

Late cycles and position jumps in the window are printed, not asserted: they come from the
machine's scheduler (a shared runner's CPU taken away for a cycle), not from the chain's cost,
which is what this scenario proves; today's code and P1 both run without them here, so asserting
them would add only flakiness on CI. test_speakerd_installed's smoke check already asserts no
late cycle while it watches. Only the left speaker runs the chain: a node's cost does not depend
on the other speaker, and a second chain on a two- or four-core runner would measure the
contention, not the code.

It prints each node's mean and max, the graph's and the cycle's, the window's cycles, late
cycles, position jumps and dropped records; its failure message carries them too.

It needs step 3's speakerd (the local link, the Mine pin): with an older one it fails, saying so.

    python3 -m unittest -v tests.vpair.test_chain_cost
"""

import shutil
import statistics
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from harness import (NO_LIMITS, RATE, Pair, Player, Speakerd, SpeakerdLink, built, lv2_uri,
                     require_speakerd_local_link, state_dir)

LOUDNESS, FIRCONV, CROSSOVER = 16, 17, 18
URIS = {LOUDNESS: lv2_uri("loudness_headless"), FIRCONV: lv2_uri("firConv"), CROSSOVER: lv2_uri("crossover")}

QUANTUM = 1024
SETTLE_S, WINDOW_S = 5.0, 20.0
TIMING_RATE_HZ = 2
FIRCONV_MEAN_LIMIT, CROSSOVER_MEAN_LIMIT = 0.05, 0.015
COVERAGE = 0.8
PLAYING_DBFS = -60.0


def chain_document(revision=1, firconv_gain=-6.0):
    """The board's chain: Mine -> loudness_headless (16) -> firConv (17) -> crossover (18), its low
    band (output 0) to the woofer and its high band (output 1) to the tweeter."""
    def node(uid, params):
        return {"uid": uid, "plugin": {"format": "LV2", "identifier": URIS[uid]}, "params": params}

    def wire(src, src_channel, dst, dst_channel):
        return {"src": {"node": src, "channel": src_channel}, "dst": {"node": dst, "channel": dst_channel}}

    return {"format": "speakerd-graph", "version": 1, "revision": revision,
            "nodes": [node(LOUDNESS, {"s0_e0_gain": 0.0}), node(FIRCONV, {"gain": firconv_gain}),
                      node(CROSSOVER, {"gain": 0.0})],
            "connections": [wire(1, 2, LOUDNESS, 0), wire(LOUDNESS, 0, FIRCONV, 0), wire(FIRCONV, 0, CROSSOVER, 0),
                            wire(CROSSOVER, 0, 2, 0), wire(CROSSOVER, 1, 2, 1)]}


def chain_bundles():
    """The bundles of the chain, by node uid."""
    return {LOUDNESS: built("loudness", "headless"), FIRCONV: built("firConv", "complete"),
            CROSSOVER: built("crossover", "complete")}


def noise(seconds, level=0.05, seed=1):
    """Stereo Gaussian noise, `level` RMS a channel (0.05 is -26 dBFS), clipped at 0.9."""
    rng = np.random.default_rng(seed)
    return np.clip(rng.standard_normal((int(seconds * RATE), 2)) * level, -0.9, 0.9)


def summarise(values):
    """speakerd/timing values [(arrival, v)] added up: the cycles, late cycles and position jumps,
    the graph's and the cycle's mean (weighted by each value's cycles) and max, and per node uid
    its name, mean, max and the cycles it was measured in."""
    cycles = sum(v["cycles"] for _, v in values)
    out = {"cycles": cycles, "late": sum(v["late"] for _, v in values), "jumps": sum(v["jumps"] for _, v in values),
           "dropped": (values[-1][1].get("dropped_total", 0) - values[0][1].get("dropped_total", 0)) if values else 0,
           "nodes": {}}
    for key in ("graph", "cycle"):
        out[key] = {"mean": sum(v[key]["mean"] * v["cycles"] for _, v in values) / max(cycles, 1),
                    "max": max((v[key]["max"] for _, v in values), default=0.0)}
    for _, v in values:
        for n in v["nodes"]:
            node = out["nodes"].setdefault(n["uid"], {"name": n["name"], "sum": 0.0, "max": 0.0, "cycles": 0})
            node["sum"] += n["mean"] * v["cycles"]
            node["max"] = max(node["max"], n["max"])
            node["cycles"] += v["cycles"]
    for node in out["nodes"].values():
        node["mean"] = node["sum"] / max(node["cycles"], 1)
    return out


def report(summary, expected_cycles, level):
    lines = [f"{summary['cycles']} cycles in the {WINDOW_S:.0f} s window ({expected_cycles:.1f} at quantum "
             f"{QUANTUM}), {summary['late']} late, {summary['jumps']} position jump(s), {summary['dropped']} dropped"]
    for uid, node in sorted(summary["nodes"].items()):
        lines.append(f"node {uid} ({node['name']}): mean {100 * node['mean']:.2f} %, max {100 * node['max']:.2f} %"
                     f" of the cycle, over {node['cycles']} cycles")
    for key in ("graph", "cycle"):
        lines.append(f"{key}: mean {100 * summary[key]['mean']:.2f} %, max {100 * summary[key]['max']:.2f} %")
    lines.append(f"woofer: median RMS {level:.1f} dBFS (output/level)")
    return "\n  ".join(lines)


class ChainCost(unittest.TestCase):
    def test_the_fir_plugins_cost_under_their_limits(self):
        require_speakerd_local_link(self)
        tmp = Path(tempfile.mkdtemp(prefix="vpair-chain-cost-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        bundles = chain_bundles()
        state = state_dir(tmp / "left", role="left", current=chain_document(), limits=NO_LIMITS)
        with Pair() as pair, Speakerd(pair["left"], state, bundles=list(bundles.values()),
                                      allow_tweeter=False) as sd:
            playing = sd.lines_starting("playing: ")[-1]
            self.assertIn(f"playing: layer current (graph.json), revision 1, {RATE} Hz, quantum {QUANTUM}", playing,
                          f"speakerd does not play the chain at quantum {QUANTUM}:\n{sd.log()}")
            with SpeakerdLink(pair["left"]) as link, \
                    Player(pair["left"], noise(SETTLE_S + WINDOW_S + 15.0), target="speakerd"):
                link.subscribe("speakerd/timing", TIMING_RATE_HZ)
                link.subscribe("output/level", TIMING_RATE_HZ)
                time.sleep(SETTLE_S)
                start = time.monotonic()
                time.sleep(WINDOW_S)
                end = time.monotonic()
                timing = link.values("speakerd/timing", since=start, until=end)
                levels = [v[1] for _, v in link.values("output/level", since=start, until=end)]
            quantum_changes = sd.lines_starting("pipewire: the quantum changed")

        summary = summarise(timing)
        expected_cycles = WINDOW_S * RATE / QUANTUM
        level = statistics.median(levels) if levels else -240.0
        table = report(summary, expected_cycles, level)
        print(f"\n  the board's chain on the left speaker, speakerd's own timing:\n  {table}")

        # The measurement is one.
        self.assertTrue(timing, "no speakerd/timing value arrived in the window")
        self.assertEqual(quantum_changes, [], f"the quantum changed during the measurement:\n  {table}")
        self.assertGreaterEqual(summary["cycles"], COVERAGE * expected_cycles,
                                f"the window holds too few cycles to be a measurement:\n  {table}")
        for uid in (LOUDNESS, FIRCONV, CROSSOVER):
            measured = summary["nodes"].get(uid, {}).get("cycles", 0)
            self.assertGreaterEqual(measured, COVERAGE * summary["cycles"],
                                    f"node {uid} was measured in {measured} of {summary['cycles']} cycles:\n  {table}")
        self.assertGreater(level, PLAYING_DBFS, f"the stream did not play through the chain:\n  {table}")

        # The criterion.
        failures = []
        for uid, limit in ((FIRCONV, FIRCONV_MEAN_LIMIT), (CROSSOVER, CROSSOVER_MEAN_LIMIT)):
            node = summary["nodes"][uid]
            if not node["mean"] < limit:
                failures.append(f"node {uid} ({node['name']}) costs {100 * node['mean']:.2f} % of the cycle on "
                                f"average, not under {100 * limit:.1f} %")
        if failures:
            self.fail("\n".join(failures) + f"\n  {table}")


if __name__ == "__main__":
    unittest.main()
