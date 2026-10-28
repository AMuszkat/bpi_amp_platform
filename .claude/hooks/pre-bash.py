#!/usr/bin/env python3
"""PreToolUse hook for Bash. Enforces the rules of docs/autonomous-development.md that
instructions alone cannot:

- no push to main or master, no force push;
- no private data in a commit (the staged additions are scanned);
- nothing reaches a board (ssh, scp, rsync, the deploy targets of the build scripts, the
  /board skill's tools/board.sh) while the lab lock .claude/lab.lock exists, or without a
  green test status for the exact commit being deployed (tests/.status/<commit>.json,
  written by /test, both rungs run on a clean tree), with a clean tree now. Looking at a board
  (tools/board.sh status or journal) only needs the lab to be unlocked. The lab lock of the
  main checkout counts in a worktree too;
- no command lifts a speaker's output cap (--person-present): that is a person's, at the speaker.

Override, logged: a command that starts with BPI_DEPLOY_OVERRIDE=1 deploys regardless of
the test status (never regardless of the lab lock).
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CHECK = os.path.join(HERE, "private-data-check.py")
LAB_LOCK = os.path.join(ROOT, ".claude", "lab.lock")
STATUS_DIR = os.path.join(ROOT, "tests", ".status")

PUSH_MAIN = re.compile(r"\bgit\s+push\b.*\b(main|master)\b")
PUSH_REFSPEC_MAIN = re.compile(r"\bgit\s+push\b.*:\s*(refs/heads/)?(main|master)\b")
FORCE = re.compile(r"\bgit\s+push\b.*(\s-f\b|\s--force\b|\s--force-with-lease\b)")
COMMIT = re.compile(r"\bgit\s+commit\b")
DEPLOY = re.compile(r"(^|[\s;&|(])(ssh|scp|rsync|sftp)\s|build-podman\.sh\b.*\s-install\b"
                    r"|\bmake\b.*\binstall|--target-host\b|--mode\s+(target-remote|both)\b"
                    r"|\btools/board\.sh\b")
# Reading a board changes nothing on it: the lab lock applies, the test status does not.
BOARD_READ = re.compile(r"\btools/board\.sh\s+(status|journal)\b")
# Lifting the output cap is a person's, at the speaker, in their own shell: never an agent's.
PERSON_ONLY = re.compile(r"--person-present\b")


def refuse(msg):
    print(msg, file=sys.stderr)
    return 2


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout


def check_commit(command):
    staged = git("diff", "--cached", "-U0")
    if "-a" in command.split() or "--all" in command:
        staged += git("diff", "-U0", "HEAD")
    added = "\n".join(l[1:] for l in staged.splitlines() if l.startswith("+") and not l.startswith("+++"))
    result = subprocess.run([sys.executable, CHECK, "--label", "the staged changes"],
                            input=added, text=True, capture_output=True)
    if result.returncode != 0:
        return refuse(result.stderr.rstrip())
    return 0


def lab_locks():
    """The lab lock of this checkout and of the main checkout, when this one is a worktree."""
    locks = [LAB_LOCK]
    common = git("rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    if common:
        locks.append(os.path.join(os.path.dirname(common), ".claude", "lab.lock"))
    return locks


def check_deploy(command):
    for lock in lab_locks():
        if os.path.exists(lock):
            return refuse(f"the lab is locked ({lock} exists): nothing may reach a board. "
                          "A person removes the lock when the boards are free.")
    if BOARD_READ.search(command) and not DEPLOY.search(BOARD_READ.sub("", command)):
        return 0
    override = command.lstrip().startswith("BPI_DEPLOY_OVERRIDE=1")
    head = git("rev-parse", "HEAD").strip()
    dirty = git("status", "--porcelain").strip()
    status_file = os.path.join(STATUS_DIR, f"{head}.json")
    status = {}
    if os.path.exists(status_file):
        try:
            with open(status_file) as f:
                status = json.load(f)
        except (OSError, ValueError):
            status = {"unreadable": status_file}
    green = (status.get("unit") == "pass" and status.get("vpair") == "pass"
             and status.get("unit_tree") == "clean" and status.get("vpair_tree") == "clean")

    if override:
        os.makedirs(STATUS_DIR, exist_ok=True)
        with open(os.path.join(STATUS_DIR, "overrides.log"), "a") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()} {head[:12]} "
                    f"{'dirty ' if dirty else ''}{command.strip()[:200]}\n")
        return 0
    if dirty:
        return refuse("the working tree has uncommitted changes: commit first, so that what is "
                      "deployed is what was tested.")
    if not green:
        return refuse(f"no green test status for commit {head[:12]}: run `/test all` first "
                      f"(it writes tests/.status/{head[:12]}....json). Status now: "
                      f"{json.dumps(status) if status else 'none'}. A person may bypass this "
                      "once with `BPI_DEPLOY_OVERRIDE=1 <command>`; the override is logged.")
    return 0


def main():
    data = json.load(sys.stdin)
    command = data.get("tool_input", {}).get("command", "")

    if PUSH_MAIN.search(command) or PUSH_REFSPEC_MAIN.search(command):
        return refuse("pushing to main or master is not allowed: push a branch and open a pull request.")
    if FORCE.search(command):
        return refuse("force pushes are not allowed: no history rewrite on shared branches.")
    if COMMIT.search(command):
        rc = check_commit(command)
        if rc:
            return rc
    if PERSON_ONLY.search(command):
        return refuse("--person-present lifts the output cap of a speaker: a person does that, in "
                      "their own shell at the speaker, after a smoke test and a listen. Not an agent.")
    if DEPLOY.search(command):
        return check_deploy(command)
    return 0


if __name__ == "__main__":
    sys.exit(main())
