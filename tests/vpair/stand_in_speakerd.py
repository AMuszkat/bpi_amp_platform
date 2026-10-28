#!/usr/bin/env python3
"""A stand-in speakerd for ampd's tests: the local link of docs/protocol.md and nothing else
(no PipeWire, no audio). It lets the protocol tests (test_ampd_protocol.py) check ampd against a
speakerd that does exactly what it is told, including failing.

    stand_in_speakerd.py --socket PATH --state-dir DIR --events FILE [--control FILE] [--version TEXT]

- At start it "loads" the first layer of DIR that parses as JSON with nodes and connections
  (graph.json, graph.lkg.json, factory.json), else passthrough, and greets each client with
  {"op":"hello","role":"speakerd","version":...,"revision":...,"layer":...,"protocol":1}.
- One client at a time: a second gets {"op":"error","reason":"busy"} and is closed.
- `apply` is answered `applied` after `delay` seconds, or `failed` with the reason in the control
  file's "fail" ({"<revision>": "<reason>"}), or not at all for a revision in its "silent".
- `param` is recorded; a node the current document lacks gets an error.
- `subscribe` makes values at the rate asked: output/level is the control file's "level" (four
  numbers, default [-12, -15, -120, -120]), node/<uid>/<mon_id> its "monitors"[stream] (a number).
- `status` is answered with the hello's fields.
- Every line it reads and the hellos it sends go to the events file, one JSON object per line
  with "dir" ("in" or "out") and "t" (time.monotonic()), for the test to read.
- SIGTERM ends it, status 0.
"""

import argparse
import asyncio
import json
import os
import signal
import sys
import time


def first_layer(state):
    for layer, name in (("current", "graph.json"), ("last-known-good", "graph.lkg.json"), ("factory", "factory.json")):
        try:
            with open(os.path.join(state, name)) as f:
                doc = json.load(f)
            if isinstance(doc, dict) and isinstance(doc.get("nodes"), list) and isinstance(doc.get("connections"), list):
                return layer, doc
        except (OSError, ValueError):
            continue
    return "passthrough", {"revision": 0, "nodes": [], "connections": []}


class StandIn:
    def __init__(self, args):
        self.args = args
        self.layer, self.doc = first_layer(args.state_dir)
        self.busy = False
        self.events = open(args.events, "a", buffering=1)

    def control(self):
        try:
            with open(self.args.control) as f:
                return json.load(f)
        except (OSError, ValueError, TypeError):
            return {}

    def record(self, direction, obj):
        self.events.write(json.dumps({"dir": direction, "t": time.monotonic(), **obj}) + "\n")

    def hello(self, op="hello"):
        return {"op": op, "role": "speakerd", "version": self.args.version, "revision": self.doc.get("revision", 0),
                "layer": self.layer, "protocol": 1}

    async def client(self, reader, writer):
        def send(obj):
            writer.write(json.dumps(obj).encode() + b"\n")

        if self.busy:
            send({"op": "error", "reason": "busy"})
            self.record("out", {"op": "error", "reason": "busy"})
            await writer.drain()
            writer.close()
            return
        self.busy = True
        subs, producers = {}, {}

        async def produce(stream):
            while stream in subs:
                control = self.control()
                if stream == "output/level":
                    v = control.get("level", [-12.0, -15.0, -120.0, -120.0])
                else:
                    v = control.get("monitors", {}).get(stream, -30.0)
                send({"op": "value", "stream": stream, "t": time.monotonic(), "v": v})
                await asyncio.sleep(1.0 / subs[stream])

        async def answer_apply(msg):
            control = self.control()
            revision = msg.get("revision")
            await asyncio.sleep(float(control.get("delay", 0.0)))
            if str(revision) in control.get("silent", []) or revision in control.get("silent", []):
                return
            reason = control.get("fail", {}).get(str(revision))
            if reason:
                out = {"op": "failed", "revision": revision, "reason": reason}
            else:
                self.doc, self.layer = dict(msg.get("document") or {}, revision=revision), "current"
                out = {"op": "applied", "revision": revision}
            self.record("out", out)
            send(out)

        send(self.hello())
        self.record("out", self.hello())
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                msg = json.loads(line)
                self.record("in", msg)
                op = msg.get("op")
                if op == "apply":
                    asyncio.get_running_loop().create_task(answer_apply(msg))
                elif op == "param":
                    uids = {n.get("uid") for n in self.doc.get("nodes", [])}
                    if msg.get("node") not in uids:
                        send({"op": "error", "about": "param", "node": msg.get("node"), "id": msg.get("id"),
                              "reason": "no such node"})
                elif op == "subscribe":
                    stream = msg.get("stream")
                    fresh = stream not in subs
                    subs[stream] = max(0.1, min(float(msg.get("rate_hz", 30)), 60.0))
                    if fresh:
                        producers[stream] = asyncio.get_running_loop().create_task(produce(stream))
                elif op == "unsubscribe":
                    if subs.pop(msg.get("stream"), None) is not None:
                        producers.pop(msg.get("stream")).cancel()
                elif op == "status":
                    send(self.hello("status"))
        except (ConnectionError, ValueError):
            pass
        finally:
            subs.clear()
            for task in producers.values():
                task.cancel()
            self.busy = False
            writer.close()

    async def run(self):
        os.makedirs(os.path.dirname(self.args.socket), exist_ok=True)
        try:
            os.unlink(self.args.socket)
        except OSError:
            pass
        server = await asyncio.start_unix_server(self.client, self.args.socket, limit=64 * 1024 * 1024)
        os.chmod(self.args.socket, 0o600)
        print(f"stand-in speakerd: layer {self.layer}, revision {self.doc.get('revision', 0)}, socket {self.args.socket}",
              file=sys.stderr, flush=True)
        stop = asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        await stop.wait()
        server.close()
        try:
            os.unlink(self.args.socket)
        except OSError:
            pass
        print("stand-in speakerd: stopping", file=sys.stderr, flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--events", required=True)
    parser.add_argument("--control")
    parser.add_argument("--version", default="0.3.0 (stand-in)")
    asyncio.run(StandIn(parser.parse_args()).run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
