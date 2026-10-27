---
name: roadmap
description: Where the remote plugin host stands and who does the next step. Run it at the start of any session on this repository, and whenever a task does not say which agent should do it: it prints the roadmap steps of docs/remote-plugin-host.md with their status, the next step, the agent that owns it and what to ask that agent for.
---

# /roadmap

```
tools/roadmap.py          # every step with its status, the next one, the routing rule
tools/roadmap.py --next   # one line: the next step and its agent
```

The status is derived from the tree and the last recorded test run, not from a hand-kept
list, so it cannot go stale: a step counts as done when the files it produces exist and
the virtual pair scenario that proves it exists.

Then route the work:

| The task | Agent |
|---|---|
| changes one area only: plugins, `speakerd`, the controller, or `firmware`/`tests/vpair` | that area's agent: `plugin-dev`, `host-dev`, `controller-dev`, `board-dev` |
| changes several areas, or a protocol shared between them | `orchestrator`, which splits it across the area agents in parallel worktrees |
| "is it green?", before a review or a pull request | `verifier` |
| before merging | `reviewer` |
| anything on a real board | `board-ops`, and only after the verifier says both rungs are green |

The agent definitions are in `.claude/agents/`; each one's description says what it is for,
and its rules say what it never does. When a task's area is unclear, ask which files it
would change: the path decides.
