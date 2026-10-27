#!/usr/bin/env python3
"""Stop hook: refuses to end a turn that changed the virtual pair, the firmware agent or the
JUCE applications while the virtual pair tests are red. Runs them only when such files
changed, through tools/test.sh (in the development container when Podman is installed, else
natively when PipeWire is), without recording a result; takes about ten seconds.
"""

import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
WATCH = ("tests/vpair/", "firmware/ampd/", "dsp/juce_apps/")


def main():
    data = json.load(sys.stdin)
    if data.get("stop_hook_active"):
        return 0                      # already looping on this hook: let the turn end
    changed = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                             capture_output=True, text=True).stdout.splitlines()
    touched = [l for l in changed if any(l[3:].startswith(w) for w in WATCH)]
    if not touched:
        return 0
    if not (shutil.which("podman") or (shutil.which("pipewire") and shutil.which("wireplumber"))):
        print("virtual pair files changed but neither Podman nor PipeWire is installed here: tests not run.")
        return 0
    result = subprocess.run([os.path.join(ROOT, "tools", "test.sh"), "vpair", "--no-record"],
                            cwd=ROOT, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        print("the virtual pair tests fail with the current changes:\n"
              + (result.stdout + result.stderr)[-3000:], file=sys.stderr)
        return 2
    print("virtual pair tests pass.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
