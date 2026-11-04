#!/usr/bin/env python3
"""The checks of tools/test.sh that cover the files changed, by tests/impact.json.

    tools/impact.py [REF]      the checks, one per line; on stderr, the files no rule names
                               and the by_hand advice of the rules that decide a file changed

The files changed are those that differ from REF in the working tree, untracked files
included. REF defaults to the merge base with develop, or to HEAD on develop itself. A rule's
optional "by_hand" is advice: a command its checks leave out, to run by hand after some edits
(the Engine's rule), or the only thing to run (ampScope's: no check builds it). It is said once
per rule, with the rule's files that changed.
"""

import fnmatch
import glob
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VPAIR = os.path.join(ROOT, "tests", "vpair")
IMPORTS = re.compile(r"^\s*(?:from|import)\s+(test_\w+)", re.M)


def git(*args):
    return subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True, check=True).stdout


def default_ref():
    branch = git("rev-parse", "--abbrev-ref", "HEAD").strip()
    if branch == "develop":
        return "HEAD"
    try:
        return git("merge-base", "HEAD", "develop").strip()
    except subprocess.CalledProcessError:
        return "HEAD"


def changed_files(ref):
    files = set(git("diff", "--name-only", "--no-renames", "-z", "--ignore-submodules=none", ref).split("\0"))
    files |= set(git("ls-files", "--others", "--exclude-standard", "-z").split("\0"))
    return sorted(files - {""})


def with_importers(modules):
    """The modules, and every test module of tests/vpair that imports one of them, transitively."""
    imports = {}
    for path in glob.glob(os.path.join(VPAIR, "test_*.py")):
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


def rule_for(rules, path):
    """The rule that decides `path`: the first whose pattern matches it, or None."""
    for rule in rules:
        if any(fnmatch.fnmatchcase(path, p) for p in rule["paths"]):
            return rule
    return None


def checks_for(rules, files):
    """(checks, uncovered): the checks to run, in the rules' order, and the files no rule names."""
    checks, modules, uncovered = [], set(), []
    for path in files:
        rule = rule_for(rules, path)
        if rule is None:
            uncovered.append(path)
            continue
        for check in rule["checks"]:
            if check == "plugin":
                check = "plugin " + path.split("/")[2]
            elif check == "self":
                module = os.path.basename(path)[:-3]
                if os.path.exists(os.path.join(ROOT, path)):
                    modules.add(module)
                modules.update(m for m in with_importers([module]) if m != module)
                continue
            if check not in checks:
                checks.append(check)
    if modules:
        checks.append("module " + " ".join(sorted(modules)))
    return checks, uncovered


def by_hand_for(rules, files):
    """[(advice, paths)]: the "by_hand" of every rule that decides one of the files, once per rule,
    with the files it decides, in the order the files reach the rules."""
    found = {}
    for path in files:
        rule = rule_for(rules, path)
        if rule is not None and rule.get("by_hand"):
            found.setdefault(id(rule), (rule["by_hand"], []))[1].append(path)
    return list(found.values())


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    ref = argv[0] if argv else default_ref()
    with open(os.path.join(ROOT, "tests", "impact.json")) as f:
        rules = json.load(f)["rules"]
    files = changed_files(ref)
    checks, uncovered = checks_for(rules, files)
    for path in uncovered:
        print(f"no check covers {path}: the levels do (tools/test.sh all)", file=sys.stderr)
    for advice, paths in by_hand_for(rules, files):
        print(f"by hand, for {', '.join(paths)}: {advice}", file=sys.stderr)
    print(f"{len(files)} file(s) changed since {ref[:12]}", file=sys.stderr)
    for check in checks:
        print(check)
    return 0


if __name__ == "__main__":
    sys.exit(main())
