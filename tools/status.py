#!/usr/bin/env python3
"""Records a test result for the current commit in tests/.status/<commit>.json, which the
deploy gate (.claude/hooks/pre-bash.py) reads: nothing is deployed to a board unless `unit`
and `vpair` both read "pass" for the exact commit, with a clean tree.

    tools/status.py set <key> <pass|fail|skipped> [note]
    tools/status.py show
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUS_DIR = os.path.join(ROOT, "tests", ".status")


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def main():
    head = git("rev-parse", "HEAD")
    dirty = bool(git("status", "--porcelain"))
    path = os.path.join(STATUS_DIR, f"{head}.json")
    status = {}
    if os.path.exists(path):
        with open(path) as f:
            status = json.load(f)

    if len(sys.argv) >= 4 and sys.argv[1] == "set":
        key, result = sys.argv[2], sys.argv[3]
        status[key] = result
        status[key + "_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        status[key + "_tree"] = "dirty" if dirty else "clean"
        if len(sys.argv) > 4:
            status[key + "_note"] = " ".join(sys.argv[4:])
        status["commit"] = head
        os.makedirs(STATUS_DIR, exist_ok=True)
        with open(path, "w") as f:
            json.dump(status, f, indent=2)
        print(f"{key}: {result} for {head[:12]}{' (dirty tree)' if dirty else ''}")
        return 0

    print(f"commit {head[:12]}{' (dirty tree)' if dirty else ''}")
    print(json.dumps(status, indent=2) if status else "no results recorded for this commit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
