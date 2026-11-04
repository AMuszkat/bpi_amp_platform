#!/usr/bin/env python3
"""The virtual pair's modules on a pool of workers, each worker on a virtual pair of its own
(README.md, "The pool").

    python3 tests/vpair/pool.py --jobs N [--quiet-jobs M] [--gate FILE] [--after-gate NAME]...
                                --note FILE [MODULE...]
    python3 tests/vpair/pool.py --jobs N --prebuild-only [--note FILE] [MODULE...]
    python3 tests/vpair/pool.py --hold-checkout [--what TEXT] -- COMMAND...

From the repository root, in the development container (tools/test.sh calls it there; from
outside, it moves there itself, as the harness does). A MODULE is test_<name> (test_<name>.<Class>
names one unit); none is every module of tests/vpair, as `unittest discover` finds them.

1. The prebuild. Every module says what its built() calls need, also through the harness's
   classes, in POOL_BUILDS ("speakerd", "loudness:headless"; "controller?" for one a test skips
   without). The pool builds all of them first, once, with tools/build.sh: one call per build tree
   with every kind it needs, the trees at once. A build's output is kept and printed only if it
   fails, which fails the run before any test (one marked "?" fails only the built() that asks for
   it, as it would have); tools/build.sh's line that it waits for another build in its tree
   ("tools/build.sh: waiting for build/speakerd: ...") is printed as it comes. A SIGINT during the
   prebuild kills its builds and reaps them before it ends the pool (no unit runs, no note; SIGTERM
   there ends it at once, its builds left to end on their own). --prebuild-only does that alone
   and exits, 0 or 1, no unit run; its note (with --note) is written only when the prebuild fails,
   the note a pool whose prebuild failed writes (tools/test.sh's schedule runs the prebuild among
   the builds, and the units later).
   Under the pool built() builds nothing (VPAIR_PREBUILT) and refuses what the module's POOL_BUILDS
   does not name: build/ may hold a stale one.
2. The units: each test class the unittest loader finds in a module (a module with POOL_WHOLE =
   True is one unit; one with POOL_SPLIT = True, whose tests are independent, a unit per test),
   each in a process of its own (setUpModule and setUpClass run once per unit), in three phases:
   - main: the units that are neither quiet nor alone, on N workers;
   - quiet: the units whose pass criterion is a time, a rate, a CPU share or an xrun count, which
     the module marks (POOL_QUIET = True: all of its units; POOL_QUIET = ("Class", "Class.test_x"):
     those, names relative to the module, a unit being quiet when its name or its class is listed),
     and those --after-gate names, on M workers (--quiet-jobs, default min(N, 12)), after the main
     phase and, with --gate, once FILE exists;
   - alone: a module with POOL_ALONE = True (it measures CPU: test_chain_cost) runs last, alone,
     after the gate too.
   In each phase the longest first: durations learned from earlier runs
   (build/vpair-pool-durations.json of the main checkout, merged and written atomically after each
   run), seeded (SEED) for units never timed; a unit known to neither counts as long. Each worker
   has its own VPAIR_DIR (/tmp/vpool<k>, short for PipeWire's socket path), VPAIR_ADDRESS_BASE
   (127.77.<k>), VPAIR_PLUGIN_HOST (vpairPlugHost<k>), X display (VPAIR_DISPLAY and DISPLAY,
   :<100 + k>) and VPAIR_PREBUILT (<root>/build); a pair left in its VPAIR_DIR by an interrupted run
   is brought down before it starts. (`pool.py --unit NAME --events FILE` is how the pool runs one
   unit, in its own process.)
3. The gate (--gate FILE): the quiet and alone phases wait until FILE exists, saying so once and
   then every 60 s, so that what times itself never runs beside the other tests of a schedule
   (tools/schedule.py creates FILE when they have ended). A line "leave-out NAME: why" in it
   leaves out the units of NAME (a module, a class or a test, as named): not run, not in the note
   (tools/test.sh's controller check: test_controller_end_to_end after a failed build or own test
   of the controller, which --after-gate holds for the gate).
4. Each unit's whole output, stdout and stderr, is printed as one block when it ends and kept in
   build/vpair-pool/<unit>.log; then a table (unit, phase, worker, seconds, result), the failed
   units and the container's peak use (tasks against pids.max, memory, /dev/shm).
5. FILE gets tools/test.sh's note: "N tests[, K failed: id, id], M skipped[: id (reason); ...]",
   ids as unittest's test.id(). A unit whose process ends without its result (it died, or ran past
   its timeout: 4 times its learned duration, at least 300 s; 900 s unknown) is failed under its
   unit name, with what it said before; so is one the pool could not run, clean up after or read
   the log of, with what went wrong, its block printed all the same.
6. Exit status 0 when every test passed (skips allowed), 1 otherwise. SIGINT or SIGTERM stops the
   workers, brings their pairs down, writes the note so far, and ends the pool by that signal.

One pool at a time per checkout: build/vpair-pool/ and the prebuild's build trees are this
checkout's, so a second pool waits for the first (build/vpair-pool.lock), saying which one it is.
tools/test.sh's serial runner (--jobs 1) holds the same lock through --hold-checkout, which takes
it (waiting the same way), then becomes COMMAND (exec, the lock's descriptor inherited and named in
VPAIR_CHECKOUT_LOCK_FD, which the runner makes non-inheritable again), so that a serial run and a
pool of one checkout never run at once either.
tests/vpair/pool_selftest.py tests all this in seconds, on synthetic modules: VPAIR_POOL_TESTS is
the folder of the modules (default tests/vpair) and VPAIR_POOL_FILES the folder of the logs, the
lock and the learned durations (default this checkout's build/, the main checkout's for the
durations).
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
import traceback
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
VPAIR = HERE / "vpair.sh"
# The modules and the pool's own files: tests/vpair and this checkout's build/, or what
# pool_selftest.py gives (README.md, "The pool").
TESTS = Path(os.environ.get("VPAIR_POOL_TESTS") or HERE).resolve()
FILES = Path(os.environ["VPAIR_POOL_FILES"]).resolve() if os.environ.get("VPAIR_POOL_FILES") else None
LOGS = (FILES or ROOT / "build") / "vpair-pool"
LOCK = (FILES or ROOT / "build") / "vpair-pool.lock"

# Seconds per unit, for units the pool never timed: measured by the pool on the machine it was written
# on (24 threads, 12 workers; the first runs were seeded with the investigation's timings of each class).
SEED = {
    "test_monitor_fanout.MonitorFanout": 32.5, "test_controller_end_to_end.ControllerEndToEnd": 34.3,
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


def describe(error):
    return f"{type(error).__name__}: {one_line(error)}"


# ---------------------------------------------------------------------------------------------
# A unit, in its own process: python3 tests/vpair/pool.py --unit NAME --events FILE

def run_child(name, events_path):
    """Runs one unit as tools/test.sh's runner runs a module, and writes what happens to
    `events_path` as it happens, one JSON line each, so that the pool knows what a unit that dies
    did before; the last line is the result."""
    sys.path.insert(0, str(HERE))
    if TESTS != HERE:
        sys.path.insert(0, str(TESTS))
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
import harness  # noqa: E402  (its import is the move into the container)
if TESTS != HERE:
    sys.path.insert(0, str(TESTS))


# ---------------------------------------------------------------------------------------------
# The units

class Unit:
    def __init__(self, name, module, count, builds, alone, quiet=False):
        self.name, self.module, self.count, self.builds, self.alone = name, module, count, builds, alone
        self.quiet = quiet
        self.worker = self.seconds = self.state = None
        self.left_out = None                     # why the gate left it out (then not in the note)
        self.run, self.failures, self.errors, self.skipped = 0, [], [], []
        self.successful, self.complete, self.why = False, False, ""

    @property
    def phase(self):
        return "alone" if self.alone else "quiet" if self.quiet else "main"

    def result_text(self):
        if self.left_out is not None:
            return f"left out: {self.left_out}"
        if self.state != "ran":
            return self.state
        tests = f"{self.run} test{'s' if self.run != 1 else ''}"
        if self.successful:
            return f"passed ({tests}" + (f", {len(self.skipped)} skipped)" if self.skipped else ")")
        if not self.complete:
            return f"FAILED: {self.why or 'no result'}"
        if not self.run:
            return "FAILED before any test ran"
        return f"FAILED ({len(self.failures) + len(self.errors)} of {tests})"


def module_names(names):
    """The modules named, or those `unittest discover` finds in tests/vpair (test*.py, a valid module
    name, in its order)."""
    if names:
        return list(names)
    return sorted(p.stem for p in TESTS.iterdir()
                  if p.is_file() and re.fullmatch(r"test\w*\.py", p.name, re.IGNORECASE))


def find_units(names, after_gate=()):
    """The units of the modules named, in the order the unittest loader runs them, and errors (what
    could not be read). A unit is quiet when its module's POOL_QUIET says so, or when `after_gate`
    names it (its module, class or test)."""
    loader = unittest.TestLoader()
    units, errors = [], []
    for name in module_names(names):
        module_name = name.split(".")[0]
        try:
            count = loader.loadTestsFromName(name).countTestCases()
        except Exception:                        # noqa: BLE001  (the unit's own run says what)
            count = 1
        module = sys.modules.get(module_name)
        marker = quiet_marker(module, errors)
        if "." in name:                          # test_x.Class: that unit alone
            units.append(Unit(name, module_name, count, declared(module, errors), alone(module),
                              is_quiet(marker, name.partition(".")[2])))
            continue
        if module is None or getattr(module, "POOL_WHOLE", False) or hasattr(module, "load_tests"):
            # Not importable (its unit's run reproduces the error), or to stay whole.
            if marker not in (True, False):
                errors.append(f"{module_name}.POOL_QUIET: {marker!r} names units of a module that runs as one unit "
                              f"(POOL_WHOLE, load_tests): POOL_QUIET = True or nothing")
            units.append(Unit(name, name, count, declared(module, errors), alone(module), marker is True))
            continue
        builds, lonely, split = declared(module, errors), alone(module), getattr(module, "POOL_SPLIT", False)
        found = []
        for attr in dir(module):                 # as loadTestsFromModule finds the classes
            obj = getattr(module, attr)
            if isinstance(obj, type) and issubclass(obj, unittest.TestCase):
                tests = list(loader.loadTestsFromTestCase(obj))
                if split:                        # its tests are independent: one unit each
                    found += [Unit(f"{name}.{attr}.{t._testMethodName}", name, 1, builds, lonely,
                                   is_quiet(marker, f"{attr}.{t._testMethodName}")) for t in tests]
                elif tests:                      # a base class without tests yields nothing
                    found.append(Unit(f"{name}.{attr}", name, len(tests), builds, lonely, is_quiet(marker, attr)))
        check_quiet(name, marker, found, errors)
        units += found
    for unit in units:
        if any(names_unit(held, unit) for held in after_gate):
            unit.quiet = True
    return units, errors


def names_unit(name, unit):
    """Whether `name` (a module, a class or a test, as test_x[.Class[.test_y]]) is or holds `unit`:
    the unit is it, or within it (its class of a unit per test included)."""
    return unit.name == name or unit.name.startswith(name + ".")


def quiet_marker(module, errors):
    """A module's POOL_QUIET, checked: True, False, or a tuple of names relative to the module
    ("Class", "Class.test_x")."""
    value = getattr(module, "POOL_QUIET", False) if module is not None else False
    if isinstance(value, bool):
        return value
    if isinstance(value, (tuple, list)) and value and all(
            isinstance(v, str) and re.fullmatch(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)?", v) for v in value):
        return tuple(value)
    errors.append(f"{module.__name__}.POOL_QUIET: {value!r} is not True or a tuple of names relative to the module "
                  f"(\"Class\", \"Class.test_x\")")
    return False


def is_quiet(marker, relative):
    """Whether the unit `relative` to its module ("Class" or "Class.test_x") is quiet: its name or its
    class is listed (or the whole module is)."""
    if marker is True:
        return True
    if not marker:
        return False
    return relative in marker or relative.split(".")[0] in marker


def check_quiet(module_name, marker, units, errors):
    """Every name a POOL_QUIET tuple lists must be one of the module's units or the class of some: a
    test of a class that runs as one unit would otherwise quietly stay in the main phase."""
    if marker in (True, False):
        return
    relative = {u.name[len(module_name) + 1:] for u in units}
    classes = {r.split(".")[0] for r in relative}
    for entry in marker:
        if entry in relative or entry in classes:
            continue
        if entry.split(".")[0] in classes:
            errors.append(f"{module_name}.POOL_QUIET: {entry!r} is a test of a class that runs as one unit "
                          f"(no POOL_SPLIT): list the class, {entry.split('.')[0]!r}")
        else:
            errors.append(f"{module_name}.POOL_QUIET: {entry!r} names no test class or test of the module")


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

def build_command(what, kinds):
    """The prebuild's call of tools/build.sh for one build tree (pool_selftest.py puts a stand-in
    in its place)."""
    return [str(ROOT / "tools" / "build.sh"), what, *kinds]


def read_build(process, lines, ended, out):
    """A build's output (stdout and stderr), kept in `lines` to be shown if it fails; its waiting
    line (it begins with harness.BUILD_WAITING) also goes to the pool's output at once: the wait may
    last as long as the other build. Then its end, in `ended` (its own: the builds are reported in
    their order, each after the one before). A fault while it reads (a print to a closed stdout)
    kills the build, as subprocess.run does, rather than leave it running with its tree's lock."""
    try:
        with process.stdout:
            for line in process.stdout:
                lines.append(line)
                if line.startswith(harness.BUILD_WAITING):
                    out(line.rstrip("\n"))
    except BaseException:
        process.kill()
        raise
    finally:
        process.wait()
        ended.append(time.monotonic())


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
    try:
        for what, tree in trees.items():
            process = subprocess.Popen(build_command(what, tree["kinds"]), cwd=ROOT, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
            lines, ended = [], []
            reader = threading.Thread(target=read_build, args=(process, lines, ended, out), daemon=True)
            running.append((what, tree, process, reader, lines, ended, time.monotonic()))
            reader.start()
        built, unbuilt, failed = set(), set(), False
        for what, tree, process, reader, lines, ended, t0 in running:
            reader.join()
            output = "".join(lines)
            shown = " ".join(["tools/build.sh", what, *tree["kinds"]])
            took = ended[0] - t0
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
    except BaseException:
        # A KeyboardInterrupt to the pool alone (in a join), a print to a closed stdout: the builds
        # still running are killed and reaped, as subprocess.run does, rather than left running with
        # their trees' locks.
        for _, _, process, *_ in running:
            process.kill()
        for _, _, process, *_ in running:
            process.wait()
        raise
    out(f"prebuild: {len(trees)} build tree(s) in {time.monotonic() - started:.1f} s")
    return built, unbuilt, failed


# ---------------------------------------------------------------------------------------------
# The workers

class Slot:
    """A worker's place: /tmp/vpool<k> for its pair, its addresses and its stand-in plugin host's
    name, held with a lock (/tmp/vpool<k>.lock: another pool in the same /tmp takes other slots,
    pool_selftest.py's beside a real one, or native pools of two checkouts)."""

    def __init__(self, k, lock):
        self.k, self.lock = k, lock
        self.dir = f"/tmp/vpool{k}"
        self.base = f"127.77.{k}"
        self.host = f"vpairPlugHost{k}"
        self.display = str(100 + k)

    def env(self):
        # The worker's X display (:100 to :199): where its harness.VirtualDisplay runs, and the one
        # the controller opens (a JUCE program without DISPLAY opens :0.0, and dies when that server
        # stops; speakerd opens none since 0.3.3, nor do its plugins). No Xvfb of the checks is :0:
        # VirtualDisplay without VPAIR_DISPLAY (the serial runner) takes the first free one of :100
        # to :199, plugin-check's :200 to :299, the controller's own tests' :300 up and its
        # screenshot run's (tests/run.sh --screenshot) a fixed :96.
        return {"VPAIR_DIR": self.dir, "VPAIR_ADDRESS_BASE": self.base, "VPAIR_PLUGIN_HOST": self.host,
                "VPAIR_DISPLAY": self.display, "DISPLAY": f":{self.display}"}

    def clean(self):
        """Brings down a pair left in its VPAIR_DIR (an interrupted run, a killed unit) and stops a
        stand-in plugin host left under its name; says what it found. Only in this pool's PID
        namespace: outside the container /proc shows every container's processes too, and another
        checkout's pool has a slot of the same number there. Raises what goes wrong (vpair.sh down
        not done within 60 s), for the unit it cleans up after."""
        found = []
        try:
            own = os.readlink("/proc/self/ns/pid")
        except OSError:
            own = None
        for pid in os.listdir("/proc"):
            if pid.isdigit():
                try:
                    if Path(f"/proc/{pid}/comm").read_text().strip() != self.host:
                        continue
                    if own is not None and os.readlink(f"/proc/{pid}/ns/pid") != own:
                        continue                 # a container's, seen from outside it
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


SLOTS = 100                                  # vpairPlugHost<k> is 15 characters at most: k <= 99


def take_slots(n):
    """n free slots, or as many as there are."""
    slots = []
    for k in range(SLOTS):
        lock = open(f"/tmp/vpool{k}.lock", "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock.close()
            continue
        slots.append(Slot(k, lock))
        if len(slots) == n:
            break
    return slots


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
        """Runs one unit in its own session on `slot`, and prints its whole output when it ends.
        What goes wrong on the pool's side (the unit not started, its session not stopped, its
        pair not brought down, its log unreadable) is that unit's failure, with what went wrong."""
        log, events = LOGS / f"{unit.name}.log", LOGS / f"{unit.name}.events"
        unit.worker, unit.state = slot.k, "ran"
        trouble = []                            # the pool's side, in the unit's result
        process, ended, timeout = None, None, self.timeout(unit)
        t0 = time.monotonic()
        try:
            events.unlink(missing_ok=True)
            own = [entry.rstrip("?") for entry in unit.builds]
            env = dict(os.environ, **slot.env(), VPAIR_PREBUILT=str(ROOT / "build"), VPAIR_POOL_MODULE=unit.module,
                       VPAIR_POOL_BUILDS=" ".join(n for n in own if n in self.built),
                       VPAIR_POOL_UNBUILT=" ".join(n for n in own if n in self.unbuilt))
            with open(log, "wb") as output:
                process = subprocess.Popen([sys.executable, str(HERE / "pool.py"), "--unit", unit.name, "--events",
                                            str(events)], cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=output,
                                           stderr=subprocess.STDOUT, start_new_session=True)
                with self.lock:
                    self.running[process.pid] = unit
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    ended = f"no result within its timeout of {timeout:.0f} s"
                    stop_group(process)
        except Exception as error:              # noqa: BLE001  (the unit's failure, said)
            trouble.append(f"the pool could not run it to its end: {describe(error)}")
        finally:
            unit.seconds = time.monotonic() - t0
            if process is not None:
                with self.lock:
                    self.running.pop(process.pid, None)
                # Whatever it left: its processes (its own session), then below its pair and a
                # stand-in plugin host.
                kill_group(process.pid)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=10)
        try:
            slot.clean()
        except Exception as error:              # noqa: BLE001
            trouble.append(f"its worker's pair could not be brought down after it: {describe(error)}")
        if process is not None:                 # else it never started: the trouble says why
            status = process.returncode
            if ended is None and self.stopping.is_set() and status is not None and status < 0:
                ended = "interrupted"
            self.read_events(unit, events, status, ended)
        try:
            text = log.read_text(errors="replace")
        except OSError as error:
            text = ""
            trouble.append(f"its log could not be read: {describe(error)}")
        if trouble:
            unit.successful = unit.complete = False
            unit.why = "; ".join(([unit.why] if unit.why else []) + trouble)
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
        lines = [e for e in lines if isinstance(e, dict)]
        end = next((e["end"] for e in lines if "end" in e), None)
        if end is not None and ended is None:
            try:
                run, failures, errors = int(end["run"]), list(end["failures"]), list(end["errors"])
                skipped = [(str(s[0]), str(s[1])) for s in end["skipped"]]
                successful = bool(end["successful"])
            except (KeyError, TypeError, ValueError, IndexError):
                end = None                      # not the pool's own: as if there were none
            else:
                unit.complete = True
                unit.run, unit.failures, unit.errors, unit.skipped = run, failures, errors, skipped
                unit.successful = successful and status == 0
                if not unit.successful and not (unit.failures or unit.errors):
                    unit.complete, unit.why = False, f"its process exited with status {status}"
                return
        # It ended without its result: what it said before, and failed under its own name.
        unit.run = sum(1 for e in lines if "start" in e)
        unit.failures = [str(e["failure"]) for e in lines if "failure" in e]
        unit.errors = [str(e["error"]) for e in lines if "error" in e]
        unit.skipped = [(str(e["skip"]), str(e.get("reason", ""))) for e in lines if "skip" in e]
        unit.why = ended or (f"its process was killed by signal {-status}" if status is not None and status < 0
                             else f"its process exited with status {status} without its result")

    def work(self, queue, slot):
        while not self.stopping.is_set():
            with self.lock:
                if not queue:
                    return
                unit = queue.pop(0)
            try:
                self.run_unit(unit, slot)
            except Exception as error:          # noqa: BLE001  (a fault of the pool's: the unit's failure, said)
                unit.state, unit.complete, unit.successful = "ran", False, False
                unit.worker = slot.k if unit.worker is None else unit.worker
                unit.why = f"the pool failed while handling it: {describe(error)}"
                self.out(f"===== {unit.name} (worker {slot.k}): {unit.result_text()} =====\n"
                         f"{traceback.format_exc().rstrip()}\n===== end of {unit.name}")

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
    return (FILES or main_checkout() / "build") / "vpair-pool-durations.json"


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
# One pool at a time per checkout

def hold_checkout(out, interrupted, who, me="pool"):
    """Takes the checkout's lock (build/vpair-pool.lock) for the whole run: the logs in
    build/vpair-pool/, which a pool clears as it starts, and the build trees its prebuild runs ninja
    in are this checkout's (a worktree has its own build/, so worktrees' pools run at once), and the
    serial runner's pair would share the machine with a pool's. Waits while another pool or serial
    runner holds it, naming that one; returns the open lock, held until this process exits (no unit
    or build inherits it), or None when `interrupted()` while waiting. `me` begins what it says."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    lock = os.fdopen(os.open(LOCK, os.O_RDWR | os.O_CREAT, 0o644), "r+")   # not truncated: it names the holder
    waited = said = None
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            pass
        now = time.monotonic()
        if waited is None or now - said >= 60:
            try:
                holder = one_line(LOCK.read_text()) or "a run that has not said which yet"
            except OSError:
                holder = "a run"
            if waited is None:
                waited = now
                out(f"{me}: another run holds this checkout: {holder}; waiting for it to end (one pool or serial "
                    f"runner at a time per checkout, {LOCK}: the logs and the prebuild's build trees are the "
                    f"checkout's)")
            else:
                out(f"{me}: still waiting for {holder} ({now - waited:.0f} s)")
            said = now
        if interrupted():
            lock.close()
            return None
        time.sleep(0.2)
    if waited is not None:
        out(f"{me}: the other run ended; this one starts after waiting {time.monotonic() - waited:.1f} s")
    lock.seek(0)
    lock.truncate()
    lock.write(who + "\n")
    lock.flush()
    return lock


def holder_text(jobs, modules, prebuild_only=False):
    """What a waiting pool says of this one."""
    what = " ".join(modules) if modules else "every module"
    if len(what) > 160:
        what = what[:157] + "..."
    return (f"pool.py pid {os.getpid()} ({where()}), since {time.strftime('%H:%M:%S %Z').strip()}, --jobs {jobs}"
            + (" --prebuild-only" if prebuild_only else "") + f" {what}")


def where():
    return "in the container" if os.environ.get("BPI_IN_CONTAINER") else "outside the container"


def wait_for_checkout(out, who, me="pool"):
    """hold_checkout(), SIGINT and SIGTERM while it waits ending the wait: (the lock or None, the
    signal that came or None). The handlers before are put back."""
    waiting_signal = []
    previous = {s: signal.signal(s, lambda signum, _frame: waiting_signal.append(signum))
                for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        checkout = hold_checkout(out, lambda: bool(waiting_signal), who, me)
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler)
    return checkout, (waiting_signal[0] if waiting_signal else None)


def die_by(signum):
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


def hold_and_exec(argv):
    """--hold-checkout [--what TEXT] -- COMMAND...: the serial runner's side of the checkout's lock.
    Takes it as a pool does (waiting for a pool or another serial runner, naming it), then execs
    COMMAND with the lock's descriptor inherited, its number in VPAIR_CHECKOUT_LOCK_FD: COMMAND holds
    it until it exits (tools/test.sh's runner makes it non-inheritable again, so that nothing it
    starts keeps it). SIGINT or SIGTERM while it waits ends it by that signal, COMMAND never run."""
    parser = argparse.ArgumentParser(prog="pool.py --hold-checkout",
                                     description="Holds the checkout's lock for COMMAND (tools/test.sh's serial runner).")
    parser.add_argument("--what", default="", help="what COMMAND runs, for a waiting pool to say")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="-- COMMAND...")
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("no COMMAND after --")

    def out(text):
        print(text, flush=True)

    what = one_line(args.what) or "every module"
    if len(what) > 160:
        what = what[:157] + "..."
    who = (f"the serial runner, pid {os.getpid()} ({where()}), since {time.strftime('%H:%M:%S %Z').strip()}, "
           f"--jobs 1 {what}")
    checkout, signum = wait_for_checkout(out, who, me="serial runner")
    if checkout is None or signum is not None:
        out(f"serial runner: {signal.Signals(signum).name} while waiting: nothing ran")
        die_by(signum)
        return 1
    os.set_inheritable(checkout.fileno(), True)
    env = dict(os.environ, VPAIR_CHECKOUT_LOCK_FD=str(checkout.fileno()))
    try:
        os.execvpe(command[0], command, env)
    except OSError as error:
        out(f"serial runner: {command[0]} could not be run: {describe(error)}")
        return 127


# ---------------------------------------------------------------------------------------------
# The note and the summary

def note(units, extra_failed=()):
    """tools/test.sh's note, in its runner's format exactly. The units the gate left out are not in it,
    as if never asked for."""
    units = [u for u in units if u.left_out is None]
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
    rows = [f"{'unit':{width}}  phase  worker  seconds  result"]
    order = {"main": 0, "quiet": 1, "alone": 2}
    for u in sorted(units, key=lambda u: (order[u.phase], -(u.seconds or 0))):
        worker = "" if u.worker is None else str(u.worker)
        seconds = "" if u.seconds is None else f"{u.seconds:.1f}"
        rows.append(f"{u.name:{width}}  {u.phase:5}  {worker:>6}  {seconds:>7}  {u.result_text()}")
    return "\n".join(rows)


def read_gate(path):
    """The gate's lines "leave-out NAME: why", as [(NAME, why)]; anything else in it is no line of
    the gate's, returned as (None, the line) to be said."""
    found = []
    for line in path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        m = re.fullmatch(r"leave-out (test\w*(?:\.\w+){0,2}): (.*)", line.strip())
        found.append((m.group(1), m.group(2)) if m else (None, line.strip()))
    return found


def wait_for_gate(gate, out, stopped, what):
    """Until `gate` exists, saying so once and then every 60 s; False if `stopped()` first."""
    if gate.exists():
        out(f"pool: the gate is open ({gate}): {what} start now")
        return True
    waited = said = time.monotonic()
    out(f"pool: {what} wait for the gate, {gate} (tools/schedule.py creates it once the run's other tests have "
        f"ended)")
    while not gate.exists():
        if stopped():
            return False
        now = time.monotonic()
        if now - said >= 60:
            out(f"pool: still waiting for the gate, {gate} ({now - waited:.0f} s)")
            said = now
        time.sleep(0.2)
    out(f"pool: the gate opened after {time.monotonic() - waited:.1f} s")
    return True


# ---------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="The virtual pair's modules on a pool of workers (README.md).")
    parser.add_argument("--jobs", type=int, required=True,
                        help="workers of the main phase, each on its own virtual pair (1 to 64)")
    parser.add_argument("--quiet-jobs", type=int, default=None,
                        help="workers of the quiet phase (1 to 64; default min(--jobs, 12))")
    parser.add_argument("--gate", default=None,
                        help="the quiet and alone phases wait until this file exists (tools/schedule.py's)")
    parser.add_argument("--after-gate", action="append", default=[], metavar="NAME",
                        help="the units of NAME (a module, class or test) wait for the gate, as quiet ones do")
    parser.add_argument("--prebuild-only", action="store_true", help="build what the units need, and exit: no unit")
    parser.add_argument("--note", default=None, help="the file for tools/test.sh's note")
    parser.add_argument("--unit-timeout", type=float, default=None,
                        help="seconds per unit instead of 4 times its learned duration (at least 300 s; 900 s unknown)")
    parser.add_argument("modules", nargs="*", help="test_<name> (none: every module of tests/vpair)")
    args = parser.parse_args()
    # 64 at most. Nothing of a worker's own runs out there: the slots' names allow 100 in one /tmp
    # (SLOTS), and 64 leaves the rest to another pool there (pool_selftest.py's beside a real one);
    # the address blocks (127.77.<k>) allow 256, the displays (:<100 + k>) 100, :100 to :199
    # (plugin-check's Xvfb takes :200 to :299, the controller's own tests' :300 up). tools/test.sh's default stops at 16, where the pool stops
    # gaining (measured: 20 and 24 workers were no faster, and made timing tests fail), far below
    # what the container's tasks allow (README.md, "The pool").
    if not 1 <= args.jobs <= 64:
        parser.error("--jobs: 1 to 64")
    quiet_jobs = min(args.jobs, 12) if args.quiet_jobs is None else args.quiet_jobs
    if not 1 <= quiet_jobs <= 64:
        parser.error("--quiet-jobs: 1 to 64")
    if args.note is None and not args.prebuild_only:
        parser.error("--note FILE is needed (but with --prebuild-only)")
    os.chdir(ROOT)
    started = time.monotonic()
    printing = threading.RLock()        # the signal handler prints too, maybe while the main thread does

    def out(text):
        with printing:
            print(text, flush=True)

    def finish(units, extra_failed=(), status=None):
        text = note(units, extra_failed)
        if args.note is not None:
            Path(args.note).write_text(text)
        out(f"note: {text}")
        return status if status is not None else (0 if all(u.state == "ran" and u.successful for u in units
                                                           if u.left_out is None) and not extra_failed else 1)

    units, errors = find_units(args.modules, args.after_gate)
    if errors:
        for error in errors:
            out(f"pool: {error}")
        return finish(units, extra_failed=[e.split(":")[0] for e in errors], status=1)
    if not units:
        out("pool: no test found")
        return finish(units, status=1)

    # One pool at a time per checkout; SIGINT or SIGTERM while waiting ends this one, no unit run.
    checkout, signum = wait_for_checkout(out, holder_text(args.jobs, args.modules, args.prebuild_only))
    if checkout is None or signum is not None:
        for unit in units:
            unit.state = "not run: interrupted while waiting for the other pool"
        out(f"pool: {signal.Signals(signum).name} while waiting: none of the {len(units)} unit(s) ran")
        finish(units)
        die_by(signum)
        return 1

    if args.prebuild_only:
        # The logs of the last pool are kept: no unit runs here.
        built, unbuilt, failed = prebuild(units, out)
        if failed:
            for unit in units:
                unit.state = "not run: the prebuild failed"
            out(f"pool: --prebuild-only: the prebuild failed: none of the {len(units)} unit(s) can run")
            return finish(units)
        out(f"pool: --prebuild-only: built what the {len(units)} unit(s) need, no unit run")
        return 0

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

    def longest_first(phase):
        return sorted((u for u in units if u.phase == phase), key=lambda u: -known.get(u.name, UNKNOWN))

    main_units, quiet_units, alone_units = longest_first("main"), longest_first("quiet"), \
        [u for u in units if u.phase == "alone"]
    jobs = max(1, min(args.jobs, len(main_units)))
    quiet_width = max(1, min(quiet_jobs, len(quiet_units)))
    needed = max(jobs if main_units else 1, quiet_width if quiet_units else 1)
    slots = take_slots(needed)
    if len(slots) < needed:
        for unit in units:
            unit.state = "not run: no free worker slot"
        out(f"pool: only {len(slots)} of the {SLOTS} worker slots are free (/tmp/vpool<k>.lock), {needed} needed: "
            f"other pools run in this /tmp")
        return finish(units)
    pool = Pool(slots, out, built, unbuilt, known, args.unit_timeout)
    for slot in slots:
        try:
            for what in slot.clean():
                out(f"pool: worker {slot.k}: left by an earlier run: {what}")
        except Exception as error:              # noqa: BLE001  (its units find out; said here)
            out(f"pool: worker {slot.k}: what an earlier run left could not be cleaned up: {describe(error)}")
    watch = Watch()
    watch.start()

    caught = []

    def on_signal(signum, _frame):
        if not caught:
            out(f"pool: {signal.Signals(signum).name}: stopping the workers")
        caught.append(signum)
        pool.stop()

    previous = {s: signal.signal(s, on_signal) for s in (signal.SIGINT, signal.SIGTERM)}
    gate = Path(args.gate) if args.gate else None
    after = (f" after the gate" if gate else "")
    out(f"pool: {len(main_units)} unit(s) on {jobs} worker(s)"
        + (f", then {len(quiet_units)} quiet on {quiet_width} worker(s){after}" if quiet_units else "")
        + (f", then {len(alone_units)} alone{after if not quiet_units else ''}: "
           + ", ".join(u.name for u in alone_units) if alone_units else ""))
    seconds = {}
    t = time.monotonic()
    pool.run(main_units, jobs)
    seconds["main"] = time.monotonic() - t
    if gate and (quiet_units or alone_units) and not caught:
        t = time.monotonic()
        held = (f"the quiet units ({len(quiet_units)})" if quiet_units else "") \
            + (" and " if quiet_units and alone_units else "") \
            + (f"the units alone ({len(alone_units)})" if alone_units else "")
        if wait_for_gate(gate, out, lambda: bool(caught), held):
            try:
                lines = read_gate(gate)
            except OSError as error:
                lines = []
                out(f"pool: the gate {gate} could not be read: {describe(error)}; nothing left out")
            for name, why in lines:
                if name is None:
                    out(f"pool: the gate {gate} has a line that is not \"leave-out NAME: why\": {why!r}")
                    continue
                held_units = [u for u in quiet_units + alone_units if names_unit(name, u)]
                for unit in held_units:
                    unit.left_out = why
                    out(f"pool: {unit.name} left out by the gate: {why}")
                if not held_units:
                    out(f"pool: the gate leaves out {name}, which no unit waiting for it is ({why})")
            quiet_units = [u for u in quiet_units if u.left_out is None]
            alone_units = [u for u in alone_units if u.left_out is None]
        seconds["gate"] = time.monotonic() - t
    if quiet_units and not caught:
        t = time.monotonic()
        pool.run(quiet_units, quiet_width)
        seconds["quiet"] = time.monotonic() - t
    if alone_units and not caught:
        t = time.monotonic()
        pool.run(alone_units, 1)
        seconds["alone"] = time.monotonic() - t
    for unit in units:
        if unit.state is None and unit.left_out is None:
            unit.state = "not run: interrupted"
    for slot in slots:
        try:
            slot.clean()
        except Exception as error:              # noqa: BLE001
            out(f"pool: worker {slot.k}: its pair could not be brought down at the end: {describe(error)}")
    watch.done.set()

    try:
        save_durations({u.name: u.seconds for u in units if u.state == "ran" and u.complete})
    except OSError as error:
        out(f"pool: the durations could not be saved in {durations_file()}: {describe(error)}")
    failed_units = [u.name for u in units if u.left_out is None and not (u.state == "ran" and u.successful)]
    widths = f"{jobs} worker(s)" + (f", {quiet_width} quiet" if any(u.phase == "quiet" for u in units) else "")
    summary = (f"{table(units)}\n"
               f"pool: {len(units)} unit(s), {widths}, {time.monotonic() - started:.1f} s ("
               + ", ".join(f"{phase} {took:.1f} s" for phase, took in seconds.items()) + ")\n"
               + (f"pool: failed: {', '.join(failed_units)}\n" if failed_units else "")
               + watch.summary())
    out(summary)
    try:
        (LOGS / "summary.txt").write_text(summary + "\n")
    except OSError as error:
        out(f"pool: {LOGS / 'summary.txt'} could not be written: {describe(error)}")
    status = finish(units)
    if caught:
        for s, handler in previous.items():
            signal.signal(s, signal.SIG_DFL)
        os.kill(os.getpid(), caught[0])
    return status


if __name__ == "__main__":
    sys.exit(hold_and_exec(sys.argv[2:]) if sys.argv[1:2] == ["--hold-checkout"] else main())
