"""speakerd_crash_restart (docs/autonomous-development.md, section 5): the unit that runs
speakerd on a board starts it on current, on last known good after a crash, and on factory
after three crashes within 60 s (docs/remote-plugin-host.md, section 8: "A graph that crashes
speakerd").

speakerd writes no file and keeps no count of its crashes; the unit's ExecStart,
firmware/speakerd/speakerd-run, does (firmware/CLAUDE.md, "speakerd on a board"). There is no
systemd in the container, so harness.UserUnit runs the installed speakerd.service as systemd
would: its Environment= lines, ExecStart=, and after the process has ended ExecStopPost= with
SERVICE_RESULT, EXIT_CODE and EXIT_STATUS set as systemd sets them. Whether systemd restarts
the service is read from the unit's Restart= lines with systemd's documented rules.

Pass when:

- the installed unit orders itself after pipewire.service with no network unit anywhere,
  restarts on failure after 1 s, never after exit 2, has no start limit
  (StartLimitIntervalSec=0) and gives speakerd an empty HOME;
- the real speakerd, started by the unit on the left virtual speaker, starts on current; killed
  by SIGSEGV, it is recorded as a crash, systemd would restart it, and the next start says
  `starting at layer last-known-good (--from)`; stopped by SIGTERM it exits 0, is recorded as
  clean, is not restarted, and the next start is on current again. The installer installs only
  a speakerd that enforces device.json's limits (SPEAKERD_MIN_VERSION), so against an older
  one (2cb3278's) this part fails at the install, and says why;
- with a stand-in speakerd that dies as it is told, the starts go: current, then after each of
  six crashes last-known-good, last-known-good, factory (three crashes within 60 s), factory,
  factory, passthrough (three crashes on factory); exit 3 (PipeWire) restarts on the same
  layer; SIGTERM is clean and the next start is on current; exit 2 is not restarted; SIGPIPE
  and an abort with a core dump are crashes, and SIGPIPE is restarted although systemd counts
  it as clean;
- crashes older than 60 s do not count towards factory: three of them, then a start, go to
  last-known-good, not factory; a start whose end was never recorded counts as a crash;
- once five starts failed within 60 s, a start first waits (SPEAKERD_BACKOFF, 0.5 s here) and
  says so;
- while a process called AudioPluginHost runs, the unit's ExecStart does not start speakerd and
  says why, and starts it once that process has gone.

    python3 -m unittest -v tests.vpair.test_speakerd_crash_restart
"""

import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from harness import FIRMWARE, Pair, UserUnit, built, firmware, stub_speakerd

LAYER_LINE = "speakerd: stub:"
MIN_VERSION = re.search(r"(?m)^SPEAKERD_MIN_VERSION=(\S+)$", (FIRMWARE / "speakerd-setup.sh").read_text()).group(1)
PAIR = None


def setUpModule():
    global PAIR
    PAIR = Pair().__enter__()
    unittest.addModuleCleanup(PAIR.__exit__, None, None, None)


def from_layer(line):
    words = line.split()
    return words[words.index("--from") + 1]


class Supervision(unittest.TestCase):
    """The unit and its wrapper, with the stand-in speakerd."""

    @classmethod
    def setUpClass(cls):
        headless = built("loudness", "headless")
        cls.tmp = Path(tempfile.mkdtemp(prefix="vpair-crash-"))
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)
        no_pipewire = {"XDG_RUNTIME_DIR": str(cls.tmp / "no-pipewire"), "STUB_VERSION": MIN_VERSION}
        (cls.tmp / "no-pipewire").mkdir()

        # The stand-in, installed as on a board.
        root = cls.tmp / "stub-build"
        (root / "speakerd/speakerd_artefacts/Release").mkdir(parents=True)
        shutil.copy(stub_speakerd(), root / "speakerd/speakerd_artefacts/Release/speakerd")
        (root / "loudness").mkdir()
        os.symlink(headless.parents[2], root / "loudness/loudness_headless_artefacts")
        stub = cls.tmp / "bundle-stub"
        firmware("speakerd-setup.sh", "bundle", "--arch", "native", "--out", stub, "--plugin", "loudness:headless",
                 env={"BUILD_ROOT": str(root)})
        cls.stub_home = cls.tmp / "stub-home"
        firmware("speakerd-setup.sh", "install", "--bundle", stub, "--home", cls.stub_home, "--no-systemd",
                 "--role", "left", "--amplifier", "amplifier", env=no_pipewire)
        cls.stub_dir = cls.tmp / "stub"
        cls.stub_dir.mkdir()

    def unit(self, home):
        return UserUnit(home / ".config/systemd/user/speakerd.service", home, PAIR["left"],
                        env={"STUB_DIR": str(self.stub_dir), "SPEAKERD_BACKOFF": "0.5"})

    def history(self):
        return Path(PAIR["left"].env["XDG_RUNTIME_DIR"]) / "speakerd" / "history"

    def setUp(self):
        self.history().unlink(missing_ok=True)

    def stub_start(self, unit, fate):
        """One start of the stand-in, which then meets `fate`; returns (its --from, the result)."""
        (self.stub_dir / "fate").write_text(fate)
        run = unit.start()
        line = run.wait_for(LAYER_LINE, timeout=15)
        if fate == "run":
            run.signal(signal.SIGTERM)
        result = run.finish()
        result["log"] = run.log()
        return from_layer(line), result

    # ---------------------------------------------------------------------------------------

    def test_the_unit(self):
        unit = self.unit(self.stub_home)
        text = (self.stub_home / ".config/systemd/user/speakerd.service").read_text()
        self.assertRegex(text, r"(?m)^After=.*\bpipewire\.service\b")
        self.assertNotRegex(text, r"(?m)^(After|Wants|Requires|BindsTo)=.*network")
        self.assertEqual(unit.service["Restart"], ["on-failure"])
        self.assertEqual(unit.service["RestartSec"], ["1"])
        self.assertIn("2", unit.service["RestartPreventExitStatus"][0].split())
        self.assertRegex(text, r"(?m)^StartLimitIntervalSec=0$")
        self.assertEqual(unit.environment()["HOME"], str(self.stub_home / ".local/lib/speakerd/home"))

    def test_the_layers_after_crashes(self):
        unit = self.unit(self.stub_home)
        starts = []
        for fate in ("segv",) * 6 + ("exit 3", "run", "exit 3", "exit 2"):
            layer, result = self.stub_start(unit, fate)
            starts.append((fate, layer, result["EXIT_STATUS"], unit.would_restart(result)))
            last = result
        print("\n  " + "\n  ".join(f"{fate:7} from {layer:16} ended {status:5} restart {restart}"
                                   for fate, layer, status, restart in starts))
        self.assertEqual([layer for _, layer, _, _ in starts],
                         ["current", "last-known-good", "last-known-good", "factory", "factory", "factory",
                          "passthrough", "passthrough", "current", "current"])
        self.assertEqual([restart for _, _, _, restart in starts],
                         [True] * 6 + [True, False, True, False])
        self.assertIn("speakerd ended: badcmd", last["stop_post"])

    def test_backoff_is_said(self):
        unit = self.unit(self.stub_home)
        logs = []
        for _ in range(6):
            _, result = self.stub_start(unit, "segv")
            logs.append(result["log"])
        self.assertNotIn("waiting", "\n".join(logs[:5]))
        self.assertIn("5 starts failed within 60 s: waiting 0.5 s before the next", logs[5])

    def test_sigpipe_and_core_dumps_are_crashes(self):
        unit = self.unit(self.stub_home)
        layer, piped = self.stub_start(unit, "pipe")
        self.assertEqual((layer, piped["SERVICE_RESULT"], piped["EXIT_STATUS"]), ("current", "success", "PIPE"))
        self.assertIn("speakerd ended: crash", piped["stop_post"])
        self.assertTrue(unit.would_restart(piped))
        (self.stub_dir / "fate").write_text("abrt")
        run = unit.start()
        self.assertEqual(from_layer(run.wait_for(LAYER_LINE)), "last-known-good")
        dumped = run.finish(core_dumped=True)
        self.assertEqual((dumped["EXIT_CODE"], dumped["EXIT_STATUS"]), ("dumped", "ABRT"))
        self.assertIn("speakerd ended: crash", dumped["stop_post"])
        self.assertTrue(unit.would_restart(dumped))

    def test_old_crashes_do_not_count(self):
        unit = self.unit(self.stub_home)
        now = int(time.time())
        history = self.history()
        history.parent.mkdir(parents=True, exist_ok=True)
        old = [f"start {now - 200} current", f"exit {now - 199} crash killed:SEGV:signal",
               f"start {now - 198} last-known-good", f"exit {now - 197} crash killed:SEGV:signal",
               f"start {now - 196} last-known-good", f"exit {now - 61} crash killed:SEGV:signal"]
        history.write_text("\n".join(old) + "\n")
        self.assertEqual(self.stub_start(unit, "exit 0")[0], "last-known-good")
        recent = [f"start {now - 30} current", f"exit {now - 29} crash killed:SEGV:signal",
                  f"start {now - 28} last-known-good", f"exit {now - 27} crash killed:SEGV:signal",
                  f"start {now - 26} last-known-good", f"exit {now - 25} crash killed:SEGV:signal"]
        history.write_text("\n".join(recent) + "\n")
        self.assertEqual(self.stub_start(unit, "exit 0")[0], "factory")
        history.write_text(f"start {now - 10} current\n")
        layer, result = self.stub_start(unit, "exit 0")
        self.assertEqual(layer, "last-known-good")
        self.assertIn("has no exit recorded, counted as a crash", result["log"])

    def test_waits_for_an_audiopluginhost(self):
        fake = self.tmp / "bin" / "AudioPluginHost"
        fake.parent.mkdir(exist_ok=True)
        shutil.copy("/bin/sleep", fake)
        host = subprocess.Popen([str(fake), "60"])
        self.addCleanup(lambda: (host.kill(), host.wait()))
        time.sleep(0.2)
        unit = self.unit(self.stub_home)
        (self.stub_dir / "fate").write_text("run")
        run = unit.start()
        said = run.wait_for("speakerd-run: *** AudioPluginHost runs", timeout=10)
        time.sleep(2.5)
        self.assertNotIn(LAYER_LINE, run.log(), "speakerd started while an AudioPluginHost runs")
        host.kill()
        host.wait()
        gone = time.monotonic()
        run.wait_for(LAYER_LINE, timeout=10)
        print(f"\n  {said}\n  started {time.monotonic() - gone:.1f} s after it had gone")
        run.signal(signal.SIGTERM)
        run.finish()


class RealSpeakerd(unittest.TestCase):
    """The unit and its wrapper with the real speakerd, installed as on a board."""

    @classmethod
    def setUpClass(cls):
        built("speakerd")
        built("loudness", "headless")
        cls.tmp = Path(tempfile.mkdtemp(prefix="vpair-crash-real-"))
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)
        (cls.tmp / "no-pipewire").mkdir()
        real = cls.tmp / "bundle-real"
        firmware("speakerd-setup.sh", "bundle", "--arch", "native", "--out", real, "--plugin", "loudness:headless")
        cls.real_home = cls.tmp / "real-home"
        firmware("speakerd-setup.sh", "install", "--bundle", real, "--home", cls.real_home, "--no-systemd",
                 "--role", "left", "--amplifier", "amplifier", env={"XDG_RUNTIME_DIR": str(cls.tmp / "no-pipewire")})

    def setUp(self):
        (Path(PAIR["left"].env["XDG_RUNTIME_DIR"]) / "speakerd" / "history").unlink(missing_ok=True)

    def test_the_real_speakerd_after_a_segfault(self):
        unit = UserUnit(self.real_home / ".config/systemd/user/speakerd.service", self.real_home, PAIR["left"])
        run = unit.start()
        run.wait_for("speakerd: using layer ")
        self.assertIn("from layer current", run.wait_for("speakerd-run: starting speakerd"))
        run.signal(signal.SIGSEGV)
        crashed = run.finish()
        print(f"\n  SIGSEGV: {crashed['SERVICE_RESULT']}/{crashed['EXIT_CODE']}/{crashed['EXIT_STATUS']}; "
              f"{crashed['stop_post'].strip()}")
        self.assertEqual(crashed["EXIT_STATUS"], "SEGV")
        self.assertIn("speakerd ended: crash", crashed["stop_post"])
        self.assertTrue(unit.would_restart(crashed))

        run = unit.start()
        line = run.wait_for("speakerd: starting at layer ")
        self.assertEqual(line, "speakerd: starting at layer last-known-good (--from)", run.log())
        run.wait_for("speakerd: using layer ")
        run.signal(signal.SIGTERM)
        stopped = run.finish()
        self.assertEqual((stopped["EXIT_CODE"], stopped["EXIT_STATUS"]), ("exited", "0"), run.log())
        self.assertIn("speakerd ended: clean", stopped["stop_post"])
        self.assertFalse(unit.would_restart(stopped))

        run = unit.start()
        self.assertIn("from layer current", run.wait_for("speakerd-run: starting speakerd"))
        run.wait_for("speakerd: using layer ")
        self.assertNotIn("starting at layer", run.log())
        run.signal(signal.SIGTERM)
        run.finish()


if __name__ == "__main__":
    unittest.main()
