#!/usr/bin/env python3
"""Records test results for the current commit in tests/.status/<commit>.json, and says whether
a checkout is green. Nothing enforces it any more: there is no test-status gate on a deploy
(2026-11-02, the person's decision); `gate` only reports, and `plan` is what `tools/test.sh
affected` runs.

    tools/status.py set <key> <pass|fail|skipped> [note]
    tools/status.py affected <pass|fail> <base> "<module ...>" [note]
    tools/status.py show
    tools/status.py plan               what `tools/test.sh affected` runs, on top of which commit
    tools/status.py gate [--tree DIR]  exit 0 if the checkout is green, else 1; says why

A commit is green in one of two ways, with the tree clean now:
- in full: `unit` and `vpair` both "pass", both run on a clean tree (tools/test.sh all);
- affected: an affected run (tools/test.sh affected) passed on a clean tree on top of a base,
  an ancestor commit green in full, the test modules it ran cover every file changed since the
  base, by the base's tests/impact.json, and no full run failed on a clean tree on this commit
  or since the base. The gate recomputes what changed and what had to run from git: it does
  not take the record's word for those. A file the map does not name needs both rungs in full.
Git is asked with submodules and untracked files shown whatever the local configuration says,
and a git command that fails refuses.
"""

import fnmatch
import glob
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMPACT = "tests/impact.json"
VPAIR = "tests/vpair"
# How far back `plan` looks for a commit green in full.
BASE_SEARCH = 200
IMPORTS = re.compile(r"^\s*(?:from|import)\s+(test_\w+)", re.M)
TEST_MODULE = re.compile(r"tests/vpair/(test_\w+)\.py")


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
    return all(status.get(k) == "pass" and status.get(k + "_tree") == "clean" for k in ("unit", "vpair"))


def find_base(tree, head):
    """The nearest commit green in full among head and its ancestors, or None."""
    for commit in out(tree, "rev-list", f"--max-count={BASE_SEARCH}", head).split():
        try:
            if green_in_full(read_status(tree, commit)):
                return commit
        except (OSError, ValueError):
            pass
    return None


def changed_files(tree, base, head=None):
    """The files that differ between base and head, or, with no head, between base and the working
    tree (untracked files included). Renames count as both paths; a submodule's commit counts
    whatever the local configuration ignores."""
    if head:
        result = git(tree, "diff-tree", "-r", "-z", "--name-only", "--no-renames", "--ignore-submodules=none",
                     base, head)
    else:
        result = git(tree, "diff", "--name-only", "--no-renames", "-z", "--ignore-submodules=none", base)
    if result.returncode:
        raise RuntimeError(f"git diff {base[:12]}: {result.stderr.strip()}")
    files = set(result.stdout.split("\0"))
    if head is None:
        others = git(tree, "ls-files", "--others", "--exclude-standard", "-z")
        if others.returncode:
            raise RuntimeError(f"git ls-files: {others.stderr.strip()}")
        files |= set(others.stdout.split("\0"))
    return sorted(files - {""})


def load_impact(tree, base):
    """The base commit's tests/impact.json, or the reason there is none to use."""
    shown = git(tree, "show", f"{base}:{IMPACT}")
    if shown.returncode:
        return None, f"{base[:12]} has no {IMPACT}"
    try:
        impact = json.loads(shown.stdout)
        rules = impact["rules"]
        if impact.get("version") != 1 or not isinstance(rules, list):
            raise ValueError("not version 1 with a list of rules")
        for rule in rules:
            if not isinstance(rule.get("paths"), list) or not (rule.get("tests") == "self"
                                                               or isinstance(rule.get("tests"), list)):
                raise ValueError(f"a rule without paths, or whose tests are neither a list nor \"self\": {rule}")
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        return None, f"{IMPACT} of {base[:12]} does not read ({e})"
    return impact, None


def classify(impact, path):
    """(tests, None) for a path the map covers, tests a list of module names or "self"; (None,
    why) for one that needs both rungs in full."""
    for rule in impact["rules"]:
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in rule["paths"]):
            return rule["tests"], None
    return None, f"no rule of {IMPACT} names it"


def with_importers(tree, modules):
    """The modules, and every test module of tests/vpair that imports one of them, transitively."""
    imports = {}
    for path in glob.glob(os.path.join(tree, VPAIR, "test_*.py")):
        with open(path, encoding="utf-8") as f:
            imports[os.path.basename(path)[:-3]] = set(IMPORTS.findall(f.read()))
    result, grew = set(modules), True
    while grew:
        grew = False
        for module, imported in imports.items():
            if module not in result and imported & result:
                result.add(module)
                grew = True
    return sorted(result)


def plan(tree, base, head=None):
    """What an affected run on top of base must run: {"base", "changed", "tests", "full"}, where
    "full" lists (path, why) for the files that need both rungs in full."""
    files = changed_files(tree, base, head)
    impact, why = load_impact(tree, base)
    if impact is None:
        return {"base": base, "changed": files, "tests": [], "full": [("every file", why)]}
    tests, deleted, full = set(), set(), []
    for path in files:
        run, why = classify(impact, path)
        if run is None:
            full.append((path, why))
        elif run == "self":
            module = TEST_MODULE.fullmatch(path)
            if not module:
                full.append((path, f"a \"self\" rule of {IMPACT} names a file that is not a test module"))
            elif os.path.exists(os.path.join(tree, path)):
                tests.add(module.group(1))
            else:
                deleted.add(module.group(1))  # runs no more, but whatever still imports it does
        else:
            tests.update(run)
    tests = set(with_importers(tree, tests | deleted)) - deleted
    return {"base": base, "changed": files, "tests": sorted(tests), "full": full}


def failed_since(tree, base, head):
    """Why a full run that failed on a clean tree on head or since base bars an affected run,
    or None. Such a run found something the map does not see: only a full run that passes on
    head clears it."""
    since = git(tree, "rev-list", f"{base}..{head}")
    if since.returncode:
        raise RuntimeError(f"git rev-list {base[:12]}..{head[:12]}: {since.stderr.strip()}")
    for commit in since.stdout.split():
        try:
            status = read_status(tree, commit)
        except (OSError, ValueError):
            continue
        failed = [k for k in ("unit", "vpair") if status.get(k) == "fail" and status.get(k + "_tree") == "clean"]
        if failed:
            return (f"a full run failed on {commit[:12]} ({', '.join(f'{k}: fail' for k in failed)}) since "
                    f"{base[:12]}: only a full run that passes clears it")
    return None


def check_affected(tree, head, status):
    """(True, why) when the affected run recorded for head makes it green, else (False, why)."""
    if status.get("affected") != "pass" or status.get("affected_tree") != "clean":
        tree_said = status.get("affected_tree", "?")
        return False, f"affected: {status.get('affected')}" + ("" if tree_said == "clean" else f" (tree {tree_said})")
    base, ran = status.get("affected_base"), status.get("affected_ran")
    if not isinstance(base, str) or not re.fullmatch(r"[0-9a-f]{40}", base) or not isinstance(ran, list):
        return False, "its record names no base commit or no list of what ran"
    try:
        base_status = read_status(tree, base)
    except (OSError, ValueError) as e:
        return False, f"its base {base[:12]} has no readable test status ({e})"
    if not green_in_full(base_status):
        return False, f"its base {base[:12]} is not green in full"
    if git(tree, "merge-base", "--is-ancestor", base, head).returncode:
        return False, f"its base {base[:12]} is not an ancestor of {head[:12]}"
    failure = failed_since(tree, base, head)
    if failure:
        return False, failure
    needed = plan(tree, base, head)
    if needed["full"]:
        path, why = needed["full"][0]
        more = len(needed["full"]) - 1
        return False, (f"{path} changed since {base[:12]} and needs both rungs in full ({why})"
                       + (f", and {more} more file(s)" if more else ""))
    missing = sorted(set(needed["tests"]) - set(ran))
    if missing:
        return False, f"it did not run {', '.join(missing)}, which cover files changed since {base[:12]}"
    return True, (f"green: an affected run passed on a clean tree for {head[:12]} "
                  f"({', '.join(ran) if ran else 'nothing to run'}) on top of {base[:12]}, green in full; "
                  f"{len(needed['changed'])} file(s) changed since")


def gate(tree):
    """(True, why) when the checkout at tree may reach a board, else (False, why). The lab lock and
    the override are the callers'."""
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
        return False, ("the working tree has uncommitted changes: commit first, so that what is deployed "
                       "is what was tested")
    try:
        status = read_status(tree, head)
    except (OSError, ValueError) as e:
        return False, (f"no readable test status {status_path(tree, head)} ({e}): run tools/test.sh all "
                       "first, or tools/test.sh affected after a fix on top of a commit green in full")
    if green_in_full(status):
        return True, f"green: both rungs passed on a clean tree for {head[:12]}"
    affected_why = None
    if "affected" in status:
        try:
            ok, affected_why = check_affected(tree, head, status)
        except RuntimeError as e:
            ok, affected_why = False, str(e)
        if ok:
            return True, affected_why
    bad = [f"{k}: {status.get(k, 'not run')}"
           + ("" if status.get(k + "_tree") == "clean" or k not in status else f" (tree {status.get(k + '_tree', '?')})")
           for k in ("unit", "vpair") if status.get(k) != "pass" or status.get(k + "_tree") != "clean"]
    return False, ("the test status of this commit is not green on a clean tree: " + ", ".join(bad)
                   + (f"; its affected run does not count: {affected_why}" if affected_why else ""))


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
    try:
        changes = dirty(ROOT)
    except RuntimeError:
        changes = True  # what git cannot call clean is recorded as dirty
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tree = "dirty" if changes else "clean"

    if len(args) >= 3 and args[0] == "set":
        key, result = args[1], args[2]
        update = {key: result, key + "_at": now, key + "_tree": tree}
        if len(args) > 3:
            update[key + "_note"] = " ".join(args[3:])
        record(head, update)
        print(f"{key}: {result} for {head[:12]}{' (dirty tree)' if changes else ''}")
        return 0

    if len(args) >= 4 and args[0] == "affected":
        result, base, ran = args[1], args[2], args[3].split()
        update = {"affected": result, "affected_at": now, "affected_tree": tree, "affected_base": base,
                  "affected_ran": ran}
        if len(args) > 4:
            update["affected_note"] = " ".join(args[4:])
        record(head, update)
        print(f"affected: {result} for {head[:12]} on top of {base[:12]}{' (dirty tree)' if changes else ''}")
        return 0

    if args == ["plan"]:
        base = find_base(ROOT, head)
        if base is None:
            print(f"no commit green in full among {head[:12]} and its last {BASE_SEARCH - 1} ancestors in "
                  f"{ROOT}/tests/.status: run tools/test.sh all", file=sys.stderr)
            return 1
        try:
            failure = failed_since(ROOT, base, head)
            needed = plan(ROOT, base)
        except RuntimeError as e:
            print(e, file=sys.stderr)
            return 1
        if failure:
            print(f"{failure}: run tools/test.sh all", file=sys.stderr)
            return 1
        for path, why in needed["full"]:
            print(f"needs both rungs in full: {path} ({why})", file=sys.stderr)
        if needed["full"]:
            return 1
        print(f"on top of {base[:12]}, green in full: {len(needed['changed'])} file(s) changed; "
              f"runs {', '.join(needed['tests']) if needed['tests'] else 'nothing'}", file=sys.stderr)
        print(base)
        print(" ".join(needed["tests"]))
        return 0

    if args[:1] == ["gate"] and (len(args) == 1 or (len(args) == 3 and args[1] == "--tree")):
        ok, why = gate(args[2] if len(args) == 3 else ROOT)
        print(why)
        return 0 if ok else 1

    if args == ["show"] or not args:
        status = {}
        if os.path.exists(status_path(ROOT, head)):
            status = read_status(ROOT, head)
        print(f"commit {head[:12]}{' (dirty tree)' if changes else ''}")
        print(json.dumps(status, indent=2) if status else "no results recorded for this commit")
        return 0

    print(__doc__.split("\n\n")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
