"""monitor_fanout (docs/autonomous-development.md, section 5): eight monitor clients of one
speaker's stream, subscribed at staggered times, each get its values as they arrive: the value
age at the client is under 5 ms at the 99th percentile over all clients, and each client's rate
is within 30 % of what it asked for.

Both virtual speakers run speakerd and ampd (the left the hub, the right its agent) on
test_graph_edit's document (loudness_headless, flat), each playing a steady 200 Hz tone at -20 dBFS
into its speakerd sink, so that every audio cycle runs and `output/level` has a value in every
period. On one speaker at a time, the hub's first and then the agent's, eight harness monitor
clients (plugin windows) connect to its ampd and subscribe to `output/level` at 20 Hz, the k-th
1.5 k / 8 periods after the first: their phases (0, 3/16, 6/16, ... of the 50 ms period) all
differ, and the subscriptions are spread over 1.3 periods, so a relay that sends each client's
values on a clock of its own makes each value wait for that client's phase. From 1 s after every
client got its first value, every value that arrives in a 30 s window is measured. A value's
**age** is its arrival at the client (`time.monotonic()` when the client has read its line) minus
its `t` (speakerd's `CLOCK_MONOTONIC` when its monitor thread sampled it: one clock on one machine).

Why 30 s: each flush sends one value to all eight clients, so a stall (speakerd's monitor thread,
ampd's loop or the harness held up a moment, on a shared runner that also runs two PipeWire graphs)
makes all eight late at once. The window's 4800 values are about 600 periods, and the 99th
percentile stays under 5 ms with six of them stalled; a 10 s window tolerated two. Each speaker
takes about 32 s, the scenario about 70 s with the pair's start (30 s before).

Pass when, on each speaker:

- the 99th percentile of the age, over every value of all eight clients in the window, is under
  5 ms;
- each client's rate (its values in the window over the window's length) is within 30 % of the
  20 Hz it asked for.

Printed, per client: its rate and its age's median, 90th and 99th percentile and maximum; per
speaker: the age's percentiles over all clients, and ampd's CPU over the window (its utime and
stime from /proc, a share of one core; printed, not asserted).

With the per-client pump of ampd 0.1.0 (one task per client and stream, on its own clock) the
age's 99th percentile is about 50 ms on the virtual pair; relayed as it arrives, about 1 ms.

It needs step 3's speakerd (the local socket and output/level: 0.3.0): with an older one it fails,
saying so.

    python3 -m unittest -v tests.vpair.test_monitor_fanout
"""

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from harness import ADDRESS, MonitorClient, Player, SpeakerPair, built, require_speakerd_local_link, tone
from test_graph_edit import DOCUMENT

STREAM = "output/level"
CLIENTS = 8
RATE_HZ = 20.0
STAGGER = 1.5 / CLIENTS          # periods between two clients' subscriptions
SETTLE, WINDOW = 1.0, 30.0       # seconds (WINDOW: the docstring's "Why 30 s")
AGE_P99_MS = 5.0
RATE_TOLERANCE = 0.3
FREQ, LEVEL = 200.0, -20.0
TICK = os.sysconf("SC_CLK_TCK")


def cpu_seconds(pid):
    """utime + stime of a process, in seconds (/proc/<pid>/stat, fields 14 and 15)."""
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return (int(fields[11]) + int(fields[12])) / TICK


def ages_ms(values):
    return np.array([1000.0 * (arrived - value["t"]) for arrived, value in values])


def summary(ages):
    return (f"age median {np.median(ages):6.2f} ms, p90 {np.percentile(ages, 90):6.2f}, "
            f"p99 {np.percentile(ages, 99):6.2f}, max {np.max(ages):6.2f}")


class MonitorFanout(unittest.TestCase):
    def test_eight_clients_get_values_as_they_arrive(self):
        require_speakerd_local_link(self)
        tmp = Path(tempfile.mkdtemp(prefix="vpair-fanout-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        results = {}
        with SpeakerPair(tmp, {"left": DOCUMENT, "right": DOCUMENT}, [built("loudness", "headless")]) as pair, \
                Player(pair["left"], tone(FREQ, LEVEL, 60.0), target="speakerd"), \
                Player(pair["right"], tone(FREQ, LEVEL, 60.0), target="speakerd"):
            for name in ("left", "right"):
                results[name] = self.measure(name, pair.ampd[name].process.pid)
        failures = []
        for name, (clients, cpu) in results.items():
            role = "the hub" if name == "left" else "the agent"
            every = np.concatenate([ages for _, ages in clients]) if clients else np.array([])
            print(f"\n  {name} ({role}): {CLIENTS} clients of {STREAM} at {RATE_HZ:g} Hz, {WINDOW:g} s; "
                  f"ampd's CPU {100 * cpu:.1f} % of a core", end="")
            for k, (rate, ages) in enumerate(clients):
                print(f"\n    client {k} (subscribed {1000 * k * STAGGER / RATE_HZ:5.1f} ms after the first): "
                      f"{rate:5.1f} values/s, " + (summary(ages) if len(ages) else "no value"), end="")
                if abs(rate - RATE_HZ) > RATE_TOLERANCE * RATE_HZ:
                    failures.append(f"{name}: client {k} got {rate:.1f} values/s, asked {RATE_HZ:g} "
                                    f"(more than {100 * RATE_TOLERANCE:.0f} % off)")
            if len(every):
                p99 = float(np.percentile(every, 99))
                print(f"\n    all clients, {len(every)} values: {summary(every)}", end="")
                if p99 >= AGE_P99_MS:
                    failures.append(f"{name}: the value age's 99th percentile over all clients is {p99:.2f} ms "
                                    f"(must be under {AGE_P99_MS:g} ms)")
            else:
                failures.append(f"{name}: no value reached any client in the window")
        print()
        if failures:
            self.fail("\n".join(failures))

    def measure(self, name, ampd_pid):
        """Eight clients on `name`'s monitor link, subscribed at staggered phases; returns
        ([(rate, ages in ms) per client], ampd's CPU share over the window)."""
        period = 1.0 / RATE_HZ
        clients = [MonitorClient(ADDRESS[name], name=f"fanout-{name}-{k}") for k in range(CLIENTS)]
        try:
            first = time.monotonic()
            for k, client in enumerate(clients):
                time.sleep(max(0.0, first + k * STAGGER * period - time.monotonic()))
                client.subscribe(STREAM, RATE_HZ)
            for client in clients:
                client.wait_value(STREAM, timeout=5)
            time.sleep(SETTLE)
            cpu0, start = cpu_seconds(ampd_pid), time.monotonic()
            time.sleep(WINDOW)
            end, cpu1 = time.monotonic(), cpu_seconds(ampd_pid)
            measured = []
            for client in clients:
                values = [(t, v) for t, v in client.of(STREAM, since=start) if t <= end]
                measured.append((len(values) / (end - start), ages_ms(values)))
            return measured, (cpu1 - cpu0) / (end - start)
        finally:
            for client in clients:
                client.close()


if __name__ == "__main__":
    unittest.main()
