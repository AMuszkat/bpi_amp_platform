"""speakerd_import (docs/autonomous-development.md, section 5): speakerd turns an
AudioPluginHost session into a graph document that sounds exactly like the same settings
written as parameters, and refuses a session it cannot read or that would be unsafe.

This is how a board's current AudioPluginHost session becomes speakerd's factory layer
(firmware/speakerd-setup.sh --import-filtergraph). The session is speakerd's example,
dsp/juce_apps/speakerd/examples/loudness.filtergraph: loudness (the complete build) between the
input's FL and the output's first channel, the woofer, with the settings of
examples/loudness.params.json. No PipeWire is involved: the import and the render are offline.

Pass when:

- `speakerd --import-filtergraph` of the example exits 0 with a graph document on stdout, and
  speakerd's render of that document is, sample for sample, its render of a document that gives
  loudness the same settings as parameters (the complete build);
- the same with --prefer-headless: the document names loudness_headless, and its render equals
  the parameters' render through the headless build, sample for sample;
- an empty file, text that is not XML, XML that is not a FILTERGRAPH, and a session with a
  connection to an output channel the amplifier does not have (2) each exit with status 2 and
  print nothing on stdout;
- the example with one more connection, from the input straight to the tweeter (output channel
  1), exits with status 2 and prints nothing on stdout.

The example and the import came with speakerd's board readiness, after commit 2cb3278 (and the
refusal of a session that feeds the tweeter after b10e23b): before them this fails, and says so.

    python3 -m unittest -v tests.vpair.test_speakerd_import
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from harness import ROOT, built, lv2_uri, render, state_dir, tone

import numpy as np

EXAMPLES = ROOT / "dsp/juce_apps/speakerd/examples"
SESSION = EXAMPLES / "loudness.filtergraph"
PARAMS = EXAMPLES / "loudness.params.json"
HINT = "\n  (the example session and the import came after 2cb3278: this passes once they are merged)"
# Quiet, then loud: loudness's detector (Auto) goes through its settings.
SIGNAL = np.concatenate([tone(100.0, -45.0, 1.0) + tone(1000.0, -50.0, 1.0),
                         tone(100.0, -20.0, 1.0) + tone(1000.0, -26.0, 1.0),
                         tone(100.0, -6.0, 1.0)])


def params_document(identifier):
    return {"format": "speakerd-graph", "version": 1, "revision": 1,
            "nodes": [{"uid": 16, "plugin": {"format": "LV2", "identifier": identifier},
                       "params": json.loads(PARAMS.read_text())}],
            "connections": [{"src": {"node": 1, "channel": 0}, "dst": {"node": 16, "channel": 0}},
                            {"src": {"node": 16, "channel": 0}, "dst": {"node": 2, "channel": 0}}]}


class Import(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.speakerd = built("speakerd")
        cls.headless = built("loudness", "headless")
        cls.complete = built("loudness", "complete")
        cls.tmp = Path(tempfile.mkdtemp(prefix="vpair-import-"))
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)
        (cls.tmp / "home").mkdir()
        if not SESSION.exists() or not PARAMS.exists():
            raise AssertionError(f"no example session {SESSION.relative_to(ROOT)} or {PARAMS.relative_to(ROOT)}{HINT}")
        result = cls.run_import(SESSION, "--lv2-bundle", cls.complete)
        if result.returncode != 0:
            raise AssertionError(f"speakerd --import-filtergraph of the example exited with status "
                                 f"{result.returncode}:\n{result.stderr}{HINT}")
        cls.imported = json.loads(result.stdout)

    @classmethod
    def run_import(cls, session, *args):
        return subprocess.run([str(cls.speakerd), "--import-filtergraph", str(session), *map(str, args)],
                              env=dict(os.environ, HOME=str(cls.tmp / "home")), stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=60)

    def renders_alike(self, document, reference, bundle, what):
        out, log = render(state_dir(self.tmp / f"{what}-import", current=document), SIGNAL, bundles=[bundle])
        want, want_log = render(state_dir(self.tmp / f"{what}-params", current=reference), SIGNAL, bundles=[bundle])
        for text in (log, want_log):
            self.assertIn("layer current (graph.json): loaded", text)
        differs = int(np.count_nonzero(out != want))
        print(f"\n  {what}: {len(out)} frames, {differs} samples differ; woofer "
              f"{20 * np.log10(np.max(np.abs(out[:, 0]))):.2f} dBFS peak")
        self.assertEqual(out.shape, want.shape)
        self.assertEqual(differs, 0, f"{what}: the import does not render like the parameters")
        self.assertFalse(np.array_equal(out[:, 0], np.zeros(len(out))), f"{what}: the render is silent")

    # ---------------------------------------------------------------------------------------

    def test_the_import_sounds_like_the_parameters(self):
        identifiers = [n["plugin"]["identifier"] for n in self.imported["nodes"]]
        self.assertEqual(identifiers, [lv2_uri("loudness")])
        self.renders_alike(self.imported, params_document(lv2_uri("loudness")), self.complete, "complete")

    def test_prefer_headless(self):
        result = self.run_import(SESSION, "--prefer-headless", "--lv2-bundle", self.headless)
        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads(result.stdout)
        self.assertEqual([n["plugin"]["identifier"] for n in document["nodes"]], [lv2_uri("loudness_headless")])
        self.renders_alike(document, params_document(lv2_uri("loudness_headless")), self.headless, "headless")

    def test_malformed_sessions(self):
        text = SESSION.read_text()
        sessions = {
            "empty": "",
            "not XML": "this is not a session\n",
            "not a FILTERGRAPH": '<?xml version="1.0" encoding="UTF-8"?>\n<PLUGINLIST/>\n',
            "output channel 2": text.replace('dstFilter="3" dstChannel="0"', 'dstFilter="3" dstChannel="2"'),
        }
        self.assertNotEqual(sessions["output channel 2"], text)
        for name, content in sessions.items():
            with self.subTest(name):
                path = self.tmp / f"{name.replace(' ', '-')}.filtergraph"
                path.write_text(content)
                result = self.run_import(path, "--lv2-bundle", self.complete)
                print(f"\n  {name}: status {result.returncode}, {result.stderr.strip().splitlines()[-1:]}")
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(result.stdout, "")

    def test_refuses_the_input_straight_to_the_tweeter(self):
        text = SESSION.read_text()
        straight = re.sub(r"(</FILTERGRAPH>)",
                          '  <CONNECTION srcFilter="1" srcChannel="0" dstFilter="3" dstChannel="1"/>\n\\1', text)
        self.assertNotEqual(straight, text)
        path = self.tmp / "straight-to-the-tweeter.filtergraph"
        path.write_text(straight)
        result = self.run_import(path, "--lv2-bundle", self.complete)
        print(f"\n  straight to the tweeter: status {result.returncode}, {result.stderr.strip().splitlines()[-1:]}")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
