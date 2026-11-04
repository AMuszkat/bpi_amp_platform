#!/usr/bin/env python3
"""tools/jobserver.sh, in a second: the command's exit status passed through (and its death by a
signal), 127 and 126 for a command that cannot be run; the jobserver it serves: MAKEFLAGS'
format, its two descriptors open in the command (inheritable) and nothing else leaked, the read
end of a pipe and non-blocking, nproc - 1 tokens in it; the process state python would change
put back (SIGPIPE and SIGXFSZ at their default, or ignored when the script was given them
ignored; the signal mask; no locale coercion); and the fallbacks, where the command runs as it
is: no GNU make 4.2 or later, no python3, a jobserver already open. Part of the `tools` check
(tools/test.sh tools).

    python3 tools/tests/test_jobserver.py

`make` is a stand-in that only says its version (all the script asks of it), in a PATH of its
own, so no result depends on the make installed. Needs Linux's /proc.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JOBSERVER = ROOT / "tools" / "jobserver.sh"

# What the command sees, as JSON: MAKEFLAGS and MFLAGS, LC_CTYPE and PYTHONCOERCECLOCALE, from the
# environment it was exec'd with (/proc's environ: the probe's own python coerces the locale too);
# the descriptors open in it; and, when MAKEFLAGS names a pipe jobserver, its read end's kind and
# flags and the tokens it holds (read until a read finds none, which must fail at once rather than
# block; none read from a blocking one). Its signals the probe cannot tell (python ignores SIGPIPE and SIGXFSZ at its start): a grep
# reads them (signals()).
PROBE = r'''
import json, os, re, stat, sys, fcntl
environ = dict(e.split(b"=", 1) for e in open("/proc/self/environ", "rb").read().split(b"\0") if b"=" in e)
def var(name):
    v = environ.get(name.encode())
    return None if v is None else v.decode()
out = {"makeflags": var("MAKEFLAGS"), "mflags": var("MFLAGS"), "lc_ctype": var("LC_CTYPE"),
       "coerce": var("PYTHONCOERCECLOCALE")}
names = os.listdir("/proc/self/fd")
fds = []
for n in names:
    try:
        os.fstat(int(n))
        fds.append(int(n))
    except OSError:
        pass                                    # the listing's own descriptor, closed by now
out["fds"] = sorted(fds)
m = re.search(r"--jobserver-auth=(\d+),(\d+)", out["makeflags"] or "")
if m:
    r, w = int(m.group(1)), int(m.group(2))
    out["read_fifo"] = stat.S_ISFIFO(os.fstat(r).st_mode)
    out["write_fifo"] = stat.S_ISFIFO(os.fstat(w).st_mode)
    out["read_nonblock"] = bool(fcntl.fcntl(r, fcntl.F_GETFL) & os.O_NONBLOCK)
    tokens = 0
    while out["read_nonblock"]:                 # a blocking read end would hang here: none read
        try:
            got = os.read(r, 1)
        except BlockingIOError:
            break
        if not got:
            break
        tokens += 1
        if tokens > 100000:
            break
    out["tokens"] = tokens
print(json.dumps(out))
'''

MAKEFLAGS_RE = re.compile(r"^ -j(\d+) --jobserver-auth=(\d+),(\d+)$")


def bit(sig):
    return 1 << (int(sig) - 1)


def nproc():
    return int(subprocess.run(["nproc"], capture_output=True, text=True, check=True).stdout)


@unittest.skipUnless(Path("/proc/self/status").is_file(), "needs Linux's /proc")
class Jobserver(unittest.TestCase):

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="test_jobserver."))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.probe = self.dir / "probe.py"
        self.probe.write_text(PROBE)

    def bin(self, make="GNU Make 4.4.1", python=True):
        """A PATH of its own: the tools the script and the tests run (sed, nproc, sh, bash, yes, head, grep
        and, unless `python` is false, python3) and a stand-in make that says `make` as its first line (none when None)."""
        d = Path(tempfile.mkdtemp(dir=self.dir, prefix="bin."))
        for tool in ("sed", "nproc", "sh", "bash", "yes", "head", "grep") + (("python3",) if python else ()):
            os.symlink(shutil.which(tool), d / tool)
        if make is not None:
            (d / "make").write_text(f"#!/bin/sh\necho '{make}'\necho 'Built for x86_64-pc-linux-gnu'\n")
            (d / "make").chmod(0o755)
        return str(d)

    def env(self, path=None, **extra):
        env = {k: v for k, v in os.environ.items() if k not in ("MAKEFLAGS", "MFLAGS", "PYTHONCOERCECLOCALE")}
        env["PATH"] = path or self.bin()
        env.update(extra)
        return env

    def run_js(self, *command, env=None, **kwargs):
        return subprocess.run([str(JOBSERVER), *command], env=env or self.env(), capture_output=True,
                              text=True, timeout=30, **kwargs)

    def see(self, env=None, through=True, **kwargs):
        """What the probe sees, run through the script (or, `through` false, directly)."""
        command = [sys.executable, "-I", str(self.probe)]
        if through:
            p = self.run_js(*command, env=env, **kwargs)
        else:
            p = subprocess.run(command, env=env or self.env(), capture_output=True, text=True, timeout=30, **kwargs)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def signals(self, through=True, **kwargs):
        """(ignored, blocked): the masks of a grep's signals (/proc's SigIgn and SigBlk), run through
        the script (or, `through` false, directly)."""
        command = ["grep", "^Sig", "/proc/self/status"]
        if through:
            p = self.run_js(*command, **kwargs)
        else:
            p = subprocess.run(command, env=self.env(), capture_output=True, text=True, timeout=30, **kwargs)
        self.assertEqual(p.returncode, 0, p.stderr)
        masks = dict(re.findall(r"^Sig(\w+):\s*(\w+)$", p.stdout, re.M))
        return int(masks["Ign"], 16), int(masks["Blk"], 16)

    # --- The command's exit -------------------------------------------------------------------

    def test_exit_status_passed_through(self):
        for rc in (0, 1, 7, 255):
            self.assertEqual(self.run_js("sh", "-c", f"exit {rc}").returncode, rc)
        # Exec'd, not run under anything: a signal that kills the command ends the script with it.
        self.assertEqual(self.run_js("sh", "-c", "kill -TERM $$").returncode, -signal.SIGTERM)
        # Its output is its own.
        p = self.run_js("sh", "-c", "echo out; echo err >&2")
        self.assertEqual((p.stdout, p.stderr), ("out\n", "err\n"))

    def test_a_command_that_cannot_be_run(self):
        p = self.run_js("no-such-command-here")
        self.assertEqual(p.returncode, 127)
        self.assertIn("tools/jobserver.sh: no-such-command-here: No such file or directory", p.stderr)
        plain = self.dir / "not-executable"
        plain.write_text("#!/bin/sh\nexit 0\n")
        plain.chmod(0o644)
        p = self.run_js(str(plain))
        self.assertEqual(p.returncode, 126)
        self.assertIn(f"tools/jobserver.sh: {plain}: Permission denied", p.stderr)

    def test_usage(self):
        p = self.run_js()
        self.assertEqual(p.returncode, 2)
        self.assertIn("usage:", p.stderr)

    # --- The jobserver ------------------------------------------------------------------------

    def test_the_jobserver_it_serves(self):
        seen = self.see(env=self.env(MFLAGS="-j3"))
        m = MAKEFLAGS_RE.match(seen["makeflags"] or "")
        self.assertIsNotNone(m, seen["makeflags"])
        jobs, r, w = (int(g) for g in m.groups())
        self.assertEqual(jobs, nproc())
        self.assertIsNone(seen["mflags"])
        # Both descriptors inherited, and nothing else beside what a direct run has.
        direct = self.see(through=False)
        self.assertIn(r, seen["fds"])
        self.assertIn(w, seen["fds"])
        self.assertEqual(sorted(set(seen["fds"]) - {r, w}), direct["fds"])
        self.assertTrue(seen["read_fifo"] and seen["write_fifo"])
        self.assertTrue(seen["read_nonblock"])
        # The command holds the last token without taking it, as make's first job does.
        self.assertEqual(seen["tokens"], jobs - 1)

    def test_make_versions(self):
        for version, serves in (("GNU Make 4.2", True), ("GNU Make 4.2.1", True), ("GNU Make 4.10", True),
                                ("GNU Make 5.0", True), ("GNU Make 4.1", False), ("GNU Make 3.82", False),
                                ("bmake 20200710", False), (None, False)):
            seen = self.see(env=self.env(path=self.bin(make=version)))
            if serves:
                self.assertRegex(seen["makeflags"] or "", MAKEFLAGS_RE, version)
            else:
                self.assertIsNone(seen["makeflags"], version)

    def test_no_python3(self):
        # The probe runs by its absolute path; python3 is missing from the script's PATH only.
        seen = self.see(env=self.env(path=self.bin(python=False)))
        self.assertIsNone(seen["makeflags"])
        self.assertEqual(self.run_js("sh", "-c", "exit 5", env=self.env(path=self.bin(python=False))).returncode, 5)

    def test_an_inherited_jobserver(self):
        r, w = os.pipe()
        os.set_blocking(r, False)                         # as make's is
        self.addCleanup(os.close, r)
        self.addCleanup(os.close, w)
        os.write(w, b"++")
        theirs = f"-j3 --jobserver-auth={r},{w}"
        seen = self.see(env=self.env(MAKEFLAGS=theirs), pass_fds=(r, w))
        self.assertEqual(seen["makeflags"], theirs)       # shared, as a recursive make does
        self.assertEqual(seen["tokens"], 2)
        # Named but not open here (a make's recipe run without '+'): a jobserver of ours instead.
        seen = self.see(env=self.env(MAKEFLAGS=theirs))
        self.assertRegex(seen["makeflags"], MAKEFLAGS_RE)
        self.assertEqual(seen["tokens"], nproc() - 1)
        # make 4.4's fifo: shared when it exists, ours when it does not.
        fifo = self.dir / "fifo"
        os.mkfifo(fifo)
        theirs = f"-j3 --jobserver-auth=fifo:{fifo}"
        self.assertEqual(self.see(env=self.env(MAKEFLAGS=theirs))["makeflags"], theirs)
        gone = f"-j3 --jobserver-auth=fifo:{self.dir / 'no-fifo'}"
        self.assertRegex(self.see(env=self.env(MAKEFLAGS=gone))["makeflags"], MAKEFLAGS_RE)

    # --- What python would change -------------------------------------------------------------

    def test_signals_as_given(self):
        both = bit(signal.SIGPIPE) | bit(signal.SIGXFSZ)
        # Given at their default (subprocess restores them in the child): at their default, though
        # python ignores both; and a write to a closed pipe kills the writer.
        direct, seen = self.signals(through=False), self.signals()
        self.assertEqual(seen[0] & both, 0)
        self.assertEqual(seen, direct)
        p = self.run_js("bash", "-c", 'yes | head -c 1 > /dev/null; echo "${PIPESTATUS[0]}"')
        self.assertEqual(p.stdout.strip(), str(128 + signal.SIGPIPE), p.stderr)
        # Given ignored (this process ignores both, and SIGINT too): still ignored.
        old = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            direct = self.signals(through=False, restore_signals=False)
            seen = self.signals(restore_signals=False)
        finally:
            signal.signal(signal.SIGINT, old)
        self.assertEqual(direct[0] & both, both)
        self.assertTrue(direct[0] & bit(signal.SIGINT))
        self.assertEqual(seen, direct)
        # A blocked signal stays blocked, and python blocks none of its own.
        def block():
            signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGUSR1})
        direct, seen = self.signals(through=False, preexec_fn=block), self.signals(preexec_fn=block)
        self.assertTrue(direct[1] & bit(signal.SIGUSR1))
        self.assertEqual(seen, direct)

    def test_no_locale_coercion(self):
        env = {k: v for k, v in self.env().items() if not k.startswith("LC_")}
        env["LANG"] = "C"
        seen = self.see(env=env)
        self.assertIsNone(seen["lc_ctype"])
        self.assertIsNone(seen["coerce"])
        self.assertEqual(self.see(env=dict(env, PYTHONCOERCECLOCALE="1"))["coerce"], "1")
        self.assertEqual(self.see(env=dict(env, PYTHONCOERCECLOCALE=""))["coerce"], "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
