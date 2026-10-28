"""ampd_restart (docs/autonomous-development.md, section 5): killing ampd leaves the audio capture
uninterrupted, and open windows resubscribe within 2 s.

Both virtual speakers run speakerd and ampd (the left the hub) and play a steady 200 Hz tone at
-20 dBFS, their amplifiers recorded as floats. A harness monitor client on each speaker's ampd
subscribes to output/level at 30 Hz and, as an open plugin window does, connects again every
0.1 s when the link drops and subscribes again. Then each ampd in turn, the hub's first, is killed
(SIGKILL, as a crash) and started again 1 s later, as its unit does (Restart=on-failure,
RestartSec=1).

Pass when, for each speaker's ampd:

- the monitor client gets a value again within 2 s of the kill;
- the agent is connected to the hub again afterwards (the hub's ampd says so);

and, over the whole time, on both speakers, no 10 ms block of the capture is more than 0.5 dB from
the level before the first kill, and none is exact zeros: speakerd plays on, whatever ampd does.

It needs step 3's speakerd (the local socket and output/level: 0.3.0): with an older one it fails,
saying so.

    python3 -m unittest -v tests.vpair.test_ampd_restart
"""

import shutil
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from harness import (ADDRESS, RATE, MonitorClient, Player, SpeakerPair, TimedRecorder, block_levels, built,
                     require_speakerd_local_link, rms_dbfs, tone)
from test_graph_edit import DOCUMENT

FREQ, LEVEL = 200.0, -20.0


class AmpdRestart(unittest.TestCase):
    def test_killing_ampd(self):
        require_speakerd_local_link(self)
        tmp = Path(tempfile.mkdtemp(prefix="vpair-restart-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        with SpeakerPair(tmp, {"left": DOCUMENT, "right": DOCUMENT}, [built("loudness", "headless")]) as pair, \
                Player(pair["left"], tone(FREQ, LEVEL, 40.0), target="speakerd"), \
                Player(pair["right"], tone(FREQ, LEVEL, 40.0), target="speakerd"):
            clients = {name: MonitorClient(ADDRESS[name], reconnect=True) for name in ("left", "right")}
            recorders = {name: TimedRecorder(pair[name]).start() for name in ("left", "right")}
            back = {}
            try:
                for client in clients.values():
                    client.subscribe("output/level", 30)
                    client.wait_value("output/level", timeout=5)
                time.sleep(1.0)
                first = time.monotonic()
                for name in ("left", "right"):
                    killed = time.monotonic()
                    connects = clients[name].connects
                    pair.ampd[name].kill()
                    time.sleep(1.0)
                    mark = pair.ampd["left"].mark() if name == "right" else 0
                    pair.start_ampd(name)
                    t, _ = clients[name].wait_value("output/level", since=killed + 0.05, timeout=10)
                    back[name] = (t - killed, clients[name].connects - connects)
                    hub = pair.ampd["left"]
                    hub.wait_for("agent right connected", after=mark if name == "right" else 0, timeout=10)
                    time.sleep(1.0)
                end = time.monotonic()
            finally:
                captures = {name: r.stop() for name, r in recorders.items()}
                for client in clients.values():
                    client.close()
            for name, (after, reconnects) in back.items():
                print(f"\n  {name}'s ampd killed: values again {after:.2f} s later ({reconnects} reconnection(s))", end="")
                self.assertLess(after, 2.0, f"{name}: no value within 2 s of the kill ({after:.2f} s)")
            for name, (x, t0) in captures.items():
                index = lambda t: int((t - t0) * RATE)
                steady = rms_dbfs(x[index(first - 1.0):index(first - 0.1), 0])
                watched = x[index(first - 0.5):index(end - 0.1), 0]
                blocks = block_levels(watched, 0.01)
                zeros = int(np.sum(np.all(watched[:len(watched) // 480 * 480].reshape(-1, 480) == 0, axis=1)))
                worst = float(np.max(np.abs(blocks - steady)))
                print(f"\n  {name}: {steady:.2f} dBFS, every 10 ms block within {worst:.3f} dB of it, blocks of zeros {zeros}",
                      end="")
                self.assertLess(worst, 0.5, f"{name}: the capture moved {worst:.2f} dB while ampd restarted")
                self.assertEqual(zeros, 0, f"{name}: the capture has blocks of zeros")


if __name__ == "__main__":
    unittest.main()
