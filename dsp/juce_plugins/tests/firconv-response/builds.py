"""The builds the response test compares (run.sh): speakerd and each plugin at the current tree,
for x86_64 and aarch64, through tools/build.sh, and each plugin at a base revision, from a
`git archive` export of dsp/juce_plugins, built by the same tools/build.sh with the export as its
root. Writes <out>/builds.json, which the test module reads.

    python3 builds.py --out DIR [--base REF] PLUGIN...

The base defaults to the previous version of each plugin: the parent of the commit that raised
the plugin's declared VERSION to the one it has now, that is, the newest commit before the raise
(when the working tree raised it and HEAD has not, HEAD). Nothing is checked out: the export goes
to <out>/base-<commit>/, which the base builds use as the repository root. Run in the
development container (run.sh moves there), where tools/build.sh runs natively.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
PLUGINS_DIR = "dsp/juce_plugins"
ARCHES = ("x86_64", "aarch64")


def git(*args, cwd=ROOT, check=True):
    result = subprocess.run(["git", *args], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True)
    if check and result.returncode != 0:
        raise SystemExit(f"builds: git {' '.join(args)} failed: {result.stderr.strip()}")
    return result


def declared(text):
    """(version, shared folders) of a plugin's CMakeLists.txt, read from its text as
    cmake/SourceId.cmake reads it: comments out, the VERSION of project(), the SHARED folders of
    speaker_add_plugin(). The version is None when there is none to read."""
    text = "\n" + re.sub(r"#[^\n]*", "", text)
    project = re.search(r"[^A-Za-z0-9_]project[ \t\r\n]*\(([^)]*)\)", text, re.IGNORECASE)
    version = None
    if project:
        found = re.search(r"[ \t\r\n]VERSION[ \t\r\n]+([0-9]+\.[0-9]+\.[0-9]+)[ \t\r\n]", f" {project.group(1)} ")
        version = found.group(1) if found else None
    shared = []
    call = re.search(r"\n[ \t]*speaker_add_plugin[ \t]*\(([^)]*)\)", text)
    if call:
        keywords = {"CODE", "INPUTS", "OUTPUTS", "SOURCES", "DSP", "EDITOR", "MODULES", "TARGETS", "SHARED"}
        in_shared = False
        for token in call.group(1).split()[1:]:
            if token in keywords:
                in_shared = token == "SHARED"
            elif in_shared:
                shared.append(token)
    return version, shared


def declared_at(rev, plugin):
    """(version, shared) of the plugin at commit `rev`, or (None, []) where it has no
    CMakeLists.txt."""
    shown = git("show", f"{rev}:{PLUGINS_DIR}/{plugin}/CMakeLists.txt", check=False)
    return declared(shown.stdout) if shown.returncode == 0 else (None, [])


def default_base(plugin, current_version):
    """The previous version's commit: see the module's docstring."""
    head = git("rev-parse", "HEAD").stdout.strip()
    if declared_at(head, plugin)[0] != current_version:
        return head
    path = f"{PLUGINS_DIR}/{plugin}/CMakeLists.txt"
    for commit in git("rev-list", "HEAD", "--", path).stdout.split():
        parents = git("rev-list", "--parents", "-n", "1", commit).stdout.split()[1:]
        if not parents:
            break
        if declared_at(parents[0], plugin)[0] != current_version:
            return parents[0]
    raise SystemExit(f"builds: no commit in HEAD's history has another version of {plugin} than "
                     f"{current_version}: name a base with --base REF")


def source_id_at(rev, plugin, shared):
    """The source identity a clean checkout of `rev` embeds in the plugin
    (dsp/juce_plugins/README.md, "Versions and source identity"), computed from git."""
    empty = git("hash-object", "-t", "tree", "/dev/null").stdout.strip()
    inputs = [plugin] + [f"shared/{name}" for name in shared] + ["cmake", "JUCE"]
    objects = []
    for name in inputs:
        found = git("rev-parse", "--verify", "--quiet", f"{rev}:{PLUGINS_DIR}/{name}", check=False)
        objects.append(found.stdout.strip() if found.returncode == 0 else empty)
    hashed = subprocess.run(["git", "hash-object", "--stdin"], cwd=ROOT, input="".join(o + "\n" for o in objects),
                            stdout=subprocess.PIPE, text=True, check=True)
    return hashed.stdout.strip()[:12]


def build_info(bundle):
    """(version, source id) from a bundle's buildinfo.ttl, or (None, None) without one."""
    try:
        text = (Path(bundle) / "buildinfo.ttl").read_text()
    except OSError:
        return None, None
    version = re.search(r'bpi:version\s+"([^"]*)"', text)
    source = re.search(r'bpi:sourceId\s+"([^"]*)"', text)
    return (version.group(1) if version else None), (source.group(1) if source else None)


def bundle_version(bundle):
    """The version JUCE wrote into the bundle's dsp.ttl (doap:revision)."""
    found = re.search(r'doap:revision\s+"([^"]*)"', (Path(bundle) / "dsp.ttl").read_text())
    return found.group(1) if found else None


def plugin_uri(bundle):
    """The URI the bundle's manifest declares `a lv2:Plugin`."""
    found = re.search(r"<([^>]+)>\s+a\s+lv2:Plugin", (Path(bundle) / "manifest.ttl").read_text())
    if not found:
        raise SystemExit(f"builds: {bundle}/manifest.ttl declares no lv2:Plugin")
    return found.group(1)


def run_build(build_sh, what, arch, log, env=None):
    """tools/build.sh <what> [aarch64] [complete] (a plugin's complete target), its output in `log`."""
    args = [str(build_sh), what] + (["aarch64"] if arch == "aarch64" else [])
    if what != "speakerd":
        args.append("complete")
    print(f"builds: {' '.join(args[1:])} ({build_sh.parent.parent}), log {log}", flush=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "w") as out:
        result = subprocess.run(args, cwd=build_sh.parent.parent, env=env, stdin=subprocess.DEVNULL,
                                stdout=out, stderr=subprocess.STDOUT)
    if result.returncode != 0:
        lines = log.read_text(errors="replace").splitlines()
        sys.stderr.write("\n".join(lines[-30:]) + "\n")
        raise SystemExit(f"builds: tools/build.sh {' '.join(args[1:])} failed (status {result.returncode}); log {log}")


def artefact(root, what, arch):
    top = Path(root) / "build" / ("aarch64" if arch == "aarch64" else "") / what
    if what == "speakerd":
        return top / "speakerd_artefacts" / "Release" / "speakerd"
    return top / f"{what}_artefacts" / "Release" / "LV2" / f"{what}.lv2"


def export_base(commit, out):
    """dsp/juce_plugins of `commit`, by git archive, with the current tools/build.sh and
    tools/container.sh beside it, so the base is built by today's build script from its own
    sources (its toolchain file, cmake/ and plugin folders)."""
    root = out / f"base-{commit[:12]}"
    done = root / ".exported"
    if not done.exists():
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True)
        archive = subprocess.Popen(["git", "archive", "--format=tar", commit, PLUGINS_DIR], cwd=ROOT,
                                   stdout=subprocess.PIPE)
        subprocess.run(["tar", "-x", "-C", str(root)], stdin=archive.stdout, check=True)
        if archive.wait() != 0:
            raise SystemExit(f"builds: git archive {commit} {PLUGINS_DIR} failed")
        done.write_text(commit + "\n")
    (root / "tools").mkdir(exist_ok=True)
    for name in ("build.sh", "container.sh"):
        shutil.copy2(ROOT / "tools" / name, root / "tools" / name)
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--base", help="the base revision (default: each plugin's previous version)")
    parser.add_argument("plugins", nargs="+")
    options = parser.parse_args()
    out = options.out.resolve()
    logs = out / "logs"
    juce = os.environ.get("JUCE_INSTALL_DIR")
    if not juce:
        raise SystemExit("builds: JUCE_INSTALL_DIR is not set (run.sh sets it)")
    juce_commit = Path(juce, ".juce-commit").read_text().strip()

    head = git("rev-parse", "HEAD").stdout.strip()
    manifest = {"root": str(ROOT), "head": head, "speakerd": {}, "plugins": {}}
    build_sh = ROOT / "tools" / "build.sh"
    out.mkdir(parents=True, exist_ok=True)
    (out / "builds.json").unlink(missing_ok=True)     # the test never reads a stale one

    # Every base first, so that a bad one fails before anything is built.
    bases = {}
    for plugin in options.plugins:
        current_version, _ = declared((ROOT / PLUGINS_DIR / plugin / "CMakeLists.txt").read_text())
        if current_version is None:
            raise SystemExit(f"builds: {PLUGINS_DIR}/{plugin}/CMakeLists.txt declares no VERSION")
        if options.base:
            found = git("rev-parse", "--verify", "--quiet", f"{options.base}^{{commit}}", check=False)
            if found.returncode != 0:
                raise SystemExit(f"builds: --base {options.base} is not a commit")
            base = found.stdout.strip()
        else:
            base = default_base(plugin, current_version)
        base_version, base_shared = declared_at(base, plugin)
        if base_version is None:
            raise SystemExit(f"builds: {plugin} has no CMakeLists.txt with a VERSION at {base[:12]}")
        base_juce = git("rev-parse", "--verify", "--quiet", f"{base}:{PLUGINS_DIR}/JUCE", check=False).stdout.strip()
        if base_juce != juce_commit:
            raise SystemExit(f"builds: {base[:12]} pins JUCE {base_juce[:12]}, the JUCE install "
                             f"({juce}) is {juce_commit[:12]}: a base on another JUCE pin is not supported")
        bases[plugin] = (current_version, base, base_version, base_shared)

    for arch in ARCHES:
        run_build(build_sh, "speakerd", arch, logs / f"speakerd-{arch}.log")
        manifest["speakerd"][arch] = str(artefact(ROOT, "speakerd", arch))

    for plugin, (current_version, base, base_version, base_shared) in bases.items():
        entry = {"current": {"version": current_version, "commit": head, "bundles": {}},
                 "base": {"version": base_version, "commit": base, "bundles": {}}}
        for arch in ARCHES:
            run_build(build_sh, plugin, arch, logs / f"{plugin}-current-{arch}.log")
            entry["current"]["bundles"][arch] = str(artefact(ROOT, plugin, arch))

        export = export_base(base, out)
        home = export / "home"
        shutil.rmtree(home, ignore_errors=True)
        home.mkdir()
        # The export's git sees no repository (it would otherwise find this checkout around it
        # and compute a source identity from the wrong trees): the base builds say "unknown",
        # and the identity of the base is computed from git below. The x86_64 build copies its
        # bundle into this throwaway HOME, never into a ~/.lv2 a host here reads.
        env = dict(os.environ, HOME=str(home), JUCE_INSTALL_DIR=juce, GIT_CEILING_DIRECTORIES=str(export.parent))
        for name in ("GIT_DIR", "GIT_WORK_TREE"):
            env.pop(name, None)
        for arch in ARCHES:
            run_build(export / "tools" / "build.sh", plugin, arch, logs / f"{plugin}-base-{arch}.log", env=env)
            entry["base"]["bundles"][arch] = str(artefact(export, plugin, arch))
        entry["base"]["src"] = str(export / PLUGINS_DIR / plugin)
        entry["current"]["src"] = str(ROOT / PLUGINS_DIR / plugin)

        for side in ("current", "base"):
            info = entry[side]
            bundles = info["bundles"].values()
            for bundle in bundles:
                if bundle_version(bundle) != info["version"]:
                    raise SystemExit(f"builds: {bundle} is version {bundle_version(bundle)}, "
                                     f"its CMakeLists.txt says {info['version']}")
            uris = {plugin_uri(bundle) for bundle in bundles}
            if len(uris) != 1 or (side == "base" and uris != {entry["current"]["uri"]}):
                raise SystemExit(f"builds: the {side} bundles of {plugin} declare {sorted(uris)}; "
                                 f"the renders need one URI for every build")
            info["uri"] = uris.pop()
            embedded = {build_info(bundle)[1] for bundle in bundles}
            if side == "current":
                if len(embedded) != 1 or None in embedded:
                    raise SystemExit(f"builds: the current bundles of {plugin} embed {sorted(map(str, embedded))}")
                info["source"], info["source_from"] = embedded.pop(), "buildinfo.ttl"
            else:
                info["source"], info["source_from"] = source_id_at(base, plugin, base_shared), "git"
        manifest["plugins"][plugin] = entry
        print(f"builds: {plugin} {current_version} (source {entry['current']['source']}) against "
              f"{base_version} at {base[:12]} (source {entry['base']['source']})", flush=True)

    version = subprocess.run([manifest["speakerd"]["x86_64"], "--version"], stdout=subprocess.PIPE,
                             text=True, check=True).stdout.strip()
    manifest["speakerd"]["version"] = version
    (out / "builds.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"builds: {out / 'builds.json'}", flush=True)


if __name__ == "__main__":
    main()
