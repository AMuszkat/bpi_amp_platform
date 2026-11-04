#!/usr/bin/env python3
"""The virtual pair's modules on a pool of workers, each worker on a virtual pair of its own
(README.md, "The pool").

    python3 tests/vpair/pool.py --jobs N --note FILE [MODULE...]

From the repository root, in the development container (tools/test.sh calls it there; from
outside, it moves there itself, as the harness does). A MODULE is test_<name> (test_<name>.<Class>
names one unit); none is every module of tests/vpair, as `unittest discover` finds them.

1. The prebuild. Every module says what its built() calls need, also through the harness's
   classes, in POOL_BUILDS ("speakerd", "loudness:headless"; "controller?" for one a test skips
   without). The pool builds all of them first, once, with tools/build.sh: one call per build tree
   with every kind it needs, the trees at once. A failed build prints its output and fails the run
   before any test (one marked "?" fails only the built() that asks for it, as it would have).
   Under the pool built() builds nothing (VPAIR_PREBUILT) and refuses what the module's POOL_BUILDS
   does not name: build/ may hold a stale one.
2. The units: each test class the unittest loader finds in a module (a module with POOL_WHOLE =
   True is one unit; one with POOL_SPLIT = True, whose tests are independent, a unit per test),
   each in a process of its own (setUpModule and setUpClass run once per unit), on N workers,
   the longest first: durations learned from earlier runs (build/vpair-pool-durations.json of the
   main checkout, merged and written atomically after each run), seeded (SEED) for units never
   timed; a unit known to neither counts as long. Each worker has its own VPAIR_DIR (/tmp/vpool<k>,
   short for PipeWire's socket path), VPAIR_ADDRESS_BASE (127.77.<k>), VPAIR_PLUGIN_HOST
   (vpairPlugHost<k>), X display (VPAIR_DISPLAY and DISPLAY, :<100 + k>) and VPAIR_PREBUILT
   (<root>/build); a pair left in its VPAIR_DIR by an interrupted run is brought down before it
   starts. A module with POOL_ALONE = True (it measures CPU: test_chain_cost) runs after the pool,
   alone. (`pool.py --unit NAME --events FILE` is how the pool runs one unit, in its own process.)
3. Each unit's whole output, stdout and stderr, is printed as one block when it ends and kept in
   build/vpair-pool/<unit>.log; then a table (unit, worker, seconds, result), the failed units and
   the container's peak use (tasks against pids.max, memory, /dev/shm).
4. FILE gets tools/test.sh's note: "N tests[, K failed: id, id], M skipped[: id (reason); ...]",
   ids as unittest's test.id(). A unit whose process ends without its result (it died, or ran past
   its timeout: 4 times its learned duration, at least 300 s; 900 s unknown) is failed under its
   unit name, with what it said before.
5. Exit status 0 when every test passed (skips allowed), 1 otherwise. SIGINT or SIGTERM stops the
   workers, brings their pairs down, writes the note so far, and ends the pool by that signal.
"""

import argparse
import contextlib
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
VPAIR = HERE / "vpair.sh"
LOGS = ROOT / "build" / "vpair-pool"

# Seconds per unit, for units the pool never timed: measured by the pool on the machine it was written
# on (24 threads, 12 workers; the first runs were seeded with the investigation's timings of each class).
SEED = {
    "test_monitor_fanout.MonitorFanout": 63.7, "test_controller_end_to_end.ControllerEndToEnd": 34.3,
    "test_knob_latency.KnobLatency": 32.8, "test_ampd_protocol.Protocol.test_monitor_relay": 25.6,
    "test_graph_edit.GraphEdit": 23.8, "test_firmware_speakerd_install.Install.test_smoke_reports_the_timing": 20.8,
    "test_ampd_persistence.AmpdPersistence": 19.6, "test_firmware_ampd_install.AmpdInstall": 18.2,
    "test_speakerd_installed.Installed": 17.9, "test_meter_values.MeterValues": 16.7,
    "test_speakerd_device_limits.DeviceLimits": 14.6, "test_passthrough_safety.PassthroughSafety": 12.9,
    "test_ampd_protocol.Protocol.test_revisions": 12.2,
    "test_firmware_speakerd_install.Install.test_smoke_reports_limits_it_does_not_play": 11.8,
    "test_chain_cost.ChainCost": 10.8, "test_ampd_protocol.Protocol.test_persistence": 10.2,
    "test_speakerd_crash_restart.Supervision": 10.1, "test_speakerd_same_sound.SameSoundAsTheCompleteBuild": 10.0,
    "test_firmware_speakerd_install.Systemd.test_install_checks_what_speakerd_says_after_its_start": 9.9,
    "test_buffer_size.TheChosenSize": 9.1, "test_drag_during_revision.DragDuringRevision": 9.0,
    "test_ampd_protocol.Protocol.test_gestures": 8.7,
    "test_firmware_speakerd_install.Systemd.test_limits_and_rollback_restart_a_running_speakerd": 7.9,
    "test_speakerd_start_order.StartOrder": 7.9, "test_ampd_restart.AmpdRestart": 7.7,
    "test_board_cpu.BoardCpu": 7.5,
    "test_firmware_speakerd_install.Install.test_smoke_finds_snapclient_by_its_application_name": 7.5,
    "test_ampd_protocol.Protocol.test_the_local_link": 7.0, "test_hub_down.HubDown": 6.6,
    "test_speakerd_quantum_change.QuantumChange": 6.3,
    "test_ampd_protocol.Protocol.test_values_set_while_a_revision_applies": 5.8,
    "test_ampd_protocol.Protocol.test_the_buffer_size": 5.7, "test_speakerd_installed.DropInNotLoaded": 5.4,
    "test_speakerd_state_layers.StateLayers": 5.3, "test_audio_path.AudioPath": 5.0,
    "test_two_controllers.TwoControllers": 4.3,
    "test_firmware_speakerd_install.Systemd.test_setup_stops_the_plugin_host_before_pipewire_restarts": 4.1,
    "test_ampd_protocol.Protocol.test_a_hub_that_cannot_prove_the_secret_is_not_believed": 4.0,
    "test_ampd_protocol.Protocol.test_monitoring": 4.0, "test_speakerd_restart.Restart": 3.8,
    "test_firmware_speakerd_install.Install.test_a_reinstall_keeps_the_device_and_its_limits": 3.8,
    "test_ampd_protocol.Protocol.test_a_large_document_keeps_every_controller": 3.7,
    "test_speakerd_quantum_change.QuantumAboveMaxQuantum": 3.6,
    "test_ampd_protocol.Protocol.test_the_hub_goes_on_from_its_seq": 3.4,
    "test_buffer_size.OutsideOneSpeakersRange": 3.3,
    "test_firmware_speakerd_install.Systemd.test_a_reinstall_restarts_nothing": 3.2,
    "test_ampd_protocol.Protocol.test_board_cpu": 2.6,
    "test_firmware_speakerd_install.Install.test_refuses_a_kept_layer_naming_a_plugin_the_bundle_lacks": 2.6,
    "test_firmware_speakerd_install.Install.test_refuses_a_factory_that_is_not_a_graph_document": 2.2,
    "test_firmware_speakerd_install.Install.test_setup_checks_first_and_installs_in_boot_order": 2.2,
    "test_buffer_size.TheRealController": 2.2, "test_speakerd_second_instance.SecondInstance": 2.1,
    "test_firmware_speakerd_install.Install.test_names_a_bundle_shadowed_by_a_system_lv2_folder": 1.8,
    "test_plugin_identity.PluginIdentity": 1.8, "test_ampd_protocol.Protocol.test_parameters": 1.8,
    "test_ampd_protocol.Protocol.test_the_pairs_secret": 1.7,
    "test_firmware_speakerd_install.Install.test_refuses_a_speakerd_that_ignores_the_limits": 1.7,
    "test_firmware_speakerd_install.Install.test_routing_and_uninstall": 1.7,
    "test_firmware_speakerd_install.Install.test_versions_rollback_and_pruning": 1.7,
    "test_firmware_speakerd_install.Install.test_the_bundles_chain_document": 1.7,
    "test_ampd_protocol.Protocol.test_an_offline_speaker_is_caught_up": 1.6,
    "test_firmware_speakerd_install.Install.test_finding_the_amplifier": 1.6,
    "test_firmware_speakerd_install.Install.test_limits_writes_device_json": 1.5,
    "test_firmware_speakerd_install.Install.test_the_layout": 1.5, "test_ampd_protocol.Protocol.test_bad_lines": 1.5,
    "test_ampd_protocol.Protocol.test_handshakes_are_capped": 1.5,
    "test_firmware_speakerd_install.Install.test_import_filtergraph": 1.5,
    "test_firmware_speakerd_install.Install.test_the_repository_conf_holds_the_sink": 1.5,
    "test_firmware_speakerd_install.Systemd.test_setup_installs_the_base_conf_at_its_rate": 1.5,
    "test_speakerd_crash_restart.RealSpeakerd": 1.5,
    "test_firmware_speakerd_install.Install.test_uninstall_stops_snapclient_when_its_old_target_is_gone": 1.4,
    "test_firmware_speakerd_install.Remote.test_the_hints_name_the_installed_copy": 1.3,
    "test_firmware_speakerd_install.SnapcastTarget.test_a_second_run_changes_nothing": 1.3,
    "test_firmware_speakerd_install.Install.test_the_old_plugin_host": 1.2,
    "test_firmware_speakerd_install.SnapcastTarget.test_the_server_by_name": 1.2,
    "test_ampd_protocol.Protocol.test_the_hub_adopts_a_newer_revision": 1.2,
    "test_firmware_speakerd_install.Remote.test_check_and_dry_run_on_the_board": 1.1,
    "test_ampd_protocol.Protocol.test_without_a_secret_an_agent_is_not_displaced": 1.1,
    "test_ampd_protocol.Protocol.test_unknown_keys_are_kept": 1.0,
    "test_firmware_speakerd_install.Install.test_the_first_install_needs_a_role": 1.0,
    "test_firmware_speakerd_install.Install.test_routing_into_speakerd_needs_a_unit_that_reads_client_rt": 1.0,
    "test_firmware_speakerd_install.SnapcastTarget.test_how_the_player_is_found": 0.9,
    "test_firmware_speakerd_install.Install.test_the_bundle": 0.9,
    "test_firmware_speakerd_install.Systemd.test_setup_stops_on_a_failed_pipewire_restart": 0.9,
    "test_speakerd_import.Import": 0.9, "test_ampd_protocol.Protocol.test_limits_and_bad_values": 0.9,
    "test_ampd_protocol.Protocol.test_a_malformed_quantum_on_disk_is_left_out": 0.8,
    "test_firmware_speakerd_install.Remote.test_every_argument_arrives_as_given": 0.8,
    "test_firmware_speakerd_install.SnapcastTarget.test_snapclient_is_installed_once": 0.8,
    "test_firmware_speakerd_install.Remote.test_snapcast_remote_ships_the_route_check": 0.7,
    "test_ampd_protocol.Protocol.test_lines_before_the_proof_are_capped": 0.6,
    "test_firmware_speakerd_install.Remote.test_the_remote_mode_has_the_lab_lock": 0.6,
    "test_firmware_speakerd_install.SnapcastTarget.test_routing_only_twice_changes_nothing": 0.6,
    "test_ampd_protocol.Protocol.test_an_outdated_current_layer_gets_the_revision": 0.5,
    "test_ampd_protocol.Protocol.test_times_are_checked": 0.5,
    "test_firmware_speakerd_install.Remote.test_not_as_root": 0.5,
    "test_firmware_speakerd_install.Remote.test_the_board_script": 0.4,
    "test_ampd_protocol.Protocol.test_the_authentication_vector": 0.3,
}
UNKNOWN = 600.0               # a unit never timed: scheduled as a long one
TIMEOUT_FACTOR, TIMEOUT_MIN, TIMEOUT_UNKNOWN = 4.0, 300.0, 900.0
KINDS = ("complete", "control", "headless")


def main_checkout():
    """The checkout holding the git directory (a worktree's main checkout), or this one."""
    try:
        common = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                capture_output=True, text=True, timeout=10).stdout.strip()
        if common:
            return Path(common).parent
    except (OSError, subprocess.SubprocessError):
        pass
    return ROOT


def one_line(text):
    return " ".join(str(text).split())


# ---------------------------------------------------------------------------------------------
# A unit, in its own process: python3 tests/vpair/pool.py --unit NAME --events FILE

def run_child(name, events_path):
    """Runs one unit as tools/test.sh's runner runs a module, and writes what happens to
    `events_path` as it happens, one JSON line each, so that the pool knows what a unit that dies
    did before; the last line is the result."""
    sys.path.insert(0, str(HERE))
    sys.stdout.reconfigure(line_buffering=True)      # its prints in order with unittest's lines
    events = open(events_path, "a")

    def emit(obj):
        events.write(json.dumps(obj) + "\n")
        events.flush()

    class Result(unittest.TextTestResult):
        def startTest(self, test):
            emit({"start": test.id()})
            super().startTest(test)

        def addFailure(self, test, err):
            super().addFailure(test, err)
            emit({"failure": test.id()})

        def addError(self, test, err):
            super().addError(test, err)
            emit({"error": test.id()})

        def addSkip(self, test, reason):
            super().addSkip(test, reason)
            emit({"skip": test.id(), "reason": str(reason)})

        def addSubTest(self, test, subtest, err):
            super().addSubTest(test, subtest, err)
            if err is not None:
                emit({"failure" if issubclass(err[0], test.failureException) else "error": subtest.id()})

    suite = unittest.defaultTestLoader.loadTestsFromName(name)
    result = unittest.TextTestRunner(verbosity=2, resultclass=Result).run(suite)
    emit({"end": {"run": result.testsRun, "failures": [t.id() for t, _ in result.failures],
                  "errors": [t.id() for t, _ in result.errors],
                  "skipped": [[t.id(), str(reason)] for t, reason in result.skipped],
                  "successful": result.wasSuccessful()}})
    events.close()
    sys.exit(0 if result.wasSuccessful() else 1)


if len(sys.argv) > 1 and sys.argv[1] == "--unit":
    run_child(sys.argv[2], sys.argv[4] if len(sys.argv) > 4 and sys.argv[3] == "--events" else os.devnull)

# The pool itself runs where the virtual pair runs: the harness moves this command line into the
# development container when it is used (BPI_CONTAINER), and the modules import it anyway.
sys.path.insert(0, str(HERE))
import harness  # noqa: E402,F401  (its import is the move into the container)


# ---------------------------------------------------------------------------------------------
# The units

class Unit:
    def __init__(self, name, module, count, builds, alone):
        self.name, self.module, self.count, self.builds, self.alone = name, module, count, builds, alone
        self.worker = self.seconds = self.state = None
        self.run, self.failures, self.errors, self.skipped = 0, [], [], []
        self.successful, self.complete, self.why = False, False, ""

    def result_text(self):
        if self.state != "ran":
            return self.state
        tests = f"{self.run} test{'s' if self.run != 1 else ''}"
        if self.successful:
            return f"passed ({tests}" + (f", {len(self.skipped)} skipped)" if self.skipped else ")")
        if not self.complete:
            return f"FAILED: {self.why}"
        if not self.run:
            return "FAILED before any test ran"
        return f"FAILED ({len(self.failures) + len(self.errors)} of {tests})"


def module_names(names):
    """The modules named, or those `unittest discover` finds in tests/vpair (test*.py, a valid module
    name, in its order)."""
    if names:
        return list(names)
    return sorted(p.stem for p in HERE.iterdir()
                  if p.is_file() and re.fullmatch(r"test\w*\.py", p.name, re.IGNORECASE))


def find_units(names):
    """The units of the modules named, in the order the unittest loader runs them, and errors (what
    could not be read)."""
    loader = unittest.TestLoader()
    units, errors = [], []
    for name in module_names(names):
        module_name = name.split(".")[0]
        try:
            count = loader.loadTestsFromName(name).countTestCases()
        except Exception:                        # noqa: BLE001  (the unit's own run says what)
            count = 1
        module = sys.modules.get(module_name)
        if "." in name:                          # test_x.Class: that unit alone
            units.append(Unit(name, module_name, count, declared(module, errors), alone(module)))
            continue
        if module is None or getattr(module, "POOL_WHOLE", False) or hasattr(module, "load_tests"):
            # Not importable (its unit's run reproduces the error), or to stay whole.
            units.append(Unit(name, name, count, declared(module, errors), alone(module)))
            continue
        builds, lonely, split = declared(module, errors), alone(module), getattr(module, "POOL_SPLIT", False)
        for attr in dir(module):                 # as loadTestsFromModule finds the classes
            obj = getattr(module, attr)
            if isinstance(obj, type) and issubclass(obj, unittest.TestCase):
                tests = list(loader.loadTestsFromTestCase(obj))
                if split:                        # its tests are independent: one unit each
                    units += [Unit(f"{name}.{attr}.{t._testMethodName}", name, 1, builds, lonely) for t in tests]
                elif tests:                      # a base class without tests yields nothing
                    units.append(Unit(f"{name}.{attr}", name, len(tests), builds, lonely))
    return units, errors


def declared(module, errors):
    """A module's POOL_BUILDS, checked: ("speakerd", "loudness:headless", "controller?")."""
    builds = tuple(getattr(module, "POOL_BUILDS", ()) or ())
    for entry in builds:
        what, _, kind = entry.rstrip("?").partition(":")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", what) or (kind and kind not in KINDS):
            errors.append(f"{module.__name__}.POOL_BUILDS: {entry!r} is not an application or plugin:kind "
                          f"(kinds: {', '.join(KINDS)}; '?' after one a test skips without)")
    return builds


def alone(module):
    return bool(getattr(module, "POOL_ALONE", False))


# ---------------------------------------------------------------------------------------------
# The prebuild

def prebuild(units, out):
    """Builds what the units' modules declare, one tools/build.sh per build tree, the trees at once.
    Returns (the names built, the optional names that failed, whether a required one failed)."""
    trees = {}            # what -> {"kinds": [...], "required": bool, "names": set, "modules": set}
    for unit in units:
        for entry in unit.builds:
            optional = entry.endswith("?")
            name = entry.rstrip("?")
            what, _, kind = name.partition(":")
            tree = trees.setdefault(what, {"kinds": [], "required": False, "names": set(), "modules": set()})
            if kind and kind not in tree["kinds"]:
                tree["kinds"].append(kind)
            tree["required"] |= not optional
            tree["names"].add(name)
            tree["modules"].add(unit.module)
    if not trees:
        return set(), set(), False
    started = time.monotonic()
    running = []
    for what, tree in trees.items():
        command = [str(ROOT / "tools" / "build.sh"), what, *tree["kinds"]]
        process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True)
        running.append((what, tree, command, process, time.monotonic()))
    built, unbuilt, failed = set(), set(), False
    for what, tree, command, process, t0 in running:
        output = process.communicate()[0]
        shown = " ".join(["tools/build.sh", *command[1:]])
        took = time.monotonic() - t0
        if process.returncode == 0:
            built |= tree["names"]
            out(f"prebuild: {shown}: built in {took:.1f} s")
            continue
        for_whom = ", ".join(sorted(tree["modules"]))
        if tree["required"]:
            failed = True
            out(f"prebuild: {shown} FAILED (exit {process.returncode}) after {took:.1f} s, for {for_whom}:\n"
                + output.rstrip() + f"\nprebuild: end of {shown}")
        else:
            unbuilt |= tree["names"]
            out(f"prebuild: {shown} failed (exit {process.returncode}), optional for {for_whom}: its built() "
                f"fails as the build would\n" + "\n".join(output.rstrip().splitlines()[-40:]))
    out(f"prebuild: {len(trees)} build tree(s) in {time.monotonic() - started:.1f} s")
    return built, unbuilt, failed


# ---------------------------------------------------------------------------------------------
# The workers

class Slot:
    """A worker's place: /tmp/vpool<k> for its pair, its addresses and its stand-in plugin host's
    name, held with a lock (another pool in this container takes other slots)."""

    def __init__(self, k, lock):
        self.k, self.lock = k, lock
        self.dir = f"/tmp/vpool{k}"
        self.base = f"127.77.{k}"
        self.host = f"vpairPlugHost{k}"
        self.display = str(100 + k)

    def env(self):
        # The X display a JUCE program of its own opens (speakerd, the controller), and where its
        # harness.VirtualDisplay runs: without DISPLAY, JUCE opens :0.0, which would be any
        # worker's (or plugin-check's) Xvfb, and dies when that one stops.
        return {"VPAIR_DIR": self.dir, "VPAIR_ADDRESS_BASE": self.base, "VPAIR_PLUGIN_HOST": self.host,
                "VPAIR_DISPLAY": self.display, "DISPLAY": f":{self.display}"}

    def clean(self):
        """Brings down a pair left in its VPAIR_DIR (an interrupted run, a killed unit) and stops a
        stand-in plugin host left under its name; says what it found."""
        found = []
        for pid in os.listdir("/proc"):
            if pid.isdigit():
                try:
                    if Path(f"/proc/{pid}/comm").read_text().strip() == self.host:
                        os.kill(int(pid), signal.SIGKILL)
                        found.append(f"{self.host} (pid {pid}) killed")
                except (OSError, ValueError):
                    pass
        left = [name for name in ("left", "right") if (Path(self.dir) / name).exists()]
        if left:
            subprocess.run([str(VPAIR), "down"], env=dict(os.environ, **self.env()), stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            found.append(f"the pair in {self.dir} ({', '.join(left)}) brought down")
        return found


def take_slots(n):
    slots = []
    for k in range(100):                     # vpairPlugHost<k> is 15 characters at most
        lock = open(f"/tmp/vpool{k}.lock", "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock.close()
            continue
        slots.append(Slot(k, lock))
        if len(slots) == n:
            return slots
    raise SystemExit(f"pool: only {len(slots)} free worker slots of the 100 (/tmp/vpool<k>.lock): other pools run")


class Watch(threading.Thread):
    """The container's peak use while the pool runs: tasks (threads count) against pids.max, memory,
    /dev/shm."""

    def __init__(self):
        super().__init__(daemon=True)
        self.peak = {"pids": 0, "memory": 0, "shm": 0}
        self.done = threading.Event()

    @staticmethod
    def read(path):
        try:
            text = Path(path).read_text().strip()
            return None if text == "max" else int(text)
        except (OSError, ValueError):
            return None

    def run(self):
        while not self.done.wait(0.5):
            for key, value in (("pids", self.read("/sys/fs/cgroup/pids.current")),
                               ("memory", self.read("/sys/fs/cgroup/memory.current")), ("shm", self.shm())):
                if value is not None:
                    self.peak[key] = max(self.peak[key], value)

    @staticmethod
    def shm():
        try:
            st = os.statvfs("/dev/shm")
            return (st.f_blocks - st.f_bfree) * st.f_frsize
        except OSError:
            return None

    def summary(self):
        pids_max, memory_max = self.read("/sys/fs/cgroup/pids.max"), self.read("/sys/fs/cgroup/memory.max")
        try:
            st = os.statvfs("/dev/shm")
            shm_size = st.f_blocks * st.f_frsize
        except OSError:
            shm_size = 0
        gib, mib = 1 << 30, 1 << 20
        text = (f"container: peak {self.peak['pids']} tasks of pids.max {pids_max or 'max'}, "
                f"memory {self.peak['memory'] / gib:.1f} GiB of {f'{memory_max / gib:.1f} GiB' if memory_max else 'max'}, "
                f"/dev/shm {self.peak['shm'] / mib:.0f} of {shm_size / mib:.0f} MiB")
        if pids_max and self.peak["pids"] > 0.8 * pids_max:
            text += f"\ncontainer: WARNING the tasks came within 20 % of pids.max ({pids_max}): use fewer --jobs"
        return text


class Pool:
    def __init__(self, slots, out, built, unbuilt, durations, fixed_timeout=None):
        self.slots, self.out = slots, out
        self.built, self.unbuilt, self.durations = built, unbuilt, durations
        self.fixed_timeout = fixed_timeout
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.running = {}                       # pid -> Unit

    def timeout(self, unit):
        if self.fixed_timeout:
            return self.fixed_timeout
        known = self.durations.get(unit.name)
        return max(TIMEOUT_MIN, TIMEOUT_FACTOR * known) if known else TIMEOUT_UNKNOWN

    def run_unit(self, unit, slot):
        """Runs one unit in its own session on `slot`, and prints its whole output when it ends."""
        log, events = LOGS / f"{unit.name}.log", LOGS / f"{unit.name}.events"
        events.unlink(missing_ok=True)
        own = [entry.rstrip("?") for entry in unit.builds]
        env = dict(os.environ, **slot.env(), VPAIR_PREBUILT=str(ROOT / "build"), VPAIR_POOL_MODULE=unit.module,
                   VPAIR_POOL_BUILDS=" ".join(n for n in own if n in self.built),
                   VPAIR_POOL_UNBUILT=" ".join(n for n in own if n in self.unbuilt))
        unit.worker, unit.state = slot.k, "ran"
        timeout = self.timeout(unit)
        t0 = time.monotonic()
        with open(log, "wb") as output:
            process = subprocess.Popen([sys.executable, str(HERE / "pool.py"), "--unit", unit.name, "--events",
                                        str(events)], cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=output,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            with self.lock:
                self.running[process.pid] = unit
            try:
                process.wait(timeout=timeout)
                ended = None
            except subprocess.TimeoutExpired:
                ended = f"no result within its timeout of {timeout:.0f} s"
                stop_group(process)
            with self.lock:
                self.running.pop(process.pid, None)
        unit.seconds = time.monotonic() - t0
        # Whatever it left: its processes (its own session), its pair, a stand-in plugin host.
        kill_group(process.pid)
        slot.clean()
        if ended is None and self.stopping.is_set() and process.returncode < 0:
            ended = "interrupted"
        self.read_events(unit, events, process.returncode, ended)
        text = log.read_text(errors="replace")
        header = (f"===== {unit.name} (worker {slot.k}, {unit.seconds:.1f} s): {unit.result_text()} =====")
        self.out(f"{header}\n{text.rstrip()}\n===== end of {unit.name}" if text.strip() else header)

    @staticmethod
    def read_events(unit, path, status, ended):
        lines = []
        try:
            for line in path.read_text().splitlines():
                with contextlib.suppress(ValueError):
                    lines.append(json.loads(line))
        except OSError:
            pass
        end = next((e["end"] for e in lines if "end" in e), None)
        if end is not None and ended is None:
            unit.complete = True
            unit.run, unit.failures, unit.errors = end["run"], end["failures"], end["errors"]
            unit.skipped = [tuple(s) for s in end["skipped"]]
            unit.successful = bool(end["successful"]) and status == 0
            if not unit.successful and not (unit.failures or unit.errors):
                unit.complete, unit.why = False, f"its process exited with status {status}"
            return
        # It ended without its result: what it said before, and failed under its own name.
        unit.run = sum(1 for e in lines if "start" in e)
        unit.failures = [e["failure"] for e in lines if "failure" in e]
        unit.errors = [e["error"] for e in lines if "error" in e]
        unit.skipped = [(e["skip"], e["reason"]) for e in lines if "skip" in e]
        unit.why = ended or (f"its process was killed by signal {-status}" if status is not None and status < 0
                             else f"its process exited with status {status} without its result")

    def work(self, queue, slot):
        while not self.stopping.is_set():
            with self.lock:
                if not queue:
                    return
                unit = queue.pop(0)
            self.run_unit(unit, slot)

    def run(self, units, jobs):
        queue = list(units)
        threads = [threading.Thread(target=self.work, args=(queue, slot), daemon=True) for slot in self.slots[:jobs]]
        for thread in threads:
            thread.start()
        while any(thread.is_alive() for thread in threads):
            for thread in threads:
                thread.join(0.2)

    def stop(self):
        """Stops every running unit (its whole session) and takes no more."""
        self.stopping.set()
        with self.lock:
            running = list(self.running)
        for pid in running:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pid, signal.SIGTERM)


def stop_group(process):
    """SIGTERM to a unit's whole session, SIGKILL 5 s later."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        kill_group(process.pid)
        process.wait(timeout=10)


def kill_group(pgid):
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, signal.SIGKILL)


# ---------------------------------------------------------------------------------------------
# Durations

def durations_file():
    return main_checkout() / "build" / "vpair-pool-durations.json"


def load_durations():
    try:
        learned = json.loads(durations_file().read_text())
        return {k: float(v) for k, v in learned.items()} if isinstance(learned, dict) else {}
    except (OSError, ValueError):
        return {}


def save_durations(measured):
    """Merges this run's durations into the file (a newer one wins), atomically, one run at a time."""
    if not measured:
        return
    path = durations_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(".vpair-pool-durations.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        learned = load_durations()
        learned.update({k: round(v, 2) for k, v in measured.items()})
        tmp = path.with_name(f".{path.name}.{os.getpid()}")
        tmp.write_text(json.dumps(dict(sorted(learned.items())), indent=1) + "\n")
        os.replace(tmp, path)


# ---------------------------------------------------------------------------------------------
# The note and the summary

def note(units, extra_failed=()):
    """tools/test.sh's note, in its runner's format exactly."""
    run = sum(u.run for u in units)
    broken = [i for u in units for i in u.failures] + [i for u in units for i in u.errors]
    broken += [u.name for u in units if u.state != "ran" or not u.complete] + list(extra_failed)
    skipped = [s for u in units for s in u.skipped]
    parts = [f"{run} tests"]
    if broken:
        parts.append(f"{len(broken)} failed: " + ", ".join(broken))
    parts.append(f"{len(skipped)} skipped" + (": " if skipped else "")
                 + "; ".join(f"{test_id} ({one_line(reason)})" for test_id, reason in skipped))
    return ", ".join(parts)


def table(units):
    width = max([len(u.name) for u in units] + [4])
    rows = [f"{'unit':{width}}  worker  seconds  result"]
    for u in sorted(units, key=lambda u: -(u.seconds or 0)):
        worker = "" if u.worker is None else str(u.worker)
        seconds = "" if u.seconds is None else f"{u.seconds:.1f}"
        rows.append(f"{u.name:{width}}  {worker:>6}  {seconds:>7}  {u.result_text()}")
    return "\n".join(rows)


# ---------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="The virtual pair's modules on a pool of workers (README.md).")
    parser.add_argument("--jobs", type=int, required=True, help="workers, each on its own virtual pair (1 to 64)")
    parser.add_argument("--note", required=True, help="the file for tools/test.sh's note")
    parser.add_argument("--unit-timeout", type=float, default=None,
                        help="seconds per unit instead of 4 times its learned duration (at least 300 s; 900 s unknown)")
    parser.add_argument("modules", nargs="*", help="test_<name> (none: every module of tests/vpair)")
    args = parser.parse_args()
    if not 1 <= args.jobs <= 64:
        parser.error("--jobs: 1 to 64")
    os.chdir(ROOT)
    started = time.monotonic()
    printing = threading.RLock()        # the signal handler prints too, maybe while the main thread does

    def out(text):
        with printing:
            print(text, flush=True)

    def finish(units, extra_failed=(), status=None):
        text = note(units, extra_failed)
        Path(args.note).write_text(text)
        out(f"note: {text}")
        return status if status is not None else (0 if all(u.state == "ran" and u.successful for u in units)
                                                  and not extra_failed else 1)

    units, errors = find_units(args.modules)
    if errors:
        for error in errors:
            out(f"pool: {error}")
        return finish(units, extra_failed=[e.split(":")[0] for e in errors], status=1)
    if not units:
        out("pool: no test found")
        return finish(units, status=1)
    if LOGS.exists():
        for old in list(LOGS.glob("*.log")) + list(LOGS.glob("*.events")):
            old.unlink(missing_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)

    built, unbuilt, failed = prebuild(units, out)
    if failed:
        for unit in units:
            unit.state = "not run: the prebuild failed"
        out(f"pool: the prebuild failed: none of the {len(units)} unit(s) ran")
        return finish(units)

    learned = load_durations()
    known = {**SEED, **learned}
    pooled = sorted((u for u in units if not u.alone), key=lambda u: -known.get(u.name, UNKNOWN))
    alone_units = [u for u in units if u.alone]
    jobs = max(1, min(args.jobs, len(pooled))) if pooled else 1
    slots = take_slots(jobs)
    pool = Pool(slots, out, built, unbuilt, known, args.unit_timeout)
    for slot in slots:
        for what in slot.clean():
            out(f"pool: worker {slot.k}: left by an earlier run: {what}")
    watch = Watch()
    watch.start()

    caught = []

    def on_signal(signum, _frame):
        if not caught:
            out(f"pool: {signal.Signals(signum).name}: stopping the workers")
        caught.append(signum)
        pool.stop()

    previous = {s: signal.signal(s, on_signal) for s in (signal.SIGINT, signal.SIGTERM)}
    out(f"pool: {len(pooled)} unit(s) on {jobs} worker(s)" + (f", then {len(alone_units)} alone: "
                                                              + ", ".join(u.name for u in alone_units) if alone_units else ""))
    t_pool = time.monotonic()
    pool.run(pooled, jobs)
    pool_seconds = time.monotonic() - t_pool
    t_alone = time.monotonic()
    if not caught:
        pool.run(alone_units, 1)
    alone_seconds = time.monotonic() - t_alone
    for unit in units:
        if unit.state is None:
            unit.state = "not run: interrupted"
    for slot in slots:
        slot.clean()
    watch.done.set()

    save_durations({u.name: u.seconds for u in units if u.state == "ran" and u.complete})
    failed_units = [u.name for u in units if not (u.state == "ran" and u.successful)]
    summary = (f"{table(units)}\n"
               f"pool: {len(units)} unit(s), {jobs} worker(s), {time.monotonic() - started:.1f} s "
               f"(the pool {pool_seconds:.1f} s" + (f", alone {alone_seconds:.1f} s" if alone_units else "") + ")\n"
               + (f"pool: failed: {', '.join(failed_units)}\n" if failed_units else "")
               + watch.summary())
    out(summary)
    (LOGS / "summary.txt").write_text(summary + "\n")
    status = finish(units)
    if caught:
        for s, handler in previous.items():
            signal.signal(s, signal.SIG_DFL)
        os.kill(os.getpid(), caught[0])
    return status


if __name__ == "__main__":
    sys.exit(main())
