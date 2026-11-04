#!/usr/bin/env python3
"""A self-test of tests/vpair/pool.py: seconds, no PipeWire, nothing built (README.md, "The pool").

    python3 tests/vpair/pool_selftest.py      # from the repository root, in the container or not

Tiny synthetic modules, written into a temporary folder, run by pool.py itself (VPAIR_POOL_TESTS is
that folder, VPAIR_POOL_FILES another for its logs, lock and durations, so a real pool of this
checkout is not disturbed), all at once, and checked for:

- the note, in tools/test.sh's VPAIR_RUNNER format exactly and the same as VPAIR_RUNNER itself
  writes for the same modules: failures, errors, a failed subtest, a skipped test, a skipped class
  and a skip in setUpClass, reasons of several lines on one line;
- a unit killed mid-run, one past its timeout and one whose log vanishes: each failed under its unit
  name with its reason, its block printed, the tests it ran before counted (the one past its timeout
  in a run of its own, the only one with a short --unit-timeout);
- each unit's output as one block, never interleaved with another's;
- the exit status: 0 when every test passed (skips allowed), 1 otherwise, 2 for --jobs outside 1 to
  64; SIGTERM ends the pool by that signal, with its note;
- the built() guard (harness._check_pool_build): under the pool a build the module's POOL_BUILDS
  does not name is refused, saying so, and one whose prebuild failed fails as the build would;
- tools/build.sh's line that it waits for another build in its tree, passed through as it comes,
  once, by the prebuild and by built() (harness.run_build), while the rest of a build's output shows
  only when it fails: a stand-in tools/build.sh says the line and waits until the test saw it; the
  prefix both look for, harness.BUILD_WAITING (the stand-in's line is made from it), is how
  tools/build.sh's echo begins, and that line, $out expanded, goes through both readers;
- a build whose reading stops killed and reaped, as subprocess.run does, not left running with its
  tree's lock: in built() (a print to a closed stderr), in the prebuild's reader (to a closed
  stdout) and in the prebuild (a SIGINT to the pool alone);
- the checkout's lock: a second pool on the same files waits for the first, naming it, and starts
  when it ends; the first keeps every result; a third and a fourth, sent SIGTERM and SIGINT while
  they wait, end by that signal there and then, their notes failing their unit, which never started;
- the serial runner's side of it (pool.py --hold-checkout, then tools/test.sh's VPAIR_RUNNER): it
  waits for a pool, naming it, and a pool for it; SIGTERM while it waits ends it, nothing run; a
  child it leaves running (a shell's background sleep) does not hold the lock;
- the phases: main units first, then the quiet ones (POOL_QUIET True, a class, a test of a split
  module) on min(--jobs, 12) workers or --quiet-jobs, then those alone, each after the one before
  ended; the table's phase column; a POOL_QUIET that cannot be (not True or names, a test of a class
  that runs whole, no such class) failing the run; --quiet-jobs outside 1 to 64;
- the gate: nothing quiet or alone (nor what --after-gate names) starts before FILE exists; its
  leave-out line leaves a unit out of the run and the note, the table saying why; another line is
  said; (its "still waiting" line every 60 s is not tested: it would take a minute);
- --prebuild-only: built, 0 and no note, no unit; failed, 1 and the note of a failed prebuild;
- the pool's last resorts: fewer free worker slots than it needs, and a fault of its own while it
  handles a unit (work()'s "the pool failed while handling it"): each failed under the unit's name,
  saying why, the pool going on with the next unit. These two, the prebuild's waiting line and the
  SIGINT in the prebuild run pool.py's main() in a process that imports it and replaces one of its
  functions first (take_slots, Pool.read_events, build_command): pool.py needs no other seam for
  them.

No result depends on how fast anything runs, within generous bounds (the unit past its 2 s timeout
must print its line within those 2 s; what waits to be released, the lock test's first pool's unit
or a stand-in build, gives up after 60 s; the test's own waits, there for a pool that hangs, after
60 or 120 s, 30 s for the prebuild's reader to give up a build): a unit or a build that must still
be running when something happens blocks on a release file the test writes once it has (ST_RELEASE,
ST_BUILD_RELEASE; 60 s at most, then it writes its end stamp and fails), and the only short timeout
is the one under test (2 s), of a unit that prints its line at once and sleeps.

POOL_SELFTEST_KEEP=1 keeps its temporary folder (the pools' outputs, notes and logs) and names it.
Its name does not match test*.py, so the vpair level never runs it. It runs where it is started
(BPI_CONTAINER=0: nothing moves into the container) and needs python3 and numpy, which the harness
imports (the container and CI have both).
"""

import io
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
POOL = HERE / "pool.py"
os.environ["BPI_CONTAINER"] = "0"           # here, and in every pool and unit it starts

try:
    import numpy  # noqa: F401
except ImportError:
    sys.exit("pool_selftest: needs numpy, which the harness imports: python3-numpy, or run it in the "
             "development container (tools/container.sh python3 tests/vpair/pool_selftest.py)")

sys.path.insert(0, str(HERE))
import harness  # noqa: E402
import pool  # noqa: E402  (its prebuild's reader, read_build, tested here in this process)

# A synthetic module's head: stamp(test), its unit's start and end, for the phases' order.
STAMP = """
        import os
        import time
        import unittest
        from pathlib import Path


        def stamp(test, sleep=0.2):
            # The unit's start and end, by its name, in ST_STAMPS (a class unit by its class).
            name = test.id() if os.environ.get("ST_BY_TEST") else f"{type(test).__module__}.{type(test).__name__}"
            stamps = Path(os.environ["ST_STAMPS"])
            (stamps / f"{name}.start").write_text(repr(time.time()))
            time.sleep(sleep)
            (stamps / f"{name}.end").write_text(repr(time.time()))
"""

MODULES = {
    "test_st_results.py": '''
        import unittest


        class Outcomes(unittest.TestCase):
            def test_a_passes(self):
                print("<<Outcomes>> test_a_passes says so")

            def test_b_fails(self):
                self.assertEqual(1, 2, "one is not two")

            def test_c_errors(self):
                raise RuntimeError("an error, not a failure")

            def test_d_subtests(self):
                for i in range(3):
                    with self.subTest(i=i):
                        self.assertNotEqual(i, 1)

            def test_e_skips(self):
                self.skipTest("a reason\\n   of two lines")


        @unittest.skip("the whole class\\nskipped")
        class SkippedClass(unittest.TestCase):
            def test_one(self):
                pass

            def test_two(self):
                pass


        class SkippedInSetUpClass(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                raise unittest.SkipTest("setUpClass\\tsays\\n no")

            def test_never(self):
                pass
    ''',
    "test_st_blocks.py": '''
        import sys
        import time
        import unittest


        def chatter(tag):
            for i in range(25):
                print(f"<<{tag}>> out {i}", flush=True)
                print(f"<<{tag}>> err {i}", file=sys.stderr, flush=True)
                time.sleep(0.01)


        class Alpha(unittest.TestCase):
            def test_chatters(self):
                chatter("Alpha")


        class Beta(unittest.TestCase):
            def test_chatters(self):
                chatter("Beta")


        class Gamma(unittest.TestCase):
            def test_chatters(self):
                chatter("Gamma")
    ''',
    "test_st_guard.py": '''
        import unittest

        import harness

        POOL_BUILDS = ()


        class Guard(unittest.TestCase):
            def test_an_undeclared_build_is_refused(self):
                with self.assertRaises(RuntimeError) as caught:
                    harness.built("loudness", "headless")
                print(f"refused: {caught.exception}")
                self.assertIn("test_st_guard.POOL_BUILDS does not name 'loudness:headless'", str(caught.exception))

            @unittest.skip("a skip, which passes")
            def test_skipped(self):
                pass
    ''',
    "test_st_killed.py": '''
        import os
        import signal
        import unittest


        class Killed(unittest.TestCase):
            def test_a_runs(self):
                print("<<Killed>> test_a_runs ran")

            def test_b_dies(self):
                print("<<Killed>> test_b_dies: SIGKILL to itself", flush=True)
                os.kill(os.getpid(), signal.SIGKILL)

            def test_c_never_runs(self):
                pass
    ''',
    "test_st_hangs.py": '''
        import time
        import unittest


        class Hangs(unittest.TestCase):
            def test_hangs(self):
                print("<<Hangs>> sleeping past the unit's timeout", flush=True)
                time.sleep(60)
    ''',
    "test_st_nolog.py": '''
        import os
        import unittest


        class NoLog(unittest.TestCase):
            def test_removes_its_log(self):
                os.unlink(os.readlink("/proc/self/fd/1"))      # its stdout is the pool's log of it
    ''',
    "test_st_builds.py": '''
        import unittest

        POOL_BUILDS = ("st_app",)


        class Builds(unittest.TestCase):
            def test_runs_after_its_build(self):
                print("<<Builds>> ran")
    ''',
    "test_st_slow.py": '''
        import os
        import time
        import unittest
        from pathlib import Path


        def take_a_while():
            stamps = Path(os.environ["ST_STAMPS"])
            (stamps / "start").write_text(repr(time.time()))
            print("<<Slow>> started", flush=True)
            released = True
            if os.environ.get("ST_RELEASE"):    # until the test releases it, 60 s at most
                release, deadline = Path(os.environ["ST_RELEASE"]), time.monotonic() + 60
                while not (released := release.exists()) and time.monotonic() < deadline:
                    time.sleep(0.02)
            else:
                time.sleep(float(os.environ["ST_SLEEP"]))
            (stamps / "end").write_text(repr(time.time()))      # its end, released or not
            if not released:
                raise AssertionError(f"not released within 60 s: no {release}")


        class Slow(unittest.TestCase):
            def test_takes_a_while(self):
                take_a_while()


        class AlsoSlow(unittest.TestCase):          # another unit: its own log and events
            def test_takes_a_while(self):
                take_a_while()
    ''',
    # The phases: units neither quiet nor alone (main), quiet ones by each form of POOL_QUIET, one
    # alone (and quiet too, as test_chain_cost is), each stamping its start and end.
    "test_st_main.py": STAMP + '''

        class MainA(unittest.TestCase):
            def test_it(self):
                stamp(self)


        class MainB(unittest.TestCase):
            def test_it(self):
                stamp(self)
    ''',
    "test_st_quiet.py": STAMP + '''
        POOL_QUIET = True


        class QuietA(unittest.TestCase):
            def test_it(self):
                stamp(self)


        class QuietB(unittest.TestCase):
            def test_it(self):
                stamp(self)
    ''',
    "test_st_split.py": STAMP + '''
        POOL_SPLIT = True
        POOL_QUIET = ("Mixed.test_quiet", "AllQuiet")


        class Mixed(unittest.TestCase):
            def test_quiet(self):
                stamp(self)

            def test_loud(self):
                stamp(self)


        class AllQuiet(unittest.TestCase):
            def test_one(self):
                stamp(self)
    ''',
    "test_st_lonely.py": STAMP + '''
        POOL_ALONE = True
        POOL_QUIET = True


        class Lonely(unittest.TestCase):
            def test_it(self):
                stamp(self)
    ''',
    # Thirteen quiet units: the quiet phase's default width stops at 12.
    "test_st_many.py": '''
        import unittest

        POOL_QUIET = True
        for i in range(13):
            globals()[f"Many{i:02d}"] = type(f"Many{i:02d}", (unittest.TestCase,), {"test_it": lambda self: None})
    ''',
    # POOL_QUIET that cannot be: not True or names; a test of a class that runs as one unit; no such class.
    "test_st_badquiet_a.py": '''
        import unittest

        POOL_QUIET = "yes"


        class A(unittest.TestCase):
            def test_it(self):
                pass
    ''',
    "test_st_badquiet_b.py": '''
        import unittest

        POOL_QUIET = ("Whole.test_x",)


        class Whole(unittest.TestCase):
            def test_x(self):
                pass

            def test_y(self):
                pass
    ''',
    "test_st_badquiet_c.py": '''
        import unittest

        POOL_QUIET = ("Nothing",)


        class C(unittest.TestCase):
            def test_it(self):
                pass
    ''',
    # Run by tools/test.sh's serial runner under pool.py --hold-checkout: the lock's descriptor is not
    # passed on (the runner took VPAIR_CHECKOUT_LOCK_FD and made it non-inheritable), so a child left
    # running (a shell's background sleep: os.system passes inheritable descriptors on) does not keep
    # the checkout's lock after the runner ends.
    "test_st_serial.py": STAMP + '''

        class Serial(unittest.TestCase):
            def test_its_children_do_not_hold_the_lock(self):
                self.assertNotIn("VPAIR_CHECKOUT_LOCK_FD", os.environ)
                child = Path(os.environ["ST_STAMPS"]) / "child"
                os.system(f"sleep 60 > /dev/null 2>&1 & echo $! > {child}")
                stamp(self)
    ''',
}

# What VPAIR_RUNNER writes for test_st_results and test_st_blocks, and so the pool.
RESULTS_NOTE = (
    "10 tests, 3 failed: test_st_results.Outcomes.test_b_fails, test_st_results.Outcomes.test_d_subtests (i=1), "
    "test_st_results.Outcomes.test_c_errors, 4 skipped: test_st_results.Outcomes.test_e_skips (a reason of two "
    "lines); test_st_results.SkippedClass.test_one (the whole class skipped); test_st_results.SkippedClass.test_two "
    "(the whole class skipped); setUpClass (test_st_results.SkippedInSetUpClass) (setUpClass says no)")

# pool.py's main() with one of its functions replaced first: python3 -c WRAPPER <tests/vpair> <patch> ARGS...
# (its units still run the real pool.py --unit).
WRAPPER = """
import sys
here, patch = sys.argv[1], sys.argv[2]
sys.argv = [here + "/pool.py", *sys.argv[3:]]
sys.path.insert(0, here)
import pool
exec(patch)
sys.exit(pool.main())
"""
# One free worker slot fewer than it needs, as when other pools in this /tmp hold the rest: none for
# one (take_slots(0) would take every free slot).
FEWER_SLOTS = """
real = pool.take_slots
pool.take_slots = lambda n: real(n - 1) if n > 1 else []
"""
# A fault of the pool's own while it handles test_st_slow.Slow, after the unit ran (outside run_unit's
# own try blocks): work()'s last resort.
A_FAULT = """
real = pool.Pool.read_events
def read_events(unit, *args):
    if unit.name == "test_st_slow.Slow":
        raise RuntimeError("a fault of the pool's own, injected")
    return real(unit, *args)
pool.Pool.read_events = staticmethod(read_events)
"""

# The prebuild with a stand-in for tools/build.sh (STAND_IN, run by python3: /tmp may be noexec).
STAND_IN_BUILD = """
import os
pool.build_command = lambda what, kinds: [sys.executable, os.environ["ST_STAND_IN"], what, *kinds]
"""
# The stand-in: its process ID in ST_BUILD_PID (if set), a line of the build's own (kept to itself
# unless the build fails), tools/build.sh's line when another build holds the tree (WAITING_LINE, from
# the prefix built() and the prebuild look for, harness.BUILD_WAITING, which
# TheBuildsWaitingLine.test_the_prefix_is_tools_build_shs checks against tools/build.sh), then, until
# ST_BUILD_RELEASE exists (60 s at most), nothing; then its end stamp, ST_BUILD_END. With ST_BUILD_FAIL
# it fails at once instead.
STAND_IN = '''
import os
import sys
import time
from pathlib import Path

WAITING = ...
what = sys.argv[1]
if os.environ.get("ST_BUILD_PID"):
    Path(os.environ["ST_BUILD_PID"]).write_text(str(os.getpid()))
print(f"<<build>> {what}: the build's own output", flush=True)
print(f"{WAITING} build/{what}: another build runs in it (build/{what}/.build.lock)", file=sys.stderr, flush=True)
if os.environ.get("ST_BUILD_FAIL"):
    print(f"<<build>> {what}: an error of the build's own", flush=True)
    sys.exit(2)
release, deadline = Path(os.environ["ST_BUILD_RELEASE"]), time.monotonic() + 60
while not release.exists() and time.monotonic() < deadline:
    time.sleep(0.02)
Path(os.environ["ST_BUILD_END"]).write_text(repr(time.time()))
sys.exit(0 if release.exists() else 1)
'''.replace("WAITING = ...", f"WAITING = {harness.BUILD_WAITING!r}")
WAITING_LINE = f"{harness.BUILD_WAITING} build/st_app: another build runs in it (build/st_app/.build.lock)"

TMP = Path(tempfile.mkdtemp(prefix="pool-selftest-"))
TESTS = TMP / "tests" / "vpair"             # as VPAIR_RUNNER finds them from TMP
RUNS = {}
SETUP_ERRORS = {}
FIRST_RAN_ON = {}                           # pool name -> whether the first still ran when it ended
SEEN_WHILE_BUILDING = {}                    # "prebuild" -> whether its stand-in still ran when the line came
GATE_SEEN = {}                              # "started before" -> the units stamped before the gate was written
SERIAL_SEEN = {}                            # what had started while the other side held the checkout


class Run:
    """pool.py on the synthetic modules, in the background: its own files folder (and so its own
    lock) unless given another's; `patch`, Python run in the pool's process before its main().
    `serial`: tools/test.sh's serial runner instead, as it runs it (pool.py --hold-checkout, then
    VPAIR_RUNNER from TMP, which finds the modules in tests/vpair there), `args` its modules."""

    def __init__(self, name, args, files=None, patch=None, serial=False, **env):
        self.name, self.files = name, files or TMP / f"files-{name}"
        self.files.mkdir(parents=True, exist_ok=True)
        self.stamps = TMP / f"stamps-{name}"
        self.stamps.mkdir(exist_ok=True)
        self.note_path = TMP / f"note-{name}.txt"
        self.out_path = TMP / f"out-{name}.txt"
        environment = dict(os.environ, VPAIR_POOL_TESTS=str(TESTS), VPAIR_POOL_FILES=str(self.files),
                           ST_STAMPS=str(self.stamps), **env)
        if serial:
            command = [sys.executable, str(POOL), "--hold-checkout", "--what", " ".join(args), "--",
                       sys.executable, "-c", vpair_runner(), str(self.note_path), *args]
        else:
            command = [*([sys.executable, "-c", WRAPPER, str(HERE), patch] if patch else [sys.executable, str(POOL)]),
                       "--note", str(self.note_path), *args]
        with open(self.out_path, "wb") as out:
            self.process = subprocess.Popen(command, cwd=TMP if serial else ROOT, env=environment,
                                            stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)
        self.pid = self.process.pid

    def wait(self, timeout=120):
        self.process.wait(timeout=timeout)
        return self.process.returncode

    @property
    def output(self):
        return self.out_path.read_text(errors="replace")

    @property
    def note(self):
        return self.note_path.read_text() if self.note_path.exists() else None

    def wait_for(self, path, timeout=60):
        self.until(path.exists, f"no {path.name}", timeout)

    def wait_for_output(self, text, timeout=60):
        self.until(lambda: text in self.output, f"no {text!r} in its output", timeout)

    def until(self, done, what, timeout):
        """Until done(), while it runs; the timeout is for a pool that hangs, not a measure."""
        deadline = time.monotonic() + timeout
        while not done():
            if time.monotonic() > deadline or self.process.poll() is not None:
                if done():
                    return
                raise AssertionError(f"{self.name}: {what} within {timeout} s (exit status "
                                     f"{self.process.poll()}):\n{self.output}")
            time.sleep(0.05)


def block(output, unit):
    """The lines of a unit's block: from its header to its end line."""
    lines = output.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith(f"===== {unit} (worker ")), None)
    if start is None:
        raise AssertionError(f"no block of {unit} in:\n{output}")
    end = next((i for i in range(start, len(lines)) if lines[i] == f"===== end of {unit}"), None)
    return lines[start:end + 1 if end is not None else start + 1], start, end


def build_sh_waiting_line():
    """tools/build.sh's line when another build holds its tree's lock, as its echo writes it ($out
    unexpanded): found by its place, the echo just before the flock that waits, not by its words."""
    text = (ROOT / "tools" / "build.sh").read_text()
    found = re.findall(r'^[ \t]*echo "([^"\n]*)" >&2\n[ \t]*flock "\$tree_lock"[ \t]*$', text, re.MULTILINE)
    if len(found) != 1:
        raise AssertionError(f"tools/build.sh has {len(found)} echo \"...\" >&2 lines just before a "
                             f"flock \"$tree_lock\" (its line that it waits for another build), not one")
    return found[0]


def gone(pid):
    """Whether process `pid` has ended and been reaped (a zombie is not gone)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def kill_if_running(pid):
    """A stand-in build a failed test left running (its 60 s are not waited for)."""
    if not gone(pid):
        os.kill(pid, signal.SIGKILL)


def stamps(run):
    """{unit: (start, end)} of the stamping modules' units a run ran."""
    found = {}
    for path in run.stamps.glob("*.start"):
        name = path.name[:-len(".start")]
        end = run.stamps / f"{name}.end"
        found[name] = (float(path.read_text()), float(end.read_text()) if end.exists() else None)
    return found


def table_phase(output, unit):
    """The phase the pool's table gives a unit."""
    found = re.search(rf"(?m)^{re.escape(unit)}\s+(main|quiet|alone)\s", output)
    if not found:
        raise AssertionError(f"no row of {unit} with a phase in the table:\n{output}")
    return found.group(1)


def lock_is_free(files):
    """Whether this process can take the checkout's lock in `files` at once (and gives it back)."""
    import fcntl
    with open(files / "vpair-pool.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(lock, fcntl.LOCK_UN)
        return True


def stop(process):
    if process.poll() is None:
        process.kill()
        process.wait()


def vpair_runner():
    """tools/test.sh's VPAIR_RUNNER, the note format the pool's must be exactly."""
    text = (ROOT / "tools" / "test.sh").read_text()
    found = re.search(r"^VPAIR_RUNNER='\n(.*?)^'$", text, re.MULTILINE | re.DOTALL)
    if not found:
        raise AssertionError("tools/test.sh has no VPAIR_RUNNER='...' (the serial runner), whose note format "
                             "pool.py's must be exactly")
    return found.group(1)


def setUpModule():
    TESTS.mkdir(parents=True)
    for name, source in MODULES.items():
        (TESTS / name).write_text(textwrap.dedent(source).lstrip())
    (TMP / "stand-in-build.py").write_text(STAND_IN.lstrip())
    RUNS["results"] = Run("results", ["--jobs", "4", "test_st_results", "test_st_blocks"])
    RUNS["guard"] = Run("guard", ["--jobs", "64", "test_st_guard"])
    # The short timeout for the unit under test alone: a slow start of the others is no failure.
    RUNS["broken"] = Run("broken", ["--jobs", "2", "test_st_killed", "test_st_nolog"])
    RUNS["hangs"] = Run("hangs", ["--jobs", "1", "--unit-timeout", "2", "test_st_hangs"])
    RUNS["noslot"] = Run("noslot", ["--jobs", "2", "test_st_slow.Slow", "test_st_slow.AlsoSlow"], patch=FEWER_SLOTS,
                         ST_SLEEP="0")
    RUNS["fault"] = Run("fault", ["--jobs", "1", "test_st_slow.Slow", "test_st_slow.AlsoSlow"], patch=A_FAULT,
                        ST_SLEEP="0")
    RUNS["buildfails"] = Run("buildfails", ["--jobs", "1", "test_st_builds"], patch=STAND_IN_BUILD,
                             ST_STAND_IN=str(TMP / "stand-in-build.py"), ST_BUILD_FAIL="1")
    RUNS["jobs0"] = Run("jobs0", ["--jobs", "0", "test_st_guard"])
    RUNS["jobs65"] = Run("jobs65", ["--jobs", "65", "test_st_guard"])
    RUNS["quietjobs0"] = Run("quietjobs0", ["--jobs", "2", "--quiet-jobs", "0", "test_st_guard"])
    RUNS["quietjobs65"] = Run("quietjobs65", ["--jobs", "2", "--quiet-jobs", "65", "test_st_guard"])
    # The phases, without a gate: main on 3 workers, quiet on min(3, 12), alone last.
    RUNS["phases"] = Run("phases", ["--jobs", "3", "test_st_main", "test_st_quiet", "test_st_split", "test_st_lonely"],
                         ST_BY_TEST="1")
    RUNS["many"] = Run("many", ["--jobs", "20", "test_st_many"])
    RUNS["fewquiet"] = Run("fewquiet", ["--jobs", "20", "--quiet-jobs", "2", "test_st_many"])
    RUNS["badquiet"] = Run("badquiet", ["--jobs", "2", "test_st_badquiet_a", "test_st_badquiet_b", "test_st_badquiet_c"])
    released = TMP / "released"
    released.touch()
    RUNS["prebuildonly"] = Run("prebuildonly", ["--jobs", "1", "--prebuild-only", "test_st_builds"], patch=STAND_IN_BUILD,
                               ST_STAND_IN=str(TMP / "stand-in-build.py"), ST_BUILD_RELEASE=str(released),
                               ST_BUILD_END=str(TMP / "build-end-prebuildonly"))
    RUNS["prebuildonlyfails"] = Run("prebuildonlyfails", ["--jobs", "1", "--prebuild-only", "test_st_builds"],
                                    patch=STAND_IN_BUILD, ST_STAND_IN=str(TMP / "stand-in-build.py"), ST_BUILD_FAIL="1")
    # The serial runner on the same modules, from TMP (it reads tests/vpair from where it runs).
    with open(TMP / "out-runner.txt", "wb") as out:
        RUNS["runner"] = subprocess.Popen([sys.executable, "-c", vpair_runner(), str(TMP / "note-runner.txt"),
                                           "test_st_results", "test_st_blocks"], cwd=TMP, stdin=subprocess.DEVNULL,
                                          stdout=out, stderr=subprocess.STDOUT)

    # The lock: a pool that holds this checkout's (here: these files') lock while its unit runs, until
    # released; three more started then, on its files; once all wait for it, SIGTERM to the third and
    # SIGINT to the fourth, and when those have ended, the first's release. And a pool stopped by
    # SIGTERM mid-unit (its unit never released). In a thread each, beside the rest.
    def lock():
        release = TMP / "release-first"
        try:
            first = RUNS["first"] = Run("first", ["--jobs", "1", "test_st_slow.Slow"], ST_RELEASE=str(release))
            first.wait_for(first.stamps / "start")
            second = RUNS["second"] = Run("second", ["--jobs", "1", "test_st_slow.AlsoSlow"], files=first.files,
                                          ST_SLEEP="0.1")
            signalled = {name: Run(name, ["--jobs", "1", "test_st_slow.Slow"], files=first.files, ST_SLEEP="0.1")
                         for name in ("third", "fourth")}
            RUNS.update(signalled)
            for run in (second, *signalled.values()):
                run.wait_for_output("; waiting for it to end")
            for run, signum in zip(signalled.values(), (signal.SIGTERM, signal.SIGINT)):
                run.process.send_signal(signum)
            for name, run in signalled.items():
                run.wait()
                FIRST_RAN_ON[name] = first.process.poll() is None
        finally:
            release.touch()                 # whatever happened: the first ends, and the second runs

    def term():
        run = RUNS["term"] = Run("term", ["--jobs", "1", "test_st_slow.Slow"], ST_RELEASE=str(TMP / "never"))
        run.wait_for(run.stamps / "start")
        time.sleep(0.3)
        run.process.send_signal(signal.SIGTERM)

    # The prebuild's waiting line: its stand-in build waits until the line is in the pool's output.
    def waiting():
        release, end = TMP / "release-build", TMP / "build-end-prebuild"
        try:
            run = RUNS["prebuild"] = Run("prebuild", ["--jobs", "1", "test_st_builds"], patch=STAND_IN_BUILD,
                                         ST_STAND_IN=str(TMP / "stand-in-build.py"), ST_BUILD_RELEASE=str(release),
                                         ST_BUILD_END=str(end))
            run.wait_for_output(WAITING_LINE)
            SEEN_WHILE_BUILDING["prebuild"] = not end.exists()
        finally:
            release.touch()

    # The gate: its quiet and alone units (MainB too, by --after-gate) wait until the test writes it,
    # leaving QuietB out; the test looks first that nothing waiting for it started.
    def gate():
        gate_file = TMP / "the-gate"
        try:
            run = RUNS["gate"] = Run("gate", ["--jobs", "2", "--gate", str(gate_file), "--after-gate", "test_st_main.MainB",
                                              "test_st_main", "test_st_quiet", "test_st_lonely"])
            run.wait_for_output("wait for the gate")
            time.sleep(0.5)               # time for a unit that would not wait to start (none must)
            GATE_SEEN["started before"] = sorted(stamps(run))
            tmp = gate_file.with_name(".the-gate.tmp")
            tmp.write_text("leave-out test_st_quiet.QuietB: the controller's own tests failed\n"
                           "not a line of the gate's\n")
            os.replace(tmp, gate_file)
        finally:
            if not gate_file.exists():
                gate_file.touch()

    # The serial runner and a pool exclude each other: the runner waits for a pool (and a second
    # runner, sent SIGTERM while it waits, ends there); a pool waits for a runner.
    def serial():
        release = TMP / "release-serial-holder"
        try:
            holder = RUNS["serial-holder"] = Run("serial-holder", ["--jobs", "1", "test_st_slow.Slow"],
                                                 ST_RELEASE=str(release))
            holder.wait_for(holder.stamps / "start")
            runner = RUNS["serial"] = Run("serial", ["test_st_serial"], files=holder.files, serial=True)
            term = RUNS["serial-term"] = Run("serial-term", ["test_st_serial"], files=holder.files, serial=True)
            for run in (runner, term):
                run.wait_for_output("; waiting for it to end")
            term.process.send_signal(signal.SIGTERM)
            term.wait()
            SERIAL_SEEN["started while held"] = sorted(stamps(runner))
        finally:
            release.touch()

    def serial_holds():
        release = TMP / "release-serial-runner"
        try:
            runner = RUNS["serial-runner"] = Run("serial-runner", ["test_st_slow.Slow"], serial=True,
                                                 ST_RELEASE=str(release))
            runner.wait_for(runner.stamps / "start")
            waiting = RUNS["pool-after-serial"] = Run("pool-after-serial", ["--jobs", "1", "test_st_slow.AlsoSlow"],
                                                      files=runner.files, ST_SLEEP="0.1")
            waiting.wait_for_output("; waiting for it to end")
            SERIAL_SEEN["pool started while held"] = (waiting.stamps / "start").exists()
        finally:
            release.touch()

    def keeping(name, f):
        def run():
            try:
                f()
            except Exception as error:      # noqa: BLE001  (the test that needs it says so)
                SETUP_ERRORS[name] = error
        return run

    threads = [threading.Thread(target=keeping(name, f))
               for name, f in (("lock", lock), ("term", term), ("prebuild", waiting), ("gate", gate),
                               ("serial", serial), ("serial_holds", serial_holds))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def tearDownModule():
    for run in RUNS.values():
        process = run.process if isinstance(run, Run) else run
        if process.poll() is None:
            process.kill()
            process.wait()
    if os.environ.get("POOL_SELFTEST_KEEP"):
        print(f"pool_selftest: kept {TMP}", file=sys.stderr)
    else:
        shutil.rmtree(TMP, ignore_errors=True)


class TheNote(unittest.TestCase):
    def test_failures_errors_subtests_and_skips(self):
        run = RUNS["results"]
        self.assertEqual(run.wait(), 1, run.output)
        self.assertEqual(run.note, RESULTS_NOTE, run.output)
        self.assertIn(f"note: {RESULTS_NOTE}", run.output)

    def test_the_same_note_as_the_serial_runner(self):
        runner = RUNS["runner"]
        runner.wait(timeout=60)
        output = (TMP / "out-runner.txt").read_text(errors="replace")
        self.assertEqual(runner.returncode, 1, output)
        RUNS["results"].wait()
        self.assertEqual((TMP / "note-runner.txt").read_text(), RUNS["results"].note, output)

    def test_each_units_output_is_one_block(self):
        run = RUNS["results"]
        run.wait()
        output = run.output
        lines = output.splitlines()
        for tag in ("Alpha", "Beta", "Gamma", "Outcomes"):
            unit = f"test_st_blocks.{tag}" if tag != "Outcomes" else "test_st_results.Outcomes"
            _, start, end = block(output, unit)
            self.assertIsNotNone(end, f"{unit}'s block has no end line:\n{output}")
            mine = [i for i, line in enumerate(lines) if f"<<{tag}>>" in line]
            self.assertTrue(mine, output)
            self.assertTrue(all(start < i < end for i in mine), f"{unit}'s lines outside its block:\n{output}")
            other = re.compile(r"<<(?!" + tag + r">>)\w+>>")
            others = [i for i, line in enumerate(lines[start:end], start) if other.search(line)]
            self.assertEqual(others, [], f"another unit's lines in {unit}'s block:\n{output}")
        for tag in ("Alpha", "Beta", "Gamma"):
            self.assertEqual(sum(f"<<{tag}>> out" in line for line in lines), 25)
            self.assertEqual(sum(f"<<{tag}>> err" in line for line in lines), 25)

    def test_the_table_and_the_failed_units(self):
        run = RUNS["results"]
        run.wait()
        output = run.output
        self.assertRegex(output, r"(?m)^unit\s+phase\s+worker\s+seconds\s+result$")
        self.assertRegex(output, r"(?m)^test_st_results\.Outcomes\s+main\s+\d+\s+[\d.]+\s+FAILED \(3 of 5 tests\)$")
        self.assertRegex(output, r"(?m)^test_st_results\.SkippedInSetUpClass\s+main\s+\d+\s+[\d.]+\s+"
                                 r"passed \(0 tests, 1 skipped\)$")
        self.assertRegex(output, r"(?m)^test_st_blocks\.Alpha\s+main\s+\d+\s+[\d.]+\s+passed \(1 test\)$")
        self.assertIn("pool: failed: test_st_results.Outcomes\n", output)


class BrokenUnits(unittest.TestCase):
    """A unit killed mid-run, one past its timeout, one whose log vanished: each failed under its
    own name, with its reason, its block printed. The one past its timeout in a run of its own, the
    only one with a short --unit-timeout."""

    @classmethod
    def setUpClass(cls):
        cls.run_, cls.hangs = RUNS["broken"], RUNS["hangs"]
        cls.status, cls.hangs_status = cls.run_.wait(), cls.hangs.wait()
        cls.output, cls.hangs_output = cls.run_.output, cls.hangs.output

    def test_the_note(self):
        self.assertEqual(self.status, 1, self.output)
        self.assertEqual(self.run_.note, "3 tests, 2 failed: test_st_killed.Killed, test_st_nolog.NoLog, 0 skipped",
                         self.output)
        self.assertEqual(self.hangs_status, 1, self.hangs_output)
        self.assertEqual(self.hangs.note, "1 tests, 1 failed: test_st_hangs.Hangs, 0 skipped", self.hangs_output)

    def test_killed_mid_run(self):
        lines, _, end = block(self.output, "test_st_killed.Killed")
        self.assertIn("FAILED: its process was killed by signal 9 =====", lines[0], self.output)
        self.assertIsNotNone(end, self.output)
        self.assertTrue(any("test_b_dies: SIGKILL to itself" in line for line in lines), self.output)
        self.assertRegex(self.output, r"(?m)^test_st_killed\.Killed\s+main\s+\d+\s+[\d.]+\s+"
                                      r"FAILED: its process was killed by signal 9$")

    def test_past_its_timeout(self):
        lines, _, end = block(self.hangs_output, "test_st_hangs.Hangs")
        self.assertIn("FAILED: no result within its timeout of 2 s =====", lines[0], self.hangs_output)
        self.assertIsNotNone(end, self.hangs_output)
        self.assertTrue(any("sleeping past the unit's timeout" in line for line in lines), self.hangs_output)

    def test_its_log_gone(self):
        lines, _, _ = block(self.output, "test_st_nolog.NoLog")
        self.assertRegex(lines[0], r"FAILED: its log could not be read: FileNotFoundError: .*test_st_nolog\.NoLog\.log",
                         self.output)

    def test_every_failure_says_why(self):
        for output in (self.output, self.hangs_output):
            self.assertNotRegex(output, r"(?m)FAILED:\s*(=====)?$", output)
            self.assertNotIn("Exception in thread", output)      # a worker thread that died
            self.assertNotIn("the pool failed while handling it", output)


class ExitStatus(unittest.TestCase):
    def test_passed_with_a_skip(self):
        run = RUNS["guard"]
        self.assertEqual(run.wait(), 0, run.output)
        self.assertEqual(run.note, "2 tests, 1 skipped: test_st_guard.Guard.test_skipped (a skip, which passes)")

    def test_jobs_from_1_to_64(self):
        for name in ("jobs0", "jobs65"):
            run = RUNS[name]
            self.assertEqual(run.wait(), 2, run.output)
            self.assertIn("--jobs: 1 to 64", run.output)
            self.assertIsNone(run.note)
        self.assertIn("pool: 1 unit(s) on 1 worker(s)", RUNS["guard"].output)    # --jobs 64 taken

    def test_sigterm(self):
        if "term" in SETUP_ERRORS:
            raise SETUP_ERRORS["term"]
        run = RUNS["term"]
        self.assertEqual(run.wait(), -signal.SIGTERM, run.output)
        self.assertIn("pool: SIGTERM: stopping the workers", run.output)
        lines, _, _ = block(run.output, "test_st_slow.Slow")
        self.assertIn("FAILED: interrupted =====", lines[0], run.output)
        self.assertEqual(run.note, "1 tests, 1 failed: test_st_slow.Slow, 0 skipped", run.output)
        self.assertFalse((run.stamps / "end").exists(), "the unit ran on after the pool's SIGTERM")


class TheGuard(unittest.TestCase):
    """harness._check_pool_build, which built() calls before it uses anything."""

    def test_through_the_pool(self):
        run = RUNS["guard"]
        run.wait()
        lines, _, _ = block(run.output, "test_st_guard.Guard")
        self.assertTrue(any("refused: built('loudness', 'headless'): test_st_guard.POOL_BUILDS does not name "
                            "'loudness:headless', so tests/vpair/pool.py's prebuild did not build it" in line
                            for line in lines), run.output)

    def test_its_cases(self):
        check = harness._check_pool_build
        clean = {k: v for k, v in os.environ.items() if not k.startswith("VPAIR_POOL_")}
        with mock.patch.dict(os.environ, clean, clear=True):
            self.assertIsNone(check("crossover", "headless"))          # no pool: no guard
        pool = dict(clean, VPAIR_POOL_MODULE="test_x", VPAIR_POOL_BUILDS="speakerd loudness:headless",
                    VPAIR_POOL_UNBUILT="controller")
        with mock.patch.dict(os.environ, pool, clear=True):
            self.assertIsNone(check("speakerd", None))
            self.assertIsNone(check("loudness", "headless"))
            with self.assertRaisesRegex(RuntimeError, r"test_x\.POOL_BUILDS does not name 'loudness:complete'"):
                check("loudness", "complete")
            with self.assertRaisesRegex(RuntimeError, r"test_x\.POOL_BUILDS does not name 'crossover:headless'"):
                check("crossover", "headless")
            with self.assertRaisesRegex(RuntimeError, r"tools/build.sh controller\s+failed in tests/vpair/pool.py's "
                                                      r"prebuild"):
                check("controller", None)
        with mock.patch.dict(os.environ, dict(clean, VPAIR_POOL_BUILDS=""), clear=True):
            with self.assertRaisesRegex(RuntimeError, r"the module\.POOL_BUILDS does not name 'speakerd'"):
                check("speakerd", None)


class TheBuildsWaitingLine(unittest.TestCase):
    """tools/build.sh's line that it waits for another build in its tree, passed through as it comes
    and once, by the prebuild (a stand-in tools/build.sh in its place, which waits until the test saw
    the line) and by built() (harness.run_build); the rest of a build's output shows only when it
    fails, as before."""

    def test_through_the_prebuild(self):
        if "prebuild" in SETUP_ERRORS:
            raise SETUP_ERRORS["prebuild"]
        run = RUNS["prebuild"]
        self.assertEqual(run.wait(), 0, run.output)
        self.assertTrue(SEEN_WHILE_BUILDING["prebuild"], "the line came only once the build ended:\n" + run.output)
        lines = run.output.splitlines()
        self.assertEqual(lines.count(WAITING_LINE), 1, run.output)
        built_line = next((i for i, line in enumerate(lines)
                           if re.fullmatch(r"prebuild: tools/build\.sh st_app: built in [\d.]+ s", line)), None)
        self.assertIsNotNone(built_line, run.output)
        self.assertLess(lines.index(WAITING_LINE), built_line, run.output)
        self.assertNotIn("<<build>>", run.output)             # the rest of its output: not on success
        self.assertEqual(run.note, "1 tests, 0 skipped", run.output)

    def test_a_failed_prebuild_shows_the_rest(self):
        run = RUNS["buildfails"]
        self.assertEqual(run.wait(), 1, run.output)
        self.assertIn("prebuild: tools/build.sh st_app FAILED (exit 2)", run.output)
        failed = run.output[run.output.index("prebuild: tools/build.sh st_app FAILED"):]
        for line in ("<<build>> st_app: the build's own output", WAITING_LINE, "<<build>> st_app: an error of the "
                     "build's own"):
            self.assertIn(line, failed, run.output)
        self.assertEqual(run.note, "0 tests, 1 failed: test_st_builds.Builds, 0 skipped", run.output)
        self.assertNotIn("<<Builds>>", run.output)

    def test_through_built(self):
        release, end = TMP / "release-built", TMP / "build-end-built"
        said, came, result = [], threading.Event(), []

        class Stderr:
            def write(self, text):
                said.append(text)
                if WAITING_LINE in "".join(said):
                    came.set()

            def flush(self):
                pass

        env = dict(ST_BUILD_RELEASE=str(release), ST_BUILD_END=str(end))
        command = [sys.executable, str(TMP / "stand-in-build.py"), "st_app"]
        with mock.patch.dict(os.environ, env), mock.patch.object(sys, "stderr", Stderr()):
            builder = threading.Thread(target=lambda: result.append(harness.run_build(command)), daemon=True)
            builder.start()
            try:
                self.assertTrue(came.wait(60), f"no waiting line on stderr within 60 s: {''.join(said)!r}")
                self.assertFalse(end.exists(), "the line came only once the build ended")
            finally:
                release.touch()
                builder.join(60)
        self.assertEqual(len(result), 1, "harness.run_build did not return within 60 s of the release")
        status, output = result[0]
        self.assertEqual(status, 0, output)
        self.assertEqual("".join(said), WAITING_LINE + "\n")   # that line alone, once
        self.assertIn("<<build>> st_app: the build's own output", output)
        self.assertIn(WAITING_LINE, output)                   # kept too, for a failure's tail

    def test_the_prefix_is_tools_build_shs(self):
        # The prefix built() and the prebuild look for, harness.BUILD_WAITING (the pool's is the
        # harness's), is how tools/build.sh's line begins: the stand-in says what the harness looks for,
        # so a rewording in tools/build.sh would otherwise stop the pass-through with every other test
        # green. tools/build.sh's own line, $out expanded, goes through both readers.
        echo = build_sh_waiting_line()
        self.assertTrue(echo.startswith(harness.BUILD_WAITING),
                        f"tools/build.sh's waiting line, {echo!r}, does not begin with harness.BUILD_WAITING, "
                        f"{harness.BUILD_WAITING!r}: built() and the pool's prebuild would no longer pass it "
                        f"through (change them together)")
        self.assertTrue(WAITING_LINE.startswith(harness.BUILD_WAITING), WAITING_LINE)
        self.assertIn(f"\nWAITING = {harness.BUILD_WAITING!r}\n", STAND_IN)    # the stand-in's line, from it
        line = echo.replace("$out", "build/st_app")
        command = [sys.executable, "-c", f"import sys; print('<<build>> its own'); print({line!r}, file=sys.stderr)"]
        said = io.StringIO()
        with mock.patch.object(sys, "stderr", said):
            status, output = harness.run_build(command)
        self.assertEqual((status, said.getvalue()), (0, line + "\n"), output)
        process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, errors="replace")
        shown, lines, ended = [], [], []
        pool.read_build(process, lines, ended, shown.append)
        self.assertEqual((process.returncode, shown), (0, [line]), "".join(lines))


class AStoppedRead(unittest.TestCase):
    """What stops a build's reading kills the build and reaps it before it goes on, as subprocess.run
    does, rather than leave it running with its tree's lock: in built() (harness.run_build: a
    KeyboardInterrupt to the test process alone, a print to a closed stderr), in the prebuild's reader
    (a print to a closed stdout) and in the prebuild itself (a SIGINT to the pool alone). Each stand-in
    build would otherwise wait 60 s for a release that never comes."""

    def stand_in(self, name):
        """The stand-in's environment for test `name`: never released, its PID in a file."""
        pid_file = TMP / f"build-pid-{name}"
        return pid_file, dict(ST_STAND_IN=str(TMP / "stand-in-build.py"), ST_BUILD_RELEASE=str(TMP / "never"),
                              ST_BUILD_END=str(TMP / f"build-end-{name}"), ST_BUILD_PID=str(pid_file))

    def pid(self, pid_file):
        pid = int(pid_file.read_text())
        self.addCleanup(kill_if_running, pid)
        return pid

    def test_in_built(self):
        pid_file, env = self.stand_in("built-stopped")
        closed = io.StringIO()
        closed.close()
        command = [sys.executable, env["ST_STAND_IN"], "st_app"]
        with mock.patch.dict(os.environ, env), mock.patch.object(sys, "stderr", closed):
            with self.assertRaisesRegex(ValueError, "closed file"):        # at its waiting line
                harness.run_build(command)
        self.assertTrue(gone(self.pid(pid_file)), "harness.run_build left the build it stopped reading running "
                                                  "(or did not reap it)")

    def test_in_the_prebuilds_reader(self):
        pid_file, env = self.stand_in("reader-stopped")

        def out(_text):
            raise BrokenPipeError(32, "Broken pipe")     # the pool's stdout, closed

        process = subprocess.Popen([sys.executable, env["ST_STAND_IN"], "st_app"], cwd=ROOT, env=dict(os.environ, **env),
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                   errors="replace")
        self.addCleanup(stop, process)
        lines, ended, raised = [], [], []

        def read():
            try:
                pool.read_build(process, lines, ended, out)
            except BrokenPipeError as error:
                raised.append(error)

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        reader.join(30)                                   # at once; 30 s for a loaded machine, not a measure
        self.assertFalse(reader.is_alive(), "pool.read_build still waits for the build it stopped reading")
        self.assertEqual(len(raised), 1, "pool.read_build did not raise what stopped it")
        self.assertEqual((process.returncode, len(ended)), (-signal.SIGKILL, 1), "".join(lines))
        self.assertTrue(gone(self.pid(pid_file)))

    def test_in_the_prebuild(self):
        pid_file, env = self.stand_in("prebuild-sigint")
        run = RUNS["prebuild-sigint"] = Run("prebuild-sigint", ["--jobs", "1", "test_st_builds"], patch=STAND_IN_BUILD,
                                            **env)
        run.wait_for_output(WAITING_LINE)
        pid = self.pid(pid_file)
        run.process.send_signal(signal.SIGINT)            # to the pool alone, not to its build
        self.assertEqual(run.wait(), -signal.SIGINT, run.output)
        self.assertTrue(gone(pid), "the pool ended with its build still running (or not reaped):\n" + run.output)
        self.assertFalse(Path(env["ST_BUILD_END"]).exists(), run.output)
        self.assertNotIn("<<Builds>>", run.output)       # no unit ran


class TheCheckoutsLock(unittest.TestCase):
    """Two pools of one checkout: the second waits for the first, naming it, and starts when it
    ends; the first keeps every result (without the lock the second would delete its logs). A third
    and a fourth, sent SIGTERM and SIGINT while they wait, end by that signal at once, none of their
    units run."""

    def test_the_second_waits(self):
        if "lock" in SETUP_ERRORS:
            raise SETUP_ERRORS["lock"]
        first, second = RUNS["first"], RUNS["second"]
        self.assertEqual(first.wait(), 0, first.output)
        self.assertEqual(second.wait(), 0, second.output)
        self.assertEqual(first.note, "1 tests, 0 skipped", first.output)          # its result kept
        self.assertEqual(second.note, "1 tests, 0 skipped", second.output)
        self.assertRegex(second.output, rf"pool: another run holds this checkout: pool\.py pid {first.pid} "
                                        r"\((in|outside) the container\), since [\d:]+( \S+)?, "
                                        r"--jobs 1 test_st_slow\.Slow; waiting for it to end", second.output)
        self.assertRegex(second.output, r"pool: the other run ended; this one starts after waiting [\d.]+ s")
        said = second.output
        self.assertLess(said.index("waiting for it to end"), said.index("pool: 1 unit(s) on 1 worker(s)"), said)
        first_end = float((first.stamps / "end").read_text())
        second_start = float((second.stamps / "start").read_text())
        self.assertGreaterEqual(second_start, first_end, "the second pool's unit started before the first ended")
        self.assertNotIn("waiting", first.output)

    def test_a_signal_while_waiting(self):
        # SIGTERM to the third, SIGINT to the fourth (whose handler before the wait was Python's own, or
        # ignored: only SIG_DFL again ends it by the signal), while they wait.
        if "lock" in SETUP_ERRORS:
            raise SETUP_ERRORS["lock"]
        first = RUNS["first"]
        for name, signum in (("third", signal.SIGTERM), ("fourth", signal.SIGINT)):
            with self.subTest(name):
                run = RUNS[name]
                self.assertEqual(run.wait(), -signum, run.output)         # SIG_DFL again, and itself killed
                self.assertTrue(FIRST_RAN_ON[name], f"the {name} pool waited for the first to end:\n{run.output}")
                self.assertRegex(run.output, rf"pool: another run holds this checkout: pool\.py pid {first.pid} "
                                             r".*; waiting for it to end", run.output)
                self.assertIn(f"pool: {signal.Signals(signum).name} while waiting: none of the 1 unit(s) ran\n",
                              run.output)
                self.assertEqual(run.note, "0 tests, 1 failed: test_st_slow.Slow, 0 skipped", run.output)
                self.assertIn(f"note: {run.note}\n", run.output)
                self.assertFalse((run.stamps / "start").exists(), f"the {name} pool's unit started:\n{run.output}")
                self.assertNotIn("unit(s) on", run.output)
                self.assertNotIn("===== test_st_slow.Slow", run.output)
                self.assertNotIn("Traceback", run.output)


class ThePoolsLastResorts(unittest.TestCase):
    """What the pool's own side cannot do is a unit's failure, under its name, saying why: fewer free
    worker slots than it needs (no unit runs), a fault while it handles a unit (work()'s last resort:
    the next unit runs)."""

    def test_no_free_worker_slot(self):
        run = RUNS["noslot"]
        self.assertEqual(run.wait(), 1, run.output)
        self.assertRegex(run.output, r"(?m)^pool: only 1 of the 100 worker slots are free \(/tmp/vpool<k>\.lock\), "
                                     r"2 needed: other pools run in this /tmp$")
        self.assertEqual(run.note, "0 tests, 2 failed: test_st_slow.Slow, test_st_slow.AlsoSlow, 0 skipped", run.output)
        self.assertIn(f"note: {run.note}\n", run.output)
        self.assertFalse((run.stamps / "start").exists(), "a unit started without its worker slot:\n" + run.output)
        self.assertNotIn("unit(s) on", run.output)

    def test_a_fault_while_handling_a_unit(self):
        run = RUNS["fault"]
        self.assertEqual(run.wait(), 1, run.output)
        why = "the pool failed while handling it: RuntimeError: a fault of the pool's own, injected"
        lines, _, end = block(run.output, "test_st_slow.Slow")
        self.assertRegex(lines[0], r"^===== test_st_slow\.Slow \(worker \d+\): FAILED: " + re.escape(why) + " =====$",
                         run.output)
        self.assertIsNotNone(end, run.output)
        self.assertTrue(any(line.startswith("Traceback") for line in lines), run.output)
        self.assertEqual(lines[-2], "RuntimeError: a fault of the pool's own, injected", run.output)
        self.assertRegex(run.output, r"(?m)^test_st_slow\.Slow\s+main\s+\d+\s+[\d.]+\s+FAILED: " + re.escape(why) + "$")
        # The pool went on: the next unit, on the same worker, ran and passed; the note has both.
        self.assertRegex(run.output, r"(?m)^===== test_st_slow\.AlsoSlow \(worker \d+, [\d.]+ s\): passed \(1 test\) =====$")
        self.assertLess(run.output.index("===== test_st_slow.Slow ("), run.output.index("===== test_st_slow.AlsoSlow ("),
                        "the unit after the fault ran first (the order of two units never timed: as named):\n"
                        + run.output)
        self.assertIn("pool: failed: test_st_slow.Slow\n", run.output)
        self.assertEqual(run.note, "1 tests, 1 failed: test_st_slow.Slow, 0 skipped", run.output)
        self.assertNotIn("Exception in thread", run.output)



class ThePhases(unittest.TestCase):
    """Main units first, on --jobs workers; then the quiet ones (POOL_QUIET True, a class, a test of a
    module split by test), on min(--jobs, 12) or --quiet-jobs; then those alone; each phase after the
    one before has ended; the table says each unit's phase."""

    def test_the_order_and_the_table(self):
        run = RUNS["phases"]
        self.assertEqual(run.wait(), 0, run.output)
        phases = {
            "test_st_main.MainA.test_it": "main", "test_st_main.MainB.test_it": "main",
            "test_st_split.Mixed.test_loud": "main",
            "test_st_quiet.QuietA.test_it": "quiet", "test_st_quiet.QuietB.test_it": "quiet",
            "test_st_split.Mixed.test_quiet": "quiet", "test_st_split.AllQuiet.test_one": "quiet",
            "test_st_lonely.Lonely.test_it": "alone",
        }
        units = {"test_st_main.MainA.test_it": "test_st_main.MainA", "test_st_main.MainB.test_it": "test_st_main.MainB",
                 "test_st_quiet.QuietA.test_it": "test_st_quiet.QuietA",
                 "test_st_quiet.QuietB.test_it": "test_st_quiet.QuietB",
                 "test_st_lonely.Lonely.test_it": "test_st_lonely.Lonely"}
        for test, phase in phases.items():
            with self.subTest(test):
                self.assertEqual(table_phase(run.output, units.get(test, test)), phase)
        times = stamps(run)
        self.assertEqual(sorted(times), sorted(phases), run.output)
        ends = {phase: max(times[t][1] for t, ph in phases.items() if ph == phase) for phase in ("main", "quiet")}
        starts = {phase: min(times[t][0] for t, ph in phases.items() if ph == phase) for phase in ("quiet", "alone")}
        self.assertGreaterEqual(starts["quiet"], ends["main"], "a quiet unit started before the main phase ended")
        self.assertGreaterEqual(starts["alone"], ends["quiet"], "the unit alone started before the quiet phase ended")
        self.assertIn("pool: 3 unit(s) on 3 worker(s), then 4 quiet on 3 worker(s), then 1 alone: "
                      "test_st_lonely.Lonely\n", run.output)
        self.assertRegex(run.output, r"pool: 8 unit\(s\), 3 worker\(s\), 3 quiet, [\d.]+ s \(main [\d.]+ s, "
                                     r"quiet [\d.]+ s, alone [\d.]+ s\)")
        self.assertEqual(run.note, "8 tests, 0 skipped", run.output)
        self.assertNotIn("gate", run.output)              # none asked for: none waited for

    def test_the_quiet_width(self):
        many = RUNS["many"]
        self.assertEqual(many.wait(), 0, many.output)
        self.assertIn("pool: 0 unit(s) on 1 worker(s), then 13 quiet on 12 worker(s)\n", many.output)
        few = RUNS["fewquiet"]
        self.assertEqual(few.wait(), 0, few.output)
        self.assertIn("pool: 0 unit(s) on 1 worker(s), then 13 quiet on 2 worker(s)\n", few.output)
        workers = set(re.findall(r"(?m)^test_st_many\.Many\d\d\s+quiet\s+(\d+)\s", few.output))
        self.assertEqual(len(workers), 2, few.output)
        for name in ("quietjobs0", "quietjobs65"):
            run = RUNS[name]
            self.assertEqual(run.wait(), 2, run.output)
            self.assertIn("--quiet-jobs: 1 to 64", run.output)
            self.assertIsNone(run.note)

    def test_a_marker_that_cannot_be(self):
        run = RUNS["badquiet"]
        self.assertEqual(run.wait(), 1, run.output)
        self.assertIn("pool: test_st_badquiet_a.POOL_QUIET: 'yes' is not True or a tuple of names", run.output)
        self.assertIn("pool: test_st_badquiet_b.POOL_QUIET: 'Whole.test_x' is a test of a class that runs as one "
                      "unit (no POOL_SPLIT): list the class, 'Whole'", run.output)
        self.assertIn("pool: test_st_badquiet_c.POOL_QUIET: 'Nothing' names no test class or test of the module",
                      run.output)
        # As for POOL_BUILDS: every unit failed, none run, and each marker under its name.
        self.assertEqual(run.note, "0 tests, 6 failed: test_st_badquiet_a.A, test_st_badquiet_b.Whole, test_st_badquiet_c.C, "
                                   "test_st_badquiet_a.POOL_QUIET, test_st_badquiet_b.POOL_QUIET, "
                                   "test_st_badquiet_c.POOL_QUIET, 0 skipped", run.output)
        self.assertNotIn("unit(s) on", run.output)


class TheGate(unittest.TestCase):
    """--gate FILE: the quiet and alone phases (and what --after-gate names) wait until FILE exists,
    saying so; its "leave-out NAME: why" lines leave units out, not run and not in the note, the
    table saying why; another line is said and ignored."""

    def test_the_wait_and_what_it_leaves_out(self):
        if "gate" in SETUP_ERRORS:
            raise SETUP_ERRORS["gate"]
        run = RUNS["gate"]
        self.assertEqual(run.wait(), 0, run.output)
        self.assertEqual(GATE_SEEN["started before"], ["test_st_main.MainA"], run.output)
        self.assertIn("pool: 1 unit(s) on 1 worker(s), then 3 quiet on 2 worker(s) after the gate, then 1 alone: "
                      "test_st_lonely.Lonely\n", run.output)
        self.assertRegex(run.output, r"pool: the quiet units \(3\) and the units alone \(1\) wait for the gate, "
                                     r"\S*the-gate \(tools/schedule\.py creates it")
        self.assertRegex(run.output, r"pool: the gate opened after [\d.]+ s\n")
        self.assertIn("pool: test_st_quiet.QuietB left out by the gate: the controller's own tests failed\n", run.output)
        self.assertIn("pool: the gate ", run.output)
        odd = "not a line of the gate's"
        self.assertIn(f"has a line that is not \"leave-out NAME: why\": {odd!r}\n", run.output)
        self.assertEqual(sorted(stamps(run)), ["test_st_lonely.Lonely", "test_st_main.MainA", "test_st_main.MainB",
                                               "test_st_quiet.QuietA"], run.output)
        self.assertEqual(table_phase(run.output, "test_st_main.MainB"), "quiet")         # by --after-gate
        self.assertRegex(run.output, r"(?m)^test_st_quiet\.QuietB\s+quiet\s+left out: the controller's own tests "
                                     r"failed$")
        self.assertEqual(run.note, "4 tests, 0 skipped", run.output)
        self.assertNotIn("pool: failed:", run.output)


class PrebuildOnly(unittest.TestCase):
    """--prebuild-only builds what the units need and exits, no unit run: 0 and no note when it built,
    1 and the note of a pool whose prebuild failed when it did not."""

    def test_built(self):
        run = RUNS["prebuildonly"]
        self.assertEqual(run.wait(), 0, run.output)
        self.assertRegex(run.output, r"prebuild: tools/build\.sh st_app: built in [\d.]+ s")
        self.assertIn("pool: --prebuild-only: built what the 1 unit(s) need, no unit run\n", run.output)
        self.assertNotIn("<<Builds>>", run.output)
        self.assertNotIn("unit(s) on", run.output)
        self.assertIsNone(run.note, run.output)

    def test_failed(self):
        run = RUNS["prebuildonlyfails"]
        self.assertEqual(run.wait(), 1, run.output)
        self.assertIn("prebuild: tools/build.sh st_app FAILED (exit 2)", run.output)
        self.assertIn("pool: --prebuild-only: the prebuild failed: none of the 1 unit(s) can run\n", run.output)
        self.assertEqual(run.note, "0 tests, 1 failed: test_st_builds.Builds, 0 skipped", run.output)
        self.assertNotIn("<<Builds>>", run.output)


class TheSerialRunnersLock(unittest.TestCase):
    """tools/test.sh's serial runner (pool.py --hold-checkout, then VPAIR_RUNNER) holds the checkout's
    lock as a pool does: it waits for a pool, naming it, a pool waits for it, naming it; SIGTERM while
    it waits ends it there, nothing run; what it leaves running does not keep the lock."""

    def test_it_waits_for_a_pool(self):
        if "serial" in SETUP_ERRORS:
            raise SETUP_ERRORS["serial"]
        holder, runner = RUNS["serial-holder"], RUNS["serial"]
        self.assertEqual(holder.wait(), 0, holder.output)
        self.assertEqual(runner.wait(), 0, runner.output)
        self.assertEqual(SERIAL_SEEN["started while held"], [], runner.output)
        self.assertRegex(runner.output, rf"serial runner: another run holds this checkout: pool\.py pid {holder.pid} "
                                        r".*--jobs 1 test_st_slow\.Slow; waiting for it to end")
        self.assertRegex(runner.output, r"serial runner: the other run ended; this one starts after waiting [\d.]+ s")
        self.assertEqual(runner.note, "1 tests, 0 skipped", runner.output)
        start = stamps(runner)["test_st_serial.Serial"][0]
        self.assertGreaterEqual(start, float((holder.stamps / "end").read_text()))

    def test_what_it_leaves_running_does_not_hold_the_lock(self):
        if "serial" in SETUP_ERRORS:
            raise SETUP_ERRORS["serial"]
        runner = RUNS["serial"]
        runner.wait()
        child = int((runner.stamps / "child").read_text())
        self.addCleanup(kill_if_running, child)
        self.assertFalse(gone(child), "the runner's background child already ended: nothing proven")
        self.assertTrue(lock_is_free(runner.files), "the serial runner's child still holds the checkout's lock")

    def test_sigterm_while_it_waits(self):
        if "serial" in SETUP_ERRORS:
            raise SETUP_ERRORS["serial"]
        term = RUNS["serial-term"]
        self.assertEqual(term.wait(), -signal.SIGTERM, term.output)
        self.assertIn("serial runner: SIGTERM while waiting: nothing ran\n", term.output)
        self.assertEqual(stamps(term), {}, term.output)
        self.assertIsNone(term.note, term.output)

    def test_a_pool_waits_for_it(self):
        if "serial_holds" in SETUP_ERRORS:
            raise SETUP_ERRORS["serial_holds"]
        runner, pool_run = RUNS["serial-runner"], RUNS["pool-after-serial"]
        self.assertEqual(runner.wait(), 0, runner.output)
        self.assertEqual(pool_run.wait(), 0, pool_run.output)
        self.assertFalse(SERIAL_SEEN["pool started while held"], pool_run.output)
        self.assertRegex(pool_run.output, rf"pool: another run holds this checkout: the serial runner, pid {runner.pid} "
                                          r"\((in|outside) the container\), since [\d:]+( \S+)?, --jobs 1 "
                                          r"test_st_slow\.Slow; waiting for it to end")
        self.assertGreaterEqual(float((pool_run.stamps / "start").read_text()),
                                float((runner.stamps / "end").read_text()))
        self.assertEqual(runner.note, "1 tests, 0 skipped", runner.output)

if __name__ == "__main__":
    unittest.main(verbosity=2)
