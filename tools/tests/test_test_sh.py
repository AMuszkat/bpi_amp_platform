#!/usr/bin/env python3
"""tools/test.sh's arguments: what it refuses (status 2, before it runs anything), and that --jobs
and --no-record are taken wherever they stand. No virtual pair and no build: test.sh runs natively
here (BPI_CONTAINER=0; inside the development container it does anyway), and with
BPI_TEST_DRY_RUN=1 it prints what its arguments decided and stops with status 3 (DRY_RUN), so that
an argument it should refuse but takes runs nothing either, and a stray BPI_TEST_DRY_RUN fails a
run instead of passing it. Part of the `tools` check (tools/test.sh tools).

    python3 tools/tests/test_test_sh.py
"""

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEST_SH = ROOT / "tools" / "test.sh"
MAX_JOBS = 64           # tests/vpair/pool.py's limit, what --jobs and BPI_TEST_JOBS take
MAX_DEFAULT_JOBS = 16   # the default's limit: the container's tasks (pids.max), not the pool's
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
    return min(max(processors // 2, 1), MAX_DEFAULT_JOBS)


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

    def test_the_default_is_half_the_processors_at_most_16(self):
        # A busy worker takes 65 to 75 of the container's 2048 tasks: the default stops at 16 from
        # 32 processors up, far below the pool's 64, which an explicit number may still ask for.
        with tempfile.TemporaryDirectory(prefix="test-sh-") as tmp:
            nproc = Path(tmp) / "nproc"
            for processors, want in (("256", "16"), ("129", "16"), ("128", "16"), ("33", "16"), ("32", "16"),
                                     ("31", "15"), ("2", "1"), ("1", "1")):
                nproc.write_text(f"#!/bin/sh\necho {processors}\n")
                nproc.chmod(nproc.stat().st_mode | stat.S_IXUSR)
                with self.subTest(processors=processors):
                    self.assertEqual(self.taken("tools", path=tmp)["jobs"], want)
            self.assertEqual(self.taken("tools", "--jobs", "40", path=tmp)["jobs"], "40")
            self.assertEqual(self.taken("tools", jobs="40", path=tmp)["jobs"], "40")
            self.assertEqual(self.taken("tools", path=tmp, ci="true")["jobs"], "1")


if __name__ == "__main__":
    unittest.main()
