#!/usr/bin/env python3
"""tools/test.sh's arguments: what it refuses (status 2, before it runs anything), and that --jobs
and --no-record are taken wherever they stand. No virtual pair and no build: test.sh runs natively
here (BPI_CONTAINER=0; inside the development container it does anyway), and with
BPI_TEST_DRY_RUN=1 it prints what its arguments decided, the schedule's plan too, and stops with
status 3 (DRY_RUN), so that an argument it should refuse but takes runs nothing either, and a stray
BPI_TEST_DRY_RUN fails a run instead of passing it.

And its runs, with stand-in steps (BPI_TEST_STUB: every step, the serial runner and tools/status.py
replaced by small scripts in a folder of build/, which record what they were asked and when):
the schedule's phases and niceness as test.sh plans them (every LTO build heavy, `tools` a test
step), what each level records (the same checked list, the pool's note, read from the run's own
folder: a stale results file of another run is never taken), a dependent skipped, a failed prebuild
stopping only what needs it (the pool still runs), the gate leaving out the controller check's end
to end after its own tests failed, no schedule for a vpair level without PipeWire,
test_controller_end_to_end once (in the vpair level, not in the unit level's controller step, in
the serial path too), and a run that would pass ending with 3.
Part of the `tools` check (tools/test.sh tools).

    python3 tools/tests/test_test_sh.py
"""

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEST_SH = ROOT / "tools" / "test.sh"
MAX_JOBS = 64           # tests/vpair/pool.py's limit, what --jobs and BPI_TEST_JOBS take
MAX_DEFAULT_JOBS = 16   # the default's limit: where the pool stops gaining (measured), not the pool's
DRY_RUN = 3             # a dry run's status: never 0, which would pass a check that ran nothing


def run(*args, ci=None, jobs=None, path=None):
    """tools/test.sh with these arguments, dry: (status, stdout, stderr). CI and BPI_TEST_JOBS are
    unset unless given, whatever the caller's environment says."""
    env = {k: v for k, v in os.environ.items() if k not in ("CI", "BPI_TEST_JOBS")}
    env.update(BPI_CONTAINER="0", BPI_TEST_DRY_RUN="1")
    if ci is not None:
        env["CI"] = ci
    if jobs is not None:
        env["BPI_TEST_JOBS"] = jobs
    if path is not None:
        env["PATH"] = f"{path}:{env['PATH']}"
    result = subprocess.run([str(TEST_SH), *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    return result.returncode, result.stdout, result.stderr


def decided(stdout):
    """The dry run's lines as {"jobs": "4", "record": "1", "run": "...", "check": [...]}."""
    found = {"check": []}
    for line in stdout.splitlines():
        key, _, value = line.partition(" ")
        if key == "check":
            found["check"].append(value)
        elif key in ("jobs", "record", "run"):
            found[key] = value
    return found


def default_jobs():
    processors = int(subprocess.run(["nproc"], capture_output=True, text=True, check=True).stdout)
    return min(max(processors * 2 // 3, 1), MAX_DEFAULT_JOBS)


class Refused(unittest.TestCase):
    """Each with status 2, saying why, and nothing run (a dry run that went on would say `jobs`)."""

    def refused(self, *args, said, **env):
        status, out, err = run(*args, **env)
        self.assertEqual(status, 2, f"{args}: status {status}\n{out}{err}")
        self.assertNotIn("jobs ", out, f"{args}: went on to run\n{out}")
        self.assertIn(said, err, f"{args}: said\n{err}")

    def test_no_arguments(self):
        self.refused(said="tools/test.sh plugin <name>")      # the usage

    def test_an_unknown_check(self):
        self.refused("nosuchcheck", said="tools/test.sh plugin <name>")
        self.refused("speakerd", "nosuchcheck", said="no check named nosuchcheck")

    def test_jobs_that_are_no_number_from_1_to_64(self):
        for args in (("--jobs", "0"), ("--jobs=0",), ("--jobs=x",), ("--jobs", "-3"), ("--jobs", "1.5"),
                     ("--jobs", "065"), ("--jobs", "65"), ("--jobs=256",), ("--jobs", "99999999999999999999999")):
            with self.subTest(args=args):
                self.refused(*args, "tools", said="--jobs takes a whole number from 1 to 64")

    def test_jobs_with_no_value(self):
        for args in (("--jobs=",), ("--jobs", ""), ("tools", "--jobs")):
            with self.subTest(args=args):
                self.refused(*args, said="--jobs takes a number")

    def test_bpi_test_jobs_like_jobs(self):
        for jobs in ("0", "65", "x"):
            with self.subTest(jobs=jobs):
                self.refused("tools", jobs=jobs, said="BPI_TEST_JOBS takes a whole number from 1 to 64")

    def test_a_plugin_or_a_module_without_a_name(self):
        self.refused("plugin", said="plugin: which plugin?")
        self.refused("plugin", "--jobs", "2", said="plugin: which plugin?")
        self.refused("speakerd", "plugin", said="plugin: which plugin?")
        self.refused("module", said="module: which module?")
        self.refused("speakerd", "module", "--no-record", said="module: which module?")

    def test_a_level_with_another_word(self):
        for level in ("unit", "vpair", "all"):
            with self.subTest(level=level):
                self.refused(level, "speakerd", said=f"{level} takes no arguments but --no-record and --jobs N")


class Taken(unittest.TestCase):
    """Each a dry run: status 3, `nothing ran` on stderr, and what the arguments decided."""

    def taken(self, *args, **env):
        status, out, err = run(*args, **env)
        self.assertEqual(status, DRY_RUN, f"{args}: status {status}\n{out}{err}")
        self.assertIn("BPI_TEST_DRY_RUN=1: nothing ran", err, f"{args}: said\n{err}")
        return decided(out)

    def test_a_dry_run_never_passes(self):
        # tools/container.sh passes every BPI_* variable into the container: a stray
        # BPI_TEST_DRY_RUN=1 must fail a check, a level and `changed`, not pass them unrun.
        for args in (("speakerd",), ("tools",), ("changed",), ("unit",), ("all",)):
            with self.subTest(args=args):
                self.assertEqual(self.taken(*args)["run"], args[0])

    def test_jobs_and_no_record_anywhere(self):
        for args in (("--jobs", "3", "--no-record", "plugin", "loudness", "speakerd"),
                     ("plugin", "--jobs", "3", "loudness", "--no-record", "speakerd"),
                     ("plugin", "loudness", "speakerd", "--no-record", "--jobs=3")):
            with self.subTest(args=args):
                got = self.taken(*args)
                self.assertEqual((got["jobs"], got["record"], got["check"]), ("3", "0", ["plugin loudness", "speakerd"]))

    def test_module_takes_every_name_after_it_but_the_options(self):
        got = self.taken("speakerd", "module", "audio_path", "--jobs", "2", "test_graph_edit", "--no-record")
        self.assertEqual((got["jobs"], got["record"], got["check"]), ("2", "0", ["speakerd", "module audio_path test_graph_edit"]))

    def test_a_level_takes_the_options(self):
        for args in (("unit", "--no-record"), ("--jobs", "1", "vpair"), ("all", "--jobs=64", "--no-record")):
            with self.subTest(args=args):
                got = self.taken(*args)
                self.assertEqual(got["run"], [a for a in args if a in ("unit", "vpair", "all")][0])
                self.assertEqual(got["record"], "0" if "--no-record" in args else "1")
                self.assertEqual(got["check"], [])

    def test_the_jobs_by_default(self):
        self.assertEqual(self.taken("tools")["jobs"], str(default_jobs()))
        self.assertEqual(self.taken("tools", ci="true")["jobs"], "1")
        self.assertEqual(self.taken("tools", jobs="5")["jobs"], "5")
        self.assertEqual(self.taken("tools", "--jobs", "7", jobs="5")["jobs"], "7")
        self.assertEqual(self.taken("tools", "--jobs", "4", ci="true")["jobs"], "4")

    def test_the_default_is_two_thirds_of_the_processors_at_most_16(self):
        # Measured on 24 threads: the pool gains nothing past 16 workers, and 20 or 24 made timing
        # tests fail. The default stops at 16 from 24 processors up, far below the pool's 64, which
        # an explicit number may still ask for.
        with tempfile.TemporaryDirectory(prefix="test-sh-") as tmp:
            nproc = Path(tmp) / "nproc"
            for processors, want in (("256", "16"), ("25", "16"), ("24", "16"), ("23", "15"), ("12", "8"),
                                     ("3", "2"), ("2", "1"), ("1", "1")):
                nproc.write_text(f"#!/bin/sh\necho {processors}\n")
                nproc.chmod(nproc.stat().st_mode | stat.S_IXUSR)
                with self.subTest(processors=processors):
                    self.assertEqual(self.taken("tools", path=tmp)["jobs"], want)
            self.assertEqual(self.taken("tools", "--jobs", "40", path=tmp)["jobs"], "40")
            self.assertEqual(self.taken("tools", jobs="40", path=tmp)["jobs"], "40")
            self.assertEqual(self.taken("tools", path=tmp, ci="true")["jobs"], "1")


    def test_the_plan(self):
        got = run("all", "--jobs", "4")[1]
        plan = [line[len("plan "):] for line in got.splitlines() if line.startswith("plan ")]
        self.assertTrue(plan[0].startswith("prebuild heavy slots=2 "), plan)       # first of the heavy steps
        heavy = [line.split()[0] for line in plan if line.split()[1] == "heavy"]
        lto = [f"plugin-lto-{p}" for p in plugins_with_targets()]
        # Every LTO build heavy (two heavy steps at most at once, the prebuild taking both): the block FIR first after the
        # prebuild, the aarch64 build identity last.
        self.assertEqual(heavy, ["prebuild", "blockfir", *lto, "buildinfo-aarch64"], plan)
        light = [line.split()[0] for line in plan if line.split()[1] == "light"]
        self.assertEqual(light, ["plugin-versions", "controller-build"], plan)
        self.assertIn("tools test title=unit:\\ tools -- tools", plan)              # builds nothing: phase 2
        self.assertIn("controller-tests test title=unit:\\ controller\\ \\(its\\ own\\ tests\\) needs=controller-build "
                      "-- controller-tests", plan)
        # The pool does not need its prebuild to pass: it fails only what needs a failed build.
        self.assertEqual(plan[-1], "pool test gated title=vpair -- pool vpair --")
        got = run("plugins", "tools", "--jobs", "4")[1]
        self.assertIn("plan blockfir heavy title=plugins:\\ blockfir -- blockfir\n", got)
        self.assertIn("plan tools test title=tools -- tools\n", got)
        self.assertFalse(any("guards=" in line for line in plan), plan)            # the levels run every module
        got = run("controller", "--jobs", "4")[1]
        self.assertIn("plan controller-tests test title=controller:\\ its\\ own\\ tests needs=controller-build "
                      "guards=test_controller_end_to_end -- controller-tests\n", got)
        self.assertIn("plan pool test gated title=controller -- pool controller --after-gate "
                      "test_controller_end_to_end -- test_controller_end_to_end\n", got)
        for args in (("all", "--jobs", "1"), ("speakerd", "--jobs", "1"), ("changed", "--jobs", "4")):
            with self.subTest(args=args):
                self.assertNotIn("plan ", run(*args)[1])


# The stand-ins: DIR/step records each call (its words, start, end, niceness, the run's folder
# TEST_RUN_DIR and TEST_SCHEDULE_PID) in DIR/calls; fails the steps STUB_FAIL names (a step, or
# plugin-lto-<name>); the prebuild writes its report in the run's folder (and a failed one the note
# of the units that cannot run); the pool waits for the gate there, keeps what it says in
# DIR/gate-seen and writes its note (a failure when it fails or the report says a build failed);
# the serial runner writes its note. DIR/status records its words in DIR/status-calls.
STUB_STEP = r"""#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
d = Path(os.environ["BPI_TEST_STUB"])
run = Path(os.environ.get("TEST_RUN_DIR") or "/nonexistent")
step, args = sys.argv[1], sys.argv[2:]
key = f"{step}-{args[0]}" if step in ("plugin-lto", "plugin") else step
fail = set(filter(None, os.environ.get("STUB_FAIL", "").split(",")))
start = time.time()
print(f"<<stub>> {key} {' '.join(args)}", flush=True)
if step == "pool":
    gate, deadline = run / "gate", time.monotonic() + 60
    while not gate.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    (d / "gate-seen").write_text(gate.read_text() if gate.exists() else "(no gate within 60 s)")
    report = json.loads((run / "prebuilt.json").read_text()) if (run / "prebuilt.json").exists() else None
    if report is None or report["failed"]:
        key_failed = True
        (run / "pool.note").write_text("6 tests, 1 failed: test_x.X, 0 skipped")
    else:
        key_failed = key in fail
        (run / "pool.note").write_text("1 tests, 1 failed: test_x.X, 0 skipped" if key_failed else "7 tests, 0 skipped")
    if key_failed:
        fail.add(key)
elif step == "prebuild":
    (run / "prebuilt.json").write_text(json.dumps({"built": [], "failed": {"x": "tools/build.sh x"}} if key in fail
                                                  else {"built": ["x"], "failed": {}}))
    if key in fail:
        (run / "pool.note").write_text("0 tests, 1 failed: test_x.X, 0 skipped")
elif step == "serial":
    Path(args[0]).write_text("3 tests, 0 skipped")
time.sleep(float(os.environ.get("STUB_SLEEP", "0.2")))
with open(d / "calls", "a") as f:
    f.write(json.dumps({"step": step, "key": key, "args": args, "start": start, "end": time.time(),
                        "nice": os.nice(0), "run": os.environ.get("TEST_RUN_DIR"),
                        "schedule": os.environ.get("TEST_SCHEDULE_PID")}) + "\n")
sys.exit(1 if key in fail else 0)
"""
STUB_STATUS = r"""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
with open(Path(os.environ["BPI_TEST_STUB"]) / "status-calls", "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
"""


def plugins_with_targets():
    """test.sh's plugins_with_targets, in its order (the glob's)."""
    return [p.parent.name for p in sorted((ROOT / "dsp" / "juce_plugins").glob("*/CMakeLists.txt"))
            if re.search(r"(?m)^\s*TARGETS", p.read_text(errors="replace"))]


UNIT_CHECKED = " ".join(plugins_with_targets() + ["buildinfo-aarch64", "blockfir", "plugin-versions", "controller",
                                                  "tools"]) + " "


class Stubbed(unittest.TestCase):
    """tools/test.sh's runs with stand-in steps (BPI_TEST_STUB), in a folder of build/ (a temporary
    folder may be noexec), with or without the schedule."""

    def stub_run(self, *args, fail="", prepare=None, **extra):
        build = ROOT / "build"
        build.mkdir(exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix="test-sh-stub-", dir=build))
        self.addCleanup(shutil.rmtree, folder, True)
        for name, text in (("step", STUB_STEP), ("status", STUB_STATUS)):
            (folder / name).write_text(text)
            (folder / name).chmod(0o755)
        if prepare is not None:
            prepare(folder)
        env = {k: v for k, v in os.environ.items()
               if k not in ("CI", "BPI_TEST_JOBS", "BPI_TEST_DRY_RUN", "TEST_RUN_DIR", "TEST_SCHEDULE_PID")}
        env.update(BPI_CONTAINER="0", BPI_TEST_STUB=str(folder), STUB_FAIL=fail, **extra)
        result = subprocess.run([str(TEST_SH), *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
        calls = [json.loads(line) for line in (folder / "calls").read_text().splitlines()] \
            if (folder / "calls").exists() else []
        status = [json.loads(line) for line in (folder / "status-calls").read_text().splitlines()] \
            if (folder / "status-calls").exists() else []
        gate = (folder / "gate-seen").read_text() if (folder / "gate-seen").exists() else None
        return result.returncode, result.stdout + result.stderr, calls, status, gate

    def keys(self, calls):
        return [c["key"] for c in calls]

    def by_key(self, calls):
        return {c["key"]: c for c in calls}

    def test_a_run_that_would_pass_ends_with_3(self):
        code, out, *_ = self.stub_run("tools", "--jobs", "4")
        self.assertEqual(code, DRY_RUN, out)
        self.assertIn("BPI_TEST_STUB: stand-in steps, so no pass (3)", out)
        code, out, *_ = self.stub_run("tools", "--jobs", "4", fail="tools")
        self.assertEqual(code, 1, out)

    def test_the_unit_level_scheduled(self):
        code, out, calls, status, gate = self.stub_run("unit", "--jobs", "4")
        self.assertEqual(code, DRY_RUN, out)
        lto = [f"plugin-lto-{p}" for p in plugins_with_targets()]
        self.assertEqual(sorted(self.keys(calls)), sorted(lto + ["buildinfo-aarch64", "blockfir", "plugin-versions",
                                                                 "controller-build", "controller-tests", "tools"]))
        found = self.by_key(calls)
        phase1_end = max(c["end"] for c in calls if c["key"] not in ("controller-tests", "tools"))
        self.assertGreaterEqual(found["controller-tests"]["start"], phase1_end, out)
        self.assertGreaterEqual(found["tools"]["start"], phase1_end, out)          # a test step: phase 2
        base = os.nice(0)
        for key in lto + ["blockfir", "buildinfo-aarch64"]:                         # every LTO build heavy
            self.assertEqual(found[key]["nice"], min(base + 19, 19), key)
        for key in ("tools", "plugin-versions", "controller-build", "controller-tests"):
            self.assertEqual(found[key]["nice"], base, key)
        heavy = sorted((c for c in calls if c["key"] in lto + ["blockfir", "buildinfo-aarch64"]), key=lambda c: c["start"])
        for c in heavy:                                                             # two at most at once
            at_once = sum(1 for o in heavy if o["start"] <= c["start"] < o["end"])
            self.assertLessEqual(at_once, 2, out)
        runs = {c["run"] for c in calls}
        self.assertEqual(len(runs), 1, calls)                                       # one run's folder,
        self.assertFalse(Path(runs.pop()).exists(), "the run's folder was left behind")   # removed at the end
        self.assertEqual(len({c["schedule"] for c in calls}), 1, calls)
        self.assertEqual(status, [["set", "unit", "pass", UNIT_CHECKED], ["show"]])
        for p in plugins_with_targets() + ["controller", "tools"]:
            self.assertIn(f"unit: {p} passed\n", out)
        self.assertIsNone(gate)
        self.assertRegex(out, r"(?m)^phase  step\s+start  seconds  result$")

    def test_a_failed_controller_build_skips_its_tests_and_stops_nothing_else(self):
        code, out, calls, status, _ = self.stub_run("unit", "--jobs", "4", "--no-record", fail="controller-build")
        self.assertEqual(code, 1, out)
        self.assertNotIn("controller-tests", self.keys(calls))
        self.assertIn("tools", self.keys(calls))
        self.assertIn("=== /test unit: controller (its own tests): skipped: unit: controller (its build) failed", out)
        self.assertIn("unit: controller FAILED\n", out)
        self.assertIn("unit: tools passed\n", out)
        self.assertEqual(status, [])                                     # --no-record

    def test_all_scheduled_records_both_levels_and_runs_the_end_to_end_once(self):
        code, out, calls, status, gate = self.stub_run("all", "--jobs", "4")
        self.assertEqual(code, DRY_RUN, out)
        found = self.by_key(calls)
        self.assertEqual(self.keys(calls).count("pool"), 1)
        self.assertEqual(found["pool"]["args"], ["vpair", "--"])        # every module, nothing held
        self.assertNotIn("serial", self.keys(calls))
        self.assertFalse(any("test_controller_end_to_end" in c["args"] for c in calls), calls)
        heavy = [c for c in calls if c["key"].startswith("plugin-lto-")]
        self.assertLessEqual(found["prebuild"]["start"], min(c["start"] for c in heavy) + 0.05, out)
        phase1_end = max(c["end"] for c in calls if c["key"] not in ("pool", "controller-tests", "tools"))
        for key in ("pool", "controller-tests", "tools"):
            self.assertGreaterEqual(found[key]["start"], phase1_end, f"{key}\n{out}")
        self.assertEqual(gate, "")
        self.assertEqual(status, [["set", "unit", "pass", UNIT_CHECKED], ["set", "vpair", "pass", "7 tests, 0 skipped"],
                                  ["show"]])
        self.assertIn("vpair: 7 tests, 0 skipped\n", out)

    def test_a_failed_prebuild_stops_only_what_needs_it(self):
        # The pool runs all the same, on the prebuild's report: the units whose build failed fail,
        # the others run (tests/vpair/pool.py --prebuilt); its note is the one recorded.
        code, out, calls, status, _ = self.stub_run("vpair", "--jobs", "4", fail="prebuild")
        self.assertEqual(code, 1, out)
        found = self.by_key(calls)
        self.assertGreaterEqual(found["pool"]["start"], found["prebuild"]["end"], out)
        self.assertEqual(found["pool"]["run"], found["prebuild"]["run"], calls)      # the same run's folder
        self.assertNotIn("skipped:", out)
        self.assertEqual(status, [["set", "vpair", "fail", "6 tests, 1 failed: test_x.X, 0 skipped"], ["show"]])

    def test_results_are_the_runs_own(self):
        # A schedule that ends without writing results (here: its lock cannot be opened) passes no
        # step, whatever an earlier run left in the steps' folder.
        def prepare(folder):
            (folder / "test-run.lock").mkdir()                   # os.open(O_RDWR) of a folder fails
            (folder / "steps").mkdir()
            (folder / "steps" / "results").write_text("".join(f"{k} passed\n" for k in (
                [f"plugin-lto-{p}" for p in plugins_with_targets()]
                + ["buildinfo-aarch64", "blockfir", "plugin-versions", "controller-build", "controller-tests", "tools"])))
        code, out, calls, status, _ = self.stub_run("unit", "--jobs", "4", prepare=prepare)
        self.assertEqual(code, 1, out)
        self.assertEqual(calls, [])
        self.assertEqual(status, [["set", "unit", "fail", UNIT_CHECKED], ["show"]])
        self.assertIn("unit: tools FAILED\n", out)
        self.assertRegex(out, r"schedule: the lock \S+test-run\.lock: .*: nothing ran\n")

    def test_no_schedule_for_a_vpair_level_without_pipewire(self):
        code, out, calls, status, _ = self.stub_run("vpair", "--jobs", "4", STUB_PIPEWIRE="no")
        self.assertEqual(code, 1, out)
        self.assertEqual(calls, [])
        self.assertNotIn("schedule:", out)                       # no plan of no step (its exit 2)
        self.assertIn("vpair: pipewire and wireplumber are needed", out)
        self.assertEqual(status, [["set", "vpair", "skipped", "no pipewire"], ["show"]])

    def test_a_failed_pool_records_fail(self):
        code, out, _, status, _ = self.stub_run("vpair", "--jobs", "4", fail="pool")
        self.assertEqual(code, 1, out)
        self.assertEqual(status, [["set", "vpair", "fail", "1 tests, 1 failed: test_x.X, 0 skipped"], ["show"]])

    def test_the_controller_check_leaves_its_end_to_end_out_after_its_tests_failed(self):
        code, out, calls, status, gate = self.stub_run("controller", "tools", "--jobs", "4", fail="controller-tests")
        self.assertEqual(code, 1, out)
        found = self.by_key(calls)
        self.assertEqual(found["pool"]["args"], ["controller", "--after-gate", "test_controller_end_to_end", "--",
                                                 "test_controller_end_to_end"])
        self.assertEqual(gate, "leave-out test_controller_end_to_end: controller: its own tests did not pass (failed)\n")
        self.assertIn("controller: its build or its own tests failed: test_controller_end_to_end not run\n", out)
        self.assertEqual(status, [])                                     # a check records nothing
        code, out, calls, _, gate = self.stub_run("controller", "--jobs", "4")
        self.assertEqual((code, gate), (DRY_RUN, ""), out)

    def test_the_serial_unit_level_has_no_end_to_end(self):
        code, out, calls, status, _ = self.stub_run("unit", "--jobs", "1")
        self.assertEqual(code, DRY_RUN, out)
        lto = [f"plugin-lto-{p}" for p in plugins_with_targets()]
        self.assertEqual(self.keys(calls), lto + ["buildinfo-aarch64", "blockfir", "plugin-versions",
                                                  "controller-build", "controller-tests", "tools"])
        self.assertEqual(status, [["set", "unit", "pass", UNIT_CHECKED], ["show"]])
        self.assertNotIn("schedule:", out)                               # the serial path, as before

    def test_the_serial_levels_and_controller_check(self):
        code, out, calls, status, _ = self.stub_run("all", "--jobs", "1")
        self.assertEqual(code, DRY_RUN, out)
        serial = [c for c in calls if c["step"] == "serial"]
        self.assertEqual(len(serial), 1, calls)
        self.assertEqual(serial[0]["args"][1:], [])                      # the vpair level: every module
        self.assertEqual(status[1], ["set", "vpair", "pass", "3 tests, 0 skipped"])
        code, out, calls, _, _ = self.stub_run("controller", "--jobs", "1")
        self.assertEqual(code, DRY_RUN, out)
        self.assertEqual(self.keys(calls), ["controller-build", "controller-tests", "serial"])
        self.assertEqual(calls[-1]["args"][1:], ["test_controller_end_to_end"])
        code, out, calls, _, _ = self.stub_run("controller", "--jobs", "1", fail="controller-tests")
        self.assertEqual(code, 1, out)
        self.assertEqual(self.keys(calls), ["controller-build", "controller-tests"])


if __name__ == "__main__":
    unittest.main()
