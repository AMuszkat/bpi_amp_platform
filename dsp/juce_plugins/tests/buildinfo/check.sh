#!/bin/bash
# Checks that built LV2 bundles of a plugin carry its version and source identity the way
# juce_plugins/README.md ("Versions and source identity") says, and that they agree with
# tools/plugin-versions.sh list, i.e. with what a build of this tree embeds: buildinfo.ttl
# exactly, the manifest's rdfs:seeAlso line once and last, and the bpi-build-info string in the
# binary. Part of rung 1 (tools/test.sh unit), on the bundles plugin-check builds.
#
#     dsp/juce_plugins/tests/buildinfo/check.sh <plugin> <bundle.lv2>...
set -euo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../tools" && pwd)/common.sh"

plugin="${1:?usage: $0 <plugin> <bundle.lv2>...}"
shift
[ $# -gt 0 ] || { echo "usage: $0 <plugin> <bundle.lv2>..." >&2; exit 2; }

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

    see_also="<$uri> rdfs:seeAlso <buildinfo.ttl> ."
    count="$(grep -cxF "$see_also" "$bundle/manifest.ttl" || true)"
    if [ "$count" != 1 ] || [ "$(tail -n 1 "$bundle/manifest.ttl")" != "$see_also" ]; then
        problems+=("the manifest has the seeAlso line $count times, or not as its last line")
    fi

    so="$bundle/lib$target.so"
    if ! grep -aqF "bpi-build-info: $uri version $version source $id" "$so" 2> /dev/null; then
        problems+=("lib$target.so does not carry 'bpi-build-info: $uri version $version source $id'")
    fi

    if [ ${#problems[@]} = 0 ]; then
        echo "buildinfo: $target.lv2: version $version, source $id: buildinfo.ttl, manifest and binary agree"
    else
        joined="$(printf '; %s' "${problems[@]}")"
        echo "buildinfo: $target.lv2 FAILED: ${joined#; }"
        failed=1
    fi
done
exit $failed
