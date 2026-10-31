#!/bin/bash
# Checks that built LV2 bundles of a plugin carry its version and source identity the way
# juce_plugins/README.md ("Versions and source identity") says, and that they agree with
# tools/plugin-versions.sh list, i.e. with what a build of this tree embeds: buildinfo.ttl
# exactly, a manifest.ttl that does not mention it (no LV2 host may parse it, so a damaged one
# can never stop the bundle loading), the bpi-build-info string in the binary and, with
# --arch, that the binary is code for that machine. Part of rung 1 (tools/test.sh unit): on the
# x86_64 bundles plugin-check builds and the copies JUCE's copy step made of them in ~/.lv2, and
# on one aarch64 bundle of tools/build.sh and its copy in the build's copied/lv2.
#
#     dsp/juce_plugins/tests/buildinfo/check.sh [--arch x86_64|aarch64] <plugin> <bundle.lv2>...
set -euo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../tools" && pwd)/common.sh"
usage="usage: $0 [--arch x86_64|aarch64] <plugin> <bundle.lv2>..."

arch=""
machine=""
if [ "${1:-}" = --arch ]; then
    arch="${2:-}"
    shift 2 || true
    case "$arch" in
        x86_64) machine="Advanced Micro Devices X86-64" ;;
        aarch64) machine="AArch64" ;;
        *) echo "$usage" >&2; exit 2 ;;
    esac
    command -v readelf > /dev/null || { echo "buildinfo: --arch needs readelf (binutils)" >&2; exit 2; }
fi
plugin="${1:-}"
[ -n "$plugin" ] && shift && [ $# -gt 0 ] || { echo "$usage" >&2; exit 2; }

line="$("$TOOLS_DIR/plugin-versions.sh" list | awk -v p="$plugin" '$1 == p')"
[ -n "$line" ] || { echo "buildinfo: $plugin is not a plugin" >&2; exit 2; }
read -r _ version id <<< "$line"
base="$(plugin_lv2_uri "")"
base="${base%/}"

expected="$(mktemp)"
trap 'rm -f "$expected"' EXIT
failed=0

for bundle in "$@"; do
    target="$(basename "$bundle" .lv2)"
    uri="$base/$target"
    problems=()

    printf '@prefix bpi: <%s#> .\n\n<%s>\n    bpi:version "%s" ;\n    bpi:sourceId "%s" .\n' \
        "$base" "$uri" "$version" "$id" > "$expected"
    if [ ! -f "$bundle/buildinfo.ttl" ]; then
        problems+=("no buildinfo.ttl")
    elif ! cmp -s "$expected" "$bundle/buildinfo.ttl"; then
        problems+=("buildinfo.ttl is not what this tree builds ($(grep -o 'sourceId "[^"]*"' "$bundle/buildinfo.ttl" || echo 'unreadable'), expected version $version, source $id)")
    fi

    if [ ! -f "$bundle/manifest.ttl" ]; then
        problems+=("no manifest.ttl")
    elif grep -qF "buildinfo" "$bundle/manifest.ttl"; then
        problems+=("manifest.ttl mentions buildinfo.ttl: $(grep -m 1 -nF "buildinfo" "$bundle/manifest.ttl")")
    fi

    so="$bundle/lib$target.so"
    if ! grep -aqF "bpi-build-info: $uri version $version source $id" "$so" 2> /dev/null; then
        problems+=("lib$target.so does not carry 'bpi-build-info: $uri version $version source $id'")
    fi
    if [ -n "$arch" ] && ! readelf -h "$so" 2> /dev/null | grep -qF "$machine"; then
        problems+=("lib$target.so is not $arch code ($(readelf -h "$so" 2> /dev/null | sed -n 's/^ *Machine: *//p' || true))")
    fi

    if [ ${#problems[@]} = 0 ]; then
        echo "buildinfo: $bundle: version $version, source $id${arch:+, $arch}: buildinfo.ttl and binary agree, the manifest does not name it"
    else
        joined="$(printf '; %s' "${problems[@]}")"
        echo "buildinfo: $bundle FAILED: ${joined#; }"
        failed=1
    fi
done
exit $failed
