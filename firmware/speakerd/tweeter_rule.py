"""The tweeter rule: only a crossover's high band may reach the tweeter.

The one definition of the rule in the Python of firmware/ (firmware/CLAUDE.md, "The tweeter
rule"): setup-helper.py checks it on every layer it installs or keeps, and on every layer before
`limits` turns the tweeter on; ampd checks it on every revision it would apply while its speaker's
device.json has the tweeter on. speakerd applies the same rule (dsp/juce_apps/speakerd/README.md).

A graph document (version 1) feeds the tweeter (output node 2, channel 1) safely only through a
crossover's high band. Walking from the input node (1, every channel, Mine included) along every
connection, a node's outputs carry the stream unfiltered for a tweeter, except a crossover's
output 1 (its high band; output 0 is its low band, the woofer's). A crossover is a node whose
plugin identifier's last segment starts with "crossover", that is not bypassed and is not a
control build ("_control": an editor with no DSP). Whatever reaches the tweeter from such an
unfiltered output breaks the rule: the stream straight, through any other plugin, through a
bypassed crossover or a control build, or a crossover's low band. Something that does not come
from the input (no path from node 1) does not.

Standard library only (a board's python3).
"""

INPUT_NODE, OUTPUT_NODE, TWEETER_CHANNEL = 1, 2, 1
# The crossover's outputs (dsp/juce_plugins/crossover/README.md): 0 is the low band, 1 the high.
CROSSOVER_HIGH = 1

MESSAGE = ("the stream reaches the tweeter other than through a crossover's high band (output {outputs}; a "
           "crossover's channel 0 is its low band): full range or the woofer's band on a tweeter can destroy it")


def is_crossover(node):
    """A crossover that filters: not bypassed, and not a control build (an editor with no DSP)."""
    name = str((node.get("plugin") or {}).get("identifier", "")).rstrip("/").rsplit("/", 1)[-1].lower()
    return name.startswith("crossover") and not name.endswith("_control") and not node.get("bypassed")


def unsafe_tweeter_feeds(doc):
    """The outputs ("node:channel") that reach the tweeter other than through a crossover's high
    band, sorted; [] when the document keeps the rule. `doc` is a graph document whose structure
    has been checked (nodes with uids, connections with src and dst node and channel)."""
    by_uid = {node["uid"]: node for node in doc.get("nodes", []) if isinstance(node, dict) and "uid" in node}
    edges, feeders = {}, set()
    for c in doc.get("connections", []):
        src, dst = (c["src"]["node"], c["src"]["channel"]), (c["dst"]["node"], c["dst"]["channel"])
        if dst == (OUTPUT_NODE, TWEETER_CHANNEL):
            feeders.add(src)
        else:
            edges.setdefault(src, set()).add(dst[0])

    def unsafe_outputs(uid):
        outputs = {s for s in list(edges) + list(feeders) if s[0] == uid}
        if uid in by_uid and is_crossover(by_uid[uid]):
            return {s for s in outputs if s[1] != CROSSOVER_HIGH}
        return outputs

    reached, todo, unsafe_out = {INPUT_NODE}, [INPUT_NODE], set()
    while todo:
        for out in unsafe_outputs(todo.pop()):
            unsafe_out.add(out)
            for nxt in edges.get(out, ()):
                if nxt not in reached:
                    reached.add(nxt)
                    todo.append(nxt)
    return sorted(f"{node}:{channel}" for node, channel in feeders & unsafe_out)


def violation(doc):
    """Why `doc` breaks the rule (a sentence), or None when it keeps it."""
    unsafe = unsafe_tweeter_feeds(doc)
    return MESSAGE.format(outputs=", ".join(unsafe)) if unsafe else None
