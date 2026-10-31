#!/usr/bin/env python3
"""The deploy gate's definition of green (tools/status.py gate and plan), on scratch git
repositories with a copy of tools/status.py and a small tests/impact.json, and the decisions of
the real tests/impact.json. Part of rung 1 (tools/test.sh unit).

    python3 tools/tests/test_status_gate.py
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATUS_PY = ROOT / "tools" / "status.py"

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("status", STATUS_PY)
status = importlib.util.module_from_spec(spec)
spec.loader.exec_module(status)

INSTALLERS = ["test_firmware_speakerd_install", "test_firmware_ampd_install", "test_speakerd_installed",
              "test_speakerd_crash_restart"]
MAP = {"version": 1, "rules": [
    {"paths": ["*.md", "docs/*"], "tests": []},
    {"paths": ["firmware/speakerd-setup.sh"], "tests": ["test_installers"]},
    {"paths": ["tests/vpair/test_*.py"], "tests": "self"},
]}
GREEN = {"unit": "pass", "unit_tree": "clean", "vpair": "pass", "vpair_tree": "clean"}


class Scratch:
    """A git repository with tools/status.py, tests/impact.json and a few files."""

    def __init__(self, path):
        self.path = path
        (path / "tools").mkdir(parents=True)
        shutil.copy(STATUS_PY, path / "tools" / "status.py")
        self.write("tests/impact.json", json.dumps(MAP))
        self.write(".gitignore", "tests/.status/\n")
        self.write("README.md", "scratch\n")
        self.write("firmware/speakerd-setup.sh", "echo 1\n")
        self.write("dsp/engine.cpp", "int x;\n")
        self.write("tests/vpair/test_a.py", "import harness\n")
        self.write("tests/vpair/test_b.py", "from test_a import thing\n")
        self.write("tests/vpair/test_c.py", "from test_b import other\n")
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

    def affected(self, commit, base, ran, result="pass", tree="clean"):
        self.record(commit, affected=result, affected_tree=tree, affected_base=base, affected_ran=ran)

    def run(self, *args):
        return subprocess.run([sys.executable, str(self.path / "tools" / "status.py"), *args],
                              cwd=self.path, capture_output=True, text=True)

    def gate(self):
        result = self.run("gate")
        return result.returncode, result.stdout.strip()


class Gate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="status-gate-"))
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
        self.green("both rungs passed")

    def test_not_a_checkout(self):
        plain = self.tmp / "plain"
        (plain / "tools").mkdir(parents=True)
        shutil.copy(STATUS_PY, plain / "tools" / "status.py")
        result = subprocess.run([sys.executable, str(plain / "tools" / "status.py"), "gate"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("is not a git checkout", result.stdout)

    def test_a_folder_inside_another_checkout_is_not_one(self):
        inner = self.repo.path / "inner"
        (inner / "tools").mkdir(parents=True)
        shutil.copy(STATUS_PY, inner / "tools" / "status.py")
        result = subprocess.run([sys.executable, str(inner / "tools" / "status.py"), "gate"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("is not a git checkout", result.stdout)

    def test_a_dirty_tree(self):
        self.repo.write("README.md", "changed\n")
        self.refused("uncommitted changes")

    def test_no_status(self):
        self.repo.commit("docs", {"docs/a": "words\n"})
        self.refused("no readable test status")

    def test_red_or_dirty_results(self):
        self.repo.record(self.base, **dict(GREEN, vpair="fail"))
        self.refused("vpair: fail")
        self.repo.record(self.base, **dict(GREEN, vpair_tree="dirty"))
        self.refused("vpair: pass (tree dirty)")

    def test_docs_only_need_nothing(self):
        head = self.repo.commit("docs", {"docs/a": "words\n", "README.md": "more\n"})
        self.repo.affected(head, self.base, [])
        self.green("an affected run passed")

    def test_an_installer_needs_its_tests(self):
        head = self.repo.commit("fix", {"firmware/speakerd-setup.sh": "echo 2\n"})
        self.repo.affected(head, self.base, [])
        self.refused("it did not run test_installers")
        self.repo.affected(head, self.base, ["test_installers"])
        self.green("test_installers")

    def test_a_failed_or_dirty_affected_run(self):
        head = self.repo.commit("docs", {"docs/a": "words\n"})
        self.repo.affected(head, self.base, [], result="fail")
        self.refused("affected: fail")
        self.repo.affected(head, self.base, [], tree="dirty")
        self.refused("(tree dirty)")

    def test_code_needs_the_full_run(self):
        head = self.repo.commit("code", {"dsp/engine.cpp": "int y;\n"})
        self.repo.affected(head, self.base, ["test_installers", "test_a"])
        self.refused("dsp/engine.cpp changed since")

    def test_the_map_of_the_base_decides(self):
        # By its own map this commit would need nothing; by the base's, the full run.
        widened = dict(MAP, rules=[{"paths": ["dsp/*", "tests/impact.json"], "tests": []}] + MAP["rules"])
        head = self.repo.commit("widen", {"tests/impact.json": json.dumps(widened), "dsp/engine.cpp": "int y;\n"})
        self.repo.affected(head, self.base, [])
        self.refused("needs both rungs in full")

    def test_a_base_without_a_map(self):
        os.remove(self.repo.path / "tests" / "impact.json")
        bare = self.repo.commit("no map")
        self.repo.record(bare, **GREEN)
        self.repo.write("tests/impact.json", json.dumps(MAP))
        head = self.repo.commit("map again", {"docs/a": "words\n"})
        self.repo.affected(head, bare, [])
        self.refused("has no tests/impact.json")

    def test_the_base_must_be_green_in_full_and_an_ancestor(self):
        first = self.repo.commit("docs", {"docs/a": "words\n"})
        second = self.repo.commit("docs", {"docs/b": "words\n"})
        self.repo.affected(second, first, [])
        self.refused(f"its base {first[:12]} has no readable test status")
        self.repo.affected(first, self.base, [])  # green, but by an affected run: no base for another
        self.refused(f"its base {first[:12]} is not green in full")
        self.repo.record(first, **GREEN)
        self.green(f"on top of {first[:12]}")
        self.repo.git("checkout", "-q", "-b", "other", self.base)
        side = self.repo.commit("side", {"docs/c": "words\n"})
        self.repo.affected(side, first, [])
        self.refused("is not an ancestor")

    def test_a_test_module_runs_with_its_importers(self):
        head = self.repo.commit("test", {"tests/vpair/test_a.py": "import harness  # changed\n"})
        self.repo.affected(head, self.base, ["test_a"])
        self.refused("it did not run test_b, test_c")
        self.repo.affected(head, self.base, ["test_a", "test_b", "test_c"])
        self.green()

    def test_a_record_without_tree_keys(self):
        self.repo.record(self.base, unit="pass", vpair="pass")
        self.refused("unit: pass (tree ?), vpair: pass (tree ?)")

    def test_a_red_full_run_on_the_commit_bars_an_affected_one(self):
        head = self.repo.commit("fix", {"firmware/speakerd-setup.sh": "echo 2\n"})
        self.repo.record(head, **dict(GREEN, vpair="fail"), affected="pass", affected_tree="clean",
                         affected_base=self.base, affected_ran=["test_installers"])
        self.refused(f"a full run failed on {head[:12]} (vpair: fail)")
        self.assertIn("a full run failed", self.repo.run("plan").stderr)

    def test_a_red_full_run_since_the_base_bars_an_affected_one(self):
        first = self.repo.commit("fix", {"firmware/speakerd-setup.sh": "echo 2\n"})
        self.repo.record(first, **dict(GREEN, unit="fail"))
        head = self.repo.commit("docs", {"docs/a": "words\n"})
        self.repo.affected(head, self.base, ["test_installers"])
        self.refused(f"a full run failed on {first[:12]} (unit: fail)")
        self.repo.record(head, **GREEN)
        self.green("both rungs passed")

    def test_a_red_affected_run_since_the_base_does_not(self):
        first = self.repo.commit("fix", {"firmware/speakerd-setup.sh": "echo 2\n"})
        self.repo.affected(first, self.base, ["test_installers"], result="fail")
        head = self.repo.commit("fix again", {"firmware/speakerd-setup.sh": "echo 3\n"})
        self.repo.affected(head, self.base, ["test_installers"])
        self.green("test_installers")

    def test_local_configuration_hides_nothing(self):
        # A submodule's commit counts, and an untracked file dirties the tree, whatever
        # .git/config says. The configuration goes in after the commits: with it, git 2.39
        # finds nothing to commit in a submodule bump.
        (self.repo.path / "lib").mkdir()
        self.repo.git("update-index", "--add", "--cacheinfo", f"160000,{self.base},lib")
        self.repo.git("commit", "-q", "-m", "a submodule")
        with_lib = self.repo.git("rev-parse", "HEAD")
        self.repo.record(with_lib, **GREEN)
        first = self.repo.commit("docs", {"docs/a": "words\n"})
        self.repo.git("update-index", "--cacheinfo", f"160000,{first},lib")
        self.repo.git("commit", "-q", "-m", "bump the submodule")
        head = self.repo.git("rev-parse", "HEAD")
        self.repo.git("config", "diff.ignoreSubmodules", "all")
        self.repo.git("config", "status.showUntrackedFiles", "no")
        self.repo.affected(head, with_lib, [])
        self.refused("lib changed since")
        self.repo.write("untracked.txt", "x\n")
        self.refused("uncommitted changes")

    def test_deleting_a_test_module_runs_what_imports_it(self):
        os.remove(self.repo.path / "tests" / "vpair" / "test_a.py")
        head = self.repo.commit("delete test_a")
        self.repo.affected(head, self.base, [])
        self.refused("it did not run test_b, test_c")

    def test_the_hook_asks_the_gate(self):
        hook = ROOT / ".claude" / "hooks" / "pre-bash.py"

        def hook_on(cwd):
            data = json.dumps({"tool_input": {"command": "tools/" + "board.sh smoke"}, "cwd": str(cwd)})
            return subprocess.run([sys.executable, str(hook)], input=data, capture_output=True, text=True)

        self.assertEqual(hook_on(self.repo.path).returncode, 0)
        head = self.repo.commit("docs", {"docs/a": "words\n"})
        result = hook_on(self.repo.path)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("no readable test status", result.stderr)
        self.repo.affected(head, self.base, [])
        self.assertEqual(hook_on(self.repo.path).returncode, 0)

    def test_plan(self):
        self.repo.commit("fix", {"firmware/speakerd-setup.sh": "echo 2\n"})
        self.repo.write("tests/vpair/test_c.py", "from test_b import other  # not committed\n")
        self.repo.write("docs/new.md", "untracked\n")
        result = self.repo.run("plan")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [self.base, "test_c test_installers"])
        self.repo.write("dsp/engine.cpp", "int z;\n")
        result = self.repo.run("plan")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("needs both rungs in full: dsp/engine.cpp", result.stderr)

    def test_recording_an_affected_run(self):
        head = self.repo.commit("docs", {"docs/a": "words\n"})
        result = self.repo.run("affected", "pass", self.base, "test_a test_b", "2 tests")
        self.assertEqual(result.returncode, 0, result.stderr)
        saved = json.loads((self.repo.path / "tests" / ".status" / f"{head}.json").read_text())
        self.assertEqual((saved["affected"], saved["affected_tree"], saved["affected_base"], saved["affected_ran"],
                          saved["affected_note"]), ("pass", "clean", self.base, ["test_a", "test_b"], "2 tests"))


class TheRealMap(unittest.TestCase):
    """What the repository's tests/impact.json decides for the files a bench fix touches."""

    @classmethod
    def setUpClass(cls):
        cls.impact = json.loads((ROOT / "tests" / "impact.json").read_text())

    def decides(self, path):
        return status.classify(self.impact, path)

    def test_words_run_nothing(self):
        for path in ("docs/remote-plugin-host.md", "docs/images/p1/p1-result.svg", "firmware/CLAUDE.md",
                     "CLAUDE.md", "hardware/amp/amp.kicad_pcb", ".claude/agents/verifier.md"):
            self.assertEqual(self.decides(path), ([], None), path)

    def test_the_installers_run_the_installer_tests(self):
        for path in ("firmware/speakerd-setup.sh", "firmware/ampd-setup.sh", "firmware/setup.sh",
                     "firmware/snapcast-setup.sh", "firmware/speakerd/speakerd-run", "tools/board.sh",
                     "firmware/pipewire/pipewire.conf.d/60-speakerd-sink.conf"):
            self.assertEqual(self.decides(path), (INSTALLERS, None), path)

    def test_what_needs_the_full_run(self):
        for path in ("dsp/juce_apps/speakerd/Engine.cpp", "dsp/juce_plugins/firConv/filters.hpp",
                     "firmware/ampd/ampd.py", "firmware/speakerd/tweeter_rule.py", "firmware/install-lib.sh",
                     "tests/vpair/harness.py", "tests/vpair/stub_speakerd.c", "tools/status.py", "tools/test.sh",
                     "tests/impact.json", ".claude/hooks/pre-bash.py", ".claude/settings.json",
                     ".github/workflows/ci.yml", "dsp/juce_plugins/JUCE"):
            tests, why = self.decides(path)
            self.assertIsNone(tests, path)

    def test_every_installer_test_exists(self):
        for module in INSTALLERS:
            self.assertTrue((ROOT / "tests" / "vpair" / f"{module}.py").exists(), module)


if __name__ == "__main__":
    unittest.main(verbosity=2)
