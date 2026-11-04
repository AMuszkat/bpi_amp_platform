"""firmware_speakerd_install (docs/autonomous-development.md, section 5): the installers lay
speakerd out as firmware/CLAUDE.md documents it, refuse what is unsafe before
they change anything, and their remote mode and the board script quote what they send.

firmware/speakerd-setup.sh runs here as it runs on a board, with two test hooks: --home (a
fresh folder stands for the audio user's home) and --no-systemd (the systemctl calls are only
printed), or, for the systemd path, stand-ins for systemctl and journalctl that record each call
and answer as a test tells them (journalctl, as on a board's volatile journal, finds no journal
with --user: only --user-unit reads the units' lines). The bundles are made by the installer's
own bundle step; their speakerd is the stand-in of stub_speakerd.c, which says the version
STUB_VERSION gives it (the installer's minimum, SPEAKERD_MIN_VERSION, unless a test says
otherwise). The old plugin host is
a copy of sleep called harness.TEST_PLUGIN_HOST. The remote mode runs through a stand-in ssh
that runs the board's side on this machine, as an unprivileged user when run as root, in a
stand-in board home; the board script runs with --dry-run. Nothing here reaches a board.

Pass when:

- the bundle holds bin/speakerd, lv2/loudness_headless.lv2, firmware/speakerd/factory.json (the
  repository's chain document, byte for byte) and a VERSION whose id is the commit, "-dirty" if
  the tree is, and the hash of its contents; the default plugins are the headless builds of
  loudness, crossover and firConv (DEFAULT_PLUGINS), and a bundle of them carries exactly those;
- the repository's chain document names only the three headless URIs, with firConv's gain at
  -12 dB, and wires the board's chain: Mine into firConv, then the crossover, its low band through
  loudness to the woofer and its high band to the tweeter; a first install with no --factory and no factory.json writes it when the bundle
  has every plugin it names (said so), and with a bundle of loudness alone writes none, says
  passthrough and names the plugins that bundle lacks;
- a factory.json kept by an install, or a --factory file, that names a plugin the bundle lacks is
  refused before anything changes (no version, no file of the home), naming each missing plugin
  and --factory (firmware/speakerd/factory.json) or the headless build; check says the same;
  ampd's graph.json and graph.lkg.json are never rewritten, and install goes on without a word
  about them; status marks such a layer with *** and the installed version's plugins;
- a --factory file, or a factory.json kept by an install, that setup-helper.py graph refuses (a
  connection to node 99, which the document lacks) is refused before anything changes (every
  file of the home as it was, factory.json unchanged), by install and check, naming the helper's
  reason; status marks such a layer `*** <that reason> ***`;
- a speakerd that cannot say its version, one older than the minimum, and one that cannot be
  executed (as from a noexec /tmp: said so) are refused by install, which installs nothing, and
  rollback refuses an older one;
- a first install without --role is refused, and installs nothing, and a running plugin host
  is left running although --stop-plugin-host was given (refused before anything is stopped);
- the first install lays out, in the home: .local/lib/speakerd/<id>/ with bin/speakerd,
  bin/speakerd-run, lv2/loudness_headless.lv2, VERSION (with the speakerd version) and setup/
  (the installer); current -> <id>; an empty home/ (speakerd's HOME);
  .local/state/speakerd/device.json exactly {version 1, role left, amplifier {node, woofer
  playback_FL, tweeter playback_FR}, limits {gain_db 0.0, tweeter on}}; factory.json, the
  document given; the unit and the PipeWire drop-in, byte for byte the tracked firmware/ files;
  and graph.json and graph.lkg.json, there before, unchanged byte for byte; and it says every
  library speakerd and its plugins need is there; VERSION records speakerd's whole --version
  line (speakerd_build), the installed scripts are executable, and the installed
  speakerd-setup.sh, called directly, says the status; its closing hints name the installed copy
  (.local/lib/speakerd/current/setup/speakerd-setup.sh smoke and rollback), never the bundle's;
- installing the same bundle again adds no version and changes nothing, and keeps device.json's
  limits (-6 dB, tweeter on), role and amplifier;
  --role right changes the role alone; one of --amplifier, --woofer-port, --tweeter-port
  changes that alone and keeps device.json's others; a device.json with a key speakerd does not
  know, or a key twice, is refused (speakerd would reject it and play its fail-safe defaults);
- `limits --gain-db <g> --tweeter-limit <muted|on>` writes exactly {gain_db g, tweeter ...} into
  device.json's limits and changes nothing else of it (-20 on, -20 muted, 0 on in turn), and a
  gain above 0 dB or another tweeter limit is refused, device.json unchanged;
- a second, different bundle becomes current, the first previous; rollback swaps them, and
  rollback again swaps them back, keeping device.json's limits; with --keep 2 a third version removes the oldest, never current or
  previous;
- while the plugin host runs, install refuses, says so, and installs nothing; with
  --stop-plugin-host it still refuses, stopping nothing, while a desktop autostart or a profile
  line would start it again, naming each and what to do; once they are gone it stops it,
  disables the user unit that starts it, and uninstall re-enables that unit;
- a plugin bundle with one of the bundle's URIs in a system LV2 folder is named loudly, once per
  system bundle, with its version against the bundle's: with one of the same version or a newer
  one among them, as making speakerd refuse to start (status 2); with older ones only, as
  replaced by the deployed bundle;
- --import-filtergraph writes speakerd's conversion as factory.json, having given speakerd the
  device.json to be (--device), and a conversion that fails (exit 2) is refused;
- the amplifier is found in a pw-dump: the one ALSA sink; with several, bpi-alsa-playback-pcm
  or alsa-playback-pcm; none or an ambiguous set is refused, naming the sinks; on a virtual
  speaker (whose amplifier is a null sink) install refuses without --amplifier, and with
  --amplifier amplifier reads its ports playback_FL and playback_FR;
- smoke passes the exact line `speakerd: device: limits: output at -20 dB, tweeter muted` when
  device.json has those limits (any limits: there is no cap), and reports, each as a failure: a `device.json: FAILED` line,
  the limits line with either fail-safe suffix (or any other), speakerd's `NO INPUT SINK
  CONFIGURED` line, and no limits line at all;
- smoke finds snapclient's stream by its application.name in pw-dump (snapclient 0.34.0's has no
  node.name, so pw-link lists its ports under an empty name) and reads its links there: into the
  speakerd sink passes, into another node (the amplifier) or into nothing warns, none is said; an
  application.name on its client only, or a node.name naming it, counts too;
- smoke reports speakerd's timing: from its status on the local link, read before and after the
  window, or (the link busy: ampd holds it) from its log's last `timing: last <n> s:` line, it
  prints each node's and the graph's mean and max; it fails when the graph's longest cycle took
  more than half its cycle (50.1 %; 50.0 % passes) and when a cycle was late in the window (the
  log's `timing: in the last second` lines in it, or late_total's difference), and warns on
  position jumps in the window, on late cycles before it, and when nothing measured the timing;
  it reads the passthrough minute line (no node: it begins with the graph); it prints speakerd's
  start-up `timing: a clock read costs <x> ns` in the timing line and warns, never fails, above
  150 ns ("clock reads are system calls on this kernel ...: per-node timing costs about 6×812 ns
  = 4.9 us per cycle" with one node);
- snapcast-setup.sh --route speakerd refuses while no PipeWire config defines the speakerd
  sink, and after `speakerd-setup.sh sink` routes snapclient (and shairport-sync and an
  AudioPluginHost) into "speakerd", keeping the routing there was; uninstall puts that routing
  back exactly (or, with none kept, the AudioPluginHost route), removes the unit, the drop-in
  and the versions and keeps the state, and --purge removes that too; --route speakerd is
  refused, client-rt.conf not written, while the snapclient unit lacks
  PIPEWIRE_CONFIG_NAME=client-rt.conf, and with --no-client-rt (into Ap.cap it only warns), and
  no temporary file is left beside client-rt.conf; with a running PipeWire, uninstall stops and
  disables snapclient when the restored routing's target (Ap.cap) is not there, and says the
  target exists when it is;
- snapcast-setup.sh --mode target, through stand-ins for systemctl, loginctl, apt-get, curl, dpkg
  and snapclient: a snapclient whose --help lists pipewire for --player has the player (said so,
  with that line); without the line its program's text decides, and a --player line without
  pipewire wins over it; it is downloaded and installed (the packaged system service disabled,
  linger enabled) only when it lacks the player; the first run keeps a person's client-rt.conf as
  client-rt.conf.orig, writes client-rt.conf and the unit and restarts snapclient, never
  PipeWire; a second run with the same options downloads nothing, runs no apt, calls nothing that
  changes anything, leaves every file of the home as it was and says "already set up, nothing
  changed" in one line; another server address writes the unit alone, another route
  client-rt.conf alone, each restarting snapclient; client-rt.conf.orig survives every later run;
  a snapclient that started before its unit was written, or does not run, is restarted with
  nothing written; --routing-only run twice writes nothing the second time and says so;
  --server example.local lands verbatim in the unit's --host, and --server-ip is the same
  option; a .local name that getent hosts does not resolve installs libnss-mdns (only when
  missing, not with --dry-run) and is checked again, one that still does not is refused with
  nothing written or restarted, a name that is not .local and an address are not looked up, and
  a server a shell would split is refused;
- with the repository's pipewire.conf as the user's (it defines the speakerd sink), never two
  speakerd nodes: speakerd-setup.sh sink and install write no drop-in and remove one that is
  there, saying so (the dry run removes nothing), and a second sink writes nothing;
  snapcast-setup.sh --route speakerd finds the sink in pipewire.conf, setup.sh's snapcast
  component alone accepts it, status names it, and uninstall says the sink stays (a null sink:
  silent) and leaves pipewire.conf as it was, restoring the routing;
- setup.sh checks before it changes: --dry-run changes nothing; --route speakerd without the
  pipewire component and without the sink, and --route plugin-host with the speakerd component,
  are refused before anything changes; and the components pipewire, snapcast (re-routing an
  installed snapclient) and speakerd install in that order;
- setup.sh --only pipewire --base-conf installs the repository's pipewire.conf byte for byte,
  whose graph (default.clock.rate, default.clock.allowed-rates) and amplifier
  (bpi-alsa-playback-pcm, its audio.rate) are 48000 Hz, the only object with a rate, and says
  those rates (with --dry-run too, which writes nothing); it removes the drop-ins the file
  contains (pipewire.conf.d/20-resample.conf, 60-speakerd-sink.conf), saying so (the dry run
  says it would and removes nothing), and writes no drop-in; --rate 44100 gives 44100 for the
  graph, its allowed rates and the amplifier, changes no other line, and warns that the shipped
  filters are designed for 48000 Hz; --rate 48000 gives the repository's file again, and the same run twice writes
  nothing; it says when the running PipeWire runs at another rate; with --no-restart nothing is
  restarted; the board's own pipewire.conf is kept as .orig once, and one the script installed
  never is; a pipewire.conf whose amplifier runs at another rate than its graph is refused,
  writing nothing, unless --rate sets both; --rate without --base-conf and --base-conf without
  the pipewire component are refused;
- under (stand-in) systemd, install fails unless speakerd, after its start, runs and says
  exactly the limits device.json gives: no line within the wait, an inactive service, a
  fail-safe suffix, a FAILED line and NO INPUT SINK CONFIGURED each fail it; `limits` and
  `rollback` restarting a speakerd that runs pass (limits having written its limits into
  device.json), although the old instance's `speakerd ended` line precedes the new start, and
  fail on a `speakerd ended` after it; the same install again
  (speakerd-setup.sh install, and setup.sh --only pipewire,speakerd) restarts neither PipeWire
  nor speakerd and writes no file of the home; setup.sh stops on a
  PipeWire restart that fails, before anything is routed into speakerd; and, the whole
  architecture and --only pipewire alike, it refuses while the plugin host runs, restarting
  nothing, and with --stop-plugin-host stops it before it restarts PipeWire;
- the remote mode (target-remote) copies one bundle to the board's ~/.cache and runs the board's
  side there, also for check (with the artifacts) and for --dry-run (the board-side dry run,
  which installs nothing), every argument arriving as given (a path with a space, a value with a
  semicolon that runs nothing), and the copy is removed afterwards; ssh as root is refused; setup.sh
  --mode target-remote --only speakerd ends with hints that name
  ~/.local/lib/speakerd/current/setup/speakerd-setup.sh (smoke, rollback), not the bundle's
  copy in ~/.cache, which is gone; snapcast-setup.sh --mode target-remote copies the script with
  speakerd/setup-helper.py to a fresh mktemp -d folder on the board, where a
  --route speakerd run finds the helper and checks the running PipeWire's sink with it (passing
  with the drop-in's sink, refusing without one), the folder removed afterwards either way, and
  its --dry-run prints those steps and contacts nothing; and, for a
  host that is not a test's (.invalid), the remote mode refuses before it calls ssh while a lab
  lock exists, and with none reaches the (stand-in) board from any checkout, tested or not (there
  is no test-status gate);
- the board script (with --dry-run) refuses `journal -n` that is not a number and a deploy
  option that is not an agent's (--base-conf, --stop-plugin-host, --server, --server-ip, --no-restart),
  quotes journal's argument, reads the journal with `journalctl --user-unit` (never `--user -u`,
  which finds nothing on a board's volatile journal), and reads status from the board's
  installed script with a fixed command.

    python3 -m unittest -v tests.vpair.test_firmware_speakerd_install
"""

import json
import os
import re
import shutil
import subprocess
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from harness import (FIRMWARE, ROOT, TEST_ENV, TEST_PLUGIN_HOST, Pair, build_name, built, firmware, graph_document,
                     lv2_uri, stub_plugin_host, stub_speakerd)

DOCUMENT = graph_document(lv2_uri("loudness_headless"), {"mode": "Force 1", "s0_e0_gain": 12.0})
CAPPED = {"gain_db": -20.0, "tweeter": "muted"}
# What a first install writes: no limit at all.
OPEN = {"gain_db": 0.0, "tweeter": "on"}
MIN_VERSION = re.search(r"(?m)^SPEAKERD_MIN_VERSION=(\S+)$", (FIRMWARE / "speakerd-setup.sh").read_text()).group(1)
LIMITS = "speakerd: device: limits: output at -20 dB, tweeter muted"
AMPD_LISTENING = "ampd: listening on 0.0.0.0: control 7450, knobs 7451, monitor 7452"
# A client-rt.conf a person wrote, before any of the installers ran.
PERSONS_ROUTING = ('# a person\'s own routing\nstream.rules = [ { matches = [ { application.name = "Snapclient" } ] '
                   'actions = { update-props = { target.object = "Ap.cap" } } } ]\n')

# Stand-ins for systemctl, journalctl and loginctl (by the name they are called), for the
# installers' systemd path: each call is recorded in $FAKE_SYSTEMD/calls; is-active answers
# $FAKE_SYSTEMD/active (default active); journalctl prints $FAKE_SYSTEMD/journal, except with
# --user, which finds no journal file, as on a board (a volatile journal, not split per user:
# only --user-unit reads the user units' lines); a call whose arguments contain a line of
# $FAKE_SYSTEMD/fail fails. At a restart of pipewire.service it notes whether a process called
# $FAKE_PLUGIN_HOST runs.
FAKE_SYSTEMD = r'''#!/bin/bash
name="$(basename "$0")"; d="${FAKE_SYSTEMD:?}"
echo "$name $*" >> "$d/calls"
case "$name" in
    journalctl)
        case " $* " in *" --user "*) echo "No journal files were found." >&2; echo "-- No entries --"; exit 0 ;; esac
        cat "$d/before" "$d/journal" 2> /dev/null; exit 0 ;;
    loginctl) echo yes; exit 0 ;;
esac
if [ -f "$d/fail" ]; then
    while read -r bad; do [ -n "$bad" ] && [[ "$*" == *"$bad"* ]] && exit 1; done < "$d/fail"
fi
case " $* " in
    *" restart "*speakerd.service*)
        # A speakerd that runs ($d/running) ends first: its ExecStopPost line comes before the
        # new start, in the same journal window.
        [ ! -f "$d/running" ] || echo "speakerd-run: speakerd ended: stopped (killed TERM); the next start uses current: a clean stop" > "$d/before" ;;
    *" restart "*pipewire.service*)
        alive=no
        for f in /proc/[0-9]*/stat; do
            { s="$(< "$f")"; } 2> /dev/null || continue
            [[ "$s" == *"(${FAKE_PLUGIN_HOST:-none})"* ]] || continue
            s="${s##*) }"; [ "${s%% *}" = Z ] || alive=yes
        done
        echo "pipewire restarted; the plugin host runs: $alive" >> "$d/calls" ;;
    *" is-active "*speakerd*)
        state="$(cat "$d/active" 2> /dev/null || echo active)"
        [[ " $* " == *" --quiet "* ]] || echo "$state"
        [ "$state" = active ]; exit ;;
    *" is-enabled "*) exit 1 ;;
    *" show "*) echo 0 ;;
esac
exit 0
'''

# A stand-in for ssh: the board's side runs on this machine, in $FAKE_BOARD/home, as nobody when
# run as root (the board's audio user is never root), and nothing is contacted. It records the
# host in $FAKE_BOARD/hosts.
FAKE_SSH = r'''#!/bin/bash
while [ $# -gt 0 ]; do case "$1" in -t | -T) shift ;; --) shift; break ;; -*) shift ;; *) break ;; esac; done
host="$1"; shift
echo "$host" >> "${FAKE_BOARD:?}/hosts"
export HOME="$FAKE_BOARD/home"
cd "$HOME" || exit 255
if [ "$(id -u)" = 0 ]; then exec setpriv --reuid=65534 --regid=65534 --clear-groups bash -c "$*"; fi
exec bash -c "$*"
'''


# Stand-ins for what snapcast-setup.sh's target mode calls on a board (by the name they are
# called): systemctl (the system's and --user), loginctl, apt-get, curl, dpkg, dpkg-query and
# getent. Each
# call is recorded in $FAKE_CLIENT/calls. The user's snapclient unit is $FAKE_CLIENT/active,
# enabled and started (its main process's start, as systemd shows it), which a restart sets;
# linger is $FAKE_CLIENT/linger; the packaged system snapclient system-active and system-enabled.
# curl answers the release's assets (one with-pipewire .deb for this machine's codename) or writes
# the download; apt-get install puts $FAKE_CLIENT/help-installed into the stand-in snapclient's
# --help and $FAKE_CLIENT/version-installed into its version. mDNS: `getent hosts NAME` answers
# for a name listed in $FAKE_CLIENT/mdns-names only once $FAKE_CLIENT/nss-mdns exists (libnss-mdns
# installed: dpkg-query says so, and apt-get install libnss-mdns creates it); every other getent
# database is the real one's.
FAKE_CLIENT_TOOLS = r'''#!/bin/bash
name="$(basename "$0")"; d="${FAKE_CLIENT:?}"
echo "$name $*" >> "$d/calls"
state() { cat "$d/$1" 2> /dev/null || echo "$2"; }
case "$name" in
    loginctl)
        case " $* " in
            *" show-user "*) state linger no ;;
            *" enable-linger "*) echo yes > "$d/linger" ;;
        esac ;;
    curl)
        out=""; prev=""
        for a in "$@"; do [ "$prev" = -o ] && out="$a"; prev="$a"; done
        if [ -n "$out" ]; then echo "a .deb" > "$out"
        else
            codename="$(. /etc/os-release; echo "$VERSION_CODENAME")"
            echo "{\"assets\": [{\"browser_download_url\": \"https://github.invalid/snapclient_0.34.0-1_arm64_${codename}_with-pipewire.deb\"}]}"
        fi ;;
    apt-get)
        case " $* " in
            *" install "*libnss-mdns*) touch "$d/nss-mdns" ;;
            *" install "*) cp "$d/help-installed" "$d/help"; cp "$d/version-installed" "$d/version" ;;
        esac ;;
    dpkg) case " $* " in *" --print-architecture "*) echo arm64 ;; esac ;;
    dpkg-query)
        case " $* " in
            *" libnss-mdns "*) [ -e "$d/nss-mdns" ] && printf 'install ok installed' ;;
            *) state version unknown ;;
        esac ;;
    getent)
        if [ "$1" = hosts ]; then
            if [ -e "$d/nss-mdns" ] && grep -qxF "$2" "$d/mdns-names" 2> /dev/null; then
                echo "192.0.2.20      $2"; exit 0
            fi
            exit 2
        fi
        PATH="${PATH#*:}" exec getent "$@" ;;
    systemctl)
        if [[ " $* " == *" --user "* ]]; then
            case " $* " in
                *" show "*snapclient*)
                    echo "ActiveState=$(state active inactive)"; echo "UnitFileState=$(state enabled disabled)"
                    echo "NeedDaemonReload=no"; echo "ExecMainStartTimestamp=$(state started n/a)" ;;
                *" restart "*snapclient*)
                    echo active > "$d/active"; TZ=UTC date '+%a %Y-%m-%d %H:%M:%S UTC' > "$d/started" ;;
                *" enable "*snapclient*) echo enabled > "$d/enabled" ;;
            esac
        else
            case " $* " in
                *" show "*snapclient*)
                    echo "ActiveState=$(state system-active inactive)"; echo "UnitFileState=$(state system-enabled disabled)" ;;
                *" disable "*snapclient*) echo inactive > "$d/system-active"; echo disabled > "$d/system-enabled" ;;
            esac
        fi ;;
esac
exit 0
'''

# A stand-in snapclient: its --help is $FAKE_CLIENT/help, its --version says $FAKE_CLIENT/version
# as snapclient does. MARK is replaced by a line of its program's text.
FAKE_SNAPCLIENT = r'''#!/bin/bash
d="${FAKE_CLIENT:?}"
echo "snapclient $*" >> "$d/calls"
# MARK
case " $* " in
    *" --help "*) cat "$d/help" ;;
    *" --version "*) echo "snapclient v$(cat "$d/version")"; echo "Copyright (C) 2014-2025 BadAix." ;;
esac
'''

# snapclient 0.34.0's --help (the with-pipewire build), around its --player line, and an older
# one's without the pipewire player.
HELP_WITH_PIPEWIRE = """Usage: snapclient [options...] [url]

 --logsink arg                 log sink [null,system,stdout,stderr,file:<filename>]
  -p, --player arg (=alsa)     alsa|pipewire|file[:<options>|?]
  -s, --soundcard arg (=default)
                               index or name of the pcm device
"""
HELP_WITHOUT_PIPEWIRE = HELP_WITH_PIPEWIRE.replace("alsa|pipewire|file", "alsa|file")


def tree(root):
    """Every path under `root`, relative, folders marked with a slash; symlinks as "-> target"."""
    out = set()
    for path in sorted(Path(root).rglob("*")):
        rel = str(path.relative_to(root))
        if path.is_symlink():
            out.add(f"{rel} -> {os.readlink(path)}")
        elif path.is_dir():
            out.add(rel + "/")
        else:
            out.add(rel)
    return out


def version_file(path):
    return dict(line.split("=", 1) for line in Path(path).read_text().splitlines())


# The plugin builds the stand-in build tree has: loudness's both, and the headless builds of
# crossover and firConv (the default bundle's: firmware/install-lib.sh, DEFAULT_PLUGINS).
STUB_BUILDS = (("loudness", "headless"), ("loudness", "complete"), ("crossover", "headless"), ("firConv", "headless"))

# What this module's built() calls need, also through the harness (Speakerd, render, SpeakerPair,
# ScriptedController): tests/vpair/pool.py builds them before any test runs (README.md, "The pool").
POOL_BUILDS = tuple(build_name(plugin, kind) for plugin, kind in STUB_BUILDS)
POOL_SPLIT = True

DEFAULT_PLUGINS = re.search(r"(?m)^DEFAULT_PLUGINS=\(([^)]*)\)$", (FIRMWARE / "install-lib.sh").read_text()).group(1).split()
CHAIN_DOCUMENT = json.loads((FIRMWARE / "speakerd/factory.json").read_text())


def stub_build_root(root):
    """A build tree with the stand-in speakerd and the real plugin builds of STUB_BUILDS, for the
    bundle step (BUILD_ROOT)."""
    (root / "speakerd/speakerd_artefacts/Release").mkdir(parents=True, exist_ok=True)
    shutil.copy(stub_speakerd(), root / "speakerd/speakerd_artefacts/Release/speakerd")
    for plugin, kind in STUB_BUILDS:
        (root / plugin).mkdir(exist_ok=True)
        target = plugin if kind == "complete" else f"{plugin}_{kind}"
        link = root / f"{plugin}/{target}_artefacts"
        if not link.exists():
            os.symlink(built(plugin, kind).parents[2], link)
    return root


def lv2_version(bundle):
    """(minor, micro, version) of a JUCE bundle: the lv2:minorVersion and lv2:microVersion lilv
    compares, and doap:revision, all from its dsp.ttl; None for what it does not say."""
    text = (bundle / "dsp.ttl").read_text()
    found = [re.search(pattern, text) for pattern in
             (r"lv2:minorVersion\s+(\d+)", r"lv2:microVersion\s+(\d+)", r'doap:revision\s+"([^"]+)"')]
    return tuple(m and m.group(1) for m in found)


def copy_at_version(bundle, to, minor, micro):
    """A copy of a JUCE bundle at `to` whose dsp.ttl says version <major>.<minor>.<micro>, the
    major its own; returns that version."""
    shutil.copytree(bundle, to)
    text = (to / "dsp.ttl").read_text()
    version = f"{lv2_version(bundle)[2].split('.')[0]}.{minor}.{micro}"
    for pattern, value in ((r"lv2:minorVersion\s+\d+", f"lv2:minorVersion {minor}"),
                           (r"lv2:microVersion\s+\d+", f"lv2:microVersion {micro}"),
                           (r'doap:revision\s+"[^"]+"', f'doap:revision "{version}"')):
        text = re.sub(pattern, value, text, count=1)
    (to / "dsp.ttl").write_text(text)
    return version


def firmware_at(repo, script, *args, env):
    """firmware/<script> of another checkout (a scratch copy), as harness.firmware runs ours."""
    return subprocess.run(["bash", str(Path(repo) / "firmware" / script), *map(str, args)],
                          env={**os.environ, **TEST_ENV, **env}, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, timeout=120)


def write_script(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


class Setup(unittest.TestCase):
    """What every class of this module shares: the stand-in builds and bundles."""

    @classmethod
    def setUpClass(cls):
        for plugin, kind in STUB_BUILDS:
            built(plugin, kind)
        cls.tmp = Path(tempfile.mkdtemp(prefix="vpair-install-"))
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)
        # No PipeWire here unless a test brings one up: pw-dump fails at once.
        (cls.tmp / "no-pipewire").mkdir()
        (cls.tmp / "stub").mkdir()
        cls.env = {"XDG_RUNTIME_DIR": str(cls.tmp / "no-pipewire"), "STUB_VERSION": MIN_VERSION,
                   "STUB_DIR": str(cls.tmp / "stub")}
        cls.root = stub_build_root(cls.tmp / "build")
        cls.bundle = cls.make_bundle("one", ["loudness:headless"])
        # The pair's secret, for the whole architecture (setup.sh's ampd component needs it).
        cls.secret_file = cls.tmp / "ampd.secret"
        cls.secret_file.write_text("the-pairs-test-secret-0123456789\n")
        cls.factory = cls.tmp / "factory.json"
        cls.factory.write_text(json.dumps(DOCUMENT, indent=2))

    @classmethod
    def make_bundle(cls, name, plugins):
        out = cls.tmp / f"bundle-{name}"
        args = ["bundle", "--arch", "native", "--out", out]
        for plugin in plugins:
            args += ["--plugin", plugin]
        firmware("speakerd-setup.sh", *args, env={"BUILD_ROOT": str(cls.root)})
        return out

    def install(self, home, *args, bundle=None, check=True, env=None):
        return firmware("speakerd-setup.sh", "install", "--bundle", bundle or self.bundle, "--home", home,
                        "--no-systemd", "--amplifier", "amplifier", *args, env=env or self.env, check=check)

    def fresh_home(self, name):
        path = self.tmp / name
        shutil.rmtree(path, ignore_errors=True)
        return path

    def plugin_host(self):
        host = stub_plugin_host(self.tmp / "bin")
        self.addCleanup(lambda: (host.kill(), host.wait()))
        return host


class Install(Setup):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bundle2 = cls.make_bundle("two", ["loudness:headless", "loudness:complete"])
        cls.bundle3 = cls.make_bundle("three", ["loudness:complete"])
        # A board's bundle: the default plugins, the headless builds of all three.
        cls.board_bundle = cls.make_bundle("board", [])

    def test_the_bundle(self):
        files = tree(self.bundle)
        for want in ("bin/speakerd", "lv2/loudness_headless.lv2/", "VERSION",
                     "firmware/speakerd-setup.sh", "firmware/speakerd/speakerd-run", "firmware/speakerd/factory.json",
                     "firmware/pipewire/pipewire.conf.d/60-speakerd-sink.conf"):
            self.assertIn(want, files)
        self.assertEqual((self.bundle / "firmware/speakerd/factory.json").read_bytes(),
                         (FIRMWARE / "speakerd/factory.json").read_bytes())
        # The default bundle carries the headless builds of every plugin, and nothing else.
        self.assertEqual(DEFAULT_PLUGINS, ["loudness:headless", "crossover:headless", "firConv:headless"])
        self.assertEqual(sorted(d.name for d in (self.board_bundle / "lv2").iterdir()),
                         ["crossover_headless.lv2", "firConv_headless.lv2", "loudness_headless.lv2"])
        version = version_file(self.bundle / "VERSION")
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=FIRMWARE, capture_output=True,
                                text=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=FIRMWARE, capture_output=True,
                                    text=True).stdout.strip())
        print(f"\n  bundle {version['id']}")
        self.assertEqual(version["commit"], commit)
        self.assertEqual(version["dirty"], "yes" if dirty else "no")
        self.assertRegex(version["id"], rf"^{commit[:12]}{'-dirty' if dirty else ''}-[0-9a-f]{{8}}$")
        self.assertNotEqual(version_file(self.bundle / "VERSION")["id"], version_file(self.bundle2 / "VERSION")["id"])

    def test_refuses_a_speakerd_that_ignores_the_limits(self):
        home = self.fresh_home("old-speakerd")
        older = "0.0.1"
        for env, said in ((dict(self.env, STUB_VERSION=older), f"is {older}"),
                          ({k: v for k, v in self.env.items() if k != "STUB_VERSION"}, "too old to say its version")):
            result = self.install(home, "--role", "left", env=env, check=False)
            print(f"\n  {said}: status {result.returncode}")
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn(said, result.stdout)
            self.assertIn(f"needs speakerd {MIN_VERSION} or newer", result.stdout)
            self.assertFalse((home / ".local/lib/speakerd").exists(), result.stdout)

        # One that cannot be executed, as from a noexec /tmp.
        noexec = self.tmp / "bundle-noexec"
        shutil.rmtree(noexec, ignore_errors=True)
        shutil.copytree(self.bundle, noexec, symlinks=True)
        (noexec / "bin/speakerd").chmod(0o644)
        result = self.install(home, "--role", "left", bundle=noexec, check=False)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("noexec", result.stdout)
        self.assertFalse((home / ".local/lib/speakerd").exists(), result.stdout)

        # Rollback to a version whose speakerd is older: refused too.
        self.install(home, "--role", "left")
        self.install(home, bundle=self.bundle2)
        lib = home / ".local/lib/speakerd"
        previous = os.readlink(lib / "previous")
        version = lib / previous / "VERSION"
        version.write_text(re.sub(r"(?m)^speakerd_version=.*$", f"speakerd_version={older}", version.read_text()))
        refused = firmware("speakerd-setup.sh", "rollback", "--home", home, "--no-systemd", env=self.env, check=False)
        self.assertNotEqual(refused.returncode, 0, refused.stdout)
        self.assertIn(f"is {older}", refused.stdout)
        self.assertEqual(os.readlink(lib / "previous"), previous)

    def test_the_first_install_needs_a_role(self):
        home = self.fresh_home("no-role")
        host = self.plugin_host()
        result = self.install(home, "--stop-plugin-host", check=False)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("--role left|right|mono is required", result.stdout)
        self.assertFalse((home / ".local/lib/speakerd").exists(), result.stdout)
        self.assertIsNone(host.poll(), f"the plugin host was stopped by an install that refused:\n{result.stdout}")

    def test_the_layout(self):
        home = self.fresh_home("layout")
        state = home / ".local/state/speakerd"
        state.mkdir(parents=True)
        (state / "graph.json").write_text('{"ampd": "current"}')
        (state / "graph.lkg.json").write_text('{"ampd": "last known good"}')
        result = self.install(home, "--role", "left", "--factory", self.factory)
        self.assertIn("Every library speakerd and its plugins need is here", result.stdout)
        self.assertIn("installed. The limits: output at 0.0 dB, tweeter on.", result.stdout)
        vid = version_file(self.bundle / "VERSION")["id"]
        files = tree(home)
        for want in (f".local/lib/speakerd/{vid}/bin/speakerd", f".local/lib/speakerd/{vid}/bin/speakerd-run",
                     f".local/lib/speakerd/{vid}/lv2/loudness_headless.lv2/", f".local/lib/speakerd/{vid}/VERSION",
                     f".local/lib/speakerd/{vid}/setup/speakerd-setup.sh",
                     f".local/lib/speakerd/current -> {vid}", ".local/lib/speakerd/home/",
                     ".local/state/speakerd/device.json", ".local/state/speakerd/factory.json",
                     ".config/systemd/user/speakerd.service",
                     ".config/pipewire/pipewire.conf.d/60-speakerd-sink.conf"):
            self.assertIn(want, files, "\n".join(sorted(files)))
        installed = version_file(home / f".local/lib/speakerd/{vid}/VERSION")
        self.assertEqual(installed["speakerd_version"], MIN_VERSION)
        self.assertEqual(installed["speakerd_build"], f"speakerd {MIN_VERSION}")
        # firmware/CLAUDE.md calls the installed scripts directly: they are executable, and run.
        setup = home / f".local/lib/speakerd/{vid}/setup"
        for script in ("setup.sh", "speakerd-setup.sh", "snapcast-setup.sh", "speakerd/speakerd-run",
                       "speakerd/setup-helper.py"):
            self.assertTrue(os.access(setup / script, os.X_OK), script)
        direct = subprocess.run([str(home / ".local/lib/speakerd/current/setup/speakerd-setup.sh"), "status", "--home",
                                 str(home), "--no-systemd"], env={**os.environ, **TEST_ENV, **self.env},
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(direct.returncode, 0, direct.stdout + direct.stderr)
        self.assertRegex(direct.stdout, rf"current +{vid} +\(speakerd {re.escape(MIN_VERSION)},")
        self.assertEqual(list((home / ".local/lib/speakerd/home").iterdir()), [])
        self.assertEqual(json.loads((state / "device.json").read_text()),
                         {"version": 1, "role": "left",
                          "amplifier": {"node": "amplifier", "woofer": "playback_FL", "tweeter": "playback_FR"},
                          "limits": OPEN})
        self.assertEqual(json.loads((state / "factory.json").read_text()), DOCUMENT)
        self.assertEqual((home / ".config/systemd/user/speakerd.service").read_bytes(),
                         (FIRMWARE / "speakerd/speakerd.service").read_bytes())
        self.assertEqual((home / ".config/pipewire/pipewire.conf.d/60-speakerd-sink.conf").read_bytes(),
                         (FIRMWARE / "pipewire/pipewire.conf.d/60-speakerd-sink.conf").read_bytes())
        self.assertEqual((state / "graph.json").read_text(), '{"ampd": "current"}')
        self.assertEqual((state / "graph.lkg.json").read_text(), '{"ampd": "last known good"}')
        # The closing hints name the installed copy, never the bundle the installer ran from.
        for action in ("smoke", "rollback"):
            self.assertIn(f"{home}/.local/lib/speakerd/current/setup/speakerd-setup.sh {action}", result.stdout)
        self.assertNotIn(f"{FIRMWARE}/speakerd-setup.sh smoke", result.stdout)

    def test_a_reinstall_keeps_the_device_and_its_limits(self):
        home = self.fresh_home("reinstall")
        self.install(home, "--role", "left")
        device_json = home / ".local/state/speakerd/device.json"
        device = json.loads(device_json.read_text())
        device["limits"] = {"gain_db": -6.0, "tweeter": "on"}
        device["amplifier"]["tweeter"] = "playback_FL"
        device["amplifier"]["woofer"] = "playback_FR"
        device_json.write_text(json.dumps(device))
        before = tree(home / ".local/lib/speakerd")

        firmware("speakerd-setup.sh", "install", "--bundle", self.bundle, "--home", home, "--no-systemd",
                 env=self.env)
        self.assertEqual(tree(home / ".local/lib/speakerd"), before)
        again = json.loads(device_json.read_text())
        self.assertEqual(again["limits"], {"gain_db": -6.0, "tweeter": "on"})
        self.assertEqual((again["role"], again["amplifier"]),
                         ("left", {"node": "amplifier", "woofer": "playback_FR", "tweeter": "playback_FL"}))

        firmware("speakerd-setup.sh", "install", "--bundle", self.bundle, "--home", home, "--no-systemd",
                 "--role", "right", env=self.env)
        right = json.loads(device_json.read_text())
        self.assertEqual(right, dict(again, role="right"))

        # One port, or the node, alone: the rest stays device.json's.
        for option, value, key in (("--tweeter-port", "playback_RL", "tweeter"), ("--woofer-port", "playback_RR", "woofer"),
                                   ("--amplifier", "another-amplifier", "node")):
            expected = json.loads(device_json.read_text())
            expected["amplifier"][key] = value
            firmware("speakerd-setup.sh", "install", "--bundle", self.bundle, "--home", home, "--no-systemd",
                     option, value, env=self.env)
            self.assertEqual(json.loads(device_json.read_text()), expected, option)

        for text, said in ((json.dumps(dict(right, trim=1.5)), "trim"),
                           (json.dumps(dict(right, amplifier=dict(right["amplifier"], gain=1))), "amplifier.gain"),
                           ('{"version": 1, "role": "left", "role": "right"}', "role appear twice")):
            device_json.write_text(text)
            refused = firmware("speakerd-setup.sh", "install", "--bundle", self.bundle, "--home", home,
                               "--no-systemd", env=self.env, check=False)
            self.assertNotEqual(refused.returncode, 0, refused.stdout)
            self.assertIn(said, refused.stdout)
            self.assertEqual(device_json.read_text(), text, "a refused device.json was changed")

    def test_limits_writes_device_json(self):
        """`limits` writes exactly the limits given into device.json and nothing else of it; a gain
        above 0 dB or another tweeter limit is refused, device.json unchanged."""
        home = self.fresh_home("limits")
        self.install(home, "--role", "left")
        device_json = home / ".local/state/speakerd/device.json"
        device = json.loads(device_json.read_text())
        for gain, tweeter, limits in (("-20", "on", {"gain_db": -20.0, "tweeter": "on"}),
                                      ("-20", "muted", {"gain_db": -20.0, "tweeter": "muted"}),
                                      ("0", "on", OPEN)):
            with self.subTest(gain=gain, tweeter=tweeter):
                result = firmware("speakerd-setup.sh", "limits", "--gain-db", gain, "--tweeter-limit", tweeter,
                                  "--home", home, "--no-systemd", env=self.env)
                self.assertEqual(json.loads(device_json.read_text()), dict(device, limits=limits))
                self.assertIn(f"device.json limits: output at {limits['gain_db']:.1f} dB, tweeter {tweeter}",
                              result.stdout)
        before = device_json.read_text()
        for gain, tweeter, said in (("3", "on", "cannot be above 0 dB"), ("-20", "loud", "muted or on")):
            with self.subTest(gain=gain, tweeter=tweeter):
                result = firmware("speakerd-setup.sh", "limits", "--gain-db", gain, "--tweeter-limit", tweeter,
                                  "--home", home, "--no-systemd", env=self.env, check=False)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn(said, result.stdout)
                self.assertEqual(device_json.read_text(), before)

    def test_versions_rollback_and_pruning(self):
        home = self.fresh_home("versions")
        lib = home / ".local/lib/speakerd"
        one, two, three = (version_file(b / "VERSION")["id"] for b in (self.bundle, self.bundle2, self.bundle3))

        def link(name):
            return os.readlink(lib / name) if (lib / name).is_symlink() else None

        self.install(home, "--role", "left")
        self.install(home, bundle=self.bundle2)
        self.assertEqual((link("current"), link("previous")), (two, one))
        # device.json's limits stay as they are: rollback changes the version, nothing else.
        device_json = home / ".local/state/speakerd/device.json"
        device_json.write_text(json.dumps(dict(json.loads(device_json.read_text()), limits={"gain_db": -6.0, "tweeter": "on"})))
        firmware("speakerd-setup.sh", "rollback", "--home", home, "--no-systemd", env=self.env)
        self.assertEqual((link("current"), link("previous")), (one, two))
        self.assertEqual(json.loads(device_json.read_text())["limits"], {"gain_db": -6.0, "tweeter": "on"})
        firmware("speakerd-setup.sh", "rollback", "--home", home, "--no-systemd", env=self.env)
        self.assertEqual((link("current"), link("previous")), (two, one))
        status = firmware("speakerd-setup.sh", "status", "--home", home, "--no-systemd", env=self.env).stdout
        self.assertRegex(status, rf"current +{two} +\(speakerd {re.escape(MIN_VERSION)},")
        self.assertRegex(status, rf"previous +{one}")

        # A third version with --keep 2: the oldest goes, current and previous stay.
        self.install(home, "--keep", "2", bundle=self.bundle3)
        self.assertEqual((link("current"), link("previous")), (three, two))
        self.assertFalse((lib / one).exists(), "\n".join(sorted(tree(lib))))
        self.assertTrue((lib / two).is_dir())

    def test_the_old_plugin_host(self):
        home = self.fresh_home("plugin-host")
        host = self.plugin_host()
        autostart = home / ".config/autostart/plugin-host.desktop"
        autostart.parent.mkdir(parents=True)
        autostart.write_text(f"[Desktop Entry]\nExec=/opt/{TEST_PLUGIN_HOST} session.filtergraph\n")
        unit = home / ".config/systemd/user/plugin-host.service"
        unit.parent.mkdir(parents=True)
        unit.write_text(f"[Service]\nExecStart=/opt/{TEST_PLUGIN_HOST}\n")

        profile = home / ".profile"
        profile.write_text(f"/opt/{TEST_PLUGIN_HOST} &\n")

        result = self.install(home, "--role", "left", check=False)
        print(f"\n  refused with status {result.returncode}")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(f"{TEST_PLUGIN_HOST} runs (pid {host.pid})", result.stdout)
        self.assertIn("Refusing", result.stdout)
        self.assertFalse((home / ".local/lib/speakerd").exists())
        self.assertIsNone(host.poll(), "the plugin host was stopped without --stop-plugin-host")

        # With --stop-plugin-host, refused while anything a person must remove would start it
        # again at boot or login, naming each: nothing stopped, nothing installed.
        refused = self.install(home, "--role", "left", "--stop-plugin-host", check=False)
        self.assertNotEqual(refused.returncode, 0, refused.stdout)
        self.assertIn(f"{autostart}: a desktop autostart; delete the file", refused.stdout)
        self.assertIn(f"{profile}: remove the line that starts {TEST_PLUGIN_HOST}", refused.stdout)
        self.assertIn("Refusing to install until these are gone", refused.stdout)
        self.assertIn(f"{unit}: will be disabled (--stop-plugin-host)", refused.stdout)
        self.assertIsNone(host.poll(), f"stopped by an install that refused:\n{refused.stdout}")
        self.assertFalse((home / ".local/lib/speakerd").exists())
        autostart.unlink()
        profile.write_text("# nothing starts it here\n")

        # Refused for another reason (no such port) with --stop-plugin-host: nothing stopped.
        refused = self.install(home, "--role", "left", "--stop-plugin-host", "--woofer-port", "playback_FR",
                               "--tweeter-port", "playback_FR", check=False)
        self.assertNotEqual(refused.returncode, 0, refused.stdout)
        self.assertIsNone(host.poll(), f"stopped by an install that refused:\n{refused.stdout}")

        result = self.install(home, "--role", "left", "--stop-plugin-host")
        self.assertIsNotNone(host.poll(), f"the plugin host still runs:\n{result.stdout}")
        self.assertIn(f"Stopping {TEST_PLUGIN_HOST}", result.stdout)
        self.assertIn("systemctl --user disable --now plugin-host.service", result.stdout)
        self.assertTrue((home / ".local/lib/speakerd/current").is_symlink())
        uninstalled = firmware("speakerd-setup.sh", "uninstall", "--home", home, "--no-systemd", env=self.env).stdout
        self.assertIn("systemctl --user enable --now plugin-host.service", uninstalled)
        self.assertFalse((home / ".local/state/speakerd/plugin-host-units").exists())

    def test_the_bundles_chain_document(self):
        """firmware/speakerd/factory.json is the board's chain over the headless builds, and a first
        install writes it when the bundle has every plugin it names."""
        chain = CHAIN_DOCUMENT
        uris = {node["plugin"]["identifier"] for node in chain["nodes"]}
        self.assertEqual(uris, {lv2_uri(t) for t in ("loudness_headless", "crossover_headless", "firConv_headless")})
        firconv = [node for node in chain["nodes"] if node["plugin"]["identifier"] == lv2_uri("firConv_headless")]
        self.assertEqual([node["params"]["gain"] for node in firconv], [-12.0])
        tweeter_feeds = [c["src"] for c in chain["connections"] if c["dst"] == {"node": 2, "channel": 1}]
        crossover = [node["uid"] for node in chain["nodes"] if node["plugin"]["identifier"] == lv2_uri("crossover_headless")]
        self.assertEqual(tweeter_feeds, [{"node": crossover[0], "channel": 1}], "the crossover's high band on the tweeter")

        home = self.fresh_home("chain")
        result = self.install(home, "--role", "left", bundle=self.board_bundle)
        self.assertIn("No factory.json yet: installing the bundle's chain document", result.stdout)
        self.assertIn("factory.json: revision 1, 3 plugin node(s): firConv, crossover, loudness", result.stdout)
        self.assertEqual(json.loads((home / ".local/state/speakerd/factory.json").read_text()), chain)
        self.assertNotIn("PASSTHROUGH", result.stdout)

        # A bundle without the plugins it names: no chain document, passthrough said, the plugins named.
        home = self.fresh_home("chain-lacking")
        result = self.install(home, "--role", "left")
        self.assertFalse((home / ".local/state/speakerd/factory.json").exists())
        self.assertIn("PASSTHROUGH", result.stdout)
        self.assertIn("not installed: it", result.stdout)
        for uri in ("firConv_headless", "crossover_headless"):
            self.assertIn(lv2_uri(uri), result.stdout)
        self.assertNotIn(lv2_uri("loudness_headless") + " ", result.stdout + " ")

    def test_refuses_a_kept_layer_naming_a_plugin_the_bundle_lacks(self):
        """A factory.json an install keeps, or a --factory file, must name only plugins of the bundle
        being installed: speakerd could not load it and the speaker would be silent. ampd's layers
        are never rewritten."""
        home = self.fresh_home("kept")
        state = home / ".local/state/speakerd"
        self.install(home, "--role", "left", bundle=self.board_bundle)
        self.assertEqual(json.loads((state / "factory.json").read_text()), CHAIN_DOCUMENT)
        before = tree(home)

        def refused(result, what):
            self.assertNotEqual(result.returncode, 0, f"{what}:\n{result.stdout}")
            self.assertIn("names plugins this bundle lacks", result.stdout, what)
            for uri in ("firConv_headless", "crossover_headless"):
                self.assertIn(lv2_uri(uri), result.stdout, what)
            self.assertNotIn(lv2_uri("loudness_headless") + "\n", result.stdout, what)
            self.assertIn("--factory firmware/speakerd/factory.json", result.stdout, what)
            self.assertIn(":headless", result.stdout, what)
            self.assertEqual(tree(home), before, f"{what} changed the home")

        # The loudness-only bundle keeps the board's factory.json: refused before anything changes.
        refused(self.install(home, check=False), "the kept factory.json")
        refused(self.install(home, "--factory", FIRMWARE / "speakerd/factory.json", check=False), "the --factory file")
        refused(firmware("speakerd-setup.sh", "check", "--bundle", self.bundle, "--home", home, "--no-systemd",
                         "--amplifier", "amplifier", env=self.env, check=False), "check")
        self.assertEqual(json.loads((state / "factory.json").read_text()), CHAIN_DOCUMENT)

        # ampd's graph.json names the chain (revision 31): the install goes on with a factory the
        # bundle can load, leaves graph.json alone and says nothing of it.
        revision_31 = json.dumps(dict(CHAIN_DOCUMENT, revision=31), indent=1)
        (state / "graph.json").write_text(revision_31)
        (state / "graph.lkg.json").write_text(json.dumps(dict(CHAIN_DOCUMENT, revision=30)))
        result = self.install(home, "--factory", self.factory)
        self.assertNotIn("graph.json", result.stdout)
        self.assertEqual((state / "graph.json").read_text(), revision_31)
        self.assertEqual(json.loads((state / "factory.json").read_text()), DOCUMENT)
        status = firmware("speakerd-setup.sh", "status", "--home", home, "--no-systemd", env=self.env).stdout
        self.assertRegex(status, r"(?m)^  graph\.json: revision 31, 3 plugin node\(s\): firConv, crossover, loudness "
                                 r"\*\*\* names plugins the current version lacks: \S*firConv_headless \S*crossover_headless: "
                                 r"speakerd plays the next layer \*\*\*$")
        self.assertRegex(status, r"(?m)^  factory\.json: revision 1, 1 plugin node\(s\): loudness_headless$")

    def test_refuses_a_factory_that_is_not_a_graph_document(self):
        """A --factory file, or a kept factory.json, that `setup-helper.py graph` refuses (here a
        connection to node 99, which the document lacks) is refused before anything changes;
        status marks such a layer with the helper's reason."""
        home = self.fresh_home("broken-factory")
        state = home / ".local/state/speakerd"
        self.install(home, "--role", "left", "--factory", self.factory)
        broken = dict(DOCUMENT, connections=DOCUMENT["connections"] + [
            {"src": {"node": 99, "channel": 0}, "dst": {"node": DOCUMENT["nodes"][0]["uid"], "channel": 0}}])
        broken_file = self.tmp / "broken-factory.json"
        broken_file.write_text(json.dumps(broken))
        reason = "a connection names node 99, which is not in the document"

        before = snapshot(home)
        result = self.install(home, "--factory", broken_file, check=False)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(reason, result.stdout)
        self.assertIn("refusing this factory chain", result.stdout)
        self.assertEqual(snapshot(home), before, "a refused --factory file changed the home")
        self.assertEqual(json.loads((state / "factory.json").read_text()), DOCUMENT)

        (state / "factory.json").write_text(json.dumps(broken))
        before = snapshot(home)
        for what, result in (("install", self.install(home, check=False)),
                             ("check", firmware("speakerd-setup.sh", "check", "--bundle", self.bundle, "--home", home,
                                                "--no-systemd", "--amplifier", "amplifier", env=self.env,
                                                check=False))):
            with self.subTest(what):
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn(reason, result.stdout)
                self.assertIn("the factory.json there is (kept) is refused", result.stdout)
                self.assertIn("replace it with --factory or --import-filtergraph", result.stdout)
                self.assertEqual(snapshot(home), before, f"{what} changed the home")
        self.assertEqual(json.loads((state / "factory.json").read_text()), broken)
        status = firmware("speakerd-setup.sh", "status", "--home", home, "--no-systemd", env=self.env).stdout
        self.assertRegex(status, rf"(?m)^  factory\.json: \*\*\* \S*factory\.json: {reason} \*\*\*$")

    def test_names_a_bundle_shadowed_by_a_system_lv2_folder(self):
        # lilv keeps the first bundle of a URI unless a later one is newer: a system copy of the
        # same version or a newer one makes speakerd refuse the deployed bundle, older ones are
        # replaced by it. Each is named with its version, and the message says which case it is.
        headless = built("loudness", "headless")
        minor, micro, version = lv2_version(headless)
        self.assertTrue(minor and micro and version, f"{headless}/dsp.ttl does not say its version as JUCE writes it")
        minor, micro = int(minor), int(micro)
        self.assertGreater(minor + micro, 0, f"loudness_headless is {version}: no older version to make")
        older = (minor, micro - 1) if micro > 0 else (minor - 1, micro)
        home = self.fresh_home("shadow")
        systems = [self.tmp / "system-lv2-a", self.tmp / "system-lv2-b"]
        for system in systems:
            shutil.rmtree(system, ignore_errors=True)
        same = systems[0] / "same-loudness.lv2"
        shutil.copytree(headless, same)
        newer = systems[1] / "newer-loudness.lv2"
        newer_version = copy_at_version(headless, newer, minor, micro + 1)
        old = systems[1] / "older-loudness.lv2"
        old_version = copy_at_version(headless, old, *older)

        uri = lv2_uri("loudness_headless")
        named = "A system LV2 folder has a plugin with the URI of one of the bundle's:"
        refuses = ("With a copy of the same version or a newer one, speakerd will refuse to start (status 2)\n"
                   "and the speaker stay silent.")
        replaced = ("Each is older, so the deployed bundle replaces it; but it shadows the next deploy that is not\n"
                    "newer than it, which speakerd would refuse (status 2).")

        def said(dirs):
            result = self.install(home, "--role", "left",
                                  env=dict(self.env, SPEAKERD_SYSTEM_LV2_DIRS=" ".join(map(str, dirs))))
            loud = [line[4:] for line in result.stdout.splitlines() if line.startswith("*** ")]
            print("\n  " + "\n  ".join(loud))
            return loud, "\n".join(loud)

        loud, text = said(systems)
        self.assertIn(named, loud)
        for copy, at, relation in ((same, version, "the same as"), (newer, newer_version, "newer than"),
                                   (old, old_version, "older than")):
            self.assertIn(f"    {uri}: {copy}, version {at}, {relation} the bundle's {version}", loud)
        self.assertEqual(sum(f"{uri}: " in line for line in loud), 3,
                         "not one line per system bundle (a preset's URI apart from its plugin's?)")
        self.assertIn(refuses, text)
        self.assertNotIn(replaced, text)

        # Only the older copy: the deployed bundle replaces it, and it is named as loudly.
        shutil.rmtree(newer)
        loud, text = said([systems[1]])
        self.assertIn(named, loud)
        self.assertIn(f"    {uri}: {old}, version {old_version}, older than the bundle's {version}", loud)
        self.assertEqual(sum(f"{uri}: " in line for line in loud), 1, loud)
        self.assertIn(replaced, text)
        self.assertNotIn(refuses, text)

        quiet = self.install(home, check=True)
        self.assertNotIn("A system LV2 folder", quiet.stdout)

    def test_import_filtergraph(self):
        session = self.tmp / "session.filtergraph"
        session.write_text("<FILTERGRAPH/>\n")
        converted = self.tmp / "converted.json"
        converted.write_text(json.dumps(DOCUMENT))
        home = self.fresh_home("import")
        (self.tmp / "stub/calls").unlink(missing_ok=True)
        env = dict(self.env, STUB_IMPORT=str(converted))
        result = firmware("speakerd-setup.sh", "install", "--bundle", self.bundle, "--home", home, "--no-systemd",
                          "--amplifier", "amplifier", "--role", "left", "--import-filtergraph", session, env=env)
        self.assertIn("Converting", result.stdout)
        self.assertEqual(json.loads((home / ".local/state/speakerd/factory.json").read_text()), DOCUMENT)
        calls = [line for line in (self.tmp / "stub/calls").read_text().splitlines() if line.startswith("import: ")]
        self.assertEqual(len(calls), 1, calls)
        self.assertRegex(calls[0], rf"--import-filtergraph {re.escape(str(session))} --prefer-headless --device \S+/device\.json ")
        self.assertNotIn("--allow-unsafe-routing", calls[0])
        failed = firmware("speakerd-setup.sh", "install", "--bundle", self.bundle, "--home", home, "--no-systemd",
                          "--import-filtergraph", session, env=self.env, check=False)
        self.assertNotEqual(failed.returncode, 0, failed.stdout)
        self.assertIn("speakerd: import: FAILED", failed.stdout)

    def test_finding_the_amplifier(self):
        def sink(i, name, alsa=True, priority=None):
            props = {"node.name": name, "media.class": "Audio/Sink"}
            props["factory.name"] = "api.alsa.pcm.sink" if alsa else "support.null-audio-sink"
            if priority is not None:
                props["priority.driver"] = priority
            return [{"id": i, "type": "PipeWire:Interface:Node", "info": {"props": props}}] + [
                {"id": i * 10 + k, "type": "PipeWire:Interface:Port",
                 "info": {"direction": "input", "props": {"node.id": i, "port.name": f"playback_{c}"}}}
                for k, c in enumerate(("FL", "FR"))]

        def amp(dump, *args):
            path = self.tmp / "dump.json"
            path.write_text(json.dumps(dump))
            return subprocess.run(["python3", str(FIRMWARE / "speakerd/setup-helper.py"), "amp", str(path), *args],
                                  capture_output=True, text=True)

        one = amp(sink(1, "bpi-alsa-playback-pcm", priority=1000) + sink(2, "speakerd", alsa=False))
        self.assertEqual(one.returncode, 0, one.stderr)
        self.assertIn("node='bpi-alsa-playback-pcm'", one.stdout)
        self.assertIn("ports='playback_FL playback_FR'", one.stdout)
        self.assertIn("priority='1000'", one.stdout)
        for name in ("bpi-alsa-playback-pcm", "alsa-playback-pcm"):
            several = amp(sink(1, "hdmi-out") + sink(2, name))
            self.assertIn(f"node='{name}'", several.stdout, several.stderr)
        ambiguous = amp(sink(1, "hdmi-out") + sink(2, "usb-out"))
        self.assertNotEqual(ambiguous.returncode, 0)
        self.assertIn("hdmi-out, usb-out", ambiguous.stderr)
        none = amp(sink(1, "amplifier", alsa=False))
        self.assertNotEqual(none.returncode, 0)
        self.assertIn("found no ALSA Audio/Sink", none.stderr)

        with Pair() as pair:
            env = dict(self.env, XDG_RUNTIME_DIR=pair["left"].env["XDG_RUNTIME_DIR"])
            home = self.fresh_home("live")
            refused = firmware("speakerd-setup.sh", "install", "--bundle", self.bundle, "--home", home,
                               "--no-systemd", "--role", "left", env=env, check=False)
            self.assertNotEqual(refused.returncode, 0, refused.stdout)
            self.assertIn("found no ALSA Audio/Sink", refused.stdout)
            found = firmware("speakerd-setup.sh", "check", "--bundle", self.bundle, "--home", home,
                             "--no-systemd", "--role", "left", "--amplifier", "amplifier", env=env)
            self.assertIn("ports playback_FL playback_FR", found.stdout)
            self.assertIn("woofer = amplifier:playback_FL, tweeter = amplifier:playback_FR", found.stdout)

    def test_smoke_reports_limits_it_does_not_play(self):
        home = self.fresh_home("smoke")
        self.install(home, "--role", "left")
        device_json = home / ".local/state/speakerd/device.json"
        device_json.write_text(json.dumps(dict(json.loads(device_json.read_text()), limits=CAPPED)))
        start = "speakerd-run: starting speakerd x from layer current (test)"
        not_capped = "FAIL speakerd does not play device.json's limits"
        cases = {
            "not read": ([start, "speakerd: device.json: FAILED: limits: unknown key 'limts'; using the defaults",
                          LIMITS + " (device.json not read: the fail-safe)"],
                         ["FAIL device.json: FAILED", not_capped]),
            "no limits": ([start, LIMITS + " (no limits in device.json: the fail-safe)"], [not_capped]),
            "another suffix": ([start, LIMITS + " (something else)"], [not_capped]),
            "own sink": ([start, LIMITS, "speakerd: pipewire: created the input sink 'speakerd'",
                          "speakerd: *** NO INPUT SINK CONFIGURED: PipeWire had no node 'speakerd', so speakerd made it ***"],
                         ["FAIL *** NO INPUT SINK CONFIGURED"]),
            "silent": ([start], ["FAIL speakerd did not say which limits it enforces"]),
        }
        for name, (lines, fails) in cases.items():
            log = self.tmp / f"smoke-{name.replace(' ', '-')}.log"
            log.write_text("\n".join(lines) + "\n")
            result = firmware("speakerd-setup.sh", "smoke", "--home", home, "--no-systemd", "--log", log,
                              "--window", "0", env=self.env, check=False)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertRegex(result.stdout, r"smoke: \d+ failed, \d+ warning\(s\)")
            if name != "own sink":
                self.assertNotIn("pass device.json's limits", result.stdout, name)
            for fail in fails:
                self.assertIn(fail, result.stdout, name)
        # The exact line, with device.json's own limits, passes that check (no PipeWire here, so
        # smoke fails on the sink and the links all the same, and still prints its summary).
        log = self.tmp / "smoke-capped.log"
        log.write_text("\n".join([start, LIMITS]) + "\n")
        result = firmware("speakerd-setup.sh", "smoke", "--home", home, "--no-systemd", "--log", log,
                          "--window", "0", env=self.env, check=False)
        self.assertIn("pass device.json's limits: output at -20 dB, tweeter muted", result.stdout)
        self.assertIn("FAIL the speakerd sink is not the drop-in's", result.stdout)
        # A broken device.json: every check still reports, and the summary prints.
        (home / ".local/state/speakerd/device.json").write_text("{ broken")
        result = firmware("speakerd-setup.sh", "smoke", "--home", home, "--no-systemd", "--log", log,
                          "--window", "0", env=self.env, check=False)
        self.assertIn("FAIL device.json cannot be read", result.stdout)
        self.assertRegex(result.stdout, r"smoke: \d+ failed")

    def test_smoke_finds_snapclient_by_its_application_name(self):
        """snapclient 0.34.0's stream has no node.name (pw-link lists its ports under an empty
        name): smoke finds it by application.name in pw-dump and reads its links there."""
        home = self.fresh_home("smoke-snapclient")
        self.install(home, "--role", "left")
        log = self.tmp / "smoke-snapclient.log"
        log.write_text("speakerd-run: starting speakerd x from layer current (test)\n" + LIMITS + "\n")

        def node(i, **props):
            return {"id": i, "type": "PipeWire:Interface:Node", "info": {"props": props}}

        def port(i, node_id, direction, name):
            return {"id": i, "type": "PipeWire:Interface:Port",
                    "info": {"direction": direction, "props": {"node.id": node_id, "port.name": name}}}

        def link(i, src, src_port, dst, dst_port):
            return {"id": i, "type": "PipeWire:Interface:Link",
                    "info": {"output-node-id": src, "output-port-id": src_port, "input-node-id": dst,
                             "input-port-id": dst_port, "state": "active"}}

        sinks = [node(30, **{"node.name": "speakerd", "factory.name": "support.null-audio-sink",
                             "media.class": "Audio/Sink", "priority.session": 1500, "priority.driver": 0}),
                 port(31, 30, "input", "playback_FL"), port(32, 30, "input", "playback_FR"),
                 node(40, **{"node.name": "amplifier", "factory.name": "support.null-audio-sink",
                             "media.class": "Audio/Sink"}),
                 port(41, 40, "input", "playback_FL"), port(42, 40, "input", "playback_FR"),
                 {"id": 70, "type": "PipeWire:Interface:Metadata", "props": {"metadata.name": "default"},
                  "metadata": [{"subject": 0, "key": "default.audio.sink", "value": {"name": "speakerd"}}]}]
        # As on the board: no node.name, application.name Snapclient.
        stream = [node(50, **{"application.name": "Snapclient", "media.class": "Stream/Output/Audio",
                              "target.object": "speakerd"}),
                  port(51, 50, "output", "output_FL"), port(52, 50, "output", "output_FR")]
        pw = self.tmp / "smoke-snapclient-pipewire"
        for tool in ("pw-dump", "pw-link", "pw-profiler"):
            write_script(pw / "bin" / tool, '#!/bin/bash\nd="${FAKE_PIPEWIRE:?}"\ncase "$(basename "$0")" in\n'
                                            '    pw-dump) cat "$d/dump.json" ;;\n    pw-link) cat "$d/links" ;;\nesac\n')
        env = dict(self.env, FAKE_PIPEWIRE=str(pw), PATH=f"{pw / 'bin'}:{os.environ['PATH']}")
        cases = {"into speakerd": (stream + [link(60, 50, 51, 30, 31), link(61, 50, 52, 30, 32)],
                                   "\n  |-> speakerd:playback_FL\n\n  |-> speakerd:playback_FR\n",
                                   "pass snapclient plays into the speakerd sink"),
                 "elsewhere": (stream + [link(60, 50, 51, 40, 41), link(61, 50, 52, 40, 42)],
                               "\n  |-> amplifier:playback_FL\n\n  |-> amplifier:playback_FR\n",
                               "warn snapclient plays into: amplifier:playback_FL amplifier:playback_FR"),
                 "linked to nothing": (stream, "", "warn snapclient's stream (node 50) is linked to nothing"),
                 "none": ([], "", "info no snapclient stream in PipeWire now")}
        for name, (extra, links, said) in cases.items():
            with self.subTest(name):
                (pw / "dump.json").write_text(json.dumps(sinks + extra))
                (pw / "links").write_text(links)
                result = firmware("speakerd-setup.sh", "smoke", "--home", home, "--no-systemd", "--log", log,
                                  "--window", "0", env=env, check=False)
                print(f"\n  {name}: " + next((line.strip() for line in result.stdout.splitlines() if "snapclient" in line), "-"))
                self.assertIn(said, result.stdout)
                if name != "none":
                    self.assertNotIn("no snapclient stream", result.stdout)

        # Its application.name may be on its client only; a node.name naming it, with none, counts too.
        dump = self.tmp / "stream-dump.json"
        for props, client in (({"client.id": 80, "media.class": "Stream/Output/Audio"}, {"application.name": "Snapclient"}),
                              ({"node.name": "Snapcast", "media.class": "Stream/Output/Audio"}, {})):
            dump.write_text(json.dumps([node(50, **props), {"id": 80, "type": "PipeWire:Interface:Client",
                                                            "info": {"props": client}}]))
            found = subprocess.run(["python3", str(FIRMWARE / "speakerd/setup-helper.py"), "stream", str(dump),
                                    "Snapclient", "Snapcast"], capture_output=True, text=True)
            self.assertEqual(found.returncode, 0, found.stderr)
            self.assertIn("nodes='50'", found.stdout)

    def test_smoke_reports_the_timing(self):
        home = self.fresh_home("smoke-timing")
        self.install(home, "--role", "left")
        start = "speakerd-run: starting speakerd x from layer current (test)"

        def minute(graph_max, late=0, jumps=0):
            return (f"speakerd: timing: last 60 s: node 16 (loudness_headless) mean 1.2 % max 3.4 %; "
                    f"graph mean 1.3 % max {graph_max} %; cycle mean 1.5 % max 3.9 %; 2813 cycles, {late} late, "
                    f"{jumps} position jump(s)")

        def smoke(lines, window="0", env=None, during=()):
            """smoke over a log of `lines`, `during` appended to it while it watches."""
            log = self.tmp / "smoke-timing.log"
            log.write_text("\n".join(lines) + "\n")
            run = subprocess.Popen(["bash", str(FIRMWARE / "speakerd-setup.sh"), "smoke", "--home", str(home),
                                    "--no-systemd", "--log", str(log), "--window", window],
                                   env={**os.environ, **TEST_ENV, **(env or self.env)}, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            out = []
            with run.stdout:
                for line in run.stdout:
                    out.append(line)
                    if "info watching for" in line and during:
                        with log.open("a") as f:
                            f.write("\n".join(during) + "\n")
            run.wait(timeout=60)
            return "".join(out)

        # From the log: its last minute line; nothing late in the window.
        out = smoke([start, LIMITS, minute(3.6)])
        print("\n" + "".join(line + "\n" for line in out.splitlines() if "timing" in line or "cycle" in line))
        self.assertIn("info timing: node 16 (loudness_headless) mean 1.2 % max 3.4 % of the cycle", out)
        self.assertIn("info timing: graph mean 1.3 % max 3.6 %, cycle mean 1.5 % max 3.9 %; 2813 cycles, 0 late, "
                      "0 position jump(s)", out)
        self.assertIn("pass the graph's longest cycle took 3.6 % of its cycle", out)
        self.assertIn("pass no late cycle in 0 s", out)
        # The graph above half its cycle fails; at exactly half it passes.
        self.assertIn("FAIL the graph's longest cycle took 50.1 % of its cycle", smoke([start, LIMITS, minute(50.1)]))
        self.assertIn("pass the graph's longest cycle took 50.0 % of its cycle", smoke([start, LIMITS, minute(50.0)]))
        # Late cycles and position jumps said while it watches: a failure and a warning.
        second = "speakerd: timing: in the last second {} late cycle(s), the longest at 104.2 % of its cycle; {} position jump(s)"
        out = smoke([start, LIMITS, minute(3.6)], window="2", during=[second.format(2, 1), second.format(0, 1)])
        self.assertIn("FAIL 2 late cycle(s) in 2 s", out)
        self.assertIn("warn 2 position jump(s) in 2 s", out)
        # Late cycles before the window: a warning only.
        out = smoke([start, LIMITS, minute(3.6, late=3)])
        self.assertIn("pass no late cycle in 0 s", out)
        self.assertIn("warn 3 late cycle(s) before the window", out)
        # A speakerd that has not logged a minute yet, its link busy (ampd holds it): not measured.
        out = smoke([start, LIMITS])
        self.assertIn("warn the graph's timing was NOT measured", out)
        self.assertIn("info timing: speakerd did not say what a clock read costs", out)
        # Passthrough: no plugin node, so the minute line begins with the graph.
        out = smoke([start, LIMITS, "speakerd: timing: last 60 s: graph mean 0.0 % max 0.1 %; cycle mean 0.2 % "
                                    "max 0.4 %; 2813 cycles, 0 late, 0 position jump(s)"])
        self.assertIn("pass the graph's longest cycle took 0.1 % of its cycle (speakerd's log", out)
        self.assertNotIn("info timing: node", out)
        # What a clock read costs, from speakerd's start-up line: tens of ns (the vDSO) is said;
        # above 150 ns (system calls) is a warning, with what per-node timing then costs.
        out = smoke([start, "speakerd: timing: a clock read costs 44.6 ns", LIMITS, minute(3.6)])
        self.assertIn("2813 cycles, 0 late, 0 position jump(s); a clock read costs 44.6 ns (speakerd's log", out)
        self.assertNotIn("clock reads are system calls", out)
        out = smoke([start, "speakerd: timing: a clock read costs 812.0 ns ", LIMITS, minute(3.6)])
        print("  " + next(line for line in out.splitlines() if "clock reads" in line))
        self.assertIn("a clock read costs 812 ns (speakerd's log", out)
        self.assertIn("warn clock reads are system calls on this kernel (ARM erratum 858921 workaround?): per-node "
                      "timing costs about 6\u00d7812 ns = 4.9 us per cycle", out)
        self.assertNotIn("FAIL clock", out)

        # From speakerd's status on its local link, read before and after the window.
        runtime = self.tmp / "timing-runtime"
        (runtime / "speakerd").mkdir(parents=True, exist_ok=True)
        path = runtime / "speakerd/control.sock"
        answers = []

        def serve(server):
            while True:
                try:
                    conn, _ = server.accept()
                except OSError:
                    return
                with conn:
                    if not answers:
                        conn.sendall(b'{"op":"error","reason":"busy"}\n')
                        continue
                    conn.sendall(b'{"op":"hello","role":"speakerd","protocol":1}\n')
                    conn.recv(4096)
                    conn.sendall(json.dumps(dict(op="status", timing=answers.pop(0))).encode() + b"\n")

        timing = {"cycles": 2810, "late": 0, "jumps": 0, "seconds": 60,
                  "cycle": {"mean": 0.0018, "max": 0.0035}, "graph": {"mean": 0.0016, "max": 0.0032},
                  "nodes": [{"uid": 16, "name": "loudness_headless", "mean": 0.00098, "max": 0.0016}],
                  "cycles_total": 9000, "late_total": 5, "jumps_total": 1, "dropped_total": 0}
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(path))
        server.listen(4)
        threading.Thread(target=serve, args=(server,), daemon=True).start()
        try:
            env = dict(self.env, XDG_RUNTIME_DIR=str(runtime))
            answers[:] = [timing, dict(timing, late_total=5, jumps_total=1)]
            out = smoke([start, LIMITS], env=env)
            self.assertIn("pass the graph's longest cycle took 0.3 % of its cycle (speakerd's status, the last 60 s", out)
            self.assertIn("info timing: node 16 (loudness_headless) mean 0.1 % max 0.2 % of the cycle", out)
            self.assertIn("pass no late cycle in 0 s (speakerd's late_total and jumps_total before and after", out)
            answers[:] = [timing, dict(timing, late_total=7, jumps_total=2, graph={"mean": 0.3, "max": 0.71})]
            out = smoke([start, LIMITS], env=env)
            self.assertIn("FAIL the graph's longest cycle took 71.0 % of its cycle", out)
            self.assertIn("FAIL 2 late cycle(s) in 0 s", out)
            self.assertIn("warn 1 position jump(s) in 0 s", out)
            # Busy (ampd holds the link): the log's minute line instead.
            answers[:] = []
            out = smoke([start, LIMITS, minute(3.6)], env=env)
            self.assertIn("pass the graph's longest cycle took 3.6 % of its cycle (speakerd's log", out)
        finally:
            server.close()

    def test_routing_and_uninstall(self):
        home = self.fresh_home("routing")
        crt = home / ".config/pipewire/client-rt.conf"
        refused = firmware("snapcast-setup.sh", "--mode", "target", "--routing-only", "--route", "speakerd",
                           "--client-home", home, env=self.env, check=False)
        self.assertNotEqual(refused.returncode, 0, refused.stdout)
        self.assertIn("would fall back to the amplifier", refused.stdout)
        self.assertFalse(crt.exists())

        original = '# a person\'s own routing\nstream.rules = [ { matches = [ { application.name = "Snapclient" } ] actions = { update-props = { target.object = "Ap.cap" } } } ]\n'
        crt.parent.mkdir(parents=True, exist_ok=True)
        crt.write_text(original)
        firmware("speakerd-setup.sh", "sink", "--home", home, "--no-systemd", env=self.env)
        firmware("snapcast-setup.sh", "--mode", "target", "--routing-only", "--route", "speakerd",
                 "--client-home", home, env=self.env)
        text = crt.read_text()
        self.assertEqual(text.count('target.object = "speakerd"'), 3, text)
        self.assertNotIn("Ap.cap", text.split("stream.rules")[1])
        self.assertEqual((home / ".config/pipewire/client-rt.conf.before-speakerd").read_text(), original)

        self.install(home, "--role", "left")
        firmware("speakerd-setup.sh", "uninstall", "--home", home, "--no-systemd", env=self.env)
        self.assertEqual(crt.read_text(), original, "uninstall did not restore the routing from before speakerd")
        self.assertFalse((home / ".config/pipewire/client-rt.conf.before-speakerd").exists())
        for gone in (".config/systemd/user/speakerd.service", ".config/pipewire/pipewire.conf.d/60-speakerd-sink.conf",
                     ".local/lib/speakerd"):
            self.assertFalse((home / gone).exists(), gone)
        self.assertTrue((home / ".local/state/speakerd/device.json").exists())

        # With no routing kept: the AudioPluginHost route is written.
        crt.unlink()
        firmware("speakerd-setup.sh", "sink", "--home", home, "--no-systemd", env=self.env)
        firmware("snapcast-setup.sh", "--mode", "target", "--routing-only", "--route", "speakerd",
                 "--client-home", home, env=self.env)
        self.install(home, "--role", "left")
        firmware("speakerd-setup.sh", "uninstall", "--home", home, "--no-systemd", "--purge", env=self.env)
        self.assertIn('target.object = "Ap.cap"', crt.read_text())
        self.assertNotIn('target.object = "speakerd"', crt.read_text())
        self.assertFalse((home / ".local/state/speakerd").exists())

    def test_routing_into_speakerd_needs_a_unit_that_reads_client_rt(self):
        home = self.fresh_home("routing-unit")
        crt = home / ".config/pipewire/client-rt.conf"
        unit = home / ".config/systemd/user/snapclient.service"
        unit.parent.mkdir(parents=True)
        unit.write_text("[Service]\nExecStart=/usr/bin/snapclient\n")
        firmware("speakerd-setup.sh", "sink", "--home", home, "--no-systemd", env=self.env)

        def route(*args):
            return firmware("snapcast-setup.sh", "--mode", "target", "--routing-only", "--client-home", home, *args,
                            env=self.env, check=False)

        refused = route("--route", "speakerd")
        self.assertNotEqual(refused.returncode, 0, refused.stdout)
        self.assertIn("does not point snapclient at client-rt.conf", refused.stdout)
        self.assertFalse(crt.exists(), "client-rt.conf was written all the same")
        refused = route("--route", "speakerd", "--no-client-rt")
        self.assertNotEqual(refused.returncode, 0, refused.stdout)
        self.assertIn("--route speakerd needs client-rt.conf", refused.stdout)
        # Into Ap.cap it only warns, as before speakerd.
        warned = route("--route", "plugin-host")
        self.assertEqual(warned.returncode, 0, warned.stdout)
        self.assertIn("does not point snapclient at client-rt.conf", warned.stdout)

        unit.write_text("[Service]\nEnvironment=PIPEWIRE_CONFIG_NAME=client-rt.conf\nExecStart=/usr/bin/snapclient\n")
        routed = route("--route", "speakerd")
        self.assertEqual(routed.returncode, 0, routed.stdout)
        self.assertIn('target.object = "speakerd"', crt.read_text())
        self.assertEqual([f.name for f in crt.parent.iterdir() if ".tmp." in f.name], [], "a temporary file was left")

    def test_uninstall_stops_snapclient_when_its_old_target_is_gone(self):
        """The routing uninstall restores must name a node the running PipeWire has: else
        snapclient would play into the default sink, the amplifier."""
        with Pair() as pair:
            env = dict(self.env, XDG_RUNTIME_DIR=pair["left"].env["XDG_RUNTIME_DIR"])
            for target, stopped in (("Ap.cap", True), ("amplifier", False)):
                with self.subTest(target):
                    home = self.fresh_home(f"uninstall-{target}")
                    self.install(home, "--role", "left")
                    crt = home / ".config/pipewire/client-rt.conf"
                    crt.write_text('stream.rules = [ { matches = [ { application.name = "Snapclient" } ] '
                                   'actions = { update-props = { target.object = "speakerd" } } } ]\n')
                    before = crt.read_text().replace('"speakerd"', f'"{target}"')
                    (home / ".config/pipewire/client-rt.conf.before-speakerd").write_text(before)
                    result = firmware("speakerd-setup.sh", "uninstall", "--home", home, "--no-systemd", env=env)
                    self.assertEqual(crt.read_text(), before)
                    if stopped:
                        self.assertIn(f"sends it to '{target}', which the running PipeWire does not have", result.stdout)
                        self.assertIn("systemctl --user stop snapclient.service", result.stdout)
                        self.assertIn("systemctl --user disable snapclient.service", result.stdout)
                    else:
                        self.assertIn(f"snapclient's target '{target}' exists", result.stdout)
                        self.assertNotIn("stop snapclient.service", result.stdout)

    def test_setup_checks_first_and_installs_in_boot_order(self):
        home = self.fresh_home("setup")
        unit = home / ".config/systemd/user/snapclient.service"
        unit.parent.mkdir(parents=True)
        unit.write_text("[Service]\nEnvironment=PIPEWIRE_CONFIG_NAME=client-rt.conf\nExecStart=/usr/bin/snapclient\n")
        args = ["--home", home, "--no-systemd", "--bundle", self.bundle, "--role", "left", "--amplifier", "amplifier",
                "--secret-file", self.secret_file]
        before = tree(home)

        firmware("setup.sh", *args, "--dry-run", env=self.env)
        self.assertEqual(tree(home), before)
        for extra, said in ((["--skip", "pipewire"], "needs the speakerd sink"),
                            (["--route", "plugin-host"], "--route plugin-host with the speakerd component")):
            refused = firmware("setup.sh", *args, *extra, env=self.env, check=False)
            self.assertNotEqual(refused.returncode, 0, refused.stdout)
            self.assertIn(said, refused.stdout)
            self.assertEqual(tree(home), before)

        result = firmware("setup.sh", *args, env=self.env).stdout
        order = [result.index(f"==> {c}:") for c in ("pipewire", "snapcast", "speakerd", "ampd")]
        self.assertEqual(order, sorted(order), result)
        self.assertTrue((home / ".config/pipewire/pipewire.conf.d/60-speakerd-sink.conf").exists())
        self.assertIn('target.object = "speakerd"', (home / ".config/pipewire/client-rt.conf").read_text())
        self.assertTrue((home / ".local/lib/speakerd/current").is_symlink())

    def test_the_repository_conf_holds_the_sink(self):
        """With the repository's pipewire.conf (which defines the speakerd sink) as the user's,
        never two speakerd nodes: sink and install write no drop-in and remove one that is
        there, snapcast-setup.sh --route speakerd and setup.sh accept the sink in pipewire.conf,
        and uninstall says the sink stays (a null sink) and leaves pipewire.conf alone."""
        home = self.fresh_home("repository-conf")
        conf = home / ".config/pipewire/pipewire.conf"
        dropin = home / ".config/pipewire/pipewire.conf.d/60-speakerd-sink.conf"
        dropin.parent.mkdir(parents=True)
        shutil.copy(FIRMWARE / "pipewire/pipewire.conf", conf)
        shutil.copy(FIRMWARE / "pipewire/pipewire.conf.d/60-speakerd-sink.conf", dropin)
        sink = ("speakerd-setup.sh", "sink", "--home", home, "--no-systemd")

        before = snapshot(home)
        dry = firmware(*sink, "--dry-run", env=self.env).stdout
        self.assertIn(f"(dry-run) would remove {dropin}: {conf}, the repository's, defines the speakerd sink", dry)
        self.assertEqual(snapshot(home), before)
        out = firmware(*sink, env=self.env).stdout
        self.assertIn(f"Removed {dropin}: {conf}, the repository's, defines the speakerd sink", out)
        self.assertFalse(dropin.exists())
        before = snapshot(home)
        out = firmware(*sink, env=self.env).stdout
        self.assertIn(f"The speakerd sink is configured in {conf} (the repository's pipewire.conf): no drop-in", out)
        self.assertEqual(snapshot(home), before)

        # The route into speakerd finds the sink in pipewire.conf.
        routed = firmware("snapcast-setup.sh", "--mode", "target", "--routing-only", "--route", "speakerd",
                          "--client-home", home, env=self.env)
        self.assertIn(f"The speakerd sink is defined in {conf}", routed.stdout)
        crt = home / ".config/pipewire/client-rt.conf"
        self.assertIn('target.object = "speakerd"', crt.read_text())
        # setup.sh's snapcast component alone: the sink is configured (in pipewire.conf).
        unit = home / ".config/systemd/user/snapclient.service"
        unit.parent.mkdir(parents=True)
        unit.write_text("[Service]\nEnvironment=PIPEWIRE_CONFIG_NAME=client-rt.conf\nExecStart=/usr/bin/snapclient\n")
        accepted = firmware("setup.sh", "--home", home, "--no-systemd", "--only", "snapcast", "--dry-run",
                            env=self.env, check=False)
        self.assertEqual(accepted.returncode, 0, accepted.stdout)
        self.assertNotIn("needs the speakerd sink", accepted.stdout)

        # install: no drop-in, and one put back is removed.
        shutil.copy(FIRMWARE / "pipewire/pipewire.conf.d/60-speakerd-sink.conf", dropin)
        result = self.install(home, "--role", "left")
        self.assertIn(f"Removed {dropin}", result.stdout)
        self.assertFalse(dropin.exists())
        result = self.install(home, "--role", "left")
        self.assertIn("(the repository's pipewire.conf): no drop-in", result.stdout)
        self.assertFalse(dropin.exists())
        status = firmware("speakerd-setup.sh", "status", "--home", home, "--no-systemd", env=self.env).stdout
        self.assertIn(f"sink: {conf} (the repository's pipewire.conf)", status)

        # uninstall: the sink stays in pipewire.conf, said so; the routing from before goes back.
        repo = (FIRMWARE / "pipewire/pipewire.conf").read_bytes()
        result = firmware("speakerd-setup.sh", "uninstall", "--home", home, "--no-systemd", env=self.env)
        self.assertIn(f"the speakerd sink stays: it is in {conf} (the repository's pipewire.conf), a null sink, "
                      "so what plays into it is silent", result.stdout)
        self.assertEqual(conf.read_bytes(), repo)
        self.assertFalse(dropin.exists())
        self.assertFalse((home / ".local/lib/speakerd").exists())
        self.assertNotIn('target.object = "speakerd"', crt.read_text())


def pipewire_conf_rates(text):
    """The rates a pipewire.conf sets, read here, not with setup.sh's reader: (default.clock.rate,
    default.clock.allowed-rates or None, [{factory.name, node.name, audio.rate}] of each object
    that sets audio.rate). Commented lines are skipped."""
    graph, allowed, objects, current = None, None, [], None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if re.match(r"\{?\s*factory\s*=", line):
            current = {"factory.name": None, "node.name": None, "audio.rate": None}
            objects.append(current)
        for key in ("factory.name", "node.name", "audio.rate"):
            m = re.match(re.escape(key) + r'\s*=\s*"?([^"\s]+)"?', line)
            if m and current is not None:
                current[key] = m.group(1)
        m = re.match(r"default\.clock\.rate\s*=\s*(\d+)", line)
        if m:
            graph = int(m.group(1))
        m = re.match(r"default\.clock\.allowed-rates\s*=\s*\[([^\]]*)\]", line)
        if m:
            allowed = [int(x) for x in m.group(1).split()]
    return graph, allowed, [o for o in objects if o["audio.rate"] is not None]


def snapshot(root):
    """Every file under `root` with its modification time and content: what a run that writes
    nothing leaves as it was."""
    return {str(p.relative_to(root)): (p.stat().st_mtime_ns, p.read_bytes())
            for p in sorted(Path(root).rglob("*")) if p.is_file()}


def changing(calls):
    """The calls of FAKE_CLIENT_TOOLS that change something: all but systemctl show, loginctl
    show-user, dpkg-query, getent and snapclient's --help and --version."""
    def reads(call):
        words = call.split()
        return (words[0] in ("snapclient", "dpkg-query", "getent") or words[:2] == ["loginctl", "show-user"]
                or (words[0] == "systemctl" and "show" in words))
    return [call for call in calls if not reads(call)]


class SnapcastTarget(Setup):
    """snapcast-setup.sh --mode target (the client on a board) through stand-ins for what it calls
    there: it checks before it changes, and a second run with the same options changes nothing."""

    SERVER = "192.0.2.10"

    def client(self, name, help=HELP_WITH_PIPEWIRE, version="0.34.0", mark="", linger="yes", running=True):
        """The stand-ins, and a home with the speakerd sink configured and a person's own
        client-rt.conf. With `running`, snapclient's user unit is enabled and has run since 2020."""
        d = self.tmp / f"client-{name}"
        shutil.rmtree(d, ignore_errors=True)
        for tool in ("systemctl", "loginctl", "apt-get", "curl", "dpkg", "dpkg-query", "getent"):
            write_script(d / "bin" / tool, FAKE_CLIENT_TOOLS)
        write_script(d / "bin/snapclient", FAKE_SNAPCLIENT.replace("# MARK", mark))
        (d / "help").write_text(help)
        (d / "version").write_text(version)
        (d / "help-installed").write_text(HELP_WITH_PIPEWIRE)
        (d / "version-installed").write_text("0.34.0")
        (d / "linger").write_text(linger)
        if running:
            (d / "active").write_text("active")
            (d / "enabled").write_text("enabled")
            (d / "started").write_text("Mon 2020-01-06 10:00:00 UTC")
        home = self.fresh_home(f"home-client-{name}")
        firmware("speakerd-setup.sh", "sink", "--home", home, "--no-systemd", env=self.env)
        (home / ".config/pipewire/client-rt.conf").write_text(PERSONS_ROUTING)
        return d, home, dict(self.env, FAKE_CLIENT=str(d), PATH=f"{d / 'bin'}:{os.environ['PATH']}")

    def target(self, d, home, env, *args, server=SERVER, route="speakerd", option="--server-ip", check=True):
        """One --mode target run; returns its output and the calls it made (with check=False, the
        completed process instead of its output)."""
        (d / "calls").write_text("")
        result = firmware("snapcast-setup.sh", "--mode", "target", "--client-home", home, option, server,
                          "--route", route, *args, env=env, check=check)
        return (result.stdout if check else result), (d / "calls").read_text().splitlines()

    def test_the_server_by_name(self):
        """--server takes a name, which lands verbatim in the unit's --host; --server-ip is the same
        option. A .local name that does not resolve installs libnss-mdns (only when missing) and is
        checked again; one that still does not is refused, with nothing written."""
        d, home, env = self.client("by-name")
        unit = home / ".config/systemd/user/snapclient.service"
        name = "example.local"           # the server, known to the stand-in mDNS
        missing = "my-board.local"       # a name nothing answers for
        (d / "mdns-names").write_text(name + "\n")

        # The name does not resolve without nss-mdns: libnss-mdns is installed, then it does.
        out, calls = self.target(d, home, env, server=name, option="--server")
        print("\n  " + "\n  ".join(line for line in out.splitlines() if name in line))
        self.assertEqual([c for c in calls if c.startswith("apt-get install")], ["apt-get install -y libnss-mdns"], out)
        self.assertIn(f"The server {name} resolves here (192.0.2.20)", out)
        self.assertIn(f"ExecStart=/usr/bin/snapclient --host {name} --player pipewire --latency 0\n", unit.read_text())
        self.assertIn("changed: libnss-mdns installed", out)

        # Again: it resolves, so no apt, nothing written, nothing restarted.
        before = snapshot(home)
        out, calls = self.target(d, home, env, server=name, option="--server")
        self.assertEqual([c for c in calls if c.split()[0] == "apt-get"], [], out)
        self.assertEqual(changing(calls), [], out)
        self.assertIn(f"server {name}, route speakerd: already set up, nothing changed", out)
        self.assertEqual(snapshot(home), before)

        # --server-ip is --server: the same name through it changes nothing either.
        out, calls = self.target(d, home, env, server=name, option="--server-ip")
        self.assertEqual(changing(calls), [], out)
        self.assertEqual(snapshot(home), before)

        # A name that is not a .local one is not looked up (DNS, /etc/hosts: snapclient's own
        # business), and an address through --server-ip lands verbatim too.
        for option, server in (("--server", "snapserver.example.com"), ("--server-ip", "192.0.2.11")):
            with self.subTest(option=option, server=server):
                out, calls = self.target(d, home, env, server=server, option=option)
                self.assertEqual([c for c in calls if c.split()[0] in ("apt-get", "getent")], [], out)
                self.assertIn(f"ExecStart=/usr/bin/snapclient --host {server} --player pipewire --latency 0\n",
                              unit.read_text())

        # A .local name that does not resolve with libnss-mdns installed: refused, the unit
        # untouched, nothing installed, snapclient not restarted.
        before = snapshot(home)
        result, calls = self.target(d, home, env, server=missing, option="--server", check=False)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(f"The server {missing} does not resolve here (getent hosts), and libnss-mdns is installed: "
                      "pass --server <the server's address>, or fix mDNS here", result.stdout)
        self.assertEqual(changing(calls), [], result.stdout)
        self.assertEqual(snapshot(home), before)

        # Nor one that still does not resolve once libnss-mdns is installed.
        (d / "nss-mdns").unlink()
        result, calls = self.target(d, home, env, server=missing, option="--server", check=False)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(f"The server {missing} still does not resolve here after installing libnss-mdns", result.stdout)
        self.assertEqual(snapshot(home), before)

        # With --dry-run nothing is installed; said.
        (d / "nss-mdns").unlink()
        out, calls = self.target(d, home, env, "--dry-run", server=name, option="--server")
        self.assertIn("(dry-run) apt-get install -y libnss-mdns", out)
        self.assertEqual([c for c in calls if c.split()[0] == "apt-get"], [], out)
        self.assertFalse((d / "nss-mdns").exists())

        # Something a shell or systemd would split is no server.
        result, _ = self.target(d, home, env, server="a b", option="--server", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--server 'a b': a host name or an address", result.stdout)

    def test_a_second_run_changes_nothing(self):
        d, home, env = self.client("again")
        crt = home / ".config/pipewire/client-rt.conf"
        orig = home / ".config/pipewire/client-rt.conf.orig"
        unit = home / ".config/systemd/user/snapclient.service"

        # The board as the person found it: snapclient 0.34.0 with the pipewire player, running.
        out, calls = self.target(d, home, env)
        self.assertIn("snapclient 0.34.0 has the pipewire player (its --help: -p, --player arg (=alsa) "
                      "alsa|pipewire|file[:<options>|?]): nothing to install", out)
        self.assertEqual([c for c in calls if c.split()[0] in ("curl", "apt-get", "dpkg")], [], out)
        # client-rt.conf and the unit are snapclient's: PipeWire is not restarted for them.
        self.assertEqual([c for c in calls if "pipewire" in c or "wireplumber" in c], [], out)
        self.assertEqual(changing(calls), ["systemctl --user daemon-reload", "systemctl --user restart snapclient.service"],
                         out)
        self.assertEqual(orig.read_text(), PERSONS_ROUTING)
        self.assertIn('target.object = "speakerd"', crt.read_text())
        self.assertIn(f"--host {self.SERVER} --player pipewire", unit.read_text())
        self.assertIn("Environment=PIPEWIRE_CONFIG_NAME=client-rt.conf", unit.read_text())

        # Again, the same: nothing downloaded, no apt, nothing written, nothing restarted; said so.
        before = snapshot(home)
        out, calls = self.target(d, home, env)
        print("\n  " + "\n  ".join(out.splitlines()[-3:]))
        self.assertIn(f"snapclient 0.34.0 with the pipewire player, server {self.SERVER}, route speakerd: already set up, "
                      "nothing changed", out)
        self.assertEqual(changing(calls), [], out)
        for said in ("Wrote ", "Kept ", "Backed up", "restarted", "Enabled"):
            self.assertNotIn(said, out)
        self.assertEqual(snapshot(home), before)

        # Another server: the unit alone, and snapclient restarted for it.
        out, calls = self.target(d, home, env, server="192.0.2.11")
        self.assertIn(f"Wrote {unit}", out)
        self.assertNotIn(f"Wrote {crt}", out)
        self.assertEqual(changing(calls), ["systemctl --user daemon-reload", "systemctl --user restart snapclient.service"],
                         out)
        self.assertIn("server 192.0.2.11, route speakerd: changed: the snapclient unit (server 192.0.2.11); "
                      "snapclient restarted", out)

        # Another route: client-rt.conf alone; the person's .orig survives every run.
        out, calls = self.target(d, home, env, server="192.0.2.11", route="plugin-host")
        self.assertIn(f"Wrote {crt}", out)
        self.assertNotIn(f"Wrote {unit}", out)
        self.assertEqual(changing(calls), ["systemctl --user restart snapclient.service"], out)
        self.assertIn('target.object = "Ap.cap"', crt.read_text())
        self.assertEqual(orig.read_text(), PERSONS_ROUTING)
        self.target(d, home, env, server="192.0.2.11")
        self.assertEqual(orig.read_text(), PERSONS_ROUTING, "a later run overwrote the person's client-rt.conf.orig")

        # A snapclient that started before what it reads was written (a run with --no-restart, a
        # person's edit), or that does not run, is restarted; nothing is written.
        for state, said in (("started", "it started before snapclient.service was last written"),
                            ("active", "it does not run")):
            (d / "active").write_text("active")
            (d / "started").write_text("Mon 2020-01-06 10:00:00 UTC" if state == "started" else
                                       subprocess.run(["date", "-u", "+%a %Y-%m-%d %H:%M:%S UTC"], capture_output=True,
                                                      text=True).stdout.strip())
            if state == "active":
                (d / "active").write_text("inactive")
            before = snapshot(home)
            out, calls = self.target(d, home, env, server="192.0.2.11")
            self.assertEqual(changing(calls), ["systemctl --user restart snapclient.service"], out)
            self.assertIn(said, out)
            self.assertEqual(snapshot(home), before)

    def test_snapclient_is_installed_once(self):
        """A snapclient without the pipewire player: the with-pipewire .deb is downloaded and
        installed, the packaged system service disabled and linger enabled, once."""
        d, home, env = self.client("install", help=HELP_WITHOUT_PIPEWIRE, version="0.26.0", linger="no", running=False)
        (d / "system-active").write_text("active")
        (d / "system-enabled").write_text("enabled")
        out, calls = self.target(d, home, env)
        print("\n  " + "\n  ".join(line for line in out.splitlines() if "snapclient" in line and "pipewire player" in line))
        self.assertIn("snapclient 0.26.0 lacks the pipewire player (its --help: -p, --player arg (=alsa) "
                      "alsa|file[:<options>|?])", out)
        self.assertIn("Installed snapclient 0.34.0 with the pipewire player (its --help: -p, --player arg (=alsa) "
                      "alsa|pipewire|file[:<options>|?])", out)
        for want in ("curl", "apt-get", "dpkg", "systemctl disable --now snapclient.service", "loginctl enable-linger",
                     "systemctl --user enable snapclient.service", "systemctl --user restart snapclient.service"):
            self.assertTrue([c for c in calls if c.startswith(want)], f"no {want}:\n{out}")

        before = snapshot(home)
        out, calls = self.target(d, home, env)
        self.assertEqual(changing(calls), [], out)
        self.assertIn(f"snapclient 0.34.0 with the pipewire player, server {self.SERVER}, route speakerd: already set up, "
                      "nothing changed", out)
        self.assertEqual(snapshot(home), before)

    def test_how_the_player_is_found(self):
        """Its --help's --player line decides; without one, its program's text (or its libraries)."""
        cases = (("no-player-line", "Usage: snapclient [options...]\n  -h, --help  this help\n",
                  "# a build with pipewire_player", "has the pipewire player (its --help names no --player; its "
                  "program names pipewire_player)"),
                 ("help-wins", HELP_WITHOUT_PIPEWIRE, "# a build with pipewire_player",
                  "lacks the pipewire player (its --help: -p, --player arg (=alsa) alsa|file[:<options>|?])"),
                 ("nothing", "Usage: snapclient [options...]\n", "",
                  "lacks the pipewire player (its --help names no --player, it does not link libpipewire-0.3 and its "
                  "program does not name pipewire_player)"))
        for name, help, mark, said in cases:
            with self.subTest(name):
                d, home, env = self.client(f"player-{name}", help=help, mark=mark)
                out, calls = self.target(d, home, env, "--no-install")
                self.assertIn(said, out)
                self.assertEqual([c for c in calls if c.split()[0] in ("curl", "apt-get")], [], out)

    def test_routing_only_twice_changes_nothing(self):
        home = self.fresh_home("routing-twice")
        unit = home / ".config/systemd/user/snapclient.service"
        unit.parent.mkdir(parents=True)
        unit.write_text("[Service]\nEnvironment=PIPEWIRE_CONFIG_NAME=client-rt.conf\nExecStart=/usr/bin/snapclient\n")
        firmware("speakerd-setup.sh", "sink", "--home", home, "--no-systemd", env=self.env)
        crt = home / ".config/pipewire/client-rt.conf"
        crt.write_text(PERSONS_ROUTING)

        def route(name):
            return firmware("snapcast-setup.sh", "--mode", "target", "--routing-only", "--route", name,
                            "--client-home", home, env=self.env).stdout

        first = route("speakerd")
        self.assertIn(f"Wrote {crt}", first)
        before = snapshot(home)
        again = route("speakerd")
        self.assertIn("snapclient's routing into speakerd (client-rt.conf, --route speakerd): already set up, nothing "
                      "changed", again)
        self.assertNotIn("Wrote ", again)
        self.assertEqual(snapshot(home), before)
        route("plugin-host")
        route("speakerd")
        self.assertEqual((home / ".config/pipewire/client-rt.conf.orig").read_text(), PERSONS_ROUTING)


class Systemd(Setup):
    """The systemd path, with stand-ins for systemctl, journalctl and loginctl."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bundle2 = cls.make_bundle("two", ["loudness:headless", "loudness:complete"])

    def systemd(self, name, journal=(), active="active", fail=()):
        d = self.tmp / f"systemd-{name}"
        shutil.rmtree(d, ignore_errors=True)
        for tool in ("systemctl", "journalctl", "loginctl"):
            write_script(d / "bin" / tool, FAKE_SYSTEMD)
        (d / "journal").write_text("".join(line + "\n" for line in journal))
        (d / "active").write_text(active)
        (d / "fail").write_text("".join(line + "\n" for line in fail))
        env = dict(self.env, FAKE_SYSTEMD=str(d), PATH=f"{d / 'bin'}:{os.environ['PATH']}",
                   SPEAKERD_START_WAIT="1", FAKE_PLUGIN_HOST=TEST_PLUGIN_HOST)
        return d, env

    def test_install_checks_what_speakerd_says_after_its_start(self):
        start = "speakerd-run: starting speakerd x from layer current (test)"
        cases = {
            "the limits": ([start, LIMITS], "active", None),
            "no line": ([start], "active", "has said nothing about its limits"),
            "inactive": ([start, LIMITS], "inactive", "speakerd is not running after its start"),
            "fail-safe": ([start, LIMITS + " (no limits in device.json: the fail-safe)"], "active",
                          "does not play device.json's limits"),
            "FAILED": ([start, "speakerd: device.json: FAILED: limits: no; using the defaults", LIMITS], "active",
                       "could not read device.json"),
            "own sink": ([start, LIMITS, "speakerd: *** NO INPUT SINK CONFIGURED: PipeWire had no node 'speakerd' ***"],
                         "active", "the drop-in is not loaded"),
        }
        for name, (journal, active, said) in cases.items():
            with self.subTest(name):
                d, env = self.systemd(name.replace(" ", "-"), journal, active)
                home = self.fresh_home(f"home-{name.replace(' ', '-')}")
                result = firmware("speakerd-setup.sh", "install", "--bundle", self.bundle, "--home", home,
                                  "--role", "left", "--amplifier", "amplifier", env=env, check=False)
                calls = (d / "calls").read_text()
                self.assertIn("systemctl --user restart speakerd.service", calls)
                if said is None:
                    self.assertEqual(result.returncode, 0, result.stdout)
                    self.assertIn("device: limits: output at -20 dB, tweeter muted", result.stdout)
                else:
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertIn(said, result.stdout)

    def test_limits_and_rollback_restart_a_running_speakerd(self):
        """A restart of a speakerd that runs: the old instance's 'speakerd ended' line (its
        ExecStopPost) is in the window before the new start, and is not the new one's failure."""
        start = "speakerd-run: starting speakerd x from layer current (test)"
        d, env = self.systemd("running", [start, LIMITS])
        home = self.fresh_home("home-running")
        firmware("speakerd-setup.sh", "install", "--bundle", self.bundle, "--home", home, "--role", "left",
                 "--amplifier", "amplifier", env=env)
        firmware("speakerd-setup.sh", "install", "--bundle", self.bundle2, "--home", home, env=env)
        (d / "running").touch()
        device_json = home / ".local/state/speakerd/device.json"

        (d / "journal").write_text(f"{start}\nspeakerd: device: limits: output at -30 dB, tweeter muted\n")
        result = firmware("speakerd-setup.sh", "limits", "--gain-db", "-30", "--tweeter-limit", "muted", "--home", home,
                          env=env, check=False)
        print(f"\n  limits: {(d / 'before').read_text().strip()}")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("speakerd ended", (d / "before").read_text())
        self.assertEqual(json.loads(device_json.read_text())["limits"], {"gain_db": -30.0, "tweeter": "muted"})

        # rollback keeps device.json's limits, as an install does.
        device = json.loads(device_json.read_text())
        device_json.write_text(json.dumps(dict(device, limits={"gain_db": -6.0, "tweeter": "on"})))
        (d / "before").unlink()
        (d / "journal").write_text(f"{start}\n{LIMITS}\n")
        result = firmware("speakerd-setup.sh", "rollback", "--home", home, env=env, check=False)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("speakerd ended", (d / "before").read_text())
        self.assertEqual(json.loads(device_json.read_text())["limits"], {"gain_db": -6.0, "tweeter": "on"})

        # A crash after the new start still fails both.
        (d / "journal").write_text(f"{start}\n{LIMITS}\nspeakerd-run: speakerd ended: crash (status 134, signal ABRT); the next start uses current\n")
        for action in (["limits", "--gain-db", "-25", "--tweeter-limit", "muted"], ["rollback"]):
            with self.subTest(action[0]):
                result = firmware("speakerd-setup.sh", *action, "--home", home, env=env, check=False)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("speakerd is not running after its start", result.stdout)

    def test_a_reinstall_restarts_nothing(self):
        """The same bundle and options again, through speakerd-setup.sh and through setup.sh: the
        drop-in is the same, so PipeWire is not restarted; nothing speakerd reads changed, so it is
        not restarted either; no file of the home is written."""
        d, env = self.systemd("reinstall", ["speakerd-run: starting speakerd x from layer current (test)", LIMITS])
        home = self.fresh_home("home-reinstall")
        install = ("speakerd-setup.sh", "install", "--bundle", self.bundle, "--home", home, "--role", "left",
                   "--amplifier", "amplifier")
        firmware(*install, env=env)
        calls = (d / "calls").read_text()
        self.assertIn("systemctl --user restart pipewire.service wireplumber.service", calls)
        self.assertIn("systemctl --user restart speakerd.service", calls)
        for again in (install, ("setup.sh", "--only", "pipewire,speakerd", "--bundle", self.bundle, "--home", home,
                                "--role", "left", "--amplifier", "amplifier")):
            with self.subTest(again[0]):
                (d / "calls").write_text("")
                before = snapshot(home)
                result = firmware(*again, env=env)
                calls = (d / "calls").read_text().splitlines()
                self.assertEqual([c for c in calls if re.search(r" (restart|daemon-reload|stop|start)( |$)", c)], [],
                                 result.stdout)
                self.assertIn("Nothing changed: speakerd keeps running", result.stdout)
                self.assertEqual(snapshot(home), before)

    def test_setup_stops_on_a_failed_pipewire_restart(self):
        d, env = self.systemd("restart-fails", fail=["restart pipewire.service"])
        home = self.fresh_home("home-restart-fails")
        unit = home / ".config/systemd/user/snapclient.service"
        unit.parent.mkdir(parents=True)
        unit.write_text("[Service]\nEnvironment=PIPEWIRE_CONFIG_NAME=client-rt.conf\n")
        result = firmware("setup.sh", "--home", home, "--bundle", self.bundle, "--role", "left",
                          "--amplifier", "amplifier", "--secret-file", self.secret_file, env=env, check=False)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("could not restart PipeWire", result.stdout)
        self.assertFalse((home / ".config/pipewire/client-rt.conf").exists(), "snapclient was routed all the same")
        self.assertFalse((home / ".local/lib/speakerd").exists())

    def test_setup_stops_the_plugin_host_before_pipewire_restarts(self):
        # Every component that restarts PipeWire into the speakerd route: the whole architecture,
        # and the pipewire component alone.
        for name, only in (("all", []), ("pipewire", ["--only", "pipewire"])):
            with self.subTest(name):
                # The whole architecture ends with ampd, which must say it listens.
                d, env = self.systemd(f"plugin-host-first-{name}", journal=["speakerd-run: starting speakerd", LIMITS,
                                                                            AMPD_LISTENING])
                home = self.fresh_home(f"home-plugin-host-first-{name}")
                unit = home / ".config/systemd/user/snapclient.service"
                unit.parent.mkdir(parents=True)
                unit.write_text("[Service]\nEnvironment=PIPEWIRE_CONFIG_NAME=client-rt.conf\n")
                host = self.plugin_host()
                args = ["--home", home, "--bundle", self.bundle, "--role", "left", "--amplifier", "amplifier",
                        "--secret-file", self.secret_file, *only]
                refused = firmware("setup.sh", *args, env=env, check=False)
                self.assertNotEqual(refused.returncode, 0, refused.stdout)
                self.assertIn(f"{TEST_PLUGIN_HOST} runs (pid {host.pid})", refused.stdout)
                calls = (d / "calls").read_text() if (d / "calls").exists() else ""
                self.assertNotIn("pipewire restarted", calls)
                self.assertIsNone(host.poll())

                result = firmware("setup.sh", *args, "--stop-plugin-host", env=env, check=False)
                calls = (d / "calls").read_text()
                restarts = [line for line in calls.splitlines() if line.startswith("pipewire restarted")]
                print(f"\n  {name}: {restarts}")
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertIsNotNone(host.poll())
                self.assertTrue(restarts, calls)
                self.assertTrue(all(line.endswith("runs: no") for line in restarts), calls)

    def test_setup_installs_the_base_conf_at_its_rate(self):
        """setup.sh --only pipewire --base-conf: the repository's pipewire.conf, the graph (its
        rate and allowed rates) and the amplifier at 48000 Hz or at --rate's; the drop-ins it
        contains (20-resample.conf, 60-speakerd-sink.conf) removed, and no drop-in written;
        nothing restarts with --no-restart (the stand-in systemctl records every call)."""
        repo = (FIRMWARE / "pipewire/pipewire.conf").read_text()

        def rates(text):
            graph, allowed, objects = pipewire_conf_rates(text)
            amplifier = [o["audio.rate"] for o in objects if o["factory.name"] == "api.alsa.pcm.sink"]
            return graph, allowed, amplifier, len(objects)

        # The repository's file itself (the person's decisions): the graph and the amplifier
        # (bpi-alsa-playback-pcm) at 48000; no other object sets a rate.
        self.assertEqual(rates(repo), (48000, [48000], ["48000"], 1))
        amp_line = r"the graph and bpi-alsa-playback-pcm at {} Hz$"
        dropins = ("20-resample.conf", "60-speakerd-sink.conf")

        d, env = self.systemd("base-conf")
        # PipeWire says it runs at 44100 now (a stand-in pw-metadata).
        write_script(d / "bin/pw-metadata", "#!/bin/sh\necho 'Found \"settings\" metadata 32'\n"
                     "echo \"update: id:0 key:'clock.rate' value:'44100' type:''\"\n")
        boards_own = "# the board's own pipewire.conf\ncontext.properties = { default.clock.rate = 48000 }\n"
        for name, folder, own in (("no own file", "home-base-conf-new", None),
                                  ("the board's own", "home-base-conf-own", boards_own)):
            with self.subTest(name):
                home = self.fresh_home(folder)
                conf = home / ".config/pipewire/pipewire.conf"
                conf.parent.mkdir(parents=True)
                if own:
                    conf.write_text(own)
                # The drop-ins of the board measured, which the file now contains.
                (conf.parent / "pipewire.conf.d").mkdir()
                (conf.parent / "pipewire.conf.d/20-resample.conf").write_text(
                    "context.properties = {\n    resample.quality = 15\n}\n")
                shutil.copy(FIRMWARE / "pipewire/pipewire.conf.d/60-speakerd-sink.conf", conf.parent / "pipewire.conf.d")
                args = ("setup.sh", "--home", home, "--only", "pipewire", "--base-conf", "--no-restart")

                before = snapshot(home)
                dry = firmware(*args, "--dry-run", env=env).stdout
                self.assertRegex(dry, re.compile(r"\(dry-run\) would install firmware/pipewire/pipewire\.conf as \S+: "
                                                 + amp_line.format(48000), re.M))
                for name in dropins:
                    self.assertIn(f"(dry-run) would remove {conf.parent}/pipewire.conf.d/{name}: the repository's "
                                  "pipewire.conf has what it sets", dry)
                self.assertEqual(snapshot(home), before)

                for rate, expected in ((None, 48000), ("44100", 44100), ("48000", 48000)):
                    out = firmware(*args, *(("--rate", rate) if rate else ()), env=env).stdout
                    text = conf.read_text()
                    self.assertEqual(rates(text), (expected, [expected], [str(expected)], 1), out)
                    # Only the graph's rate, its allowed rates and the amplifier's differ from the
                    # repository's file.
                    self.assertEqual(len(text.splitlines()), len(repo.splitlines()))
                    changed = [(a, b) for a, b in zip(repo.splitlines(), text.splitlines()) if a != b]
                    self.assertEqual(len(changed), 0 if expected == 48000 else 3, changed)
                    for a, b in changed:
                        self.assertEqual(a.replace("48000", str(expected)), b)
                    if expected == 48000:
                        self.assertEqual(text, repo)
                    self.assertRegex(out, re.compile(r"Wrote \S+pipewire\.conf: " + amp_line.format(expected), re.M))
                    if rate is None:
                        for name in dropins:
                            self.assertIn(f"Removed {conf.parent}/pipewire.conf.d/{name}: the repository's "
                                          "pipewire.conf has what it sets", out)
                    # The sink is the file's: no drop-in, neither removed again nor written.
                    self.assertEqual(sorted(p.name for p in (conf.parent / "pipewire.conf.d").iterdir()), [], out)
                    self.assertIn("The speakerd sink is configured in", out)
                    if expected != 48000:
                        self.assertIn("filters, designed for 48000 Hz, run with every frequency scaled by", out)
                    if expected != 44100:
                        self.assertIn("PipeWire runs at 44100 Hz now; with this pipewire.conf it runs at "
                                      f"{expected} Hz once it restarts", out)
                    else:
                        self.assertNotIn("PipeWire runs at 44100 Hz now", out)
                    self.assertIn("PipeWire needs a restart for the new configuration (not done", out)

                # The same again writes nothing.
                before = snapshot(home)
                out = firmware(*args, env=env).stdout
                self.assertIn("pipewire.conf is the repository's already: the graph and", out)
                self.assertEqual(snapshot(home), before)
                orig = conf.with_name("pipewire.conf.orig")
                if own:
                    self.assertEqual(orig.read_text(), own)
                else:
                    self.assertFalse(orig.exists(), "the repository's pipewire.conf was kept as the board's own")
        calls = (d / "calls").read_text() if (d / "calls").exists() else ""
        self.assertEqual([c for c in calls.splitlines() if re.search(r" (restart|try-restart|reload|stop|start)( |$)", c)],
                         [], calls)

        # A pipewire.conf whose amplifier runs at another rate than its graph is refused, writing
        # nothing; --rate sets both. A copy of setup.sh with such a file beside it, routed to the
        # plugin host so that it needs no speakerd sink (and no speakerd-setup.sh).
        copy = self.tmp / "firmware-mixed-rates"
        shutil.rmtree(copy, ignore_errors=True)
        (copy / "pipewire").mkdir(parents=True)
        for script in ("setup.sh", "install-lib.sh"):
            shutil.copy2(FIRMWARE / script, copy / script)
        at = repo.index("api.alsa.pcm.sink")
        at = repo.index("audio.rate             = 48000", at)
        mixed = repo[:at] + "audio.rate             = 44100" + repo[at + len("audio.rate             = 48000"):]
        (copy / "pipewire/pipewire.conf").write_text(mixed)
        home = self.fresh_home("home-base-conf-mixed")
        home.mkdir()

        def run(*extra):
            return subprocess.run(["bash", str(copy / "setup.sh"), "--home", str(home), "--only", "pipewire",
                                   "--route", "plugin-host", "--base-conf", "--no-restart", "--no-systemd", *extra],
                                  env={**os.environ, **TEST_ENV, **self.env}, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=60)
        refused = run()
        self.assertNotEqual(refused.returncode, 0, refused.stdout)
        self.assertRegex(refused.stdout, r"would run the graph at 48000 Hz but the amplifier \S+ at 44100 Hz")
        self.assertEqual(snapshot(home), {})
        accepted = run("--rate", "48000")
        self.assertEqual(accepted.returncode, 0, accepted.stdout)
        self.assertEqual((home / ".config/pipewire/pipewire.conf").read_text(), repo)

        # --rate is --base-conf's, and --base-conf the pipewire component's.
        for extra, said in ((("--only", "pipewire", "--rate", "44100"), "--rate needs --base-conf"),
                            (("--only", "speakerd", "--base-conf"), "belong to the pipewire component")):
            refused = firmware("setup.sh", "--home", self.fresh_home("home-base-conf-refused"), *extra,
                               env=self.env, check=False)
            self.assertNotEqual(refused.returncode, 0, refused.stdout)
            self.assertIn(said, refused.stdout)

class Remote(Setup):
    """The remote mode through a stand-in ssh, and the board script with --dry-run."""

    def board(self, name):
        board = self.tmp / f"board-{name}"
        shutil.rmtree(board, ignore_errors=True)
        (board / "home").mkdir(parents=True)
        (board / "hosts").touch()
        write_script(board / "bin/ssh", FAKE_SSH)
        if os.geteuid() == 0:
            # The board's side runs as nobody: it must reach its home, and nothing else of ours.
            shutil.chown(board / "home", 65534, 65534)
            os.chmod(self.tmp, 0o711)
            os.chmod(board, 0o711)
        env = dict(self.env, FAKE_BOARD=str(board), PATH=f"{board / 'bin'}:{os.environ['PATH']}",
                   BUILD_ROOT=str(self.root))
        return board, env

    def remote(self, env, action, *args, host="user@bpi-amp.invalid"):
        return firmware("speakerd-setup.sh", action, "--mode", "target-remote", "--target-host", host,
                        "--arch", "native", "--plugin", "loudness:headless", *args, env=env, check=False)

    def test_check_and_dry_run_on_the_board(self):
        board, env = self.board("check")
        result = self.remote(env, "check", "--role", "left", "--amplifier", "amplifier")
        print("\n" + result.stdout[-600:])
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("is good for this machine", result.stdout)
        self.assertIn("Nothing in the way of an install", result.stdout)
        self.assertEqual((board / "hosts").read_text().split(), ["user@bpi-amp.invalid"] * 3)

        result = self.remote(env, "install", "--role", "left", "--amplifier", "amplifier", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Dry run: nothing changed", result.stdout)
        self.assertEqual(list((board / "home/.cache").iterdir()), [], "the bundle was left on the board")
        self.assertFalse((board / "home/.local").exists())

    def test_every_argument_arrives_as_given(self):
        board, env = self.board("quoting")
        # Where the board's user could write, so that an argument run as a command would show.
        marker = board / "home" / "injected"
        given = f"/no such folder/session; touch {marker}"
        result = self.remote(env, "install", "--role", "left", "--amplifier", "amplifier",
                             "--import-filtergraph", given, "--dry-run")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(f"no .filtergraph at {given}", result.stdout)
        self.assertFalse(marker.exists(), "an argument ran as a command on the board")
        self.assertEqual(list((board / "home/.cache").iterdir()), [])

    def test_snapcast_remote_ships_the_route_check(self):
        """snapcast-setup.sh --mode target-remote: the board gets the script and what its target
        mode loads from beside it (speakerd/setup-helper.py) in a fresh mktemp -d folder, in the same
        layout; a --route speakerd run there finds the helper and checks the running PipeWire's
        speakerd sink with it, either way; the folder is gone afterwards; --dry-run says so and
        contacts nothing."""
        board, env = self.board("snapcast")
        # The board's sudo (the remote command runs the script under it) and its PipeWire.
        write_script(board / "bin/sudo",
                     '#!/bin/bash\nwhile [ $# -gt 0 ]; do case "$1" in -*) shift ;; *) break ;; esac; done\nexec "$@"\n')
        write_script(board / "bin/pw-dump", '#!/bin/bash\ncat "${FAKE_BOARD:?}/dump.json"\n')
        tmp = board / "tmp"          # the board's /tmp, where its mktemp -d makes the folder
        tmp.mkdir()
        home = board / "home/client"
        firmware("speakerd-setup.sh", "sink", "--home", home, "--no-systemd", env=self.env)
        crt = home / ".config/pipewire/client-rt.conf"
        crt.write_text(PERSONS_ROUTING)
        if os.geteuid() == 0:
            for path in (tmp, board / "home", *(board / "home").rglob("*")):
                shutil.chown(path, 65534, 65534)
        env = dict(env, TMPDIR=str(tmp))
        host = "user@bpi-amp.invalid"

        def dump(props):
            nodes = [{"id": 30, "type": "PipeWire:Interface:Node",
                      "info": {"props": dict({"node.name": "speakerd"}, **props)}}] if props is not None else []
            (board / "dump.json").write_text(json.dumps(nodes))

        def remote(*args):
            (board / "hosts").write_text("")
            return firmware("snapcast-setup.sh", "--mode", "target-remote", "--target-host", host,
                            "--server", "192.0.2.10", "--routing-only", "--route", "speakerd",
                            "--client-home", home, *args, env=env, check=False)

        # --dry-run: what it would copy and run, nothing contacted.
        result = remote("--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn(f"(dry-run) ssh {host} mktemp -d", result.stdout)
        self.assertIn("snapcast-setup.sh speakerd/setup-helper.py | ssh", result.stdout)
        self.assertIn(f"(dry-run) ssh -t {host} sudo bash DIR/snapcast-setup.sh --mode target", result.stdout)
        self.assertEqual((board / "hosts").read_text(), "", "--dry-run contacted the board")

        # The running PipeWire has the drop-in's sink: the helper, run on the board, says so.
        dump({"factory.name": "support.null-audio-sink", "media.class": "Audio/Sink",
              "priority.session": 1500, "priority.driver": 0})
        result = remote()
        print("\n  " + "\n  ".join(result.stdout.splitlines()[-6:]))
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("is missing", result.stdout)
        self.assertIn("The running PipeWire has the drop-in's speakerd sink", result.stdout)
        self.assertIn('target.object = "speakerd"', crt.read_text())
        self.assertEqual((board / "hosts").read_text().split(), [host] * 4)
        self.assertEqual(list(tmp.iterdir()), [], "the copy was left on the board")

        # Without it the check, made there all the same, refuses; the copy is removed all the same.
        crt.write_text(PERSONS_ROUTING)
        dump(None)
        result = remote()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("is missing", result.stdout)
        self.assertIn("--route speakerd: the running PipeWire has no speakerd sink", result.stdout)
        self.assertIn("Remote client setup failed on " + host, result.stdout)
        self.assertEqual(crt.read_text(), PERSONS_ROUTING)
        self.assertEqual(list(tmp.iterdir()), [], "the copy was left on the board")

    def test_the_remote_mode_has_the_lab_lock(self):
        """The installers' remote mode refuses, as tools/board.sh does, before it calls ssh, while a
        lab lock exists. There is no test-status gate (2026-11-02, the person's decision): with no
        lock it reaches the board from any checkout, tested or not, clean or not. A copy of
        firmware/ in a scratch folder stands for the checkout; the host is a .test name (reserved:
        nothing resolves it), reached only through the stand-in ssh."""
        board, env = self.board("gate")
        repo = self.tmp / "gate-repo"
        shutil.rmtree(repo, ignore_errors=True)
        shutil.copytree(FIRMWARE, repo / "firmware", ignore=shutil.ignore_patterns("local.mk", "localBuild"))
        host = "user@bpi-amp.test"

        def status():
            return firmware_at(repo, "speakerd-setup.sh", "status", "--mode", "target-remote", "--target-host", host,
                               env=env)

        (repo / ".claude").mkdir()
        (repo / ".claude/lab.lock").write_text("")
        result = status()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("the lab is locked", result.stdout)
        self.assertEqual((board / "hosts").read_text(), "", "ssh was called")
        (repo / ".claude/lab.lock").unlink()

        # Not a git checkout, nothing tested, a file nobody committed: it reaches the board.
        (repo / "firmware/edited").write_text("not committed\n")
        result = status()
        self.assertIn(host, (board / "hosts").read_text(), result.stdout)

    def test_the_hints_name_the_installed_copy(self):
        """setup.sh --mode target-remote --only speakerd, as the person ran it: the closing hints
        name ~/.local/lib/speakerd/current/setup/, not the bundle's copy in ~/.cache, which is
        gone when the run ends. The board's side runs with stand-ins for systemctl, journalctl and
        loginctl, and for getent, whose passwd gives the stand-in board's home."""
        board, env = self.board("hints")
        write_script(board / "bin/getent", '#!/bin/bash\nif [ "$1" = passwd ]; then\n'
                                           '    echo "$(id -un):x:$(id -u):$(id -g)::${FAKE_BOARD:?}/home:/bin/bash"; exit 0\n'
                                           'fi\nexec /usr/bin/getent "$@"\n')
        for tool in ("systemctl", "journalctl", "loginctl"):
            write_script(board / "bin" / tool, FAKE_SYSTEMD)
        state = board / "systemd"
        state.mkdir()
        if os.geteuid() == 0:
            shutil.chown(state, 65534, 65534)
        result = firmware("setup.sh", "--mode", "target-remote", "--target-host", "user@bpi-amp.invalid", "--only",
                          "speakerd", "--role", "left", "--amplifier", "amplifier", "--no-restart", "--arch", "native",
                          "--plugin", "loudness:headless", env=dict(env, FAKE_SYSTEMD=str(state)), check=False)
        self.assertEqual(result.returncode, 0, result.stdout)
        hints = [line.strip() for line in result.stdout.splitlines() if line.strip().startswith("- ")]
        print("\n  " + "\n  ".join(hints))
        installed = "~/.local/lib/speakerd/current/setup/speakerd-setup.sh"
        self.assertTrue(any(line.endswith(f": {installed} smoke") for line in hints), result.stdout)
        self.assertTrue(any(line.endswith(f": {installed} rollback") for line in hints), result.stdout)
        self.assertFalse([line for line in hints if ".cache" in line], result.stdout)
        self.assertTrue((board / "home/.local/lib/speakerd/current/setup/speakerd-setup.sh").is_file())
        self.assertEqual(list((board / "home/.cache").iterdir()), [], "the bundle was left on the board")

    def test_not_as_root(self):
        board, env = self.board("root")
        result = self.remote(env, "status", host="root@bpi-amp.invalid")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("not root", result.stdout)
        self.assertEqual((board / "hosts").read_text(), "")

    def test_the_board_script(self):
        for lock in (ROOT / ".claude/lab.lock",
                     Path(subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=ROOT,
                                         capture_output=True, text=True).stdout.strip()).parent / ".claude/lab.lock"):
            if lock.exists():
                self.skipTest(f"the lab is locked ({lock}): the board script refuses everything")
        env = dict(os.environ, BOARD_HOST="bpi-amp.invalid", BOARD_USER="user")

        def board(*args):
            return subprocess.run(["bash", str(ROOT / "tools" / "board.sh"), "--dry-run", *args], env=env,
                                  capture_output=True, text=True, timeout=60)

        marker = self.tmp / "journal-injected"
        refused = board("journal", "-n", f"1; touch {marker}")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("whole number", refused.stderr)
        self.assertFalse(marker.exists())
        journal = board("journal", "-n", "50")
        self.assertEqual(journal.returncode, 0, journal.stderr)
        self.assertIn("ssh -t -- user@bpi-amp.invalid journalctl --user-unit speakerd.service -b --no-pager -n 50",
                      journal.stdout.replace("\\", ""))
        status = board("status")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn('ssh -t -- user@bpi-amp.invalid bash "$HOME/.local/lib/speakerd/current/setup/speakerd-setup.sh" status',
                      status.stdout.replace("\\", ""))
        for option in ("--base-conf", "--stop-plugin-host", "--server", "--server-ip", "--no-restart"):
            refused = board("deploy", option, *(["192.0.2.10"] if option.startswith("--server") else []))
            self.assertNotEqual(refused.returncode, 0, option)
            self.assertIn(f"deploy does not take {option}", refused.stderr)
        self.assertNotEqual(board("smoke", "--window", "3; x").returncode, 0)


if __name__ == "__main__":
    unittest.main()
