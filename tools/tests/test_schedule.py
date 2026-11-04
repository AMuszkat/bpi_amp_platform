#!/usr/bin/env python3
"""tools/schedule.py on synthetic steps, in seconds: the phases (no test before every build and
light step has ended), at most two heavy steps at once (a step of two slots alone among them), in
the plan's order, at nice 19, light ones beside them and not niced; the gate (created once every
other test has ended, with what it leaves out); a dependent skipped, saying why, the independent
ones run; each step's output as one block; the results, the table, the exit status (2 for a plan
it cannot run); the lock (a second schedule waits, naming the first); SIGTERM passed to the
running steps. Part of the `tools` check (tools/test.sh tools).

    python3 tools/tests/test_schedule.py

The steps are a small Python script (STEP) that stamps its start and end and sleeps; what must
still run when something happens waits for a file the test writes (60 s at most), so no result
depends on how fast the machine is beyond generous bounds.
"""

import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEDULE = ROOT / "tools" / "schedule.py"

# STEP NAME [sleep=S] [rc=N] [wait=FILE] [trap=1]: stamps NAME.start and NAME.end (time.time()) and
# its niceness in DIR (ST_DIR), prints some lines of its own, waits, and exits with rc.
STEP = r'''
import os, signal, sys, time
from pathlib import Path
name, opts = sys.argv[1], dict(a.split("=", 1) for a in sys.argv[2:])
d = Path(os.environ["ST_DIR"])
if opts.get("trap"):
    def on_term(signum, frame):
        (d / f"{name}.term").write_text(repr(time.time()))
        sys.exit(7)
    signal.signal(signal.SIGTERM, on_term)
(d / f"{name}.start").write_text(repr(time.time()))
(d / f"{name}.nice").write_text(str(os.nice(0)))
for i in range(3):
    print(f"<<{name}>> line {i}", flush=True)
print(f"<<{name}>> on stderr", file=sys.stderr, flush=True)
if "wait" in opts:
    deadline = time.monotonic() + 60
    while not Path(opts["wait"]).exists() and time.monotonic() < deadline:
        time.sleep(0.02)
time.sleep(float(opts.get("sleep", "0")))
(d / f"{name}.end").write_text(repr(time.time()))
sys.exit(int(opts.get("rc", "0")))
'''


class Schedule:
    """A schedule of `lines` ("NAME KIND [OPTION...] -- STEP-ARGS": the step's command is STEP
    NAME STEP-ARGS) in a temporary folder, run in the background."""

    def __init__(self, test, lines, *extra, gate=True, folder=None):
        self.dir = Path(folder or tempfile.mkdtemp(prefix="test-schedule-"))
        if folder is None:
            test.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.dir)]))
        (self.dir / "step.py").write_text(STEP)
        plan = []
        for line in lines:
            head, _, args = line.partition(" -- ")
            name = head.split()[0]
            plan.append(f"{head} -- {shlex.quote(sys.executable)} {shlex.quote(str(self.dir / 'step.py'))} {name} {args}")
        self.plan = self.dir / f"plan-{time.monotonic_ns()}"
        self.plan.write_text("# a plan\n\n" + "\n".join(plan) + "\n")
        self.logs, self.results_file, self.gate = self.dir / "logs", self.dir / "results", self.dir / "gate"
        self.out = self.dir / f"out-{time.monotonic_ns()}"
        command = [sys.executable, str(SCHEDULE), "--plan", str(self.plan), "--logs", str(self.logs),
                   "--results", str(self.results_file), *(["--gate", str(self.gate)] if gate else []), *extra]
        with open(self.out, "wb") as out:
            self.process = subprocess.Popen(command, cwd=self.dir, env=dict(os.environ, ST_DIR=str(self.dir)),
                                            stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)
        test.addCleanup(self.kill)

    def kill(self):
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait()

    def wait(self, timeout=120):
        self.process.wait(timeout=timeout)
        return self.process.returncode

    @property
    def output(self):
        return self.out.read_text(errors="replace")

    def stamp(self, name, what):
        path = self.dir / f"{name}.{what}"
        return float(path.read_text()) if path.exists() else None

    def interval(self, name):
        return self.stamp(name, "start"), self.stamp(name, "end")

    def results(self):
        return dict(line.split(" ", 1) for line in self.results_file.read_text().splitlines())

    def until(self, done, what, timeout=60):
        deadline = time.monotonic() + timeout
        while not done():
            if time.monotonic() > deadline or self.process.poll() is not None:
                if done():
                    return
                raise AssertionError(f"{what} within {timeout} s:\n{self.output}")
            time.sleep(0.02)


def run(test, lines, *extra, **kw):
    schedule = Schedule(test, lines, *extra, **kw)
    status = schedule.wait()
    return schedule, status


def overlap(a, b):
    return a[0] < b[1] and b[0] < a[1]


class ThePhases(unittest.TestCase):
    def test_tests_start_after_every_build_and_light_step(self):
        # Each of a pair that must overlap waits for the other's start: no sleep decides it.
        s, status = run(self, ["b1 heavy -- wait=l1.start", "b2 heavy -- sleep=0.2", "l1 light -- wait=b1.start",
                               "t1 test -- wait=t2.start", "t2 test -- wait=t1.start"])
        self.assertEqual(status, 0, s.output)
        phase1_end = max(s.interval(n)[1] for n in ("b1", "b2", "l1"))
        for name in ("t1", "t2"):
            self.assertGreaterEqual(s.interval(name)[0], phase1_end, f"{name} started before phase 1 ended\n{s.output}")
        self.assertTrue(overlap(s.interval("t1"), s.interval("t2")), "the tests of phase 2 ran one after the other")
        self.assertTrue(overlap(s.interval("b1"), s.interval("l1")), "the light step did not run beside the build")
        self.assertEqual(s.results(), {n: "passed" for n in ("b1", "b2", "l1", "t1", "t2")})
        self.assertRegex(s.output, r"schedule: 5 step\(s\) in [\d.]+ s: all passed\n")

    def test_at_most_two_heavy(self):
        s = Schedule(self, ["h0 heavy -- wait=RELEASE", "h1 heavy -- wait=RELEASE", "h2 heavy -- ",
                            "l light -- wait=RELEASE"])
        s.until(lambda: all(s.stamp(n, "start") for n in ("h0", "h1", "l")), "two heavy steps and the light one")
        time.sleep(0.5)                   # time for a third heavy step to start, if it would (it must not)
        self.assertIsNone(s.stamp("h2", "start"), "a third heavy step started beside two\n" + s.output)
        (s.dir / "RELEASE").touch()
        self.assertEqual(s.wait(), 0, s.output)
        self.assertGreaterEqual(s.stamp("h2", "start"), min(s.stamp("h0", "end"), s.stamp("h1", "end")))

    def test_in_the_plans_order_niced(self):
        names = [f"h{i}" for i in range(5)]
        s, status = run(self, ["h0 heavy -- wait=h1.start", "h1 heavy -- wait=h0.start"]
                        + [f"{n} heavy -- sleep=0.1" for n in names[2:]] + ["l light -- sleep=0.1"])
        self.assertEqual(status, 0, s.output)
        intervals = [s.interval(n) for n in names]
        for t in [start for start, _ in intervals]:
            at_once = sum(1 for a, b in intervals if a <= t < b)
            self.assertLessEqual(at_once, 2, f"{at_once} heavy steps at once\n{s.output}")
        self.assertTrue(any(overlap(intervals[i], intervals[j]) for i in range(5) for j in range(i + 1, 5)),
                        "no two heavy steps ever ran at once")
        started = re.findall(r"(?m)^schedule: \[\s*[\d.]+ s\] start (h\d) ", s.output)
        self.assertEqual(started, names, "the heavy steps did not start in the plan's order")
        base = os.nice(0)
        for n in names:
            self.assertEqual(int((s.dir / f"{n}.nice").read_text()), min(base + 19, 19), n)
        self.assertEqual(int((s.dir / "l.nice").read_text()), base, "a light step was niced")
        self.assertIn("(phase 1, heavy, nice 19;", s.output)

    def test_a_step_of_two_slots_runs_alone_among_the_heavy(self):
        s, status = run(self, ["a heavy -- wait=l.start", "both heavy slots=2 -- sleep=0.3", "b heavy -- sleep=0.3",
                               "c heavy -- sleep=0.3", "l light -- wait=a.start"])
        self.assertEqual(status, 0, s.output)
        for other in ("a", "b", "c"):
            self.assertFalse(overlap(s.interval("both"), s.interval(other)), f"{other} ran beside the two-slot step")
        self.assertTrue(overlap(s.interval("a"), s.interval("l")), "the light step waited for a heavy one")
        started = re.findall(r"(?m)^schedule: \[\s*[\d.]+ s\] start (\w+) ", s.output)
        self.assertLess(started.index("both"), started.index("b"), "b overtook the two-slot step that waited")
        self.assertIn("2 slots, nice 19", s.output)

    def test_three_slots_wide(self):
        # h0 and h1 hold their slots until h2 has started: only a third slot lets it.
        s, status = run(self, ["h0 heavy -- wait=h2.start", "h1 heavy -- wait=h2.start", "h2 heavy -- "],
                        "--heavy-slots", "3")
        self.assertEqual(status, 0, s.output)
        # With two slots h2 would start only once h0 or h1 had given up waiting for it (60 s).
        self.assertLess(s.stamp("h2", "start"), s.stamp("h0", "end"), s.output)
        self.assertLess(s.stamp("h2", "start"), s.stamp("h1", "end"), s.output)


class Dependents(unittest.TestCase):
    def test_a_failed_step_skips_its_dependents_and_stops_nothing_else(self):
        s, status = run(self, ["build light -- rc=3", "other heavy -- sleep=0.2",
                               "tests test needs=build -- ", "after test needs=tests -- ",
                               "independent test -- "])
        self.assertEqual(status, 1, s.output)
        self.assertEqual(s.results(), {"build": "failed", "other": "passed", "tests": "skipped", "after": "skipped",
                                       "independent": "passed"})
        self.assertIsNone(s.stamp("tests", "start"))
        self.assertIsNone(s.stamp("after", "start"))
        self.assertIn("=== /test tests: skipped: build failed\n", s.output)
        self.assertIn("=== /test after: skipped: tests skipped\n", s.output)
        self.assertIn("=== /test build: FAILED (exit 3) (phase 1, light,", s.output)
        self.assertRegex(s.output, r"schedule: 5 step\(s\) in [\d.]+ s: FAILED: build; skipped: tests, after\n")
        self.assertRegex(s.output, r"(?m)^\s+2  tests\s+[\d.]+\s+skipped: build failed$")


class TheGate(unittest.TestCase):
    def test_created_when_every_other_test_has_ended(self):
        s = Schedule(self, ["b heavy -- sleep=0.2", "slow test guards=test_x,test_y -- rc=1 wait=" + "RELEASE",
                            "fine test guards=test_z -- ", "pool test gated -- wait=GATE"])
        release = s.dir / "RELEASE"
        # The steps wait on files named relative to the schedule's folder (its cwd).
        s.until(lambda: s.stamp("pool", "start") is not None, "the gated step's start")
        time.sleep(0.3)
        self.assertFalse(s.gate.exists(), "the gate opened while a test still ran\n" + s.output)
        release.touch()
        s.until(lambda: s.gate.exists(), "the gate")
        (s.dir / "GATE").write_text(s.gate.read_text())
        self.assertEqual(s.wait(), 1, s.output)
        self.assertGreaterEqual(s.gate.stat().st_mtime, s.interval("slow")[1] - 0.01)
        self.assertEqual(s.gate.read_text(),
                         "leave-out test_x: slow did not pass (failed)\nleave-out test_y: slow did not pass (failed)\n")
        self.assertRegex(s.output, r"the gate is open \(\S+gate\): every other test has ended; it leaves out "
                                   r"test_x, test_y\n")
        self.assertEqual(s.results()["pool"], "passed")

    def test_open_at_once_with_no_other_test_and_after_a_skip(self):
        s = Schedule(self, ["b light -- rc=1", "t test needs=b guards=test_c -- ", "pool test gated -- wait=gate"])
        self.assertEqual(s.wait(), 1, s.output)
        self.assertEqual(s.gate.read_text(), "leave-out test_c: t did not pass (skipped: b failed)\n")
        self.assertEqual(s.results(), {"b": "failed", "t": "skipped", "pool": "passed"})

    def test_a_stale_gate_and_logs_are_removed_first(self):
        folder = Path(tempfile.mkdtemp(prefix="test-schedule-"))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(folder)]))
        (folder / "logs").mkdir()
        (folder / "logs" / "pool.note").write_text("an old run's note")
        (folder / "gate").write_text("")
        s = Schedule(self, ["t test -- sleep=0.5", "pool test gated -- "], folder=folder)
        s.until(lambda: s.stamp("t", "start") is not None, "t's start")
        self.assertFalse((folder / "gate").exists(), "the last run's gate is still there")
        self.assertEqual(s.wait(), 0, s.output)
        self.assertFalse((folder / "logs" / "pool.note").exists(), "the last run's note is still there")


class TheOutput(unittest.TestCase):
    def test_each_steps_output_is_one_block_and_a_table(self):
        s, status = run(self, ["a light -- sleep=0.2", "b light -- sleep=0.2", "c heavy title=the\\ c\\ step -- ",
                               "t test -- rc=2"])
        self.assertEqual(status, 1, s.output)
        lines = s.output.splitlines()
        for name, title in (("a", "a"), ("b", "b"), ("c", "the c step"), ("t", "t")):
            start = next(i for i, line in enumerate(lines) if line.startswith(f"=== /test {title}: "))
            self.assertEqual(lines[start + 1:start + 5], [f"<<{name}>> line 0", f"<<{name}>> line 1",
                                                          f"<<{name}>> line 2", f"<<{name}>> on stderr"], s.output)
            self.assertEqual(lines[start + 5], f"=== end of {title}")
            self.assertEqual((s.logs / f"{name}.log").read_text().count(f"<<{name}>>"), 4)
        self.assertRegex(s.output, r"=== /test t: FAILED \(exit 2\) \(phase 2, test, [\d.]+ s\)\n")
        self.assertRegex(s.output, r"(?m)^phase  step\s+start  seconds  result$")
        self.assertRegex(s.output, r"(?m)^\s+1  the c step\s+[\d.]+\s+[\d.]+  passed$")
        self.assertRegex(s.output, r"(?m)^\s+2  t\s+[\d.]+\s+[\d.]+  FAILED \(exit 2\)$")

    def test_a_plan_it_cannot_run(self):
        for lines, said in (
                (["a light needs=b -- ", "b light -- "], "a needs b, which no line before it is"),
                (["t test -- ", "b light needs=t -- "], "b (phase 1) needs t, a later phase's"),
                (["l light gated -- "], "only a test step is gated"),
                (["l light slots=2 -- "], "slots are a heavy step's"),
                (["h heavy slots=0 -- "], "option 'slots=0'"),
                (["x odd -- "], "kind 'odd'"),
                (["a light -- ", "a light -- "], "'a' is used twice"),
                (["a light what=1 -- "], "option 'what=1'")):
            with self.subTest(said=said):
                s, status = run(self, lines)
                self.assertEqual(status, 2, s.output)
                self.assertIn(said, s.output)
                self.assertFalse(s.logs.exists(), "a step ran")
        folder = Path(tempfile.mkdtemp(prefix="test-schedule-"))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(folder)]))
        (folder / "plan").write_text("a light\n")
        result = subprocess.run([sys.executable, str(SCHEDULE), "--plan", str(folder / "plan"), "--logs",
                                 str(folder / "logs")], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 2)
        self.assertIn("no -- before the command", result.stderr)


class TheLock(unittest.TestCase):
    def test_a_second_schedule_waits_for_the_first(self):
        folder = Path(tempfile.mkdtemp(prefix="test-schedule-"))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(folder)]))
        lock = folder / "run.lock"
        first = Schedule(self, ["a light -- wait=RELEASE"], "--lock", str(lock), "--what", "the first run",
                         folder=folder)
        first.until(lambda: first.stamp("a", "start") is not None, "the first's step")
        second_dir = folder / "second"
        second_dir.mkdir()
        second = Schedule(self, ["b light -- "], "--lock", str(lock), "--what", "the second run", folder=second_dir)
        second.until(lambda: "waiting for it to end" in second.output, "the second's waiting line")
        self.assertRegex(second.output, rf"schedule: another run holds this checkout: the first run, pid "
                                        rf"{first.process.pid}, since [\d:]+( \S+)?; waiting for it to end")
        self.assertIsNone(second.stamp("b", "start"))
        (folder / "RELEASE").touch()
        self.assertEqual(first.wait(), 0, first.output)
        self.assertEqual(second.wait(), 0, second.output)
        self.assertGreaterEqual(second.stamp("b", "start"), first.stamp("a", "end"))
        self.assertRegex(second.output, r"schedule: the other run ended; this one starts after waiting [\d.]+ s")
        self.assertNotIn("waiting", first.output)

    def test_sigterm_while_waiting(self):
        folder = Path(tempfile.mkdtemp(prefix="test-schedule-"))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(folder)]))
        lock = folder / "run.lock"
        first = Schedule(self, ["a light -- wait=RELEASE"], "--lock", str(lock), folder=folder)
        self.addCleanup(lambda: (folder / "RELEASE").touch())
        first.until(lambda: first.stamp("a", "start") is not None, "the first's step")
        second_dir = folder / "second"
        second_dir.mkdir()
        second = Schedule(self, ["b light -- "], "--lock", str(lock), folder=second_dir)
        second.until(lambda: "waiting for it to end" in second.output, "the second's waiting line")
        second.process.send_signal(signal.SIGTERM)
        self.assertEqual(second.wait(), -signal.SIGTERM, second.output)
        self.assertIn("schedule: SIGTERM while waiting: nothing ran", second.output)
        self.assertIsNone(second.stamp("b", "start"))


class Signals(unittest.TestCase):
    def test_sigterm_reaches_the_running_steps(self):
        s = Schedule(self, ["a light -- trap=1 wait=never", "b light -- sleep=0.1",
                            "c heavy -- wait=never", "t test -- "])
        s.until(lambda: s.stamp("a", "start") is not None and s.stamp("c", "start") is not None
                and s.stamp("b", "end") is not None, "the steps' starts")
        time.sleep(0.2)
        s.process.send_signal(signal.SIGTERM)
        self.assertEqual(s.wait(), -signal.SIGTERM, s.output)
        self.assertIsNotNone(s.stamp("a", "term"), "the running step never got the SIGTERM")
        self.assertIsNone(s.stamp("t", "start"), "a step started after the signal")
        self.assertEqual(s.results(), {"a": "interrupted", "b": "passed", "c": "interrupted", "t": "interrupted"})
        self.assertIn("=== /test a: interrupted (exit 7)", s.output)
        self.assertIn("=== /test c: interrupted (signal 15)", s.output)
        self.assertRegex(s.output, r"(?m)^\s+2  t\s+[\d.]+\s+interrupted: not started$")


if __name__ == "__main__":
    unittest.main(verbosity=2)
