"""firmware_speakerd_install (docs/autonomous-development.md, section 5): the installers lay
speakerd out as firmware/CLAUDE.md documents it, always capped, refuse what is unsafe before
they change anything, and their remote mode and the board script quote what they send.

firmware/speakerd-setup.sh runs here as it runs on a board, with two test hooks: --home (a
fresh folder stands for the audio user's home) and --no-systemd (the systemctl calls are only
printed), or, for the systemd path, stand-ins for systemctl and journalctl that record each call
and answer as a test tells them. The bundles are made by the installer's own bundle step; their
speakerd is the stand-in of stub_speakerd.c, which says the version STUB_VERSION gives it (the
installer's minimum, SPEAKERD_MIN_VERSION, unless a test says otherwise). The old plugin host is
a copy of sleep called harness.TEST_PLUGIN_HOST. The remote mode runs through a stand-in ssh
that runs the board's side on this machine, as an unprivileged user when run as root, in a
stand-in board home; the board script runs with --dry-run. Nothing here reaches a board.

Pass when:

- the bundle holds bin/speakerd, lv2/loudness_headless.lv2 and a VERSION whose id is the
  commit, "-dirty" if the tree is, and the hash of its contents;
- a speakerd that cannot say its version, one older than the minimum, and one that cannot be
  executed (as from a noexec /tmp: said so) are refused by install, which installs nothing, and
  rollback refuses an older one;
- a first install without --role is refused, and installs nothing, and a running plugin host
  is left running although --stop-plugin-host was given (refused before anything is stopped);
- the first install lays out, in the home: .local/lib/speakerd/<id>/ with bin/speakerd,
  bin/speakerd-run, lv2/loudness_headless.lv2, VERSION (with the speakerd version) and setup/
  (the installer); current -> <id>; an empty home/ (speakerd's HOME);
  .local/state/speakerd/device.json exactly {version 1, role left, amplifier {node, woofer
  playback_FL, tweeter playback_FR}, limits {gain_db -20.0, tweeter muted}}; factory.json, the
  document given; the unit and the PipeWire drop-in, byte for byte the tracked firmware/ files;
  and graph.json and graph.lkg.json, there before, unchanged byte for byte; and it says every
  library speakerd and its plugins need is there; VERSION records speakerd's whole --version
  line (speakerd_build), the installed scripts are executable, and the installed
  speakerd-setup.sh, called directly, says the status;
- installing the same bundle again adds no version and changes nothing, but a device.json whose
  limits a person lifted (0 dB, tweeter on) is capped again, keeping its role and amplifier;
  --role right changes the role alone; one of --amplifier, --woofer-port, --tweeter-port
  changes that alone and keeps device.json's others; a device.json with a key speakerd does not
  know, or a key twice, is refused (speakerd would reject it and play its fail-safe defaults);
- a second, different bundle becomes current, the first previous; rollback swaps them, and
  rollback again swaps them back, and a device.json whose limits a person lifted is capped
  again by the rollback; with --keep 2 a third version removes the oldest, never current or
  previous;
- while the plugin host runs, install refuses, says so, and installs nothing; with
  --stop-plugin-host it still refuses, stopping nothing, while a desktop autostart or a profile
  line would start it again, naming each and what to do; once they are gone it stops it,
  disables the user unit that starts it, and uninstall re-enables that unit;
- a factory graph that sends the stream to the tweeter other than through a crossover's high
  band (channel 1) is refused: straight, through a bypassed crossover, and from a crossover's low
  band (channel 0); it is accepted with --allow-tweeter-without-crossover (with --home), and a
  later install that keeps it refuses it; `limits` turning the tweeter on refuses, leaving
  device.json as it was, while factory.json or ampd's graph.json breaks that rule (naming the
  layer), and turns it on once none does; status marks such a layer with ***; the allow flag
  without --home, and in the remote mode, is refused before anything is changed or contacted;
- a plugin bundle with one of the bundle's URIs in a system LV2 folder is named loudly;
- --import-filtergraph writes speakerd's conversion as factory.json, having given speakerd the
  device.json to be (--device), and a conversion that fails (exit 2) is refused;
- the amplifier is found in a pw-dump: the one ALSA sink; with several, bpi-alsa-playback-pcm
  or alsa-playback-pcm; none or an ambiguous set is refused, naming the sinks; on a virtual
  speaker (whose amplifier is a null sink) install refuses without --amplifier, and with
  --amplifier amplifier reads its ports playback_FL and playback_FR;
- smoke passes the exact line `speakerd: device: limits: output at -20 dB, tweeter muted` when
  device.json has those limits, and reports, each as a failure: a `device.json: FAILED` line,
  the limits line with either fail-safe suffix (or any other), speakerd's `NO INPUT SINK
  CONFIGURED` line, and no limits line at all;
- snapcast-setup.sh --route speakerd refuses while no PipeWire config defines the speakerd
  sink, and after `speakerd-setup.sh sink` routes snapclient (and shairport-sync and an
  AudioPluginHost) into "speakerd", keeping the routing there was; uninstall puts that routing
  back exactly (or, with none kept, the AudioPluginHost route), removes the unit, the drop-in
  and the versions and keeps the state, and --purge removes that too; --route speakerd is
  refused, client-rt.conf not written, while the snapclient unit lacks
  PIPEWIRE_CONFIG_NAME=client-rt.conf, and with --no-client-rt (into Ap.cap it only warns), and
  no temporary file is left beside client-rt.conf; with a running PipeWire, uninstall stops and
  disables snapclient when the restored routing's target (Ap.cap) is not there, and says the
  target exists when it is;
- setup.sh checks before it changes: --dry-run changes nothing; --route speakerd without the
  pipewire component and without the sink, and --route plugin-host with the speakerd component,
  are refused before anything changes; and the components pipewire, snapcast (re-routing an
  installed snapclient) and speakerd install in that order;
- under (stand-in) systemd, install fails unless speakerd, after its start, runs and says
  exactly the limits device.json gives: no line within the wait, an inactive service, a
  fail-safe suffix, a FAILED line and NO INPUT SINK CONFIGURED each fail it; `limits` and
  `rollback` restarting a speakerd that runs pass, although the old instance's `speakerd ended`
  line precedes the new start, and fail on a `speakerd ended` after it; setup.sh stops on a
  PipeWire restart that fails, before anything is routed into speakerd; and, the whole
  architecture and --only pipewire alike, it refuses while the plugin host runs, restarting
  nothing, and with --stop-plugin-host stops it before it restarts PipeWire;
- the remote mode (target-remote) copies one bundle to the board's ~/.cache and runs the board's
  side there, also for check (with the artifacts) and for --dry-run (the board-side dry run,
  which installs nothing), every argument arriving as given (a path with a space, a value with a
  semicolon that runs nothing), and the copy is removed afterwards; ssh as root is refused;
  --allow-tweeter-without-crossover is refused before the stand-in ssh is called; and, for a
  host that is not a test's (.invalid), the remote mode applies the deploy gate before it calls
  ssh: refused outside a git checkout, without a readable test status for HEAD, with a red one or
  one from a dirty tree, on a dirty tree, and while a lab lock exists; with all of it green it
  reaches the (stand-in) board;
- the board script (with --dry-run) refuses `journal -n` that is not a number and a deploy
  option that is not an agent's (--base-conf, --stop-plugin-host, --server-ip, --no-restart),
  quotes journal's argument, and reads status from the board's installed script with a fixed
  command.

    python3 -m unittest -v tests.vpair.test_firmware_speakerd_install
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from harness import (FIRMWARE, ROOT, TEST_ENV, TEST_PLUGIN_HOST, Pair, built, firmware, graph_document, lv2_uri,
                     stub_plugin_host, stub_speakerd)

DOCUMENT = graph_document(lv2_uri("loudness_headless"), {"mode": "Force 1", "s0_e0_gain": 12.0})
CAPPED = {"gain_db": -20.0, "tweeter": "muted"}
MIN_VERSION = re.search(r"(?m)^SPEAKERD_MIN_VERSION=(\S+)$", (FIRMWARE / "speakerd-setup.sh").read_text()).group(1)
LIMITS = "speakerd: device: limits: output at -20 dB, tweeter muted"

# Stand-ins for systemctl, journalctl and loginctl (by the name they are called), for the
# installers' systemd path: each call is recorded in $FAKE_SYSTEMD/calls; is-active answers
# $FAKE_SYSTEMD/active (default active); journalctl prints $FAKE_SYSTEMD/journal; a call whose
# arguments contain a line of $FAKE_SYSTEMD/fail fails. At a restart of pipewire.service it
# notes whether a process called $FAKE_PLUGIN_HOST runs.
FAKE_SYSTEMD = r'''#!/bin/bash
name="$(basename "$0")"; d="${FAKE_SYSTEMD:?}"
echo "$name $*" >> "$d/calls"
case "$name" in
    journalctl) cat "$d/before" "$d/journal" 2> /dev/null; exit 0 ;;
    loginctl) echo yes; exit 0 ;;
esac
if [ -f "$d/fail" ]; then
    while read -r bad; do [ -n "$bad" ] && [[ "$*" == *"$bad"* ]] && exit 1; done < "$d/fail"
fi
case " $* " in
    *" restart "*speakerd.service*)
        # A speakerd that runs ($d/running) ends first: its ExecStopPost line comes before the
        # new start, in the same journal window.
        [ ! -f "$d/running" ] || echo "speakerd-run: speakerd ended: stopped (killed TERM); the next start uses current: a clean stop" > "$d/before" ;;
    *" restart "*pipewire.service*)
        alive=no
        for f in /proc/[0-9]*/stat; do
            { s="$(< "$f")"; } 2> /dev/null || continue
            [[ "$s" == *"(${FAKE_PLUGIN_HOST:-none})"* ]] || continue
            s="${s##*) }"; [ "${s%% *}" = Z ] || alive=yes
        done
        echo "pipewire restarted; the plugin host runs: $alive" >> "$d/calls" ;;
    *" is-active "*speakerd*)
        state="$(cat "$d/active" 2> /dev/null || echo active)"
        [[ " $* " == *" --quiet "* ]] || echo "$state"
        [ "$state" = active ]; exit ;;
    *" is-enabled "*) exit 1 ;;
    *" show "*) echo 0 ;;
esac
exit 0
'''

# A stand-in for ssh: the board's side runs on this machine, in $FAKE_BOARD/home, as nobody when
# run as root (the board's audio user is never root), and nothing is contacted. It records the
# host in $FAKE_BOARD/hosts.
FAKE_SSH = r'''#!/bin/bash
while [ $# -gt 0 ]; do case "$1" in -t | -T) shift ;; --) shift; break ;; -*) shift ;; *) break ;; esac; done
host="$1"; shift
echo "$host" >> "${FAKE_BOARD:?}/hosts"
export HOME="$FAKE_BOARD/home"
cd "$HOME" || exit 255
if [ "$(id -u)" = 0 ]; then exec setpriv --reuid=65534 --regid=65534 --clear-groups bash -c "$*"; fi
exec bash -c "$*"
'''


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


def firmware_at(repo, script, *args, env):
    """firmware/<script> of another checkout (a scratch copy), as harness.firmware runs ours."""
    return subprocess.run(["bash", str(Path(repo) / "firmware" / script), *map(str, args)],
                          env={**os.environ, **TEST_ENV, **env}, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, timeout=120)


def write_script(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


class Setup(unittest.TestCase):
    """What every class of this module shares: the stand-in builds and bundles."""

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

    def plugin_host(self):
        host = stub_plugin_host(self.tmp / "bin")
        self.addCleanup(lambda: (host.kill(), host.wait()))
        return host


class Install(Setup):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bundle2 = cls.make_bundle("two", ["loudness:headless", "loudness:complete"])
        cls.bundle3 = cls.make_bundle("three", ["loudness:complete"])

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

        # One that cannot be executed, as from a noexec /tmp.
        noexec = self.tmp / "bundle-noexec"
        shutil.rmtree(noexec, ignore_errors=True)
        shutil.copytree(self.bundle, noexec, symlinks=True)
        (noexec / "bin/speakerd").chmod(0o644)
        result = self.install(home, "--role", "left", bundle=noexec, check=False)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("noexec", result.stdout)
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
        host = self.plugin_host()
        result = self.install(home, "--stop-plugin-host", check=False)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("--role left|right|mono is required", result.stdout)
        self.assertFalse((home / ".local/lib/speakerd").exists(), result.stdout)
        self.assertIsNone(host.poll(), f"the plugin host was stopped by an install that refused:\n{result.stdout}")

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
        installed = version_file(home / f".local/lib/speakerd/{vid}/VERSION")
        self.assertEqual(installed["speakerd_version"], MIN_VERSION)
        self.assertEqual(installed["speakerd_build"], f"speakerd {MIN_VERSION}")
        # firmware/CLAUDE.md calls the installed scripts directly: they are executable, and run.
        setup = home / f".local/lib/speakerd/{vid}/setup"
        for script in ("setup.sh", "speakerd-setup.sh", "snapcast-setup.sh", "speakerd/speakerd-run",
                       "speakerd/setup-helper.py"):
            self.assertTrue(os.access(setup / script, os.X_OK), script)
        direct = subprocess.run([str(home / ".local/lib/speakerd/current/setup/speakerd-setup.sh"), "status", "--home",
                                 str(home), "--no-systemd"], env={**os.environ, **TEST_ENV, **self.env},
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(direct.returncode, 0, direct.stdout + direct.stderr)
        self.assertRegex(direct.stdout, rf"current +{vid} +\(speakerd {re.escape(MIN_VERSION)},")
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

        # One port, or the node, alone: the rest stays device.json's.
        for option, value, key in (("--tweeter-port", "playback_RL", "tweeter"), ("--woofer-port", "playback_RR", "woofer"),
                                   ("--amplifier", "another-amplifier", "node")):
            expected = json.loads(device_json.read_text())
            expected["amplifier"][key] = value
            firmware("speakerd-setup.sh", "install", "--bundle", self.bundle, "--home", home, "--no-systemd",
                     option, value, env=self.env)
            self.assertEqual(json.loads(device_json.read_text()), expected, option)

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
        # Limits a person lifted: the version rolled back to plays capped, as after an install.
        device_json = home / ".local/state/speakerd/device.json"
        device_json.write_text(json.dumps(dict(json.loads(device_json.read_text()), limits={"gain_db": -6.0, "tweeter": "on"})))
        firmware("speakerd-setup.sh", "rollback", "--home", home, "--no-systemd", env=self.env)
        self.assertEqual((link("current"), link("previous")), (one, two))
        self.assertEqual(json.loads(device_json.read_text())["limits"], CAPPED)
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

    def test_the_old_plugin_host(self):
        home = self.fresh_home("plugin-host")
        host = self.plugin_host()
        autostart = home / ".config/autostart/plugin-host.desktop"
        autostart.parent.mkdir(parents=True)
        autostart.write_text(f"[Desktop Entry]\nExec=/opt/{TEST_PLUGIN_HOST} session.filtergraph\n")
        unit = home / ".config/systemd/user/plugin-host.service"
        unit.parent.mkdir(parents=True)
        unit.write_text(f"[Service]\nExecStart=/opt/{TEST_PLUGIN_HOST}\n")

        profile = home / ".profile"
        profile.write_text(f"/opt/{TEST_PLUGIN_HOST} &\n")

        result = self.install(home, "--role", "left", check=False)
        print(f"\n  refused with status {result.returncode}")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(f"{TEST_PLUGIN_HOST} runs (pid {host.pid})", result.stdout)
        self.assertIn("Refusing", result.stdout)
        self.assertFalse((home / ".local/lib/speakerd").exists())
        self.assertIsNone(host.poll(), "the plugin host was stopped without --stop-plugin-host")

        # With --stop-plugin-host, refused while anything a person must remove would start it
        # again at boot or login, naming each: nothing stopped, nothing installed.
        refused = self.install(home, "--role", "left", "--stop-plugin-host", check=False)
        self.assertNotEqual(refused.returncode, 0, refused.stdout)
        self.assertIn(f"{autostart}: a desktop autostart; delete the file", refused.stdout)
        self.assertIn(f"{profile}: remove the line that starts {TEST_PLUGIN_HOST}", refused.stdout)
        self.assertIn("Refusing to install until these are gone", refused.stdout)
        self.assertIn(f"{unit}: will be disabled (--stop-plugin-host)", refused.stdout)
        self.assertIsNone(host.poll(), f"stopped by an install that refused:\n{refused.stdout}")
        self.assertFalse((home / ".local/lib/speakerd").exists())
        autostart.unlink()
        profile.write_text("# nothing starts it here\n")

        # Refused for another reason (no such port) with --stop-plugin-host: nothing stopped.
        refused = self.install(home, "--role", "left", "--stop-plugin-host", "--woofer-port", "playback_FR",
                               "--tweeter-port", "playback_FR", check=False)
        self.assertNotEqual(refused.returncode, 0, refused.stdout)
        self.assertIsNone(host.poll(), f"stopped by an install that refused:\n{refused.stdout}")

        result = self.install(home, "--role", "left", "--stop-plugin-host")
        self.assertIsNotNone(host.poll(), f"the plugin host still runs:\n{result.stdout}")
        self.assertIn(f"Stopping {TEST_PLUGIN_HOST}", result.stdout)
        self.assertIn("systemctl --user disable --now plugin-host.service", result.stdout)
        self.assertTrue((home / ".local/lib/speakerd/current").is_symlink())
        uninstalled = firmware("speakerd-setup.sh", "uninstall", "--home", home, "--no-systemd", env=self.env).stdout
        self.assertIn("systemctl --user enable --now plugin-host.service", uninstalled)
        self.assertFalse((home / ".local/state/speakerd/plugin-host-units").exists())

    def test_refuses_a_tweeter_without_a_crossovers_high_band(self):
        home = self.fresh_home("tweeter")
        result = self.install(home, "--role", "left", "--factory", self.factory, check=False)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("reaches the tweeter other than through a crossover's high band", result.stdout)
        self.assertFalse((home / ".local/state/speakerd/factory.json").exists())

        def crossover(high_to_tweeter=1, bypassed=False):
            document = json.loads(json.dumps(DOCUMENT))
            document["nodes"].append({"uid": 17, "plugin": {"format": "LV2", "identifier": lv2_uri("crossover")},
                                      **({"bypassed": True} if bypassed else {})})
            document["connections"] = [
                {"src": {"node": 1, "channel": 0}, "dst": {"node": 16, "channel": 0}},
                {"src": {"node": 16, "channel": 0}, "dst": {"node": 17, "channel": 0}},
                {"src": {"node": 17, "channel": 1 - high_to_tweeter}, "dst": {"node": 2, "channel": 0}},
                {"src": {"node": 17, "channel": high_to_tweeter}, "dst": {"node": 2, "channel": 1}}]
            path = self.tmp / f"crossover-{high_to_tweeter}-{bypassed}.json"
            path.write_text(json.dumps(document))
            return path

        self.install(home, "--role", "left", "--factory", crossover())
        for unsafe, what in ((crossover(bypassed=True), "a bypassed crossover"), (crossover(0), "the low band")):
            refused = self.install(home, "--factory", unsafe, check=False)
            self.assertNotEqual(refused.returncode, 0, f"{what} reached the tweeter:\n{refused.stdout}")
        self.install(home, "--factory", self.factory, "--allow-tweeter-without-crossover")
        self.assertEqual(json.loads((home / ".local/state/speakerd/factory.json").read_text()), DOCUMENT)

        # The factory.json there is, kept by a later install, is checked as a new one is.
        kept = self.install(home, check=False)
        self.assertNotEqual(kept.returncode, 0, kept.stdout)
        self.assertIn("the factory.json there is (kept) is refused", kept.stdout)

    def test_the_tweeter_goes_on_only_over_a_crossovers_high_band(self):
        """limits turns the tweeter on only if no layer speakerd may load (graph.json and
        graph.lkg.json, ampd's, before factory.json) feeds it other than through a crossover's
        high band; status names a layer that does."""
        home = self.fresh_home("tweeter-on")
        state = home / ".local/state/speakerd"
        self.install(home, "--role", "left", "--factory", self.factory, "--allow-tweeter-without-crossover")
        device_json = state / "device.json"
        before = device_json.read_text()

        def tweeter_on(*extra):
            return firmware("speakerd-setup.sh", "limits", "--gain-db", "-20", "--tweeter-limit", "on",
                            "--person-present", "--home", home, "--no-systemd", *extra, env=self.env, check=False)

        refused = tweeter_on()
        self.assertNotEqual(refused.returncode, 0, refused.stdout)
        self.assertIn("a layer speakerd may load feeds the tweeter", refused.stdout)
        self.assertIn("factory.json: ", refused.stdout)
        self.assertEqual(device_json.read_text(), before)
        status = firmware("speakerd-setup.sh", "status", "--home", home, "--no-systemd", env=self.env).stdout
        self.assertRegex(status, r"(?m)^  factory\.json: \*\*\* .*tweeter")

        # A safe factory, but ampd's graph.json is not: still refused, naming graph.json.
        safe = json.loads(json.dumps(DOCUMENT))
        safe["connections"] = [c for c in safe["connections"] if c["dst"] != {"node": 2, "channel": 1}]
        (self.tmp / "woofer-only.json").write_text(json.dumps(safe))
        self.install(home, "--factory", self.tmp / "woofer-only.json")
        (state / "graph.json").write_text(json.dumps(DOCUMENT))
        refused = tweeter_on()
        self.assertNotEqual(refused.returncode, 0, refused.stdout)
        self.assertIn("graph.json: ", refused.stdout)
        self.assertEqual(json.loads(device_json.read_text())["limits"], CAPPED)

        (state / "graph.json").unlink()
        lifted = tweeter_on()
        self.assertEqual(lifted.returncode, 0, lifted.stdout)
        self.assertEqual(json.loads(device_json.read_text())["limits"], {"gain_db": -20.0, "tweeter": "on"})
        firmware("speakerd-setup.sh", "limits", "--cap", "--home", home, "--no-systemd", env=self.env)
        self.assertEqual(json.loads(device_json.read_text())["limits"], CAPPED)

        # The allow flag is for these tests (--home), never for a board.
        refused = firmware("speakerd-setup.sh", "limits", "--cap", "--allow-tweeter-without-crossover", "--no-systemd",
                           env=dict(self.env, HOME=str(self.fresh_home("no-home-given"))), check=False)
        self.assertNotEqual(refused.returncode, 0, refused.stdout)
        self.assertIn("--allow-tweeter-without-crossover is for the virtual pair tests (--home) only", refused.stdout)
        self.assertFalse((self.tmp / "no-home-given").exists())

    def test_names_a_bundle_shadowed_by_a_system_lv2_folder(self):
        home = self.fresh_home("shadow")
        system = self.tmp / "system-lv2"
        shutil.rmtree(system, ignore_errors=True)
        shutil.copytree(built("loudness", "headless"), system / "old-loudness.lv2")
        result = self.install(home, "--role", "left", env=dict(self.env, SPEAKERD_SYSTEM_LV2_DIRS=str(system)))
        self.assertIn("A system LV2 folder has a plugin with the URI of one of the bundle's", result.stdout)
        self.assertIn(f"{lv2_uri('loudness_headless')}: {system / 'old-loudness.lv2'}", result.stdout)
        quiet = self.install(home, check=True)
        self.assertNotIn("A system LV2 folder", quiet.stdout)

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
        calls = [line for line in (self.tmp / "stub/calls").read_text().splitlines() if line.startswith("import: ")]
        self.assertEqual(len(calls), 1, calls)
        self.assertRegex(calls[0], rf"--import-filtergraph {re.escape(str(session))} --prefer-headless --device \S+/device\.json ")
        self.assertNotIn("--allow-unsafe-routing", calls[0])
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
            found = firmware("speakerd-setup.sh", "check", "--bundle", self.bundle, "--home", home,
                             "--no-systemd", "--role", "left", "--amplifier", "amplifier", env=env)
            self.assertIn("ports playback_FL playback_FR", found.stdout)
            self.assertIn("woofer = amplifier:playback_FL, tweeter = amplifier:playback_FR", found.stdout)

    def test_smoke_reports_what_is_not_capped(self):
        home = self.fresh_home("smoke")
        self.install(home, "--role", "left")
        start = "speakerd-run: starting speakerd x from layer current (test)"
        not_capped = "FAIL speakerd does not play device.json's limits"
        cases = {
            "not read": ([start, "speakerd: device.json: FAILED: limits: unknown key 'limts'; using the defaults",
                          LIMITS + " (device.json not read: the fail-safe)"],
                         ["FAIL device.json: FAILED", not_capped]),
            "no limits": ([start, LIMITS + " (no limits in device.json: the fail-safe)"], [not_capped]),
            "another suffix": ([start, LIMITS + " (something else)"], [not_capped]),
            "own sink": ([start, LIMITS, "speakerd: pipewire: created the input sink 'speakerd'",
                          "speakerd: *** NO INPUT SINK CONFIGURED: PipeWire had no node 'speakerd', so speakerd made it ***"],
                         ["FAIL *** NO INPUT SINK CONFIGURED"]),
            "silent": ([start], ["FAIL speakerd did not say which limits it enforces"]),
        }
        for name, (lines, fails) in cases.items():
            log = self.tmp / f"smoke-{name.replace(' ', '-')}.log"
            log.write_text("\n".join(lines) + "\n")
            result = firmware("speakerd-setup.sh", "smoke", "--home", home, "--no-systemd", "--log", log,
                              "--window", "0", env=self.env, check=False)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertRegex(result.stdout, r"smoke: \d+ failed, \d+ warning\(s\)")
            if name != "own sink":
                self.assertNotIn("pass capped", result.stdout, name)
            for fail in fails:
                self.assertIn(fail, result.stdout, name)
        # The exact line, with device.json's own limits, passes that check (no PipeWire here, so
        # smoke fails on the sink and the links all the same, and still prints its summary).
        log = self.tmp / "smoke-capped.log"
        log.write_text("\n".join([start, LIMITS]) + "\n")
        result = firmware("speakerd-setup.sh", "smoke", "--home", home, "--no-systemd", "--log", log,
                          "--window", "0", env=self.env, check=False)
        self.assertIn("pass capped: output at -20 dB, tweeter muted", result.stdout)
        self.assertIn("FAIL the speakerd sink is not the drop-in's", result.stdout)
        # A broken device.json: every check still reports, and the summary prints.
        (home / ".local/state/speakerd/device.json").write_text("{ broken")
        result = firmware("speakerd-setup.sh", "smoke", "--home", home, "--no-systemd", "--log", log,
                          "--window", "0", env=self.env, check=False)
        self.assertIn("FAIL device.json cannot be read", result.stdout)
        self.assertRegex(result.stdout, r"smoke: \d+ failed")

    def test_routing_and_uninstall(self):
        home = self.fresh_home("routing")
        crt = home / ".config/pipewire/client-rt.conf"
        refused = firmware("snapcast-setup.sh", "--mode", "target", "--routing-only", "--route", "speakerd",
                           "--client-home", home, env=self.env, check=False)
        self.assertNotEqual(refused.returncode, 0, refused.stdout)
        self.assertIn("would fall back to the amplifier", refused.stdout)
        self.assertFalse(crt.exists())

        original = '# a person\'s own routing\nstream.rules = [ { matches = [ { application.name = "Snapclient" } ] actions = { update-props = { target.object = "Ap.cap" } } } ]\n'
        crt.parent.mkdir(parents=True, exist_ok=True)
        crt.write_text(original)
        firmware("speakerd-setup.sh", "sink", "--home", home, "--no-systemd", env=self.env)
        firmware("snapcast-setup.sh", "--mode", "target", "--routing-only", "--route", "speakerd",
                 "--client-home", home, env=self.env)
        text = crt.read_text()
        self.assertEqual(text.count('target.object = "speakerd"'), 3, text)
        self.assertNotIn("Ap.cap", text.split("stream.rules")[1])
        self.assertEqual((home / ".config/pipewire/client-rt.conf.before-speakerd").read_text(), original)

        self.install(home, "--role", "left")
        firmware("speakerd-setup.sh", "uninstall", "--home", home, "--no-systemd", env=self.env)
        self.assertEqual(crt.read_text(), original, "uninstall did not restore the routing from before speakerd")
        self.assertFalse((home / ".config/pipewire/client-rt.conf.before-speakerd").exists())
        for gone in (".config/systemd/user/speakerd.service", ".config/pipewire/pipewire.conf.d/60-speakerd-sink.conf",
                     ".local/lib/speakerd"):
            self.assertFalse((home / gone).exists(), gone)
        self.assertTrue((home / ".local/state/speakerd/device.json").exists())

        # With no routing kept: the AudioPluginHost route is written.
        crt.unlink()
        firmware("speakerd-setup.sh", "sink", "--home", home, "--no-systemd", env=self.env)
        firmware("snapcast-setup.sh", "--mode", "target", "--routing-only", "--route", "speakerd",
                 "--client-home", home, env=self.env)
        self.install(home, "--role", "left")
        firmware("speakerd-setup.sh", "uninstall", "--home", home, "--no-systemd", "--purge", env=self.env)
        self.assertIn('target.object = "Ap.cap"', crt.read_text())
        self.assertNotIn('target.object = "speakerd"', crt.read_text())
        self.assertFalse((home / ".local/state/speakerd").exists())

    def test_routing_into_speakerd_needs_a_unit_that_reads_client_rt(self):
        home = self.fresh_home("routing-unit")
        crt = home / ".config/pipewire/client-rt.conf"
        unit = home / ".config/systemd/user/snapclient.service"
        unit.parent.mkdir(parents=True)
        unit.write_text("[Service]\nExecStart=/usr/bin/snapclient\n")
        firmware("speakerd-setup.sh", "sink", "--home", home, "--no-systemd", env=self.env)

        def route(*args):
            return firmware("snapcast-setup.sh", "--mode", "target", "--routing-only", "--client-home", home, *args,
                            env=self.env, check=False)

        refused = route("--route", "speakerd")
        self.assertNotEqual(refused.returncode, 0, refused.stdout)
        self.assertIn("does not point snapclient at client-rt.conf", refused.stdout)
        self.assertFalse(crt.exists(), "client-rt.conf was written all the same")
        refused = route("--route", "speakerd", "--no-client-rt")
        self.assertNotEqual(refused.returncode, 0, refused.stdout)
        self.assertIn("--route speakerd needs client-rt.conf", refused.stdout)
        # Into Ap.cap it only warns, as before speakerd.
        warned = route("--route", "plugin-host")
        self.assertEqual(warned.returncode, 0, warned.stdout)
        self.assertIn("does not point snapclient at client-rt.conf", warned.stdout)

        unit.write_text("[Service]\nEnvironment=PIPEWIRE_CONFIG_NAME=client-rt.conf\nExecStart=/usr/bin/snapclient\n")
        routed = route("--route", "speakerd")
        self.assertEqual(routed.returncode, 0, routed.stdout)
        self.assertIn('target.object = "speakerd"', crt.read_text())
        self.assertEqual([f.name for f in crt.parent.iterdir() if ".tmp." in f.name], [], "a temporary file was left")

    def test_uninstall_stops_snapclient_when_its_old_target_is_gone(self):
        """The routing uninstall restores must name a node the running PipeWire has: else
        snapclient would play into the default sink, the amplifier."""
        with Pair() as pair:
            env = dict(self.env, XDG_RUNTIME_DIR=pair["left"].env["XDG_RUNTIME_DIR"])
            for target, stopped in (("Ap.cap", True), ("amplifier", False)):
                with self.subTest(target):
                    home = self.fresh_home(f"uninstall-{target}")
                    self.install(home, "--role", "left")
                    crt = home / ".config/pipewire/client-rt.conf"
                    crt.write_text('stream.rules = [ { matches = [ { application.name = "Snapclient" } ] '
                                   'actions = { update-props = { target.object = "speakerd" } } } ]\n')
                    before = crt.read_text().replace('"speakerd"', f'"{target}"')
                    (home / ".config/pipewire/client-rt.conf.before-speakerd").write_text(before)
                    result = firmware("speakerd-setup.sh", "uninstall", "--home", home, "--no-systemd", env=env)
                    self.assertEqual(crt.read_text(), before)
                    if stopped:
                        self.assertIn(f"sends it to '{target}', which the running PipeWire does not have", result.stdout)
                        self.assertIn("systemctl --user stop snapclient.service", result.stdout)
                        self.assertIn("systemctl --user disable snapclient.service", result.stdout)
                    else:
                        self.assertIn(f"snapclient's target '{target}' exists", result.stdout)
                        self.assertNotIn("stop snapclient.service", result.stdout)

    def test_setup_checks_first_and_installs_in_boot_order(self):
        home = self.fresh_home("setup")
        unit = home / ".config/systemd/user/snapclient.service"
        unit.parent.mkdir(parents=True)
        unit.write_text("[Service]\nEnvironment=PIPEWIRE_CONFIG_NAME=client-rt.conf\nExecStart=/usr/bin/snapclient\n")
        args = ["--home", home, "--no-systemd", "--bundle", self.bundle, "--role", "left", "--amplifier", "amplifier"]
        before = tree(home)

        firmware("setup.sh", *args, "--dry-run", env=self.env)
        self.assertEqual(tree(home), before)
        for extra, said in ((["--skip", "pipewire"], "needs the speakerd sink"),
                            (["--route", "plugin-host"], "--route plugin-host with the speakerd component")):
            refused = firmware("setup.sh", *args, *extra, env=self.env, check=False)
            self.assertNotEqual(refused.returncode, 0, refused.stdout)
            self.assertIn(said, refused.stdout)
            self.assertEqual(tree(home), before)

        result = firmware("setup.sh", *args, env=self.env).stdout
        order = [result.index(f"==> {c}:") for c in ("pipewire", "snapcast", "speakerd", "ampd")]
        self.assertEqual(order, sorted(order), result)
        self.assertTrue((home / ".config/pipewire/pipewire.conf.d/60-speakerd-sink.conf").exists())
        self.assertIn('target.object = "speakerd"', (home / ".config/pipewire/client-rt.conf").read_text())
        self.assertTrue((home / ".local/lib/speakerd/current").is_symlink())


class Systemd(Setup):
    """The systemd path, with stand-ins for systemctl, journalctl and loginctl."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bundle2 = cls.make_bundle("two", ["loudness:headless", "loudness:complete"])

    def systemd(self, name, journal=(), active="active", fail=()):
        d = self.tmp / f"systemd-{name}"
        shutil.rmtree(d, ignore_errors=True)
        for tool in ("systemctl", "journalctl", "loginctl"):
            write_script(d / "bin" / tool, FAKE_SYSTEMD)
        (d / "journal").write_text("".join(line + "\n" for line in journal))
        (d / "active").write_text(active)
        (d / "fail").write_text("".join(line + "\n" for line in fail))
        env = dict(self.env, FAKE_SYSTEMD=str(d), PATH=f"{d / 'bin'}:{os.environ['PATH']}",
                   SPEAKERD_START_WAIT="1", FAKE_PLUGIN_HOST=TEST_PLUGIN_HOST)
        return d, env

    def test_install_checks_what_speakerd_says_after_its_start(self):
        start = "speakerd-run: starting speakerd x from layer current (test)"
        cases = {
            "the limits": ([start, LIMITS], "active", None),
            "no line": ([start], "active", "has said nothing about its limits"),
            "inactive": ([start, LIMITS], "inactive", "speakerd is not running after its start"),
            "fail-safe": ([start, LIMITS + " (no limits in device.json: the fail-safe)"], "active",
                          "does not play device.json's limits"),
            "FAILED": ([start, "speakerd: device.json: FAILED: limits: no; using the defaults", LIMITS], "active",
                       "could not read device.json"),
            "own sink": ([start, LIMITS, "speakerd: *** NO INPUT SINK CONFIGURED: PipeWire had no node 'speakerd' ***"],
                         "active", "the drop-in is not loaded"),
        }
        for name, (journal, active, said) in cases.items():
            with self.subTest(name):
                d, env = self.systemd(name.replace(" ", "-"), journal, active)
                home = self.fresh_home(f"home-{name.replace(' ', '-')}")
                result = firmware("speakerd-setup.sh", "install", "--bundle", self.bundle, "--home", home,
                                  "--role", "left", "--amplifier", "amplifier", env=env, check=False)
                calls = (d / "calls").read_text()
                self.assertIn("systemctl --user restart speakerd.service", calls)
                if said is None:
                    self.assertEqual(result.returncode, 0, result.stdout)
                    self.assertIn("device: limits: output at -20 dB, tweeter muted", result.stdout)
                else:
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertIn(said, result.stdout)

    def test_limits_and_rollback_restart_a_running_speakerd(self):
        """A restart of a speakerd that runs: the old instance's 'speakerd ended' line (its
        ExecStopPost) is in the window before the new start, and is not the new one's failure."""
        start = "speakerd-run: starting speakerd x from layer current (test)"
        d, env = self.systemd("running", [start, LIMITS])
        home = self.fresh_home("home-running")
        firmware("speakerd-setup.sh", "install", "--bundle", self.bundle, "--home", home, "--role", "left",
                 "--amplifier", "amplifier", env=env)
        firmware("speakerd-setup.sh", "install", "--bundle", self.bundle2, "--home", home, env=env)
        (d / "running").touch()
        device_json = home / ".local/state/speakerd/device.json"

        (d / "journal").write_text(f"{start}\nspeakerd: device: limits: output at -30 dB, tweeter muted\n")
        result = firmware("speakerd-setup.sh", "limits", "--gain-db", "-30", "--tweeter-limit", "muted", "--home", home,
                          env=env, check=False)
        print(f"\n  limits: {(d / 'before').read_text().strip()}")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("speakerd ended", (d / "before").read_text())

        # As a person's limits would leave it: rollback caps it again, as an install does.
        device = json.loads(device_json.read_text())
        device_json.write_text(json.dumps(dict(device, limits={"gain_db": -6.0, "tweeter": "on"})))
        (d / "before").unlink()
        (d / "journal").write_text(f"{start}\n{LIMITS}\n")
        result = firmware("speakerd-setup.sh", "rollback", "--home", home, env=env, check=False)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("speakerd ended", (d / "before").read_text())
        self.assertEqual(json.loads(device_json.read_text())["limits"], CAPPED)

        # A crash after the new start still fails both.
        (d / "journal").write_text(f"{start}\n{LIMITS}\nspeakerd-run: speakerd ended: crash (status 134, signal ABRT); the next start uses current\n")
        for action in (["limits", "--gain-db", "-25", "--tweeter-limit", "muted"], ["rollback"]):
            with self.subTest(action[0]):
                result = firmware("speakerd-setup.sh", *action, "--home", home, env=env, check=False)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("speakerd is not running after its start", result.stdout)

    def test_setup_stops_on_a_failed_pipewire_restart(self):
        d, env = self.systemd("restart-fails", fail=["restart pipewire.service"])
        home = self.fresh_home("home-restart-fails")
        unit = home / ".config/systemd/user/snapclient.service"
        unit.parent.mkdir(parents=True)
        unit.write_text("[Service]\nEnvironment=PIPEWIRE_CONFIG_NAME=client-rt.conf\n")
        result = firmware("setup.sh", "--home", home, "--bundle", self.bundle, "--role", "left",
                          "--amplifier", "amplifier", env=env, check=False)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("could not restart PipeWire", result.stdout)
        self.assertFalse((home / ".config/pipewire/client-rt.conf").exists(), "snapclient was routed all the same")
        self.assertFalse((home / ".local/lib/speakerd").exists())

    def test_setup_stops_the_plugin_host_before_pipewire_restarts(self):
        # Every component that restarts PipeWire into the speakerd route: the whole architecture,
        # and the pipewire component alone.
        for name, only in (("all", []), ("pipewire", ["--only", "pipewire"])):
            with self.subTest(name):
                d, env = self.systemd(f"plugin-host-first-{name}", journal=["speakerd-run: starting speakerd", LIMITS])
                home = self.fresh_home(f"home-plugin-host-first-{name}")
                unit = home / ".config/systemd/user/snapclient.service"
                unit.parent.mkdir(parents=True)
                unit.write_text("[Service]\nEnvironment=PIPEWIRE_CONFIG_NAME=client-rt.conf\n")
                host = self.plugin_host()
                args = ["--home", home, "--bundle", self.bundle, "--role", "left", "--amplifier", "amplifier", *only]
                refused = firmware("setup.sh", *args, env=env, check=False)
                self.assertNotEqual(refused.returncode, 0, refused.stdout)
                self.assertIn(f"{TEST_PLUGIN_HOST} runs (pid {host.pid})", refused.stdout)
                calls = (d / "calls").read_text() if (d / "calls").exists() else ""
                self.assertNotIn("pipewire restarted", calls)
                self.assertIsNone(host.poll())

                result = firmware("setup.sh", *args, "--stop-plugin-host", env=env, check=False)
                calls = (d / "calls").read_text()
                restarts = [line for line in calls.splitlines() if line.startswith("pipewire restarted")]
                print(f"\n  {name}: {restarts}")
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertIsNotNone(host.poll())
                self.assertTrue(restarts, calls)
                self.assertTrue(all(line.endswith("runs: no") for line in restarts), calls)


class Remote(Setup):
    """The remote mode through a stand-in ssh, and the board script with --dry-run."""

    def board(self, name):
        board = self.tmp / f"board-{name}"
        shutil.rmtree(board, ignore_errors=True)
        (board / "home").mkdir(parents=True)
        (board / "hosts").touch()
        write_script(board / "bin/ssh", FAKE_SSH)
        if os.geteuid() == 0:
            # The board's side runs as nobody: it must reach its home, and nothing else of ours.
            shutil.chown(board / "home", 65534, 65534)
            os.chmod(self.tmp, 0o711)
            os.chmod(board, 0o711)
        env = dict(self.env, FAKE_BOARD=str(board), PATH=f"{board / 'bin'}:{os.environ['PATH']}",
                   BUILD_ROOT=str(self.root))
        return board, env

    def remote(self, env, action, *args, host="user@bpi-amp.invalid"):
        return firmware("speakerd-setup.sh", action, "--mode", "target-remote", "--target-host", host,
                        "--arch", "native", "--plugin", "loudness:headless", *args, env=env, check=False)

    def test_check_and_dry_run_on_the_board(self):
        board, env = self.board("check")
        result = self.remote(env, "check", "--role", "left", "--amplifier", "amplifier")
        print("\n" + result.stdout[-600:])
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("is good for this machine", result.stdout)
        self.assertIn("Nothing in the way of an install", result.stdout)
        self.assertEqual((board / "hosts").read_text().split(), ["user@bpi-amp.invalid"] * 3)

        result = self.remote(env, "install", "--role", "left", "--amplifier", "amplifier", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Dry run: nothing changed", result.stdout)
        self.assertEqual(list((board / "home/.cache").iterdir()), [], "the bundle was left on the board")
        self.assertFalse((board / "home/.local").exists())

    def test_every_argument_arrives_as_given(self):
        board, env = self.board("quoting")
        # Where the board's user could write, so that an argument run as a command would show.
        marker = board / "home" / "injected"
        given = f"/no such folder/session; touch {marker}"
        result = self.remote(env, "install", "--role", "left", "--amplifier", "amplifier",
                             "--import-filtergraph", given, "--dry-run")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(f"no .filtergraph at {given}", result.stdout)
        self.assertFalse(marker.exists(), "an argument ran as a command on the board")
        self.assertEqual(list((board / "home/.cache").iterdir()), [])

    def test_the_remote_mode_has_the_deploy_gate(self):
        """The installers' remote mode refuses, as tools/board.sh does, before it calls ssh: while a
        lab lock exists, outside a git checkout, on a dirty tree, and without a green test status
        for HEAD from a clean tree. A copy of firmware/ in a scratch repository stands for the
        checkout; the host is a .test name (reserved: nothing resolves it), reached only through
        the stand-in ssh."""
        board, env = self.board("gate")
        repo = self.tmp / "gate-repo"
        shutil.rmtree(repo, ignore_errors=True)
        shutil.copytree(FIRMWARE, repo / "firmware", ignore=shutil.ignore_patterns("local.mk", "localBuild"))
        host = "user@bpi-amp.test"

        def status():
            return firmware_at(repo, "speakerd-setup.sh", "status", "--mode", "target-remote", "--target-host", host,
                               env=env)

        def refused(said):
            result = status()
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn(said, result.stdout)
            self.assertEqual((board / "hosts").read_text(), "", "ssh was called")

        refused("is not a git checkout")
        git = ["git", "-C", str(repo), "-c", "user.name=user", "-c", "user.email=user@bpi-amp.local"]
        (repo / ".gitignore").write_text("tests/.status/\n")
        subprocess.run(git + ["init", "-q"], check=True)
        subprocess.run(git + ["add", "-A"], check=True)
        subprocess.run(git + ["commit", "-q", "-m", "scratch"], check=True)
        refused("no readable test status")
        head = subprocess.run(git + ["rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        record = repo / "tests/.status" / f"{head}.json"
        record.parent.mkdir(parents=True)
        record.write_text(json.dumps({"unit": "pass", "unit_tree": "clean", "vpair": "fail", "vpair_tree": "clean"}))
        refused("vpair: fail")
        record.write_text(json.dumps({"unit": "pass", "unit_tree": "clean", "vpair": "pass", "vpair_tree": "dirty"}))
        refused("tree dirty")
        record.write_text(json.dumps({"unit": "pass", "unit_tree": "clean", "vpair": "pass", "vpair_tree": "clean"}))
        (repo / "firmware/edited").write_text("not committed\n")
        refused("uncommitted changes")
        (repo / "firmware/edited").unlink()
        (repo / ".claude").mkdir()
        (repo / ".claude/lab.lock").write_text("")
        refused("the lab is locked")
        (repo / ".claude/lab.lock").unlink()
        (repo / ".claude").rmdir()

        result = status()
        self.assertIn(host, (board / "hosts").read_text(), result.stdout)

    def test_not_as_root(self):
        board, env = self.board("root")
        result = self.remote(env, "status", host="root@bpi-amp.invalid")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("not root", result.stdout)
        self.assertEqual((board / "hosts").read_text(), "")

    def test_the_allow_flag_never_reaches_a_board(self):
        board, env = self.board("allow")
        for action in ("install", "limits"):
            result = self.remote(env, action, "--role", "left", "--allow-tweeter-without-crossover", "--home",
                                 board / "home")
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("--allow-tweeter-without-crossover is for the virtual pair tests (--home) only", result.stdout)
        self.assertEqual((board / "hosts").read_text(), "")

    def test_the_board_script(self):
        for lock in (ROOT / ".claude/lab.lock",
                     Path(subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=ROOT,
                                         capture_output=True, text=True).stdout.strip()).parent / ".claude/lab.lock"):
            if lock.exists():
                self.skipTest(f"the lab is locked ({lock}): the board script refuses everything")
        env = dict(os.environ, BOARD_HOST="bpi-amp.invalid", BOARD_USER="user")

        def board(*args):
            return subprocess.run(["bash", str(ROOT / "tools" / "board.sh"), "--dry-run", *args], env=env,
                                  capture_output=True, text=True, timeout=60)

        marker = self.tmp / "journal-injected"
        refused = board("journal", "-n", f"1; touch {marker}")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("whole number", refused.stderr)
        self.assertFalse(marker.exists())
        journal = board("journal", "-n", "50")
        self.assertEqual(journal.returncode, 0, journal.stderr)
        self.assertIn("ssh -t -- user@bpi-amp.invalid journalctl --user -u speakerd.service -b --no-pager -n 50",
                      journal.stdout.replace("\\", ""))
        status = board("status")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn('ssh -t -- user@bpi-amp.invalid bash "$HOME/.local/lib/speakerd/current/setup/speakerd-setup.sh" status',
                      status.stdout.replace("\\", ""))
        for option in ("--base-conf", "--stop-plugin-host", "--server-ip", "--no-restart"):
            refused = board("deploy", option, *(["192.0.2.10"] if option == "--server-ip" else []))
            self.assertNotEqual(refused.returncode, 0, option)
            self.assertIn(f"deploy does not take {option}", refused.stderr)
        self.assertNotEqual(board("smoke", "--window", "3; x").returncode, 0)


if __name__ == "__main__":
    unittest.main()
