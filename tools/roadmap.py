#!/usr/bin/env python3
"""Where the remote plugin host stands, and who does the next step.

Derives the status of each roadmap step of docs/remote-plugin-host.md from what exists in
the tree and what the last test run recorded, and names the agent that owns the next step.

    tools/roadmap.py            # the table and the next step
    tools/roadmap.py --next     # one line: the next step and its agent
"""

import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def exists(*parts):
    return os.path.exists(os.path.join(ROOT, *parts))


def has_scenario(prefix):
    d = os.path.join(ROOT, "tests", "vpair")
    return os.path.isdir(d) and any(f.startswith("test_" + prefix) for f in os.listdir(d))


def plugins_with_targets():
    out = []
    base = os.path.join(ROOT, "dsp", "juce_plugins")
    for name in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        cmake = os.path.join(base, name, "CMakeLists.txt")
        if not os.path.isfile(cmake):
            continue
        text = open(cmake).read()
        if "speaker_add_plugin(" in text and re.search(r"^\s*TARGETS\b", text, re.M):
            out.append(name)
    return out


def status_of_head():
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    path = os.path.join(ROOT, "tests", ".status", head + ".json")
    if os.path.exists(path):
        with open(path) as f:
            return head[:12], json.load(f)
    return head[:12], {}


# Each step: (title, done-when, agent, what to ask for)
STEPS = [
    ("Step 1: one plugin builds as complete, control and headless, proven by plugin-check",
     lambda: exists("dsp", "juce_apps", "plugin-check", "run.sh") and bool(plugins_with_targets()),
     "plugin-dev",
     "split a plugin into DSP and EDITOR sources with TARGETS, then /test unit"),
    ("Step 2: speakerd, the headless host, runs the headless build on one virtual speaker with no controller",
     lambda: exists("dsp", "juce_apps", "speakerd", "CMakeLists.txt") and has_scenario("speakerd"),
     "host-dev, then board-dev",
     "host-dev: dsp/juce_apps/speakerd, AudioProcessorGraph + pw_filter, the state layers and "
     "boot order; board-dev: the tests/vpair/test_speakerd_*.py scenarios that compare its "
     "output with the complete build's; /build speakerd, /test all"),
    ("Step 3: bridge + hub: parameters over OSC, the graph document over TCP, two speakers, the Mine pin, value monitors, the level bar",
     lambda: exists("firmware", "ampd") and exists("dsp", "juce_apps", "controller", "CMakeLists.txt") and has_scenario("knob_latency"),
     "orchestrator",
     "host-dev (socket, Mine pin, monitors), board-dev (ampd, hub, scenarios), "
     "controller-dev (bridge, control builds compiled in) in parallel worktrees"),
    ("Step 4: scope frames and records, catalogue and version hashes, multiple controllers, gestures, tablet build",
     lambda: has_scenario("scope_resolution") and has_scenario("catalogue"),
     "orchestrator",
     "plugin-dev (monitor layouts, version hash), host-dev (frames, records), "
     "controller-dev (subscriptions, gestures), board-dev (catalogue, scenarios)"),
]


def main():
    done = [step[1]() for step in STEPS]
    next_index = next((i for i, d in enumerate(done) if not d), None)
    head, status = status_of_head()

    if "--next" in sys.argv:
        if next_index is None:
            print("every roadmap step is done")
        else:
            title, _, agent, ask = STEPS[next_index]
            print(f"{title}\n  agent: {agent}\n  ask for: {ask}")
        return 0

    print("Remote plugin host, docs/remote-plugin-host.md section 11\n")
    for i, (title, _, agent, ask) in enumerate(STEPS):
        mark = "done " if done[i] else ("NEXT " if i == next_index else "     ")
        print(f"  [{mark}] {title}")
        print(f"          agent: {agent}")
        if i == next_index:
            print(f"          ask for: {ask}")
    print(f"\n  plugins with the three targets: {', '.join(plugins_with_targets()) or 'none'}")
    print(f"  last test status for {head}: " + (", ".join(f"{k} {status[k]}" for k in ("unit", "vpair") if k in status) or "none recorded; run /test all"))
    print("\n  Routing: one area changes -> that area's agent; several -> orchestrator; "
          "tests -> verifier; before a pull request -> reviewer; real boards -> board-ops.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
