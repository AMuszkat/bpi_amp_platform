#!/usr/bin/env python3
"""tools/impact.py: which checks of tools/test.sh cover a changed file, by tests/impact.json; and
that the map stays honest: every speakerd file is named by a rule of its own, and every path a
plugin rule without `chain` names is editor-only (never compiled into the plugin's headless
build, which is what the chain runs). Part of the `tools` check (tools/test.sh tools).

    python3 tools/tests/test_impact.py
"""

import contextlib
import fnmatch
import importlib.util
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
PLUGINS = ROOT / "dsp" / "juce_plugins"
SPEAKERD = "dsp/juce_apps/speakerd"
CATCH_ALL = f"{SPEAKERD}/*"
CHECKS = {"plugin", "plugins", "speakerd", "ampd", "pair", "controller", "installers", "chain", "tools", "self"}

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("impact", ROOT / "tools" / "impact.py")
impact = importlib.util.module_from_spec(spec)
spec.loader.exec_module(impact)
RULES = json.loads((ROOT / "tests" / "impact.json").read_text())["rules"]


def checks(*files):
    return impact.checks_for(RULES, list(files))


def repository_files(folder):
    """The files under `folder` (from the root) that git would see: tracked or untracked, not
    ignored, and still there."""
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "--cached", "--others", "--exclude-standard", "-z",
                          "--", folder], capture_output=True, text=True, check=True).stdout
    return sorted(f for f in set(out.split("\0")) - {""} if (ROOT / f).is_file())


# --- What a plugin's headless build compiles ------------------------------------------------------

KEYWORDS = ("CODE", "INPUTS", "OUTPUTS", "SOURCES", "DSP", "EDITOR", "MODULES", "SHARED", "TARGETS")
INCLUDE = re.compile(r'^\s*#\s*include\s*"([^"]+)"')
CONDITIONAL = re.compile(r"^\s*#\s*(if|ifdef|ifndef|elif|else|endif)\b(.*)")


def plugin_lists(folder):
    """The lists of the speaker_add_plugin() call in folder/CMakeLists.txt: {"SOURCES": [...], ...}."""
    text = re.sub(r"#[^\n]*", "", (folder / "CMakeLists.txt").read_text())
    call = re.search(r"speaker_add_plugin\s*\(([^)]*)\)", text)
    lists, current = {}, None
    for word in call.group(1).split()[1:]:   # the first word is the target
        if word in KEYWORDS:
            current = lists.setdefault(word, [])
        elif current is not None:
            current.append(word)
    return lists


def includes(path, headless):
    """The quoted includes of a source file; with `headless`, only those the headless build
    compiles: not those inside `#if ! SPEAKER_HEADLESS`, nor in the #else of `#if SPEAKER_HEADLESS`.
    Any other condition counts as compiled, so a guess can only make a file look less editor-only."""
    found, frames = [], []   # per open #if: [its kind, whether its current branch is left out of headless]
    for line in path.read_text(errors="replace").splitlines():
        conditional = CONDITIONAL.match(line)
        if conditional:
            word, condition = conditional.group(1), re.sub(r"\s+", "", conditional.group(2).split("//")[0])
            if word in ("if", "ifdef", "ifndef"):
                kind = {"!SPEAKER_HEADLESS": "not", "SPEAKER_HEADLESS": "is"}.get(condition, "other") if word == "if" else "other"
                frames.append([kind, kind == "not"])
            elif word == "else" and frames:
                frames[-1][1] = frames[-1][0] == "is"
            elif word == "elif" and frames:
                frames[-1] = ["other", False]
            elif word == "endif" and frames:
                frames.pop()
            continue
        include = INCLUDE.match(line)
        if include and not (headless and any(left_out for _, left_out in frames)):
            found.append(include.group(1))
    return found


def compiled(folder, sources, headless):
    """The plugin's own files that these sources compile: them, and what they include from its
    folder, transitively."""
    seen, todo = set(), list(sources)
    while todo:
        name = todo.pop()
        if name in seen or not (folder / name).is_file():
            continue
        seen.add(name)
        todo.extend(includes(folder / name, headless))
    return seen


def editor_only(folder):
    """The files of a plugin that its headless build never compiles: its EDITOR sources, and the
    headers they include that the headless build's sources (SOURCES and DSP) never reach."""
    lists = plugin_lists(folder)
    headless = compiled(folder, lists.get("SOURCES", []) + lists.get("DSP", []), headless=True)
    return compiled(folder, lists.get("EDITOR", []), headless=False) - headless


# --- The tests ------------------------------------------------------------------------------------

class TheMap(unittest.TestCase):
    def test_words_run_nothing(self):
        self.assertEqual(checks("README.md", "docs/protocol.md", "hardware/amp/amp.kicad_pcb", ".claude/agents/reviewer.md",
                                f"{SPEAKERD}/README.md"),
                         ([], []))

    def test_a_plugin_runs_its_own_check(self):
        self.assertEqual(checks("dsp/juce_plugins/crossover/PluginProcessor.cpp"), (["plugin crossover", "chain"], []))
        for path in ("crossover/CrossoverDsp.cpp", "crossover/crossover_coefs.hpp", "crossover/CMakeLists.txt",
                     "crossover/plugin-check.probes", "crossover/PluginProcessor.h"):
            self.assertEqual(checks(f"dsp/juce_plugins/{path}"), (["plugin crossover", "chain"], []), path)
        self.assertEqual(checks("dsp/juce_plugins/loudness/plugin-check.monitors.h", "dsp/juce_plugins/loudness/Monitors.h"),
                         (["plugin loudness", "chain"], []))
        self.assertEqual(checks("dsp/juce_plugins/shared/blockfir/BlockFir.h"), (["plugins", "chain"], []))

    def test_a_plugins_editor_runs_its_own_check_alone(self):
        for path in ("loudness/PluginEditor.cpp", "loudness/PluginEditor.h", "loudness/MorphPad.cpp", "loudness/Theme.h",
                     "loudness/HostWindowTracker.h"):
            self.assertEqual(checks(f"dsp/juce_plugins/{path}"), (["plugin loudness"], []), path)
        self.assertEqual(checks("dsp/juce_plugins/crossover/PlotComponent.cpp"), (["plugin crossover"], []))
        self.assertEqual(checks("dsp/juce_plugins/firConv/PluginEditor.h"), (["plugin firConv"], []))
        # With a file the headless build compiles, the chain runs too.
        self.assertEqual(checks("dsp/juce_plugins/loudness/MorphPad.cpp", "dsp/juce_plugins/loudness/LoudnessDsp.cpp"),
                         (["plugin loudness", "chain"], []))

    def test_speakerd_file_by_file(self):
        expected = {
            ("speakerd",): ["Engine.cpp", "Engine.h", "Render.cpp", "Render.h", "Log.h", "FilterGraph.cpp",
                            "FilterGraph.h", "LV2State.cpp", "LV2State.h", "Samples.h", "examples/loudness.filtergraph",
                            "examples/MakeFiltergraph.cpp"],
            ("speakerd", "installers"): ["BuildInfo.cmake", "DeviceConfig.cpp", "DeviceConfig.h", "StateLayers.cpp",
                                         "StateLayers.h"],
            ("speakerd", "installers", "pair"): ["PipeWireNode.cpp", "PipeWireNode.h", "LocalLink.cpp", "LocalLink.h"],
            ("speakerd", "installers", "pair", "chain"): ["Main.cpp", "CMakeLists.txt", "Control.cpp", "Control.h",
                                                          "Monitor.cpp", "Monitor.h", "Timing.cpp", "Timing.h",
                                                          "TimedNode.cpp", "TimedNode.h"],
            ("speakerd", "installers", "chain"): ["PluginBundles.cpp", "PluginBundles.h"],
            ("speakerd", "chain"): ["GraphBuilder.cpp", "GraphBuilder.h"],
            ("speakerd", "pair"): ["GraphDocument.cpp", "GraphDocument.h"],
        }
        for want, files in expected.items():
            for name in files:
                self.assertEqual(checks(f"{SPEAKERD}/{name}"), (list(want), []), name)

    def test_an_edit_of_the_engine_runs_speakerd_alone(self):
        # The person's decision: the Engine's parts that only pair, chain and installers prove are
        # left to the levels, and to that command by hand, which its rule's by_hand says.
        self.assertEqual(checks(f"{SPEAKERD}/Engine.cpp"), (["speakerd"], []))
        rule = impact.rule_for(RULES, f"{SPEAKERD}/Engine.cpp")
        self.assertIn("run `tools/test.sh speakerd pair chain installers`", rule["by_hand"])

    def test_by_hand_is_said_once_per_rule_with_its_files(self):
        engine, header = f"{SPEAKERD}/Engine.cpp", f"{SPEAKERD}/Engine.h"
        advice = impact.rule_for(RULES, engine)["by_hand"]
        self.assertEqual(impact.by_hand_for(RULES, [engine, f"{SPEAKERD}/Render.cpp", header, "README.md"]),
                         [(advice, [engine, header])])
        self.assertEqual(impact.by_hand_for(RULES, [f"{SPEAKERD}/Render.cpp", f"{SPEAKERD}/Monitor.cpp", "tools/build.sh"]), [])

        # What impact.py prints: the checks on stdout, the advice on stderr (where `tools/test.sh
        # changed` shows it), once, beside the files no rule names.
        def said(*files):
            stdout, stderr = io.StringIO(), io.StringIO()
            with mock.patch.object(impact, "changed_files", lambda ref: list(files)), \
                 contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(impact.main(["HEAD"]), 0)
            return stdout.getvalue().splitlines(), stderr.getvalue().splitlines()

        # `tools/test.sh changed` says the advice again after the checks, picking it out of that
        # stderr with a grep in run_changed: the pattern it greps for, read out of tools/test.sh
        # and run through grep itself, must take the advice and nothing else, or the repeat would
        # go silently if either side's wording drifted.
        test_sh = (ROOT / "tools" / "test.sh").read_text()
        body = re.search(r"^run_changed\(\) \{\n(.*?)^\}$", test_sh, re.S | re.M)
        self.assertIsNotNone(body, "tools/test.sh has no run_changed")
        patterns = re.findall(r"""grep '([^']*)' <<< "\$said\"""", body.group(1))
        self.assertEqual(len(patterns), 1, f"run_changed greps impact.py's stderr ($said) {len(patterns)} times, not once")

        def repeated(err):
            picked = subprocess.run(["grep", "--", patterns[0]], input="".join(f"{line}\n" for line in err),
                                    capture_output=True, text=True, timeout=10)
            self.assertIn(picked.returncode, (0, 1), picked.stderr)
            return picked.stdout.splitlines()

        out, err = said(engine, header, "tools/build.sh")
        self.assertEqual(out, ["speakerd"])
        self.assertEqual(err, ["no check covers tools/build.sh: the levels do (tools/test.sh all)",
                               f"by hand, for {engine}, {header}: {advice}",
                               "3 file(s) changed since HEAD"])
        self.assertEqual(repeated(err), [err[1]])   # the advice, as impact.py printed it
        out, err = said(f"{SPEAKERD}/Render.cpp")
        self.assertEqual((out, err), (["speakerd"], ["1 file(s) changed since HEAD"]))
        self.assertEqual(repeated(err), [])

    def test_several_speakerd_files_run_what_each_needs_once(self):
        self.assertEqual(checks(f"{SPEAKERD}/Engine.cpp", f"{SPEAKERD}/GraphBuilder.cpp", f"{SPEAKERD}/GraphDocument.cpp",
                                f"{SPEAKERD}/DeviceConfig.cpp", f"{SPEAKERD}/Render.cpp"),
                         (["speakerd", "chain", "pair", "installers"], []))

    def test_a_speakerd_file_no_rule_names_runs_everything_on_speakerd(self):
        self.assertEqual(checks(f"{SPEAKERD}/Something.cpp"), (["speakerd", "installers", "pair", "chain"], []))

    def test_speakerd_and_the_installers(self):
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
        everything = ["speakerd", "ampd", "pair", "controller", "installers", "chain", "tools"]
        self.assertEqual(checks("tests/vpair/harness.py"), (everything, []))
        self.assertEqual(checks("tests/vpair/pool.py"), (everything, []))

    def test_a_self_test_of_the_pairs_tools_runs_the_tools_check(self):
        # tests/vpair/*_selftest.py: not test_*, so the vpair level never discovers them; the tools
        # check runs them (tools/test.sh tools).
        self.assertEqual(checks("tests/vpair/pool_selftest.py"), (["tools"], []))
        self.assertEqual(checks("tests/vpair/pool_selftest.py", "tools/test.sh"), (["tools"], []))

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


class TheMapIsHonest(unittest.TestCase):
    def test_the_rules_name_only_checks_that_exist(self):
        for rule in RULES:
            self.assertLessEqual(set(rule["checks"]), CHECKS, rule["why"])
            self.assertTrue(rule["why"] and rule["paths"], rule)
            self.assertLessEqual(set(rule), {"why", "paths", "checks", "by_hand"}, rule["why"])
            if "by_hand" in rule:
                self.assertTrue(isinstance(rule["by_hand"], str) and rule["by_hand"].strip(), rule["why"])

    def test_every_speakerd_file_is_named_by_a_rule_of_its_own(self):
        files = [f for f in repository_files(SPEAKERD) if not f.endswith(".md")]
        self.assertIn(f"{SPEAKERD}/Engine.cpp", files)
        for path in files:
            rule = impact.rule_for(RULES, path)
            self.assertIsNotNone(rule, path)
            self.assertNotIn(CATCH_ALL, rule["paths"],
                             f"{path}: no rule of tests/impact.json names it; say which checks read what it is for")
            self.assertIn("speakerd", rule["checks"], f"{path}: every speakerd file runs the speakerd check")

    def test_every_rule_of_a_speakerd_file_runs_speakerd(self):
        for rule in RULES:
            if any(p.startswith(f"{SPEAKERD}/") for p in rule["paths"]) and rule["checks"]:
                self.assertIn("speakerd", rule["checks"], rule["why"])

    def test_a_plugin_path_that_skips_the_chain_is_editor_only(self):
        named = 0
        for rule in RULES:
            if "plugin" not in rule["checks"] or "chain" in rule["checks"]:
                continue
            for pattern in rule["paths"]:
                parts = pattern.split("/")
                self.assertTrue(len(parts) > 3 and parts[:2] == ["dsp", "juce_plugins"] and "*" not in parts[2],
                                f"{pattern}: a rule without chain names files of one plugin")
                folder = PLUGINS / parts[2]
                allowed = editor_only(folder)
                matched = [f for f in repository_files("/".join(parts[:3])) if fnmatch.fnmatchcase(f, pattern)]
                self.assertTrue(matched, f"{pattern}: names no file")
                for path in matched:
                    name = path[len("/".join(parts[:3])) + 1:]
                    self.assertIn(name, allowed,
                                  f"{path} runs no chain, but the headless build compiles it (or it is no EDITOR "
                                  f"source of {parts[2]}/CMakeLists.txt): move it to the plugin's own rule")
                    named += 1
        self.assertGreater(named, 0, "no editor-only path: the test would prove nothing")

    def test_what_the_headless_build_compiles(self):
        loudness = editor_only(PLUGINS / "loudness")
        self.assertEqual(loudness, {"PluginEditor.cpp", "PluginEditor.h", "MorphPad.cpp", "MorphPad.h",
                                    "HostWindowTracker.cpp", "HostWindowTracker.h", "Theme.h"})
        for plugin in ("crossover", "firConv"):
            self.assertEqual(editor_only(PLUGINS / plugin),
                             {"PluginEditor.cpp", "PluginEditor.h", "PlotComponent.cpp", "PlotComponent.h"}, plugin)

    def test_a_file_moved_out_of_the_editor_is_no_longer_editor_only(self):
        tmp = Path(tempfile.mkdtemp(prefix="impact-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        files = {
            "CMakeLists.txt": "speaker_add_plugin(p  # a comment: EDITOR x.cpp\n"
                              "    SOURCES Processor.cpp\n    DSP Dsp.cpp\n    EDITOR Editor.cpp Panel.cpp\n"
                              "    TARGETS complete control headless)\n",
            "Processor.cpp": '#include "Processor.h"\n#if ! SPEAKER_HEADLESS\n #include "Editor.h"\n#endif\n',
            "Processor.h": '#include "Shared.h"\n',
            "Dsp.cpp": '#if SPEAKER_HEADLESS\n#include "Fast.h"\n#else\n#include "Slow.h"\n#endif\n',
            "Editor.cpp": '#include "Editor.h"\n#include "Theme.h"\n',
            "Editor.h": '#include "Processor.h"\n#include "Panel.h"\n',
            "Panel.cpp": '#include "Panel.h"\n', "Panel.h": "", "Theme.h": "", "Shared.h": "", "Fast.h": "", "Slow.h": "",
        }
        for name, text in files.items():
            (tmp / name).write_text(text)
        self.assertEqual(editor_only(tmp), {"Editor.cpp", "Editor.h", "Panel.cpp", "Panel.h", "Theme.h"})

        # Panel.cpp moved into SOURCES, and Theme.h included by the DSP: both compiled headless now.
        (tmp / "CMakeLists.txt").write_text(files["CMakeLists.txt"].replace("SOURCES Processor.cpp", "SOURCES Processor.cpp Panel.cpp")
                                            .replace("EDITOR Editor.cpp Panel.cpp", "EDITOR Editor.cpp"))
        (tmp / "Dsp.cpp").write_text('#include "Theme.h"\n')
        self.assertEqual(editor_only(tmp), {"Editor.cpp", "Editor.h"})


if __name__ == "__main__":
    unittest.main()
