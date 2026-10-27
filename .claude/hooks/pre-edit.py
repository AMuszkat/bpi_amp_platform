#!/usr/bin/env python3
"""PreToolUse hook for Edit, Write and MultiEdit: refuses private data in tracked files.

Reads the hook's JSON from stdin. Files that are meant to hold private data (the gitignored
local settings) and build output are exempt.
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CHECK = os.path.join(HERE, "private-data-check.py")
EXEMPT = ("dsp/config/local.env", "firmware/local.mk", ".claude/lab.lock", "build/",
          "tests/.status/", "CMakeUserPresets.json", ".claude/settings.local.json")


def main():
    data = json.load(sys.stdin)
    tool_input = data.get("tool_input", {})
    path = tool_input.get("file_path", "")
    root = data.get("cwd", os.getcwd())
    rel = os.path.relpath(path, root) if path else ""
    if any(rel == e or rel.startswith(e) for e in EXEMPT):
        return 0

    pieces = []
    if "content" in tool_input:
        pieces.append(tool_input["content"])
    if "new_string" in tool_input:
        pieces.append(tool_input["new_string"])
    for edit in tool_input.get("edits", []):
        pieces.append(edit.get("new_string", ""))

    if not pieces:
        return 0

    result = subprocess.run([sys.executable, CHECK, "--label", rel or "this edit"],
                            input="\n".join(pieces), text=True, capture_output=True)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
