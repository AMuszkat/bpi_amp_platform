#!/bin/bash
# Renders the boards in Blender from the KiCad projects, and rebuilds the README's slider from
# the render and the photo. See .claude/skills/render.
#
#     tools/render.sh [--quality preview|final] [--blend FILE] [--keep-export] [--no-slider]
#
# preview: 1600 px wide, 48 samples, about a minute. final (the default): 3200 px, 256
# samples (about 8 minutes on 24 cores), what goes into docs/images/. Needs KiCad 9 or later
# (kicad-cli); Blender comes from tools/blender.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
quality=final
slider=1
pass=()
while [ $# -gt 0 ]; do
    case "$1" in
        --quality) quality="${2:?--quality preview|final}"; shift 2 ;;
        --blend) pass+=(--blend "${2:?--blend FILE}"); shift 2 ;;
        --keep-export) pass+=(--keep-export); shift ;;
        --no-slider) slider=0; shift ;;
        *) echo "unknown option '$1'" >&2; sed -n '5p' "$0" >&2; exit 2 ;;
    esac
done
out="build/boards/boards-render-$quality.png"
[ "$quality" = final ] && pass+=(--publish docs/images/boards-render.jpg)
blender="$(tools/blender.sh --path)"
python3 hardware/render/render.py --blender "$blender" --quality "$quality" --out "$out" "${pass[@]}"
if [ "$quality" = final ] && [ "$slider" = 1 ]; then
    python3 hardware/render/slider.py
fi
