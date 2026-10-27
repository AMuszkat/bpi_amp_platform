#!/usr/bin/env bash
#
# Launch the tuning (DSP analysis & filter-design) GUI, dsp_gui.py, in the conda environment
# created by ../setup_env.sh. It is independent of the measurement GUI
# (../measurement/run.sh); the two share only the conda env and the nearfield package.
# Does NOT check or install dependencies -- run ./setup_env.sh once first.
#
# On startup it auto-loads ir_result_wf.wav / ir_result_tw.wav from the data folder
# (NF_DATA_DIR, default python_dsp_tools/data), falling back to the bundled examples/.
#
# Foreground (blocks the terminal):
#     ./run.sh [args...]
# Background/detached (frees the terminal, logs to $NEAR_FIELD_DSP_LOG, survives close):
#     ./run.sh --bg [args...]
#
# Any other arguments are forwarded to dsp_gui.py, e.g.:
#     ./run.sh --no-load
#     ./run.sh --bg --opengl
#
set -euo pipefail

ENV_NAME="${NEAR_FIELD_ENV:-near_field}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="${NEAR_FIELD_DSP_LOG:-/tmp/nf_dsp_gui.log}"

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
        python "$SCRIPT_DIR/dsp_gui.py" ${ARGS[@]+"${ARGS[@]}"} \
        >"$LOG" 2>&1 &
    disown || true
    echo "dsp_gui.py launched in background (PID $!). Logs: $LOG"
    echo "Watch logs:  tail -f $LOG"
    echo "Stop it:     pkill -f dsp_gui.py"
    exit 0
fi

exec "$CONDA" run --no-capture-output -n "$ENV_NAME" \
    python "$SCRIPT_DIR/dsp_gui.py" ${ARGS[@]+"${ARGS[@]}"}
