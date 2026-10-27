# Shared helpers for the plugin build scripts in this folder. Source it, don't run it:
#     . "$(dirname "${BASH_SOURCE[0]}")/common.sh"
#
# Provides:
#   TOOLS_DIR, PLUGINS_DIR (juce_plugins/), REPO_ROOT
#   load_local_config        read config/local.env (KEY=VALUE, never executed)
#   list_plugins             every plugin folder (one containing speaker_add_plugin)
#   resolve_plugins <arg>    "all" -> every plugin, a name -> that plugin (validated)
#   plugin_channels <name>   "<inputs> <outputs>" from its speaker_add_plugin() call
#   plugin_lv2_uri <name>    <SPEAKER_LV2_URI_BASE>/<name>, i.e. what CMake gives it
#   require_deploy_host      fail with instructions unless DEPLOY_HOST is set

TOOLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGINS_DIR="$(cd "$TOOLS_DIR/.." && pwd)"
REPO_ROOT="$(cd "$PLUGINS_DIR/.." && pwd)"
LOCAL_ENV="$REPO_ROOT/config/local.env"
SPEAKER_CMAKE="$PLUGINS_DIR/cmake/SpeakerPlugin.cmake"

# Read config/local.env into the environment. The file is PARSED, not sourced, so it cannot
# run code, and a variable that is already set (non-empty) in the environment wins -- the same
# rules as python_dsp_tools/nearfield/config.py, which reads the same file.
load_local_config() {
    [ -f "$LOCAL_ENV" ] || return 0
    local line key value
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line#"${line%%[![:space:]]*}"}"          # trim leading whitespace
        case "$line" in ''|'#'*) continue ;; esac
        case "$line" in *=*) ;; *) continue ;; esac
        key="${line%%=*}"; key="${key%"${key##*[![:space:]]}"}"; key="${key#export }"
        value="${line#*=}"; value="${value#"${value%%[![:space:]]*}"}"
        case "$value" in
            \"*\") value="${value#\"}"; value="${value%\"}" ;;
            \'*\') value="${value#\'}"; value="${value%\'}" ;;
            *)     value="${value%% \#*}"; value="${value%"${value##*[![:space:]]}"}" ;;
        esac
        [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
        if [ -z "${!key:-}" ] && [ -n "$value" ]; then
            printf -v "$key" '%s' "$value"
            export "${key?}"
        fi
    done < "$LOCAL_ENV"
}

list_plugins() {
    local d
    for d in "$PLUGINS_DIR"/*/; do
        d="${d%/}"
        if [ -f "$d/CMakeLists.txt" ] && grep -q '^[[:space:]]*speaker_add_plugin(' "$d/CMakeLists.txt"; then
            basename "$d"
        fi
    done
}

resolve_plugins() {
    local want="$1"
    if [ "$want" = "all" ]; then
        list_plugins
        return
    fi
    if list_plugins | grep -qx -- "$want"; then
        echo "$want"
        return
    fi
    echo "Error: '$want' is not a plugin. Known plugins: $(list_plugins | tr '\n' ' ')" >&2
    return 1
}

# The first number after INPUTS / OUTPUTS in the plugin's speaker_add_plugin() call.
plugin_channels() {
    local f="$PLUGINS_DIR/$1/CMakeLists.txt" in out
    in="$(sed -n 's/^[[:space:]]*INPUTS[[:space:]]\{1,\}\([0-9]\{1,\}\).*/\1/p' "$f" | head -1)"
    out="$(sed -n 's/^[[:space:]]*OUTPUTS[[:space:]]\{1,\}\([0-9]\{1,\}\).*/\1/p' "$f" | head -1)"
    echo "${in:-1} ${out:-1}"
}

plugin_lv2_uri() {
    local base
    base="$(sed -n 's/^set(SPEAKER_LV2_URI_BASE "\([^"]*\)".*/\1/p' "$SPEAKER_CMAKE" | head -1)"
    if [ -z "$base" ]; then
        echo "Error: could not read SPEAKER_LV2_URI_BASE from $SPEAKER_CMAKE" >&2
        return 1
    fi
    echo "$base/$1"
}

require_deploy_host() {
    if [ -z "${DEPLOY_HOST:-}" ]; then
        echo "Error: no deploy target. Set DEPLOY_HOST=user@host in config/local.env"
        echo "  (copy config/local.env.example first), or pass it for one run:"
        echo "      DEPLOY_HOST=user@my-board.local $0 ..."
        exit 1
    fi
    DEPLOY_LV2_DIR="${DEPLOY_LV2_DIR:-.lv2}"
}
