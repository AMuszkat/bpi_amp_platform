"""firmware_ampd_install (docs/autonomous-development.md, section 5): firmware/ampd-setup.sh and
setup.sh's ampd component lay ampd out as firmware/CLAUDE.md documents it, its unit runs it as
systemd would, and status and smoke say whether it runs and what it is connected to.

The installers run as on a board with the test hooks (--home, --no-systemd), on a speakerd
installed from a bundle of the stand-in speakerd (stub_speakerd.c, as in
firmware_speakerd_install); the ampd that runs is the real one, started from the installed unit
(harness.UserUnit), on stand-in speakerds (stand_in_speakerd.py). Nothing here reaches a board.

Pass when:

- speakerd's install puts ampd in its version: bin/ampd, executable, firmware/ampd/ampd.py byte for
  byte, and the version's setup/ holds ampd-setup.sh and the unit;
- the secret file is read as the controller reads it: exactly one trailing \n or \r\n dropped,
  nothing else; two line ends, a lone \r, a space or a tab around it, a byte-order mark or bytes
  that are not UTF-8 are refused, writing nothing and never showing it; the secret alone, with
  \n or with \r\n gives the same ampd.json secret;
- ampd-setup.sh install refuses, writing nothing, before speakerd is installed, for an agent with
  no --hub-address, for --hub-address with --hub, and for a name that is not one; on the left
  speaker it writes ampd.json exactly {"version": 1, "name": "left", "hub": true, "listen":
  "0.0.0.0", "hub_address": null} and the unit byte for byte; on the right, with --hub-address,
  {"name": "right", "hub": false, ..., "hub_address": <it>}; a reinstall keeps what is not given
  and --listen changes only itself; ampd accepts the ampd.json the installer writes;
- the unit: after speakerd.service, no network unit, Restart=on-failure, RestartSec=1, no restart
  after exit 2 and no start limit;
- under (stand-in) systemd, install fails unless ampd says it listens after its start;
- setup.sh: a whole run (--dry-run) for the right speaker with no --hub-address is refused before
  anything changes; the whole run installs ampd after speakerd; --only ampd changes ampd.json;
- run from the installed unit on stand-in speakerds: smoke passes on the hub (it runs, connected
  to speakerd) and on the agent (also connected to the hub); with the hub stopped, the agent's
  smoke fails, saying it is not connected to the hub, and the hub's says ampd does not run;
  speakerd-setup.sh status shows ampd's; rollback restarts ampd too; uninstall of speakerd
  removes ampd's unit and keeps ampd.json.

    python3 -m unittest -v tests.vpair.test_firmware_ampd_install
"""

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path

from harness import (ADDRESS, AMPD, FIRMWARE, StandInSpeakerd, UserUnit, built, firmware, graph_document, lv2_uri,
                     revision_document, stub_speakerd)

MIN_VERSION = re.search(r"(?m)^SPEAKERD_MIN_VERSION=(\S+)$", (FIRMWARE / "speakerd-setup.sh").read_text()).group(1)
SECRET = "the-pairs-test-secret-0123456789"
DOCUMENT = revision_document(graph_document(lv2_uri("loudness_headless"), {"mode": "Force 1"}), 2)

# A stand-in for systemctl and journalctl: records the calls, prints $FAKE_SYSTEMD/journal.
FAKE_SYSTEMD = r'''#!/bin/bash
name="$(basename "$0")"; d="${FAKE_SYSTEMD:?}"
echo "$name $*" >> "$d/calls"
case "$name" in journalctl) cat "$d/journal" 2> /dev/null; exit 0 ;; esac
case " $* " in
    *" is-active "*) state="$(cat "$d/active" 2> /dev/null || echo active)"; [[ " $* " == *" --quiet "* ]] || echo "$state"; [ "$state" = active ]; exit ;;
    *" is-enabled "*) exit 1 ;;
    *" show "*) echo 0 ;;
esac
exit 0
'''


def unit_sections(path):
    sections, section = {}, None
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            section = line.strip("[]")
            continue
        key, _, value = line.partition("=")
        sections.setdefault(section, {}).setdefault(key, []).append(value)
    return sections


class AmpdInstall(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="vpair-ampd-install-"))
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)
        (cls.tmp / "no-pipewire").mkdir()
        (cls.tmp / "stub").mkdir()
        cls.env = {"XDG_RUNTIME_DIR": str(cls.tmp / "no-pipewire"), "STUB_VERSION": MIN_VERSION,
                   "STUB_DIR": str(cls.tmp / "stub")}
        root = cls.tmp / "build"
        (root / "speakerd/speakerd_artefacts/Release").mkdir(parents=True)
        shutil.copy(stub_speakerd(), root / "speakerd/speakerd_artefacts/Release/speakerd")
        (root / "loudness").mkdir()
        os.symlink(built("loudness", "headless").parents[2], root / "loudness/loudness_headless_artefacts")
        cls.secret_file = cls.tmp / "ampd.secret"
        cls.secret_file.write_text(SECRET + "\n")
        cls.bundle = cls.tmp / "bundle"
        firmware("speakerd-setup.sh", "bundle", "--arch", "native", "--out", cls.bundle, "--plugin", "loudness:headless",
                 env={"BUILD_ROOT": str(root)})
        # Another version (one more byte in its speakerd), for a rollback.
        with open(root / "speakerd/speakerd_artefacts/Release/speakerd", "ab") as f:
            f.write(b"\0")
        cls.bundle2 = cls.tmp / "bundle2"
        firmware("speakerd-setup.sh", "bundle", "--arch", "native", "--out", cls.bundle2, "--plugin", "loudness:headless",
                 env={"BUILD_ROOT": str(root)})

    def prepare(self, name, role=None):
        """A fresh home, with speakerd installed for `role` when given."""
        folder = self.tmp / name
        shutil.rmtree(folder, ignore_errors=True)
        if role:
            firmware("speakerd-setup.sh", "install", "--bundle", self.bundle, "--home", folder, "--no-systemd",
                     "--amplifier", "amplifier", "--role", role, env=self.env)
        return folder

    def ampd_setup(self, folder, *args, check=True, env=None, secret=True):
        """ampd-setup.sh with the test hooks; an install gets the pair's secret file unless `secret`
        is False."""
        if args and args[0] == "install" and secret and "--secret-file" not in args:
            args = (*args, "--secret-file", self.secret_file)
        return firmware("ampd-setup.sh", *args, "--home", folder, "--no-systemd", env=env or self.env, check=check)

    def config(self, folder):
        return json.loads((folder / ".local/state/speakerd/ampd.json").read_text())

    def test_the_layout(self):
        folder = self.prepare("layout", "left")
        lib = folder / ".local/lib/speakerd/current"
        self.assertTrue(os.access(lib / "bin/ampd", os.X_OK))
        self.assertEqual((lib / "bin/ampd").read_bytes(), AMPD.read_bytes())
        for path in ("setup/ampd-setup.sh", "setup/ampd/ampd.service", "setup/ampd/ampd.py"):
            self.assertTrue((lib / path).exists(), path)
        self.assertTrue(os.access(lib / "setup/ampd-setup.sh", os.X_OK))
        result = self.ampd_setup(folder, "install")
        self.assertEqual(self.config(folder), {"version": 1, "name": "left", "hub": True, "listen": "0.0.0.0",
                                               "hub_address": None, "secret": SECRET})
        self.assertEqual((folder / ".local/state/speakerd/ampd.json").stat().st_mode & 0o777, 0o600)
        self.assertNotIn(SECRET, result.stdout)
        status = self.ampd_setup(folder, "status")
        self.assertNotIn(SECRET, status.stdout)
        self.assertIn('"secret": "(set)"', status.stdout)
        self.assertEqual((lib / "bin/tweeter_rule.py").read_bytes(), (FIRMWARE / "speakerd/tweeter_rule.py").read_bytes())
        unit = folder / ".config/systemd/user/ampd.service"
        self.assertEqual(unit.read_bytes(), (FIRMWARE / "ampd/ampd.service").read_bytes())
        self.assertIn("left, the hub, listening on 0.0.0.0", result.stdout)

        sections = unit_sections(unit)
        print(f"\n  [Unit] {sections['Unit']}\n  [Service] Restart {sections['Service']['Restart']}, "
              f"RestartPreventExitStatus {sections['Service']['RestartPreventExitStatus']}")
        self.assertEqual(sections["Unit"]["After"], ["speakerd.service"])
        for key in ("After", "Wants", "Requires", "BindsTo"):
            self.assertFalse([v for v in sections["Unit"].get(key, []) if "network" in v], f"{key}: a network unit")
        self.assertEqual(sections["Unit"]["StartLimitIntervalSec"], ["0"])
        self.assertEqual(sections["Service"]["Restart"], ["on-failure"])
        self.assertEqual(sections["Service"]["RestartSec"], ["1"])
        self.assertIn("2", sections["Service"]["RestartPreventExitStatus"][0].split())

        # A reinstall keeps it; --listen changes only itself.
        self.ampd_setup(folder, "install")
        self.assertEqual(self.config(folder)["listen"], "0.0.0.0")
        self.ampd_setup(folder, "install", "--listen", "127.0.1.1")
        self.assertEqual(self.config(folder), {"version": 1, "name": "left", "hub": True, "listen": "127.0.1.1",
                                               "hub_address": None, "secret": SECRET})

    def test_refusals(self):
        bare = self.prepare("bare")
        refused = self.ampd_setup(bare, "install", "--name", "left", "--hub", check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("comes with speakerd's version", refused.stdout)
        self.assertFalse((bare / ".local/state/speakerd/ampd.json").exists())

        folder = self.prepare("right", "right")
        short = self.tmp / "short.secret"
        short.write_text("too short\n")
        refused = self.ampd_setup(folder, "install", "--hub-address", "bpi-amp.local", check=False, secret=False)
        self.assertNotEqual(refused.returncode, 0, refused.stdout)
        self.assertIn("no secret: give --secret-file FILE", refused.stdout)
        refused = self.ampd_setup(folder, "install", "--hub-address", "bpi-amp.local", "--secret-file", short, check=False)
        self.assertIn("16 to 256 printable characters", refused.stdout)
        self.assertNotIn("too short", refused.stdout)
        self.assertFalse((folder / ".local/state/speakerd/ampd.json").exists())
        # Read as the controller reads it: exactly one trailing \n or \r\n is dropped, nothing else.
        odd = self.tmp / "odd.secret"
        for content in (SECRET + "\n\n", SECRET + "\r", SECRET + "\n\r\n", " " + SECRET + "\n", SECRET + " \n",
                        SECRET + "\t\n", "\ufeff" + SECRET + "\n"):
            odd.write_bytes(content.encode())
            refused = self.ampd_setup(folder, "install", "--hub-address", "bpi-amp.local", "--secret-file", odd,
                                      check=False)
            self.assertNotEqual(refused.returncode, 0, repr(content))
            self.assertIn("at most one line end", refused.stdout, repr(content))
            self.assertNotIn(SECRET, refused.stdout)
            self.assertFalse((folder / ".local/state/speakerd/ampd.json").exists(), repr(content))
        odd.write_bytes(b"\xff" + SECRET.encode())
        self.assertNotEqual(self.ampd_setup(folder, "install", "--hub-address", "bpi-amp.local", "--secret-file", odd,
                                            check=False).returncode, 0)
        for content in (SECRET, SECRET + "\r\n"):
            odd.write_bytes(content.encode())
            self.ampd_setup(folder, "install", "--hub-address", "bpi-amp.local", "--secret-file", odd, "--no-restart")
            self.assertEqual(self.config(folder)["secret"], SECRET, repr(content))
            (folder / ".local/state/speakerd/ampd.json").unlink()
        for args, said in ((["install"], "an agent needs the hub's address"),
                           (["install", "--hub", "--hub-address", "bpi-amp.local"], "--hub-address is for an agent"),
                           (["install", "--name", "no spaces"], "--name: letters")):
            refused = self.ampd_setup(folder, *args, check=False)
            self.assertNotEqual(refused.returncode, 0, refused.stdout)
            self.assertIn(said, refused.stdout)
            self.assertFalse((folder / ".local/state/speakerd/ampd.json").exists())
        self.ampd_setup(folder, "install", "--hub-address", "bpi-amp.local")
        self.assertEqual(self.config(folder), {"version": 1, "name": "right", "hub": False, "listen": "0.0.0.0",
                                               "hub_address": "bpi-amp.local", "secret": SECRET})
        # A reinstall without --secret-file keeps it.
        self.ampd_setup(folder, "install", secret=False)
        self.assertEqual(self.config(folder)["secret"], SECRET)
        check = subprocess.run([str(folder / ".local/lib/speakerd/current/bin/ampd"), "--config",
                                str(folder / ".local/state/speakerd/ampd.json"), "--check-config"],
                               capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertEqual(check.stdout.strip(),
                         "right: an agent of the hub at bpi-amp.local, listening on 0.0.0.0, with the pair's secret")

    def test_setup_sh(self):
        folder = self.prepare("setup-right")
        args = ["--home", folder, "--no-systemd", "--bundle", self.bundle, "--role", "right", "--amplifier", "amplifier",
                "--skip", "snapcast"]
        refused = firmware("setup.sh", *args, "--dry-run", env=self.env, check=False)
        self.assertNotEqual(refused.returncode, 0, refused.stdout)
        self.assertIn("an agent needs the hub's address", refused.stdout)
        self.assertFalse((folder / ".local").exists(), refused.stdout)

        result = firmware("setup.sh", *args, "--hub-address", "bpi-amp.local", "--secret-file", self.secret_file,
                          env=self.env).stdout
        self.assertNotIn(SECRET, result)
        order = [result.index(f"==> {c}:") for c in ("pipewire", "speakerd", "ampd")]
        self.assertEqual(order, sorted(order), result)
        self.assertEqual(self.config(folder)["hub_address"], "bpi-amp.local")
        firmware("setup.sh", "--home", folder, "--no-systemd", "--only", "ampd", "--listen", "127.0.1.2", env=self.env)
        self.assertEqual(self.config(folder), {"version": 1, "name": "right", "hub": False, "listen": "127.0.1.2",
                                               "hub_address": "bpi-amp.local", "secret": SECRET})

    def test_under_systemd_ampd_must_say_it_listens(self):
        folder = self.prepare("systemd", "left")
        for journal, ok in (([], False), (["ampd: listening on 0.0.0.0: control 7450, knobs 7451, monitor 7452"], True)):
            d = self.tmp / "fake-systemd"
            shutil.rmtree(d, ignore_errors=True)
            (d / "bin").mkdir(parents=True)
            for tool in ("systemctl", "journalctl"):
                (d / "bin" / tool).write_text(FAKE_SYSTEMD)
                (d / "bin" / tool).chmod(0o755)
            (d / "journal").write_text("".join(line + "\n" for line in journal))
            shutil.rmtree(folder / ".config/systemd/user", ignore_errors=True)
            env = dict(self.env, FAKE_SYSTEMD=str(d), PATH=f"{d / 'bin'}:{os.environ['PATH']}", AMPD_START_WAIT="1")
            result = firmware("ampd-setup.sh", "install", "--home", folder, "--secret-file", self.secret_file, env=env,
                              check=False)
            print(f"\n  journal {journal or 'empty'}: status {result.returncode}")
            self.assertEqual(result.returncode == 0, ok, result.stdout)
            if not ok:
                self.assertIn("ampd did not say it listens", result.stdout)
            self.assertIn("systemctl --user restart ampd.service", (d / "calls").read_text())

    def test_status_and_smoke_from_the_installed_unit(self):
        folders = {"left": self.prepare("run-left", "left"), "right": self.prepare("run-right", "right")}
        self.ampd_setup(folders["left"], "install", "--listen", ADDRESS["left"])
        self.ampd_setup(folders["right"], "install", "--listen", ADDRESS["right"], "--hub-address", ADDRESS["left"])
        envs, units, runs, sds = {}, {}, {}, {}
        for name, folder in folders.items():
            run = self.tmp / f"xdg-{name}"
            shutil.rmtree(run, ignore_errors=True)
            run.mkdir()
            envs[name] = dict(os.environ, XDG_RUNTIME_DIR=str(run))
            (folder / ".local/state/speakerd/graph.json").write_text(json.dumps(DOCUMENT))
            speaker = type("Speaker", (), {"env": envs[name]})()
            units[name] = UserUnit(folder / ".config/systemd/user/ampd.service", folder, speaker)
            sds[name] = StandInSpeakerd(envs[name], folder / ".local/state/speakerd").start()
        try:
            for name in ("left", "right"):
                runs[name] = units[name].start()
                runs[name].wait_for("ampd: listening on")
            runs["right"].wait_for("ampd: hub: connected")
            for name, folder in folders.items():
                smoke = self.ampd_setup(folder, "smoke", "--wait", "5", env=envs[name], check=False)
                print(f"\n  {name}: smoke\n" + smoke.stdout)
                self.assertEqual(smoke.returncode, 0, smoke.stdout)
                self.assertIn("connected to speakerd", smoke.stdout)
            self.assertIn(f"connected to the hub at {ADDRESS['left']} (left)", smoke.stdout)
            status = firmware("speakerd-setup.sh", "status", "--home", folders["left"], "--no-systemd", env=envs["left"]).stdout
            self.assertIn("speakerd: connected (0.3.0 (stand-in), layer current, revision 2)", status)
            self.assertIn("agent right: online", status)

            runs["left"].signal(signal.SIGTERM)
            runs["left"].finish()
            smoke = self.ampd_setup(folders["right"], "smoke", "--wait", "2", env=envs["right"], check=False)
            self.assertNotEqual(smoke.returncode, 0, smoke.stdout)
            self.assertIn(f"not connected to the hub at {ADDRESS['left']}", smoke.stdout)
            smoke = self.ampd_setup(folders["left"], "smoke", "--wait", "1", env=envs["left"], check=False)
            self.assertNotEqual(smoke.returncode, 0, smoke.stdout)
            self.assertIn("ampd does not run", smoke.stdout)
        finally:
            for run in runs.values():
                if not run.process.stdout.closed:
                    run.signal(signal.SIGTERM)
                    run.finish()
            for sd in sds.values():
                sd.stop()

        # speakerd's rollback restarts ampd; its uninstall takes ampd's unit, not ampd.json.
        left = folders["left"]
        firmware("speakerd-setup.sh", "install", "--bundle", self.bundle2, "--home", left, "--no-systemd",
                 "--amplifier", "amplifier", env=self.env)
        result = firmware("speakerd-setup.sh", "rollback", "--home", left, "--no-systemd", env=self.env).stdout
        self.assertIn("systemctl --user restart ampd.service", result)
        result = firmware("speakerd-setup.sh", "uninstall", "--home", left, "--no-systemd", env=self.env).stdout
        self.assertIn("ampd uninstalled", result)
        self.assertFalse((left / ".config/systemd/user/ampd.service").exists())
        self.assertTrue((left / ".local/state/speakerd/ampd.json").exists())


if __name__ == "__main__":
    unittest.main()
