"""plugin_identity (docs/autonomous-development.md, section 5): every plugin bundle says which build
it is, and speakerd says which build each of its plugin nodes runs, live and in the render.

A plugin's identity is its version (its CMakeLists.txt's `project(... VERSION)`) and its source
id (12 lowercase hex characters of the source it was built from, `-dirty` after them when the tree
had changes, or `unknown` without git), the same for all its targets. The source id is, from the
repository root (dsp/juce_plugins/README.md, "Versions and source identity"),

    git rev-parse HEAD:dsp/juce_plugins/<plugin>
                  [HEAD:dsp/juce_plugins/shared/<name> for each name of its SHARED, in order]
                  HEAD:dsp/juce_plugins/cmake HEAD:dsp/juce_plugins/JUCE
        | git hash-object --stdin | cut -c1-12

with a folder not yet in HEAD counted as the empty tree (`git hash-object -t tree /dev/null`) and
dirty, and `-dirty` appended when `git status --porcelain -- <the same paths>` prints anything.
The virtual pair runs in a git checkout with git, so here no plugin's id may be `unknown`.
`dsp/juce_plugins/tools/plugin-versions.sh list` prints `<plugin> <version> <source-id>`, one line
per plugin and nothing else, for the tree as it is; every LV2 bundle that `speaker_add_plugin()`
builds carries a `buildinfo.ttl` next to its `manifest.ttl`, exactly

    @prefix bpi: <https://github.com/AMuszkat/bpi_amp_dsp_tuning#> .

    <PLUGIN-URI>
        bpi:version "MAJOR.MINOR.PATCH" ;
        bpi:sourceId "SOURCE-ID" .

(with or without a newline after its last line). No LV2 host reads it: manifest.ttl stays as
JUCE writes it, without naming it, and speakerd reads it beside the manifest. speakerd logs, each
time a state layer, an apply or the render builds a graph, one line per plugin node:
`plugin node <uid>: <URI>, version <version>, source <source-id>, bundle <bundle dir>`, or
`..., no build identity (an older build), bundle <dir>` for a bundle without buildinfo.ttl, or
`(buildinfo.ttl unreadable)` for one it cannot read; none of these changes what it plays.

The chain is chain_cost's, the board's: Mine -> loudness_headless (16) -> firConv (17) ->
crossover (18), its bands to the woofer and the tweeter, from the bundles this test builds.

Pass when:

1. `plugin-versions.sh list` exits 0 and prints one line `<plugin> <version> <source-id>` for each
   plugin under dsp/juce_plugins (those whose CMakeLists.txt calls speaker_add_plugin), and
   nothing else; each version is the plugin's `project(... VERSION)`; no source id is `unknown`
   (git is there), each is the 12 hex characters of the command above, computed by this test
   with git from the plugin's own SHARED, and ends in `-dirty` exactly when `git status
   --porcelain` over the same paths prints anything (or one of them is not in HEAD yet); and the
   bundles loudness_headless, loudness (the complete build: all targets share the identity),
   firConv and crossover each carry buildinfo.ttl exactly as above with that plugin's version and
   that source id, and their manifest.ttl does not name it;
2. live, on both speakers, speakerd started on the chain says exactly one first-form line for
   each of 16, 17 and 18 (its URI, the plugin's version and source id, and the bundle directory
   the test gave it), and an apply of revision 2 over the local link on the left says them once
   more, once each;
3. `speakerd --render` of the same document says the same lines, once each;
4. an older build: a copy of firConv's bundle with buildinfo.ttl removed (as a build from before
   it existed), given to speakerd alone (an empty HOME and --lv2-bundle), renders
   firConv alone (Mine -> firConv -> the woofer) from layer current, sample for sample as the
   original bundle does, and says `plugin node 17: <URI>, no build identity (an older build),
   bundle <the copy>`; a copy whose buildinfo.ttl is cut off inside its version (half written)
   renders the same samples and says `(buildinfo.ttl unreadable), bundle <the copy>`.

A bundle directory said in a line is compared with the one given as paths (a trailing `/` is the
same directory).

With plugins and a speakerd from before the build identity (the plugins 0.0.1 and speakerd 0.3.x
of step 3), every part fails, saying what is missing: no plugin-versions.sh, no buildinfo.ttl, no
`plugin node` line. It needs step 3's speakerd (the local link, the Mine pin): with an older one
it fails, saying so.

    python3 -m unittest -v tests.vpair.test_plugin_identity
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np

from harness import (NO_LIMITS, RATE, ROOT, Pair, Speakerd, SpeakerdLink, built, lv2_uri, peak_dbfs, render,
                     require_speakerd_local_link, state_dir)
from test_chain_cost import CROSSOVER, FIRCONV, LOUDNESS, URIS, chain_document

PLUGINS_DIR = ROOT / "dsp" / "juce_plugins"
PLUGIN_VERSIONS = PLUGINS_DIR / "tools" / "plugin-versions.sh"

BUILDINFO = ('@prefix bpi: <https://github.com/AMuszkat/bpi_amp_dsp_tuning#> .\n'
             '\n'
             '<{uri}>\n'
             '    bpi:version "{version}" ;\n'
             '    bpi:sourceId "{source}" .\n')
LIST_LINE = re.compile(r"^(\S+) (\d+\.\d+\.\d+) ([0-9a-f]{12}(?:-dirty)?|unknown)$")
NODE_LINE = re.compile(r"^speakerd: plugin node (\d+): (\S+), (.*)$")
IDENTIFIED = re.compile(r"^version (\S+), source (\S+), bundle (.+)$")
UNIDENTIFIED = re.compile(r"^no build identity \((an older build|buildinfo\.ttl unreadable)\), bundle (.+)$")

# The chain's nodes: (plugin, the build's LV2 target).
CHAIN = {LOUDNESS: ("loudness", "loudness_headless"), FIRCONV: ("firConv", "firConv"),
         CROSSOVER: ("crossover", "crossover")}
# The bundles of part 1: target -> (plugin, built()'s kind).
BUNDLES = {"loudness_headless": ("loudness", "headless"), "loudness": ("loudness", "complete"),
           "firConv": ("firConv", "complete"), "crossover": ("crossover", "complete")}

# firConv alone, for the older builds: Mine -> firConv (17) -> the woofer.
FIRCONV_ALONE = {"format": "speakerd-graph", "version": 1, "revision": 1,
                 "nodes": [{"uid": FIRCONV, "plugin": {"format": "LV2", "identifier": URIS[FIRCONV]},
                            "params": {"gain": -6.0}}],
                 "connections": [{"src": {"node": 1, "channel": 2}, "dst": {"node": FIRCONV, "channel": 0}},
                                 {"src": {"node": FIRCONV, "channel": 0}, "dst": {"node": 2, "channel": 0}}]}
RENDERED = ", layer current (graph.json), revision 1, written to "


def plugin_dirs():
    """The plugins under dsp/juce_plugins: the folders whose CMakeLists.txt calls speaker_add_plugin()."""
    return sorted(d.name for d in PLUGINS_DIR.iterdir()
                  if (d / "CMakeLists.txt").is_file() and "speaker_add_plugin(" in (d / "CMakeLists.txt").read_text())


def project_version(plugin):
    """The VERSION of the plugin's project() in its CMakeLists.txt."""
    found = re.search(r"project\(\s*\S+\s+VERSION\s+(\d+\.\d+\.\d+)", (PLUGINS_DIR / plugin / "CMakeLists.txt").read_text())
    return found.group(1) if found else None


def shared_folders(plugin):
    """The SHARED folders of the plugin's speaker_add_plugin() call, in order, read from the text
    (comments out), independently of cmake/SourceId.cmake."""
    text = "\n" + re.sub(r"#[^\n]*", "", (PLUGINS_DIR / plugin / "CMakeLists.txt").read_text())
    call = re.search(r"\n[ \t]*speaker_add_plugin[ \t]*\(([^)]*)\)", text)
    if not call:
        return []
    keywords = {"CODE", "INPUTS", "OUTPUTS", "SOURCES", "DSP", "EDITOR", "MODULES", "TARGETS", "SHARED"}
    shared, in_shared = [], False
    for token in call.group(1).split():
        if token in keywords:
            in_shared = token == "SHARED"
        elif in_shared:
            shared.append(token)
    return shared


def git(test, *args, stdin=None):
    """git's stdout, run at the repository root; fails the test when git fails."""
    result = subprocess.run(["git", "--no-optional-locks", "-C", str(ROOT), *args], input=stdin,
                            capture_output=True, text=True, timeout=60)
    test.assertEqual(result.returncode, 0, f"git {' '.join(args)} exited with status {result.returncode}: "
                                           f"{result.stderr.strip()}")
    return result.stdout


def git_source_id(test, plugin):
    """The plugin's source id as dsp/juce_plugins/README.md's command gives it, computed here with
    git: the 12 hex characters, `-dirty` after them exactly when `git status --porcelain` over the
    same paths prints anything or one of them is not in HEAD yet (the empty tree, and dirty)."""
    if shutil.which("git") is None:
        test.fail("there is no git: the virtual pair runs in a git checkout, where every source id is known")
    if subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--verify", "--quiet", "HEAD^{commit}"],
                      capture_output=True, timeout=60).returncode != 0:
        test.fail(f"{ROOT} is not a git checkout with a commit: the virtual pair runs in one")
    paths = ([f"dsp/juce_plugins/{plugin}"] + [f"dsp/juce_plugins/shared/{name}" for name in shared_folders(plugin)]
             + ["dsp/juce_plugins/cmake", "dsp/juce_plugins/JUCE"])
    revisions, not_in_head = [], []
    for path in paths:
        if subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--verify", "--quiet", f"HEAD:{path}"],
                          capture_output=True, timeout=60).returncode == 0:
            revisions.append(f"HEAD:{path}")
        else:
            not_in_head.append(path)
            revisions.append(git(test, "hash-object", "-t", "tree", "/dev/null").strip())
    source = git(test, "hash-object", "--stdin", stdin=git(test, "rev-parse", *revisions)).strip()[:12]
    test.assertRegex(source, r"^[0-9a-f]{12}$", f"{plugin}: git hash-object printed {source!r}")
    changes = git(test, "status", "--porcelain", "--", *paths)
    return source + ("-dirty" if changes or not_in_head else ""), paths, changes, not_in_head


def listed_identities(test):
    """What `plugin-versions.sh list` prints, {plugin: (version, source id)}, after checking that it
    exits 0, every line is `<plugin> <version> <source-id>` and there is one per plugin."""
    if not PLUGIN_VERSIONS.exists():
        test.fail(f"there is no {PLUGIN_VERSIONS.relative_to(ROOT)}: the plugins of this tree have no build identity")
    result = subprocess.run([str(PLUGIN_VERSIONS), "list"], cwd=ROOT, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=300)
    said = f"\n  stdout:\n{result.stdout}  stderr:\n{result.stderr}"
    test.assertEqual(result.returncode, 0, f"plugin-versions.sh list exited with status {result.returncode}{said}")
    lines = result.stdout.splitlines()
    bad = [line for line in lines if not LIST_LINE.match(line)]
    test.assertEqual(bad, [], f"plugin-versions.sh list printed lines that are not '<plugin> <version> "
                              f"<source-id>'{said}")
    names = [LIST_LINE.match(line).group(1) for line in lines]
    test.assertEqual(sorted(names), plugin_dirs(), f"plugin-versions.sh list does not list each plugin once{said}")
    return {m.group(1): (m.group(2), m.group(3)) for m in map(LIST_LINE.match, lines)}


def same_dir(a, b):
    return os.path.realpath(a.rstrip("/") or "/") == os.path.realpath(str(b))


def node_lines(lines):
    """speakerd's `plugin node` lines among `lines`: {uid: [(URI, the rest), ...]}."""
    out = {}
    for line in lines:
        found = NODE_LINE.match(line)
        if found:
            out.setdefault(int(found.group(1)), []).append((found.group(2), found.group(3), line))
    return out


def identified_problems(found, expected, where):
    """What is wrong with `found` (node_lines()) against `expected`, {uid: (URI, version, source,
    bundle)}: one first-form line per node, no other."""
    problems = []
    for uid, (uri, version, source, bundle) in sorted(expected.items()):
        lines = found.get(uid, [])
        if len(lines) != 1:
            problems.append(f"{where}: {len(lines)} 'plugin node {uid}: ...' lines, not 1")
            continue
        said_uri, rest, line = lines[0]
        identity = IDENTIFIED.match(rest)
        if said_uri != uri or not identity or identity.group(1) != version or identity.group(2) != source \
                or not same_dir(identity.group(3), bundle):
            problems.append(f"{where}: '{line}', not 'speakerd: plugin node {uid}: {uri}, version {version}, "
                            f"source {source}, bundle {bundle}'")
    for uid in sorted(set(found) - set(expected)):
        problems.append(f"{where}: a line for node {uid}, which the graph does not have: {found[uid]}")
    return problems


def noise(seconds, level=0.05, seed=2):
    return np.clip(np.random.default_rng(seed).standard_normal(int(seconds * RATE)) * level, -0.9, 0.9)


class PluginIdentity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundles = {target: built(plugin, kind) for target, (plugin, kind) in BUNDLES.items()}
        cls.chain_bundles = [cls.bundles[target] for _, target in CHAIN.values()]
        built("speakerd")
        cls.tmp = Path(tempfile.mkdtemp(prefix="vpair-identity-"))
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)

    def expected_nodes(self):
        """{uid: (URI, version, source id, bundle)} for the chain: the project() version and the
        source id computed with git."""
        return {uid: (lv2_uri(target), project_version(plugin), git_source_id(self, plugin)[0], self.bundles[target])
                for uid, (plugin, target) in CHAIN.items()}

    def test_1_each_bundle_carries_its_build_identity(self):
        listed = listed_identities(self)
        problems, rows, truth = [], [], {}
        for plugin, (version, source) in sorted(listed.items()):
            truth[plugin], paths, changes, not_in_head = git_source_id(self, plugin)
            rows.append(f"plugin-versions.sh: {plugin} {version} {source}; git: {truth[plugin]}"
                        + (f" ({len(changes.splitlines())} changed paths)" if changes else "")
                        + (f" (not in HEAD: {', '.join(not_in_head)})" if not_in_head else ""))
            if version != project_version(plugin):
                problems.append(f"{plugin}: plugin-versions.sh says version {version}, its CMakeLists.txt's project() "
                                f"{project_version(plugin)}")
            if source == "unknown":
                problems.append(f"{plugin}: plugin-versions.sh says source unknown, though git is there and gives "
                                f"{truth[plugin]}")
            elif source != truth[plugin]:
                problems.append(f"{plugin}: plugin-versions.sh says source {source}, the documented git command over "
                                f"{' '.join(paths)} gives {truth[plugin]}"
                                + (f"; git status --porcelain prints:\n{changes}" if changes else
                                   "; git status --porcelain prints nothing"))
        for target, (plugin, _) in BUNDLES.items():
            bundle = self.bundles[target]
            uri = lv2_uri(target)
            version, source = project_version(plugin), truth[plugin]
            want = BUILDINFO.format(uri=uri, version=version, source=source)
            info = bundle / "buildinfo.ttl"
            if not info.is_file():
                problems.append(f"{target}: {bundle} has no buildinfo.ttl")
                continue
            text = info.read_text(errors="replace")
            rows.append(f"{target}: buildinfo.ttl says " + " ".join(re.findall(r'"([^"]*)"', text)))
            if text not in (want, want.rstrip("\n")):
                problems.append(f"{target}: buildinfo.ttl is\n{text}\n  not\n{want}")
            naming = [line for line in (bundle / "manifest.ttl").read_text().splitlines() if "buildinfo.ttl" in line]
            if naming:
                problems.append(f"{target}: manifest.ttl names buildinfo.ttl, which no LV2 host is to read: {naming}")
        print("\n  " + "\n  ".join(rows))
        self.assertEqual(problems, [], "\n".join(problems))

    def test_2_speakerd_says_each_node_s_build_live(self):
        require_speakerd_local_link(self)
        state = {name: state_dir(self.tmp / f"live-{name}", role=name, current=chain_document(), limits=NO_LIMITS)
                 for name in ("left", "right")}
        with Pair() as pair, \
                Speakerd(pair["left"], state["left"], bundles=self.chain_bundles) as left, \
                Speakerd(pair["right"], state["right"], bundles=self.chain_bundles) as right:
            at_start = {name: sd.lines_starting("plugin node ") for name, sd in (("left", left), ("right", right))}
            mark = left.mark()
            with SpeakerdLink(pair["left"]) as link:
                answer = link.apply(chain_document(revision=2, firconv_gain=-9.0), revision=2)
            self.assertEqual(answer.get("op"), "applied", f"the apply of revision 2: {answer}\n{left.log()}")
            left.wait_for("apply: revision 2: applied", after=mark)
            at_apply = left.lines_starting("plugin node ", after=mark)
        print("\n  " + "\n  ".join([f"{name}, at start: {line}" for name, lines in at_start.items() for line in lines]
                                   + [f"left, at the apply: {line}" for line in at_apply]))
        found = {where: node_lines(lines) for where, lines in
                 (("left, layer current", at_start["left"]), ("right, layer current", at_start["right"]),
                  ("left, the apply of revision 2", at_apply))}
        self.assertTrue(any(found.values()), "speakerd said no 'plugin node <uid>: ...' line at all:\n" + left.log())
        expected = self.expected_nodes()
        problems = [p for where, lines in found.items() for p in identified_problems(lines, expected, where)]
        self.assertEqual(problems, [], "\n".join(problems))

    def test_3_the_render_says_the_same(self):
        require_speakerd_local_link(self)
        state = state_dir(self.tmp / "render", role="left", current=chain_document(), limits=NO_LIMITS)
        _, log = render(state, noise(1.0), bundles=self.chain_bundles)
        lines = [line for line in log.splitlines() if line.startswith("speakerd: plugin node ")]
        print("\n  render: " + "\n  render: ".join(lines or ["(no plugin node line)"]))
        self.assertIn(RENDERED, log, f"the render did not run the chain:\n{log}")
        self.assertTrue(lines, f"the render said no 'plugin node <uid>: ...' line:\n{log}")
        problems = identified_problems(node_lines(lines), self.expected_nodes(), "the render")
        self.assertEqual(problems, [], "\n".join(problems) + f"\n{log}")

    def test_4_an_older_build_and_a_broken_buildinfo(self):
        require_speakerd_local_link(self)
        original = self.bundles["firConv"]
        uri = lv2_uri("firConv")

        # A build from before buildinfo.ttl: the same bundle without it (the manifest never names it).
        older = self.tmp / "older" / original.name
        shutil.copytree(original, older)
        (older / "buildinfo.ttl").unlink(missing_ok=True)

        # Half written: cut off inside its version, as by a power cut during a copy.
        broken = self.tmp / "broken" / original.name
        shutil.copytree(original, broken)
        info = original / "buildinfo.ttl"
        text = info.read_text() if info.is_file() else BUILDINFO.format(uri=uri, version="0.0.0", source="unknown")
        (broken / "buildinfo.ttl").write_text(text[:text.index('bpi:version "') + len('bpi:version "') + 1])

        x = noise(1.0)
        outputs, problems, rows = {}, [], []
        for what, bundle, said in (("the original", original, None), ("the older build", older, "an older build"),
                                   ("the broken buildinfo.ttl", broken, "buildinfo.ttl unreadable")):
            state = state_dir(self.tmp / f"alone-{bundle.parent.name}", role="left", current=FIRCONV_ALONE,
                              limits=NO_LIMITS)
            outputs[what], log = render(state, x, bundles=[bundle])   # fails unless it exits 0
            self.assertIn(RENDERED, log, f"{what}: the render did not run firConv from layer current:\n{log}")
            lines = [line for line in log.splitlines() if line.startswith("speakerd: plugin node ")]
            rows += [f"{what}: {line}" for line in lines] or [f"{what}: (no plugin node line)"]
            if said is None:
                continue
            found = node_lines(lines)
            got = found.get(FIRCONV, [])
            match = UNIDENTIFIED.match(got[0][1]) if len(got) == 1 else None
            if len(got) != 1 or got[0][0] != uri or not match or match.group(1) != said \
                    or not same_dir(match.group(2), bundle):
                problems.append(f"{what}: {[line for _, _, line in got] or 'no plugin node line'}, not one "
                                f"'speakerd: plugin node {FIRCONV}: {uri}, no build identity ({said}), bundle {bundle}'")
            if set(found) - {FIRCONV}:
                problems.append(f"{what}: lines for nodes the graph does not have: {found}")
            if not np.array_equal(outputs[what], outputs["the original"]):
                worst = float(np.max(np.abs(outputs[what] - outputs["the original"])))
                problems.append(f"{what}: renders other samples than the original bundle (worst difference "
                                f"{20 * np.log10(max(worst, 1e-12)):.1f} dBFS)")
        woofer = peak_dbfs(outputs["the original"][:, 0])
        rows.append(f"firConv alone, the woofer peaks at {woofer:.1f} dBFS")
        print("\n  " + "\n  ".join(rows))
        self.assertGreater(woofer, -60.0, "firConv alone renders silence: the comparison would prove nothing")
        self.assertEqual(problems, [], "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
