#!/usr/bin/env python3
"""tools/impact.py: which checks of tools/test.sh cover a changed file, by tests/impact.json.
Part of the `tools` check (tools/test.sh tools).

    python3 tools/tests/test_impact.py
"""

import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("impact", ROOT / "tools" / "impact.py")
impact = importlib.util.module_from_spec(spec)
spec.loader.exec_module(impact)
RULES = json.loads((ROOT / "tests" / "impact.json").read_text())["rules"]


def checks(*files):
    return impact.checks_for(RULES, list(files))


class TheMap(unittest.TestCase):
    def test_words_run_nothing(self):
        self.assertEqual(checks("README.md", "docs/protocol.md", "hardware/amp/amp.kicad_pcb", ".claude/agents/reviewer.md"),
                         ([], []))

    def test_a_plugin_runs_its_own_check(self):
        self.assertEqual(checks("dsp/juce_plugins/crossover/PluginProcessor.cpp"), (["plugin crossover", "chain"], []))
        self.assertEqual(checks("dsp/juce_plugins/shared/blockfir/BlockFir.h"), (["plugins", "chain"], []))

    def test_speakerd_and_the_installers(self):
        self.assertEqual(checks("dsp/juce_apps/speakerd/Engine.cpp"), (["speakerd", "installers", "pair", "chain"], []))
        self.assertEqual(checks("firmware/speakerd-setup.sh", "tools/board.sh"), (["installers"], []))
        self.assertEqual(checks("firmware/ampd/ampd.py"), (["ampd", "pair", "controller"], []))

    def test_a_test_module_runs_itself_and_its_importers(self):
        got, uncovered = checks("tests/vpair/test_graph_edit.py")
        self.assertEqual(uncovered, [])
        self.assertEqual(len(got), 1)
        modules = got[0].split()
        self.assertEqual(modules[0], "module")
        self.assertIn("test_graph_edit", modules)
        self.assertIn("test_hub_down", modules)

    def test_the_harness_runs_everything_on_the_pair(self):
        self.assertEqual(checks("tests/vpair/harness.py")[0], ["speakerd", "ampd", "pair", "controller", "installers", "chain"])

    def test_what_no_rule_names(self):
        got, uncovered = checks("firmware/install-lib.sh", ".github/workflows/ci.yml", "tools/build.sh")
        self.assertEqual(got, ["installers"])
        self.assertEqual(uncovered, [".github/workflows/ci.yml", "tools/build.sh"])

    def test_every_module_the_checks_name_exists(self):
        text = (ROOT / "tools" / "test.sh").read_text()
        for line in text.splitlines():
            if not line.strip().startswith(("SPEAKERD", "AMPD", "PAIR", "INSTALLER", "CHAIN", "test_")):
                continue
            for word in line.replace('"', " ").split():
                if word.startswith("test_"):
                    self.assertTrue((ROOT / "tests" / "vpair" / f"{word}.py").exists(), word)


if __name__ == "__main__":
    unittest.main()
