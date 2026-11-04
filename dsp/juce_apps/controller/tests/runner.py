#!/usr/bin/env python3
"""Runs test_controller.py's tests several at a time (run.sh calls it; README.md, "Tests").

    python3 runner.py [--jobs N]

Each worker has its own virtual display (display.py: Xvfb from :300 up, and openbox), its own
fake hub addresses (worker k: 127.0.3.<2k+1> for the hub, 127.0.3.<2k+2> for the right speaker,
the same ports), and each test its own HOME (the controller's settings file). Each test runs in
a process of its own (`python3 -m unittest -v <test>`), so a test sees exactly what it sees when
run alone. The tests marked @alone in test_controller.py (a bound on a time or a rate) run after
the others, one at a time, on the first worker whose display started (alone among these tests,
not on the machine: in a schedule, beside the other phase-2 steps; README.md). The output is unittest's
-v output, one test after the other as they end, and its summary; the exit status is 0 when
every test passed.

A test that outlives TEST_TIMEOUT_S is killed and is an error; its worker goes on with the next.
A worker whose display does not start is an error and runs nothing (the others take its tests).
A test that did not run (no display started, the run stopped) is an error named in the summary,
with why. SIGTERM, SIGHUP and Ctrl-C stop the run: every test's session killed, every display
stopped, the summary printed; exit 143, 129, 130.

N: --jobs, or $BPI_CONTROLLER_JOBS, or 1 where $CI is set, or DEFAULT_JOBS. Standard library only.
"""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
sys.path.insert(0, HERE)
# Each test's last duration, to start the longest first (a test not in it counts as long).
DURATIONS = os.path.join(ROOT, "build", "controller-check", "durations.json")

from display import VirtualDisplay  # noqa: E402

# 6 at a time: the run's wall time stops falling there (README.md, "Tests", has the measurement),
# and it holds about 25 tasks per worker against the container's 2048 beside the vpair pool.
DEFAULT_JOBS = 6
MAX_JOBS = 63                      # the address block: 127.0.3.1 to 127.0.3.126
TEST_TIMEOUT_S = 600               # test_12 alone may take 180 s on a slow runner
SEPARATOR1, SEPARATOR2 = "=" * 70, "-" * 70


def load_durations():
    try:
        with open(DURATIONS) as f:
            return {k: float(v) for k, v in json.load(f).items()}
    except (OSError, ValueError, AttributeError):
        return {}


def save_durations(durations, results):
    durations = dict(durations, **{r.test: round(r.seconds, 2) for r in results})
    try:
        os.makedirs(os.path.dirname(DURATIONS), exist_ok=True)
        with open(DURATIONS + ".tmp", "w") as f:
            json.dump(durations, f, indent=1, sort_keys=True)
        os.replace(DURATIONS + ".tmp", DURATIONS)
    except OSError:
        pass


def default_jobs():
    given = os.environ.get("BPI_CONTROLLER_JOBS", "")
    if given:
        return int(given)
    return 1 if os.environ.get("CI") else DEFAULT_JOBS


def tests_of_module():
    """The test ids of test_controller.py in file order, and {id: why} of those run alone."""
    import test_controller
    suite = unittest.defaultTestLoader.loadTestsFromModule(test_controller)
    ids, alone = [], {}

    def walk(s):
        for t in s:
            if isinstance(t, unittest.TestSuite):
                walk(t)
            else:
                ids.append(t.id())
                why = getattr(getattr(t, t._testMethodName, None), "run_alone", None) \
                    or getattr(type(t), "run_alone", None)
                if why:
                    alone[t.id()] = why
    walk(suite)
    return ids, alone


class Result:
    def __init__(self, test, worker):
        self.test, self.worker = test, worker
        self.ran, self.failures, self.errors, self.skipped = 1, 0, 0, 0
        self.head, self.report, self.blocks, self.seconds = "", "", "", 0.0


def parse(result, code, out, err):
    """One test's unittest output: its -v line(s), its failure blocks, its counts."""
    lines = err.splitlines()
    try:
        end = max(i for i, l in enumerate(lines) if l == SEPARATOR2 and i + 1 < len(lines) and lines[i + 1].startswith("Ran "))
    except ValueError:
        end = None
    if end is None:      # no summary: the process died or timed out
        result.errors = 1
        result.head = "%s ... ERROR" % result.test
        result.blocks = "%s\nERROR: %s (no unittest summary, exit status %s)\n%s\n%s\n%s" % (
            SEPARATOR1, result.test, code, SEPARATOR2, err[-6000:], out[-2000:])
        return result
    start = next((i for i, l in enumerate(lines[:end]) if l == SEPARATOR1), end)
    result.head = "\n".join(lines[:start]).strip("\n")
    result.blocks = "\n".join(lines[start:end]).strip("\n")
    status = lines[-1] if lines else ""
    counts = dict((k, int(v)) for k, v in re.findall(r"(\w+)=(\d+)", status))
    result.failures, result.errors = counts.get("failures", 0), counts.get("errors", 0)
    result.skipped = counts.get("skipped", 0)
    if not status.startswith("OK") and not (result.failures or result.errors):
        result.errors = 1
    result.report = "\n".join("    " + l.strip() for l in out.splitlines() if l.strip())
    return result


class Worker:
    """A display, an address pair and a temporary directory; runs one test at a time."""

    def __init__(self, index, tmp):
        self.index = index
        self.left, self.right = "127.0.3.%d" % (2 * index + 1), "127.0.3.%d" % (2 * index + 2)
        self.tmp = os.path.join(tmp, "worker-%d" % index)
        os.makedirs(self.tmp)
        self.display = None
        self.display_error = None      # why its display did not start: it runs nothing
        self.process = None
        self.lock = threading.Lock()
        self.stopping = False

    def start_display(self):
        """True when the worker can run tests: its display started, or there is no Xvfb or
        openbox (the GUI tests skip themselves). False, with display_error, when it failed."""
        try:
            self.display = VirtualDisplay().__enter__()
        except unittest.SkipTest:
            self.display = None      # no Xvfb or openbox: the GUI tests skip themselves
        except Exception as e:       # none started within its timeout, or Xvfb/openbox failed
            self.display = None
            self.display_error = "%s: %s" % (type(e).__name__, e)
            return False
        self.display_error = None
        return True

    def stop_display(self):
        if self.display is not None:
            self.display.__exit__()
            self.display = None

    def where(self):
        shown = self.display.env["DISPLAY"] if self.display else "no display"
        return "worker %d (%s, %s)" % (self.index, shown, self.left)

    def run(self, test):
        result = Result(test, self)
        home = os.path.join(self.tmp, test.rsplit(".", 1)[-1])
        os.makedirs(home)
        env = dict(os.environ, CONTROLLER_TEST_LEFT=self.left, CONTROLLER_TEST_RIGHT=self.right,
                   HOME=home, XDG_CONFIG_HOME=os.path.join(home, ".config"), PYTHONUNBUFFERED="1")
        env.pop("CONTROLLER_TEST_DISPLAY", None)
        if self.display is not None:
            env["DISPLAY"] = env["CONTROLLER_TEST_DISPLAY"] = self.display.env["DISPLAY"]
        t0 = time.monotonic()
        with self.lock:
            if self.stopping:
                return None
            # Its own session: on a timeout or an interrupt the test's controller goes with it.
            self.process = subprocess.Popen([sys.executable, "-m", "unittest", "-v", test], cwd=HERE, env=env,
                                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            text=True, start_new_session=True)
        try:
            out, err = self.process.communicate(timeout=TEST_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            self.kill_current()      # this test only: the worker goes on with the next
            out, err = self.process.communicate()
            err += "\n(killed after %d s)" % TEST_TIMEOUT_S
        if self.stopping and self.process.returncode == -signal.SIGKILL:
            err += "\n(killed: the run was stopped)"
        code, self.process = self.process.returncode, None
        result.seconds = time.monotonic() - t0
        return parse(result, code, out, err)

    def kill_current(self):
        """Kills the test running now, and what it started (its session)."""
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def kill(self):
        """The run stops: the test running now is killed, and the worker starts no other."""
        with self.lock:
            self.stopping = True
        self.kill_current()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--jobs", type=int, default=None, help="tests at a time (default: %d; 1 where CI is set)" % DEFAULT_JOBS)
    args = parser.parse_args()
    jobs = args.jobs if args.jobs is not None else default_jobs()
    if not 1 <= jobs <= MAX_JOBS:
        parser.error("--jobs: 1 to %d" % MAX_JOBS)

    ids, alone = tests_of_module()
    together = [t for t in ids if t not in alone]
    jobs = max(1, min(jobs, len(together)))
    durations = load_durations()
    if jobs > 1:     # the longest first (one at a time: the file's order)
        together.sort(key=lambda t: -durations.get(t, 1e9))
    print("%d tests: %d at a time, then %d alone, one at a time" % (len(ids), jobs, len(alone)), file=sys.stderr, flush=True)

    out_lock = threading.Lock()
    results = []

    def show(result):
        if result is None:
            return
        with out_lock:
            results.append(result)
            text = result.head
            if result.report:
                text += "\n" + result.report
            text += "\n    (%.1f s, %s)" % (result.seconds, result.worker.where())
            print(text, file=sys.stderr, flush=True)

    # Stopped (SIGTERM, or SIGHUP: a closed terminal, or tools/schedule.py passing either on to
    # every step's session; as for Ctrl-C): the finally below stops every test and display.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(128 + signal.SIGTERM))
    signal.signal(signal.SIGHUP, lambda *_: sys.exit(128 + signal.SIGHUP))
    t0 = time.monotonic()
    tmp = tempfile.mkdtemp(prefix="controller-tests-")
    workers = [Worker(k, tmp) for k in range(jobs)]
    queue, queue_lock = list(together), threading.Lock()

    display_errors = []        # [(worker index, why)]: each an error of the run
    not_run = {}               # {test: why}: each an error, named in the summary

    def run_one(worker, test):
        try:
            show(worker.run(test))
        except Exception:          # the runner's own failure (a HOME it cannot make, ...): this test's error
            result = Result(test, worker)
            result.errors = 1
            result.head = "%s ... ERROR" % test
            result.blocks = "%s\nERROR: %s (the runner failed to run it)\n%s\n%s" % (
                SEPARATOR1, test, SEPARATOR2, traceback.format_exc())
            show(result)

    def start_display(worker):
        if worker.start_display():
            return True
        with out_lock:
            display_errors.append((worker.index, worker.display_error))
            print("-- worker %d: its display did not start, it runs nothing: %s" % (worker.index, worker.display_error),
                  file=sys.stderr, flush=True)
        return False

    def work(worker):
        if not start_display(worker):
            return             # the others take its tests
        try:
            while not worker.stopping:
                with queue_lock:
                    if not queue:
                        break
                    test = queue.pop(0)
                run_one(worker, test)
        finally:
            if worker.index != 0:
                worker.stop_display()

    def alone_worker():
        """Worker 0 with its display; else the first other whose display started, its display
        started again (it stopped it); None when none starts."""
        if workers[0].display_error is None:
            return workers[0]
        for w in workers[1:]:
            if w.display_error is None and start_display(w):
                return w
        return None

    threads = [threading.Thread(target=work, args=(w,), daemon=True) for w in workers]
    stopped, code = None, None
    try:
        for t in threads:
            t.start()
        for t in threads:
            while t.is_alive():
                t.join(0.5)
        with queue_lock:       # left by workers whose displays all failed
            not_run.update((test, "no worker's display started") for test in queue)
            del queue[:]
        if alone:
            with out_lock:
                print("-- run alone, one at a time: %s" % ", ".join(
                    "%s (%s)" % (t.rsplit(".", 1)[-1], alone[t]) for t in ids if t in alone), file=sys.stderr, flush=True)
            w = alone_worker()
            for test in (t for t in ids if t in alone):
                if w is None:
                    not_run[test] = "no worker's display started"
                else:
                    run_one(w, test)
    except KeyboardInterrupt:
        stopped, code = "the run was interrupted", 130
    except SystemExit as e:    # SIGTERM (143) or SIGHUP (129)
        stopped, code = "the run was stopped", e.code
    finally:
        # A second SIGTERM or SIGHUP (schedule.py passes on each it gets) does not cut the cleanup short.
        for signum in (signal.SIGTERM, signal.SIGHUP):
            signal.signal(signum, signal.SIG_IGN)
        for w in workers:
            w.kill()
        for t in threads:      # each ends its test's communicate() and starts no other
            t.join(10)
        for w in workers:
            w.stop_display()
        shutil.rmtree(tmp, ignore_errors=True)
    elapsed = time.monotonic() - t0
    save_durations(durations, results)

    with out_lock:
        ran = {r.test for r in results}
        for test in ids:
            if test not in ran and test not in not_run:
                not_run[test] = stopped or "lost by the runner (a bug of runner.py)"
        failures = sum(r.failures for r in results)
        errors = sum(r.errors for r in results) + len(not_run) + len(display_errors)
        skipped = sum(r.skipped for r in results)
        order = {t: i for i, t in enumerate(ids)}
        blocks = ["%s\nERROR: the display of worker %d (it ran no test)\n%s\n%s" % (SEPARATOR1, k, SEPARATOR2, why)
                  for k, why in sorted(display_errors)]
        blocks += [r.blocks for r in sorted(results, key=lambda r: order[r.test]) if r.blocks]
        blocks += ["%s\nERROR: %s (not run: %s)" % (SEPARATOR1, test, not_run[test]) for test in ids if test in not_run]
        lines = [""] + blocks + [SEPARATOR2, "Ran %d tests in %.3fs" % (len(results), elapsed), ""]
        extra = [("failures", failures), ("errors", errors), ("skipped", skipped)]
        if failures or errors:
            lines.append("FAILED (%s)" % ", ".join("%s=%d" % kv for kv in extra if kv[1]))
        else:
            lines.append("OK" + (" (skipped=%d)" % skipped if skipped else ""))
        print("\n".join(lines), file=sys.stderr, flush=True)
    if code is not None:
        return code
    return 1 if failures or errors else 0


if __name__ == "__main__":
    sys.exit(main())
