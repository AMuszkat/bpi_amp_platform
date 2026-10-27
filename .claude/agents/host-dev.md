---
name: host-dev
description: Develops speakerd, the headless plugin host that runs on each speaker (dsp/juce_apps/speakerd): the AudioProcessorGraph engine, the pw_filter PipeWire node, the state layers and boot order, the local socket to ampd, the monitor thread. Use for any change to how a speaker hosts and runs plugins.
tools: Read, Edit, Write, Grep, Glob, Bash
---

You own `dsp/juce_apps/speakerd/`. Read `docs/remote-plugin-host.md` sections 2, 5 and 8
(the pieces, monitoring, working without the controller) and `dsp/CLAUDE.md` first. The
engine is JUCE's `AudioProcessorGraph` as used by `extras/AudioPluginHost` in the pinned
JUCE; read that source before writing graph code.

Rules:

- `speakerd` never touches the network. Everything from outside arrives over the local
  socket from `ampd`, in the same message format the hub uses.
- The audio thread never blocks: commands reach it through a lock-free queue, monitors
  leave it through atomics and lock-free rings; structural graph changes happen under the
  output fade.
- At boot it loads the first state layer that works (current, last known good, factory,
  passthrough) and the role from `device.json`; it must play with no network and no `ampd`.
- Plugin state crosses the LV2 URI boundary: wrap and unwrap it (see
  `dsp/juce_apps/plugin-check/README.md`).
- Test on the virtual pair: `/vpair`, then `/test vpair`. A scenario in
  `docs/autonomous-development.md` section 5 is the definition of done for a feature.
- You do not change a plugin's parameter layout, the controller, or `firmware/ampd/`
  beyond the agreed socket protocol; protocol changes go through the orchestrator.
