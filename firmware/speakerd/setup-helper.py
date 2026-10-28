#!/usr/bin/env python3
"""The JSON side of firmware/speakerd-setup.sh: everything it reads or writes as JSON. Standard
library only (the board's python3). See firmware/CLAUDE.md, "speakerd on a board".

    setup-helper.py amp DUMP [--node NAME]        the amplifier: an ALSA Audio/Sink in a pw-dump
    setup-helper.py node DUMP NAME                one node of a pw-dump: id, process, priority.driver
    setup-helper.py device FILE [--role R] [--amplifier NODE] [--woofer PORT] [--tweeter PORT]
                                [--gain-db G --tweeter-limit muted|on] [--show]
    setup-helper.py graph FILE [--allow-tweeter-without-crossover]
    setup-helper.py write FILE                     stdin to FILE, atomically
    setup-helper.py drivers PROFILER_OUTPUT DUMP   which node drives which, from pw-profiler

Each prints shell-readable `key=value` lines or a message, and exits 1 with the reason on
stderr when it cannot do what it was asked.
"""

import json
import os
import re
import sys
import tempfile

ROLES = ("left", "right", "mono")
TWEETER_LIMITS = ("muted", "on")
# The amplifier's node name in the repository's pipewire.conf, and on the boards (the name
# snapcast-setup.sh's client-rt.conf and speakerd's default use).
KNOWN_AMPLIFIERS = ("bpi-alsa-playback-pcm", "alsa-playback-pcm")
INPUT_NODE, OUTPUT_NODE, TWEETER_CHANNEL = 1, 2, 1


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

def cmd_device(args):
    path, opts = args[0], parse_options(args[1:], ("--role", "--amplifier", "--woofer", "--tweeter",
                                                   "--gain-db", "--tweeter-limit"), ("--show",))
    device = {}
    if os.path.exists(path):
        try:
            with open(path) as f:
                device = json.load(f)
            if not isinstance(device, dict):
                raise ValueError("not a JSON object")
        except (OSError, ValueError) as e:
            if "--role" not in opts:
                fail(f"{path} is broken ({e}): give --role to write a new one")
            os.replace(path, path + ".broken")
            print(f"note=kept the broken {path} as {path}.broken")
            device = {}
    if "--show" in opts:
        print(json.dumps(device, indent=2))
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
                           "tweeter": amplifier["tweeter"],
                           **{k: v for k, v in amplifier.items() if k not in ("node", "woofer", "tweeter")}}

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

def is_crossover(node):
    identifier = str((node.get("plugin") or {}).get("identifier", ""))
    return identifier.rstrip("/").rsplit("/", 1)[-1].lower().startswith("crossover") and not node.get("bypassed")


def cmd_graph(args):
    path = args[0]
    allow = "--allow-tweeter-without-crossover" in args[1:]
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
    edges, feeders = {}, set()
    for c in connections:
        try:
            src, dst = (c["src"]["node"], c["src"]["channel"]), (c["dst"]["node"], c["dst"]["channel"])
        except (KeyError, TypeError):
            fail(f"{path}: a connection needs src and dst, each with node and channel: {c!r}")
        for end in (src[0], dst[0]):
            if end not in (INPUT_NODE, OUTPUT_NODE) and end not in by_uid:
                fail(f"{path}: a connection names node {end}, which is not in the document")
        if dst == (OUTPUT_NODE, TWEETER_CHANNEL):
            feeders.add(src[0])
        else:
            edges.setdefault(src[0], set()).add(dst[0])
    # Every path from the stream to the tweeter must pass through a crossover (README: "Only a
    # crossover may feed the tweeter"). Walk from the input without entering a crossover.
    reached, todo = {INPUT_NODE}, [INPUT_NODE]
    while todo:
        for nxt in edges.get(todo.pop(), ()):
            if nxt not in reached and not (nxt in by_uid and is_crossover(by_uid[nxt])):
                reached.add(nxt)
                todo.append(nxt)
    unsafe = sorted(f for f in feeders if f in reached)
    names = ", ".join(str(by_uid[u].get("name") or str(by_uid[u]["plugin"]["identifier"]).rsplit("/", 1)[-1])
                      for u in sorted(by_uid)) or "no plugin"
    if unsafe:
        message = ("the stream reaches the tweeter without a crossover (through node(s) "
                   f"{', '.join(map(str, unsafe))}): full range on a tweeter can destroy it")
        if not allow:
            fail(f"{path}: {message}")
        print(f"warning={quote(message + ' (allowed by --allow-tweeter-without-crossover)')}")
    summary = f"revision {doc.get('revision', 0)}, {len(by_uid)} plugin node(s): {names}"
    print(f"summary={quote(summary)}")


def cmd_write(args):
    write_atomically(args[0], sys.stdin.read())


COMMANDS = {"amp": (cmd_amp, 1), "node": (cmd_node, 2), "device": (cmd_device, 1),
            "graph": (cmd_graph, 1), "write": (cmd_write, 1), "drivers": (cmd_drivers, 2)}


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
