#!/usr/bin/env python3
"""PostToolUse hook for Edit, Write and MultiEdit: after a change to a Markdown file under
docs/ or tests/, checks that every Mermaid block still parses, when the checker is
installed (tools/mermaid-check, see its README). Otherwise says so once and lets it pass.
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CHECKER = os.path.join(ROOT, "tools", "mermaid-check", "check.js")
MODULES = os.path.join(ROOT, "tools", "mermaid-check", "node_modules", "mermaid")


def main():
    data = json.load(sys.stdin)
    path = data.get("tool_input", {}).get("file_path", "")
    if not path.endswith(".md"):
        return 0
    rel = os.path.relpath(path, ROOT)
    if not (rel.startswith("docs/") or rel.startswith("tests/") or rel.startswith("dsp/docs/")):
        return 0
    with open(path, encoding="utf-8", errors="replace") as f:
        if "```mermaid" not in f.read():
            return 0
    if not os.path.isdir(MODULES):
        print(f"{rel} has Mermaid diagrams but tools/mermaid-check is not installed "
              "(cd tools/mermaid-check && npm install); the diagrams were not checked.")
        return 0
    result = subprocess.run(["node", CHECKER, path], capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        print(f"a Mermaid diagram in {rel} does not parse:\n{result.stdout}{result.stderr}", file=sys.stderr)
        return 2
    print(result.stdout.strip().splitlines()[-1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
