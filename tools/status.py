#!/usr/bin/env python3
"""Records the result of a test level for the current commit in tests/.status/<commit>.json,
and says whether a commit is green: both levels passed on a clean tree. Nothing enforces it.

    tools/status.py set <level> <pass|fail|skipped> [note]
    tools/status.py show
    tools/status.py gate [--tree DIR]  exit 0 if the checkout is green, else 1; says why

Git is asked with submodules and untracked files shown whatever the local configuration says.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEVELS = ("unit", "vpair")


def git(tree, *args):
    return subprocess.run(["git", "-C", tree, *args], capture_output=True, text=True)


def out(tree, *args):
    return git(tree, *args).stdout.strip()


def dirty(tree):
    """Whether the working tree has changes, submodules and untracked files included whatever
    the local configuration hides; RuntimeError when git cannot say."""
    result = git(tree, "status", "--porcelain", "--untracked-files=normal", "--ignore-submodules=none")
    if result.returncode:
        raise RuntimeError(f"git status: {result.stderr.strip()}")
    return bool(result.stdout.strip())


def status_path(tree, commit):
    return os.path.join(tree, "tests", ".status", f"{commit}.json")


def read_status(tree, commit):
    """A commit's record, a dict; OSError or ValueError when there is none that reads."""
    with open(status_path(tree, commit)) as f:
        status = json.load(f)
    if not isinstance(status, dict):
        raise ValueError("not a JSON object")
    return status


def green_in_full(status):
    return all(status.get(k) == "pass" and status.get(k + "_tree") == "clean" for k in LEVELS)


def gate(tree):
    """(True, why) when the checkout at tree is green, else (False, why)."""
    tree = os.path.realpath(tree)
    top = git(tree, "rev-parse", "--show-toplevel")
    if top.returncode or os.path.realpath(top.stdout.strip()) != tree:
        return False, f"{tree} is not a git checkout, so nothing says what was tested"
    head = out(tree, "rev-parse", "--verify", "-q", "HEAD")
    if not head:
        return False, f"{tree} has no commit, so nothing says what was tested"
    try:
        changes = dirty(tree)
    except RuntimeError as e:
        return False, f"{e}: cannot tell whether the tree is clean"
    if changes:
        return False, "the working tree has uncommitted changes"
    try:
        status = read_status(tree, head)
    except (OSError, ValueError) as e:
        return False, f"no readable test status {status_path(tree, head)} ({e}): run tools/test.sh all"
    if green_in_full(status):
        return True, f"green: both levels passed on a clean tree for {head[:12]}"
    bad = [f"{k}: {status.get(k, 'not run')}"
           + ("" if status.get(k + "_tree") == "clean" or k not in status else f" (tree {status.get(k + '_tree', '?')})")
           for k in LEVELS if status.get(k) != "pass" or status.get(k + "_tree") != "clean"]
    return False, "not green on a clean tree: " + ", ".join(bad)


def record(head, update):
    path = status_path(ROOT, head)
    status = {}
    if os.path.exists(path):
        with open(path) as f:
            status = json.load(f)
    status.update(update)
    status["commit"] = head
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(status, f, indent=2)


def main():
    args = sys.argv[1:]
    head = out(ROOT, "rev-parse", "HEAD")

    if len(args) >= 3 and args[0] == "set":
        try:
            changes = dirty(ROOT)
        except RuntimeError:
            changes = True  # what git cannot call clean is recorded as dirty
        key, result = args[1], args[2]
        update = {key: result, key + "_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  key + "_tree": "dirty" if changes else "clean"}
        if len(args) > 3:
            update[key + "_note"] = " ".join(args[3:])
        record(head, update)
        print(f"{key}: {result} for {head[:12]}{' (dirty tree)' if changes else ''}")
        return 0

    if args[:1] == ["gate"] and (len(args) == 1 or (len(args) == 3 and args[1] == "--tree")):
        ok, why = gate(args[2] if len(args) == 3 else ROOT)
        print(why)
        return 0 if ok else 1

    if args == ["show"] or not args:
        try:
            status = read_status(ROOT, head)
        except (OSError, ValueError):
            print(f"no test status for {head[:12]}")
            return 1
        for key in LEVELS:
            if key in status:
                print(f"{key}: {status[key]} ({status.get(key + '_tree', '?')} tree, {status.get(key + '_at', '?')})"
                      + (f": {status[key + '_note']}" if key + "_note" in status else ""))
            else:
                print(f"{key}: not run")
        return 0

    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
