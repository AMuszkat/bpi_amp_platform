#!/usr/bin/env python3
"""A small fake hub for testing the controller's bridge without ampd (docs/protocol.md).

It speaks the controller's side of three links, well enough for the controller's tests:

- control (TCP, JSON lines): hello, state, param (stamped with seq and echoed), gesture
  (relayed), graph proposals (applied or rejected), graph to every controller, error;
- knobs (UDP, OSC): /bpi/param and /bpi/gesture in, echoes out to each controller's osc_port;
- monitor (TCP, JSON lines), one listener per speaker: subscribe, unsubscribe, and value lines
  at the rate asked for, for output/level and node/<uid>/<mon_id>.

Standard library only. Every line and datagram it receives is kept in `received`, so a test can
check what the controller sent. Run alone, it serves a document until interrupted:

    python3 fake_hub.py --address 127.0.3.1 --right 127.0.3.2
"""

import argparse
import copy
import hashlib
import hmac
import json
import secrets
import socket
import socketserver
import struct
import threading
import time

LOUDNESS_URI = "https://github.com/AMuszkat/bpi_amp_dsp_tuning/loudness_headless"
PROBE_URI = "urn:bpi-amp:controller:test-probe"
SECRET = "test-pair-secret-0123456789"   # the tests' shared secret (docs/protocol.md, "Authentication")


def hmac_hex(key, text):
    """Lowercase hex HMAC-SHA256 keyed with the key's UTF-8 bytes over a UTF-8 string."""
    return hmac.new(key.encode(), text.encode(), hashlib.sha256).hexdigest()


def client_proof(secret, ns, nc, role, name):
    return hmac_hex(secret, "client:%s:%s:%s:%s" % (ns, nc, role, name))


def server_proof(secret, ns, nc, role, name):
    return hmac_hex(secret, "server:%s:%s:%s:%s" % (ns, nc, role, name))


def osc_token(secret, ns, nc):
    """K = HMAC("session:<Ns>:<Nc>") as hex; the token is HMAC-SHA256(K, "osc")[:32]."""
    return hmac_hex(hmac_hex(secret, "session:%s:%s" % (ns, nc)), "osc")[:32]


def is_nonce(n):
    return isinstance(n, str) and len(n) == 32 and all(c in "0123456789abcdef" for c in n)


# ---------------------------------------------------------------------------------------------
# OSC 1.0, the subset the protocol uses: int32, float32 and string arguments, no bundles.

def _osc_string(s):
    b = s.encode() + b"\0"
    return b + b"\0" * (-len(b) % 4)


def osc_encode(address, *args):
    tags = ","
    data = b""
    for a in args:
        if isinstance(a, bool):
            raise TypeError("no booleans on OSC")
        if isinstance(a, int):
            tags += "i"
            data += struct.pack(">i", a if a < 2**31 else a - 2**32)
        elif isinstance(a, float):
            tags += "f"
            data += struct.pack(">f", a)
        else:
            tags += "s"
            data += _osc_string(str(a))
    return _osc_string(address) + _osc_string(tags) + data


def osc_decode(packet):
    def read_string(pos):
        end = packet.index(b"\0", pos)
        s = packet[pos:end].decode()
        return s, (end + 4) & ~3

    address, pos = read_string(0)
    tags, pos = read_string(pos)
    args = []
    for t in tags[1:]:
        if t == "i":
            args.append(struct.unpack(">i", packet[pos:pos + 4])[0])
            pos += 4
        elif t == "f":
            args.append(struct.unpack(">f", packet[pos:pos + 4])[0])
            pos += 4
        elif t == "s":
            s, pos = read_string(pos)
            args.append(s)
        else:
            raise ValueError("OSC type %r not supported" % t)
    return address, tags, args


# ---------------------------------------------------------------------------------------------

def default_document(revision=7, probe=False):
    """loudness between FL and the woofer; with probe, the test probe between FR and the tweeter."""
    nodes = [{
        "uid": 16, "name": "loudness",
        "plugin": {"format": "LV2", "identifier": LOUDNESS_URI},
        "params": {"mode": 1, "s0_e0_gain": 12.0, "s0_e0_freq": 120.0},
        "ui": {"x": 0.35, "y": 0.5},
    }]
    connections = [
        {"src": {"node": 1, "channel": 0}, "dst": {"node": 16, "channel": 0}},
        {"src": {"node": 16, "channel": 0}, "dst": {"node": 2, "channel": 0}},
    ]
    if probe:
        nodes.append({
            "uid": 20, "name": "test-probe",
            "plugin": {"format": "LV2", "identifier": PROBE_URI},
            "params": {"gain": -6.0, "mode": 0, "bypass": 0},
            "ui": {"x": 0.65, "y": 0.5},
        })
        connections += [
            {"src": {"node": 1, "channel": 2}, "dst": {"node": 20, "channel": 0}},
            {"src": {"node": 20, "channel": 0}, "dst": {"node": 2, "channel": 1}},
        ]
    return {"format": "speakerd-graph", "version": 1, "revision": revision,
            "nodes": nodes, "connections": connections}


class _LineServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class FakeHub:
    """The hub and the two speakers' monitor links, on loopback addresses.

    `left` is the hub's own address (control 7450, knobs 7451, monitor 7452); `right` is the
    right speaker's, where only its monitor link listens."""

    def __init__(self, left="127.0.3.1", right="127.0.3.2", name="left",
                 document=None, ports=(7450, 7451, 7452), control_rcvbuf=None, secret=SECRET):
        self.left, self.right, self.name = left, right, name
        self.control_port, self.knobs_port, self.monitor_port = ports
        self.document = copy.deepcopy(document or default_document())
        self.revision = self.document.get("revision", 0)
        self.seq = 1000
        self.speakers = [
            {"name": "left", "role": "left", "online": True, "layer": "current"},
            {"name": "right", "role": "right", "online": True, "layer": "current"},
        ]
        self.known_plugins = {LOUDNESS_URI, PROBE_URI}
        self.reject_next = None           # a reason: the next proposal is rejected with it
        self.echo_delay = 0.0             # seconds before an OSC echo leaves (to test reordering)
        self.monitor_values = {           # per speaker: stream -> value
            "left": {"output/level": [-6.0, -12.0, -60.0, -66.0], "mon": -20.5},
            "right": {"output/level": [-9.0, -15.0, -70.0, -76.0], "mon": -30.25},
        }
        self.received = []                # (link, message): every line and datagram in
        self.sent = []                    # (link, message): every line and datagram out
        self.controllers = {}             # name -> {"conn", "osc": (host, port)}
        self.subscriptions = {"left": {}, "right": {}}   # speaker -> stream -> rate
        self.gestures = {}                # (node, id) -> origin
        self.control_rcvbuf = control_rcvbuf   # SO_RCVBUF of the control link's connections
        self.secret = secret              # None: no secret, every peer accepted
        self.refused = []                 # (link, name, reason) of every hello refused
        self.osc_refused = 0              # OSC datagrams without a valid token from their address
        self.wrong_welcome = False        # prove itself with a wrong server proof (a fake hub)
        self.tokens = {}                  # controller name -> the OSC token of its last connection
        self.monitor_names = {}           # speaker -> the name its monitor link claims (a test's lie)
        self.stalled = set()              # control connections that stopped reading
        self.lock = threading.RLock()
        self._servers = []
        self._connections = set()        # open TCP connections, closed by stop()
        self._stop = threading.Event()

    # -- lifecycle ------------------------------------------------------------------------
    def start(self):
        hub = self

        class Tracked(socketserver.StreamRequestHandler):
            def setup(self):
                super().setup()
                with hub.lock:
                    hub._connections.add(self.connection)

            def finish(self):
                with hub.lock:
                    hub._connections.discard(self.connection)
                super().finish()

        class Control(Tracked):
            def handle(self):
                hub._serve_control(self)

        def monitor_handler(speaker):
            class Monitor(Tracked):
                def handle(self):
                    hub._serve_monitor(self, speaker)
            return Monitor

        for address, port, handler in ((self.left, self.control_port, Control),
                                       (self.left, self.monitor_port, monitor_handler("left")),
                                       (self.right, self.monitor_port, monitor_handler("right"))):
            server = _LineServer((address, port), handler, bind_and_activate=False)
            if handler is Control and self.control_rcvbuf:
                # Inherited by every accepted connection: a small window fills quickly.
                server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self.control_rcvbuf)
            server.server_bind()
            server.server_activate()
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self._servers.append(server)

        self.knobs = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.knobs.bind((self.left, self.knobs_port))
        self.knobs.settimeout(0.2)
        threading.Thread(target=self._serve_knobs, daemon=True).start()
        return self

    def stop(self):
        """Stops like a hub that goes away: every connection closes."""
        self._stop.set()
        self.stalled = set()
        for s in self._servers:
            s.shutdown()
            s.server_close()
        with self.lock:
            connections = list(self._connections)
        for c in connections:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.knobs.close()

    # -- helpers --------------------------------------------------------------------------
    def _log_in(self, link, message):
        with self.lock:
            self.received.append((link, message, time.monotonic()))

    def _send_line(self, conn, link, message):
        with self.lock:
            self.sent.append((link, message, time.monotonic()))
        try:
            conn.wfile.write((json.dumps(message) + "\n").encode())
            conn.wfile.flush()
        except OSError:
            pass

    def _osc_out(self, address, *args):
        """An echo to every controller, stamped with that controller's own token."""
        def send():
            if self.echo_delay:
                time.sleep(self.echo_delay)
            with self.lock:
                targets = [(c["osc"], c["token"]) for c in self.controllers.values() if c.get("osc")]
            for target, token in targets:
                try:
                    self.knobs.sendto(osc_encode(address, *args, token), target)
                except OSError:
                    pass
        with self.lock:
            self.sent.append(("knobs", (address, list(args)), time.monotonic()))
        if self.echo_delay:
            threading.Thread(target=send, daemon=True).start()
        else:
            send()

    def _to_controllers(self, message):
        with self.lock:
            conns = [c["conn"] for c in self.controllers.values()]
        for conn in conns:
            self._send_line(conn, "control", message)

    def _node(self, uid):
        for n in self.document["nodes"]:
            if n["uid"] == uid:
                return n
        return None

    def state_message(self):
        with self.lock:
            return {"op": "state", "revision": self.revision, "seq": self.seq,
                    "document": copy.deepcopy(self.document), "speakers": copy.deepcopy(self.speakers),
                    "gestures": [{"node": k[0], "id": k[1], "origin": v} for k, v in self.gestures.items()]}

    # -- what a test does to the hub ------------------------------------------------------
    def change_param(self, node, pid, value, origin="ctl-other"):
        """A change from another controller: stamped, applied and echoed on both links."""
        with self.lock:
            self.seq += 1
            seq = self.seq
            n = self._node(node)
            if n is not None:
                n.setdefault("params", {})[pid] = value
        message = {"op": "param", "node": node, "id": pid, "value": value, "seq": seq, "origin": origin}
        if isinstance(value, (int, float)):   # on OSC only numbers: a choice's text goes on control only
            self._osc_out("/bpi/param", node, pid, float(value), seq, origin)
        self._to_controllers(message)

    def new_revision(self, document, origin="ctl-other"):
        """A revision from another controller, applied: sent to every controller."""
        with self.lock:
            self.revision += 1
            self.document = copy.deepcopy(document)
            self.document["revision"] = self.revision
            message = {"op": "graph", "revision": self.revision, "base": self.revision - 1,
                       "time": "2026-10-28T09:15:02Z", "origin": origin, "document": copy.deepcopy(self.document)}
        self._to_controllers(message)

    def forge_echo(self, node, pid, value, token=None):
        """An OSC echo to every controller that is not the hub's: no token (None), or a given
        one. A controller must ignore it."""
        with self.lock:
            targets = [c["osc"] for c in self.controllers.values() if c.get("osc")]
        args = (node, pid, float(value), self.seq + 500, "ctl-forger") + (() if token is None else (token,))
        for target in targets:
            self.knobs.sendto(osc_encode("/bpi/param", *args), target)

    def stall_controllers(self):
        """The hub stops reading every control connection open now (as if stalled or stopped);
        connections made later are served normally."""
        with self.lock:
            self.stalled = {c["conn"] for c in self.controllers.values()}

    def resume_controllers(self):
        with self.lock:
            self.stalled = set()

    def set_speaker(self, name, online):
        with self.lock:
            for s in self.speakers:
                if s["name"] == name:
                    s["online"] = online
        self._to_controllers(self.state_message())

    def received_ops(self, link, op=None):
        with self.lock:
            out = []
            for l, m, t in self.received:
                if l != link:
                    continue
                if op is None or (isinstance(m, dict) and m.get("op") == op) or (isinstance(m, tuple) and m[0] == op):
                    out.append((m, t))
            return out

    # -- control link ---------------------------------------------------------------------
    def _check_hello(self, link, message, nonce, conn):
        """True for a client hello with a well-formed nonce and a valid client proof; refuses
        (and closes) otherwise."""
        name, given, nc = message.get("name", ""), message.get("auth"), message.get("nonce")
        if not is_nonce(nc):
            reason = "a missing or malformed nonce"
        elif self.secret is None:
            return True
        elif isinstance(given, str) and hmac.compare_digest(
                given.encode(), client_proof(self.secret, nonce, nc, message.get("role", ""), name).encode()):
            return True
        else:
            reason = "no proof (auth)" if given is None else "wrong proof: check the pair's secret"
        with self.lock:
            self.refused.append((link, name, reason))
        self._send_line(conn, link, {"op": "error", "about": "hello", "reason": reason})
        return False

    def _welcome(self, conn, link, ns, nc, role, name):
        """The server's proof, before anything else (a wrong one in wrong_welcome mode)."""
        if self.secret is None:
            auth = None
        elif self.wrong_welcome:
            auth = server_proof(self.secret + "-not", ns, nc, role, name)
        else:
            auth = server_proof(self.secret, ns, nc, role, name)
        self._send_line(conn, link, {"op": "welcome", "auth": auth})

    def _serve_control(self, conn):
        name = None
        nonce = secrets.token_hex(16)
        self._send_line(conn, "control", {"op": "hello", "role": "hub", "name": self.name, "revision": self.revision,
                                          "seq": self.seq, "nonce": nonce, "protocol": 1})
        authenticated = False
        try:
            for raw in conn.rfile:
                while conn in self.stalled and not self._stop.is_set():
                    time.sleep(0.01)
                line = raw.decode().strip()
                if not line:
                    continue
                message = json.loads(line)
                self._log_in("control", message)
                op = message.get("op")
                if not authenticated:
                    if op != "hello" or message.get("role") != "controller" \
                            or not self._check_hello("control", message, nonce, conn):
                        if op != "hello":
                            self._send_line(conn, "control", {"op": "error", "about": "hello", "reason": "hello first"})
                        return
                    authenticated = True
                    name = message.get("name")
                    nc = message["nonce"]
                    token = osc_token(self.secret, nonce, nc) if self.secret is not None else ""
                    with self.lock:
                        self.tokens[name] = token
                        self.controllers[name] = {"conn": conn, "token": token,
                                                  "osc": (conn.client_address[0], int(message.get("osc_port", 0)))}
                    self._welcome(conn, "control", nonce, nc, "hub", self.name)
                    self._send_line(conn, "control", self.state_message())
                elif op == "param":
                    self._param(message, "control", conn)
                elif op == "gesture":
                    self._gesture(message)
                elif op == "graph":
                    self._proposal(message, conn)
        except (OSError, ValueError):
            pass
        finally:
            with self.lock:
                if name in self.controllers and self.controllers[name]["conn"] is conn:
                    del self.controllers[name]

    def _param(self, message, link, conn=None):
        node, pid, value, origin = message.get("node"), message.get("id"), message.get("value"), message.get("origin", "")
        with self.lock:
            n = self._node(node)
            if n is None or str(pid).startswith("mon_"):
                if conn is not None:
                    self._send_line(conn, "control", {"op": "error", "about": "param", "node": node, "id": pid,
                                                      "reason": "no such node" if n is None else "a monitor is never set"})
                return
            self.seq += 1
            seq = self.seq
            n.setdefault("params", {})[pid] = value
        if isinstance(value, (int, float)):
            self._osc_out("/bpi/param", int(node), str(pid), float(value), seq, origin)
        if link == "control":
            self._to_controllers({"op": "param", "node": node, "id": pid, "value": value, "seq": seq, "origin": origin})

    def _gesture(self, message):
        key = (message.get("node"), message.get("id"))
        with self.lock:
            if message.get("state") == "begin":
                self.gestures[key] = message.get("origin")
            else:
                self.gestures.pop(key, None)
        self._to_controllers(message)

    def _proposal(self, message, conn):
        rev, base, doc = message.get("revision"), message.get("base"), message.get("document")
        with self.lock:
            reason = None
            if base != self.revision or rev != base + 1:
                reason = "stale"
            elif not isinstance(doc, dict) or doc.get("version") != 1:
                reason = "the document does not parse"
            else:
                for n in doc.get("nodes", []):
                    uri = n.get("plugin", {}).get("identifier")
                    if uri not in self.known_plugins:
                        reason = "right: no plugin %s" % uri
            if reason is None and self.reject_next:
                reason, self.reject_next = self.reject_next, None
            if reason is not None:
                self._send_line(conn, "control", {"op": "rejected", "revision": rev, "reason": reason})
                return
            self.revision = rev
            self.document = copy.deepcopy(doc)
        self._send_line(conn, "control", {"op": "applied", "revision": rev})
        self._to_controllers({"op": "graph", "revision": rev, "base": base, "time": message.get("time"),
                              "origin": message.get("origin"), "document": doc})

    # -- knobs link -----------------------------------------------------------------------
    def _serve_knobs(self):
        while not self._stop.is_set():
            try:
                packet, sender = self.knobs.recvfrom(65536)
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                address, tags, args = osc_decode(packet)
            except (ValueError, struct.error, UnicodeDecodeError):
                continue
            # Only with the token of an authenticated controller connection from that address.
            token = args[-1] if args and isinstance(args[-1], str) else None
            with self.lock:
                allowed = token is not None and tags in (",isfiss", ",issss") and any(
                    c["osc"][0] == sender[0] and hmac.compare_digest(c["token"].encode(), token.encode())
                    for c in self.controllers.values())
            if not allowed:
                with self.lock:
                    self.osc_refused += 1
                continue
            self._log_in("knobs", (address, tags, args))
            if address == "/bpi/param" and tags == ",isfiss":
                self._param({"node": args[0], "id": args[1], "value": args[2], "origin": args[4]}, "knobs")
            elif address == "/bpi/gesture" and tags == ",issss":
                self._gesture({"op": "gesture", "node": args[0], "id": args[1], "state": args[2], "origin": args[3]})

    # -- monitor links --------------------------------------------------------------------
    def _serve_monitor(self, conn, speaker):
        subs = {}
        stop = threading.Event()

        def produce():
            last = {}
            t0 = time.monotonic()
            while not stop.is_set() and not self._stop.is_set():
                now = time.monotonic()
                with self.lock:
                    items = list(subs.items())
                for stream, rate in items:
                    if now - last.get(stream, 0) < 1.0 / max(1, min(60, rate)):
                        continue
                    last[stream] = now
                    values = self.monitor_values[speaker]
                    v = values["output/level"] if stream == "output/level" else values.get(stream, values["mon"])
                    self._send_line(conn, "monitor " + speaker,
                                    {"op": "value", "stream": stream, "t": round(now - t0, 3), "v": v})
                time.sleep(0.005)

        nonce = secrets.token_hex(16)
        claimed = self.monitor_names.get(speaker, speaker)
        self._send_line(conn, "monitor " + speaker, {"op": "hello", "role": "speaker", "name": claimed,
                                                     "nonce": nonce, "protocol": 1})
        authenticated = False
        try:
            for raw in conn.rfile:
                line = raw.decode().strip()
                if not line:
                    continue
                message = json.loads(line)
                self._log_in("monitor " + speaker, message)
                if not authenticated:
                    if message.get("op") != "hello" or not self._check_hello("monitor " + speaker, message, nonce, conn):
                        if message.get("op") != "hello":
                            self._send_line(conn, "monitor " + speaker, {"op": "error", "about": "hello", "reason": "hello first"})
                        return
                    authenticated = True
                    self._welcome(conn, "monitor " + speaker, nonce, message["nonce"], "speaker", claimed)
                    threading.Thread(target=produce, daemon=True).start()
                    continue
                with self.lock:
                    if message.get("op") == "subscribe":
                        subs[message["stream"]] = int(message.get("rate_hz", 30))
                        self.subscriptions[speaker][message["stream"]] = subs[message["stream"]]
                    elif message.get("op") == "unsubscribe":
                        subs.pop(message.get("stream"), None)
                        self.subscriptions[speaker].pop(message.get("stream"), None)
        except (OSError, ValueError):
            pass
        finally:
            stop.set()
            with self.lock:
                for stream in subs:
                    self.subscriptions[speaker].pop(stream, None)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--address", default="127.0.3.1", help="the hub's (left speaker's) address")
    parser.add_argument("--right", default="127.0.3.2", help="the right speaker's monitor address")
    parser.add_argument("--probe", action="store_true", help="put the test probe in the document")
    parser.add_argument("--trace", action="store_true", help="print every line in")
    parser.add_argument("--secret-file", help="the pair's secret (default: the tests' own, %s)" % SECRET)
    parser.add_argument("--no-secret", action="store_true", help="accept every peer, as a hub before its installer ran")
    args = parser.parse_args()
    secret = SECRET
    if args.secret_file:
        with open(args.secret_file) as f:
            secret = f.read().rstrip("\r\n")
    if args.no_secret:
        secret = None
    hub = FakeHub(args.address, args.right, document=default_document(probe=args.probe), secret=secret).start()
    print("fake hub on %s (control 7450, knobs 7451, monitor 7452), right speaker on %s" % (args.address, args.right))
    seen = 0
    try:
        while True:
            time.sleep(0.2)
            if args.trace:
                with hub.lock:
                    new = hub.received[seen:]
                    seen = len(hub.received)
                for link, message, _ in new:
                    print(link, message)
    except KeyboardInterrupt:
        hub.stop()


if __name__ == "__main__":
    main()
