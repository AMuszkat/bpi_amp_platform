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
import collections
import concurrent.futures
import copy
import datetime
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import signal
import struct
import sys
import time

# The tweeter rule's one definition (firmware/speakerd/tweeter_rule.py), beside this file when
# installed (bin/), in ../speakerd in the repository and in a bundle.
_HERE = os.path.dirname(os.path.realpath(__file__))
for _folder in (_HERE, os.path.join(os.path.dirname(_HERE), "speakerd")):
    if os.path.isfile(os.path.join(_folder, "tweeter_rule.py")):
        sys.path.insert(0, _folder)
        break
import tweeter_rule  # noqa: E402

VERSION = "0.1.1"
PROTOCOL = 1
CONTROL_PORT, KNOB_PORT, MONITOR_PORT = 7450, 7451, 7452
LINE_LIMIT = 32 * 1024 * 1024          # a longer line closes the connection
HELLO_LINE_LIMIT = 64 * 1024           # the same before the peer proved the secret, and on the monitor link
RETRY = 1.0                            # every reconnection, every second
HELLO_TIMEOUT = 10.0
APPLY_TIMEOUT = 10.0                   # a speaker that does not answer an apply in this has failed it
GESTURE_TIMEOUT = 5.0                  # a gesture with no end ends by itself
PERSIST_INTERVAL = 1.0                 # graph.json at most once a second while parameters change
LKG_AFTER = 60.0                       # graph.json -> graph.lkg.json after this long with no restart
MAX_MONITOR_RATE = 60.0
MONITOR_BACKLOG = 256 * 1024           # a monitor client further behind than this skips values
MONITOR_TOKENS = 2.0                   # a monitor subscription's bucket holds at most this (Subscription)
LAYER_FILES = {"current": "graph.json", "last-known-good": "graph.lkg.json", "factory": "factory.json"}
SEQ_MASK = 0xFFFFFFFF
BIG = 2 ** 63 - 1
TIME_FORMAT = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z")
# The streams, exactly as speakerd reads them (used with fullmatch).
STREAMS = (re.compile(r"output/level"), re.compile(r"speakerd/timing"), re.compile(r"node/(1[6-9]|[2-9][0-9]|[1-9][0-9]{2,8})/mon_[A-Za-z0-9_]{1,64}"))
SECRET_FORMAT = re.compile(r"[\x21-\x7e]{16,256}")
MAX_NODES, MAX_CONNECTIONS = 64, 512           # a document with more is refused
MAX_CONTROL_PEERS = 32                         # controllers and agents on the hub's control link
MAX_MONITOR_CLIENTS = 32
MAX_SUBSCRIPTIONS = 32                         # per monitor client
WRITE_LIMIT = 1024 * 1024                      # a client this far behind in reading is dropped
DOCUMENTS_WRITE_LIMIT = 4 * LINE_LIMIT          # a controller's unread documents (state, graph) besides that
PEER_WRITE_LIMIT = 256 * 1024 * 1024           # the same for an agent or speakerd (documents are big)


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


async def _guarded(coroutine, who):
    try:
        return await coroutine
    except asyncio.CancelledError:
        raise
    except Exception as e:
        THROTTLED.say(("task", who, type(e).__name__), f"{who}: {type(e).__name__}: {e}: carrying on")


def background(coroutine, who="a task"):
    """Runs `coroutine` on its own; an error in it is said, and ends nothing else."""
    task = asyncio.get_running_loop().create_task(_guarded(coroutine, who))
    BACKGROUND.add(task)
    task.add_done_callback(BACKGROUND.discard)
    return task


def valid_time(value):
    """A revision's time as protocol.md writes it ("2026-10-28T09:15:02Z"), or "" for anything else."""
    return value if isinstance(value, str) and TIME_FORMAT.fullmatch(value) else ""


def valid_stream(stream):
    return isinstance(stream, str) and any(pattern.fullmatch(stream) for pattern in STREAMS)


NONCE = re.compile(r"[0-9a-f]{32}")
PROOF = re.compile(r"[0-9a-f]{64}")
TOKEN = re.compile(r"[0-9a-f]{32}")


def hmac_hex(key, text):
    return hmac.new(key.encode(), text.encode(), hashlib.sha256).hexdigest()


def proof(secret, side, ns, nc, role, name):
    """docs/protocol.md, "Authentication": lowercase hex HMAC-SHA256, keyed with the secret, of
    "<side>:<Ns>:<Nc>:<role>:<name>" (side "client" or "server", so a proof never reflects)."""
    return hmac_hex(secret, f"{side}:{ns}:{nc}:{role}:{name}")


def proof_holds(secret, side, ns, nc, role, name, got):
    """Whether `got` is that proof: 64 lowercase hex characters, compared in constant time as
    bytes. Anything else (not text, not ASCII, not hex) is false, never an exception."""
    if not isinstance(got, str) or not PROOF.fullmatch(got) or not isinstance(role, str) or not isinstance(name, str):
        return False
    return hmac.compare_digest(got.encode("ascii"), proof(secret, side, ns, nc, role, name).encode("ascii"))


def osc_token(secret, ns, nc):
    """The OSC token of a control connection: the first 32 hex characters of HMAC-SHA256 keyed
    with the session key K = HMAC(secret, "session:<Ns>:<Nc>") (hex, as its UTF-8 bytes), of "osc"."""
    return hmac_hex(hmac_hex(secret, f"session:{ns}:{nc}"), "osc")[:32]


def same_token(a, b):
    return isinstance(a, str) and isinstance(b, str) and TOKEN.fullmatch(a) is not None \
        and hmac.compare_digest(a.encode("ascii"), b.encode("ascii"))


class Handshakes:
    """Connections of one link still in their handshake: at most 16, 10 s each."""
    LIMIT = 16

    def __init__(self):
        self.count = 0


async def server_handshake(core, conn, handshakes, role, extra):
    """The server's side of docs/protocol.md's handshake: hello with a nonce, the client's hello
    with its nonce and proof checked, welcome with the server's proof. Returns (the client's
    hello, its OSC token), or None after an error to the client (the caller closes)."""
    if handshakes.count >= Handshakes.LIMIT:
        conn.send({"op": "error", "about": "hello", "reason": f"{Handshakes.LIMIT} connections in their handshake already"})
        return None
    handshakes.count += 1
    try:
        ns = secrets.token_hex(16)
        conn.send({"op": "hello", "role": role, "name": core.name, **extra, "nonce": ns, "protocol": PROTOCOL})
        try:
            hello = await asyncio.wait_for(conn.read(), HELLO_TIMEOUT)
        except (asyncio.TimeoutError, ProtocolError, ConnectionError):
            return None
        if hello is None or hello.get("op") != "hello":
            conn.send({"op": "error", "about": "hello", "reason": "the first line is a hello"})
            return None
        if core.secret is None:
            conn.send({"op": "welcome", "auth": None})
            return hello, None
        nc = hello.get("nonce")
        if not isinstance(nc, str) or not NONCE.fullmatch(nc) \
                or not proof_holds(core.secret, "client", ns, nc, hello.get("role"), hello.get("name"), hello.get("auth")):
            THROTTLED.say(("auth", role, conn.address), f"refused {conn.address}: its hello does not prove the pair's secret")
            conn.send({"op": "error", "about": "hello", "reason": "the hello does not prove the pair's secret"})
            return None
        conn.send({"op": "welcome", "auth": proof(core.secret, "server", ns, nc, role, core.name)})
        return hello, osc_token(core.secret, ns, nc)
    finally:
        handshakes.count -= 1


def bad_message(who, msg, error):
    """One message that broke something: said (at most every 10 s), and nothing else ends."""
    op = msg.get("op") if isinstance(msg, dict) else None
    THROTTLED.say(("bad-message", who, op), f"ignored a message from {who} ({op}): {type(error).__name__}: {error}")


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
    """A finite number; a whole number within +-1e15 (a 400-digit one would not convert)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return -10 ** 15 <= value <= 10 ** 15
    return isinstance(value, float) and math.isfinite(value)


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
    if len(nodes) > MAX_NODES:
        return f"nodes: {len(nodes)}, at most {MAX_NODES}"
    if len(connections) > MAX_CONNECTIONS:
        return f"connections: {len(connections)}, at most {MAX_CONNECTIONS}"
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

    def __init__(self, reader, writer, limit=WRITE_LIMIT):
        self.reader, self.writer, self.limit = reader, writer, limit
        self.line_limit = getattr(reader, "_limit", LINE_LIMIT)
        # Documents sent (state, graph), by their place in what was ever written: the limit
        # counts what is unread besides them (Conn.send).
        self.written, self.documents = 0, collections.deque()
        peer, own = writer.get_extra_info("peername"), writer.get_extra_info("sockname")
        self.address = peer[0] if isinstance(peer, tuple) else None
        self.local_address = own[0] if isinstance(own, tuple) else None
        self.closed = False

    def set_line_limit(self, limit):
        """The longest line this connection reads from now on. asyncio's StreamReader takes its
        limit when it is made and has no public setter: its `_limit` (CPython 3.7 and later) is
        what readline checks, and what it buffers at most twice of before it stops reading."""
        self.line_limit = limit
        self.reader._limit = limit

    async def read(self, raw=False):
        """The next object, or None at the end; with `raw`, (the object, its line as it came, with
        its newline) or (None, None): the local link's monitor values go to the clients as they
        came (Monitor.on_value). ProtocolError for a line longer than the line limit (64 KiB until
        the peer proved the pair's secret, 32 MiB after) or one that is not a JSON object: the
        connection closes (the peer reconnects)."""
        try:
            line = await self.reader.readline()
        except (asyncio.LimitOverrunError, ValueError):
            raise ProtocolError(f"a line longer than {self.line_limit // 1024} KiB") from None
        if not line or not line.endswith(b"\n"):
            return (None, None) if raw else None      # the end, or the peer went away in the middle of a line
        try:
            obj = json.loads(line)
        except (UnicodeDecodeError, ValueError, RecursionError) as e:
            raise ProtocolError(f"a line that is not JSON ({e})") from None
        if not isinstance(obj, dict):
            raise ProtocolError("a line that is not a JSON object")
        return (obj, line) if raw else obj

    def unread_documents(self, backlog):
        """How much of `backlog` (written, not yet taken by the kernel) is documents."""
        taken = self.written - backlog
        while self.documents and self.documents[0][1] <= taken:
            self.documents.popleft()
        return sum(end - max(start, taken) for start, end in self.documents)

    def send(self, obj, document=False):
        """Queues one line. A peer that leaves more than `limit` unread, not counting whole
        documents (`document`: a state or graph, up to 32 MiB each, which a controller may
        take a while to read), or more than DOCUMENTS_WRITE_LIMIT of documents, is dropped."""
        if self.room():
            self.write(json.dumps(obj, separators=(",", ":")).encode() + b"\n", document)

    def send_line(self, line):
        """Queues a line already encoded, with its newline: a monitor value as speakerd sent it,
        which goes to every subscriber without being encoded again for each. The same limits as
        send(): it counts as what is unread besides documents."""
        if self.room():
            self.write(line, False)

    def room(self):
        """False when this connection is closed, or is closed now because its peer does not read
        what it gets (send()): nothing more queues for it."""
        if self.closed:
            return False
        if self.limit is not None:
            backlog = self.backlog()
            documents = self.unread_documents(backlog)
            if backlog - documents > self.limit or documents > DOCUMENTS_WRITE_LIMIT:
                # It does not read what it gets: nothing more queues for it.
                THROTTLED.say(("backlog", self.address), f"dropped a connection from {self.address}: more than "
                              + (f"{self.limit // 1024} KiB unread besides documents" if documents <= DOCUMENTS_WRITE_LIMIT
                                 else f"{DOCUMENTS_WRITE_LIMIT >> 20} MiB of documents unread"))
                self.close()
                return False
        return True

    def write(self, data, document):
        """Hands one encoded line to the transport, counting where documents lie in it."""
        try:
            self.writer.write(data)
        except (ConnectionError, RuntimeError):
            self.close()
            return
        if document:
            self.documents.append((self.written, self.written + len(data)))
        self.written += len(data)

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
        self.secret = config.get("secret")
        self.handshakes = {"control": Handshakes(), "monitor": Handshakes()}
        self.status_pending, self.status_written = None, -math.inf
        # Values set while a revision is being applied: the new revision keeps the ones it has.
        self.tracking, self.tracked = 0, {}

    # Files ------------------------------------------------------------------------------------

    def _device_role(self):
        try:
            with open(os.path.join(self.state_dir, "device.json")) as f:
                role = json.load(f).get("role")
            return role if role in ("left", "right", "mono") else "mono"
        except (OSError, ValueError, AttributeError):
            return "mono"

    def tweeter_on(self):
        """Whether this speaker's device.json turns the tweeter on (limits.tweeter "on"); a file
        that cannot be read is speakerd's fail-safe, the tweeter muted."""
        try:
            with open(os.path.join(self.state_dir, "device.json")) as f:
                limits = json.load(f).get("limits")
            return isinstance(limits, dict) and limits.get("tweeter") == "on"
        except (OSError, ValueError, AttributeError):
            return False

    def tweeter_refusal(self, doc):
        """Why this speaker must not apply `doc`: with its tweeter on, the tweeter rule
        (tweeter_rule.py: only a crossover's high band reaches the tweeter). None when it may."""
        if not self.tweeter_on():
            return None
        why = tweeter_rule.violation(doc)
        return None if why is None else f"the tweeter is on here (device.json) and {why}"

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
        return doc, valid_time(t), (origin if isinstance(origin, str) else "")

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
        # A value that changed while this revision was applied: speakerd carried it over into the
        # new graph, so the new revision keeps it too (when it has that parameter).
        carried = [key for key in self.tracked if key in self.params and self.params[key] != self.tracked[key]]
        for key in carried:
            self.params[key] = self.tracked[key]
        if carried:
            log(f"revision {self.revision}: keeps {len(carried)} value(s) changed while it was applied")

    def set_param(self, key, value):
        self.params[key] = value
        if self.tracking:
            self.tracked[key] = value

    def begin_tracking(self):
        self.tracking += 1

    def end_tracking(self):
        self.tracking -= 1
        if not self.tracking:
            self.tracked = {}

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

    def status_soon(self):
        """The status changed in a way that may come often (a flood of datagrams): status.json
        is written at most once a second, and always soon after the last change."""
        if self.status_pending is not None:
            return
        wait = max(0.0, self.status_written + 1.0 - time.monotonic())
        self.status_pending = asyncio.get_running_loop().call_later(wait, self._status_later)

    def _status_later(self):
        self.status_pending = None
        self.status_changed()

    def status_changed(self):
        self.status_written = time.monotonic()
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

    def send_apply(self, revision, document):
        """Hands a revision to speakerd now, in order with the values around it (a value sent
        after it is carried over into the new graph, one before it is the document's), and
        returns a future of (True, None) once it plays or (False, reason). A future cancelled (a
        timeout) is forgotten: speakerd's answer then comes as a late one."""
        future = asyncio.get_running_loop().create_future()
        if not self.connected:
            future.set_result((False, "speakerd is not running"))
            return future
        self.pending.setdefault(revision, []).append(future)
        self.send({"op": "apply", "revision": revision, "document": document})

        def forget(done):
            futures = self.pending.get(revision, [])
            if done in futures:
                futures.remove(done)
            if not futures:
                self.pending.pop(revision, None)
        future.add_done_callback(forget)
        return future

    async def apply(self, revision, document):
        """Hands a revision to speakerd: (True, None) once it plays, (False, reason) if not."""
        return await self.send_apply(revision, document)

    async def run(self):
        path = self.core.socket_path
        while True:
            try:
                reader, writer = await asyncio.open_unix_connection(path, limit=LINE_LIMIT)
            except OSError as e:
                self.once.say("connect", f"speakerd: cannot connect to {path}: {e.strerror or e}: trying again every second")
                await asyncio.sleep(RETRY)
                continue
            conn = Conn(reader, writer, PEER_WRITE_LIMIT)
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
                try:
                    self.core.role.on_speakerd(hello, reconnect)
                except Exception as e:
                    bad_message("speakerd", hello, e)
                while True:
                    obj, line = await conn.read(raw=True)
                    if obj is None:
                        break
                    try:
                        self.dispatch(obj, line)
                    except Exception as e:
                        bad_message("speakerd", obj, e)
            except ProtocolError as e:
                log(f"speakerd: {e}: closing the connection")
            except ConnectionError:
                pass
            finally:
                conn.close()
                self.conn, self.connected = None, False
                self.core.monitor.on_speakerd_lost()
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

    def dispatch(self, obj, line):
        """One message from speakerd; `line` is it as it came (a monitor value is relayed so)."""
        op = obj.get("op")
        if op in ("applied", "failed"):
            revision = obj.get("revision")
            result = (True, None) if op == "applied" else (False, str(obj.get("reason") or "failed"))
            if op == "applied" and good_int(revision, 0, BIG):
                self.layer, self.revision = "current", revision
            futures = [f for f in self.pending.get(revision, []) if not f.done()]
            if futures:
                for future in futures:
                    future.set_result(result)
            else:
                self.core.role.late_answer(revision, result)
            self.core.status_changed()
        elif op == "value":
            self.core.monitor.on_value(obj, line)
        elif op == "error" and obj.get("about") == "subscribe":
            self.core.monitor.on_error(obj)
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
        port = hello.get("osc_port")
        # Agents get no OSC: every parameter goes to them over the control link.
        self.osc = (conn.address, port) if role == "controller" and good_int(port, 1, 65535) and conn.address else None
        self.token = None            # the OSC token of this connection (a controller's)
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
        self.osc_dropped = 0         # datagrams without a valid token

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
               "speakerd": sd.version if sd.connected else None, "layer": sd.layer, "revision": sd.revision}
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
            controller.conn.send(message, document=True)

    def broadcast_state(self):
        """A speaker came online, went offline or changed layer, or the hub adopted a revision:
        every controller gets the whole state again (docs/protocol.md, clarification 2)."""
        for controller in self.controllers:
            controller.conn.send(self.state(controller.conn), document=True)
        self.core.status_changed()

    def status(self):
        return {"controllers": [c.name for c in self.controllers],
                "agents": [dict(s) for _, s in sorted(self.speakers.items())],
                "seq": self.seq, "gestures": len(self.gestures), "osc_dropped": self.osc_dropped}

    def osc(self, peer, address, tags, *args):
        """An OSC datagram to controller `peer`, stamped with its own token."""
        if self.udp is not None and peer.osc is not None:
            try:
                self.udp.sendto(osc_encode(address, tags + "s", *args, peer.token or ""), peer.osc)
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
        core.set_param((node, pid), value)
        core.sd.send_param(node, pid, value, origin)
        out = {"op": "param", "node": node, "id": pid, "value": value, "seq": self.seq, "origin": origin}
        # Agents get every parameter over the control link, authenticated both ways.
        for peer in self.agents.values():
            peer.conn.send(out)
        numeric = good_number(value) or isinstance(value, bool)
        for peer in self.controllers:
            if numeric:
                self.osc(peer, "/bpi/param", "isfis", node, pid, float(value), self.seq, origin)
            if via == "control" or not numeric:
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
        for controller in self.controllers:
            self.osc(controller, "/bpi/gesture", "isss", node, pid, state, origin)
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

    def apply_on(self, peer, revision, document, rev_time, rev_origin):
        """Sends agent `peer` the revision now; returns what to await: (True, None) once it says
        its speakerd plays it, else (False, why)."""
        future = asyncio.get_running_loop().create_future()
        if peer.conn.closed:
            future.set_result((False, "offline"))
        else:
            peer.pending[revision] = future
            peer.conn.send({"op": "apply", "revision": revision, "document": document,
                            "time": rev_time, "origin": rev_origin})

        async def answer():
            try:
                return await asyncio.wait_for(asyncio.shield(future), APPLY_TIMEOUT)
            except asyncio.TimeoutError:
                return False, f"no answer within {APPLY_TIMEOUT:g} s"
            finally:
                if peer.pending.get(revision) is future:
                    del peer.pending[revision]
        return answer()

    def apply_here(self, revision, document):
        """Sends this speaker's speakerd the revision now; returns what to await."""
        future = self.core.sd.send_apply(revision, document)

        async def answer():
            try:
                return await asyncio.wait_for(future, APPLY_TIMEOUT)
            except asyncio.TimeoutError:
                return False, f"no answer within {APPLY_TIMEOUT:g} s"
        return answer()

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
            why = core.tweeter_refusal(document)
            if why is not None:
                return refuse(f"{core.name}: {why}")
            rev_time = valid_time(msg.get("time")) or now_iso()
            rev_origin = msg["origin"] if isinstance(msg.get("origin"), str) and msg["origin"] else controller.name
            doc = copy.deepcopy(document)
            doc["revision"] = revision
            doc.pop("time", None)
            doc.pop("origin", None)
            up = ([core.name] if core.sd.connected else []) + sorted(self.agents)
            log(f"revision {revision} from {rev_origin}: applying on {', '.join(up) or 'no speaker (none is up)'}")
            core.begin_tracking()
            try:
                return await self.finish_proposal(controller, refuse, base, revision, doc, rev_time, rev_origin)
            finally:
                core.end_tracking()

    async def finish_proposal(self, controller, refuse, base, revision, doc, rev_time, rev_origin):
        core = self.core
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
            a_time = valid_time(hello.get("time"))
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
        if why is None and plugins_of(document) - self.seen:
            why = "no speaker has loaded " + ", ".join(sorted(plugins_of(document) - self.seen))
        if why is None:
            why = core.tweeter_refusal(document)
        if why is not None:
            log(f"agent {peer.name}: its revision is refused ({why}): keeping revision {core.revision}")
            return False
        revision = graph["revision"] if good_int(graph.get("revision"), 0, BIG) else document.get("revision", 0)
        doc = copy.deepcopy(document)
        doc["revision"] = revision
        rev_time = valid_time(graph.get("time"))
        rev_origin = graph["origin"] if isinstance(graph.get("origin"), str) else ""
        others = [p for p in self.agents.values() if p is not peer]
        core.begin_tracking()
        try:
            results = await self.apply_everywhere(revision, doc, rev_time, rev_origin, agents=others)
            if not results.get(core.name, (True, None))[0]:
                log(f"agent {peer.name}: revision {revision} failed on {core.name}: {results[core.name][1]}: "
                    f"keeping revision {core.revision}")
                return False
            core.set_current(doc, rev_time, rev_origin)
        finally:
            core.end_tracking()
        core.changed(urgent=True)
        core.playing()
        self.seen |= plugins_of(doc)
        self.speakers.get(peer.name, {}).update(layer="current", revision=revision)
        log(f"revision {revision} of {peer.name} adopted: current")
        self.broadcast_graph()
        self.broadcast_state()
        return True

    # Connections ---------------------------------------------------------------------------------------------

    async def serve(self, conn, hello, token=None):
        role = hello.get("role")
        if role == "controller":
            await self.serve_controller(conn, hello, token)
        elif role == "agent":
            await self.serve_agent(conn, hello)
        else:
            conn.send({"op": "error", "about": "hello", "reason": f"no role {role!r}: controller or agent"})

    async def serve_controller(self, conn, hello, token=None):
        peer = Peer(conn, hello, "controller")
        peer.token = token
        self.controllers.append(peer)
        log(f"controller {peer.name} connected from {conn.address}"
            + (f" (osc port {peer.osc[1]})" if peer.osc else " (no osc_port: no OSC echoes)"))
        conn.send(self.state(conn), document=True)
        self.core.status_changed()
        try:
            while True:
                msg = await conn.read()
                if msg is None:
                    break
                op = msg.get("op")
                try:
                    if op == "param":
                        self.param(msg, "control", peer)
                    elif op == "gesture":
                        self.gesture(msg, "control", peer)
                    elif op == "graph":
                        background(self.propose(peer, msg), f"a proposal of {peer.name}")
                except Exception as e:
                    bad_message(f"controller {peer.name}", msg, e)
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
        conn.limit = PEER_WRITE_LIMIT
        old = self.agents.get(peer.name)
        if old is not None:
            if self.core.secret is None:
                # Without the pair's secret anybody can say they are the right speaker: the one
                # connected stays.
                log(f"agent {peer.name}: refused a second connection from {conn.address} (no secret to prove it)")
                conn.send({"op": "error", "about": "hello", "reason": f"an agent called {peer.name} is connected"})
                return
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
                try:
                    self.agent_message(peer, msg)
                except Exception as e:
                    bad_message(f"agent {peer.name}", msg, e)
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

    def agent_message(self, peer, msg):
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

    def drop(self, address, why):
        self.osc_dropped += 1
        self.core.status_soon()
        self.bad.say(("osc-drop", address[0]), f"knobs: dropped a datagram from {address[0]}: {why} "
                                               f"({self.osc_dropped} dropped so far)")

    def datagram(self, data, address):
        """OSC, accepted only with the token of an authenticated controller connected from the
        datagram's address (docs/protocol.md, "Authentication"); anything else is dropped and
        counted, never answered."""
        try:
            path, args = osc_decode(data)
        except ValueError as e:
            return self.drop(address, str(e))
        kinds = [type(a) for a in args]
        if path == "/bpi/param" and len(args) == 6 and kinds[:2] == [int, str] and good_number(args[2]) \
                and kinds[3:] == [int, str, str]:
            message = {"node": args[0], "id": args[1], "value": float(args[2]), "seq": args[3], "origin": args[4]}
        elif path == "/bpi/gesture" and len(args) == 5 and kinds == [int, str, str, str, str]:
            message = {"node": args[0], "id": args[1], "state": args[2], "origin": args[3]}
        else:
            return self.drop(address, f"{path} with {len(args)} argument(s) is no /bpi/param ,isfiss or /bpi/gesture ,issss")
        token = args[-1]
        sender = None
        for peer in self.controllers:
            if peer.conn.address == address[0] and (self.core.secret is None or same_token(token, peer.token)):
                sender = peer
                break
        if sender is None:
            return self.drop(address, "no token of an authenticated controller connected from there")
        if path == "/bpi/param":
            self.param(message, "osc", sender)
        else:
            self.gesture(message, "osc", sender)


# ---------------------------------------------------------------------------------------------
# The agent

class Agent(Role):
    def __init__(self, core):
        super().__init__(core)
        self.conn = None
        self.hub_name = None
        self.last_seq = {}
        self.once = Once()
        self.osc_dropped = 0

    def status(self):
        return {"hub": {"address": self.core.hub_address, "connected": self.conn is not None, "name": self.hub_name},
                "osc_dropped": self.osc_dropped}

    def report(self):
        """What this speaker's speakerd plays, to the hub (not in protocol 1's examples: an op
        the hub uses for the speakers of its state)."""
        if self.conn is not None:
            sd = self.core.sd
            self.conn.send({"op": "status", "speakerd": sd.version if sd.connected else None, "layer": sd.layer,
                            "revision": sd.revision})

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
        core.set_param((node, pid), value)
        origin = msg.get("origin") if isinstance(msg.get("origin"), str) else "hub"
        core.sd.send_param(node, pid, value, origin)
        core.changed()

    def datagram(self, data, address):
        """An agent takes every parameter from its hub's control link: every datagram is dropped
        and counted."""
        self.osc_dropped += 1
        self.core.status_soon()
        THROTTLED.say(("osc-drop", address[0]), f"knobs: dropped a datagram from {address[0]}: an agent takes values "
                                                f"from its hub's control link only ({self.osc_dropped} dropped so far)")

    def start_apply(self, conn, msg):
        """An apply from the hub: checked and sent to speakerd at once, before the messages after
        it are read (a value the hub sent after it is carried over into the new graph by speakerd,
        and kept by ampd); the answer is awaited on its own."""
        core = self.core
        revision, document = msg.get("revision"), msg.get("document")
        why = check_document(document) if good_int(revision, 0, BIG) else "no revision"
        if why is not None:
            conn.send({"op": "failed", "revision": revision, "reason": f"the document does not parse: {why}"})
            return
        refusal = core.tweeter_refusal(document)
        if refusal is not None:
            log(f"revision {revision}: refused: {refusal}")
            conn.send({"op": "failed", "revision": revision, "reason": refusal})
            return
        doc = copy.deepcopy(document)
        doc["revision"] = revision
        rev_time = valid_time(msg.get("time") if "time" in msg else document.get("time"))
        rev_origin = msg["origin"] if isinstance(msg.get("origin"), str) else document.get("origin", "")
        core.begin_tracking()
        future = core.sd.send_apply(revision, doc)
        background(self.finish_apply(conn, revision, doc, rev_time, rev_origin, future), "an apply")

    async def finish_apply(self, conn, revision, doc, rev_time, rev_origin, future):
        core = self.core
        try:
            ok, reason = await future
            if ok:
                core.set_current(doc, rev_time, rev_origin)
        finally:
            core.end_tracking()
        if ok:
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
                reader, writer = await asyncio.open_connection(host, port, limit=HELLO_LINE_LIMIT,
                                                               local_addr=local_addr)
            except OSError as e:
                self.once.say("connect", f"hub: cannot connect to {host}:{port}: {e.strerror or e}: trying again every second")
                await asyncio.sleep(RETRY)
                continue
            conn = Conn(reader, writer, PEER_WRITE_LIMIT)
            try:
                # The hub says hello first, with its nonce; this agent answers with its own nonce
                # and proof, and believes nothing until the hub's welcome proves the secret too.
                hello = await asyncio.wait_for(conn.read(), HELLO_TIMEOUT)
                if hello is None or hello.get("op") != "hello" or hello.get("role") != "hub":
                    raise ProtocolError(f"the hub said {json.dumps(hello)[:200]}, not its hello")
                ns, hub_name, nc = hello.get("nonce"), hello.get("name"), secrets.token_hex(16)
                mine = {"op": "hello", "role": "agent", "name": core.name, "revision": core.revision,
                        "time": core.rev_time, "origin": core.rev_origin, "protocol": PROTOCOL,
                        "layer": core.sd.layer, "speakerd": core.sd.version if core.sd.connected else None,
                        "device_role": core.device_role, "nonce": nc}
                if core.secret is not None:
                    if not isinstance(ns, str) or not NONCE.fullmatch(ns) or not isinstance(hub_name, str):
                        raise ProtocolError("the hub's hello has no nonce or name: not believing it")
                    mine["auth"] = proof(core.secret, "client", ns, nc, "agent", core.name)
                conn.send(mine)
                welcome = await asyncio.wait_for(conn.read(), HELLO_TIMEOUT)
                if welcome is None or welcome.get("op") != "welcome":
                    raise ProtocolError(f"the hub answered {json.dumps(welcome)[:200]}, not a welcome")
                if core.secret is not None and not proof_holds(core.secret, "server", ns, nc, "hub", hub_name,
                                                               welcome.get("auth")):
                    raise ProtocolError(f"the hub at {host}:{port} did not prove the pair's secret: not believing it")
                conn.set_line_limit(LINE_LIMIT)
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
                    try:
                        if op == "param":
                            self.param(msg, "control")
                        elif op == "apply":
                            self.start_apply(conn, msg)
                        elif op == "get":
                            conn.send({"op": "graph", "revision": core.revision, "time": core.rev_time,
                                       "origin": core.rev_origin, "document": core.document()})
                        elif op == "error":
                            THROTTLED.say(("hub-error", msg.get("about")),
                                          f"hub: error: {msg.get('about', '?')}: {msg.get('reason', '?')}")
                    except Exception as e:
                        bad_message("the hub", msg, e)
            except asyncio.TimeoutError:
                self.once.say("hello", f"hub: {host}:{port} said no hello within {HELLO_TIMEOUT:g} s: trying again every second")
            except ProtocolError as e:
                # Each kind of failure said once (the hub's own words, which change, left out of the key).
                self.once.say(("hello", re.sub(r"\{.*\}", "{}", str(e))), f"hub: {e}: trying again every second")
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
# Monitoring: subscriptions go to speakerd as their union, each value to the clients as it arrives

class Subscription:
    """One client's subscription to one stream: its rate, and a token bucket that lets a value
    through when it holds a token. It fills at the rate and holds at most MONITOR_TOKENS (two):
    the one a value takes, and one of slack. The slack is what keeps the client at its own rate:
    a value that comes early after a late one (speakerd's monitor thread or this loop held up a
    moment) still goes, and a client slower than the union, which gets one value in every few,
    keeps the part of a token it had left at the last value it got (with one token at most it
    would wait for whole numbers of the union's periods: 26 values a second for 45 asked, beside
    a client of 60 Hz). It starts with one token, which the stored value takes (Monitor.request),
    so a new subscription's next value comes a period after it; the slack builds up over time.
    So in any T seconds it gets at most 2 + T x rate values (1 + T x rate from its start): its
    rate on average, and after a pause (a full bucket) one value may come early. Nothing waits
    in it. `sent` is the number of the last value it got (Monitor.on_value): it never gets one
    twice."""

    __slots__ = ("rate", "tokens", "then", "sent")

    def __init__(self, rate, now):
        self.rate, self.tokens, self.then, self.sent = rate, 1.0, now, 0

    def fill(self, now):
        self.tokens = min(MONITOR_TOKENS, self.tokens + (now - self.then) * self.rate)
        self.then = now

    def set_rate(self, rate, now):
        """Subscribed again at another rate: what it holds is counted at the old one up to now."""
        self.fill(now)
        self.rate = rate


class MonitorClient:
    def __init__(self, conn):
        self.conn = conn
        self.subs = {}       # stream -> Subscription


class Monitor:
    def __init__(self, core):
        self.core = core
        self.clients = []
        self.latest = {}     # stream of the union -> (number, its newest value line as speakerd sent it)
        self.union = {}      # stream -> the rate speakerd was asked for
        self.arrived = set()             # streams whose newest line has not been flushed yet
        self.flush_scheduled = False
        self.values = 0                  # the values kept so far, which numbers them

    def on_error(self, obj):
        """speakerd refused a subscription: every subscriber of that stream is told, and loses it."""
        stream = obj.get("stream")
        for client in self.clients:
            if stream in client.subs:
                client.conn.send({"op": "error", "about": "subscribe", "stream": stream, "reason": obj.get("reason")})
                del client.subs[stream]
        self.update_union()

    def on_value(self, obj, line):
        """A value from speakerd, `line` as it came. That line is what the clients get (it is not
        encoded again for each), so it is kept only when a client may get it as it is: a value
        (dispatch) of a stream some client subscribed to, that starts with its object and holds
        no NaN or Infinity (Python's json reads them, a controller does not: such a line is
        encoded again, which a value that is not a number fails), and at most 64 KiB long (the
        longest line ampd reads on the monitor link). A line longer than twice that is refused
        before anything else, so that no huge line is encoded again on the loop (encoding it
        compactly does not halve it: speakerd writes at most a space after a separator).
        Otherwise no client gets it, and the stream has no newest value until the next one."""
        stream = obj.get("stream")
        if not isinstance(stream, str) or stream not in self.union:
            return       # nobody asked for it (speakerd has not read an unsubscribe yet)
        if len(line) - 1 > 2 * HELLO_LINE_LIMIT:
            self.not_relayed(stream, "huge", f"longer than {HELLO_LINE_LIMIT // 1024} KiB")
            return
        if line[:1] != b"{" or b"NaN" in line or b"Infinity" in line:
            try:
                line = json.dumps(obj, separators=(",", ":"), allow_nan=False).encode() + b"\n"
            except ValueError:
                self.not_relayed(stream, "number", "that is not a number")
                return
        if len(line) - 1 > HELLO_LINE_LIMIT:
            self.not_relayed(stream, "long", f"longer than {HELLO_LINE_LIMIT // 1024} KiB")
            return
        self.values += 1
        self.latest[stream] = (self.values, line)
        # One flush per pass of the event loop, after every line read at once: of a burst, only
        # the newest of each stream goes out. A value waits for nothing else.
        self.arrived.add(stream)
        if not self.flush_scheduled:
            self.flush_scheduled = True
            asyncio.get_running_loop().call_soon(self.flush)

    def not_relayed(self, stream, why, text):
        """A value of `stream` that no client may get: the stream has no newest value, and ampd
        says so (at most every 10 s for each `why`)."""
        self.latest.pop(stream, None)
        THROTTLED.say(("monitor-value", why), f"speakerd: a value of {stream} {text}: not relayed")

    def flush(self):
        """Sends the newest line of each stream that got one since the last flush to each of its
        subscribers whose bucket holds a token. A callback of the loop's: what breaks in one offer
        (one client, one stream) is said here, since nobody else would, and the others go on."""
        self.flush_scheduled = False
        arrived, self.arrived = self.arrived, set()
        now = time.monotonic()
        for stream in arrived:
            newest = self.latest.get(stream)
            if newest is None:
                continue         # it left the union since, or speakerd went away
            for client in self.clients:
                sub = client.subs.get(stream)
                if sub is not None:
                    try:
                        self.offer(client, sub, newest, now)
                    except Exception as e:
                        THROTTLED.say(("task", "monitor", type(e).__name__),
                                      f"monitor: {type(e).__name__}: {e}: carrying on")

    @staticmethod
    def offer(client, sub, newest, now):
        """The newest value, (number, line), to `client` if its subscription holds a token and has
        not had it (a subscription served from the store in the same pass as a flush of that
        value: speakerd/timing's `late` would count twice). A client further behind than
        MONITOR_BACKLOG skips it (the next may go); one whose connection closed gets nothing (it
        leaves self.clients when its serve() ends, which can come after a flush)."""
        number, line = newest
        if sub.sent == number:
            return
        sub.fill(now)
        conn = client.conn
        if sub.tokens >= 1.0 and not conn.closed and conn.backlog() < MONITOR_BACKLOG:
            conn.send_line(line)
            sub.tokens -= 1.0
            sub.sent = number

    def update_union(self):
        want = {}
        for client in self.clients:
            for stream, sub in client.subs.items():
                want[stream] = max(want.get(stream, 0), sub.rate)
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

    def on_speakerd_lost(self):
        """The newest values were a speakerd's that is gone: a window opened now waits for the
        next one's."""
        self.latest.clear()

    async def serve(self, reader, writer):
        conn = Conn(reader, writer)
        if len(self.clients) >= MAX_MONITOR_CLIENTS:
            conn.send({"op": "error", "about": "hello", "reason": f"{MAX_MONITOR_CLIENTS} monitor clients already"})
            conn.close()
            return
        # The speaker says hello first; both sides prove the pair's secret (as on the control link).
        try:
            done = await server_handshake(self.core, conn, self.core.handshakes["monitor"], "speaker", {})
        except Exception as e:
            bad_message(f"monitor {conn.address}", {}, e)
            done = None
        if done is None:
            conn.close()
            return
        client = MonitorClient(conn)
        self.clients.append(client)
        try:
            while True:
                msg = await conn.read()
                if msg is None:
                    break
                try:
                    self.request(client, msg)
                except Exception as e:
                    bad_message(f"monitor {conn.address}", msg, e)
        except (ProtocolError, ConnectionError):
            pass
        finally:
            conn.close()
            self.clients.remove(client)
            self.update_union()

    def request(self, client, msg):
        conn = client.conn
        op, stream = msg.get("op"), msg.get("stream")
        if op == "subscribe":
            if not valid_stream(stream):
                conn.send({"op": "error", "about": "subscribe", "stream": stream,
                           "reason": "a stream is output/level, speakerd/timing or node/<uid>/mon_<id> (uid 16 or more)"})
                return
            if stream not in client.subs and len(client.subs) >= MAX_SUBSCRIPTIONS:
                conn.send({"op": "error", "about": "subscribe", "stream": stream,
                           "reason": f"at most {MAX_SUBSCRIPTIONS} subscriptions per client"})
                return
            rate = msg.get("rate_hz", 30)
            if not good_number(rate) or not 0.1 <= rate <= MAX_MONITOR_RATE:
                conn.send({"op": "error", "about": "subscribe", "stream": stream,
                           "reason": f"rate_hz must be 0.1 to {MAX_MONITOR_RATE:g}"})
                return
            rate, now = float(rate), time.monotonic()
            sub = client.subs.get(stream)
            if sub is not None:
                sub.set_rate(rate, now)        # the bucket follows the new rate from now on
                self.update_union()
                return
            sub = client.subs[stream] = Subscription(rate, now)
            self.update_union()
            # A window opened now shows the newest value at once, from the store the others get.
            newest = self.latest.get(stream)
            if newest is not None:
                self.offer(client, sub, newest, now)
        elif op == "unsubscribe":
            if client.subs.pop(stream, None) is not None:
                self.update_union()


# ---------------------------------------------------------------------------------------------
# The listeners and main

class Knobs(asyncio.DatagramProtocol):
    def __init__(self, role):
        self.role = role

    def datagram_received(self, data, address):
        try:
            self.role.datagram(data, address)
        except Exception as e:
            bad_message(f"knobs {address[0]}", {}, e)

    def error_received(self, exc):
        pass


async def control_connection(core, reader, writer):
    conn = Conn(reader, writer)
    try:
        if core.is_hub:
            hub = core.role
            if len(hub.controllers) + len(hub.agents) >= MAX_CONTROL_PEERS:
                conn.send({"op": "error", "about": "hello", "reason": f"{MAX_CONTROL_PEERS} connections already"})
                return
            done = await server_handshake(core, conn, core.handshakes["control"], "hub",
                                          {"revision": core.revision, "seq": hub.seq})
            if done is None:
                return
            hello, token = done
            # Proven (or no secret configured): documents may come now.
            conn.set_line_limit(LINE_LIMIT)
            await hub.serve(conn, hello, token)
        else:
            conn.send({"op": "error", "about": "hello", "reason": f"{core.name} is not the hub: the hub is {core.hub_address}"})
    except (asyncio.TimeoutError, ProtocolError, ConnectionError):
        pass
    finally:
        conn.close()


def read_config(path):
    """ampd.json (docs/protocol.md): {"version": 1, "name", "hub", "listen", "hub_address", "secret"}."""
    with open(path) as f:
        config = json.load(f)
    if not isinstance(config, dict):
        raise ValueError("not a JSON object")
    if config.get("version", 1) != 1:
        raise ValueError("version: 1 is the only one")
    unknown = sorted(set(config) - {"version", "name", "hub", "listen", "hub_address", "secret"})
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
    secret = config.get("secret")
    if secret is not None and (not isinstance(secret, str) or not SECRET_FORMAT.fullmatch(secret)):
        raise ValueError("secret: 16 to 256 printable characters, no space")
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
    if core.secret is None:
        log("*** no secret in ampd.json: anyone on the network may control this speaker ***")
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
                                             args.control_port, limit=HELLO_LINE_LIMIT, reuse_address=True)
        monitor = await asyncio.start_server(handler(core.monitor.serve), core.listen, args.monitor_port,
                                             limit=HELLO_LINE_LIMIT, reuse_address=True)
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
              + f", listening on {config.get('listen', '0.0.0.0')}"
              + (", with the pair's secret" if config.get("secret") else ", NO secret"))
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
