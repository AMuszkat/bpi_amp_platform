"""Test harness for the virtual pair (see README.md).

It plays into and records from the virtual speakers that vpair.sh starts, with the same pw-*
tools a person would use, and analyses what the "amplifier" of each speaker received. It
also builds and runs speakerd on a speaker, and renders through it offline.

    from harness import Pair, tone
    with Pair() as pair:
        received = pair["left"].play_and_record(tone(440.0, -20.0, 1.0))

    from harness import Speakerd, built, state_dir
    with Pair() as pair, Speakerd(pair["left"], state_dir(d, current=doc),
                                  bundles=[built("loudness", "headless")]):
        received = pair["left"].play_and_record(x, target="speakerd", record_target="amplifier")

    from harness import Player, Recorder
    with Player(pair["left"], long_tone, target="speakerd") as stream:   # in the background
        with Recorder(pair["left"], "amplifier", fmt="f32") as recorder:
            ...
            received = recorder.stop()
"""

import itertools
import json
import os
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
VPAIR = HERE / "vpair.sh"
VPAIR_DIR = Path(os.environ.get("VPAIR_DIR", "/tmp/vpair"))
RATE = 48000


def _into_the_container():
    """Where the virtual pair runs in the development container (tools/container.sh, chosen
    by BPI_CONTAINER as for the shell scripts), the tests must run there too: re-runs this
    very command line in it."""
    if os.environ.get("BPI_IN_CONTAINER") or os.environ.get("BPI_CONTAINER", "auto") == "0":
        return
    if os.environ.get("BPI_CONTAINER") != "1" and not shutil.which("podman"):
        return
    container = str(ROOT / "tools" / "container.sh")
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(container, [container, "python3", *sys.orig_argv[1:]])


_into_the_container()

import numpy as np  # noqa: E402  (after the move into the container, which has numpy)


# ---------------------------------------------------------------------------------------------
# Signals and analysis

def tone(freq_hz, level_dbfs, seconds, rate=RATE):
    """A mono sine, `level_dbfs` peak."""
    t = np.arange(int(seconds * rate)) / rate
    return (10 ** (level_dbfs / 20.0)) * np.sin(2 * np.pi * freq_hz * t)


def silence(seconds, rate=RATE):
    return np.zeros(int(seconds * rate))


def rms_dbfs(x):
    return 20 * np.log10(max(np.sqrt(np.mean(np.square(x))), 1e-12)) if len(x) else -240.0


def peak_dbfs(x):
    return 20 * np.log10(max(np.max(np.abs(x)), 1e-12)) if len(x) else -240.0


def peak_frequency(x, rate=RATE):
    spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return np.fft.rfftfreq(len(x), 1.0 / rate)[int(np.argmax(spectrum))]


def sine_amplitudes_dbfs(x, freqs_hz, rate=RATE):
    """The peak amplitude of each `freqs_hz` component of `x`, in dBFS, by a least-squares fit
    of a sine and a cosine at every frequency at once: exact for steady tones at those
    frequencies, and close to nothing for a frequency that is not in `x`."""
    if len(x) == 0:
        return [-240.0 for _ in freqs_hz]
    t = np.arange(len(x)) / rate
    basis = np.stack([f(2 * np.pi * freq * t) for freq in freqs_hz for f in (np.sin, np.cos)], axis=1)
    c = np.linalg.lstsq(basis, x, rcond=None)[0]
    return [20 * np.log10(max(float(np.hypot(c[2 * i], c[2 * i + 1])), 1e-12))
            for i in range(len(freqs_hz))]


def active(x, threshold_dbfs=-60.0, block=480):
    """The part of a recording between the first and last block above the threshold. A
    recording with two channels counts as loud where either channel is."""
    mono = np.max(np.abs(x), axis=1) if x.ndim > 1 else x
    n = len(mono) // block
    levels = [rms_dbfs(mono[i * block:(i + 1) * block]) for i in range(n)]
    loud = [i for i, level in enumerate(levels) if level > threshold_dbfs]
    if not loud:
        return x[:0]
    return x[loud[0] * block:(loud[-1] + 1) * block]


def steady(x, skip=0.3, rate=RATE):
    """`x` without its first and last `skip` seconds: the steady part of a tone, after the
    attack, glide or fade of whatever made it."""
    n = int(skip * rate)
    return x[n:len(x) - n]


def align(capture, reference):
    """The lag at which `reference` sits in `capture` (capture[lag + i] matches reference[i]),
    by the cross-correlation of the two: both mono, or column 0 of each is used."""
    c = capture[:, 0] if capture.ndim > 1 else capture
    r = reference[:, 0] if reference.ndim > 1 else reference
    n = 1 << int(np.ceil(np.log2(len(c) + len(r))))
    correlation = np.fft.irfft(np.fft.rfft(c, n) * np.conj(np.fft.rfft(r, n)), n)
    return int(np.argmax(correlation[:len(c)]))


def _write_wav(path, samples, rate=RATE):
    """`samples` is mono, or (n, channels). Written as 32-bit integer PCM, or as 32-bit float
    when a sample is beyond full scale: pw-play and PipeWire carry a float WAV's samples above
    0 dBFS through unclipped."""
    data = np.asarray(samples, dtype=np.float64)
    channels = 1 if data.ndim == 1 else data.shape[1]
    if np.max(np.abs(data), initial=0.0) > 1.0:
        frames = data.astype("<f4").tobytes()
        fmt = struct.pack("<HHIIHH", 3, channels, rate, rate * 4 * channels, 4 * channels, 32)
        Path(path).write_bytes(b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(frames))
                               + b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
                               + b"data" + struct.pack("<I", len(frames)) + frames)
        return
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(4)
        w.setframerate(rate)
        w.writeframes(np.round(data * (2 ** 31 - 1)).astype("<i4").tobytes())


def _read_wav(path):
    """A WAV file as an (n, channels) array of floats, full scale 1.0: 16- or 32-bit integer PCM,
    or 32-bit float, as pw-record and speakerd's render write them. A float file's samples are
    returned as they are, above full scale and NaN included. A file whose writer was stopped
    before it finished its header is read to its end."""
    data = Path(path).read_bytes()
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError(f"{path} is not a WAV file")
    fmt, frames, at = None, None, 12
    while at + 8 <= len(data):
        chunk, size = data[at:at + 4], struct.unpack("<I", data[at + 4:at + 8])[0]
        if chunk == b"fmt ":
            fmt = struct.unpack("<HHIIHH", data[at + 8:at + 24])
            if fmt[0] == 0xFFFE:   # WAVE_FORMAT_EXTENSIBLE: the format is in its sub-format GUID
                fmt = (struct.unpack("<H", data[at + 32:at + 34])[0],) + fmt[1:]
        elif chunk == b"data":
            end = len(data) if size in (0, 0xFFFFFFFF) else min(len(data), at + 8 + size)
            frames = data[at + 8:end]
            break
        at += 8 + size + (size & 1)
    if fmt is None or frames is None:
        raise ValueError(f"{path} has no fmt or data chunk")
    tag, channels, _, _, _, bits = fmt
    dtype, scale = {(1, 16): ("<i2", 2.0 ** 15), (1, 32): ("<i4", 2.0 ** 31),
                    (3, 32): ("<f4", 1.0)}.get((tag, bits), (None, None))
    if dtype is None:
        raise ValueError(f"{path}: format {tag}, {bits} bits is not read here")
    width = channels * bits // 8
    x = np.frombuffer(frames[:len(frames) // width * width], dtype=dtype).reshape(-1, channels)
    return x.astype(np.float64) / scale


# ---------------------------------------------------------------------------------------------
# The virtual speakers

class Speaker:
    """One virtual speaker: runs pw-* tools against its own PipeWire."""

    def __init__(self, name):
        self.name = name
        exports = subprocess.run([str(VPAIR), "env", name], check=True,
                                 capture_output=True, text=True).stdout
        self.env = dict(os.environ)
        for line in exports.splitlines():
            for assignment in line.replace("export ", "").split():
                key, _, value = assignment.partition("=")
                self.env[key] = value.strip("'")

    def health(self):
        """Nothing ("") while this speaker's PipeWire and WirePlumber run; otherwise which is gone,
        with the end of its log, for a failure message. A test that waits for a link or a port
        and gets none fails with this, rather than with a bare timeout."""
        gone = []
        for daemon in ("pipewire", "wireplumber"):
            try:
                os.kill(int((VPAIR_DIR / self.name / f"{daemon}.pid").read_text()), 0)
                continue
            except (OSError, ValueError):
                pass
            log = VPAIR_DIR / self.name / f"{daemon}.log"
            tail = log.read_text(errors="replace").splitlines()[-8:] if log.exists() else []
            gone.append(f"{self.name}: {daemon} is not running"
                        + (" (WirePlumber 0.4.13 dies silently 5 s after a null sink that went "
                           "idle less than 5 s earlier is destroyed: see remove_sink())"
                           if daemon == "wireplumber" else "")
                        + "; the end of its log:\n" + "\n".join("    " + line for line in tail))
        return "\n".join(gone)

    def run(self, *args, timeout=10):
        """Runs a tool against this speaker, as `vpair.sh run <speaker> <tool...>`, and returns
        what it printed. Fails when the tool does."""
        return subprocess.run([str(VPAIR), "run", self.name, *args], env=self.env, check=True,
                              capture_output=True, text=True, timeout=timeout).stdout

    def links(self):
        """The links in this speaker's PipeWire, as pw-link -l lists them: a set of
        ("node:output port", "node:input port") pairs."""
        pairs, port = set(), None
        for line in self.run("pw-link", "-l").splitlines():
            if not line.startswith(" "):
                port = line.strip()
            elif line.strip().startswith("|->") and port is not None:
                pairs.add((port, line.strip()[len("|->"):].strip()))
        return pairs

    def wait_for_links(self, wanted, timeout=5.0, what="the links", process=None):
        """Waits until every (output, input) pair in `wanted` is linked, polling pw-link, and
        returns the links. Fails with the links there are on a timeout, or when `process` (the
        player or recorder that is to be linked) exits first."""
        deadline = time.monotonic() + timeout
        while True:
            try:
                have = self.links()
            except subprocess.SubprocessError:   # pw-link failed: no PipeWire, say why below
                have = set()
            if set(wanted) <= have:
                return have
            exited = process is not None and process.poll() is not None
            if exited or time.monotonic() > deadline:
                why = (f"its process exited with status {process.returncode}" if exited
                       else f"not within {timeout} s")
                health = self.health()
                raise AssertionError(
                    f"{self.name}: {what} did not appear ({why}): wanted "
                    + ", ".join(f"{o} -> {i}" for o, i in sorted(wanted)) + "; there are "
                    + (", ".join(f"{o} -> {i}" for o, i in sorted(have)) or "none")
                    + (f"\n{health}" if health else ""))
            time.sleep(0.02)

    def play_and_record(self, samples, target="amplifier", record_target=None, fmt="s32", tail=0.4):
        """Plays `samples` (mono, or (n, 2) for FL and FR) into `target` and returns what the
        monitor of `record_target` (default: `target` itself) received, as an (n, 2) array:
        column 0 is the woofer channel, column 1 the tweeter channel. The recording is linked
        before the player starts, and the player has finished when this returns."""
        return self.play_and_record_many(samples, target, [(record_target or target, fmt)], tail)[0]

    def play_and_record_many(self, samples, target, records, tail=0.4):
        """Plays `samples` into `target` while recording each (node, format) of `records` at
        once (format "s32" or "f32", see Recorder), and returns the recordings in that order.
        After the player has finished, the recordings go on for `tail` seconds: what the player
        handed over is still on its way through the graph."""
        recorders = []
        try:
            for node, fmt in records:
                recorders.append(Recorder(self, node, fmt).start())
            with Player(self, samples, target, linked=False) as player:
                player.wait(timeout=len(samples) / RATE + 10)
            time.sleep(tail)
            return [recorder.stop() for recorder in recorders]
        finally:
            for recorder in recorders:
                recorder.close()

    def record(self, seconds, target="amplifier", fmt="s32"):
        """Records `target`'s monitor for `seconds` from the moment it is linked, playing
        nothing."""
        with Recorder(self, target, fmt) as recorder:
            time.sleep(seconds)
            return recorder.stop()

    def create_sink(self, name):
        """Adds a null sink like the amplifier (woofer FL, tweeter FR) to this speaker's
        PipeWire. It stays until remove_node() or the speaker goes down."""
        self.run("pw-cli", "create-node", "adapter",
                 "{ factory.name = support.null-audio-sink node.name = " + name
                 + " media.class = Audio/Sink audio.position = [ FL FR ] node.driver = true"
                 + " monitor.channel-volumes = false object.linger = true }")

    def has_sink(self, name):
        """Whether a sink called `name`, with its ports, is in this speaker's PipeWire."""
        return any(line.startswith(name + ":playback_") for line in self.run("pw-link", "-i").splitlines())

    def remove_sink(self, name):
        """Destroys a null sink made by create_sink() while a player of silence keeps it running.
        WirePlumber 0.4.13 dies silently about 5 s after the destroy of a null sink that went idle
        less than 5 s earlier (its suspend timer is still pending), and new nodes get no ports
        from then on. A sink that runs has no such timer."""
        with Player(self, silence(10.0), target=name):
            self.remove_node(name)

    def remove_node(self, name):
        """Destroys a node. For a null sink use remove_sink(), unless it is running."""
        self.run("pw-cli", "destroy", name)

    def set_quantum(self, quantum):
        """Forces this speaker's quantum (pw-metadata -n settings 0 clock.force-quantum), or
        lets it go back to its own with 0. PipeWire 0.3.65 rounds a forced quantum down to a
        power of two (clock.power-of-two-quantum, true by default, as on the boards)."""
        self.run("pw-metadata", "-n", "settings", "0", "clock.force-quantum", str(quantum))


_names = itertools.count(1)


def _stop_process(process):
    """SIGTERM, and SIGKILL if that does not end it within 5 s."""
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


class Recorder:
    """pw-record of one node's monitor on a speaker (a sink's monitor ports), as 32-bit integer
    ("s32", which cannot show anything above full scale) or 32-bit float ("f32", which shows
    every value as it is, NaN and above full scale included). start(), or entering a `with`
    block, returns once WirePlumber has linked the recorder to the node, so nothing played
    after it is missed. stop() returns the recording, (n, 2); close(), or leaving the block,
    ends it whatever happened."""

    def __init__(self, speaker, target, fmt="s32", timeout=5.0):
        if fmt not in ("s32", "f32"):
            raise ValueError(f"no recording format {fmt}: s32 or f32")
        self.speaker, self.target, self.fmt, self.timeout = speaker, target, fmt, timeout
        self.name = f"vpair-record-{os.getpid()}-{next(_names)}"
        self.process, self._tmp = None, None

    def start(self):
        self._tmp = tempfile.mkdtemp(prefix="vpair-record-")
        self.path = Path(self._tmp) / "record.wav"
        self.process = subprocess.Popen(
            ["pw-record", "--target", self.target,
             "-P", "{ stream.capture.sink = true node.name = " + self.name + " }",
             "--rate", str(RATE), "--channels", "2", "--format", self.fmt, str(self.path)],
            env=self.speaker.env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        try:
            self.speaker.wait_for_links(
                {(f"{self.target}:monitor_{c}", f"{self.name}:input_{c}") for c in ("FL", "FR")},
                self.timeout, f"the recorder of {self.target}", self.process)
        except BaseException:
            self.close()
            raise
        return self

    def stop(self):
        """Ends the recording and returns it."""
        _stop_process(self.process)
        try:
            return _read_wav(self.path)
        finally:
            self.close()

    def close(self):
        if self.process is not None:
            _stop_process(self.process)
        if self._tmp is not None:
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()
        return False


class Player:
    """pw-play of `samples` (mono, or (n, 2) for FL and FR) into `target` on a speaker, in the
    background, for a `with` block. Entering returns once WirePlumber has linked the player to
    `target` (unless `linked` is False); leaving stops it if it still plays. Its node is named
    `name` (a unique vpair-play-... by default), which is how pw-link shows its ports."""

    def __init__(self, speaker, samples, target="amplifier", linked=True, timeout=5.0, name=None):
        self.speaker, self.samples, self.target = speaker, samples, target
        self.linked, self.timeout = linked, timeout
        self.name = name or f"vpair-play-{os.getpid()}-{next(_names)}"
        self.process, self._tmp = None, None

    def __enter__(self):
        self._tmp = tempfile.mkdtemp(prefix="vpair-play-")
        path = Path(self._tmp) / "play.wav"
        _write_wav(path, self.samples)
        self.process = subprocess.Popen(
            ["pw-play", "--target", self.target, "-P", "{ node.name = " + self.name + " }", str(path)],
            env=self.speaker.env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, text=True)
        if self.linked:
            try:
                self.speaker.wait_for_links(
                    {(f"{self.name}:output_{c}", f"{self.target}:playback_{c}") for c in ("FL", "FR")},
                    self.timeout, f"the player into {self.target}", self.process)
            except BaseException:
                self.__exit__(None, None, None)
                raise
        return self

    def playing(self):
        return self.process.poll() is None

    def wait(self, timeout):
        """Waits for the player to finish, and fails if it did not play to the end."""
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise AssertionError(f"pw-play into {self.target} did not finish within {timeout} s")
        if self.process.returncode != 0:
            raise AssertionError(f"pw-play into {self.target} exited with status "
                                 f"{self.process.returncode}: {self.process.stderr.read()}")

    def __exit__(self, *exc):
        if self.process is not None:
            _stop_process(self.process)
            self.process.stderr.close()
        if self._tmp is not None:
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None
        return False


class Pair:
    """Brings the virtual pair up for a `with` block and down afterwards.

    `setup` gives a speaker its own start: {"left": {"speaker_conf": path, "dropins": folder}}
    starts the left speaker's PipeWire from `path` instead of speaker.conf, with the drop-ins
    of `folder` (as a board loads ~/.config/pipewire/pipewire.conf.d)."""

    def __init__(self, setup=None):
        self.setup = dict(setup or {})

    def __enter__(self):
        if self.setup:
            for name in ("left", "right"):
                self._up(name)
        else:
            self._up()
        self.speakers = {name: Speaker(name) for name in ("left", "right")}
        return self

    def __getitem__(self, name):
        return self.speakers[name]

    def _up(self, *names):
        """vpair.sh up; when a speaker does not come up, takes the pair down again and fails
        with what vpair.sh and the speakers' logs said."""
        env = dict(os.environ)
        env.pop("VPAIR_SPEAKER_CONF", None)
        env.pop("VPAIR_PIPEWIRE_DROPINS", None)
        if len(names) == 1:
            setup = self.setup.get(names[0], {})
            if setup.get("speaker_conf"):
                env["VPAIR_SPEAKER_CONF"] = str(setup["speaker_conf"])
            if setup.get("dropins"):
                env["VPAIR_PIPEWIRE_DROPINS"] = str(setup["dropins"])
        result = subprocess.run([str(VPAIR), "up", *names], capture_output=True, text=True,
                                timeout=60, env=env)
        if result.returncode == 0:
            return
        logs = []
        for log in sorted(VPAIR_DIR.glob("*/*.log")):
            tail = log.read_text(errors="replace").splitlines()[-15:]
            logs.append(f"  {log}:\n" + "\n".join("    " + line for line in tail))
        subprocess.run([str(VPAIR), "down"], check=False, capture_output=True, timeout=30)
        raise RuntimeError(f"vpair.sh up {' '.join(names)} failed (exit {result.returncode}):\n"
                           f"{result.stdout}{result.stderr}" + "\n".join(logs))

    def down(self, name):
        """Takes one speaker down: its PipeWire, WirePlumber and D-Bus session."""
        subprocess.run([str(VPAIR), "down", name], check=True, capture_output=True, timeout=30)

    def up(self, name, **setup):
        """Brings one speaker (back) up, and returns it; `setup` as for Pair()."""
        if setup:
            self.setup[name] = setup
        self._up(name)
        self.speakers[name] = Speaker(name)
        return self.speakers[name]

    def __exit__(self, *exc):
        subprocess.run([str(VPAIR), "down"], check=False, capture_output=True, timeout=30)
        return False


# ---------------------------------------------------------------------------------------------
# Builds

BUILD = ROOT / "build"
LV2_URI_BASE = "https://github.com/AMuszkat/bpi_amp_dsp_tuning/"

_built = {}


def built(what, kind=None):
    """Builds an application (`built("speakerd")`) or one build of a plugin
    (`built("loudness", "headless")`, `"complete"`) with tools/build.sh, once per test process,
    and returns the program or the LV2 bundle. tools/build.sh is incremental, so this is fast
    when nothing changed."""
    key = (what, kind)
    if key not in _built:
        command = [str(ROOT / "tools" / "build.sh"), what] + ([kind] if kind else [])
        started = time.monotonic()
        result = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        if result.returncode != 0:
            tail = "\n".join(result.stdout.splitlines()[-40:])
            raise RuntimeError(f"tools/build.sh {what} {kind or ''} failed "
                               f"(exit {result.returncode}):\n{tail}")
        if kind is None:
            path = BUILD / what / f"{what}_artefacts" / "Release" / what
        else:
            target = what if kind == "complete" else f"{what}_{kind}"
            path = BUILD / what / f"{target}_artefacts" / "Release" / "LV2" / f"{target}.lv2"
        if not path.exists():
            raise RuntimeError(f"tools/build.sh {what} {kind or ''} built no {path}")
        print(f"(built {path.relative_to(ROOT)} in {time.monotonic() - started:.1f} s)",
              file=sys.stderr, flush=True)
        _built[key] = path
    return _built[key]


def lv2_uri(target):
    """The LV2 URI of a plugin build: `loudness_headless`, `loudness` (the complete build)."""
    return LV2_URI_BASE + target


# ---------------------------------------------------------------------------------------------
# speakerd's files (dsp/juce_apps/speakerd/README.md)

def graph_document(plugin_uri, params, revision=1, input_channel=0):
    """A graph document, version 1, with one mono plugin (uid 16) between one input channel
    (0 = FL, 1 = FR) and both amplifier channels, taking `params` by parameter ID."""
    return {
        "format": "speakerd-graph",
        "version": 1,
        "revision": revision,
        "nodes": [{"uid": 16, "plugin": {"format": "LV2", "identifier": plugin_uri},
                   "params": params}],
        "connections": [
            {"src": {"node": 1, "channel": input_channel}, "dst": {"node": 16, "channel": 0}},
            {"src": {"node": 16, "channel": 0}, "dst": {"node": 2, "channel": 0}},
            {"src": {"node": 16, "channel": 0}, "dst": {"node": 2, "channel": 1}},
        ],
    }


def with_plugin(document, plugin_uri):
    """The same document with every node's plugin replaced by `plugin_uri`: the render of a
    speaker's document with the complete build is the reference its capture is compared with."""
    copy = json.loads(json.dumps(document))
    for node in copy["nodes"]:
        node["plugin"]["identifier"] = plugin_uri
    return copy


def state_dir(path, role="left", amplifier="amplifier", current=None, lkg=None, factory=None):
    """Fills `path` as speakerd's state directory and returns it: device.json, and the state
    layers graph.json (`current`), graph.lkg.json (`lkg`) and factory.json (`factory`). A
    layer is a document (dict, written as JSON), text (written as it is, for a broken file),
    or None (no file)."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    (path / "device.json").write_text(json.dumps(
        {"version": 1, "role": role, "amplifier": {"node": amplifier}}, indent=2))
    for name, layer in (("graph.json", current), ("graph.lkg.json", lkg),
                        ("factory.json", factory)):
        if layer is None:
            (path / name).unlink(missing_ok=True)
        else:
            (path / name).write_text(layer if isinstance(layer, str) else json.dumps(layer, indent=2))
    return path


# ---------------------------------------------------------------------------------------------
# speakerd

class Speakerd:
    """speakerd running on one virtual speaker, for a `with` block.

    It runs with `state` as its state directory, the LV2 `bundles` given and an empty HOME, so
    it sees exactly those plugins (lilv would otherwise prefer copies in ~/.lv2). Entering
    waits for the log line `wait_for` (by default the "playing" line, from which a player is
    heard; None waits for nothing). Leaving stops it with SIGTERM, if it still runs, and checks
    that "stopping" was its last line, said once, and that it exited with `exit_status`; after
    a failure inside the block it is stopped all the same, killed if need be.

        with Speakerd(speaker, state, bundles=[bundle]) as sd:
            sd.wait_for("linked to amplifier ")
    """

    def __init__(self, speaker, state, bundles=(), args=(), wait_for="playing: ", timeout=30,
                 prefix=(), exit_status=0):
        self.speaker = speaker
        self.command = [*prefix, str(built("speakerd")), "--state-dir", str(state)]
        for bundle in bundles:
            self.command += ["--lv2-bundle", str(bundle)]
        self.command += list(args)
        self.first_wait, self.first_timeout = wait_for, timeout
        self.exit_status = exit_status
        self.lines = []
        self.process = None
        self._changed = threading.Condition()

    # The log ------------------------------------------------------------------------------

    def _read(self):
        for line in self.process.stdout:
            with self._changed:
                self.lines.append(line.rstrip("\n"))
                self._changed.notify_all()
        with self._changed:
            self._changed.notify_all()

    def log(self):
        with self._changed:
            return "\n".join(self.lines)

    def mark(self):
        """The number of log lines so far, for wait_for(..., after=)."""
        with self._changed:
            return len(self.lines)

    def lines_starting(self, text, after=0):
        """The log lines from line `after` on that start with "speakerd: " + `text`."""
        with self._changed:
            return [line for line in self.lines[after:] if line.startswith("speakerd: " + text)]

    def own_lines(self):
        """speakerd's own lines (those starting with "speakerd: "), without what the libraries
        it loads print."""
        with self._changed:
            return [line for line in self.lines if line.startswith("speakerd: ")]

    def wait_for(self, text, timeout=30, after=0, required=True):
        """Waits for a log line starting with "speakerd: " + `text` (or with any of the texts,
        given a tuple), from line `after` on, and returns it. Fails, with speakerd's output, on
        a timeout (or returns None, if not `required`) or when speakerd exits."""
        texts = (text,) if isinstance(text, str) else tuple(text)
        wanted = " or ".join(f"'{t}'" for t in texts)
        deadline = time.monotonic() + timeout
        with self._changed:
            while True:
                for line in self.lines[after:]:
                    if any(line.startswith("speakerd: " + t) for t in texts):
                        return line
                remaining = deadline - time.monotonic()
                if self.process.poll() is not None and not self._reader.is_alive():
                    raise AssertionError(self._report(
                        f"speakerd exited with status {self.process.returncode} before it "
                        f"said {wanted}"))
                if remaining <= 0:
                    if not required:
                        return None
                    raise AssertionError(self._report(
                        f"speakerd did not say {wanted} within {timeout} s"))
                self._changed.wait(min(remaining, 0.5))

    def _report(self, what):
        health = self.speaker.health()
        return (f"{what}\n  command: {' '.join(self.command)}\n  its output:\n"
                + "\n".join("    " + line for line in self.lines)
                + (f"\n{health}" if health else ""))

    # Running ------------------------------------------------------------------------------

    def __enter__(self):
        self._home = tempfile.mkdtemp(prefix="speakerd-home-")
        self.process = subprocess.Popen(self.command, env=dict(self.speaker.env, HOME=self._home),
                                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, bufsize=1)
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        try:
            if self.first_wait:
                self.wait_for(self.first_wait, self.first_timeout)
        except BaseException:
            self._stop(check=False)
            raise
        return self

    def __exit__(self, exc_type, exc, tb):
        self._stop(check=exc_type is None)
        return False

    def stop(self, timeout=10):
        """Sends SIGTERM and waits for speakerd to exit, with its whole log read; returns its
        exit status. Leaving the block then checks the status and the log."""
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
        return self.wait_exit(timeout)

    def wait_exit(self, timeout=10):
        """Waits for speakerd to exit on its own, with its whole log read, and returns its exit
        status. Fails, with its output, if it still runs after `timeout` seconds."""
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise AssertionError(self._report(f"speakerd still runs after {timeout} s"))
        self._reader.join(timeout=5)
        return self.process.returncode

    def _stop(self, check):
        try:
            if self.process.poll() is None:
                self.process.send_signal(signal.SIGTERM)
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
                if check:
                    raise AssertionError(self._report("speakerd did not stop within 10 s of SIGTERM"))
            self._reader.join(timeout=5)
            self.process.stdout.close()
            if check:
                if self.process.returncode != self.exit_status:
                    raise AssertionError(self._report(
                        f"speakerd exited with status {self.process.returncode}, not "
                        f"{self.exit_status}"))
                own = self.own_lines()
                if own.count("speakerd: stopping") != 1 or own[-1] != "speakerd: stopping":
                    raise AssertionError(self._report(
                        "speakerd did not say 'stopping' once, as its last line"))
        finally:
            shutil.rmtree(self._home, ignore_errors=True)


def render(state, samples, bundles=(), args=(), timeout=120):
    """speakerd's offline render (--render): runs `samples` (mono, or (n, 2) for FL and FR)
    through the graph of the first layer of `state` that loads, with the `bundles` given and an
    empty HOME, and returns the output, (n, 2) woofer and tweeter, and speakerd's output."""
    with tempfile.TemporaryDirectory() as tmp:
        src, out, home = Path(tmp) / "in.wav", Path(tmp) / "out.wav", Path(tmp) / "home"
        home.mkdir()
        _write_wav(src, samples)
        command = [str(built("speakerd")), "--state-dir", str(state), "--render", str(src), str(out)]
        for bundle in bundles:
            command += ["--lv2-bundle", str(bundle)]
        command += list(args)
        result = subprocess.run(command, env=dict(os.environ, HOME=str(home)),
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, timeout=timeout)
        if result.returncode != 0:
            raise AssertionError(f"the render failed with status {result.returncode}\n"
                                 f"  command: {' '.join(command)}\n  its output:\n{result.stdout}")
        return _read_wav(out), result.stdout


# ---------------------------------------------------------------------------------------------
# The board side: firmware/'s installers, the systemd unit they install, who drives the graph

FIRMWARE = ROOT / "firmware"


def firmware(script, *args, env=None, timeout=120, check=True):
    """Runs a firmware/ script (speakerd-setup.sh, setup.sh, snapcast-setup.sh) and returns the
    CompletedProcess, stdout and stderr together. Fails with its output when `check` and it
    fails."""
    result = subprocess.run(["bash", str(FIRMWARE / script), *map(str, args)],
                            env=dict(os.environ, **(env or {})), stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            timeout=timeout)
    if check and result.returncode != 0:
        raise AssertionError(f"firmware/{script} {' '.join(map(str, args))} exited with status "
                             f"{result.returncode}:\n{result.stdout}")
    return result


_stub = {}


def stub_speakerd():
    """tests/vpair/stub_speakerd.c, compiled once per test process: an ELF stand-in for speakerd
    that does what its STUB_DIR/fate file says (see the file)."""
    if "path" not in _stub:
        out = Path(tempfile.mkdtemp(prefix="vpair-stub-")) / "speakerd"
        subprocess.run(["cc", "-O1", "-Wall", "-o", str(out), str(HERE / "stub_speakerd.c")],
                       check=True, capture_output=True, text=True)
        _stub["path"] = out
    return _stub["path"]


def drivers(speaker, seconds=1.5):
    """Who drives the graph of a speaker, from pw-profiler (PipeWire 0.3.65's pw-dump does not
    say, and its pw-top has no batch mode): ({driver node names}, {follower node names}), the
    nodes that ran in the seconds watched. Needs the profiler module (speaker.conf loads it)."""
    with tempfile.TemporaryDirectory() as tmp:
        out = subprocess.run(["timeout", "-s", "INT", str(seconds), "pw-profiler", "-o",
                              str(Path(tmp) / "profiler.log")], cwd=tmp, env=speaker.env,
                             capture_output=True, text=True).stdout
    names = {}
    for obj in json.loads(speaker.run("pw-dump")):
        if obj.get("type") == "PipeWire:Interface:Node":
            names[obj["id"]] = (obj.get("info") or {}).get("props", {}).get("node.name", f"id {obj['id']}")
    found = lambda kind: {names.get(int(i), f"id {i}") for i in
                          [line.split()[2] for line in out.splitlines() if line.startswith(f"logging {kind} ")]}
    return found("driver"), found("follower"), out


class UserUnit:
    """A systemd user unit run as systemd runs it, as far as a test can without systemd (the
    container has none): the unit file's Environment= and UnsetEnvironment= on top of the
    speaker's environment, ExecStart= started, and when it has ended ExecStopPost= run with
    the variables systemd sets for it, SERVICE_RESULT, EXIT_CODE and EXIT_STATUS. %h is
    `home`, %t the speaker's XDG_RUNTIME_DIR. It reads the real unit file and runs the real
    programs; it does not restart anything itself: would_restart() says whether systemd would,
    from Restart=, RestartPreventExitStatus= and RestartForceExitStatus=."""

    CLEAN_SIGNALS = {"HUP", "INT", "TERM", "PIPE"}

    def __init__(self, path, home, speaker, env=None):
        self.path, self.home, self.speaker = Path(path), Path(home), speaker
        self.extra_env = dict(env or {})
        self.service = {}
        section = None
        for raw in self.path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if line.startswith("["):
                section = line.strip("[]")
                continue
            key, _, value = line.partition("=")
            if section == "Service":
                self.service.setdefault(key.strip(), []).append(value.strip())

    def expand(self, value):
        return (value.replace("%h", str(self.home))
                .replace("%t", self.speaker.env["XDG_RUNTIME_DIR"]).replace("%%", "%"))

    def environment(self):
        env = dict(self.speaker.env, HOME=str(self.home), **self.extra_env)
        for assignment in self.service.get("Environment", []):
            for item in assignment.split():
                key, _, value = item.partition("=")
                env[key] = self.expand(value)
        for names in self.service.get("UnsetEnvironment", []):
            for name in names.split():
                env.pop(name, None)
        return env

    def command(self, key):
        value = self.expand(self.service[key][0])
        return value.lstrip("-").split()

    def start(self, env=None, log_path=None):
        """Starts ExecStart; its output is also appended to `log_path`, line by line, when
        given (what journalctl would show)."""
        return UnitRun(self, dict(self.environment(), **(env or {})), log_path)

    def would_restart(self, result):
        """Whether systemd restarts the service after `result` (UnitRun.finish's)."""
        def tokens(key):
            return {t.removeprefix("SIG") for v in self.service.get(key, []) for t in v.split()}
        status = result["EXIT_STATUS"]
        if status in tokens("RestartForceExitStatus"):
            return True
        if status in tokens("RestartPreventExitStatus"):
            return False
        restart = self.service.get("Restart", ["no"])[0]
        clean = result["SERVICE_RESULT"] == "success"
        return {"no": False, "always": True, "on-failure": not clean}.get(restart, False)


class UnitRun:
    """One start of a UserUnit: its ExecStart running, its output (stdout and stderr) as lines."""

    def __init__(self, unit, env, log_path=None):
        self.unit, self.env = unit, env
        self.log_path = Path(log_path) if log_path else None
        self.lines, self._changed = [], threading.Condition()
        self.process = subprocess.Popen(unit.command("ExecStart"), env=env, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                        bufsize=1)
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self):
        log = open(self.log_path, "a") if self.log_path else None
        for line in self.process.stdout:
            with self._changed:
                self.lines.append(line.rstrip("\n"))
                if log:
                    log.write(line)
                    log.flush()
                self._changed.notify_all()
        if log:
            log.close()
        with self._changed:
            self._changed.notify_all()

    def log(self):
        with self._changed:
            return "\n".join(self.lines)

    def wait_for(self, text, timeout=30):
        """Waits for a line starting with `text`, and returns it; fails with the output on a
        timeout or when the process exits first."""
        deadline = time.monotonic() + timeout
        with self._changed:
            while True:
                for line in self.lines:
                    if line.startswith(text):
                        return line
                if self.process.poll() is not None and not self._reader.is_alive():
                    raise AssertionError(f"the unit's process exited ({self.process.returncode}) "
                                         f"before it said '{text}':\n{self.log()}")
                if time.monotonic() > deadline:
                    raise AssertionError(f"the unit did not say '{text}' within {timeout} s:\n{self.log()}")
                self._changed.wait(0.2)

    def signal(self, sig):
        if self.process.poll() is None:
            self.process.send_signal(sig)

    def finish(self, timeout=15, core_dumped=False):
        """Waits for ExecStart's process to end (SIGKILL after `timeout`), then runs ExecStopPost
        with SERVICE_RESULT, EXIT_CODE and EXIT_STATUS as systemd sets them, and returns those
        (and "stop_post", its output)."""
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
            raise AssertionError(f"the unit's process did not end within {timeout} s:\n{self.log()}")
        self._reader.join(timeout=5)
        self.process.stdout.close()
        code = self.process.returncode
        if code >= 0:
            result = {"EXIT_CODE": "exited", "EXIT_STATUS": str(code),
                      "SERVICE_RESULT": "success" if code == 0 else "exit-code"}
        else:
            name = signal.Signals(-code).name.removeprefix("SIG")
            clean = name in UserUnit.CLEAN_SIGNALS
            result = {"EXIT_CODE": "dumped" if core_dumped else "killed", "EXIT_STATUS": name,
                      "SERVICE_RESULT": "success" if clean else ("core-dump" if core_dumped else "signal")}
        post = subprocess.run(self.unit.command("ExecStopPost"), env=dict(self.env, **result),
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
        result["stop_post"] = post.stdout + post.stderr
        with self._changed:
            self.lines += result["stop_post"].splitlines()
        return result
