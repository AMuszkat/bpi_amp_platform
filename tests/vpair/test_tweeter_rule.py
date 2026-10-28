"""tweeter_rule (docs/autonomous-development.md, section 5): once a speaker's tweeter is on, no
revision that reaches it other than through a crossover's high band is applied; with the tweeter
muted, any is.

The rule is defined once, in firmware/speakerd/tweeter_rule.py (setup-helper.py and ampd import
it): from the input, only a crossover's output 1 (its high band) may reach the output's channel 1
(the tweeter); not the stream straight, not a crossover's low band, not a bypassed crossover. ampd
checks it on every revision it would apply (a proposal on the hub, an adoption, an agent's apply)
while its speaker's device.json says `"tweeter": "on"`.

Both virtual speakers run speakerd (loudness_headless, and the crossover's complete build) and
ampd, from revision 1: the Mine pin through loudness and the crossover, its low band to the woofer
and its high band to the tweeter. A harness controller proposes, in turn, a document with the
stream straight to the tweeter, one with the crossover's bands swapped (its low band to the
tweeter), one with the crossover bypassed, and a safe one (revision 1's chain, other values).

Pass when:

- with the tweeter on in both device.json: the three are rejected, each with "left: the tweeter
  is on here (device.json) and the stream reaches the tweeter other than through a crossover's
  high band", and nothing changes (the hub's revision, both graph.json); the safe one is applied
  on both;
- with the tweeter on in the right's device.json only: the straight one is rejected with "right:
  the tweeter is on here ...", and both speakers stay on revision 1 (the left, which applied it,
  rolled back);
- with the tweeter muted in both: all four are applied, one after the other.

It needs step 3's speakerd (the local socket, the Mine pin: 0.3.0): with an older one it fails,
saying so.

    python3 -m unittest -v tests.vpair.test_tweeter_rule
"""

import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from harness import (NO_LIMITS, TWEETER_MUTED, Controller, SpeakerPair, built, lv2_uri, require_speakerd_local_link,
                     revision_document)

LOUDNESS, CROSSOVER = lv2_uri("loudness_headless"), lv2_uri("crossover")


def chain(low_to=0, high_to=1, bypassed=False, mode="Force 1"):
    """Mine -> loudness (16) -> crossover (17); its low band (output 0) to `low_to`, its high band
    (output 1) to `high_to` (0 the woofer, 1 the tweeter)."""
    return {"format": "speakerd-graph", "version": 1,
            "nodes": [{"uid": 16, "plugin": {"format": "LV2", "identifier": LOUDNESS}, "params": {"mode": mode}},
                      {"uid": 17, "plugin": {"format": "LV2", "identifier": CROSSOVER}, "bypassed": bypassed}],
            "connections": [{"src": {"node": 1, "channel": 2}, "dst": {"node": 16, "channel": 0}},
                            {"src": {"node": 16, "channel": 0}, "dst": {"node": 17, "channel": 0}},
                            {"src": {"node": 17, "channel": 0}, "dst": {"node": 2, "channel": low_to}},
                            {"src": {"node": 17, "channel": 1}, "dst": {"node": 2, "channel": high_to}}]}


STRAIGHT = {"format": "speakerd-graph", "version": 1, "nodes": [],
            "connections": [{"src": {"node": 1, "channel": 2}, "dst": {"node": 2, "channel": 0}},
                            {"src": {"node": 1, "channel": 2}, "dst": {"node": 2, "channel": 1}}]}
UNSAFE = {"straight": STRAIGHT, "low band": chain(low_to=1, high_to=0), "bypassed": chain(bypassed=True)}
SAFE = chain(mode="Force 2")
RULE = "the tweeter is on here (device.json) and the stream reaches the tweeter other than through a crossover's high band"


class TweeterRule(unittest.TestCase):
    def setUp(self):
        require_speakerd_local_link(self)
        self.tmp = Path(tempfile.mkdtemp(prefix="vpair-tweeter-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bundles = [built("loudness", "headless"), built("crossover", "complete")]
        self.boot = revision_document(chain(), 1)

    def pair(self, name, limits):
        return SpeakerPair(self.tmp / name, {"left": self.boot, "right": self.boot}, self.bundles, limits=limits)

    def revisions(self, pair, want=None):
        """Both speakers' graph.json revisions; with `want`, once they are that (ampd writes
        graph.json on its file thread, a moment after it answers), or after 2 s."""
        deadline = time.monotonic() + 2.0
        while True:
            found = {name: pair.graph(name)["revision"] for name in ("left", "right")}
            if want is None or found == want or time.monotonic() > deadline:
                return found
            time.sleep(0.05)

    def test_with_the_tweeter_on(self):
        with self.pair("on", {"left": NO_LIMITS, "right": NO_LIMITS}) as pair, Controller("ctl-rule") as ctl:
            for what, document in UNSAFE.items():
                answer = ctl.propose(json.loads(json.dumps(document)))
                print(f"\n  tweeter on, {what}: {answer['op']}: {answer.get('reason', '')[:110]}", end="")
                self.assertEqual(answer["op"], "rejected", what)
                self.assertTrue(answer["reason"].startswith(f"left: {RULE}"), answer["reason"])
                self.assertEqual(ctl.current_revision(), 1, what)
                self.assertEqual(self.revisions(pair), {"left": 1, "right": 1}, what)
            answer = ctl.propose(SAFE)
            print(f"\n  tweeter on, the high band: {answer['op']}", end="")
            self.assertEqual(answer["op"], "applied", answer)
            self.assertEqual(self.revisions(pair, {"left": 2, "right": 2}), {"left": 2, "right": 2})

    def test_with_the_tweeter_on_on_the_agent_only(self):
        with self.pair("agent-on", {"left": TWEETER_MUTED, "right": NO_LIMITS}) as pair, Controller("ctl-rule") as ctl:
            answer = ctl.propose(json.loads(json.dumps(STRAIGHT)))
            print(f"\n  the right's tweeter on, straight: {answer['op']}: {answer.get('reason', '')[:110]}", end="")
            self.assertEqual(answer["op"], "rejected")
            self.assertTrue(answer["reason"].startswith(f"right: {RULE}"), answer["reason"])
            pair.ampd["left"].wait_for("revision 2: rolling back to revision 1 on left")
            self.assertEqual(ctl.current_revision(), 1)
            self.assertEqual(self.revisions(pair), {"left": 1, "right": 1})

    def test_with_the_tweeter_muted(self):
        with self.pair("muted", {"left": TWEETER_MUTED, "right": TWEETER_MUTED}) as pair, Controller("ctl-rule") as ctl:
            for k, (what, document) in enumerate(list(UNSAFE.items()) + [("the high band", SAFE)]):
                answer = ctl.propose(json.loads(json.dumps(document)))
                print(f"\n  tweeter muted, {what}: {answer['op']}", end="")
                self.assertEqual(answer["op"], "applied", (what, answer))
                self.assertEqual(self.revisions(pair, {"left": k + 2, "right": k + 2}), {"left": k + 2, "right": k + 2})


if __name__ == "__main__":
    unittest.main()
