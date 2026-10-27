#!/usr/bin/env bash
#
# Launch the near-field measurement GUI in the conda environment created by
# ../setup_env.sh. Does NOT check or install dependencies -- run that once first.
# (The tuning / filter-design GUI is a separate, independent tool -- see ../tuning/run.sh.)
# Captures are written to the data folder (NF_DATA_DIR, default python_dsp_tools/data).
#
# Foreground (blocks the terminal):
#     ./run.sh [args...]
# Background/detached (frees the terminal, logs to $NEAR_FIELD_LOG, survives terminal close):
#     ./run.sh --bg [args...]
#
# Any other arguments are forwarded to near_field_gui.py, e.g.:
#     ./run.sh --measure ff
#     ./run.sh --bg --input-device "USB"
#
set -euo pipefail

ENV_NAME="${NEAR_FIELD_ENV:-near_field}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="${NEAR_FIELD_LOG:-/tmp/nf_gui.log}"

find_conda() {
    if command -v conda >/dev/null 2>&1; then echo conda; return 0; fi
    for base in "$HOME/miniforge3" "$HOME/miniconda3" "$HOME/anaconda3"; do
        if [ -x "$base/bin/conda" ]; then echo "$base/bin/conda"; return 0; fi
    done
    return 1
}

CONDA="$(find_conda)" || {
    echo "ERROR: conda not found. Run ./setup_env.sh first (needs miniforge)." >&2
    exit 1
}

# Pull out --bg; forward everything else to the GUI.
BG=0
ARGS=()
for a in "$@"; do
    if [ "$a" = "--bg" ]; then BG=1; else ARGS+=("$a"); fi
done

if [ "$BG" -eq 1 ]; then
    nohup "$CONDA" run --no-capture-output -n "$ENV_NAME" \
        python "$SCRIPT_DIR/near_field_gui.py" ${ARGS[@]+"${ARGS[@]}"} \
        >"$LOG" 2>&1 &
    disown || true
    echo "GUI launched in background (PID $!). Logs: $LOG"
    echo "Watch logs:  tail -f $LOG"
    echo "Stop it:     pkill -f near_field_gui.py"
    exit 0
fi

exec "$CONDA" run --no-capture-output -n "$ENV_NAME" \
    python "$SCRIPT_DIR/near_field_gui.py" ${ARGS[@]+"${ARGS[@]}"}
