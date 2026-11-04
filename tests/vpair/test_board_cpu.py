"""board_cpu (docs/autonomous-development.md, section 5): a monitor client of each speaker gets the
whole board's CPU use (`board/cpu`, made by ampd itself from /proc/stat) beside speakerd's own
timing (`speakerd/timing`), and ampd reads /proc/stat only while somebody subscribes.

Both virtual speakers run speakerd and ampd (the left the hub, the right its agent) on
test_graph_edit's document (loudness_headless, flat), each playing a steady 200 Hz tone at -20 dBFS
into its speakerd sink, so that every audio cycle runs and `speakerd/timing` has a value in every
period. One harness monitor client (a controller's speakers panel) connects to each speaker's ampd
and subscribes to `board/cpu` at 10 Hz and to `speakerd/timing` at 5 Hz, both speakers at once.
Values are collected for 3 s from each client's first `board/cpu` value; then each client
unsubscribes `board/cpu` and keeps `speakerd/timing`.

What tells that ampd samples /proc/stat is its log: `board/cpu: sampling <path> at <rate> Hz` when
sampling starts, `board/cpu: stopped sampling after <n> reading(s)` when it stops (firmware/CLAUDE.md,
"Log lines"). The virtual speakers share one kernel, so both speakers' values describe the same
machine; on a board each describes its own.

Pass when, on each speaker:

- neither ampd says it samples before the subscription (the pair has been up for seconds, and 1 s
  more is waited);
- the client's first `board/cpu` value and its first `speakerd/timing` value arrive within 5 s of
  subscribing;
- every `board/cpu` value is `{"busy": b, "cores": [...], "seconds": s}`: `b` in [0, 1], `cores`
  as many numbers, each in [0, 1], as there are `cpuN` lines in /proc/stat both when the test reads
  it before subscribing and when it reads it after the window (ampd's own rule: /proc/stat lists
  only the online CPUs, and a core missing from either of two readings is dropped; not
  os.cpu_count(), which can disagree with /proc/stat under hot-plug, lxcfs or a container), `s`
  0.05 to 0.5 s, and the median of `s` over the window within 10 % of the 0.1 s period; its `t` is
  within 1 s of the client's monotonic clock at arrival (one machine: the same CLOCK_MONOTONIC as
  speakerd's `t`); the window has at least 20 of them (10 Hz asked, 3 s);
- every `speakerd/timing` value has the documented shape (docs/protocol.md, "Monitoring"):
  `cycles`, `late`, `jumps` and the four `*_total` counters whole numbers of 0 or more, `cycles`
  more than 0, `cycle` and `graph` objects with `mean` and `max` numbers of 0 or more, `nodes` a
  list holding node 16 (`uid`, `name`, `mean`, `max`);
- after the unsubscribe ampd says it stopped sampling within 2 s, and from 0.3 s after that line
  no `board/cpu` value reaches the client for 1.5 s, while `speakerd/timing` values still do.

Printed per speaker: the values' count, `busy`'s median and maximum, the median of `seconds`, and
the readings ampd says it took.

It needs step 3's speakerd (the local socket and speakerd/timing: 0.3.0 or later): with an older
one it fails, saying so.

    python3 -m unittest -v tests.vpair.test_board_cpu
"""

import shutil
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from harness import ADDRESS, MonitorClient, Player, SpeakerPair, built, require_speakerd_local_link, tone
from test_graph_edit import DOCUMENT

# What this module's built() calls need, also through the harness (Speakerd, render, SpeakerPair,
# ScriptedController): tests/vpair/pool.py builds them before any test runs (README.md, "The pool").
POOL_BUILDS = ("speakerd", "loudness:headless")

CPU, TIMING = "board/cpu", "speakerd/timing"
CPU_RATE, TIMING_RATE = 10.0, 5.0
FIRST_WITHIN = 5.0
WINDOW = 3.0
MIN_VALUES = 20
SECONDS_RANGE = (0.05, 0.5)
SECONDS_MEDIAN_TOLERANCE = 0.1
CLOCK_TOLERANCE = 1.0
STOP_WITHIN, GRACE, QUIET = 2.0, 0.3, 1.5
FREQ, LEVEL, TONE = 200.0, -20.0, 40.0
COUNTERS = ("cycles", "late", "jumps", "cycles_total", "late_total", "jumps_total", "dropped_total")


def online_cpus():
    """The `cpuN` names /proc/stat lists now (the online CPUs; not the `cpu` total line)."""
    with open("/proc/stat") as f:
        return {line.split()[0] for line in f
                if line.startswith("cpu") and line.split()[0][3:].isdigit()}


def whole(x):
    return isinstance(x, int) and not isinstance(x, bool) and x >= 0


def number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x >= 0


def unit(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and 0.0 <= x <= 1.0


def timing_problems(v):
    """What is wrong with a speakerd/timing value's object, against docs/protocol.md."""
    problems = []
    if not isinstance(v, dict):
        return [f"not an object: {v!r}"]
    for key in COUNTERS:
        if not whole(v.get(key)):
            problems.append(f"{key} is {v.get(key)!r}, not a whole number of 0 or more")
    if whole(v.get("cycles")) and v["cycles"] == 0:
        problems.append("cycles is 0 (a value is sent only after audio cycles)")
    for key in ("cycle", "graph"):
        part = v.get(key)
        if not isinstance(part, dict) or not all(number(part.get(k)) for k in ("mean", "max")):
            problems.append(f"{key} is {part!r}, not an object of mean and max")
    nodes = v.get("nodes")
    if not isinstance(nodes, list):
        problems.append(f"nodes is {nodes!r}, not a list")
    else:
        for node in nodes:
            if not (isinstance(node, dict) and whole(node.get("uid")) and isinstance(node.get("name"), str)
                    and number(node.get("mean")) and number(node.get("max"))):
                problems.append(f"a node is {node!r}, not uid, name, mean and max")
        if not any(isinstance(node, dict) and node.get("uid") == 16 for node in nodes):
            problems.append(f"no node 16 among {nodes!r}")
    return problems


class BoardCpu(unittest.TestCase):
    def test_board_cpu_beside_speakerd_timing(self):
        require_speakerd_local_link(self)
        tmp = Path(tempfile.mkdtemp(prefix="vpair-board-cpu-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        failures = []
        with SpeakerPair(tmp, {"left": DOCUMENT, "right": DOCUMENT}, [built("loudness", "headless")]) as pair, \
                Player(pair["left"], tone(FREQ, LEVEL, TONE), target="speakerd"), \
                Player(pair["right"], tone(FREQ, LEVEL, TONE), target="speakerd"):
            time.sleep(1.0)
            for name in ("left", "right"):
                if pair.ampd[name].lines_starting("board/cpu: sampling"):
                    failures.append(f"{name}: ampd sampled /proc/stat with nobody subscribed")
            cpus_before = online_cpus()
            clients = {name: MonitorClient(ADDRESS[name], name=f"board-cpu-{name}") for name in ("left", "right")}
            try:
                subscribed = {}
                for name, client in clients.items():
                    subscribed[name] = time.monotonic()
                    client.subscribe(CPU, CPU_RATE)
                    client.subscribe(TIMING, TIMING_RATE)
                firsts = {}
                for name, client in clients.items():
                    for stream in (CPU, TIMING):
                        try:
                            arrived, _ = client.wait_value(stream, timeout=FIRST_WITHIN)
                        except AssertionError as e:
                            self.fail(f"{name}: {e}")
                        if arrived - subscribed[name] > FIRST_WITHIN:
                            failures.append(f"{name}: the first {stream} value came "
                                            f"{arrived - subscribed[name]:.2f} s after subscribing")
                        firsts[name, stream] = arrived
                time.sleep(WINDOW)
                cores = len(cpus_before & online_cpus())
                windows = {name: (firsts[name, CPU], firsts[name, CPU] + WINDOW) for name in clients}
                for name, client in clients.items():
                    start, end = windows[name]
                    values = [(t, v) for t, v in client.of(CPU) if start - 1e-6 <= t <= end]
                    failures += self.check_cpu(name, values, cores)
                    for _, value in client.of(TIMING):
                        failures += [f"{name}: speakerd/timing: {p}" for p in timing_problems(value.get("v"))]
                # Unsubscribed: ampd stops sampling, and no value of it comes after.
                marks = {name: pair.ampd[name].mark() for name in clients}
                for client in clients.values():
                    client.unsubscribe(CPU)
                for name, client in clients.items():
                    line = pair.ampd[name].wait_for("board/cpu: stopped sampling after ", timeout=STOP_WITHIN,
                                                    after=marks[name], required=False)
                    if line is None:
                        failures.append(f"{name}: ampd did not say it stopped sampling within {STOP_WITHIN:g} s "
                                        f"of the unsubscribe")
                    else:
                        print(f"\n  {name}: {line}", end="")
                time.sleep(GRACE)
                quiet = time.monotonic()
                time.sleep(QUIET)
                for name, client in clients.items():
                    late = client.of(CPU, since=quiet)
                    if late:
                        failures.append(f"{name}: {len(late)} board/cpu value(s) after the unsubscribe")
                    if not client.of(TIMING, since=quiet):
                        failures.append(f"{name}: no speakerd/timing value after board/cpu's unsubscribe")
                    if client.errors:
                        failures.append(f"{name}: errors: {client.errors}")
            finally:
                for client in clients.values():
                    client.close()
        print()
        if failures:
            self.fail("\n".join(failures))

    def check_cpu(self, name, values, cores):
        failures = []
        if len(values) < MIN_VALUES:
            failures.append(f"{name}: {len(values)} board/cpu values in {WINDOW:g} s at {CPU_RATE:g} Hz "
                            f"(at least {MIN_VALUES})")
        seconds, busy = [], []
        for arrived, value in values:
            v = value.get("v")
            if not isinstance(v, dict) or set(v) != {"busy", "cores", "seconds"}:
                failures.append(f"{name}: a board/cpu value is {v!r}, not busy, cores and seconds")
                continue
            if not unit(v["busy"]):
                failures.append(f"{name}: busy is {v['busy']!r}, not 0 to 1")
            if not (isinstance(v["cores"], list) and len(v["cores"]) == cores and all(map(unit, v["cores"]))):
                failures.append(f"{name}: cores is {v['cores']!r}, not {cores} numbers of 0 to 1")
            s = v["seconds"]
            if not (isinstance(s, (int, float)) and SECONDS_RANGE[0] <= s <= SECONDS_RANGE[1]):
                failures.append(f"{name}: seconds is {s!r}, not {SECONDS_RANGE[0]:g} to {SECONDS_RANGE[1]:g}")
            else:
                seconds.append(s)
            t = value.get("t")
            if not isinstance(t, (int, float)) or abs(arrived - t) > CLOCK_TOLERANCE:
                failures.append(f"{name}: t is {t!r}, {arrived:.3f} on the client's monotonic clock")
            if unit(v["busy"]):
                busy.append(v["busy"])
        if seconds:
            median = float(np.median(seconds))
            if abs(median - 1 / CPU_RATE) > SECONDS_MEDIAN_TOLERANCE / CPU_RATE:
                failures.append(f"{name}: the median of seconds is {median:.4f}, not within "
                                f"{100 * SECONDS_MEDIAN_TOLERANCE:.0f} % of {1 / CPU_RATE:g}")
            print(f"\n  {name}: {len(values)} board/cpu values in {WINDOW:g} s, busy median "
                  f"{np.median(busy) if busy else float('nan'):.3f} max {max(busy) if busy else float('nan'):.3f}, "
                  f"seconds median {median:.4f}, {cores} cores", end="")
        return failures


if __name__ == "__main__":
    unittest.main()
