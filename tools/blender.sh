#!/bin/bash
# Runs Blender, the pinned LTS release, fetching it into build/tools/ the first time (about
# 350 MB, checked against the release's SHA-256). Blender runs natively, not in the development
# container: it is one self-contained folder and renders on the CPU.
#
#     tools/blender.sh --version
#     tools/blender.sh --path              # print the program's path (fetching it if needed)
#     tools/blender.sh [blender arguments ...]
#
# BLENDER=/path/to/blender uses another Blender (4.2 or later) instead.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION=4.5.15
SHA256=0d2b8bb2efe97e6c8eb57de6b7322f8800e1984e3125b60decca5f6771e4773e
NAME="blender-$VERSION-linux-x64"
DIR="$ROOT/build/tools"

if [ -n "${BLENDER:-}" ]; then
    program="$BLENDER"
else
    program="$DIR/$NAME/blender"
    if [ ! -x "$program" ]; then
        [ "$(uname -m)" = x86_64 ] || { echo "tools/blender.sh fetches Blender for x86_64 Linux only: set BLENDER" >&2; exit 2; }
        mkdir -p "$DIR"
        archive="$DIR/$NAME.tar.xz"
        echo "fetching Blender $VERSION into ${DIR#"$ROOT"/} ..." >&2
        curl -fL --retry 3 -o "$archive.part" "https://download.blender.org/release/Blender${VERSION%.*}/$NAME.tar.xz"
        echo "$SHA256  $archive.part" | sha256sum -c --quiet - || { rm -f "$archive.part"; echo "the download's SHA-256 is wrong" >&2; exit 1; }
        tar -xf "$archive.part" -C "$DIR"
        rm -f "$archive.part"
    fi
fi
if [ "${1:-}" = --path ]; then
    echo "$program"
    exit 0
fi
exec "$program" "$@"
