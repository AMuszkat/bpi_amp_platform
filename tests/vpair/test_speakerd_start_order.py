"""Nothing speakerd needs has to be there first (docs/remote-plugin-host.md, section 8, rule 2).

speakerd starts right after PipeWire with no network dependency, and links itself to the
amplifier whenever its ports exist. Here it runs the headless build of loudness (forced to its
first setting, +12 dB shelf at 120 Hz) and is started:

- before the amplifier exists (device.json names a sink that appears later); and with the
  amplifier there, which then goes away and comes back;
- before the speaker's PipeWire runs (the right speaker is down when speakerd starts);
- in a network namespace of its own, with no network at all. This one needs `unshare -rn`
  (an unprivileged user and network namespace) and is skipped where the machine refuses it.

Pass when in each case speakerd says it is waiting (for the amplifier, for PipeWire), then
that it is linked and playing, without being restarted; a 100 Hz tone played into it then
reaches the amplifier with the gain of the complete build's render of the same document,
within 0.05 dB on both channels; after the amplifier returns, the same holds again; and with
no network, speakerd holds no TCP, UDP or raw socket.

    python3 -m unittest -v tests.vpair.test_speakerd_start_order
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from harness import (Pair, Speakerd, active, built, graph_document, lv2_uri, render, rms_dbfs,
                     state_dir, steady, tone, with_plugin)

DOCUMENT = graph_document(lv2_uri("loudness_headless"),
                          {"mode": "Force 1", "s0_e0_gain": 12.0, "s0_e0_freq": 120.0})
PLAYING = "playing: layer current (graph.json), revision 1, 48000 Hz"
TONE = tone(100.0, -20.0, 1.0)
TOLERANCE_DB = 0.05
LATE_AMPLIFIER = "late-amp"


def gains(output, source=TONE):
    part = steady(active(output))
    level = rms_dbfs(steady(source))
    return [rms_dbfs(part[:, channel]) - level for channel in (0, 1)]


def ip_sockets(pid):
    """The TCP, UDP and raw sockets process `pid` holds, as /proc/<pid>/net lists them."""
    fds = Path(f"/proc/{pid}/fd")
    inodes = set()
    for fd in fds.iterdir():
        try:
            target = os.readlink(fd)
        except OSError:
            continue
        if target.startswith("socket:["):
            inodes.add(target[len("socket:["):-1])
    held = []
    for table in ("tcp", "tcp6", "udp", "udp6", "raw", "raw6"):
        path = Path(f"/proc/{pid}/net/{table}")
        if not path.exists():
            continue
        for line in path.read_text().splitlines()[1:]:
            fields = line.split()
            if len(fields) > 9 and fields[9] in inodes:
                held.append(f"{table}: {line.strip()}")
    return held


class StartOrder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.headless = built("loudness", "headless")
        complete = built("loudness", "complete")
        built("speakerd")
        cls.tmp = Path(tempfile.mkdtemp(prefix="vpair-start-order-"))
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)

        state = state_dir(cls.tmp / "reference", current=with_plugin(DOCUMENT, lv2_uri("loudness")))
        output, log = render(state, TONE, bundles=[complete])
        if "layer current (graph.json)" not in log:
            raise AssertionError(f"the render did not run the document:\n{log}")
        cls.reference = gains(output)[0]

        cls.pair = Pair().__enter__()
        cls.addClassCleanup(cls.pair.__exit__, None, None, None)

    def check_sound(self, speaker, amplifier, when):
        capture = speaker.play_and_record(TONE, target="speakerd", record_target=amplifier)
        live = gains(capture)
        summary = (f"{when}: woofer {live[0]:+.3f} dB, tweeter {live[1]:+.3f} dB, "
                   f"complete build {self.reference:+.3f} dB")
        print(f"\n  {summary}")
        for channel, gain in zip(("woofer", "tweeter"), live):
            self.assertAlmostEqual(gain, self.reference, delta=TOLERANCE_DB,
                                   msg=f"{channel}: {summary}")

    def late_amplifier(self, case):
        """speakerd started for an amplifier that is not there yet, and the speaker."""
        speaker = self.pair["left"]
        state = state_dir(self.tmp / case, amplifier=LATE_AMPLIFIER, current=DOCUMENT)
        self.addCleanup(subprocess.run, ["pw-cli", "destroy", LATE_AMPLIFIER], env=speaker.env,
                        capture_output=True, timeout=10)
        return speaker, Speakerd(speaker, state, bundles=[self.headless],
                                 wait_for=f"waiting for amplifier '{LATE_AMPLIFIER}' and its ports")

    def appears(self, speaker, sd, after=0):
        speaker.create_sink(LATE_AMPLIFIER)
        sd.wait_for(f"linked to amplifier '{LATE_AMPLIFIER}' ", after=after)
        sd.wait_for(PLAYING, after=after)

    def test_amplifier_appears_later(self):
        speaker, speakerd = self.late_amplifier("late-amplifier")
        with speakerd as sd:
            self.assertEqual(sd.lines_starting("playing: "), [],
                             f"speakerd plays with no amplifier:\n{sd.log()}")
            self.appears(speaker, sd)
            self.check_sound(speaker, LATE_AMPLIFIER, "the amplifier appeared after speakerd")

    def test_amplifier_goes_away_and_comes_back(self):
        speaker, speakerd = self.late_amplifier("returning-amplifier")
        with speakerd as sd:
            self.appears(speaker, sd)
            mark = sd.mark()
            speaker.remove_node(LATE_AMPLIFIER)
            sd.wait_for(f"unlinked from amplifier '{LATE_AMPLIFIER}': linking again", after=mark)
            self.appears(speaker, sd, after=mark)
            self.check_sound(speaker, LATE_AMPLIFIER, "the amplifier went away and came back")

    def test_pipewire_starts_later(self):
        self.pair.down("right")
        try:
            speaker = self.pair["right"]   # its environment: where its PipeWire will be
            state = state_dir(self.tmp / "late-pipewire", role="right", current=DOCUMENT)
            # "cannot connect to PipeWire: <reason>: trying again every second" is the line
            # speakerd says while it waits; its README's table of log lines does not list it yet.
            with Speakerd(speaker, state, bundles=[self.headless],
                          wait_for="cannot connect to PipeWire") as sd:
                self.assertEqual(sd.lines_starting("pipewire: "), [], sd.log())
                speaker = self.pair.up("right")
                sd.wait_for("pipewire: 48000 Hz, quantum 1024")
                sd.wait_for(PLAYING)
                self.check_sound(speaker, "amplifier", "PipeWire started after speakerd")
        finally:
            self.pair.up("right")   # for whatever runs next, whatever happened here

    def test_no_network(self):
        probe = subprocess.run(["unshare", "-rn", "true"], capture_output=True, text=True)
        if probe.returncode != 0:
            self.skipTest(f"no network namespace here (unshare -rn: {probe.stderr.strip()})")

        speaker = self.pair["left"]
        state = state_dir(self.tmp / "no-network", current=DOCUMENT)
        with Speakerd(speaker, state, bundles=[self.headless], prefix=["unshare", "-rn"]) as sd:
            pid = sd.process.pid
            self.assertEqual(Path(f"/proc/{pid}/comm").read_text().strip(), "speakerd")
            interfaces = [line.split(":")[0].strip()
                          for line in Path(f"/proc/{pid}/net/dev").read_text().splitlines()[2:]]
            self.assertEqual(interfaces, ["lo"], "speakerd's namespace has a network")
            self.check_sound(speaker, "amplifier", "no network")
            held = ip_sockets(pid)
            self.assertEqual(held, [], "speakerd holds network sockets:\n" + "\n".join(held))


if __name__ == "__main__":
    unittest.main()
