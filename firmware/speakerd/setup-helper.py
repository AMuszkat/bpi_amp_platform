#!/usr/bin/env python3
"""The JSON side of firmware/speakerd-setup.sh: everything it reads or writes as JSON. Standard
library only (the board's python3). See firmware/CLAUDE.md, "speakerd on a board".

    setup-helper.py amp DUMP [--node NAME]        the amplifier: an ALSA Audio/Sink in a pw-dump
    setup-helper.py node DUMP NAME                one node of a pw-dump: id, process, priority.driver
    setup-helper.py device FILE [--role R] [--amplifier NODE] [--woofer PORT] [--tweeter PORT]
                                [--gain-db G --tweeter-limit muted|on] [--show | --check]
    setup-helper.py graph FILE                     a graph document's structure, and its summary
    setup-helper.py plugins FILE [MANIFEST...]     the plugin identifiers a chain document names, one
                                                   per line; with LV2 manifests, only those none of
                                                   them declares (what a bundle lacks)
    setup-helper.py write FILE                     stdin to FILE, atomically
    setup-helper.py drivers PROFILER_OUTPUT DUMP   which node drives which, from pw-profiler
    setup-helper.py sink DUMP                      the live speakerd sink: the drop-in's? the default sink
    setup-helper.py stream DUMP NAME...            an application's playback streams (application.name
                                                   NAME) and what their outputs are linked to (exit 3: none)
    setup-helper.py status SOCKET                  speakerd's `status` through its local link, as JSON
                                                   (exits 3 when another client holds the link: ampd)
    setup-helper.py timing --limit F [--before FILE] [--after FILE] [--window-log FILE]
                           [--start-log FILE] [--window S]
                                                   smoke's timing check: lines "pass|FAIL|warn|info <text>"
    setup-helper.py limits --device FILE [--line TEXT] [--start-gone 0|1]
                           [--ampd-status FILE | --speakerd-status FILE [--unread TEXT]]
                                                   smoke's limits check: the same kind of lines

Each prints shell-readable `key=value` lines or a message, and exits 1 with the reason on
stderr when it cannot do what it was asked.
"""

import json
import os
import re
import sys
import tempfile

ROLES = ("left", "right", "mono")
# The keys speakerd knows. It rejects a device.json with any other key, or a key twice, and
# then plays with its fail-safe defaults (mono, bpi-alsa-playback-pcm, -20 dB, tweeter muted).
DEVICE_KEYS = {"version", "role", "amplifier", "limits"}
AMPLIFIER_KEYS = {"node", "woofer", "tweeter"}
LIMITS_KEYS = {"gain_db", "tweeter"}
TWEETER_LIMITS = ("muted", "on")
# The amplifier's node name in the repository's pipewire.conf, and on the boards (the name
# snapcast-setup.sh's client-rt.conf and speakerd's default use).
KNOWN_AMPLIFIERS = ("bpi-alsa-playback-pcm", "alsa-playback-pcm")
# The graph's fixed nodes (dsp/juce_apps/speakerd/README.md): the input and the output.
INPUT_NODE, OUTPUT_NODE = 1, 2


def fail(message):
    print(f"setup-helper: {message}", file=sys.stderr)
    sys.exit(1)


def quote(value):
    """A value for a shell `key=value` line, read with `eval`."""
    return "'" + str(value).replace("'", "'\\''") + "'"


def load_json(path, what):
    try:
        with open(path) as f:
            return json.load(f)
    except OSError as e:
        fail(f"cannot read {what} {path}: {e.strerror}")
    except json.JSONDecodeError as e:
        fail(f"{what} {path} is not JSON: {e}")


def write_atomically(path, text):
    """Writes `text` to `path` the way state files are written: a temporary file in the same
    folder, fsync, rename, fsync of the folder. A power cut leaves the old file or the new one."""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", dir=folder)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    dfd = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


# ---------------------------------------------------------------------------------------------
# pw-dump

def nodes_and_ports(dump):
    nodes, ports = {}, {}
    for obj in dump:
        kind, info = obj.get("type", ""), obj.get("info") or {}
        if kind == "PipeWire:Interface:Node":
            nodes[obj["id"]] = info.get("props") or {}
        elif kind == "PipeWire:Interface:Port":
            ports[obj["id"]] = (info.get("direction"), info.get("props") or {})
    return nodes, ports


def input_ports(node_id, ports):
    names = [(pid, props.get("port.name", "")) for pid, (direction, props) in ports.items()
             if direction == "input" and props.get("node.id") == node_id]
    return [name for _, name in sorted(names)]


def is_alsa_sink(props):
    return (props.get("media.class") == "Audio/Sink"
            and (str(props.get("factory.name", "")).startswith("api.alsa.")
                 or "api.alsa.path" in props or props.get("device.api") == "alsa"))


def find_node(nodes, name):
    for node_id, props in nodes.items():
        if props.get("node.name") == name:
            return node_id, props
    return None, None


def cmd_amp(args):
    dump = load_json(args[0], "the pw-dump")
    nodes, ports = nodes_and_ports(dump)
    wanted = args[2] if len(args) > 2 and args[1] == "--node" else None
    if wanted:
        node_id, props = find_node(nodes, wanted)
        if node_id is None:
            sinks = sorted(p.get("node.name", "?") for p in nodes.values() if p.get("media.class") == "Audio/Sink")
            fail(f"there is no node '{wanted}' in PipeWire (the sinks: {', '.join(sinks) or 'none'})")
    else:
        alsa = {i: p for i, p in nodes.items() if is_alsa_sink(p) and p.get("node.name") != "speakerd"}
        if not alsa:
            sinks = sorted(p.get("node.name", "?") for p in nodes.values() if p.get("media.class") == "Audio/Sink")
            fail("found no ALSA Audio/Sink, which would be the amplifier (the sinks: "
                 f"{', '.join(sinks) or 'none'}): name it with --amplifier")
        if len(alsa) == 1:
            node_id = next(iter(alsa))
        else:
            named = [i for name in KNOWN_AMPLIFIERS for i, p in alsa.items() if p.get("node.name") == name]
            if not named:
                fail("found several ALSA sinks and none of them is called "
                     f"{' or '.join(KNOWN_AMPLIFIERS)}: {', '.join(sorted(p.get('node.name', '?') for p in alsa.values()))}"
                     ": name the amplifier with --amplifier")
            node_id = named[0]
        props = nodes[node_id]
    print(f"node={quote(props.get('node.name', ''))}")
    print(f"node_id={node_id}")
    print(f"alsa={'yes' if is_alsa_sink(props) else 'no'}")
    print(f"priority={quote(props.get('priority.driver', ''))}")
    print(f"rate={quote(props.get('audio.rate', ''))}")
    print(f"ports={quote(' '.join(input_ports(node_id, ports)))}")


def cmd_node(args):
    dump = load_json(args[0], "the pw-dump")
    nodes, ports = nodes_and_ports(dump)
    node_id, props = find_node(nodes, args[1])
    if node_id is None:
        sys.exit(3)
    clients = {obj["id"]: (obj.get("info") or {}).get("props") or {}
               for obj in dump if obj.get("type") == "PipeWire:Interface:Client"}
    pid = props.get("application.process.id") or clients.get(props.get("client.id"), {}).get("application.process.id", "")
    print(f"node_id={node_id}")
    print(f"pid={quote(pid)}")
    print(f"priority={quote(props.get('priority.driver', ''))}")
    print(f"media_class={quote(props.get('media.class', ''))}")
    print(f"ports={quote(' '.join(input_ports(node_id, ports)))}")


# What 60-speakerd-sink.conf gives the speakerd sink, and only it.
DROPIN_SINK = {"factory.name": "support.null-audio-sink", "media.class": "Audio/Sink",
               "priority.session": 1500, "priority.driver": 0}


def cmd_sink(args):
    """Whether the running PipeWire has the speakerd sink of the drop-in (not one speakerd made
    itself, nor one from another config), and what the default sink is."""
    dump = load_json(args[0], "the pw-dump")
    nodes, _ = nodes_and_ports(dump)
    node_id, props = find_node(nodes, "speakerd")
    default = ""
    for obj in dump:
        if obj.get("type") == "PipeWire:Interface:Metadata" and (obj.get("props") or {}).get("metadata.name") == "default":
            for item in obj.get("metadata") or []:
                if item.get("key") == "default.audio.sink":
                    value = item.get("value")
                    default = value.get("name", "") if isinstance(value, dict) else str(value)
    wrong = [f"{k} {props.get(k)!r} (the drop-in's: {v!r})" for k, v in DROPIN_SINK.items()
             if node_id is not None and props.get(k) != v]
    print(f"present={'yes' if node_id is not None else 'no'}")
    print(f"dropin={'yes' if node_id is not None and not wrong else 'no'}")
    print(f"differs={quote('; '.join(wrong))}")
    print(f"default_sink={quote(default)}")


def cmd_stream(args):
    """Where an application's playback streams play: its Stream/Output nodes and the node and port
    each of their output ports is linked to, all from the pw-dump (never from pw-link, which
    names a port by its node's node.name: snapclient 0.34.0's stream has none, so pw-link prints
    its ports under an empty name). A stream is the application's when its node's (or its
    client's) application.name is one of NAMES, case aside, or, with no application.name, its
    node.name contains one. Exits 3 when it has none."""
    dump = load_json(args[0], "the pw-dump")
    names = [name.lower() for name in args[1:]]
    nodes, ports = nodes_and_ports(dump)
    clients = {obj["id"]: (obj.get("info") or {}).get("props") or {}
               for obj in dump if obj.get("type") == "PipeWire:Interface:Client"}

    def application(props):
        name = props.get("application.name") or clients.get(props.get("client.id"), {}).get("application.name")
        if name:
            return str(name).lower() in names
        return any(n in str(props.get("node.name", "")).lower() for n in names)

    streams = sorted(i for i, p in nodes.items()
                     if str(p.get("media.class", "")).startswith("Stream/Output") and application(p))
    if not streams:
        sys.exit(3)
    targets = []
    for obj in dump:
        if obj.get("type") != "PipeWire:Interface:Link":
            continue
        info = obj.get("info") or {}
        if info.get("output-node-id") not in streams:
            continue
        node = nodes.get(info.get("input-node-id"), {})
        port = ports.get(info.get("input-port-id"), (None, {}))[1]
        targets.append(f"{node.get('node.name') or 'node ' + str(info.get('input-node-id'))}:"
                       f"{port.get('port.name') or 'port ' + str(info.get('input-port-id'))}")
    print(f"nodes={quote(' '.join(map(str, streams)))}")
    print(f"targets={quote(' '.join(sorted(targets)))}")


def cmd_drivers(args):
    """pw-profiler's output names each driver by id ("logging driver 25") and each follower by
    id and name ("logging follower 40 ("pw-play")"). PipeWire 0.3.65's pw-dump shows no driver
    and its pw-top has no batch mode, so this is how a script learns who drives."""
    try:
        text = open(args[0], errors="replace").read()
    except OSError as e:
        fail(f"cannot read {args[0]}: {e.strerror}")
    nodes, _ = nodes_and_ports(load_json(args[1], "the pw-dump"))
    name = lambda i: nodes.get(i, {}).get("node.name", f"id {i}")
    drivers = [int(m) for m in re.findall(r"^logging driver (\d+)", text, re.M)]
    followers = [int(m) for m in re.findall(r"^logging follower (\d+)", text, re.M)]
    if not drivers:
        sys.exit(3)
    print(f"drivers={quote(' '.join(name(i) for i in drivers))}")
    print(f"followers={quote(' '.join(name(i) for i in followers))}")


# ---------------------------------------------------------------------------------------------
# device.json (dsp/juce_apps/speakerd/README.md)

def no_duplicates(pairs):
    keys = [k for k, _ in pairs]
    twice = sorted({k for k in keys if keys.count(k) > 1})
    if twice:
        raise ValueError(f"the key(s) {', '.join(twice)} appear twice")
    return dict(pairs)


def unknown_keys(device):
    """The keys of a device.json that speakerd does not know, as "path" strings."""
    out = [k for k in device if k not in DEVICE_KEYS]
    if isinstance(device.get("amplifier"), dict):
        out += [f"amplifier.{k}" for k in device["amplifier"] if k not in AMPLIFIER_KEYS]
    if isinstance(device.get("limits"), dict):
        out += [f"limits.{k}" for k in device["limits"] if k not in LIMITS_KEYS]
    return out


def cmd_device(args):
    path, opts = args[0], parse_options(args[1:], ("--role", "--amplifier", "--woofer", "--tweeter",
                                                   "--gain-db", "--tweeter-limit"), ("--show", "--check"))
    device = {}
    if os.path.exists(path):
        try:
            with open(path) as f:
                device = json.load(f, object_pairs_hook=no_duplicates)
            if not isinstance(device, dict):
                raise ValueError("not a JSON object")
        except (OSError, ValueError) as e:
            if "--show" in opts or "--check" in opts:
                fail(f"{path} is broken: {e}")
            if "--role" not in opts:
                fail(f"{path} is broken ({e}), and speakerd would play with its fail-safe defaults: "
                     "give --role to write a new one")
            os.replace(path, path + ".broken")
            print(f"note={quote(f'kept the broken {path} as {path}.broken')}")
            device = {}
    if "--show" in opts:
        print(json.dumps(device, indent=2))
        return
    unknown = unknown_keys(device)
    if unknown:
        fail(f"{path} has key(s) speakerd does not know: {', '.join(unknown)}. speakerd would reject the "
             "file and play with its fail-safe defaults: remove them by hand")
    if "--check" in opts:
        return

    role = opts.get("--role", device.get("role"))
    if role not in ROLES:
        fail(f"the role must be one of {', '.join(ROLES)}, not {role!r}" if role else
             "no role yet: --role left|right|mono is required on the first install")
    device["version"] = 1
    device["role"] = role

    amplifier = device.get("amplifier")
    if isinstance(amplifier, str):
        amplifier = {"node": amplifier}
    amplifier = dict(amplifier or {})
    for option, key in (("--amplifier", "node"), ("--woofer", "woofer"), ("--tweeter", "tweeter")):
        if option in opts:
            amplifier[key] = opts[option]
    if not amplifier.get("node"):
        fail("no amplifier: the installer names it (detected, or --amplifier)")
    amplifier.setdefault("woofer", "playback_FL")
    amplifier.setdefault("tweeter", "playback_FR")
    if amplifier["woofer"] == amplifier["tweeter"]:
        fail(f"the woofer and the tweeter are the same port, {amplifier['woofer']}")
    device["amplifier"] = {"node": amplifier["node"], "woofer": amplifier["woofer"],
                           "tweeter": amplifier["tweeter"]}

    if ("--gain-db" in opts) != ("--tweeter-limit" in opts):
        fail("--gain-db and --tweeter-limit go together")
    if "--gain-db" in opts:
        try:
            gain = float(opts["--gain-db"])
        except ValueError:
            fail(f"--gain-db takes a number of dB, not {opts['--gain-db']!r}")
        if not gain <= 0.0:
            fail(f"the output gain limit cannot be above 0 dB ({gain}): device.json would be invalid")
        if opts["--tweeter-limit"] not in TWEETER_LIMITS:
            fail(f"the tweeter limit is muted or on, not {opts['--tweeter-limit']!r}")
        device["limits"] = {"gain_db": gain, "tweeter": opts["--tweeter-limit"]}

    text = json.dumps(device, indent=2) + "\n"
    old = open(path).read() if os.path.exists(path) else None
    if text != old:
        write_atomically(path, text)
        print("changed=yes")
    else:
        print("changed=no")
    limits = device.get("limits")
    print(f"role={quote(role)}")
    print(f"amplifier={quote(device['amplifier']['node'])}")
    print(f"woofer={quote(device['amplifier']['woofer'])}")
    print(f"tweeter={quote(device['amplifier']['tweeter'])}")
    limits_text = f"output at {limits['gain_db']:.1f} dB, tweeter {limits['tweeter']}" if limits else "none"
    print(f"limits={quote(limits_text)}")


def parse_options(args, with_value, flags=()):
    opts, i = {}, 0
    while i < len(args):
        if args[i] in flags:
            opts[args[i]] = True
            i += 1
        elif args[i] in with_value and i + 1 < len(args):
            opts[args[i]] = args[i + 1]
            i += 2
        else:
            fail(f"unknown or incomplete option {args[i]!r}")
    return opts


# ---------------------------------------------------------------------------------------------
# Graph documents, version 1 (dsp/juce_apps/speakerd/README.md)

def cmd_graph(args):
    path = args[0]
    doc = load_json(path, "the graph document")
    if not isinstance(doc, dict):
        fail(f"{path}: not a JSON object")
    if doc.get("format", "speakerd-graph") != "speakerd-graph" or doc.get("version") != 1:
        fail(f"{path}: not a graph document, version 1 (format {doc.get('format')!r}, version {doc.get('version')!r})")
    nodes, connections = doc.get("nodes"), doc.get("connections")
    if not isinstance(nodes, list) or not isinstance(connections, list):
        fail(f"{path}: 'nodes' and 'connections' must be lists")
    by_uid = {}
    for node in nodes:
        uid = node.get("uid") if isinstance(node, dict) else None
        plugin = node.get("plugin") if isinstance(node, dict) else None
        if not isinstance(uid, int) or uid < 16 or uid in by_uid:
            fail(f"{path}: a plugin node needs a uid of 16 or more, each once (got {uid!r})")
        if not isinstance(plugin, dict) or not plugin.get("format") or not plugin.get("identifier"):
            fail(f"{path}: node {uid} has no plugin format and identifier")
        by_uid[uid] = node
    for c in connections:
        try:
            ends = (c["src"]["node"], c["src"]["channel"]), (c["dst"]["node"], c["dst"]["channel"])
        except (KeyError, TypeError):
            fail(f"{path}: a connection needs src and dst, each with node and channel: {c!r}")
        for end, _ in ends:
            if end not in (INPUT_NODE, OUTPUT_NODE) and end not in by_uid:
                fail(f"{path}: a connection names node {end}, which is not in the document")
    names = ", ".join(str(by_uid[u].get("name") or str(by_uid[u]["plugin"]["identifier"]).rsplit("/", 1)[-1])
                      for u in sorted(by_uid)) or "no plugin"
    summary = f"revision {doc.get('revision', 0)}, {len(by_uid)} plugin node(s): {names}"
    print(f"summary={quote(summary)}")


def cmd_status(args):
    """speakerd's answer to {"op":"status"} on its local link (docs/protocol.md): the hello first,
    then the answer. speakerd takes one client: with ampd connected it answers busy (exit 3)."""
    import socket
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(3.0)
    try:
        sock.connect(args[0])
        reader = sock.makefile("rb")
        sock.sendall(b'{"op":"status"}\n')
        for _ in range(64):
            line = reader.readline(1 << 20)
            if not line:
                break
            msg = json.loads(line)
            if msg.get("op") == "error" and msg.get("reason") == "busy":
                print("speakerd's local link is busy: another client (ampd) holds it", file=sys.stderr)
                sys.exit(3)
            if msg.get("op") == "status":
                print(json.dumps(msg))
                return
        fail("speakerd did not answer status")
    except (OSError, ValueError) as e:
        fail(f"speakerd's local link {args[0]}: {e}")
    finally:
        sock.close()


# The summary line and the per-second line of speakerd's log (its README, "Log lines"). The
# summary covers <n> s: up to 60 until 0.3.3; from 0.3.4 the first 60 s after the start, then up
# to 600 s while nothing is late and nothing changed, and at most 60 s again after a late cycle.
TIMING_MINUTE = re.compile(r"^(?:speakerd: )?timing: last (\d+) s: (?:(.*); )?graph mean ([\d.]+) % max ([\d.]+) %; "
                           r"cycle mean ([\d.]+) % max ([\d.]+) %; (\d+) cycles, (\d+) late, (\d+) position jump\(s\)$")
TIMING_MINUTE_NODE = re.compile(r"^node (\d+) \((.*)\) mean ([\d.]+) % max ([\d.]+) %$")
# At start: what one read of the monotonic clock costs (tens of ns: the vDSO; hundreds: a syscall).
TIMING_CLOCK = re.compile(r"^(?:speakerd: )?timing: a clock read costs ([\d.]+) ns\s*$")
TIMING_SECOND = re.compile(r"^(?:speakerd: )?timing: in the last second (\d+) late cycle\(s\), the longest at "
                           r"([\d.]+) % of its cycle; (\d+) position jump\(s\)$")


# speakerd's lines for an apply (its README, "Log lines"): the message thread starts building a
# revision, then, once the new graph plays, says it applied it (or that it FAILED: nothing changed).
APPLY_BUILDING = re.compile(r"^(?:speakerd: )?apply: revision (\d+): building$")
APPLY_DONE = re.compile(r"^(?:speakerd: )?apply: revision (\d+): (applied, |FAILED)")
# speakerd 0.3.4 schedules a summary at least 60 s after the one before (a minute after its start,
# 10 minutes while nothing happens, a minute after a late second or a graph change): only the line
# it logs at once when the graph changes, for the old graph's cycles, may cover less.
TIMING_SCHEDULED_SECONDS = 60


def current_graph_summary(lines):
    """The last `timing: last <n> s:` line of `lines` (speakerd's log since its start) that covers
    the graph that plays now: (summary, None), or (None, why) when there is none yet.

    The graph changes when a revision is applied: between its `apply: revision <r>: building` and
    a few cycles before its `applied` line. From speakerd 0.3.4 the summary is logged at once then,
    for the old graph's cycles (up to about 50 ms after `applied`), and the new graph's first one a
    minute later. So the old graph's are: every summary between `building` and `applied`, and the
    first one after `applied` when none came before it (speakerd 0.3.3 and older, and a change
    from passthrough, log none at the change: that first one then mixes both graphs) or when it
    covers less than 60 s (none of the scheduled ones does: it is the change's). The rest are the
    new graph's."""
    summaries, change, building = [], None, {}
    for i, line in enumerate(lines):
        summary = minute_from_log(line)
        if summary is not None:
            summaries.append((i, summary))
            continue
        m = APPLY_BUILDING.match(line)
        if m:
            building[m.group(1)] = i
            continue
        m = APPLY_DONE.match(line)
        if m and m.group(2).startswith("applied") and m.group(1) in building:
            change = (building[m.group(1)], i, int(m.group(1)))
    if change is None:
        return (summaries[-1][1], None) if summaries else (None, None)
    built, applied, revision = change
    old, new, before_applied, flush_taken = None, None, False, False
    for i, summary in summaries:
        if i < built:
            old = summary
        elif i < applied:
            old, before_applied = summary, True
        elif not flush_taken and (summary["seconds"] < TIMING_SCHEDULED_SECONDS or not before_applied):
            old, flush_taken = summary, True
        else:
            new, flush_taken = summary, True
    if new is not None:
        return new, None
    said = (f"its last 'timing: last' line covers the graph revision {revision} replaced ({old['seconds']} s: graph "
            f"max {100 * old['graph']['max']:.1f} %)" if old is not None else "it has logged no 'timing: last' line")
    return None, (f"speakerd applied revision {revision} and {said}: speakerd 0.3.4 logs the old graph's summary at "
                  "the change and the new graph's first a minute after it, so smoke again in a minute")


def minute_from_log(line):
    """A `timing: last <n> s: ...` line as status's timing object (fractions, not percent)."""
    m = TIMING_MINUTE.match(line)
    if not m:
        return None
    nodes = []
    for part in m.group(2).split("; ") if m.group(2) else []:
        n = TIMING_MINUTE_NODE.match(part)
        if n:
            nodes.append({"uid": int(n.group(1)), "name": n.group(2), "mean": float(n.group(3)) / 100,
                          "max": float(n.group(4)) / 100})
    return {"seconds": int(m.group(1)), "nodes": nodes,
            "graph": {"mean": float(m.group(3)) / 100, "max": float(m.group(4)) / 100},
            "cycle": {"mean": float(m.group(5)) / 100, "max": float(m.group(6)) / 100},
            "cycles": int(m.group(7)), "late": int(m.group(8)), "jumps": int(m.group(9))}


def read_status_timing(path):
    try:
        with open(path) as f:
            timing = json.loads(f.read() or "null")
    except (OSError, ValueError):
        return None
    timing = timing.get("timing") if isinstance(timing, dict) else None
    return timing if isinstance(timing, dict) and isinstance(timing.get("graph"), dict) else None


def read_lines(path):
    try:
        with open(path, errors="replace") as f:
            return f.read().splitlines()
    except (OSError, TypeError):
        return []


def cmd_timing(args):
    """smoke's timing check (firmware/CLAUDE.md, "smoke"): what speakerd's audio thread spends, per
    node, for the graph and the cycle, from its status (the last minute, read after the window) or
    else its log's last `timing: last <n> s:` line since it started that covers the graph that plays
    (current_graph_summary: none, and a warning, for a minute after an apply); the late cycles and position
    jumps in the window from the status before and after it (late_total, jumps_total) and from
    the log's `timing: in the last second` lines in the window (it names every second with one).
    FAIL when the graph's longest cycle is above --limit of its cycle, or a cycle was late in the
    window; warn on position jumps in the window, and when a clock read costs more than
    --clock-warn-ns (speakerd's start-up line: then clock reads are system calls)."""
    opts = {"--limit": "0.5", "--before": "", "--after": "", "--window-log": "", "--start-log": "", "--window": "?",
            "--clock-warn-ns": "150"}
    while args:
        if args[0] not in opts or len(args) < 2:
            fail(f"timing: unknown or incomplete option {args[0]}")
        opts[args[0]] = args[1]
        args = args[2:]
    limit = float(opts["--limit"])
    before, after = read_status_timing(opts["--before"]), read_status_timing(opts["--after"])
    window_lines, start_lines = read_lines(opts["--window-log"]), read_lines(opts["--start-log"])

    clock = None
    for line in start_lines:
        m = TIMING_CLOCK.match(line)
        if m:
            clock = float(m.group(1))
    clock_said = (f"; a clock read costs {clock:g} ns" if clock is not None
                  else "; speakerd did not say what a clock read costs")

    summary, source, changed = after, None, None
    if summary is not None:
        source = f"speakerd's status, the last {summary.get('seconds', '?')} s"
    else:
        summary, changed = current_graph_summary(start_lines + window_lines)
        if summary is not None:
            source = f"speakerd's log, the {summary['seconds']} s before its last 'timing: last' line"
    if changed is not None:
        print(f"warn the current graph's timing is NOT measured yet: {changed} (the timing comes from speakerd's "
              "log: its local link is ampd's, or busy)")
    elif summary is None:
        print("warn the graph's timing was NOT measured: speakerd's local link is busy (ampd holds it) or did not "
              "answer, and speakerd has logged no 'timing: last <n> s' line since it started (it logs one a "
              "minute after its start, then every minute up to speakerd 0.3.3 and every 10 minutes from 0.3.4 while "
              "nothing is late; a speakerd older than its timing never does)")
    else:
        for node in summary.get("nodes") or []:
            print(f"info timing: node {node.get('uid')} ({node.get('name')}) mean {100 * node.get('mean', 0):.1f} % "
                  f"max {100 * node.get('max', 0):.1f} % of the cycle")
        graph, cycle = summary["graph"], summary.get("cycle") or {}
        print(f"info timing: graph mean {100 * graph.get('mean', 0):.1f} % max {100 * graph.get('max', 0):.1f} %, "
              f"cycle mean {100 * cycle.get('mean', 0):.1f} % max {100 * cycle.get('max', 0):.1f} %; "
              f"{summary.get('cycles', '?')} cycles, {summary.get('late', '?')} late, "
              f"{summary.get('jumps', '?')} position jump(s){clock_said} ({source})")
        worst = graph.get("max", 0)
        if worst > limit:
            print(f"FAIL the graph's longest cycle took {100 * worst:.1f} % of its cycle ({source}): above "
                  f"{100 * limit:.0f} %, too close to a late cycle")
        else:
            print(f"pass the graph's longest cycle took {100 * worst:.1f} % of its cycle ({source}; at most "
                  f"{100 * limit:.0f} %)")

    if summary is None:
        print(f"info timing{clock_said.replace(';', ':', 1)}")
    if clock is not None and clock > float(opts["--clock-warn-ns"]):
        # Per cycle speakerd reads the clock 4 times, and twice per plugin node.
        n = len(summary.get("nodes") or []) if summary is not None else None
        reads = f"{4 + 2 * n}" if n is not None else "(4+2n)"
        cost = f" = {(4 + 2 * n) * clock / 1000:.1f} us" if n is not None else " for n plugin nodes"
        print(f"warn clock reads are system calls on this kernel (ARM erratum 858921 workaround?): per-node timing "
              f"costs about {reads}\u00d7{clock:g} ns{cost} per cycle (a clock read costs {clock:g} ns, more than "
              f"{float(opts['--clock-warn-ns']):g})")

    # The window: the counters' difference when speakerd answered before and after, and the log.
    late = jumps = 0
    said = []
    for line in window_lines:
        m = TIMING_SECOND.match(line)
        if m:
            late += int(m.group(1))
            jumps += int(m.group(3))
            said.append(line.split("timing: ", 1)[1])
    how = "its log's per-second lines"
    if before is not None and after is not None and "late_total" in before and "late_total" in after:
        late = max(late, after["late_total"] - before["late_total"])
        jumps = max(jumps, after.get("jumps_total", 0) - before.get("jumps_total", 0))
        how = "speakerd's late_total and jumps_total before and after, and its log"
    elif not window_lines and before is None:
        how += " (no log in the window)"
    shown = "; ".join(said[:3]) + ("; ..." if len(said) > 3 else "")
    if late:
        print(f"FAIL {late} late cycle(s) in {opts['--window']} s ({how}){': ' + shown if shown else ''}")
    else:
        print(f"pass no late cycle in {opts['--window']} s ({how})")
    if jumps:
        print(f"warn {jumps} position jump(s) in {opts['--window']} s: a cycle the driver skipped (an xrun) or "
              f"another driver ({how}){': ' + shown if shown else ''}")
    if summary is not None and not late and summary.get("late"):
        print(f"warn {summary['late']} late cycle(s) before the window ({source})")


# speakerd's start-up line for the limits it plays, exactly (speakerd-setup.sh's LIMITS_LINE);
# with the fail-safe it adds a suffix, which this does not match.
LIMITS_LINE = re.compile(r"^speakerd: device: limits: output at (-?[0-9.]+(e[-+]?[0-9]+)?) dB, tweeter (muted|on)$")


def reported_limits(opts):
    """What speakerd reports it enforces (its hello and status, from speakerd 0.3.4): (limits,
    where, why). limits is {"gain_db", "tweeter", "fail_safe"} or None; where names the source;
    why says why there is none (an older speakerd or ampd, a status that could not be read).
    `bad` in why's place (limits None, why starting "BAD ") is a report smoke cannot read."""
    if opts["--ampd-status"]:
        where = "its status, through ampd's link"
        try:
            with open(opts["--ampd-status"]) as f:
                status = json.load(f)
        except (OSError, ValueError) as e:
            return None, where, f"ampd's status.json could not be read: {e}"
        sd = status.get("speakerd") if isinstance(status, dict) else None
        sd = sd if isinstance(sd, dict) else {}
        if "limits" not in sd:
            return None, where, f"ampd {status.get('ampd', '?')} does not pass them on (ampd 0.3.3 and later do)"
        limits = sd["limits"]
        if limits is None:
            return None, where, f"speakerd {sd.get('version') or '?'} does not report them (speakerd 0.3.4 and later do)"
    else:
        where = "its status"
        text = ""
        try:
            with open(opts["--speakerd-status"]) as f:
                text = f.read().strip()
        except (OSError, TypeError):
            pass
        if not text:
            return None, where, opts["--unread"] or "speakerd's status was not read"
        try:
            status = json.loads(text)
        except ValueError:
            return None, where, "speakerd's status is not JSON"
        if not isinstance(status, dict) or "limits" not in status:
            return None, where, "this speakerd does not report them (speakerd 0.3.4 and later do)"
        limits = status["limits"]
    gain = limits.get("gain_db") if isinstance(limits, dict) else None
    if not (isinstance(limits, dict) and isinstance(gain, (int, float)) and not isinstance(gain, bool)
            and limits.get("tweeter") in TWEETER_LIMITS
            and (limits.get("fail_safe") is None or isinstance(limits.get("fail_safe"), str))):
        return None, where, "BAD " + json.dumps(limits)[:200]
    return limits, where, None


def cmd_limits(args):
    """smoke's limits check (firmware/CLAUDE.md, "smoke"): the limits speakerd enforces against
    device.json's, from two sources: its start-up line `device: limits: ...` (--line, the
    journal's first since its start; gone when the journal rotated it out) and what it reports on
    its local link (from speakerd 0.3.4: read by smoke when the link is free, --speakerd-status,
    or by ampd when ampd holds it, --ampd-status, its status.json). Each source there must say
    device.json's limits (gain within 0.001 dB, the tweeter the same) and not the fail-safe; both
    there must agree. FAIL also on device.json without limits, and on neither source while the
    journal holds the start; a warning when the start rotated out and speakerd reports nothing."""
    opts = {"--device": "", "--line": "", "--start-gone": "0", "--ampd-status": "", "--speakerd-status": "",
            "--unread": ""}
    while args:
        if args[0] not in opts or len(args) < 2:
            fail(f"limits: unknown or incomplete option {args[0]}")
        opts[args[0]] = args[1]
        args = args[2:]
    try:
        with open(opts["--device"]) as f:
            device = json.load(f)
    except (OSError, ValueError):
        device = {}
    want = device.get("limits") if isinstance(device, dict) else None
    want = want if isinstance(want, dict) and isinstance(want.get("gain_db"), (int, float)) else None
    wanted = f"output at {want['gain_db']} dB, tweeter {want.get('tweeter')}" if want else "no limits"

    def same(gain, tweeter):
        return want is not None and abs(gain - want["gain_db"]) < 0.001 and tweeter == want.get("tweeter")

    failures = []
    line = opts["--line"]
    said = None                          # (gain text, gain, tweeter) of an exact start-up line
    if line:
        m = LIMITS_LINE.match(line)
        try:
            said = (m.group(1), float(m.group(1)), m.group(3)) if m else None
        except ValueError:
            said = None
        text = line[len("speakerd: "):] if line.startswith("speakerd: ") else line
        if said is None:
            failures.append(f"speakerd does not play device.json's limits: '{text}'")
        elif want is None:
            failures.append(f"device.json has no limits (speakerd then plays with its fail-safe ones; say them "
                            f"explicitly): {text}")
        elif not same(said[1], said[2]):
            failures.append(f"speakerd says '{text}'; device.json says {wanted}")

    limits, where, why = reported_limits(opts)
    if why and why.startswith("BAD "):
        failures.append(f"speakerd reports limits smoke cannot read ({where}): {why[4:]}")
        why = None
    if limits is not None:
        gain, tweeter, fail_safe = limits["gain_db"], limits["tweeter"], limits.get("fail_safe")
        reported = f"output at {gain:g} dB, tweeter {tweeter}"
        if fail_safe is not None:
            failures.append(f"speakerd reports its fail-safe limits ({fail_safe}): {reported} ({where}); "
                            f"device.json says {wanted}")
        elif want is None:
            failures.append(f"device.json has no limits (speakerd then plays with its fail-safe ones; say them "
                            f"explicitly): speakerd reports {reported} ({where})")
        elif not same(gain, tweeter):
            failures.append(f"speakerd reports {reported} ({where}); device.json says {wanted}")
        if said is not None and not (abs(said[1] - gain) < 0.001 and said[2] == tweeter):
            failures.append(f"speakerd's start-up line says 'output at {said[0]} dB, tweeter {said[2]}' but it "
                            f"reports {reported} ({where})")

    for failure in failures:
        print(f"FAIL {failure}")
    if not line and limits is None:
        if failures:
            return
        if opts["--start-gone"] == "1":
            print(f"warn the limits speakerd enforces are NOT checked: its 'device: limits:' line is no longer in "
                  f"the journal (device.json says {wanted}), and {why}")
        else:
            print("FAIL speakerd did not say which limits it enforces ('device: limits: ...'): a speakerd without "
                  "the limits must not play on a board")
        return
    if not line and opts["--start-gone"] != "1":
        print("warn speakerd's start-up line 'device: limits:' is not in its log since its start, though it "
              f"reports its limits ({where})")
    if line and limits is None and why:
        print(f"info the limits come from speakerd's start-up line only: {why}")
    if not failures:
        shown = said[0] if said is not None else f"{limits['gain_db']:g}"
        tweeter = said[2] if said is not None else limits["tweeter"]
        sources = " and ".join(s for s in ("its start-up line" if said is not None else "",
                                            where if limits is not None else "") if s)
        print(f"pass device.json's limits: output at {shown} dB, tweeter {tweeter} ({sources})")


def cmd_plugins(args):
    """The plugin identifiers of a chain document, each once, in the order of its nodes; with
    LV2 manifest.ttl files after it, only those that none of them declares as a subject: the
    plugins a bundle lacks (an empty output: it has every one). A trailing slash is not a
    difference. A manifest that cannot be read declares nothing."""
    doc = load_json(args[0], "the graph document")
    nodes = doc.get("nodes") if isinstance(doc, dict) else None
    if not isinstance(nodes, list):
        fail(f"{args[0]}: not a graph document ('nodes' is not a list)")
    declared = set()
    for manifest in args[1:]:
        try:
            with open(manifest, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        declared.update(m.rstrip("/") for m in re.findall(r"^<([^>]+)>", text, re.M))
    named = []
    for node in nodes:
        plugin = node.get("plugin") if isinstance(node, dict) else None
        identifier = str((plugin or {}).get("identifier", "") if isinstance(plugin, dict) else "").rstrip("/")
        if identifier and identifier not in named and identifier not in declared:
            named.append(identifier)
    for identifier in named:
        print(identifier)


def cmd_write(args):
    write_atomically(args[0], sys.stdin.read())


COMMANDS = {"amp": (cmd_amp, 1), "node": (cmd_node, 2), "device": (cmd_device, 1), "sink": (cmd_sink, 1),
            "graph": (cmd_graph, 1), "plugins": (cmd_plugins, 1), "write": (cmd_write, 1), "drivers": (cmd_drivers, 2),
            "stream": (cmd_stream, 2), "status": (cmd_status, 1), "timing": (cmd_timing, 0),
            "limits": (cmd_limits, 0)}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    command, least = COMMANDS[sys.argv[1]]
    if len(sys.argv) - 2 < least:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    command(sys.argv[2:])


if __name__ == "__main__":
    main()
