#!/bin/bash
# Summarises a PipeWire graph. See .claude/skills/pw-inspect.
#     tools/pw-inspect.sh [left|right]
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
. "$ROOT/tools/container.sh"
# A virtual speaker lives where the virtual pair runs; any other PipeWire is the one here.
[ -n "${1:-}" ] && container_or_native "$0" "$@"
if [ -n "${1:-}" ]; then
    eval "$("$ROOT/tests/vpair/vpair.sh" env "$1")" || exit 1
    echo "=== virtual speaker: $1"
fi
dump="$(timeout 10 pw-dump 2>/dev/null)" || { echo "pw-dump failed: is PipeWire running and XDG_RUNTIME_DIR set?" >&2; exit 1; }
python3 - "$dump" <<'PY'
import json, sys
objs = json.loads(sys.argv[1])
by_id = {o["id"]: o for o in objs}
print("--- settings")
for o in objs:
    if o["type"].endswith("Metadata") and o.get("props", {}).get("metadata.name") == "settings":
        for m in o.get("metadata", []):
            print(f"  {m['key']} = {m['value']}")
print("--- nodes")
for o in objs:
    if o["type"].endswith("Node"):
        p = o["info"]["props"]
        print(f"  [{o['id']:>3}] {p.get('node.name','?'):32} {p.get('media.class','-'):22} {o['info'].get('state','?')}")
print("--- ports")
for o in objs:
    if o["type"].endswith("Port"):
        p = o["info"]["props"]
        node = by_id.get(p.get("node.id"), {}).get("info", {}).get("props", {}).get("node.name", "?")
        print(f"  [{o['id']:>3}] {node}:{p.get('port.name','?')}  ({o['info'].get('direction','?')})")
print("--- links")
for o in objs:
    if o["type"].endswith("Link"):
        i = o["info"]
        def pn(pid):
            port = by_id.get(pid, {}).get("info", {}).get("props", {})
            node = by_id.get(port.get("node.id"), {}).get("info", {}).get("props", {}).get("node.name", "?")
            return f"{node}:{port.get('port.name','?')}"
        print(f"  {pn(i['output-port-id'])}  ->  {pn(i['input-port-id'])}  ({i.get('state','?')})")
PY
