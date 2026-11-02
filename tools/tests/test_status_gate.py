#!/usr/bin/env python3
"""tools/status.py (what green is, on a scratch git repository) and the pre-bash hook's lab
lock. Part of the `tools` check (tools/test.sh tools).

    python3 tools/tests/test_status_gate.py
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATUS_PY = ROOT / "tools" / "status.py"
GREEN = {"unit": "pass", "unit_tree": "clean", "vpair": "pass", "vpair_tree": "clean"}


class Scratch:
    """A git repository with tools/status.py and a few files."""

    def __init__(self, path):
        self.path = path
        (path / "tools").mkdir(parents=True)
        shutil.copy(STATUS_PY, path / "tools" / "status.py")
        self.write(".gitignore", "tests/.status/\n")
        self.write("README.md", "scratch\n")
        self.git("init", "-q", "-b", "main")
        self.commit("base")

    def write(self, rel, text):
        (self.path / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.path / rel).write_text(text)

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.path), "-c", "user.name=user",
                               "-c", "user.email=user@bpi-amp.local", *args],
                              capture_output=True, text=True, check=True).stdout.strip()

    def commit(self, message, files=None):
        for rel, text in (files or {}).items():
            self.write(rel, text)
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def record(self, commit, **fields):
        path = self.path / "tests" / ".status" / f"{commit}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(fields))

    def run(self, *args):
        return subprocess.run([sys.executable, str(self.path / "tools" / "status.py"), *args],
                              cwd=self.path, capture_output=True, text=True)

    def gate(self):
        result = self.run("gate")
        return result.returncode, result.stdout.strip()


class Status(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="status-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = Scratch(self.tmp / "repo")
        self.base = self.repo.git("rev-parse", "HEAD")
        self.repo.record(self.base, **GREEN)

    def green(self, said=""):
        rc, out = self.repo.gate()
        self.assertEqual(rc, 0, out)
        self.assertIn(said, out)

    def refused(self, said):
        rc, out = self.repo.gate()
        self.assertEqual(rc, 1, out)
        self.assertIn(said, out)

    def test_green_in_full(self):
        self.green("both levels passed")

    def test_not_a_checkout(self):
        inner = self.repo.path / "inner"
        (inner / "tools").mkdir(parents=True)
        shutil.copy(STATUS_PY, inner / "tools" / "status.py")
        result = subprocess.run([sys.executable, str(inner / "tools" / "status.py"), "gate"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("is not a git checkout", result.stdout)

    def test_a_dirty_tree_and_an_untracked_file(self):
        self.repo.write("README.md", "changed\n")
        self.refused("uncommitted changes")
        self.repo.git("checkout", "--", "README.md")
        self.repo.git("config", "status.showUntrackedFiles", "no")
        self.repo.write("untracked.txt", "x\n")
        self.refused("uncommitted changes")

    def test_no_status(self):
        self.repo.commit("docs", {"docs/a": "words\n"})
        self.refused("no readable test status")

    def test_red_or_dirty_results(self):
        self.repo.record(self.base, **dict(GREEN, vpair="fail"))
        self.refused("vpair: fail")
        self.repo.record(self.base, **dict(GREEN, vpair_tree="dirty"))
        self.refused("vpair: pass (tree dirty)")

    def test_set_and_show(self):
        head = self.repo.commit("docs", {"docs/a": "words\n"})
        result = self.repo.run("set", "unit", "pass", "loudness controller")
        self.assertEqual(result.returncode, 0, result.stderr)
        saved = json.loads((self.repo.path / "tests" / ".status" / f"{head}.json").read_text())
        self.assertEqual((saved["unit"], saved["unit_tree"], saved["unit_note"]), ("pass", "clean", "loudness controller"))
        shown = self.repo.run("show").stdout
        self.assertIn("unit: pass (clean tree", shown)
        self.assertIn("vpair: not run", shown)

    def test_the_hook_asks_only_the_lab_lock(self):
        hook = ROOT / ".claude" / "hooks" / "pre-bash.py"

        def hook_on(cwd):
            data = json.dumps({"tool_input": {"command": "tools/" + "board.sh smoke"}, "cwd": str(cwd)})
            return subprocess.run([sys.executable, str(hook)], input=data, capture_output=True, text=True)

        self.repo.commit("docs", {"docs/a": "words\n"})
        result = hook_on(self.repo.path)
        self.assertEqual(result.returncode, 0, result.stderr)
        lock = self.repo.path / ".claude" / "lab.lock"
        lock.parent.mkdir(exist_ok=True)
        lock.write_text("")
        result = hook_on(self.repo.path)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("the lab is locked", result.stderr)


if __name__ == "__main__":
    unittest.main()
