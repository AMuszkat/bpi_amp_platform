"""A virtual display for the controller's tests: Xvfb on the first free number from :300 up,
with openbox, as dsp/juce_apps/plugin-check/run.sh does from :200 (standard library only).

Never :0, which Xvfb's -displayfd alone would take: a JUCE program with no DISPLAY opens :0.0
and exits when that server stops. :100 to :199 are the vpair pool's workers', :200 to :299
plugin-check's. Given a number, Xvfb writes it to the -displayfd pipe once it accepts
connections, and exits at once, writing nothing, when an X server already listens there (it
keeps no lock file with -displayfd: the socket decides), so that runs at the same time each take
the next number.

Inside a run of tests/runner.py each worker has one already: $CONTROLLER_TEST_DISPLAY names it,
and VirtualDisplay uses it instead of starting another.
"""

import os
import select
import shutil
import subprocess
import tempfile
import time
import unittest

FIRST, LAST = 300, 399


class VirtualDisplay:
    """Xvfb (from :300 up) and openbox, for a `with` block; `env` is {"DISPLAY": ...}. Raises
    unittest.SkipTest when either is missing, AssertionError when none starts within `timeout`
    seconds. With $CONTROLLER_TEST_DISPLAY set (a worker's), that display, started by the
    runner."""

    def __init__(self, size="1720x1000x24", timeout=10):
        self.size, self.timeout, self.processes, self.env, self.tmp = size, timeout, [], {}, None
        self.number = None

    def __enter__(self):
        given = os.environ.get("CONTROLLER_TEST_DISPLAY")
        if given:
            self.env = {"DISPLAY": given}
            return self
        for tool in ("Xvfb", "openbox"):
            if not shutil.which(tool):
                raise unittest.SkipTest("no %s: the GUI test needs Xvfb and openbox" % tool)
        try:
            self.tmp = tempfile.mkdtemp(prefix="controller-display-")
            deadline = time.monotonic() + self.timeout
            log_path = os.path.join(self.tmp, "xvfb.log")
            for n in range(FIRST, LAST + 1):
                if time.monotonic() > deadline:
                    break
                if self._try(n, log_path, deadline):
                    break
            if self.number is None:
                with open(log_path) as log:
                    raise AssertionError("Xvfb did not start on any of :%d to :%d within %d s:\n%s"
                                         % (FIRST, LAST, self.timeout, log.read()[-2000:]))
            self.env = {"DISPLAY": ":%d" % self.number}
            ready = os.path.join(self.tmp, "openbox-started")
            # openbox runs its --startup command once it manages the screen.
            self.processes.append(subprocess.Popen(["openbox", "--startup", "touch " + ready],
                                                   env=dict(os.environ, **self.env), stdin=subprocess.DEVNULL,
                                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            deadline = time.monotonic() + self.timeout
            while not os.path.exists(ready):
                if self.processes[-1].poll() is not None or time.monotonic() > deadline:
                    raise AssertionError("openbox did not start on %s within %d s" % (self.env["DISPLAY"], self.timeout))
                time.sleep(0.02)
            return self
        except BaseException:
            self.__exit__()
            raise

    def _try(self, n, log_path, deadline):
        """Xvfb on :n: True once it takes connections; False (and it is gone) when it exits
        first, as it does when :n is taken."""
        read, write = os.pipe()
        with open(log_path, "a") as log:
            log.write("--- Xvfb :%d\n" % n)
            log.flush()
            process = subprocess.Popen(["Xvfb", ":%d" % n, "-displayfd", str(write), "-screen", "0", self.size,
                                        "-nolisten", "tcp"],
                                       pass_fds=(write,), stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        os.close(write)
        written = b""
        with os.fdopen(read, "rb") as pipe:
            while not written.endswith(b"\n"):
                left = deadline - time.monotonic()
                if left <= 0 or not select.select([pipe], [], [], left)[0]:
                    break
                chunk = os.read(pipe.fileno(), 64)
                if not chunk:          # Xvfb exited without a number: :n is taken
                    break
                written += chunk
        if written.strip() == str(n).encode():
            self.processes.append(process)
            self.number = n
            return True
        process.kill()
        process.wait()
        return False

    def __exit__(self, *exc):
        for process in reversed(self.processes):
            process.terminate()
            try:
                process.wait(5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        self.processes = []
        if self.tmp:
            shutil.rmtree(self.tmp, ignore_errors=True)
            self.tmp = None
