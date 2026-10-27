"""Machine-specific settings for the Python tools, kept out of the source.

Values are looked up in this order, first hit wins:

    1. the process environment        (e.g.  NF_DATA_DIR=/tmp/meas ./run.sh)
    2. <repo>/config/local.env        (gitignored; copy config/local.env.example)
    3. the default passed by the caller

`local.env` is plain KEY=VALUE lines (``#`` comments, optional quotes). It is *parsed*, never
executed, and the build scripts under juce_plugins/ read the same file the same way, so one
file holds every private value (device names, deploy host, ...) for the whole repository.

Relative paths in path-valued settings are resolved against the repository root, so the
tools behave the same whatever directory they are launched from.
"""

import os
from pathlib import Path

# python_dsp_tools/nearfield/config.py -> repo root is three levels up. The package is
# installed editable (pip install -e), so __file__ always points into the checkout.
REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_ROOT = REPO_ROOT / "python_dsp_tools"
LOCAL_ENV = REPO_ROOT / "config" / "local.env"

DEFAULT_DATA_DIR = TOOLS_ROOT / "data"          # measurement GUI writes, tuning GUI reads
EXAMPLES_DIR = TOOLS_ROOT / "examples"          # small sample IRs shipped with the repo
DEFAULT_EXPORT_DIR = REPO_ROOT / "juce_plugins"  # where generated C++ headers belong

_file_cache = None


def _parse_env_file(path):
    values = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return values
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()   # trailing comment on an unquoted value
        values[key] = value
    return values


def get(key, default=None):
    """Setting `key` from the environment, then config/local.env, then `default`.
    An empty value counts as unset."""
    global _file_cache
    value = os.environ.get(key)
    if value:
        return value
    if _file_cache is None:
        _file_cache = _parse_env_file(LOCAL_ENV)
    value = _file_cache.get(key)
    return value if value else default


def _path_setting(key, default):
    value = get(key)
    if not value:
        return Path(default)
    p = Path(os.path.expanduser(value))
    return p if p.is_absolute() else REPO_ROOT / p


def data_dir():
    """Folder for measurement WAVs (raw captures and reconstructed results)."""
    return _path_setting("NF_DATA_DIR", DEFAULT_DATA_DIR)


def export_dir():
    """Root that generated C++ headers are written under (juce_plugins/ by default)."""
    return _path_setting("NF_EXPORT_DIR", DEFAULT_EXPORT_DIR)


def data_path(name):
    """Path of `name` inside the data folder (the folder itself is not created here)."""
    return data_dir() / name


def find_data_file(names, search_examples=True):
    """First existing file among `names` (preference order), looked up in the data folder
    and then, optionally, in the bundled examples. Returns a Path or None."""
    bases = [data_dir()] + ([EXAMPLES_DIR] if search_examples else [])
    for name in names:
        for base in bases:
            p = base / name
            if p.is_file():
                return p
    return None
