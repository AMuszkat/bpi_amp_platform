"""firmware_speakerd_install (docs/autonomous-development.md, section 5): the installer's local
mode lays speakerd out as firmware/CLAUDE.md documents it, always capped, and refuses what is
unsafe.

firmware/speakerd-setup.sh runs here as it runs on a board, with two test hooks: --home (a
fresh folder stands for the audio user's home) and --no-systemd (the container has no systemd:
the systemctl calls are only printed). The bundle is made by the installer's own bundle step,
from the x86_64 builds. Nothing here reaches a board.

Pass when:

- the bundle holds bin/speakerd, lv2/loudness_headless.lv2 and a VERSION whose id is the
  commit, "-dirty" if the tree is, and the hash of its contents;
- a first install without --role is refused, and installs nothing;
- the first install lays out, in the home: .local/lib/speakerd/<id>/ with bin/speakerd,
  bin/speakerd-run, lv2/loudness_headless.lv2, VERSION and setup/ (the installer); current ->
  <id>; an empty home/ (speakerd's HOME); .local/state/speakerd/device.json exactly
  {version 1, role left, amplifier {node, woofer playback_FL, tweeter playback_FR}, limits
  {gain_db -20.0, tweeter muted}}; factory.json, the document given; the unit and the
  PipeWire drop-in, byte for byte the tracked firmware/ files; and graph.json and
  graph.lkg.json, there before, unchanged byte for byte; and it says every library speakerd and
  its plugins need is there;
- installing the same bundle again adds no version and changes nothing, but a device.json whose
  limits a person lifted (0 dB, tweeter on) is capped again, keeping its role, amplifier and
  an extra key; --role right changes the role alone;
- a second, different bundle becomes current, the first previous; rollback swaps them, and
  rollback again swaps them back; with --keep 2 a third version removes the oldest, never
  current or previous;
- while a process called AudioPluginHost runs, install refuses, says so, and installs
  nothing; with --stop-plugin-host it stops that process and installs; a file that would
  start one again is named;
- a factory graph that sends the stream to the tweeter without a crossover is refused, and is
  accepted with --allow-tweeter-without-crossover;
- --import-filtergraph writes speakerd's conversion as factory.json (a stand-in speakerd here:
  2cb3278's has no import), and a conversion that fails (exit 2) is refused;
- the amplifier is found in a pw-dump: the one ALSA sink; with several, bpi-alsa-playback-pcm
  or alsa-playback-pcm; none or an ambiguous set is refused, naming the sinks; on a virtual
  speaker (whose amplifier is a null sink) install refuses without --amplifier, and with
  --amplifier amplifier reads its ports playback_FL and playback_FR;
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
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from harness import FIRMWARE, Pair, built, firmware, graph_document, lv2_uri, stub_speakerd

DOCUMENT = graph_document(lv2_uri("loudness_headless"), {"mode": "Force 1", "s0_e0_gain": 12.0})
CAPPED = {"gain_db": -20.0, "tweeter": "muted"}


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


def version_id(bundle):
    return dict(line.split("=", 1) for line in (bundle / "VERSION").read_text().splitlines())["id"]


def stub_build_root(root):
    """A build tree with the stand-in speakerd and the real loudness_headless build, for the
    bundle step (BUILD_ROOT)."""
    (root / "speakerd/speakerd_artefacts/Release").mkdir(parents=True, exist_ok=True)
    shutil.copy(stub_speakerd(), root / "speakerd/speakerd_artefacts/Release/speakerd")
    (root / "loudness").mkdir(exist_ok=True)
    if not (root / "loudness/loudness_headless_artefacts").exists():
        os.symlink(built("loudness", "headless").parents[2], root / "loudness/loudness_headless_artefacts")
    return root


class Install(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        built("speakerd")
        built("loudness", "headless")
        built("loudness", "complete")
        cls.tmp = Path(tempfile.mkdtemp(prefix="vpair-install-"))
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)
        # No PipeWire here unless a test brings one up: pw-dump fails at once.
        cls.env = {"XDG_RUNTIME_DIR": str(cls.tmp / "no-pipewire")}
        (cls.tmp / "no-pipewire").mkdir()
        cls.bundle = cls.make_bundle("one", ["loudness:headless"])
        cls.bundle2 = cls.make_bundle("two", ["loudness:headless", "loudness:complete"])
        cls.factory = cls.tmp / "factory.json"
        cls.factory.write_text(json.dumps(DOCUMENT, indent=2))

    @classmethod
    def make_bundle(cls, name, plugins, build_root=None):
        out = cls.tmp / f"bundle-{name}"
        args = ["bundle", "--arch", "native", "--out", out]
        for plugin in plugins:
            args += ["--plugin", plugin]
        firmware("speakerd-setup.sh", *args, env={"BUILD_ROOT": str(build_root)} if build_root else None)
        return out

    def install(self, home, *args, bundle=None, check=True):
        return firmware("speakerd-setup.sh", "install", "--bundle", bundle or self.bundle, "--home", home,
                        "--no-systemd", "--amplifier", "amplifier", *args, env=self.env, check=check)

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
        version = dict(line.split("=", 1) for line in (self.bundle / "VERSION").read_text().splitlines())
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=FIRMWARE, capture_output=True,
                                text=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=FIRMWARE, capture_output=True,
                                    text=True).stdout.strip())
        print(f"\n  bundle {version['id']}")
        self.assertEqual(version["commit"], commit)
        self.assertEqual(version["dirty"], "yes" if dirty else "no")
        self.assertRegex(version["id"], rf"^{commit[:12]}{'-dirty' if dirty else ''}-[0-9a-f]{{8}}$")
        self.assertNotEqual(version_id(self.bundle), version_id(self.bundle2))

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
        vid = version_id(self.bundle)
        files = tree(home)
        for want in (f".local/lib/speakerd/{vid}/bin/speakerd", f".local/lib/speakerd/{vid}/bin/speakerd-run",
                     f".local/lib/speakerd/{vid}/lv2/loudness_headless.lv2/", f".local/lib/speakerd/{vid}/VERSION",
                     f".local/lib/speakerd/{vid}/setup/speakerd-setup.sh",
                     f".local/lib/speakerd/current -> {vid}", ".local/lib/speakerd/home/",
                     ".local/state/speakerd/device.json", ".local/state/speakerd/factory.json",
                     ".config/systemd/user/speakerd.service",
                     ".config/pipewire/pipewire.conf.d/60-speakerd-sink.conf"):
            self.assertIn(want, files, "\n".join(sorted(files)))
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
        device["trim"] = 1.5
        device["amplifier"]["tweeter"] = "playback_FL"
        device["amplifier"]["woofer"] = "playback_FR"
        device_json.write_text(json.dumps(device))
        before = tree(home / ".local/lib/speakerd")

        firmware("speakerd-setup.sh", "install", "--bundle", self.bundle, "--home", home, "--no-systemd",
                 env=self.env)
        self.assertEqual(tree(home / ".local/lib/speakerd"), before)
        again = json.loads(device_json.read_text())
        self.assertEqual(again["limits"], CAPPED)
        self.assertEqual((again["role"], again["trim"], again["amplifier"]),
                         ("left", 1.5, {"node": "amplifier", "woofer": "playback_FR", "tweeter": "playback_FL"}))

        firmware("speakerd-setup.sh", "install", "--bundle", self.bundle, "--home", home, "--no-systemd",
                 "--role", "right", env=self.env)
        right = json.loads(device_json.read_text())
        self.assertEqual(right, dict(again, role="right"))

    def test_versions_rollback_and_pruning(self):
        home = self.fresh_home("versions")
        lib = home / ".local/lib/speakerd"
        one, two = version_id(self.bundle), version_id(self.bundle2)

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
        self.assertRegex(status, rf"current +{two}")
        self.assertRegex(status, rf"previous +{one}")

        # A third version with --keep 2: the oldest goes, current and previous stay.
        three_bundle = self.make_bundle("three", ["loudness:headless"], build_root=stub_build_root(self.tmp / "b3"))
        three = version_id(three_bundle)
        self.install(home, "--keep", "2", bundle=three_bundle)
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
        self.install(home, "--factory", self.factory, "--allow-tweeter-without-crossover")
        self.assertEqual(json.loads((home / ".local/state/speakerd/factory.json").read_text()), DOCUMENT)

    def test_import_filtergraph(self):
        bundle = self.make_bundle("import", ["loudness:headless"], build_root=stub_build_root(self.tmp / "bi"))
        session = self.tmp / "session.filtergraph"
        session.write_text("<FILTERGRAPH/>\n")
        converted = self.tmp / "converted.json"
        converted.write_text(json.dumps(DOCUMENT))
        home = self.fresh_home("import")
        env = dict(self.env, STUB_IMPORT=str(converted))
        result = firmware("speakerd-setup.sh", "install", "--bundle", bundle, "--home", home, "--no-systemd",
                          "--amplifier", "amplifier", "--role", "left", "--import-filtergraph", session,
                          "--allow-tweeter-without-crossover", env=env)
        self.assertIn("Converting", result.stdout)
        self.assertEqual(json.loads((home / ".local/state/speakerd/factory.json").read_text()), DOCUMENT)
        failed = firmware("speakerd-setup.sh", "install", "--bundle", bundle, "--home", home, "--no-systemd",
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
            env = {"XDG_RUNTIME_DIR": pair["left"].env["XDG_RUNTIME_DIR"]}
            home = self.fresh_home("live")
            refused = firmware("speakerd-setup.sh", "install", "--bundle", self.bundle, "--home", home,
                               "--no-systemd", "--role", "left", env=env, check=False)
            self.assertNotEqual(refused.returncode, 0, refused.stdout)
            self.assertIn("found no ALSA Audio/Sink", refused.stdout)
            found = firmware("speakerd-setup.sh", "install", "--bundle", self.bundle, "--home", home,
                             "--no-systemd", "--role", "left", "--amplifier", "amplifier", env=env)
            self.assertIn("ports playback_FL playback_FR", found.stdout)
            self.assertIn("woofer = amplifier:playback_FL, tweeter = amplifier:playback_FR", found.stdout)

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
