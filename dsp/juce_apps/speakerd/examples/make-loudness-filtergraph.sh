#!/bin/bash
# Makes examples/loudness.filtergraph: the AudioPluginHost session of the complete loudness
# build between the stream's FL and the woofer, its parameters at examples/loudness.params.json,
# its STATE what that build's getStateInformation() returns through JUCE's LV2 hosting. Run it
# again after a change to loudness's parameters or state. See speakerd's README.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
cd "$ROOT"
. tools/container.sh
container_or_native "$0" "$@"
ensure_juce

tools/build.sh loudness complete
tools/build.sh speakerd
cmake --build build/speakerd --target speakerd-make-filtergraph

# An empty HOME: exactly the bundle named, whatever ~/.lv2 holds.
home="$(mktemp -d)"
trap 'rm -rf "$home"' EXIT
here=dsp/juce_apps/speakerd/examples
HOME="$home" build/speakerd/speakerd-make-filtergraph_artefacts/Release/speakerd-make-filtergraph \
    --plugin https://github.com/AMuszkat/bpi_amp_dsp_tuning/loudness \
    --params "$here/loudness.params.json" \
    --lv2-bundle build/loudness/loudness_artefacts/Release/LV2/loudness.lv2 \
    "$here/loudness.filtergraph"
