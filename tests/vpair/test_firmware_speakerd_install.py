"""firmware_speakerd_install (docs/autonomous-development.md, section 5): the installer's local
mode lays speakerd out as firmware/CLAUDE.md documents it, always capped, and refuses what is
unsafe.

firmware/speakerd-setup.sh runs here as it runs on a board, with two test hooks: --home (a
fresh folder stands for the audio user's home) and --no-systemd (the container has no systemd:
the systemctl calls are only printed). The bundles are made by the installer's own bundle step.
What is under test is the installer, so their speakerd is the stand-in of stub_speakerd.c,
which says the version STUB_VERSION gives it (the installer's minimum, SPEAKERD_MIN_VERSION,
unless a test says otherwise), next to the real loudness_headless build. Nothing here reaches
a board.

Pass when:

- the bundle holds bin/speakerd, lv2/loudness_headless.lv2 and a VERSION whose id is the
  commit, "-dirty" if the tree is, and the hash of its contents;
- a speakerd that cannot say its version (as before --version) or is older than the minimum is
  refused by install, which installs nothing, and by rollback;
- a first install without --role is refused, and installs nothing;
- the first install lays out, in the home: .local/lib/speakerd/<id>/ with bin/speakerd,
  bin/speakerd-run, lv2/loudness_headless.lv2, VERSION (with the speakerd version) and setup/
  (the installer); current -> <id>; an empty home/ (speakerd's HOME);
  .local/state/speakerd/device.json exactly {version 1, role left, amplifier {node, woofer
  playback_FL, tweeter playback_FR}, limits {gain_db -20.0, tweeter muted}}; factory.json, the
  document given; the unit and the PipeWire drop-in, byte for byte the tracked firmware/ files;
  and graph.json and graph.lkg.json, there before, unchanged byte for byte; and it says every
  library speakerd and its plugins need is there;
- installing the same bundle again adds no version and changes nothing, but a device.json whose
  limits a person lifted (0 dB, tweeter on) is capped again, keeping its role and amplifier;
  --role right changes the role alone; a device.json with a key speakerd does not know, or a
  key twice, is refused (speakerd would reject it and play its fail-safe defaults);
- a second, different bundle becomes current, the first previous; rollback swaps them, and
  rollback again swaps them back; with --keep 2 a third version removes the oldest, never
  current or previous;
- while a process called AudioPluginHost runs, install refuses, says so, and installs
  nothing; with --stop-plugin-host it stops that process and installs; a file that would
  start one again is named;
- a factory graph that sends the stream to the tweeter without a crossover is refused, and is
  accepted with --allow-tweeter-without-crossover;
- --import-filtergraph writes speakerd's conversion as factory.json, having given speakerd
  the board's device.json (--device), and a conversion that fails (exit 2) is refused;
- the amplifier is found in a pw-dump: the one ALSA sink; with several, bpi-alsa-playback-pcm
  or alsa-playback-pcm; none or an ambiguous set is refused, naming the sinks; on a virtual
  speaker (whose amplifier is a null sink) install refuses without --amplifier, and with
  --amplifier amplifier reads its ports playback_FL and playback_FR;
- smoke passes the exact line `speakerd: device: limits: output at -20 dB, tweeter muted` when
  device.json has those limits, and reports, each as a failure: a `device.json: FAILED` line,
  the limits line with either fail-safe suffix (or any other), and no limits line at all;
- snapcast-setup.sh --route speakerd refuses while no PipeWire config defines the speakerd
  sink, and after `speakerd-setup.sh sink` routes snapclient (and shairport-sync and an
  AudioPluginHost) into "speakerd"; uninstall routes snapclient back to Ap.cap first, removes
  the unit, the drop-in and the versions and keeps the state, and --purge removes that too;
- setup.sh checks before it changes: --dry-run changes nothing; --route speakerd without the
  pipewire component and without the sink is refused before anything changes; and the
  components pipewire, snapcast (re-routing an installed snapclient) and speakerd install in
  that order.

    python3 -m unittest -v tests.vpair.test_firmware_speakerd_install
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from harness import FIRMWARE, Pair, built, firmware, graph_document, lv2_uri, stub_speakerd

DOCUMENT = graph_document(lv2_uri("loudness_headless"), {"mode": "Force 1", "s0_e0_gain": 12.0})
CAPPED = {"gain_db": -20.0, "tweeter": "muted"}
MIN_VERSION = re.search(r"(?m)^SPEAKERD_MIN_VERSION=(\S+)$", (FIRMWARE / "speakerd-setup.sh").read_text()).group(1)


def tree(root):
    """Every path under `root`, relative, folders marked with a slash; symlinks as "-> target"."""
    out = set()
    for path in sorted(Path(root).rglob("*")):
        rel = str(path.relative_to(root))
        if path.is_symlink():
            out.add(f"{rel} -> {os.readlink(path)}")
        elif path.is_dir():
            out.add(rel + "/")
        else:
            out.add(rel)
    return out


def version_file(path):
    return dict(line.split("=", 1) for line in Path(path).read_text().splitlines())


def stub_build_root(root):
    """A build tree with the stand-in speakerd and the real loudness builds, for the bundle step
    (BUILD_ROOT)."""
    (root / "speakerd/speakerd_artefacts/Release").mkdir(parents=True, exist_ok=True)
    shutil.copy(stub_speakerd(), root / "speakerd/speakerd_artefacts/Release/speakerd")
    (root / "loudness").mkdir(exist_ok=True)
    for kind in ("headless", "complete"):
        target = "loudness" if kind == "complete" else f"loudness_{kind}"
        link = root / f"loudness/{target}_artefacts"
        if not link.exists():
            os.symlink(built("loudness", kind).parents[2], link)
    return root


class Install(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        built("loudness", "headless")
        built("loudness", "complete")
        cls.tmp = Path(tempfile.mkdtemp(prefix="vpair-install-"))
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)
        # No PipeWire here unless a test brings one up: pw-dump fails at once.
        (cls.tmp / "no-pipewire").mkdir()
        (cls.tmp / "stub").mkdir()
        cls.env = {"XDG_RUNTIME_DIR": str(cls.tmp / "no-pipewire"), "STUB_VERSION": MIN_VERSION,
                   "STUB_DIR": str(cls.tmp / "stub")}
        cls.root = stub_build_root(cls.tmp / "build")
        cls.bundle = cls.make_bundle("one", ["loudness:headless"])
        cls.bundle2 = cls.make_bundle("two", ["loudness:headless", "loudness:complete"])
        cls.bundle3 = cls.make_bundle("three", ["loudness:complete"])
        cls.factory = cls.tmp / "factory.json"
        cls.factory.write_text(json.dumps(DOCUMENT, indent=2))

    @classmethod
    def make_bundle(cls, name, plugins):
        out = cls.tmp / f"bundle-{name}"
        args = ["bundle", "--arch", "native", "--out", out]
        for plugin in plugins:
            args += ["--plugin", plugin]
        firmware("speakerd-setup.sh", *args, env={"BUILD_ROOT": str(cls.root)})
        return out

    def install(self, home, *args, bundle=None, check=True, env=None):
        return firmware("speakerd-setup.sh", "install", "--bundle", bundle or self.bundle, "--home", home,
                        "--no-systemd", "--amplifier", "amplifier", *args, env=env or self.env, check=check)

    def fresh_home(self, name):
        path = self.tmp / name
        shutil.rmtree(path, ignore_errors=True)
        return path

    # ---------------------------------------------------------------------------------------

    def test_the_bundle(self):
        files = tree(self.bundle)
        for want in ("bin/speakerd", "lv2/loudness_headless.lv2/", "VERSION",
                     "firmware/speakerd-setup.sh", "firmware/speakerd/speakerd-run",
                     "firmware/pipewire/pipewire.conf.d/60-speakerd-sink.conf"):
            self.assertIn(want, files)
        version = version_file(self.bundle / "VERSION")
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=FIRMWARE, capture_output=True,
                                text=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=FIRMWARE, capture_output=True,
                                    text=True).stdout.strip())
        print(f"\n  bundle {version['id']}")
        self.assertEqual(version["commit"], commit)
        self.assertEqual(version["dirty"], "yes" if dirty else "no")
        self.assertRegex(version["id"], rf"^{commit[:12]}{'-dirty' if dirty else ''}-[0-9a-f]{{8}}$")
        self.assertNotEqual(version_file(self.bundle / "VERSION")["id"], version_file(self.bundle2 / "VERSION")["id"])

    def test_refuses_a_speakerd_that_ignores_the_limits(self):
        home = self.fresh_home("old-speakerd")
        older = "0.0.1"
        for env, said in ((dict(self.env, STUB_VERSION=older), f"is {older}"),
                          ({k: v for k, v in self.env.items() if k != "STUB_VERSION"}, "too old to say its version")):
            result = self.install(home, "--role", "left", env=env, check=False)
            print(f"\n  {said}: status {result.returncode}")
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn(said, result.stdout)
            self.assertIn(f"needs speakerd {MIN_VERSION} or newer", result.stdout)
            self.assertFalse((home / ".local/lib/speakerd").exists(), result.stdout)

        # Rollback to a version whose speakerd is older: refused too.
        self.install(home, "--role", "left")
        self.install(home, bundle=self.bundle2)
        lib = home / ".local/lib/speakerd"
        previous = os.readlink(lib / "previous")
        version = lib / previous / "VERSION"
        version.write_text(re.sub(r"(?m)^speakerd_version=.*$", f"speakerd_version={older}", version.read_text()))
        refused = firmware("speakerd-setup.sh", "rollback", "--home", home, "--no-systemd", env=self.env, check=False)
        self.assertNotEqual(refused.returncode, 0, refused.stdout)
        self.assertIn(f"is {older}", refused.stdout)
        self.assertEqual(os.readlink(lib / "previous"), previous)

    def test_the_first_install_needs_a_role(self):
        home = self.fresh_home("no-role")
        result = self.install(home, check=False)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("--role left|right|mono is required", result.stdout)
        self.assertFalse((home / ".local/lib/speakerd").exists(), result.stdout)

    def test_the_layout(self):
        home = self.fresh_home("layout")
        state = home / ".local/state/speakerd"
        state.mkdir(parents=True)
        (state / "graph.json").write_text('{"ampd": "current"}')
        (state / "graph.lkg.json").write_text('{"ampd": "last known good"}')
        result = self.install(home, "--role", "left", "--factory", self.factory,
                              "--allow-tweeter-without-crossover")
        self.assertIn("Every library speakerd and its plugins need is here", result.stdout)
        vid = version_file(self.bundle / "VERSION")["id"]
        files = tree(home)
        for want in (f".local/lib/speakerd/{vid}/bin/speakerd", f".local/lib/speakerd/{vid}/bin/speakerd-run",
                     f".local/lib/speakerd/{vid}/lv2/loudness_headless.lv2/", f".local/lib/speakerd/{vid}/VERSION",
                     f".local/lib/speakerd/{vid}/setup/speakerd-setup.sh",
                     f".local/lib/speakerd/current -> {vid}", ".local/lib/speakerd/home/",
                     ".local/state/speakerd/device.json", ".local/state/speakerd/factory.json",
                     ".config/systemd/user/speakerd.service",
                     ".config/pipewire/pipewire.conf.d/60-speakerd-sink.conf"):
            self.assertIn(want, files, "\n".join(sorted(files)))
        self.assertEqual(version_file(home / f".local/lib/speakerd/{vid}/VERSION")["speakerd_version"], MIN_VERSION)
        self.assertEqual(list((home / ".local/lib/speakerd/home").iterdir()), [])
        self.assertEqual(json.loads((state / "device.json").read_text()),
                         {"version": 1, "role": "left",
                          "amplifier": {"node": "amplifier", "woofer": "playback_FL", "tweeter": "playback_FR"},
                          "limits": CAPPED})
        self.assertEqual(json.loads((state / "factory.json").read_text()), DOCUMENT)
        self.assertEqual((home / ".config/systemd/user/speakerd.service").read_bytes(),
                         (FIRMWARE / "speakerd/speakerd.service").read_bytes())
        self.assertEqual((home / ".config/pipewire/pipewire.conf.d/60-speakerd-sink.conf").read_bytes(),
                         (FIRMWARE / "pipewire/pipewire.conf.d/60-speakerd-sink.conf").read_bytes())
        self.assertEqual((state / "graph.json").read_text(), '{"ampd": "current"}')
        self.assertEqual((state / "graph.lkg.json").read_text(), '{"ampd": "last known good"}')

    def test_a_reinstall_keeps_the_device_and_caps_it_again(self):
        home = self.fresh_home("reinstall")
        self.install(home, "--role", "left")
        device_json = home / ".local/state/speakerd/device.json"
        device = json.loads(device_json.read_text())
        device["limits"] = {"gain_db": 0.0, "tweeter": "on"}
        device["amplifier"]["tweeter"] = "playback_FL"
        device["amplifier"]["woofer"] = "playback_FR"
        device_json.write_text(json.dumps(device))
        before = tree(home / ".local/lib/speakerd")

        firmware("speakerd-setup.sh", "install", "--bundle", self.bundle, "--home", home, "--no-systemd",
                 env=self.env)
        self.assertEqual(tree(home / ".local/lib/speakerd"), before)
        again = json.loads(device_json.read_text())
        self.assertEqual(again["limits"], CAPPED)
        self.assertEqual((again["role"], again["amplifier"]),
                         ("left", {"node": "amplifier", "woofer": "playback_FR", "tweeter": "playback_FL"}))

        firmware("speakerd-setup.sh", "install", "--bundle", self.bundle, "--home", home, "--no-systemd",
                 "--role", "right", env=self.env)
        right = json.loads(device_json.read_text())
        self.assertEqual(right, dict(again, role="right"))

        for text, said in ((json.dumps(dict(right, trim=1.5)), "trim"),
                           (json.dumps(dict(right, amplifier=dict(right["amplifier"], gain=1))), "amplifier.gain"),
                           ('{"version": 1, "role": "left", "role": "right"}', "role appear twice")):
            device_json.write_text(text)
            refused = firmware("speakerd-setup.sh", "install", "--bundle", self.bundle, "--home", home,
                               "--no-systemd", env=self.env, check=False)
            self.assertNotEqual(refused.returncode, 0, refused.stdout)
            self.assertIn(said, refused.stdout)
            self.assertEqual(device_json.read_text(), text, "a refused device.json was changed")

    def test_versions_rollback_and_pruning(self):
        home = self.fresh_home("versions")
        lib = home / ".local/lib/speakerd"
        one, two, three = (version_file(b / "VERSION")["id"] for b in (self.bundle, self.bundle2, self.bundle3))

        def link(name):
            return os.readlink(lib / name) if (lib / name).is_symlink() else None

        self.install(home, "--role", "left")
        self.install(home, bundle=self.bundle2)
        self.assertEqual((link("current"), link("previous")), (two, one))
        firmware("speakerd-setup.sh", "rollback", "--home", home, "--no-systemd", env=self.env)
        self.assertEqual((link("current"), link("previous")), (one, two))
        firmware("speakerd-setup.sh", "rollback", "--home", home, "--no-systemd", env=self.env)
        self.assertEqual((link("current"), link("previous")), (two, one))
        status = firmware("speakerd-setup.sh", "status", "--home", home, "--no-systemd", env=self.env).stdout
        self.assertRegex(status, rf"current +{two} +\(speakerd {re.escape(MIN_VERSION)},")
        self.assertRegex(status, rf"previous +{one}")

        # A third version with --keep 2: the oldest goes, current and previous stay.
        self.install(home, "--keep", "2", bundle=self.bundle3)
        self.assertEqual((link("current"), link("previous")), (three, two))
        self.assertFalse((lib / one).exists(), "\n".join(sorted(tree(lib))))
        self.assertTrue((lib / two).is_dir())

    def test_refuses_while_an_audiopluginhost_runs(self):
        home = self.fresh_home("plugin-host")
        fake = self.tmp / "bin" / "AudioPluginHost"
        fake.parent.mkdir(exist_ok=True)
        shutil.copy("/bin/sleep", fake)
        host = subprocess.Popen([str(fake), "60"])
        self.addCleanup(lambda: (host.kill(), host.wait()))
        time.sleep(0.2)
        autostart = home / ".config/autostart/plugin-host.desktop"
        autostart.parent.mkdir(parents=True)
        autostart.write_text("[Desktop Entry]\nExec=/opt/AudioPluginHost session.filtergraph\n")

        result = self.install(home, "--role", "left", check=False)
        print(f"\n  refused with status {result.returncode}")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(f"AudioPluginHost runs (pid {host.pid})", result.stdout)
        self.assertIn("Refusing to install", result.stdout)
        self.assertFalse((home / ".local/lib/speakerd").exists())
        self.assertIsNone(host.poll(), "the AudioPluginHost was stopped without --stop-plugin-host")

        result = self.install(home, "--role", "left", "--stop-plugin-host")
        self.assertIsNotNone(host.poll(), f"the AudioPluginHost still runs:\n{result.stdout}")
        self.assertIn("Stopping AudioPluginHost", result.stdout)
        self.assertIn(str(autostart), result.stdout)
        self.assertTrue((home / ".local/lib/speakerd/current").is_symlink())

    def test_refuses_a_full_range_tweeter(self):
        home = self.fresh_home("tweeter")
        result = self.install(home, "--role", "left", "--factory", self.factory, check=False)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("reaches the tweeter without a crossover", result.stdout)
        self.assertFalse((home / ".local/state/speakerd/factory.json").exists())
        crossover = dict(DOCUMENT, nodes=DOCUMENT["nodes"] + [
            {"uid": 17, "plugin": {"format": "LV2", "identifier": lv2_uri("crossover")}}])
        crossover["connections"] = [
            {"src": {"node": 1, "channel": 0}, "dst": {"node": 16, "channel": 0}},
            {"src": {"node": 16, "channel": 0}, "dst": {"node": 17, "channel": 0}},
            {"src": {"node": 17, "channel": 0}, "dst": {"node": 2, "channel": 0}},
            {"src": {"node": 17, "channel": 1}, "dst": {"node": 2, "channel": 1}}]
        safe = self.tmp / "crossover.json"
        safe.write_text(json.dumps(crossover))
        self.install(home, "--role", "left", "--factory", safe)
        bypassed = json.loads(json.dumps(crossover))
        bypassed["nodes"][1]["bypassed"] = True
        unsafe = self.tmp / "bypassed.json"
        unsafe.write_text(json.dumps(bypassed))
        refused = self.install(home, "--factory", unsafe, check=False)
        self.assertNotEqual(refused.returncode, 0, refused.stdout)
        self.install(home, "--factory", self.factory, "--allow-tweeter-without-crossover")
        self.assertEqual(json.loads((home / ".local/state/speakerd/factory.json").read_text()), DOCUMENT)

    def test_import_filtergraph(self):
        session = self.tmp / "session.filtergraph"
        session.write_text("<FILTERGRAPH/>\n")
        converted = self.tmp / "converted.json"
        converted.write_text(json.dumps(DOCUMENT))
        home = self.fresh_home("import")
        (self.tmp / "stub/calls").unlink(missing_ok=True)
        env = dict(self.env, STUB_IMPORT=str(converted))
        result = firmware("speakerd-setup.sh", "install", "--bundle", self.bundle, "--home", home, "--no-systemd",
                          "--amplifier", "amplifier", "--role", "left", "--import-filtergraph", session,
                          "--allow-tweeter-without-crossover", env=env)
        self.assertIn("Converting", result.stdout)
        self.assertEqual(json.loads((home / ".local/state/speakerd/factory.json").read_text()), DOCUMENT)
        calls = (self.tmp / "stub/calls").read_text()
        self.assertIn(f"--import-filtergraph {session} --prefer-headless --device "
                      f"{home / '.local/state/speakerd/device.json'}", calls)
        failed = firmware("speakerd-setup.sh", "install", "--bundle", self.bundle, "--home", home, "--no-systemd",
                          "--import-filtergraph", session, env=self.env, check=False)
        self.assertNotEqual(failed.returncode, 0, failed.stdout)
        self.assertIn("speakerd: import: FAILED", failed.stdout)

    def test_finding_the_amplifier(self):
        def sink(i, name, alsa=True, priority=None):
            props = {"node.name": name, "media.class": "Audio/Sink"}
            props["factory.name"] = "api.alsa.pcm.sink" if alsa else "support.null-audio-sink"
            if priority is not None:
                props["priority.driver"] = priority
            return [{"id": i, "type": "PipeWire:Interface:Node", "info": {"props": props}}] + [
                {"id": i * 10 + k, "type": "PipeWire:Interface:Port",
                 "info": {"direction": "input", "props": {"node.id": i, "port.name": f"playback_{c}"}}}
                for k, c in enumerate(("FL", "FR"))]

        def amp(dump, *args):
            path = self.tmp / "dump.json"
            path.write_text(json.dumps(dump))
            return subprocess.run(["python3", str(FIRMWARE / "speakerd/setup-helper.py"), "amp", str(path), *args],
                                  capture_output=True, text=True)

        one = amp(sink(1, "bpi-alsa-playback-pcm", priority=1000) + sink(2, "speakerd", alsa=False))
        self.assertEqual(one.returncode, 0, one.stderr)
        self.assertIn("node='bpi-alsa-playback-pcm'", one.stdout)
        self.assertIn("ports='playback_FL playback_FR'", one.stdout)
        self.assertIn("priority='1000'", one.stdout)
        for name in ("bpi-alsa-playback-pcm", "alsa-playback-pcm"):
            several = amp(sink(1, "hdmi-out") + sink(2, name))
            self.assertIn(f"node='{name}'", several.stdout, several.stderr)
        ambiguous = amp(sink(1, "hdmi-out") + sink(2, "usb-out"))
        self.assertNotEqual(ambiguous.returncode, 0)
        self.assertIn("hdmi-out, usb-out", ambiguous.stderr)
        none = amp(sink(1, "amplifier", alsa=False))
        self.assertNotEqual(none.returncode, 0)
        self.assertIn("found no ALSA Audio/Sink", none.stderr)

        with Pair() as pair:
            env = dict(self.env, XDG_RUNTIME_DIR=pair["left"].env["XDG_RUNTIME_DIR"])
            home = self.fresh_home("live")
            refused = firmware("speakerd-setup.sh", "install", "--bundle", self.bundle, "--home", home,
                               "--no-systemd", "--role", "left", env=env, check=False)
            self.assertNotEqual(refused.returncode, 0, refused.stdout)
            self.assertIn("found no ALSA Audio/Sink", refused.stdout)
            found = firmware("speakerd-setup.sh", "install", "--bundle", self.bundle, "--home", home,
                             "--no-systemd", "--role", "left", "--amplifier", "amplifier", env=env)
            self.assertIn("ports playback_FL playback_FR", found.stdout)
            self.assertIn("woofer = amplifier:playback_FL, tweeter = amplifier:playback_FR", found.stdout)

    def test_smoke_reports_what_is_not_capped(self):
        home = self.fresh_home("smoke")
        self.install(home, "--role", "left")
        start = "speakerd-run: starting speakerd x from layer current (test)"
        limits = "speakerd: device: limits: output at -20 dB, tweeter muted"
        not_capped = "FAIL speakerd does not play device.json's limits"
        cases = {
            "not read": ([start, "speakerd: device.json: FAILED: limits: unknown key 'limts'; using the defaults",
                          limits + " (device.json not read: the fail-safe)"],
                         ["FAIL device.json: FAILED", not_capped]),
            "no limits": ([start, limits + " (no limits in device.json: the fail-safe)"], [not_capped]),
            "another suffix": ([start, limits + " (something else)"], [not_capped]),
            "silent": ([start], ["FAIL speakerd did not say which limits it enforces"]),
        }
        for name, (lines, fails) in cases.items():
            log = self.tmp / f"smoke-{name.replace(' ', '-')}.log"
            log.write_text("\n".join(lines) + "\n")
            result = firmware("speakerd-setup.sh", "smoke", "--home", home, "--no-systemd", "--log", log,
                              "--window", "0", env=self.env, check=False)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertNotIn("pass capped", result.stdout, name)
            for fail in fails:
                self.assertIn(fail, result.stdout, name)
        # The exact line, with device.json's own limits, passes that check (no PipeWire here, so
        # smoke fails on the links all the same).
        log = self.tmp / "smoke-capped.log"
        log.write_text("\n".join([start, limits]) + "\n")
        result = firmware("speakerd-setup.sh", "smoke", "--home", home, "--no-systemd", "--log", log,
                          "--window", "0", env=self.env, check=False)
        self.assertIn("pass capped: output at -20 dB, tweeter muted", result.stdout)

    def test_routing_and_uninstall(self):
        home = self.fresh_home("routing")
        crt = home / ".config/pipewire/client-rt.conf"
        refused = firmware("snapcast-setup.sh", "--mode", "target", "--routing-only", "--route", "speakerd",
                           "--client-home", home, env=self.env, check=False)
        self.assertNotEqual(refused.returncode, 0, refused.stdout)
        self.assertIn("would fall back to the amplifier", refused.stdout)
        self.assertFalse(crt.exists())

        firmware("speakerd-setup.sh", "sink", "--home", home, "--no-systemd", env=self.env)
        firmware("snapcast-setup.sh", "--mode", "target", "--routing-only", "--route", "speakerd",
                 "--client-home", home, env=self.env)
        text = crt.read_text()
        self.assertEqual(text.count('target.object = "speakerd"'), 3, text)
        self.assertNotIn("Ap.cap", text.split("stream.rules")[1])

        self.install(home, "--role", "left")
        firmware("speakerd-setup.sh", "uninstall", "--home", home, "--no-systemd", env=self.env)
        text = crt.read_text()
        self.assertIn('target.object = "Ap.cap"', text)
        self.assertNotIn('target.object = "speakerd"', text)
        for gone in (".config/systemd/user/speakerd.service", ".config/pipewire/pipewire.conf.d/60-speakerd-sink.conf",
                     ".local/lib/speakerd"):
            self.assertFalse((home / gone).exists(), gone)
        self.assertTrue((home / ".local/state/speakerd/device.json").exists())
        self.install(home, "--role", "left")
        firmware("speakerd-setup.sh", "uninstall", "--home", home, "--no-systemd", "--purge", env=self.env)
        self.assertFalse((home / ".local/state/speakerd").exists())

    def test_setup_checks_first_and_installs_in_boot_order(self):
        home = self.fresh_home("setup")
        unit = home / ".config/systemd/user/snapclient.service"
        unit.parent.mkdir(parents=True)
        unit.write_text("[Service]\nEnvironment=PIPEWIRE_CONFIG_NAME=client-rt.conf\nExecStart=/usr/bin/snapclient\n")
        args = ["--home", home, "--no-systemd", "--bundle", self.bundle, "--role", "left", "--amplifier", "amplifier"]
        before = tree(home)

        firmware("setup.sh", *args, "--dry-run", env=self.env)
        self.assertEqual(tree(home), before)
        refused = firmware("setup.sh", *args, "--skip", "pipewire", env=self.env, check=False)
        self.assertNotEqual(refused.returncode, 0, refused.stdout)
        self.assertIn("needs the speakerd sink", refused.stdout)
        self.assertEqual(tree(home), before)

        result = firmware("setup.sh", *args, env=self.env).stdout
        order = [result.index(f"==> {c}:") for c in ("pipewire", "snapcast", "speakerd", "ampd")]
        self.assertEqual(order, sorted(order), result)
        self.assertTrue((home / ".config/pipewire/pipewire.conf.d/60-speakerd-sink.conf").exists())
        self.assertIn('target.object = "speakerd"', (home / ".config/pipewire/client-rt.conf").read_text())
        self.assertTrue((home / ".local/lib/speakerd/current").is_symlink())


if __name__ == "__main__":
    unittest.main()
