#!/usr/bin/env python3
"""ampd: the speaker's network agent, and the hub on the left speaker.

Step 3 of docs/remote-plugin-host.md. It speaks docs/protocol.md (version 1): the local link to
this speaker's speakerd (a Unix socket), the control link (TCP 7450), the knobs (OSC over UDP
7451) and the monitor link (TCP 7452). It never touches audio: speakerd plays whatever happens
here, and keeps playing when ampd is gone. See firmware/CLAUDE.md, "ampd", for the command line,
the files and the log lines.

    ampd --config ~/.local/state/speakerd/ampd.json --state-dir ~/.local/state/speakerd

Python 3.9 or later, the standard library only (a board has no pip). One thread runs everything
(asyncio); the files are written on a worker thread, so a slow disk holds nothing up.
"""

import argparse
import asyncio
import concurrent.futures
import copy
import datetime
import json
import math
import os
import re
import signal
import struct
import sys
import time

VERSION = "0.1.0"
PROTOCOL = 1
CONTROL_PORT, KNOB_PORT, MONITOR_PORT = 7450, 7451, 7452
LINE_LIMIT = 32 * 1024 * 1024          # a longer line closes the connection
RETRY = 1.0                            # every reconnection, every second
HELLO_TIMEOUT = 10.0
APPLY_TIMEOUT = 10.0                   # a speaker that does not answer an apply in this has failed it
GESTURE_TIMEOUT = 5.0                  # a gesture with no end ends by itself
PERSIST_INTERVAL = 1.0                 # graph.json at most once a second while parameters change
LKG_AFTER = 60.0                       # graph.json -> graph.lkg.json after this long with no restart
MAX_MONITOR_RATE = 60.0
MONITOR_BACKLOG = 256 * 1024           # a monitor client further behind than this skips values
LAYER_FILES = {"current": "graph.json", "last-known-good": "graph.lkg.json", "factory": "factory.json"}
SEQ_MASK = 0xFFFFFFFF
BIG = 2 ** 63 - 1
TIME_FORMAT = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z$")
STREAM = re.compile(r"^(output/level|node/\d+/mon_[A-Za-z0-9_]+)$")


def log(text):
    print("ampd: " + text, file=sys.stderr, flush=True)


class Once:
    """Says a message once until reset: 'trying again every second' is said once, however long."""

    def __init__(self):
        self.said = set()

    def say(self, key, text):
        if key not in self.said:
            self.said.add(key)
            log(text)

    def reset(self):
        self.said.clear()


class Throttled:
    """Says a message at most once per `interval` seconds per key (a flood of bad datagrams)."""

    def __init__(self, interval=10.0):
        self.interval, self.last = interval, {}

    def say(self, key, text):
        now = time.monotonic()
        if now - self.last.get(key, -math.inf) >= self.interval:
            self.last[key] = now
            log(text)


THROTTLED = Throttled()
STOPPING = []      # set when ampd stops: the connections' ends are not news then


BACKGROUND = set()   # tasks nobody awaits: kept here until they end (asyncio keeps only weak references)


def background(coroutine):
    task = asyncio.get_running_loop().create_task(coroutine)
    BACKGROUND.add(task)
    task.add_done_callback(BACKGROUND.discard)
    return task


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def newer(seq, last):
    """Whether hub sequence number `seq` is newer than `last` (None: nothing applied yet), in
    32-bit serial number arithmetic. 0 is a controller's, never newer."""
    if seq == 0:
        return False
    if last is None:
        return True
    return 0 < ((seq - last) & SEQ_MASK) < 0x80000000


# ---------------------------------------------------------------------------------------------
# OSC 1.0, by hand: one message per datagram, no bundles

def _osc_pad(raw):
    return raw + b"\0" * (4 - len(raw) % 4)


def osc_encode(address, tags, *args):
    out = _osc_pad(address.encode()) + _osc_pad(("," + tags).encode())
    for tag, arg in zip(tags, args):
        if tag == "i":
            out += struct.pack(">i", int(arg))
        elif tag == "f":
            out += struct.pack(">f", float(arg))
        elif tag == "s":
            out += _osc_pad(str(arg).encode())
        else:
            raise ValueError(f"OSC type {tag} is not written here")
    return out


def _osc_string(data, at):
    end = data.index(b"\0", at)
    return data[at:end].decode(), (end + 4) & ~3


def osc_decode(data):
    """(address, [arguments]) of one OSC message; ValueError when it is not one."""
    if data.startswith(b"#bundle"):
        raise ValueError("an OSC bundle (not used)")
    try:
        address, at = _osc_string(data, 0)
        if not address.startswith("/"):
            raise ValueError("not an OSC message: no address")
        if at >= len(data):
            return address, []
        tags, at = _osc_string(data, at)
        if not tags.startswith(","):
            raise ValueError("not an OSC message: no type tags")
        args = []
        for tag in tags[1:]:
            if tag == "i":
                args.append(struct.unpack_from(">i", data, at)[0])
                at += 4
            elif tag == "f":
                args.append(struct.unpack_from(">f", data, at)[0])
                at += 4
            elif tag == "h":
                args.append(struct.unpack_from(">q", data, at)[0])
                at += 8
            elif tag == "d":
                args.append(struct.unpack_from(">d", data, at)[0])
                at += 8
            elif tag == "s":
                value, at = _osc_string(data, at)
                args.append(value)
            elif tag in "TF":
                args.append(tag == "T")
            else:
                raise ValueError(f"OSC type {tag} is not read here")
        return address, args
    except (struct.error, UnicodeDecodeError, IndexError):
        raise ValueError("not an OSC message") from None


# ---------------------------------------------------------------------------------------------
# Values and documents

def good_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def good_value(value):
    """A parameter's value as a document takes it: a number, a choice's text, a switch."""
    return good_number(value) or isinstance(value, bool) or (isinstance(value, str) and 0 < len(value) < 256)


def good_int(value, low, high):
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def check_document(doc):
    """Why `doc` is not a graph document, version 1, as speakerd reads it; None when it is.
    The structure only: whether its plugins load is for the speakers to say."""
    if not isinstance(doc, dict):
        return "not a JSON object"
    if doc.get("format", "speakerd-graph") != "speakerd-graph":
        return 'format: must be "speakerd-graph"'
    if doc.get("version") != 1:
        return "version: must be 1"
    if "revision" in doc and not good_int(doc["revision"], 0, BIG):
        return "revision: an integer, 0 or more"
    nodes, connections = doc.get("nodes"), doc.get("connections")
    if not isinstance(nodes, list):
        return "nodes: missing (an array, possibly empty)"
    if not isinstance(connections, list):
        return "connections: missing (an array, possibly empty)"
    uids = set()
    for i, node in enumerate(nodes):
        where = f"nodes[{i}]"
        if not isinstance(node, dict):
            return f"{where}: not an object"
        if not good_int(node.get("uid"), 16, SEQ_MASK):
            return f"{where}.uid: an integer from 16 (1 is the input, 2 the output, 3 to 15 reserved)"
        if node["uid"] in uids:
            return f"{where}.uid: {node['uid']} is used twice"
        uids.add(node["uid"])
        plugin = node.get("plugin")
        if not isinstance(plugin, dict) or not isinstance(plugin.get("format"), str) \
                or not isinstance(plugin.get("identifier"), str) or not plugin["identifier"]:
            return f'{where}.plugin: {{"format": ..., "identifier": ...}}'
        params = node.get("params", {})
        if not isinstance(params, dict):
            return f"{where}.params: not an object"
        for key, value in params.items():
            if key.startswith("mon_"):
                return f"{where}.params.{key}: a monitor is never in a document"
            if not good_value(value):
                return f"{where}.params.{key}: not a value"
        if "state" in node and not isinstance(node["state"], str):
            return f"{where}.state: base64 text"
        if "bypassed" in node and not isinstance(node["bypassed"], bool):
            return f"{where}.bypassed: true or false"
    for i, c in enumerate(connections):
        where = f"connections[{i}]"
        if not isinstance(c, dict) or not isinstance(c.get("src"), dict) or not isinstance(c.get("dst"), dict):
            return f'{where}: {{"src": {{"node", "channel"}}, "dst": {{...}}}}'
        for end in ("src", "dst"):
            if not good_int(c[end].get("node"), 1, SEQ_MASK) or not good_int(c[end].get("channel"), 0, 1023):
                return f"{where}.{end}: node and channel, whole numbers"
            if c[end]["node"] not in uids and c[end]["node"] not in (1, 2):
                return f"{where}.{end}: no node {c[end]['node']}"
        if c["src"]["node"] == 2:
            return f"{where}.src: the output node (2) has no outputs"
        if c["dst"]["node"] == 1:
            return f"{where}.dst: the input node (1) has no inputs"
    return None


def plugins_of(doc):
    return {node["plugin"]["identifier"] for node in doc.get("nodes", [])
            if isinstance(node, dict) and isinstance(node.get("plugin"), dict) and "identifier" in node["plugin"]}


def empty_document():
    return {"format": "speakerd-graph", "version": 1, "revision": 0, "nodes": [], "connections": []}


def atomic_write(path, text, sync=True):
    """Writes `text` as `path` whole or not at all: a temporary file, fsync, rename, fsync of
    the folder. A power cut leaves the old file or the new one, never half of either."""
    folder = os.path.dirname(path) or "."
    tmp = os.path.join(folder, f".{os.path.basename(path)}.tmp.{os.getpid()}")
    with open(tmp, "w") as f:
        f.write(text)
        f.flush()
        if sync:
            os.fsync(f.fileno())
    os.replace(tmp, path)
    if sync:
        fd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


# ---------------------------------------------------------------------------------------------
# JSON lines

class ProtocolError(Exception):
    pass


class Conn:
    """One JSON-lines connection (control, monitor or local)."""

    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer
        peer, own = writer.get_extra_info("peername"), writer.get_extra_info("sockname")
        self.address = peer[0] if isinstance(peer, tuple) else None
        self.local_address = own[0] if isinstance(own, tuple) else None
        self.closed = False

    async def read(self):
        """The next object, or None at the end. ProtocolError for a line longer than 32 MiB or
        one that is not a JSON object: the connection closes (the peer reconnects)."""
        try:
            line = await self.reader.readline()
        except (asyncio.LimitOverrunError, ValueError):
            raise ProtocolError("a line longer than 32 MiB") from None
        if not line or not line.endswith(b"\n"):
            return None      # the end, or the peer went away in the middle of a line
        try:
            obj = json.loads(line)
        except (UnicodeDecodeError, ValueError, RecursionError) as e:
            raise ProtocolError(f"a line that is not JSON ({e})") from None
        if not isinstance(obj, dict):
            raise ProtocolError("a line that is not a JSON object")
        return obj

    def send(self, obj):
        if self.closed:
            return
        try:
            self.writer.write(json.dumps(obj, separators=(",", ":")).encode() + b"\n")
        except (ConnectionError, RuntimeError):
            self.close()

    def backlog(self):
        transport = self.writer.transport
        return transport.get_write_buffer_size() if transport is not None else 0

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.writer.close()
            except (ConnectionError, RuntimeError):
                pass


# ---------------------------------------------------------------------------------------------
# The core: this speaker's revision and parameter values, its files and its status

class Core:
    def __init__(self, args, config):
        self.args = args
        self.name, self.is_hub = config["name"], config["hub"]
        self.listen = args.listen or config.get("listen") or "0.0.0.0"
        self.hub_address = args.hub_address or config.get("hub_address")
        self.state_dir = args.state_dir
        runtime = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
        self.socket_path = args.socket or os.path.join(runtime, "speakerd", "control.sock")
        self.runtime_dir = args.runtime_dir or os.path.join(runtime, "ampd")
        self.device_role = self._device_role()
        # This speaker's revision (on the hub: the pair's), with the current parameter values.
        self.doc, self.rev_time, self.rev_origin = empty_document(), "", ""
        self.params = {}
        self.files = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="ampd-files")
        self.persist_event = None
        self.persist_dirty = self.persist_urgent = False
        self.last_persist = -math.inf
        self.played_since = None       # since when the revision plays with no speakerd restart
        self.promoted = None           # (time, origin, revision) last promoted to graph.lkg.json
        self.sd = self.monitor = self.role = None
        self.saved_seq = 0             # the hub's seq in graph.json, where it goes on from

    # Files ------------------------------------------------------------------------------------

    def _device_role(self):
        try:
            with open(os.path.join(self.state_dir, "device.json")) as f:
                role = json.load(f).get("role")
            return role if role in ("left", "right", "mono") else "mono"
        except (OSError, ValueError, AttributeError):
            return "mono"

    def read_layer(self, layer):
        """(document, time, origin) of a state layer, or None."""
        if layer == "passthrough":
            return empty_document(), "", ""
        name = LAYER_FILES.get(layer)
        if name is None:
            return None
        try:
            with open(os.path.join(self.state_dir, name)) as f:
                doc = json.load(f)
        except (OSError, ValueError):
            return None
        if check_document(doc) is not None:
            return None
        doc.setdefault("revision", 0)
        seq = doc.pop("seq", 0)
        if layer == "current" and good_int(seq, 0, SEQ_MASK):
            self.saved_seq = seq
        t, origin = doc.pop("time", ""), doc.pop("origin", "")
        return doc, (t if isinstance(t, str) else ""), (origin if isinstance(origin, str) else "")

    def load_at_start(self):
        """What this speaker plays until speakerd says: the first layer that reads."""
        for layer in ("current", "last-known-good", "factory"):
            found = self.read_layer(layer)
            if found is not None:
                self.set_current(*found)
                return layer
        self.set_current(empty_document(), "", "")
        return "passthrough"

    # The revision -------------------------------------------------------------------------------

    @property
    def revision(self):
        return self.doc.get("revision", 0)

    def key(self):
        return (self.rev_time, self.rev_origin, self.revision)

    def set_current(self, doc, rev_time, rev_origin):
        doc = copy.deepcopy(doc)
        for key in ("time", "origin", "seq"):
            doc.pop(key, None)
        self.doc, self.rev_time, self.rev_origin = doc, rev_time or "", rev_origin or ""
        self.params = {}
        for node in doc.get("nodes", []):
            for pid, value in (node.get("params") or {}).items():
                self.params[(node["uid"], pid)] = value

    def node(self, uid):
        for node in self.doc.get("nodes", []):
            if node.get("uid") == uid:
                return node
        return None

    def document(self, meta=False):
        """The current revision's document with the current parameter values folded into its
        nodes' params; with `meta`, also its time and origin (graph.json keeps them: speakerd
        ignores keys it does not know at the top of a document)."""
        doc = copy.deepcopy(self.doc)
        by_uid = {node["uid"]: node for node in doc.get("nodes", [])}
        for (uid, pid), value in self.params.items():
            if uid in by_uid:
                by_uid[uid].setdefault("params", {})[pid] = value
        if meta:
            doc["time"], doc["origin"] = self.rev_time, self.rev_origin
            if self.is_hub:
                doc["seq"] = self.role.seq     # the hub goes on from it after a restart
        return doc

    # Persistence ----------------------------------------------------------------------------------

    def changed(self, urgent=False):
        """The values (urgent: the revision) changed: graph.json follows, at once for a new
        revision, at most once a second for parameters."""
        self.persist_dirty = True
        self.persist_urgent = self.persist_urgent or urgent
        if self.persist_event is not None:
            self.persist_event.set()

    def graph_text(self):
        return json.dumps(self.document(meta=True), indent=1) + "\n"

    async def persist_loop(self):
        loop = asyncio.get_running_loop()
        path = os.path.join(self.state_dir, "graph.json")
        while True:
            await self.persist_event.wait()
            while not self.persist_urgent and time.monotonic() < self.last_persist + PERSIST_INTERVAL:
                await asyncio.sleep(max(0.005, min(0.05, self.last_persist + PERSIST_INTERVAL - time.monotonic())))
            self.persist_event.clear()
            if not self.persist_dirty:
                continue
            self.persist_dirty = self.persist_urgent = False
            try:
                await loop.run_in_executor(self.files, atomic_write, path, self.graph_text())
            except OSError as e:
                THROTTLED.say("persist", f"cannot write {path}: {e}")
            self.last_persist = time.monotonic()

    def flush(self):
        """At a stop: what is not written yet, now."""
        if self.persist_dirty:
            try:
                atomic_write(os.path.join(self.state_dir, "graph.json"), self.graph_text())
            except OSError as e:
                log(f"cannot write graph.json: {e}")

    def playing(self):
        """The current revision plays from now on (applied, or speakerd (re)connected)."""
        self.played_since = time.monotonic()

    async def lkg_loop(self):
        """graph.json becomes graph.lkg.json once its revision has played for --lkg-after
        seconds with no speakerd restart in between."""
        loop = asyncio.get_running_loop()
        path = os.path.join(self.state_dir, "graph.lkg.json")
        while True:
            await asyncio.sleep(min(1.0, max(0.05, self.args.lkg_after / 4)))
            sd = self.sd
            if not sd.connected or self.played_since is None or sd.layer != "current" \
                    or sd.revision != self.revision or self.promoted == self.key():
                continue
            if time.monotonic() - self.played_since < self.args.lkg_after:
                continue
            key = self.key()
            try:
                await loop.run_in_executor(self.files, atomic_write, path, self.graph_text())
                self.promoted = key
                log(f"promoted graph.json to graph.lkg.json: revision {key[2]} played {self.args.lkg_after:g} s "
                    "with no speakerd restart")
            except OSError as e:
                THROTTLED.say("lkg", f"cannot write {path}: {e}")

    # Status: $XDG_RUNTIME_DIR/ampd/status.json, for status and smoke ------------------------------

    def status(self):
        sd = self.sd
        out = {"ampd": VERSION, "pid": os.getpid(), "name": self.name, "role": "hub" if self.is_hub else "agent",
               "revision": self.revision, "time": self.rev_time, "origin": self.rev_origin,
               "speakerd": {"connected": sd.connected, "version": sd.version, "layer": sd.layer,
                            "revision": sd.revision, "socket": self.socket_path},
               "updated": now_iso()}
        out.update(self.role.status())
        return out

    def status_changed(self):
        try:
            os.makedirs(self.runtime_dir, exist_ok=True)
            atomic_write(os.path.join(self.runtime_dir, "status.json"), json.dumps(self.status(), indent=1) + "\n",
                         sync=False)
        except OSError as e:
            THROTTLED.say("status", f"cannot write the status file: {e}")


# ---------------------------------------------------------------------------------------------
# The local link: this speaker's speakerd

class Speakerd:
    def __init__(self, core):
        self.core = core
        self.conn = None
        self.connected = False
        self.version = self.layer = self.revision = None
        self.pending = {}            # revision -> [futures] of apply answers
        self.ever_connected = False
        self.once = Once()

    def send(self, obj):
        if self.conn is not None:
            self.conn.send(obj)

    def send_param(self, node, pid, value, origin):
        # seq 0: speakerd applies whatever its own ampd sends, in order (docs/protocol.md). ampd
        # orders the values itself (an agent by the hub's seq, the hub by arrival); the hub's seq
        # starts again when the hub restarts, which speakerd would take for old values.
        self.send({"op": "param", "node": node, "id": pid, "value": value, "seq": 0, "origin": origin})

    async def apply(self, revision, document):
        """Hands a revision to speakerd: (True, None) once it plays, (False, reason) if not."""
        if not self.connected:
            return False, "speakerd is not running"
        future = asyncio.get_running_loop().create_future()
        self.pending.setdefault(revision, []).append(future)
        self.send({"op": "apply", "revision": revision, "document": document})
        try:
            return await future
        finally:
            futures = self.pending.get(revision, [])
            if future in futures:
                futures.remove(future)
            if not futures:
                self.pending.pop(revision, None)

    async def run(self):
        path = self.core.socket_path
        while True:
            try:
                reader, writer = await asyncio.open_unix_connection(path, limit=LINE_LIMIT)
            except OSError as e:
                self.once.say("connect", f"speakerd: cannot connect to {path}: {e.strerror or e}: trying again every second")
                await asyncio.sleep(RETRY)
                continue
            conn = Conn(reader, writer)
            try:
                hello = await asyncio.wait_for(conn.read(), HELLO_TIMEOUT)
            except (asyncio.TimeoutError, ProtocolError, ConnectionError) as e:
                self.once.say("hello", f"speakerd: no hello on {path} ({e or 'timeout'}): trying again every second")
                conn.close()
                await asyncio.sleep(RETRY)
                continue
            if hello is not None and hello.get("op") == "error" and hello.get("reason") == "busy":
                self.once.say("busy", f"speakerd: busy: another client holds {path}: trying again every second")
                conn.close()
                await asyncio.sleep(RETRY)
                continue
            if hello is None or hello.get("op") != "hello":
                self.once.say("hello", f"speakerd: no hello on {path} (it said {json.dumps(hello)[:200]}): "
                                       "trying again every second")
                conn.close()
                await asyncio.sleep(RETRY)
                continue
            self.once.reset()
            self.conn, self.connected = conn, True
            reconnect, self.ever_connected = self.ever_connected, True
            try:
                self.core.role.on_speakerd(hello, reconnect)
                while True:
                    obj = await conn.read()
                    if obj is None:
                        break
                    self.dispatch(obj)
            except ProtocolError as e:
                log(f"speakerd: {e}: closing the connection")
            except ConnectionError:
                pass
            finally:
                conn.close()
                self.conn, self.connected = None, False
                for futures in list(self.pending.values()):
                    for future in futures:
                        if not future.done():
                            future.set_result((False, "speakerd went away"))
                self.core.played_since = None
                if not STOPPING:
                    log("speakerd: lost the connection: trying again every second")
                    self.core.role.on_speakerd_lost()
                    self.core.status_changed()
            await asyncio.sleep(RETRY)

    def hello(self, hello):
        self.version = hello.get("version")
        self.layer = hello.get("layer")
        revision = hello.get("revision")
        self.revision = revision if good_int(revision, 0, BIG) else 0

    def dispatch(self, obj):
        op = obj.get("op")
        if op in ("applied", "failed"):
            revision = obj.get("revision")
            result = (True, None) if op == "applied" else (False, str(obj.get("reason") or "failed"))
            if op == "applied" and good_int(revision, 0, BIG):
                self.layer, self.revision = "current", revision
            futures = self.pending.get(revision)
            if futures:
                for future in futures:
                    if not future.done():
                        future.set_result(result)
            else:
                self.core.role.late_answer(revision, result)
            self.core.status_changed()
        elif op == "value":
            self.core.monitor.on_value(obj)
        elif op == "error":
            THROTTLED.say(("speakerd-error", obj.get("about"), obj.get("node"), obj.get("id")),
                          f"speakerd: error: {obj.get('about', '?')}"
                          + (f" {obj.get('node')}/{obj.get('id')}" if "id" in obj else "")
                          + f": {obj.get('reason', '?')}")
        elif op in ("hello", "status"):
            self.hello(obj)
            self.core.status_changed()


# ---------------------------------------------------------------------------------------------
# What both roles do with speakerd

class Role:
    def __init__(self, core):
        self.core = core

    def on_speakerd(self, hello, reconnect):
        """speakerd said hello. When it plays another revision than ampd's (it fell back to an
        older layer, or ampd started on a file speakerd did not load), ampd follows it. When it
        plays ampd's and restarted under a running ampd, it gets the values again (graph.json
        lags them by up to a second). A fresh ampd sends none: speakerd's are the newest."""
        core, sd = self.core, self.core.sd
        sd.hello(hello)
        log(f"speakerd: connected: {sd.version}, layer {sd.layer}, revision {sd.revision}")
        followed = False
        if sd.layer == "current" and sd.revision != core.revision:
            # Not a fall back: speakerd plays a graph.json older than ampd's (it ran on while ampd
            # wrote a newer one with the link down). It gets ampd's revision.
            log(f"speakerd: plays revision {sd.revision} of the current layer, not revision {core.revision}: "
                f"applying revision {core.revision}")
            background(self.apply_current())
        elif sd.revision != core.revision or (sd.layer == "passthrough" and core.doc.get("nodes")):
            found = core.read_layer(sd.layer)
            if found is not None and found[0].get("revision", 0) == sd.revision:
                log(f"speakerd: plays layer {sd.layer}, revision {sd.revision}, not revision {core.revision}: following it")
                core.set_current(*found)
                followed = True
            else:
                log(f"speakerd: plays layer {sd.layer}, revision {sd.revision}, whose document is not in the state "
                    f"directory: keeping revision {core.revision}")
        elif reconnect:
            for (uid, pid), value in core.params.items():
                sd.send_param(uid, pid, value, "hub" if core.is_hub else core.name)
        core.playing()
        core.monitor.resubscribe()
        if followed:
            self.revision_followed()
        core.status_changed()

    async def apply_current(self):
        core = self.core
        ok, why = await core.sd.apply(core.revision, core.document())
        if ok:
            core.playing()
            log(f"revision {core.revision}: applied")
        else:
            log(f"revision {core.revision}: failed: {why}")
        self.on_applied_locally()
        core.status_changed()

    def on_applied_locally(self):
        pass

    def on_speakerd_lost(self):
        pass

    def revision_followed(self):
        pass

    def late_answer(self, revision, result):
        pass

    def status(self):
        return {}


# ---------------------------------------------------------------------------------------------
# The hub

class Peer:
    """A controller or an agent connected to the hub."""

    def __init__(self, conn, hello, role):
        self.conn, self.role = conn, role
        name = hello.get("name")
        self.name = name if isinstance(name, str) and name else f"{role}@{conn.address}"
        port = hello.get("osc_port", KNOB_PORT if role == "agent" else None)
        self.osc = (conn.address, port) if good_int(port, 1, 65535) and conn.address else None
        self.pending = {}            # revision -> future of an apply's answer (agents)
        self.get_future = None


class Hub(Role):
    def __init__(self, core):
        super().__init__(core)
        self.controllers = []
        self.agents = {}             # name -> Peer, online
        self.speakers = {}           # agent name -> {"name", "role", "online", "layer", "revision"}
        self.seq = 0
        self.gestures = {}           # (node, id) -> {"origin", "deadline"}
        self.seen = set()            # the plugins some speaker has loaded
        self.lock = None             # one revision at a time: proposals, catch-ups, adoptions
        self.udp = None
        self.bad = Throttled()

    # speakerd ------------------------------------------------------------------------------------

    def on_speakerd(self, hello, reconnect):
        super().on_speakerd(hello, reconnect)
        if self.core.sd.layer != "passthrough":
            self.seen |= plugins_of(self.core.doc)
        self.broadcast_state()

    def on_speakerd_lost(self):
        self.broadcast_state()

    def on_applied_locally(self):
        self.broadcast_state()

    def revision_followed(self):
        """The hub's speakerd plays another revision (it fell back): that is the pair's now."""
        self.broadcast_graph()
        background(self.push_current_to_agents("the hub's speakerd fell back to it"))

    def late_answer(self, revision, result):
        core = self.core
        if result[0] and revision != core.revision:
            log(f"speakerd: applied revision {revision} after it was given up: applying revision {core.revision} again")
            background(core.sd.apply(core.revision, core.document()))

    # State -----------------------------------------------------------------------------------------

    def speaker_list(self, conn=None):
        """The speakers, the hub first. Each has the address its monitor link is reached at: the
        agent's from its connection, the hub's own from its configuration, or, when it listens on
        every address, the one `conn` (a controller's connection) reached it at."""
        sd, core = self.core.sd, self.core
        own_address = core.listen if core.listen not in ("0.0.0.0", "::", "") else (conn.local_address if conn else None)
        own = {"name": core.name, "role": core.device_role, "address": own_address, "online": True,
               "speakerd": sd.connected, "layer": sd.layer, "revision": sd.revision}
        return [own] + [dict(s) for _, s in sorted(self.speakers.items())]

    def gesture_list(self):
        return [{"node": node, "id": pid, "origin": g["origin"]} for (node, pid), g in self.gestures.items()]

    def state(self, conn=None):
        core = self.core
        return {"op": "state", "revision": core.revision, "seq": self.seq, "time": core.rev_time,
                "origin": core.rev_origin, "document": core.document(), "speakers": self.speaker_list(conn),
                "gestures": self.gesture_list()}

    def broadcast_graph(self, base=None):
        core = self.core
        message = {"op": "graph", "revision": core.revision, "base": core.revision - 1 if base is None else base,
                   "time": core.rev_time, "origin": core.rev_origin, "document": core.document()}
        for controller in self.controllers:
            controller.conn.send(message)

    def broadcast_state(self):
        """A speaker came online, went offline or changed layer, or the hub adopted a revision:
        every controller gets the whole state again (docs/protocol.md, clarification 2)."""
        for controller in self.controllers:
            controller.conn.send(self.state(controller.conn))
        self.core.status_changed()

    def status(self):
        return {"controllers": [c.name for c in self.controllers],
                "agents": [dict(s) for _, s in sorted(self.speakers.items())],
                "seq": self.seq, "gestures": len(self.gestures)}

    def osc(self, peer, datagram):
        if self.udp is not None and peer.osc is not None:
            try:
                self.udp.sendto(datagram, peer.osc)
            except OSError as e:
                self.bad.say(("osc-out", peer.name), f"cannot send OSC to {peer.name}: {e}")

    # Parameters ---------------------------------------------------------------------------------------

    def addressable(self, node, pid):
        core = self.core
        if pid.startswith("mon_"):
            return f"{pid} is a monitor: it is never set"
        found = core.node(node)
        if found is None:
            return f"no node {node} in revision {core.revision}"
        if pid not in (found.get("params") or {}):
            return f"node {node} has no parameter {pid} in revision {core.revision}"
        return None

    def param(self, msg, via, source=None):
        core = self.core
        node, pid, value, origin = msg.get("node"), msg.get("id"), msg.get("value"), msg.get("origin")
        if not good_int(node, 1, SEQ_MASK) or not isinstance(pid, str) or not good_value(value):
            reason = "a param needs node (a whole number), id (text) and value (a number, a choice's text or a switch)"
        else:
            reason = self.addressable(node, pid)
        if reason is not None:
            if via == "control" and source is not None:
                source.conn.send({"op": "error", "about": "param", "node": node, "id": pid, "reason": reason})
            else:
                self.bad.say(("param", node, pid), f"knobs: refused a param {node}/{pid}: {reason}")
            return
        if not isinstance(origin, str) or not origin:
            origin = source.name if source is not None else "knob"
        self.seq = ((self.seq + 1) & SEQ_MASK) or 1
        core.params[(node, pid)] = value
        core.sd.send_param(node, pid, value, origin)
        out = {"op": "param", "node": node, "id": pid, "value": value, "seq": self.seq, "origin": origin}
        datagram = None
        if good_number(value) or isinstance(value, bool):
            datagram = osc_encode("/bpi/param", "isfis", node, pid, float(value), self.seq, origin)
        for peer in list(self.agents.values()) + self.controllers:
            if datagram is not None:
                self.osc(peer, datagram)
            if via == "control" or datagram is None:
                peer.conn.send(out)
        gesture = self.gestures.get((node, pid))
        if gesture is not None and gesture["origin"] == origin:
            gesture["deadline"] = time.monotonic() + GESTURE_TIMEOUT
        core.changed()

    def all_params(self, peer):
        for (node, pid), value in self.core.params.items():
            peer.conn.send({"op": "param", "node": node, "id": pid, "value": value, "seq": self.seq, "origin": "hub"})

    # Gestures -------------------------------------------------------------------------------------------

    def gesture(self, msg, via, source=None):
        node, pid, state, origin = msg.get("node"), msg.get("id"), msg.get("state"), msg.get("origin")
        if not good_int(node, 1, SEQ_MASK) or not isinstance(pid, str) or state not in ("begin", "end"):
            if via == "control" and source is not None:
                source.conn.send({"op": "error", "about": "gesture", "node": node, "id": pid,
                                  "reason": "a gesture needs node, id and state (begin or end)"})
            return
        if not isinstance(origin, str) or not origin:
            origin = source.name if source is not None else "knob"
        if state == "begin":
            self.gestures[(node, pid)] = {"origin": origin, "deadline": time.monotonic() + GESTURE_TIMEOUT}
        elif (node, pid) in self.gestures and self.gestures[(node, pid)]["origin"] == origin:
            del self.gestures[(node, pid)]
        self.relay_gesture(node, pid, state, origin, via)

    def relay_gesture(self, node, pid, state, origin, via):
        out = {"op": "gesture", "node": node, "id": pid, "state": state, "origin": origin}
        datagram = osc_encode("/bpi/gesture", "isss", node, pid, state, origin)
        for controller in self.controllers:
            self.osc(controller, datagram)
            if via == "control":
                controller.conn.send(out)

    async def gesture_loop(self):
        while True:
            await asyncio.sleep(0.1)
            now = time.monotonic()
            for (node, pid), g in list(self.gestures.items()):
                if g["deadline"] <= now:
                    del self.gestures[(node, pid)]
                    log(f"gesture on {node}/{pid} by {g['origin']}: no end for {GESTURE_TIMEOUT:g} s: ended")
                    self.relay_gesture(node, pid, "end", g["origin"], "control")

    # Revisions ---------------------------------------------------------------------------------------------

    async def apply_on(self, peer, revision, document, rev_time, rev_origin):
        """(True, None) once agent `peer` says its speakerd plays `revision`, else (False, why)."""
        if peer.conn.closed:
            return False, "offline"
        future = asyncio.get_running_loop().create_future()
        peer.pending[revision] = future
        peer.conn.send({"op": "apply", "revision": revision, "document": document,
                        "time": rev_time, "origin": rev_origin})
        try:
            return await asyncio.wait_for(asyncio.shield(future), APPLY_TIMEOUT)
        except asyncio.TimeoutError:
            return False, f"no answer within {APPLY_TIMEOUT:g} s"
        finally:
            if peer.pending.get(revision) is future:
                del peer.pending[revision]

    async def apply_here(self, revision, document):
        try:
            return await asyncio.wait_for(self.core.sd.apply(revision, document), APPLY_TIMEOUT)
        except asyncio.TimeoutError:
            return False, f"no answer within {APPLY_TIMEOUT:g} s"

    async def apply_everywhere(self, revision, document, rev_time, rev_origin, agents=None):
        """Applies on every speaker that is up, at once: {speaker name: (ok, why)}."""
        jobs = {}
        if self.core.sd.connected:
            jobs[self.core.name] = self.apply_here(revision, document)
        for peer in (list(self.agents.values()) if agents is None else agents):
            jobs[peer.name] = self.apply_on(peer, revision, document, rev_time, rev_origin)
        results = await asyncio.gather(*jobs.values())
        return dict(zip(jobs.keys(), results))

    async def propose(self, controller, msg):
        core = self.core
        revision, base, document = msg.get("revision"), msg.get("base"), msg.get("document")

        def refuse(reason):
            log(f"revision {revision} from {controller.name}: rejected: {reason}")
            controller.conn.send({"op": "rejected", "revision": revision, "reason": reason})

        async with self.lock:
            if not good_int(base, 0, BIG) or base != core.revision:
                return refuse(f"stale: the current revision is {core.revision}")
            if revision != base + 1:
                return refuse(f"stale: a proposal is revision base + 1 ({base + 1})")
            why = check_document(document)
            if why is not None:
                return refuse(f"the document does not parse: {why}")
            if document.get("revision", revision) != revision:
                return refuse(f"the document's revision ({document.get('revision')}) is not the proposal's ({revision})")
            missing = sorted(plugins_of(document) - self.seen)
            if missing:
                return refuse("no speaker has loaded " + ", ".join(missing))
            rev_time = msg["time"] if isinstance(msg.get("time"), str) and TIME_FORMAT.match(msg["time"]) else now_iso()
            rev_origin = msg["origin"] if isinstance(msg.get("origin"), str) and msg["origin"] else controller.name
            doc = copy.deepcopy(document)
            doc["revision"] = revision
            doc.pop("time", None)
            doc.pop("origin", None)
            up = ([core.name] if core.sd.connected else []) + sorted(self.agents)
            log(f"revision {revision} from {rev_origin}: applying on {', '.join(up) or 'no speaker (none is up)'}")
            results = await self.apply_everywhere(revision, doc, rev_time, rev_origin)
            failed = {name: why for name, (ok, why) in results.items() if not ok}
            if not failed:
                core.set_current(doc, rev_time, rev_origin)
                core.changed(urgent=True)
                core.playing()
                self.seen |= plugins_of(doc)
                for name in results:
                    if name in self.speakers:
                        self.speakers[name].update(layer="current", revision=revision)
                log(f"revision {revision}: applied on {', '.join(sorted(results)) or 'no speaker'}: current")
                controller.conn.send({"op": "applied", "revision": revision})
                self.broadcast_graph(base)
                self.broadcast_state()
                return None
            refuse("; ".join(f"{name}: {why}" for name, why in sorted(failed.items())))
            applied = sorted(name for name, (ok, _) in results.items() if ok)
            if applied:
                log(f"revision {revision}: rolling back to revision {core.revision} on {', '.join(applied)}")
                back = core.document()
                jobs = []
                if core.name in applied:
                    jobs.append((core.name, self.apply_here(core.revision, back)))
                for name in applied:
                    if name in self.agents:
                        jobs.append((name, self.apply_on(self.agents[name], core.revision, back,
                                                         core.rev_time, core.rev_origin)))
                outcomes = await asyncio.gather(*(job for _, job in jobs))
                for (name, _), (ok, why) in zip(jobs, outcomes):
                    if not ok:
                        log(f"revision {core.revision}: the roll back failed on {name}: {why}")
            return None

    async def push_current_to_agents(self, why):
        async with self.lock:
            core = self.core
            for peer in list(self.agents.values()):
                log(f"agent {peer.name}: applying revision {core.revision} ({why})")
                ok, reason = await self.apply_on(peer, core.revision, core.document(), core.rev_time, core.rev_origin)
                if ok:
                    self.speakers.get(peer.name, {}).update(layer="current", revision=core.revision)
                    self.all_params(peer)
                else:
                    log(f"agent {peer.name}: revision {core.revision} failed: {reason}")
            self.broadcast_state()

    async def sync_agent(self, peer, hello):
        """An agent (re)connected: catch it up, or adopt its revision when it is newer."""
        core = self.core
        async with self.lock:
            if peer.conn.closed:
                return
            a_rev = hello.get("revision") if good_int(hello.get("revision"), 0, BIG) else 0
            a_time = hello.get("time") if isinstance(hello.get("time"), str) else ""
            a_origin = hello.get("origin") if isinstance(hello.get("origin"), str) else ""
            if (a_time, a_origin) > (core.rev_time, core.rev_origin) and await self.adopt(peer, a_rev):
                return
            if (a_rev, a_time, a_origin) != (core.revision, core.rev_time, core.rev_origin):
                log(f"agent {peer.name}: plays revision {a_rev}, not revision {core.revision}: sending revision "
                    f"{core.revision} and {len(core.params)} parameter(s)")
                ok, why = await self.apply_on(peer, core.revision, core.document(), core.rev_time, core.rev_origin)
                if not ok:
                    log(f"agent {peer.name}: revision {core.revision} failed there: {why}")
                    self.speakers.get(peer.name, {}).update(layer="failed")
                    self.broadcast_state()
                    return
                self.seen |= plugins_of(core.doc)
                self.speakers.get(peer.name, {}).update(layer="current", revision=core.revision)
            else:
                log(f"agent {peer.name}: plays revision {core.revision}: sending {len(core.params)} parameter(s)")
            self.all_params(peer)
            self.broadcast_state()

    async def adopt(self, peer, a_rev):
        """The agent's revision is newer (it changed while the hub was off): the hub takes it and
        applies it everywhere. False when it cannot: then the hub's revision goes to the agent."""
        core = self.core
        log(f"agent {peer.name}: revision {a_rev} is newer than revision {core.revision}: adopting it")
        peer.get_future = asyncio.get_running_loop().create_future()
        peer.conn.send({"op": "get"})
        try:
            graph = await asyncio.wait_for(peer.get_future, APPLY_TIMEOUT)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            log(f"agent {peer.name}: no answer to get: keeping revision {core.revision}")
            return False
        finally:
            peer.get_future = None
        document = graph.get("document")
        why = check_document(document)
        if why is not None:
            log(f"agent {peer.name}: its revision does not parse ({why}): keeping revision {core.revision}")
            return False
        revision = graph["revision"] if good_int(graph.get("revision"), 0, BIG) else document.get("revision", 0)
        doc = copy.deepcopy(document)
        doc["revision"] = revision
        rev_time = graph["time"] if isinstance(graph.get("time"), str) else ""
        rev_origin = graph["origin"] if isinstance(graph.get("origin"), str) else ""
        others = [p for p in self.agents.values() if p is not peer]
        results = await self.apply_everywhere(revision, doc, rev_time, rev_origin, agents=others)
        if not results.get(core.name, (True, None))[0]:
            log(f"agent {peer.name}: revision {revision} failed on {core.name}: {results[core.name][1]}: "
                f"keeping revision {core.revision}")
            return False
        core.set_current(doc, rev_time, rev_origin)
        core.changed(urgent=True)
        core.playing()
        self.seen |= plugins_of(doc)
        self.speakers.get(peer.name, {}).update(layer="current", revision=revision)
        log(f"revision {revision} of {peer.name} adopted: current")
        self.broadcast_graph()
        self.broadcast_state()
        return True

    # Connections ---------------------------------------------------------------------------------------------

    async def serve(self, conn, hello):
        role = hello.get("role")
        if role == "controller":
            await self.serve_controller(conn, hello)
        elif role == "agent":
            await self.serve_agent(conn, hello)
        else:
            conn.send({"op": "error", "about": "hello", "reason": f"no role {role!r}: controller or agent"})

    async def serve_controller(self, conn, hello):
        peer = Peer(conn, hello, "controller")
        self.controllers.append(peer)
        log(f"controller {peer.name} connected from {conn.address}"
            + (f" (osc port {peer.osc[1]})" if peer.osc else " (no osc_port: no OSC echoes)"))
        conn.send(self.state(conn))
        self.core.status_changed()
        tasks = set()
        try:
            while True:
                msg = await conn.read()
                if msg is None:
                    break
                op = msg.get("op")
                if op == "param":
                    self.param(msg, "control", peer)
                elif op == "gesture":
                    self.gesture(msg, "control", peer)
                elif op == "graph":
                    task = asyncio.get_running_loop().create_task(self.propose(peer, msg))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
        finally:
            self.controllers.remove(peer)
            for (node, pid), g in list(self.gestures.items()):
                if g["origin"] == peer.name:
                    del self.gestures[(node, pid)]
                    self.relay_gesture(node, pid, "end", peer.name, "control")
            if not STOPPING:
                log(f"controller {peer.name} disconnected")
                self.core.status_changed()

    async def serve_agent(self, conn, hello):
        peer = Peer(conn, hello, "agent")
        old = self.agents.get(peer.name)
        if old is not None:
            log(f"agent {peer.name}: a new connection replaces the old one")
            old.conn.close()
        self.agents[peer.name] = peer
        self.speakers[peer.name] = {"name": peer.name, "role": hello.get("device_role", "unknown"),
                                    "address": conn.address, "online": True, "speakerd": hello.get("speakerd"),
                                    "layer": hello.get("layer"), "revision": hello.get("revision")}
        log(f"agent {peer.name} connected from {conn.address}: revision {hello.get('revision')} "
            f"({hello.get('time') or 'no time'}, {hello.get('origin') or 'no origin'}), layer {hello.get('layer')}")
        background(self.sync_agent(peer, hello))
        try:
            while True:
                msg = await conn.read()
                if msg is None:
                    break
                op = msg.get("op")
                if op in ("applied", "failed"):
                    revision = msg.get("revision")
                    result = (True, None) if op == "applied" else (False, str(msg.get("reason") or "failed"))
                    future = peer.pending.get(revision)
                    if future is not None and not future.done():
                        future.set_result(result)
                    elif result[0] and revision != self.core.revision:
                        log(f"agent {peer.name}: applied revision {revision} after it was given up")
                        background(self.push_current_to_agents(f"{peer.name} applied revision {revision} late"))
                elif op == "graph":
                    if peer.get_future is not None and not peer.get_future.done():
                        peer.get_future.set_result(msg)
                elif op == "status":
                    news = {k: msg.get(k) for k in ("speakerd", "layer", "revision")}
                    if any(self.speakers[peer.name].get(k) != v for k, v in news.items()):
                        self.speakers[peer.name].update(news)
                        self.broadcast_state()
                elif op == "error":
                    self.bad.say(("agent-error", peer.name),
                                 f"agent {peer.name}: error: {msg.get('about', '?')}: {msg.get('reason', '?')}")
        finally:
            conn.close()
            for future in peer.pending.values():
                if not future.done():
                    future.set_result((False, "went away"))
            if peer.get_future is not None and not peer.get_future.done():
                peer.get_future.cancel()
            if self.agents.get(peer.name) is peer:
                del self.agents[peer.name]
                self.speakers[peer.name]["online"] = False
                if not STOPPING:
                    log(f"agent {peer.name} disconnected: offline")
                    self.broadcast_state()

    def datagram(self, data, address):
        try:
            path, args = osc_decode(data)
        except ValueError as e:
            self.bad.say(("osc", address[0]), f"knobs: {e} from {address[0]}")
            return
        kinds = [type(a) for a in args]
        if path == "/bpi/param" and kinds[:2] == [int, str] and len(args) == 5 and good_number(args[2]) \
                and kinds[4] is str:
            self.param({"node": args[0], "id": args[1], "value": float(args[2]), "seq": args[3], "origin": args[4]}, "osc")
        elif path == "/bpi/gesture" and len(args) == 4 and kinds == [int, str, str, str]:
            self.gesture({"node": args[0], "id": args[1], "state": args[2], "origin": args[3]}, "osc")
        else:
            self.bad.say(("osc-what", path), f"knobs: ignored {path} with {len(args)} argument(s) from {address[0]}")


# ---------------------------------------------------------------------------------------------
# The agent

class Agent(Role):
    def __init__(self, core):
        super().__init__(core)
        self.conn = None
        self.hub_name = None
        self.last_seq = {}
        self.once = Once()

    def status(self):
        return {"hub": {"address": self.core.hub_address, "connected": self.conn is not None, "name": self.hub_name}}

    def report(self):
        """What this speaker's speakerd plays, to the hub (not in protocol 1's examples: an op
        the hub uses for the speakers of its state)."""
        if self.conn is not None:
            sd = self.core.sd
            self.conn.send({"op": "status", "speakerd": sd.connected, "layer": sd.layer, "revision": sd.revision})

    def on_speakerd(self, hello, reconnect):
        super().on_speakerd(hello, reconnect)
        self.report()

    def on_speakerd_lost(self):
        self.report()

    def on_applied_locally(self):
        self.report()

    def revision_followed(self):
        """speakerd plays another revision (it fell back): hello to the hub again, which then
        catches this speaker up, or adopts its revision."""
        if self.conn is not None:
            log(f"hub: saying hello again, with revision {self.core.revision}")
            self.conn.close()

    def param(self, msg, via):
        core = self.core
        node, pid, value, seq = msg.get("node"), msg.get("id"), msg.get("value"), msg.get("seq")
        if not good_int(node, 1, SEQ_MASK) or not isinstance(pid, str) or not good_value(value) \
                or not good_int(seq, 0, SEQ_MASK) or pid.startswith("mon_"):
            return
        last = self.last_seq.get((node, pid))
        # A change that came to the hub over the control link comes here twice with one seq: over
        # OSC, as a 32-bit float, and over the control link, exact. The exact one wins.
        exact = via == "control" and seq != 0 and seq == last and core.params.get((node, pid)) != value
        if not newer(seq, last) and not exact:
            return
        self.last_seq[(node, pid)] = seq
        core.params[(node, pid)] = value
        origin = msg.get("origin") if isinstance(msg.get("origin"), str) else "hub"
        core.sd.send_param(node, pid, value, origin)
        core.changed()

    def datagram(self, data, address):
        try:
            path, args = osc_decode(data)
        except ValueError as e:
            THROTTLED.say(("osc", address[0]), f"knobs: {e} from {address[0]}")
            return
        if path == "/bpi/param" and len(args) == 5 and isinstance(args[0], int) and isinstance(args[1], str) \
                and good_number(args[2]) and isinstance(args[3], int):
            self.param({"node": args[0], "id": args[1], "value": float(args[2]), "seq": args[3] & SEQ_MASK,
                        "origin": args[4]}, "osc")

    async def apply(self, conn, msg):
        core = self.core
        revision, document = msg.get("revision"), msg.get("document")
        why = check_document(document) if good_int(revision, 0, BIG) else "no revision"
        if why is not None:
            conn.send({"op": "failed", "revision": revision, "reason": f"the document does not parse: {why}"})
            return
        doc = copy.deepcopy(document)
        doc["revision"] = revision
        rev_time = msg["time"] if isinstance(msg.get("time"), str) else document.get("time", "")
        rev_origin = msg["origin"] if isinstance(msg.get("origin"), str) else document.get("origin", "")
        ok, reason = await core.sd.apply(revision, doc)
        if ok:
            core.set_current(doc, rev_time, rev_origin)
            core.changed(urgent=True)
            core.playing()
            log(f"revision {revision}: applied")
            conn.send({"op": "applied", "revision": revision})
        else:
            log(f"revision {revision}: failed: {reason}")
            conn.send({"op": "failed", "revision": revision, "reason": reason})
        core.status_changed()

    def late_answer(self, revision, result):
        if result[0] and self.conn is not None:
            self.conn.send({"op": "applied", "revision": revision})

    async def run(self):
        core = self.core
        host, port = core.hub_address, core.args.hub_control_port
        local_addr = None if core.listen in ("0.0.0.0", "::", "") else (core.listen, 0)
        while True:
            try:
                reader, writer = await asyncio.open_connection(host, port, limit=LINE_LIMIT, local_addr=local_addr)
            except OSError as e:
                self.once.say("connect", f"hub: cannot connect to {host}:{port}: {e.strerror or e}: trying again every second")
                await asyncio.sleep(RETRY)
                continue
            conn = Conn(reader, writer)
            conn.send({"op": "hello", "role": "agent", "name": core.name, "revision": core.revision,
                       "time": core.rev_time, "origin": core.rev_origin, "protocol": PROTOCOL,
                       "layer": core.sd.layer, "speakerd": core.sd.connected, "device_role": core.device_role,
                       "osc_port": core.args.knob_port})
            tasks = set()
            try:
                hello = await asyncio.wait_for(conn.read(), HELLO_TIMEOUT)
                if hello is None or hello.get("op") != "hello" or hello.get("role") != "hub":
                    raise ProtocolError(f"the hub said {json.dumps(hello)[:200]}, not its hello")
                self.once.reset()
                self.hub_name = hello.get("name")
                self.last_seq.clear()    # a new hub session: its seq may have started again
                self.conn = conn
                log(f"hub: connected to {host}:{port} (hub {self.hub_name}, revision {hello.get('revision')})")
                self.report()      # speakerd may have come or gone since the hello
                core.status_changed()
                while True:
                    msg = await conn.read()
                    if msg is None:
                        break
                    op = msg.get("op")
                    if op == "param":
                        self.param(msg, "control")
                    elif op == "apply":
                        task = asyncio.get_running_loop().create_task(self.apply(conn, msg))
                        tasks.add(task)
                        task.add_done_callback(tasks.discard)
                    elif op == "get":
                        conn.send({"op": "graph", "revision": core.revision, "time": core.rev_time,
                                   "origin": core.rev_origin, "document": core.document()})
                    elif op == "error":
                        THROTTLED.say(("hub-error", msg.get("about")),
                                      f"hub: error: {msg.get('about', '?')}: {msg.get('reason', '?')}")
            except asyncio.TimeoutError:
                self.once.say("hello", f"hub: {host}:{port} said no hello within {HELLO_TIMEOUT:g} s: trying again every second")
            except ProtocolError as e:
                self.once.say("hello", f"hub: {e}: trying again every second")
            except ConnectionError:
                pass
            finally:
                conn.close()
                was = self.conn is conn
                self.conn = None
                if was and not STOPPING:
                    log("hub: lost the connection: trying again every second")
                    core.status_changed()
            await asyncio.sleep(RETRY)


# ---------------------------------------------------------------------------------------------
# Monitoring: subscriptions go to speakerd as their union, values to each client newest-wins

class MonitorClient:
    def __init__(self, conn):
        self.conn = conn
        self.subs = {}       # stream -> rate in Hz
        self.pumps = {}      # stream -> task


class Monitor:
    def __init__(self, core):
        self.core = core
        self.clients = []
        self.latest = {}     # stream -> the newest value line from speakerd
        self.union = {}      # stream -> the rate speakerd was asked for

    def on_value(self, obj):
        stream = obj.get("stream")
        if isinstance(stream, str) and stream in self.union:
            self.latest[stream] = obj

    def update_union(self):
        want = {}
        for client in self.clients:
            for stream, rate in client.subs.items():
                want[stream] = max(want.get(stream, 0), rate)
        sd = self.core.sd
        for stream, rate in want.items():
            if self.union.get(stream) != rate:
                sd.send({"op": "subscribe", "stream": stream, "rate_hz": rate})
        for stream in self.union:
            if stream not in want:
                sd.send({"op": "unsubscribe", "stream": stream})
                self.latest.pop(stream, None)
        self.union = want

    def resubscribe(self):
        """speakerd (re)connected: it knows no subscription."""
        for stream, rate in self.union.items():
            self.core.sd.send({"op": "subscribe", "stream": stream, "rate_hz": rate})

    async def pump(self, client, stream):
        """Sends `stream`'s newest value to `client` at the client's rate, when there is one it
        has not had: newest wins, a value it has not taken yet is replaced, never queued."""
        sent, due = None, time.monotonic()
        while stream in client.subs and not client.conn.closed:
            value = self.latest.get(stream)
            if value is not None and value is not sent and client.conn.backlog() < MONITOR_BACKLOG:
                client.conn.send(value)
                sent = value
            due = max(due + 1.0 / client.subs.get(stream, 1.0), time.monotonic() - 0.5)
            await asyncio.sleep(max(0.0, due - time.monotonic()))

    async def serve(self, reader, writer):
        conn = Conn(reader, writer)
        client = MonitorClient(conn)
        self.clients.append(client)
        try:
            while True:
                msg = await conn.read()
                if msg is None:
                    break
                op, stream = msg.get("op"), msg.get("stream")
                if op == "subscribe":
                    if not isinstance(stream, str) or not STREAM.match(stream):
                        conn.send({"op": "error", "about": "subscribe", "stream": stream,
                                   "reason": "a stream is output/level or node/<uid>/mon_<id>"})
                        continue
                    rate = msg.get("rate_hz", 30)
                    rate = max(min(float(rate), MAX_MONITOR_RATE), 0.1) if good_number(rate) and rate > 0 else 30.0
                    fresh = stream not in client.subs
                    client.subs[stream] = rate
                    if fresh:
                        client.pumps[stream] = asyncio.get_running_loop().create_task(self.pump(client, stream))
                    self.update_union()
                elif op == "unsubscribe":
                    if client.subs.pop(stream, None) is not None:
                        client.pumps.pop(stream).cancel()
                        self.update_union()
        except (ProtocolError, ConnectionError):
            pass
        finally:
            conn.close()
            self.clients.remove(client)
            for task in client.pumps.values():
                task.cancel()
            self.update_union()


# ---------------------------------------------------------------------------------------------
# The listeners and main

class Knobs(asyncio.DatagramProtocol):
    def __init__(self, role):
        self.role = role

    def datagram_received(self, data, address):
        self.role.datagram(data, address)

    def error_received(self, exc):
        pass


async def control_connection(core, reader, writer):
    conn = Conn(reader, writer)
    try:
        if core.is_hub:
            conn.send({"op": "hello", "role": "hub", "name": core.name, "revision": core.revision, "seq": core.role.seq,
                       "protocol": PROTOCOL})
            hello = await asyncio.wait_for(conn.read(), HELLO_TIMEOUT)
            if hello is None or hello.get("op") != "hello":
                conn.send({"op": "error", "about": "hello", "reason": "the first line is a hello"})
                return
            await core.role.serve(conn, hello)
        else:
            conn.send({"op": "error", "about": "hello", "reason": f"{core.name} is not the hub: the hub is {core.hub_address}"})
    except (asyncio.TimeoutError, ProtocolError, ConnectionError):
        pass
    finally:
        conn.close()


def read_config(path):
    """ampd.json (docs/protocol.md): {"version": 1, "name", "hub", "listen", "hub_address"}."""
    with open(path) as f:
        config = json.load(f)
    if not isinstance(config, dict):
        raise ValueError("not a JSON object")
    if config.get("version", 1) != 1:
        raise ValueError("version: 1 is the only one")
    unknown = sorted(set(config) - {"version", "name", "hub", "listen", "hub_address"})
    if unknown:
        raise ValueError("unknown key(s): " + ", ".join(unknown))
    if not isinstance(config.get("name"), str) or not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$", config["name"]):
        raise ValueError("name: letters, digits, '.', '_' or '-', at most 64")
    if not isinstance(config.get("hub"), bool):
        raise ValueError("hub: true or false")
    if not isinstance(config.get("listen", "0.0.0.0"), str):
        raise ValueError("listen: an address")
    address = config.get("hub_address")
    if address is not None and not isinstance(address, str):
        raise ValueError("hub_address: an address or null")
    if not config["hub"] and not address:
        raise ValueError("hub_address: an agent needs the hub's address")
    return config


async def main_async(args, config):
    core = Core(args, config)
    core.persist_event = asyncio.Event()
    core.sd = Speakerd(core)
    core.monitor = Monitor(core)
    core.role = Hub(core) if core.is_hub else Agent(core)
    if core.is_hub:
        core.role.lock = asyncio.Lock()
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        loop.add_signal_handler(sig, stop.set)

    log(f"ampd {VERSION}: {core.name}, " + ("the hub" if core.is_hub else f"an agent of the hub at {core.hub_address}")
        + f", state dir {core.state_dir}, speakerd socket {core.socket_path}")
    layer = core.load_at_start()
    if core.is_hub:
        core.role.seq = core.saved_seq
    log(f"starts on revision {core.revision} from {layer}"
        + (f" ({core.rev_time}, {core.rev_origin})" if core.rev_time else ""))
    handlers = set()

    def handler(serve):
        async def run(reader, writer):
            task = asyncio.current_task()
            handlers.add(task)
            try:
                await serve(reader, writer)
            finally:
                handlers.discard(task)
        return run

    try:
        control = await asyncio.start_server(handler(lambda r, w: control_connection(core, r, w)), core.listen,
                                             args.control_port, limit=LINE_LIMIT, reuse_address=True)
        monitor = await asyncio.start_server(handler(core.monitor.serve), core.listen, args.monitor_port,
                                             limit=LINE_LIMIT, reuse_address=True)
        transport, _ = await loop.create_datagram_endpoint(lambda: Knobs(core.role), local_addr=(core.listen, args.knob_port))
    except OSError as e:
        log(f"cannot listen on {core.listen}: {e.strerror or e}")
        return 3
    if core.is_hub:
        core.role.udp = transport
    log(f"listening on {core.listen}: control {args.control_port}, knobs {args.knob_port}, monitor {args.monitor_port}")

    tasks = [loop.create_task(core.sd.run()), loop.create_task(core.persist_loop()), loop.create_task(core.lkg_loop()),
             loop.create_task(core.role.gesture_loop() if core.is_hub else core.role.run())]
    core.status_changed()
    await stop.wait()
    STOPPING.append(True)
    control.close()
    monitor.close()
    for task in tasks + list(handlers):
        task.cancel()
    await asyncio.gather(*tasks, *handlers, return_exceptions=True)
    transport.close()
    core.flush()
    core.files.shutdown(wait=True)
    try:
        os.unlink(os.path.join(core.runtime_dir, "status.json"))
    except OSError:
        pass
    log("stopping")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ampd", description="The speaker's network agent, and the hub on the left speaker.")
    parser.add_argument("--version", action="version", version=f"ampd {VERSION} (protocol {PROTOCOL})")
    parser.add_argument("--config", required=True, help="ampd.json")
    parser.add_argument("--state-dir", help="speakerd's state directory (graph.json, device.json, ...): required")
    parser.add_argument("--check-config", action="store_true", help="read ampd.json, say what it says, and exit")
    parser.add_argument("--socket", help="speakerd's local socket (default $XDG_RUNTIME_DIR/speakerd/control.sock)")
    parser.add_argument("--runtime-dir", help="where status.json goes (default $XDG_RUNTIME_DIR/ampd)")
    parser.add_argument("--listen", help="instead of ampd.json's listen")
    parser.add_argument("--hub-address", help="instead of ampd.json's hub_address")
    parser.add_argument("--control-port", type=int, default=CONTROL_PORT)
    parser.add_argument("--knob-port", type=int, default=KNOB_PORT)
    parser.add_argument("--monitor-port", type=int, default=MONITOR_PORT)
    parser.add_argument("--hub-control-port", type=int, default=CONTROL_PORT, help="the hub's control port (an agent)")
    parser.add_argument("--lkg-after", type=float, default=LKG_AFTER,
                        help=f"seconds a revision plays with no speakerd restart before graph.json becomes "
                             f"graph.lkg.json (default {LKG_AFTER:g})")
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    if not args.lkg_after > 0:
        log("--lkg-after: more than 0 seconds")
        return 2
    try:
        config = read_config(args.config)
    except (OSError, ValueError) as e:
        log(f"config {args.config}: FAILED: {e}")
        return 2
    if args.check_config:
        print(f"{config['name']}: " + ("the hub" if config["hub"] else f"an agent of the hub at {config['hub_address']}")
              + f", listening on {config.get('listen', '0.0.0.0')}")
        return 0
    if not args.state_dir:
        log("--state-dir is required")
        return 2
    if not os.path.isdir(args.state_dir):
        log(f"no state directory {args.state_dir}")
        return 2
    try:
        return asyncio.run(main_async(args, config))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
