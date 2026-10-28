"""speakerd_tweeter_rule (docs/autonomous-development.md, section 5): speakerd, run as on a board
(without --allow-tweeter-without-crossover) with device.json's tweeter on, refuses every document
that breaks the tweeter rule, and the rule is the same in speakerd and in
firmware/speakerd/tweeter_rule.py (which setup-helper.py and ampd use).

The parity: the same documents, each as graph.json with the tweeter on and no other layer, through
tweeter_rule.py and through speakerd's render (allow_tweeter=False; no plugin bundle is needed:
speakerd checks the rule when it has read the document, before it loads a plugin). speakerd's
verdict and its named outputs are in the layer's line, "layer current (graph.json): FAILED: the
stream reaches the tweeter other than through a crossover's high band (output <node:channel>, ...;".
The documents: the stream straight (FL, and Mine), a crossover's low band, a bypassed crossover,
a crossover's control build, another plugin before the crossover and after its high band, a
crossover feeding both channels (high band only, and low band too), two crossovers in series
(high into high, low into high), several paths to the tweeter, and a plugin the input never
reaches.

On the virtual speaker (loudness_headless and the crossover's complete build), with the tweeter on:

- a graph.json that sends loudness to both channels fails with "the stream reaches the tweeter
  ..." and speakerd plays the next layer, graph.lkg.json (Mine through loudness and the crossover,
  its high band to the tweeter): its capture of a 100 Hz + 5 kHz tone matches that layer's render
  within 0.05 dB on both channels;
- an apply of that document on the local link (no ampd: a client of speakerd's socket) is
  answered `failed` with the same reason, and the capture, recorded across it, goes on unchanged:
  both channels' level after the apply within 0.05 dB of before, and no 10 ms block 0.5 dB off.

Pass when all of that holds, and every document gets the same verdict and outputs from both.

It needs step 3's speakerd (the rule and the local socket): with an older one it fails, saying so.

    python3 -m unittest -v tests.vpair.test_speakerd_tweeter_rule
"""

import json
import os
import re
import shutil
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from harness import (FIRMWARE, NO_LIMITS, RATE, Pair, Player, Recorder, Speakerd, active, block_levels, built,
                     lv2_uri, render, require_speakerd_local_link, revision_document, rms_dbfs, state_dir, steady,
                     tone)
from test_tweeter_rule import chain

sys.path.insert(0, str(FIRMWARE / "speakerd"))
import tweeter_rule  # noqa: E402

LOUDNESS, CROSSOVER = lv2_uri("loudness_headless"), lv2_uri("crossover")
SAID = re.compile(r"^speakerd: layer current \(graph\.json\): FAILED: the stream reaches the tweeter other than "
                  r"through a crossover's high band \(output ([^;]*);", re.M)


def node(uid, identifier, **extra):
    return {"uid": uid, "plugin": {"format": "LV2", "identifier": identifier}, **extra}


def wire(*pairs):
    """Connections from "src:ch-dst:ch" strings."""
    out = []
    for pair in pairs:
        src, dst = pair.split("-")
        (sn, sc), (dn, dc) = src.split(":"), dst.split(":")
        out.append({"src": {"node": int(sn), "channel": int(sc)}, "dst": {"node": int(dn), "channel": int(dc)}})
    return out


def doc(nodes, *pairs):
    return {"format": "speakerd-graph", "version": 1, "revision": 1, "nodes": nodes, "connections": wire(*pairs)}


XO, XO2, LD, LD2 = node(17, CROSSOVER), node(18, CROSSOVER), node(16, LOUDNESS), node(19, LOUDNESS)
DOCUMENTS = {
    "straight from FL": doc([], "1:0-2:0", "1:0-2:1"),
    "straight from Mine": doc([], "1:2-2:0", "1:2-2:1"),
    "a crossover's low band": doc([XO], "1:2-17:0", "17:0-2:1", "17:1-2:0"),
    "a bypassed crossover": doc([node(17, CROSSOVER, bypassed=True)], "1:2-17:0", "17:0-2:0", "17:1-2:1"),
    "a crossover's control build": doc([node(17, CROSSOVER + "_control")], "1:2-17:0", "17:0-2:0", "17:1-2:1"),
    "a plugin before the crossover": doc([LD, XO], "1:2-16:0", "16:0-17:0", "17:0-2:0", "17:1-2:1"),
    "a plugin after the high band": doc([XO, LD2], "1:2-17:0", "17:0-2:0", "17:1-19:0", "19:0-2:1"),
    "a plugin after the low band": doc([XO, LD2], "1:2-17:0", "17:0-19:0", "19:0-2:1", "17:1-2:0"),
    "the high band to both channels": doc([XO], "1:2-17:0", "17:1-2:0", "17:1-2:1"),
    "both bands to the tweeter": doc([XO], "1:2-17:0", "17:0-2:1", "17:1-2:1"),
    "two crossovers, high into high": doc([XO, XO2], "1:2-17:0", "17:1-18:0", "18:1-2:1", "17:0-2:0"),
    "two crossovers, low into high": doc([XO, XO2], "1:2-17:0", "17:0-18:0", "18:1-2:1", "18:0-2:0"),
    "several paths": doc([LD, XO], "1:0-2:1", "1:2-16:0", "16:0-2:1", "1:2-17:0", "17:1-2:1"),
    "a plugin the input never reaches": doc([LD2, XO], "19:0-2:1", "1:2-17:0", "17:0-2:0"),
    "the high band, Mine": doc([XO], "1:2-17:0", "17:0-2:0", "17:1-2:1"),
}

FREQS = (100.0, 5000.0)
BREAKING = revision_document(doc([LD], "1:0-16:0", "16:0-2:0", "16:0-2:1"), 2)
SAFE = revision_document(chain(), 1)


def mix(seconds):
    return tone(FREQS[0], -20.0, seconds) + tone(FREQS[1], -20.0, seconds)


class Parity(unittest.TestCase):
    def test_the_same_verdicts_and_outputs(self):
        require_speakerd_local_link(self)
        tmp = Path(tempfile.mkdtemp(prefix="vpair-rule-parity-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        disagreements = []
        for k, (name, document) in enumerate(DOCUMENTS.items()):
            python = tweeter_rule.unsafe_tweeter_feeds(document)
            state = state_dir(tmp / f"d{k}", role="left", current=document, limits=NO_LIMITS)
            _, log = render(state, silence_of(0.1), allow_tweeter=False)
            found = SAID.search(log)
            speakerd = [o.strip() for o in found.group(1).split(",")] if found else []
            print(f"\n  {name}: tweeter_rule.py {python or 'keeps the rule'}, speakerd {speakerd or 'keeps the rule'}",
                  end="")
            if python != speakerd:
                disagreements.append(f"{name}: tweeter_rule.py {python}, speakerd {speakerd}\n{log}")
        self.assertEqual(disagreements, [], "\n".join(disagreements))
        self.assertGreaterEqual(sum(1 for d in DOCUMENTS.values() if tweeter_rule.unsafe_tweeter_feeds(d)), 7)


def silence_of(seconds):
    return np.zeros(int(seconds * RATE))


class OnTheSpeaker(unittest.TestCase):
    def test_a_breaking_layer_and_apply_are_refused(self):
        require_speakerd_local_link(self)
        tmp = Path(tempfile.mkdtemp(prefix="vpair-rule-live-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        bundles = [built("loudness", "headless"), built("crossover", "complete")]
        state = state_dir(tmp / "live", role="left", current=BREAKING, lkg=SAFE, limits=NO_LIMITS)
        reference, _ = render(state_dir(tmp / "ref", role="left", lkg=SAFE, limits=NO_LIMITS), mix(3.0),
                              bundles=bundles, allow_tweeter=False)
        with Pair() as pair, Speakerd(pair["left"], state, bundles=bundles, allow_tweeter=False) as sd:
            refused = sd.wait_for("layer current (graph.json): FAILED: ")
            print(f"\n  {refused}")
            self.assertIn("FAILED: the stream reaches the tweeter other than through a crossover's high band (output 16:0;",
                          refused)
            sd.wait_for("using layer last-known-good (graph.lkg.json), revision 1")
            live = pair["left"].play_and_record(mix(3.0), target="speakerd", record_target="amplifier")
            for channel, what in ((0, "woofer"), (1, "tweeter")):
                got, want = (rms_dbfs(steady(active(x)[:, channel])) for x in (live, reference))
                print(f"\n  last known good, {what}: live {got:.3f} dBFS, its render {want:.3f} dBFS", end="")
                self.assertLess(abs(got - want), 0.05, f"the {what} is not graph.lkg.json's sound")

            # An apply of the breaking document on the local link: failed, and the sound goes on.
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(os.path.join(pair["left"].env["XDG_RUNTIME_DIR"], "speakerd", "control.sock"))
            f = sock.makefile("rb")
            self.assertEqual(json.loads(f.readline())["op"], "hello")
            with Player(pair["left"], mix(8.0), target="speakerd"), Recorder(pair["left"], "amplifier") as recorder:
                time.sleep(2.0)
                sock.sendall(json.dumps({"op": "apply", "revision": 3, "document": dict(BREAKING, revision=3)}).encode()
                             + b"\n")
                sock.settimeout(10)
                answer = json.loads(f.readline())
                time.sleep(2.0)
                capture = recorder.stop()
            f.close()
            sock.close()
            print(f"\n  apply of the breaking document: {answer['op']}: {answer.get('reason', '')[:100]}", end="")
            self.assertEqual((answer["op"], answer.get("revision")), ("failed", 3))
            self.assertIn("the stream reaches the tweeter other than through a crossover's high band", answer["reason"])
            # The apply went 2 s into the recording, the answer at once: before is 0.3 to 1.8 s,
            # after from 2.4 s to the end; the blocks cover all of it.
            x = capture
            for channel, what in ((0, "woofer"), (1, "tweeter")):
                before = rms_dbfs(x[int(0.3 * RATE):int(1.8 * RATE), channel])
                after = rms_dbfs(x[int(2.4 * RATE):len(x) - int(0.3 * RATE), channel])
                blocks = block_levels(x[int(0.3 * RATE):len(x) - int(0.3 * RATE), channel], 0.01)
                worst = float(np.max(np.abs(blocks - np.median(blocks))))
                print(f"\n  {what} across the refused apply: {before:.3f} then {after:.3f} dBFS, every 10 ms block "
                      f"within {worst:.3f} dB", end="")
                self.assertLess(abs(after - before), 0.05, f"the {what} changed across the refused apply")
                self.assertLess(worst, 0.5, f"the {what} was interrupted across the refused apply")


if __name__ == "__main__":
    unittest.main()
