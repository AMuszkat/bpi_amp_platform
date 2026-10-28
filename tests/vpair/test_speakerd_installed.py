"""speakerd_installed (docs/autonomous-development.md, section 5): a virtual speaker set up the
way firmware/speakerd-setup.sh sets up a board plays through the persistent speakerd sink,
clocked by the amplifier, capped, from the bundle that was deployed.

Two versions are installed into one home (firmware/CLAUDE.md, "speakerd on a board"), the
second current, with a factory graph of loudness's headless build (+12 dB shelf at 120 Hz) on
both amplifier channels. Each speaker's PipeWire starts with the installed drop-in folder, as a
board's loads ~/.config/pipewire/pipewire.conf.d: the left one from speaker.conf, whose
amplifier has priority.driver 1000 like the board's ALSA sink in firmware/pipewire/pipewire.conf;
the right one with the amplifier's priority.driver removed, as a board's ALSA sink defined
without one. speakerd is started by the installed unit's ExecStart (harness.UserUnit), with
the unit's environment: an empty HOME, client-rt.conf. An older, broken copy of
loudness_headless.lv2 lies in the home's own ~/.lv2, where build-podman.sh -install puts
bundles.

Pass when:

- before any speakerd runs, the `speakerd` sink is in each speaker's PipeWire with its ports,
  made by the drop-in, at priority.driver 0, and is the default sink;
- speakerd, started by the unit, says it uses the sink that exists and plays factory;
- with a stream playing into the sink, pw-profiler shows the amplifier as the only driver on
  each speaker, with the speakerd sink, speakerd-dsp and the stream among its followers: the
  null sink never clocks the amplifier, whether the amplifier has priority 1000 or none;
- speakerd says `device: limits: output at -20 dB, tweeter muted`, every sample of the tweeter
  capture is 0, and the woofer's gain for a 100 Hz tone is the complete build's offline render
  of the same document (no limits) minus 20 dB, within 0.05 dB;
- speakerd loads loudness_headless from the current version's lv2/ folder, although the copy
  in the home's own ~/.lv2 has the same URI; the same command with HOME set to that home stops
  with status 2 (the copy would shadow it); after a rollback, the next start loads it from the
  previous version's folder; speakerd's HOME is still empty after it ran;
- firmware/speakerd-setup.sh smoke, with a stream playing, passes every check: it plays, it is
  capped, the live speakerd sink has the drop-in's properties and is the default sink, it is
  linked from the sink to the amplifier and nothing else feeds the amplifier, no AudioPluginHost
  runs, the amplifier drives, and no restart or xrun in 3 s;
- snapcast-setup.sh --route speakerd finds the drop-in's sink in the running PipeWire and routes;
- with a PipeWire that does NOT load the drop-in (the file installed, the speaker started without
  it): the route into speakerd is refused and client-rt.conf left as it was, both with no
  speakerd sink at all and with the sink speakerd made itself (which it says it made); smoke
  fails on the sink that is not the drop-in's; and `sink` and `install` refuse, saying nothing may
  be routed into speakerd.

The installer installs only a speakerd that enforces device.json's limits (SPEAKERD_MIN_VERSION
in firmware/speakerd-setup.sh), which came after commit 2cb3278: against an older one the
install in setUpClass refuses, and every test here fails, saying why.

    python3 -m unittest -v tests.vpair.test_speakerd_installed
"""

import json
import re
import shutil
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path

from harness import (FIRMWARE, HERE, RATE, TEST_PLUGIN_HOST, Pair, Player, UserUnit, active, built, drivers, firmware, graph_document,
                     lv2_uri, render, rms_dbfs, state_dir, steady, tone, with_plugin)

import numpy as np

DOCUMENT = graph_document(lv2_uri("loudness_headless"),
                          {"mode": "Force 1", "s0_e0_gain": 12.0, "s0_e0_freq": 120.0})
TONE = tone(100.0, -20.0, 1.0)
STREAM = tone(100.0, -20.0, 30.0)
CAP_DB = -20.0
TOLERANCE_DB = 0.05
RECORD_SECONDS, SKIP, MEASURED = 0.8, int(0.2 * RATE), int(0.5 * RATE)
LIMITS_LINE = "speakerd: device: limits: output at -20 dB, tweeter muted"
URI = lv2_uri("loudness_headless")


def version_id(bundle):
    return dict(line.split("=", 1) for line in (bundle / "VERSION").read_text().splitlines())["id"]


class Installed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        built("speakerd")
        headless = built("loudness", "headless")
        complete = built("loudness", "complete")
        cls.tmp = Path(tempfile.mkdtemp(prefix="vpair-installed-"))
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)
        cls.user_home = cls.tmp / "user"
        no_pipewire = {"XDG_RUNTIME_DIR": str(cls.tmp / "no-pipewire")}
        (cls.tmp / "no-pipewire").mkdir()

        bundles = []
        for name, plugins in (("one", ["loudness:headless"]), ("two", ["loudness:headless", "loudness:complete"])):
            out = cls.tmp / f"bundle-{name}"
            args = ["bundle", "--arch", "native", "--out", out]
            for plugin in plugins:
                args += ["--plugin", plugin]
            firmware("speakerd-setup.sh", *args)
            bundles.append(out)
        cls.previous, cls.current = (version_id(b) for b in bundles)
        factory = cls.tmp / "factory.json"
        factory.write_text(json.dumps(DOCUMENT, indent=2))
        firmware("speakerd-setup.sh", "install", "--bundle", bundles[0], "--home", cls.user_home, "--no-systemd",
                 "--role", "left", "--amplifier", "amplifier", "--factory", factory,
                 "--allow-tweeter-without-crossover", env=no_pipewire)
        firmware("speakerd-setup.sh", "install", "--bundle", bundles[1], "--home", cls.user_home, "--no-systemd",
                 env=no_pipewire)
        cls.lib = cls.user_home / ".local/lib/speakerd"

        # An older copy of the bundle in the home's own ~/.lv2, broken: were it the one loaded,
        # the factory layer could not load its plugin.
        old = cls.user_home / ".lv2" / "loudness_headless.lv2"
        shutil.copytree(headless, old)
        for library in old.glob("*.so"):
            library.write_bytes(b"not a shared library\n")

        # The reference: the complete build's render of the same document, with no limits.
        reference_state = state_dir(cls.tmp / "reference", current=with_plugin(DOCUMENT, lv2_uri("loudness")))
        output, log = render(reference_state, TONE, bundles=[complete])
        if "layer current (graph.json)" not in log:
            raise AssertionError(f"the render did not run the document:\n{log}")
        cls.reference = rms_dbfs(steady(active(output))[:, 0]) - rms_dbfs(steady(TONE))

        no_priority = cls.tmp / "speaker-no-priority.conf"
        text = (HERE / "speaker.conf").read_text()
        no_priority.write_text(re.sub(r"(?m)^\s*priority\.driver\s*=\s*1000\s*$\n", "", text))
        if re.search(r"(?m)^\s*priority\.driver", no_priority.read_text()):
            raise AssertionError("could not remove the amplifier's priority.driver from speaker.conf")
        dropins = cls.user_home / ".config/pipewire/pipewire.conf.d"
        cls.pair = Pair({"left": {"dropins": dropins},
                         "right": {"speaker_conf": no_priority, "dropins": dropins}}).__enter__()
        cls.addClassCleanup(cls.pair.__exit__, None, None, None)

        cls.before = {}
        for name in ("left", "right"):
            speaker = cls.pair[name]
            nodes = {o["info"]["props"].get("node.name"): o["info"]["props"]
                     for o in json.loads(speaker.run("pw-dump")) if o.get("type") == "PipeWire:Interface:Node"}
            cls.before[name] = {"has_sink": speaker.has_sink("speakerd"), "props": nodes.get("speakerd", {}),
                                "default": speaker.run("pw-metadata", "-n", "default", "0", "default.audio.sink")}

        cls.log = {name: cls.tmp / f"{name}.log" for name in ("left", "right")}
        cls.runs = {}
        cls.addClassCleanup(cls.stop_all)
        for name in ("left", "right"):
            cls.start(name)

    @classmethod
    def unit(cls, name):
        return UserUnit(cls.user_home / ".config/systemd/user/speakerd.service", cls.user_home, cls.pair[name])

    @classmethod
    def start(cls, name):
        run = cls.unit(name).start(log_path=cls.log[name])
        cls.runs[name] = run
        run.wait_for("speakerd: playing: ")
        return run

    @classmethod
    def stop(cls, name):
        run = cls.runs.pop(name, None)
        if run is not None:
            run.signal(signal.SIGTERM)
            return run.finish()

    @classmethod
    def stop_all(cls):
        for name in list(cls.runs):
            try:
                cls.stop(name)
            except AssertionError:
                pass

    # ---------------------------------------------------------------------------------------

    def test_the_sink_is_there_before_speakerd(self):
        for name in ("left", "right"):
            before = self.before[name]
            print(f"\n  {name}: sink {before['has_sink']}, priority.driver "
                  f"{before['props'].get('priority.driver')}, default {before['default'].strip()}")
            self.assertTrue(before["has_sink"], f"{name}: no speakerd sink before speakerd")
            self.assertEqual(before["props"].get("factory.name"), "support.null-audio-sink")
            self.assertEqual(before["props"].get("priority.driver"), 0)
            self.assertIn('"name":"speakerd"', before["default"])

    def test_speakerd_adopts_the_sink_and_plays_factory(self):
        for name in ("left", "right"):
            log = self.runs[name].log()
            self.assertIn("speakerd: pipewire: input sink 'speakerd' exists: using it", log)
            self.assertNotIn("speakerd: pipewire: created the input sink", log)
            self.assertRegex(log, r"(?m)^speakerd: using layer factory \(factory\.json\)")

    def test_the_amplifier_drives(self):
        for name in ("left", "right"):
            speaker = self.pair[name]
            with Player(speaker, STREAM, target="speakerd") as stream:
                found, followers, output = drivers(speaker)
            print(f"\n  {name}: drivers {sorted(found)}, followers {sorted(followers)}")
            self.assertEqual(found, {"amplifier"}, f"{name}: {output}")
            self.assertLessEqual({"speakerd", "speakerd-dsp", stream.name}, followers, f"{name}: {output}")

    def test_plays_capped(self):
        speaker = self.pair["left"]
        with Player(speaker, STREAM, target="speakerd"):
            capture = speaker.record(RECORD_SECONDS)
        part = capture[SKIP:SKIP + MEASURED]
        self.assertEqual(len(part), MEASURED)
        woofer = rms_dbfs(part[:, 0]) - rms_dbfs(TONE)
        tweeter = capture[:, 1]
        log = self.runs["left"].log()
        limits = [line for line in log.splitlines() if line.startswith("speakerd: device: limits:")
                  or line.startswith("speakerd: device: no limits")]
        summary = (f"woofer {woofer:+.3f} dB, render {self.reference:+.3f} dB (so {self.reference + CAP_DB:+.3f} "
                   f"expected), tweeter {np.count_nonzero(tweeter)} samples not 0; speakerd says: "
                   f"{limits[0] if limits else 'nothing about limits'}")
        print(f"\n  {summary}")
        hint = ("\n  (this needs speakerd's device.json limits, which came after 2cb3278: a speakerd "
                "without them ignores the cap)")
        self.assertIn(LIMITS_LINE, log.splitlines(), summary + hint)
        self.assertEqual(np.count_nonzero(tweeter), 0, f"the tweeter is not muted: {summary}{hint}")
        self.assertAlmostEqual(woofer, self.reference + CAP_DB, delta=TOLERANCE_DB, msg=summary + hint)

    def test_the_deployed_bundle_runs_and_rollback_switches_it(self):
        current = f"speakerd: lv2 bundle {self.lib / self.current / 'lv2/loudness_headless.lv2'}: {URI}"
        self.assertIn(current, self.runs["left"].log().splitlines())

        # The same command with the home's own ~/.lv2 in view: the old copy shadows the bundle.
        version = self.lib / self.current
        command = [str(version / "bin/speakerd"), "--state-dir", str(self.user_home / ".local/state/speakerd"),
                   "--lv2-bundle", str(version / "lv2/loudness_headless.lv2")]
        shadowed = subprocess.run(command, env={"HOME": str(self.user_home), "PATH": "/usr/bin:/bin",
                                                "XDG_RUNTIME_DIR": str(self.tmp / "no-pipewire")},
                                  stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
        print(f"\n  with HOME={self.user_home}: status {shadowed.returncode}")
        self.assertEqual(shadowed.returncode, 2, shadowed.stdout + shadowed.stderr)

        firmware("speakerd-setup.sh", "rollback", "--home", self.user_home, "--no-systemd")
        try:
            self.stop("left")
            run = self.start("left")
            previous = f"speakerd: lv2 bundle {self.lib / self.previous / 'lv2/loudness_headless.lv2'}: {URI}"
            self.assertIn(previous, run.log().splitlines(), run.log())
            self.assertRegex(run.log(), r"(?m)^speakerd: using layer factory")
        finally:
            firmware("speakerd-setup.sh", "rollback", "--home", self.user_home, "--no-systemd")
            self.stop("left")
            self.start("left")
        self.assertIn(current, self.runs["left"].log().splitlines())
        self.assertEqual(list((self.lib / "home").iterdir()), [], "speakerd wrote into its HOME")

    def test_smoke(self):
        speaker = self.pair["left"]
        env = {k: v for k, v in speaker.env.items() if k.startswith(("XDG_", "DBUS_"))}
        with Player(speaker, STREAM, target="speakerd"):
            result = firmware("speakerd-setup.sh", "smoke", "--home", self.user_home, "--no-systemd",
                              "--log", self.log["left"], "--window", "3", env=env, check=False)
        print("\n" + result.stdout)
        for check in ("pass it plays: layer factory", "pass capped: output at -20 dB, tweeter muted",
                      "pass the speakerd sink is the drop-in's (priority.session 1500, priority.driver 0)",
                      "pass the default sink is speakerd",
                      "pass linked: speakerd sink -> speakerd-dsp -> amplifier",
                      "pass nothing but speakerd-dsp plays into amplifier", f"pass no {TEST_PLUGIN_HOST} runs",
                      "pass amplifier drives the graph", "pass no restart in 3 s", "pass no xrun in 3 s"):
            self.assertIn(check, result.stdout)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_the_route_into_speakerd(self):
        env = {k: v for k, v in self.pair["left"].env.items() if k.startswith(("XDG_", "DBUS_"))}
        home = self.user_home
        result = firmware("snapcast-setup.sh", "--mode", "target", "--routing-only", "--route", "speakerd",
                          "--client-home", home, env=env)
        self.assertIn("The running PipeWire has the drop-in's speakerd sink", result.stdout)
        self.assertIn('target.object = "speakerd"', (home / ".config/pipewire/client-rt.conf").read_text())


class DropInNotLoaded(unittest.TestCase):
    """A speaker whose PipeWire does not load ~/.config/pipewire/pipewire.conf.d (as with another
    config name, or PIPEWIRE_CONFIG_DIR): the drop-in file is there, the sink is not. Nothing may
    be routed into speakerd, and smoke, sink and install say so."""

    @classmethod
    def setUpClass(cls):
        built("speakerd")
        built("loudness", "headless")
        cls.tmp = Path(tempfile.mkdtemp(prefix="vpair-no-dropin-"))
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)
        cls.user_home = cls.tmp / "user"
        (cls.tmp / "no-pipewire").mkdir()
        cls.bundle = cls.tmp / "bundle"
        firmware("speakerd-setup.sh", "bundle", "--arch", "native", "--out", cls.bundle, "--plugin", "loudness:headless")
        firmware("speakerd-setup.sh", "install", "--bundle", cls.bundle, "--home", cls.user_home, "--no-systemd",
                 "--role", "left", "--amplifier", "amplifier", env={"XDG_RUNTIME_DIR": str(cls.tmp / "no-pipewire")})
        cls.pair = Pair().__enter__()
        cls.addClassCleanup(cls.pair.__exit__, None, None, None)
        cls.env = {k: v for k, v in cls.pair["left"].env.items() if k.startswith(("XDG_", "DBUS_"))}

    def route(self):
        home = self.tmp / "routing"
        crt = home / ".config/pipewire/client-rt.conf"
        crt.parent.mkdir(parents=True, exist_ok=True)
        crt.write_text("# the routing there was\n")
        # The drop-in file exists in this home: only the live check can refuse.
        dropin = home / ".config/pipewire/pipewire.conf.d/60-speakerd-sink.conf"
        dropin.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIRMWARE / "pipewire/pipewire.conf.d/60-speakerd-sink.conf", dropin)
        result = firmware("snapcast-setup.sh", "--mode", "target", "--routing-only", "--route", "speakerd",
                          "--client-home", home, env=self.env, check=False)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(crt.read_text(), "# the routing there was\n", "client-rt.conf was rewritten")
        return result.stdout

    def test_nothing_is_routed_and_smoke_sink_and_install_refuse(self):
        said = self.route()
        print(f"\n  no sink: {said.strip().splitlines()[-1]}")
        self.assertIn("the running PipeWire has no speakerd sink", said)

        log = self.tmp / "speakerd.log"
        unit = UserUnit(self.user_home / ".config/systemd/user/speakerd.service", self.user_home, self.pair["left"])
        run = unit.start(log_path=log)
        try:
            run.wait_for("speakerd: playing: ")
            self.assertIn("speakerd: pipewire: created the input sink 'speakerd'", run.log())

            said = self.route()
            print(f"\n  speakerd's own sink: {said.strip().splitlines()[-1]}")
            self.assertIn("the running PipeWire's speakerd sink is not the drop-in's", said)

            smoke = firmware("speakerd-setup.sh", "smoke", "--home", self.user_home, "--no-systemd", "--log", log,
                             "--window", "0", env=self.env, check=False)
            self.assertNotEqual(smoke.returncode, 0, smoke.stdout)
            self.assertIn("FAIL the speakerd sink is not the drop-in's", smoke.stdout)

            for action in (["sink"], ["install", "--bundle", self.bundle]):
                refused = firmware("speakerd-setup.sh", *action, "--home", self.user_home, "--no-systemd",
                                   env=self.env, check=False)
                self.assertNotEqual(refused.returncode, 0, refused.stdout)
                self.assertIn("is not the drop-in's", refused.stdout)
                self.assertIn("Nothing may be routed into speakerd", refused.stdout)
        finally:
            run.signal(signal.SIGTERM)
            run.finish()


if __name__ == "__main__":
    unittest.main()
