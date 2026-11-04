#!/bin/bash
# Runs a command under a GNU make jobserver of $(nproc) tokens, so that GCC's link-time
# optimisation (-flto=auto) draws the LTRANS jobs of every link the command runs at once from one
# pool, instead of $(nproc) jobs for each link. tools/build.sh and dsp/juce_apps/plugin-check/run.sh
# run `cmake --build` under it.
#
#     tools/jobserver.sh COMMAND...
#
# The jobserver is the one GNU make serves in its pipe style: a pipe holding $(nproc) - 1 tokens
# (the command holds the last one without taking it, as make's first job does), its read end
# non-blocking, and MAKEFLAGS=" -jN --jobserver-auth=R,W" naming its two descriptors, which the
# command and everything it starts inherit. GCC's lto-wrapper finds it there and runs its LTRANS
# makefile with the make on PATH, which takes the tokens; GCC 13 and later also take tokens
# themselves for their WPA streaming, and wait for a job of their own when a read finds none (a
# blocking read end would hang the link). GCC 12 (bookworm, the development container) reads only
# this style, not make 4.4's fifo. The command is exec'd, not run under a make: its output, its
# exit status and the signals it gets are its own, as without this script. Ninja 1.11 neither
# serves nor reads the tokens: its -j is unchanged. GCC keeps -flto=auto (-flto=jobserver would
# run the LTRANS jobs one at a time wherever no jobserver is found).
#
# The command runs as it is, with no jobserver of ours:
#   - when MAKEFLAGS already names a jobserver whose descriptors (or fifo) are open here (a make's
#     recipe that ran us with '+' or $(MAKE)): the command shares that one, as a recursive make does;
#   - when no GNU make 4.2 or later is on PATH: GCC's LTRANS jobs need it, and make 4.2 is the first
#     to name the option --jobserver-auth, the only name GCC reads;
#   - when python3 is missing (it makes the pipe: bash cannot make one non-blocking).
set -euo pipefail
[ $# -gt 0 ] || { echo "usage: $0 COMMAND..." >&2; exit 2; }

# A jobserver already open here.
inherited() {
    local auth="${MAKEFLAGS:-}"
    case "$auth" in *--jobserver-auth=*) ;; *) return 1 ;; esac
    auth="${auth##*--jobserver-auth=}"
    auth="${auth%% *}"
    case "$auth" in
        fifo:*) [ -p "${auth#fifo:}" ] ;;
        *,*)    [[ "$auth" =~ ^[0-9]+,[0-9]+$ ]] && [ -e "/proc/$$/fd/${auth%,*}" ] && [ -e "/proc/$$/fd/${auth#*,}" ] ;;
        *)      return 1 ;;
    esac
}

# GNU make 4.2 or later on PATH.
gnu_make() {
    local version
    version="$(make --version 2> /dev/null | sed -n '1s/^GNU Make \([0-9][0-9]*\)\.\([0-9][0-9]*\).*/\1 \2/p')"
    [ -n "$version" ] || return 1
    set -- $version
    [ "$1" -gt 4 ] || { [ "$1" = 4 ] && [ "$2" -ge 2 ]; }
}

inherited && exec "$@"
gnu_make || exec "$@"
command -v python3 > /dev/null || exec "$@"

# The signals this script was given ignored (/proc's SigIgn, a hexadecimal mask; empty without /proc).
ignored="$(sed -n 's/^SigIgn:[[:space:]]*//p' "/proc/$$/status" 2> /dev/null || true)"

# The pipe, then the command in place of python, with the environment it was given and MAKEFLAGS,
# and the process state python changes put back as it was:
#   - PYTHONCOERCECLOCALE=0, then put back: under the C locale (the container's) python would
#     otherwise export LC_CTYPE=C.UTF-8 (PEP 538), and the compilers' messages would change;
#   - SIGPIPE and SIGXFSZ, which python ignores at its start and an exec keeps ignored: back to
#     their default (a write to a closed pipe kills the writer, as the build's tools expect), unless
#     this script was given them ignored. Its SIGINT handler an exec resets; it blocks no signal.
# A command that cannot be run fails as bash would fail it (127 not found, 126 not executable).
PYTHONCOERCECLOCALE=0 exec python3 -S -c '
import sys
sys.path = [p for p in sys.path if p]   # no module from the current directory
import fcntl, os, signal
was_set, was, ignored, tokens, command = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5:]
if was_set:
    os.environ["PYTHONCOERCECLOCALE"] = was
else:
    os.environ.pop("PYTHONCOERCECLOCALE", None)
ignored = int(ignored, 16) if ignored else 0
for s in (signal.SIGPIPE, signal.SIGXFSZ):
    if not ignored & (1 << (s - 1)):
        signal.signal(s, signal.SIG_DFL)
r, w = os.pipe()
os.write(w, b"+" * (tokens - 1))
fcntl.fcntl(r, fcntl.F_SETFL, fcntl.fcntl(r, fcntl.F_GETFL) | os.O_NONBLOCK)
os.set_inheritable(r, True)
os.set_inheritable(w, True)
os.environ["MAKEFLAGS"] = f" -j{tokens} --jobserver-auth={r},{w}"
os.environ.pop("MFLAGS", None)
try:
    os.execvp(command[0], command)
except OSError as e:
    print(f"tools/jobserver.sh: {command[0]}: {e.strerror}", file=sys.stderr)
    sys.exit(127 if isinstance(e, FileNotFoundError) else 126)
' "${PYTHONCOERCECLOCALE+set}" "${PYTHONCOERCECLOCALE-}" "$ignored" "$(nproc)" "$@"
