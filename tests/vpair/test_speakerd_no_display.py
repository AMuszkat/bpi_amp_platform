"""speakerd_no_display (docs/autonomous-development.md, section 4): speakerd, and the plugins it hosts,
connect to no X server, whether DISPLAY names one or is unset, and speakerd plays on when that
server goes away (dsp/juce_apps/speakerd/README.md, "No X display", speakerd 0.3.3).

Every JUCE LV2 plugin, the headless build too, opens $DISPLAY (":0.0" when it is unset) from its
message thread, and with no X error handler of its own a server that went away ended speakerd,
status 1, about two seconds later ("X connection to :0.0 broken"). speakerd 0.3.3 starts with a
DISPLAY no server can have, whatever it was given.

An Xvfb runs with -audit 4 (it says each client that connects), on a display number of its own in
:100 to :199 (harness.VirtualDisplay: the pool worker's, VPAIR_DISPLAY, or the first free one; never
:0), with no window manager. A minimal X client from this test connects first, so the audit is seen
to say a client that connects. Then speakerd runs the headless build of loudness (forced to its
first setting, +12 dB shelf at 120 Hz, on both amplifier channels) on each virtual speaker at once:
on the left with DISPLAY naming that Xvfb, on the right with no DISPLAY. A 100 Hz tone is played
into each speakerd and its amplifier recorded; the Xvfb is then killed (SIGKILL), and after 3 s
(0.3.2 exited one to two seconds after) the tone is played and recorded again.

With no DISPLAY a speakerd before 0.3.3 opened :0.0, where no check runs an X server (the virtual
pair's displays are :100 up, VirtualDisplay's rule), so the right's run shows that speakerd starts,
plays and stops cleanly without DISPLAY, not that it would stay off a server on :0; the left's
shows that the DISPLAY speakerd is given, whatever it is, is not the one it opens, which is the
same code (NoDisplay.h).

Pass when:

- the audit says the test's own client connected, and no other client connected from the moment
  both speakerd were started until the Xvfb was killed (one would be speakerd's, or a plugin's);
- before the kill, each speaker's amplifier receives the tone (above -60 dBFS);
- 3 s after the kill both speakerd still run, and each amplifier receives the tone at the gain
  it had before the kill, within 0.05 dB on both channels;
- each speakerd, stopped with SIGTERM, says `stopping` once, as its last line, and exits with
  status 0.

Against 0.3.2 it fails: an X client connects, and (shown separately) speakerd exits when the Xvfb
goes. The client assertion comes before the kill and ends the test, so one run shows the first.

    python3 -m unittest -v tests.vpair.test_speakerd_no_display
"""

import re
import shutil
import socket
import struct
import tempfile
import time
import unittest
from pathlib import Path

from harness import (Pair, Speakerd, VirtualDisplay, active, built, graph_document, lv2_uri, rms_dbfs,
                     speakerd_version, state_dir, steady, tone)

# What this module's built() calls need, also through the harness (Speakerd, render, SpeakerPair,
# ScriptedController): tests/vpair/pool.py builds them before any test runs (README.md, "The pool").
POOL_BUILDS = ("speakerd", "loudness:headless")

DOCUMENT = graph_document(lv2_uri("loudness_headless"),
                          {"mode": "Force 1", "s0_e0_gain": 12.0, "s0_e0_freq": 120.0})
TONE = tone(100.0, -20.0, 1.0)
TOLERANCE_DB = 0.05
AFTER_KILL = 3.0          # seconds: 0.3.2 exited with status 1 one to two seconds after the server went
CONNECTED = re.compile(r"client (\d+) connected from (.*)")


class Speaking:
    """A speaker as harness.Speakerd sees it, with its environment's DISPLAY set to `display`, or
    removed for None."""

    def __init__(self, speaker, display):
        self.speaker, self.name = speaker, speaker.name
        self.env = dict(speaker.env)
        self.env.pop("DISPLAY", None)
        if display is not None:
            self.env["DISPLAY"] = display

    def health(self):
        return self.speaker.health()


def x_client(number, timeout=5.0):
    """Connects to display `number` as a minimal X client (the connection setup, no authorisation,
    as an Xvfb with no -auth takes from the same machine), and disconnects: whether the server
    accepted it."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(f"/tmp/.X11-unix/X{number}")
        # Byte order 'l', protocol 11.0, no authorisation name or data.
        s.sendall(b"l\0" + struct.pack("<HHHH", 11, 0, 0, 0) + b"\0\0")
        return s.recv(8)[:1] == b"\x01"


def owner(line, whose):
    """Who an audit line's client is: one of `whose` ({pid: name}) or its pid."""
    found = re.search(r"pid=(\d+)", line)
    pid = int(found.group(1)) if found else None
    return whose.get(pid, f"pid {pid}, not a speakerd" if pid else "a client of no pid")


def gains(output, source=TONE):
    part = steady(active(output))
    if len(part) == 0:
        return None
    level = rms_dbfs(steady(source))
    return [rms_dbfs(part[:, channel]) - level for channel in (0, 1)]


class NoDisplay(unittest.TestCase):
    def setUp(self):
        self.headless = built("loudness", "headless")
        built("speakerd")
        self.tmp = Path(tempfile.mkdtemp(prefix="vpair-no-display-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def connected(self, display, after):
        """The audit's "client N connected" lines from character `after` of its log on."""
        return [line.strip() for line in display.audit_log()[after:].splitlines() if CONNECTED.search(line)]

    def test_no_x_client_and_plays_on_after_the_server_goes(self):
        version = speakerd_version()
        with VirtualDisplay(size="640x480x24", audit=True, window_manager=False) as display, Pair() as pair:
            # The audit says a client that connects: this test's own, first.
            self.assertTrue(x_client(display.number), f"Xvfb :{display.number} refused a client: "
                                                      f"{display.audit_log()}")
            deadline = time.monotonic() + 5
            while not self.connected(display, 0) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(self.connected(display, 0), f"Xvfb -audit 4 on :{display.number} did not say the test's "
                                                        f"own client connected, so it would not say speakerd's: "
                                                        f"{display.audit_log()!r}")
            mark = len(display.audit_log())

            speakers = {"left": Speaking(pair["left"], display.env["DISPLAY"]),
                        "right": Speaking(pair["right"], None)}
            given = {"left": f"DISPLAY={display.env['DISPLAY']}", "right": "no DISPLAY"}
            with Speakerd(speakers["left"], state_dir(self.tmp / "left", current=DOCUMENT),
                          bundles=[self.headless]) as left, \
                    Speakerd(speakers["right"], state_dir(self.tmp / "right", role="right", current=DOCUMENT),
                             bundles=[self.headless]) as right:
                running = {"left": left, "right": right}
                before = {}
                for name in running:
                    before[name] = gains(pair[name].play_and_record(TONE, target="speakerd", record_target="amplifier"))
                    self.assertIsNotNone(before[name], f"{name} ({given[name]}): the tone played into speakerd did "
                                                       f"not reach the amplifier:\n{running[name].log()}")

                clients = self.connected(display, mark)
                whose = {sd.process.pid: f"the {name} speakerd ({given[name]})" for name, sd in running.items()}
                self.assertEqual(clients, [], f"speakerd {version}: X clients connected to Xvfb :{display.number} "
                                              f"while speakerd ran (left with {given['left']}, right with "
                                              f"{given['right']}), so speakerd or a plugin it hosts opened a display "
                                              f"(0.3.3 opens none, NoDisplay.h); whose: "
                                              + ", ".join(owner(c, whose) for c in clients))

                display.kill()
                time.sleep(AFTER_KILL)
                for name, sd in running.items():
                    self.assertIsNone(sd.process.poll(), f"{name} ({given[name]}): speakerd {version} exited with "
                                                         f"status {sd.process.returncode} after the X server went "
                                                         f"away:\n{sd.log()}")
                for name in running:
                    after = gains(pair[name].play_and_record(TONE, target="speakerd", record_target="amplifier"))
                    self.assertIsNotNone(after, f"{name} ({given[name]}): after the X server went away, the tone "
                                                f"played into speakerd did not reach the amplifier:\n"
                                                f"{running[name].log()}")
                    for channel, (b, a) in enumerate(zip(before[name], after)):
                        self.assertAlmostEqual(a, b, delta=TOLERANCE_DB,
                                               msg=f"{name} ({given[name]}), channel {channel}: {a:+.3f} dB after "
                                                   f"the X server went away, {b:+.3f} dB before")
                    print(f"\n  {name} ({given[name]}): {before[name][0]:+.2f} / {before[name][1]:+.2f} dB before "
                          f"the kill, {after[0]:+.2f} / {after[1]:+.2f} dB {AFTER_KILL:g} s after", end="")
            # Leaving the Speakerd blocks checked that each said `stopping` last and exited with 0.


if __name__ == "__main__":
    unittest.main()
