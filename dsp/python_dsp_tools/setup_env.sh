#!/usr/bin/env bash
#
# One-time environment setup for the Python tools (python_dsp_tools/).
#
# Creates a conda/miniforge environment with every Python *and* native dependency
# (PortAudio for sounddevice, libsndfile for soundfile). Run this ONCE per machine:
#
#     ./setup_env.sh
#
# After that, just use the env -- nothing checks or installs dependencies at run time
# (see measurement/run.sh and tuning/run.sh).
#
# Re-running is safe: it updates the existing env instead of recreating it.
# Override the env name with:  NEAR_FIELD_ENV=myenv ./setup_env.sh
#
set -euo pipefail

ENV_NAME="${NEAR_FIELD_ENV:-near_field}"
PY_VERSION="${NEAR_FIELD_PY:-3.12}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Native libs come from conda-forge (no sudo/apt needed): portaudio is what sounddevice
# dlopens on Linux, libsndfile is what soundfile needs. The audio Python bindings are also
# installed via conda-forge (python-sounddevice / pysoundfile) so they link against those
# native libs correctly inside the env.
CONDA_PKGS=(
    "python=${PY_VERSION}"
    numpy scipy matplotlib
    librosa plotly bokeh
    pyqtgraph pyside6
    python-sounddevice pysoundfile portaudio libsndfile
)
# Only this one isn't reliably on conda-forge -> install via pip into the env afterwards.
PIP_PKGS=( plotly-resampler )

# Locate a conda-compatible launcher (prefer mamba for a faster solve).
find_conda() {
    for c in mamba conda; do
        if command -v "$c" >/dev/null 2>&1; then echo "$c"; return 0; fi
    done
    for base in "$HOME/miniforge3" "$HOME/miniconda3" "$HOME/anaconda3"; do
        if [ -x "$base/bin/conda" ]; then echo "$base/bin/conda"; return 0; fi
    done
    return 1
}

CONDA="$(find_conda)" || {
    echo "ERROR: neither 'mamba' nor 'conda' found, and no miniforge/conda install under \$HOME." >&2
    echo "Install miniforge first: https://github.com/conda-forge/miniforge" >&2
    exit 1
}
echo ">> Using package manager: $CONDA"

CONDA_BASE="$("$CONDA" info --base)"
ENV_DIR="$CONDA_BASE/envs/$ENV_NAME"

if [ -d "$ENV_DIR" ]; then
    echo ">> Environment '$ENV_NAME' already exists -- installing/updating packages."
    "$CONDA" install -y -n "$ENV_NAME" -c conda-forge "${CONDA_PKGS[@]}"
else
    echo ">> Creating environment '$ENV_NAME' (Python ${PY_VERSION})..."
    "$CONDA" create -y -n "$ENV_NAME" -c conda-forge "${CONDA_PKGS[@]}"
fi

echo ">> Installing pip-only packages: ${PIP_PKGS[*]}"
"$CONDA" run -n "$ENV_NAME" python -m pip install --upgrade pip
"$CONDA" run -n "$ENV_NAME" python -m pip install "${PIP_PKGS[@]}"

# The shared DSP core (nearfield/) is installed editable, so every tool folder
# (measurement/, tuning/, scripts/) can `import nearfield` and edits need no reinstall.
echo ">> Installing the shared 'nearfield' package (editable)"
"$CONDA" run -n "$ENV_NAME" python -m pip install -e "$SCRIPT_DIR"

echo ">> Verifying imports..."
"$CONDA" run -n "$ENV_NAME" python - <<'PY'
import importlib
mods = ["numpy", "scipy", "sounddevice", "soundfile",
        "pyqtgraph", "PySide6", "librosa", "plotly", "bokeh",
        "nearfield.lib", "nearfield.config"]
missing = []
for m in mods:
    try:
        importlib.import_module(m)
    except Exception as e:  # noqa: BLE001
        missing.append(f"{m}: {e}")
if missing:
    print("FAILED imports:\n  " + "\n  ".join(missing))
    raise SystemExit(1)
print("All key dependencies import OK.")
PY

echo
echo ">> Setup complete. The environment '$ENV_NAME' is ready."
echo "   Measurement GUI:    ./measurement/run.sh"
echo "   Tuning GUI:         ./tuning/run.sh"
echo "   or activate it:     conda activate $ENV_NAME"
echo "   Machine-specific settings (audio devices, data folder): copy"
echo "   ../config/local.env.example to ../config/local.env and edit it."
