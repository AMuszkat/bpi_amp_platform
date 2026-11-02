#!/usr/bin/env python3
"""PreToolUse hook for Bash. Enforces the rules of docs/autonomous-development.md that
instructions alone cannot:

- no push to main or master, no force push;
- no private data in a commit (the staged additions are scanned);
- nothing reaches a board (ssh, scp, rsync, the deploy targets of the build scripts, any make
  on the firmware, the installers' remote mode, the /board skill's tools/board.sh) while a lab lock
  (.claude/lab.lock, in the checkout the command runs in or in the main checkout) exists. There is
  no test-status gate (2026-11-02, the person's decision): a deploy does not need a green commit.

It is a tripwire on the command text, not a sandbox: tools/board.sh checks the same lock
itself. Whatever goes wrong inside the hook on a guarded path blocks the command.
"""

import importlib.util
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CHECK = os.path.join(HERE, "private-data-check.py")

PUSH_MAIN = re.compile(r"\bgit\s+push\b.*\b(main|master)\b")
PUSH_REFSPEC_MAIN = re.compile(r"\bgit\s+push\b.*:\s*(refs/heads/)?(main|master)\b")
FORCE = re.compile(r"\bgit\s+push\b.*(\s-f\b|\s--force\b|\s--force-with-lease\b)")
COMMIT = re.compile(r"\bgit\s+commit\b")
# A remote program, also behind a path or in quotes (/usr/bin/ssh, "ssh"); the build scripts'
# deploy targets; the installers' remote modes in any spelling; the board script.
DEPLOY = re.compile(r"""(^|[\s;&|(`'"])(?:[\w./-]*/)?["']?(ssh|scp|rsync|sftp)["']?(\s|$)"""
                    r"|build-podman\.sh\b.*\s-install\b|\bmake\b.*\binstall"
                    r"""|--target-host\b|--mode[\s=]+["']?(target-remote|both)\b"""
                    r"|\btools/board\.sh\b")
# make on the firmware: its targets copy kernels, modules and device trees to a board and
# change its /boot, so every make there is deploy-class, whatever the target.
MAKE = re.compile(r"(^|[\s;&|(])make\b")


def refuse(msg):
    print(msg, file=sys.stderr)
    return 2


def git(cwd, *args):
    return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True).stdout


def checkout_of(cwd):
    """The checkout the command runs in: the session's working directory's, else this one."""
    top = git(cwd, "rev-parse", "--show-toplevel").strip() if cwd and os.path.isdir(cwd) else ""
    return top or ROOT


def check_commit(tree, command):
    staged = git(tree, "diff", "--cached", "-U0")
    if "-a" in command.split() or "--all" in command:
        staged += git(tree, "diff", "-U0", "HEAD")
    added = "\n".join(l[1:] for l in staged.splitlines() if l.startswith("+") and not l.startswith("+++"))
    result = subprocess.run([sys.executable, CHECK, "--label", "the staged changes"],
                            input=added, text=True, capture_output=True)
    if result.returncode != 0:
        return refuse(result.stderr.rstrip())
    return 0


def lab_locks(tree):
    """The lab lock of the checkout and of the main checkout, when that one is a worktree."""
    locks = [os.path.join(tree, ".claude", "lab.lock"), os.path.join(ROOT, ".claude", "lab.lock")]
    common = git(tree, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    if common:
        locks.append(os.path.join(os.path.dirname(common), ".claude", "lab.lock"))
    return list(dict.fromkeys(locks))


def check_deploy(tree, command):
    for lock in lab_locks(tree):
        if os.path.exists(lock):
            return refuse(f"the lab is locked ({lock} exists): nothing may reach a board. "
                          "A person removes the lock when the boards are free.")
    return 0


def touches_firmware_make(command, cwd):
    if not MAKE.search(command):
        return False
    return "firmware" in command or "/firmware" in (cwd or "")


def main():
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
        command = data.get("tool_input", {}).get("command", "")
        cwd = data.get("cwd")
        tree = checkout_of(cwd)
    except Exception as e:  # cannot even read the command: block anything that looks guarded
        if any(p.search(raw) for p in (DEPLOY, COMMIT, MAKE)):
            return refuse(f"the pre-bash hook could not read the command ({type(e).__name__}); "
                          "blocking it.")
        return 0

    if PUSH_MAIN.search(command) or PUSH_REFSPEC_MAIN.search(command):
        return refuse("pushing to main or master is not allowed: push a branch and open a pull request.")
    if FORCE.search(command):
        return refuse("force pushes are not allowed: no history rewrite on shared branches.")
    try:
        if COMMIT.search(command):
            rc = check_commit(tree, command)
            if rc:
                return rc
        if DEPLOY.search(command) or touches_firmware_make(command, cwd):
            return check_deploy(tree, command)
    except Exception as e:  # a guarded path fails closed
        return refuse(f"the pre-bash hook failed on a guarded command ({type(e).__name__}: {e}); "
                      "blocking it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
