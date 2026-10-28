#!/usr/bin/env python3
"""A stand-in speakerd for ampd's tests: the local link of docs/protocol.md and nothing else
(no PipeWire, no audio). It lets the protocol tests (test_ampd_protocol.py) check ampd against a
speakerd that does exactly what it is told, including failing.

    stand_in_speakerd.py --socket PATH --state-dir DIR --events FILE [--control FILE] [--version TEXT]

- At start it "loads" the first layer of DIR that parses as JSON with nodes and connections
  (graph.json, graph.lkg.json, factory.json), else passthrough, and greets each client with
  {"op":"hello","role":"speakerd","version":...,"revision":...,"layer":...,"protocol":1}.
- One client at a time: a second gets {"op":"error","reason":"busy"} and is closed.
- Applies are queued and answered in order, as speakerd's are (docs/protocol.md, clarification
  18): `applied` after `delay` seconds, or `failed` with the reason in the control file's "fail"
  ({"<revision>": "<reason>"}), or not at all for a revision in its "silent".
- `param` is recorded, and answered with `{"op":"error","about":"param",...}` as speakerd answers
  (clarification 15): a node the current document lacks, a `mon_` ID, a value that is neither a
  number, a text nor true/false, or a value the control file's "invalid" lists
  ({"<node>/<id>": [values]}).
- `subscribe` makes values at the rate asked (60 at most, 0 or less refused): output/level is the
  control file's "level" (four numbers, default [-12, -15, -120, -120]), node/<uid>/<mon_id> its
  "monitors"[stream] (a number), speakerd/timing the control file's "timing" (an object, default
  TIMING with "cycles_total" counting up), and nothing while the current document has no node <uid>
  (clarification 19: the stream is kept and starts when a revision has it). A name that is no
  stream, or one in the control file's "refuse_streams", is answered with an error.
- `status` is answered with the hello's fields.
- Every line it reads and the hellos it sends go to the events file, one JSON object per line
  with "dir" ("in" or "out") and "t" (time.monotonic()), for the test to read.
- SIGTERM ends it, status 0.
"""

import argparse
import asyncio
import json
import os
import re
import signal
import sys
import time

# speakerd/timing's object as speakerd's README (Timing) shows it.
TIMING = {"cycles": 47, "late": 0, "jumps": 0,
          "cycle": {"mean": 0.001765, "max": 0.003544}, "graph": {"mean": 0.001572, "max": 0.003237},
          "nodes": [{"uid": 16, "name": "loudness_headless", "mean": 0.0009778, "max": 0.001554}],
          "cycles_total": 0, "late_total": 0, "jumps_total": 1, "dropped_total": 0}


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
                    send({"op": "value", "stream": stream, "t": time.monotonic(),
                          "v": control.get("level", [-12.0, -15.0, -120.0, -120.0])})
                elif stream == "speakerd/timing":
                    timing = dict(control.get("timing", TIMING))
                    timing["cycles_total"] = timing.get("cycles_total", 0) + int(time.monotonic() * 47)
                    send({"op": "value", "stream": stream, "t": time.monotonic(), "v": timing})
                elif int(stream.split("/")[1]) in {n.get("uid") for n in self.doc.get("nodes", [])}:
                    send({"op": "value", "stream": stream, "t": time.monotonic(),
                          "v": control.get("monitors", {}).get(stream, -30.0)})
                await asyncio.sleep(1.0 / subs[stream])

        applies = asyncio.Queue()

        async def apply_in_order():
            while True:
                await answer_apply(await applies.get())

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
        in_order = asyncio.get_running_loop().create_task(apply_in_order())
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                msg = json.loads(line)
                self.record("in", msg)
                op = msg.get("op")
                if op == "apply":
                    applies.put_nowait(msg)
                elif op == "param":
                    uids = {n.get("uid") for n in self.doc.get("nodes", [])}
                    value, pid = msg.get("value"), str(msg.get("id"))
                    invalid = self.control().get("invalid", {}).get(f"{msg.get('node')}/{pid}", [])
                    reason = ("no such node" if msg.get("node") not in uids
                              else "a monitor is never set" if pid.startswith("mon_")
                              else "not a value" if not isinstance(value, (int, float, str, bool))
                              else "not a value of this parameter" if value in invalid else None)
                    if reason:
                        send({"op": "error", "about": "param", "node": msg.get("node"), "id": msg.get("id"),
                              "reason": reason})
                elif op == "subscribe":
                    stream, rate = msg.get("stream"), msg.get("rate_hz", 30)
                    is_stream = isinstance(stream, str) and (stream in ("output/level", "speakerd/timing") or re.fullmatch(
                        r"node/(1[6-9]|[2-9][0-9]|[1-9][0-9]{2,8})/mon_[A-Za-z0-9_]{1,64}", stream))
                    if not is_stream or stream in self.control().get("refuse_streams", []) \
                            or not isinstance(rate, (int, float)) or rate <= 0:
                        send({"op": "error", "about": "subscribe", "stream": stream, "reason": "no such stream"})
                        continue
                    fresh = stream not in subs
                    subs[stream] = min(float(rate), 60.0)
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
            in_order.cancel()
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
