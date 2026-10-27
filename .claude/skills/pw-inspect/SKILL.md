---
name: pw-inspect
description: Show the PipeWire graph of a virtual speaker (or, with an env already pointing at one, any PipeWire instance): nodes, ports, links and the settings, summarised. Use when a stream does not reach the amplifier, a node is missing, or to confirm what speakerd registered.
---

# /pw-inspect

```
tools/pw-inspect.sh [left|right]
```

Prints, for the chosen virtual speaker (or the PipeWire that `XDG_RUNTIME_DIR` points at
when no speaker is named): the clock settings, every node with its class and state, every
port, and every link. Read it top to bottom: a stream that is there but unlinked is a
WirePlumber target problem; a node that is absent never registered.

Underneath it is `pw-dump` (everything, as JSON), `pw-link -l` (links) and `pw-top -b -n 1`
(load per node). Run those directly for detail, through `tests/vpair/vpair.sh run <speaker>`
for a virtual speaker, since it may live in the development container; `pw-top` is the one that shows how much of
the quantum each node uses, which is the first thing to look at when there are xruns.
