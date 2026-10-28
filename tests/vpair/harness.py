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

import atexit
import itertools
import json
import os
import re
import shutil
import signal
import socket
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


def block_levels(x, block, rate=RATE):
    """The RMS level in dBFS of each block of `block` seconds of `x` (mono)."""
    n = max(1, int(round(block * rate)))
    count = len(x) // n
    blocks = np.asarray(x[:count * n], dtype=np.float64).reshape(count, n)
    return 10 * np.log10(np.maximum(np.mean(blocks ** 2, axis=1), 1e-24))


def longest_run(mask):
    """The length of the longest run of True in `mask`."""
    best = run = 0
    for value in mask:
        run = run + 1 if value else 0
        best = max(best, run)
    return best


def high_band(x, cutoff_hz=4000.0, rate=RATE):
    """`x` (mono) above `cutoff_hz`, through an FFT filter with a raised-cosine edge one
    kilohertz wide (so its impulse response is a few milliseconds long): where a click is, and
    a tone below the edge is not."""
    spectrum = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1.0 / rate)
    edge = np.clip((f - (cutoff_hz - 1000.0)) / 1000.0, 0.0, 1.0)
    return np.fft.irfft(spectrum * (0.5 - 0.5 * np.cos(np.pi * edge)), len(x))


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
    prebuilt = os.environ.get("VPAIR_PREBUILT")
    if prebuilt and key not in _built:
        # A build tree laid out like build/, made elsewhere (another branch's speakerd, say):
        # used as it is, nothing is built.
        target = what if kind in (None, "complete") else f"{what}_{kind}"
        path = (Path(prebuilt) / what / f"{what}_artefacts" / "Release" / what if kind is None
                else Path(prebuilt) / what / f"{target}_artefacts" / "Release" / "LV2" / f"{target}.lv2")
        if not path.exists():
            raise RuntimeError(f"VPAIR_PREBUILT={prebuilt} has no {path}")
        print(f"(prebuilt {path})", file=sys.stderr, flush=True)
        _built[key] = path
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


NO_LIMITS = {"gain_db": 0, "tweeter": "on"}
# No output limit, the tweeter muted: what a board has once a person lifted the gain but not yet the
# tweeter. The step 3 scenarios run so (their documents send a plugin to both amplifier channels,
# which ampd refuses with the tweeter on: the tweeter rule); test_tweeter_rule turns it on.
TWEETER_MUTED = {"gain_db": 0, "tweeter": "muted"}


def state_dir(path, role="left", amplifier="amplifier", current=None, lkg=None, factory=None,
              limits=NO_LIMITS, device=None):
    """Fills `path` as speakerd's state directory and returns it: device.json, and the state
    layers graph.json (`current`), graph.lkg.json (`lkg`) and factory.json (`factory`). A
    layer is a document (dict, written as JSON), text (written as it is, for a broken file),
    or None (no file).

    device.json says its `limits` explicitly, by default none (0 dB, tweeter on): a device.json
    without them plays capped (speakerd fails closed). `limits=None` leaves them out. `device`
    replaces the whole file: a dict, text (written as it is), or False for no file."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    if device is None:
        device = {"version": 1, "role": role, "amplifier": {"node": amplifier}}
        if limits is not None:
            device["limits"] = limits
    if device is False:
        (path / "device.json").unlink(missing_ok=True)
    else:
        (path / "device.json").write_text(device if isinstance(device, str) else json.dumps(device, indent=2))
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
# The process name the installers and speakerd-run look for instead of AudioPluginHost, and the
# system LV2 folders they look in, in every test: a test must never see, wait for or stop this
# machine's own AudioPluginHost, nor read its system plugins. A test's stand-in plugin host is
# a copy of sleep with this name (at most 15 characters, the kernel's comm).
TEST_PLUGIN_HOST = "vpairPluginHost"
TEST_ENV = {"SPEAKERD_PLUGIN_HOST_NAME": TEST_PLUGIN_HOST, "SPEAKERD_SYSTEM_LV2_DIRS": "/nonexistent-vpair-lv2"}


def firmware(script, *args, env=None, timeout=120, check=True):
    """Runs a firmware/ script (speakerd-setup.sh, setup.sh, snapcast-setup.sh) and returns the
    CompletedProcess, stdout and stderr together, with TEST_ENV and `env` on top of this
    environment. Fails with its output when `check` and it fails."""
    result = subprocess.run(["bash", str(FIRMWARE / script), *map(str, args)],
                            env={**os.environ, **TEST_ENV, **(env or {})}, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            timeout=timeout)
    if check and result.returncode != 0:
        raise AssertionError(f"firmware/{script} {' '.join(map(str, args))} exited with status "
                             f"{result.returncode}:\n{result.stdout}")
    return result


_stub = {}


def stub_plugin_host(folder):
    """Starts a stand-in for AudioPluginHost (a copy of sleep called TEST_PLUGIN_HOST) and
    returns its process; the caller kills it."""
    fake = Path(folder) / TEST_PLUGIN_HOST
    fake.parent.mkdir(parents=True, exist_ok=True)
    # Beside it, then renamed over it: an earlier stand-in may still be running the old file.
    shutil.copy("/bin/sleep", fake.with_name(fake.name + ".new"))
    os.replace(fake.with_name(fake.name + ".new"), fake)
    host = subprocess.Popen([str(fake), "120"])
    time.sleep(0.2)
    return host


def stub_speakerd():
    """tests/vpair/stub_speakerd.c, compiled once per test process: an ELF stand-in for speakerd
    that does what its STUB_DIR/fate file says (see the file)."""
    if "path" not in _stub:
        out = Path(tempfile.mkdtemp(prefix="vpair-stub-")) / "speakerd"
        atexit.register(shutil.rmtree, out.parent, True)
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
        self.extra_env = {**TEST_ENV, **(env or {})}
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
        result["stop_post"] = ""
        if "ExecStopPost" in self.unit.service:
            post = subprocess.run(self.unit.command("ExecStopPost"), env=dict(self.env, **result),
                                  stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
            result["stop_post"] = post.stdout + post.stderr
        with self._changed:
            self.lines += result["stop_post"].splitlines()
        return result


# ---------------------------------------------------------------------------------------------
# ampd, controllers and monitor subscribers (step 3: docs/protocol.md)

AMPD = FIRMWARE / "ampd" / "ampd.py"
STAND_IN = HERE / "stand_in_speakerd.py"
# The two virtual speakers share one network namespace: each ampd listens on its own loopback
# address, with the protocol's ports (docs/protocol.md, "Framing and addresses").
ADDRESS = {"left": "127.0.1.1", "right": "127.0.1.2"}
CONTROL_PORT, KNOB_PORT, MONITOR_PORT = 7450, 7451, 7452


def _ampd_module():
    """firmware/ampd/ampd.py as a module, for its OSC encoder and decoder."""
    if "module" not in _stub:
        import importlib.util
        spec = importlib.util.spec_from_file_location("ampd_under_test", AMPD)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _stub["module"] = module
    return _stub["module"]


def osc_encode(address, tags, *args):
    return _ampd_module().osc_encode(address, tags, *args)


def osc_decode(data):
    return _ampd_module().osc_decode(data)


# The pair's secret in the tests' ampd.json (the installer writes the real one from a file a person
# keeps; it never appears in a tracked file). Controllers and monitor clients prove it.
TEST_SECRET = "vpair-test-secret-not-a-real-one"


def ampd_auth(nonce, role, name, secret=TEST_SECRET):
    """What a peer answers a hello's nonce with (firmware/ampd/ampd.py, auth_of)."""
    return _ampd_module().auth_of(secret, nonce, role, name)


def ampd_config(state, name, hub, listen, hub_address=None, secret=TEST_SECRET):
    """Writes ampd.json into `state` (as the installer does) and returns its path; `secret` None
    leaves the secret out."""
    path = Path(state) / "ampd.json"
    config = {"version": 1, "name": name, "hub": hub, "listen": listen, "hub_address": hub_address}
    if secret is not None:
        config["secret"] = secret
    path.write_text(json.dumps(config, indent=2))
    return path


class LoggedProcess:
    """A program run for a `with` block with its output read line by line: `prefix` is how its
    own lines start ("ampd: "). wait_for(), mark(), stop() as for Speakerd."""

    def __init__(self, command, env, prefix, wait_for=None, timeout=30, what="the program", exit_status=0):
        self.command, self.env, self.prefix = [str(c) for c in command], env, prefix
        self.first_wait, self.first_timeout, self.what = wait_for, timeout, what
        self.exit_status = exit_status
        self.lines, self.process, self._changed = [], None, threading.Condition()

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
        with self._changed:
            return len(self.lines)

    def lines_starting(self, text, after=0):
        with self._changed:
            return [line for line in self.lines[after:] if line.startswith(self.prefix + text)]

    def wait_for(self, text, timeout=30, after=0, required=True):
        texts = (text,) if isinstance(text, str) else tuple(text)
        deadline = time.monotonic() + timeout
        with self._changed:
            while True:
                for line in self.lines[after:]:
                    if any(line.startswith(self.prefix + t) for t in texts):
                        return line
                if self.process.poll() is not None and not self._reader.is_alive():
                    raise AssertionError(self.report(f"{self.what} exited with status {self.process.returncode} "
                                                     f"before it said {' or '.join(map(repr, texts))}"))
                if time.monotonic() > deadline:
                    if not required:
                        return None
                    raise AssertionError(self.report(f"{self.what} did not say {' or '.join(map(repr, texts))} "
                                                     f"within {timeout} s"))
                self._changed.wait(0.2)

    def report(self, what):
        return f"{what}\n  command: {' '.join(self.command)}\n  its output:\n" + "\n".join("    " + l for l in self.lines)

    def start(self):
        self.process = subprocess.Popen(self.command, env=self.env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, bufsize=1)
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        try:
            if self.first_wait:
                self.wait_for(self.first_wait, self.first_timeout)
        except BaseException:
            self.kill()
            raise
        return self

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc, tb):
        if self.process is None:
            return False
        if self.process.poll() is None:
            status = self.stop()
            if exc_type is None and status != self.exit_status:
                raise AssertionError(self.report(f"{self.what} exited with status {status}, not {self.exit_status}"))
        elif not self.process.stdout.closed:
            self._closed()
        return False

    def running(self):
        return self.process is not None and self.process.poll() is None

    def stop(self, timeout=10):
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
            raise AssertionError(self.report(f"{self.what} did not stop within {timeout} s of SIGTERM"))
        self._closed()
        return self.process.returncode

    def kill(self):
        """SIGKILL, as a crash would: nothing is flushed, nothing said."""
        if self.process is not None and self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=5)
        if self.process is not None:
            self._closed()

    def signal(self, sig):
        if self.process.poll() is None:
            self.process.send_signal(sig)

    def _closed(self):
        self._reader.join(timeout=5)
        if not self._reader.is_alive():
            self.process.stdout.close()


class Ampd(LoggedProcess):
    """ampd (firmware/ampd/ampd.py) for one speaker, for a `with` block: `env` is the speaker's
    (speaker.env: its own XDG_RUNTIME_DIR, so the default speakerd socket is that speaker's), its
    ampd.json is written into `state`. The left speaker is the hub on 127.0.1.1, the right an
    agent on 127.0.1.2. Entering waits for the `listening on` line; leaving stops it with SIGTERM
    and checks it exits with status 0 having said `stopping`."""

    def __init__(self, env, state, name, hub=None, listen=None, hub_address=None, args=(), wait_for="listening on ",
                 timeout=15, secret=TEST_SECRET):
        hub = (name == "left") if hub is None else hub
        listen = listen or ADDRESS.get(name, "127.0.0.1")
        hub_address = hub_address or (None if hub else ADDRESS["left"])
        self.state, self.name = Path(state), name
        self.config = ampd_config(state, name, hub, listen, hub_address, secret)
        self.listen, self.hub = listen, hub
        self.runtime = Path(env["XDG_RUNTIME_DIR"]) / "ampd"
        super().__init__([sys.executable, AMPD, "--config", self.config, "--state-dir", state, *args], dict(env),
                         "ampd: ", wait_for, timeout, f"ampd ({name})")

    def __exit__(self, exc_type, exc, tb):
        super().__exit__(exc_type, exc, tb)
        if exc_type is None and self.process is not None and self.process.returncode == 0:
            own = [line for line in self.lines if line.startswith("ampd: ")]
            if not own or own[-1] != "ampd: stopping":
                raise AssertionError(self.report(f"{self.what} did not say 'stopping' last"))
        return False

    def status(self):
        """$XDG_RUNTIME_DIR/ampd/status.json, or {} when there is none."""
        try:
            return json.loads((self.runtime / "status.json").read_text())
        except (OSError, ValueError):
            return {}

    def wait_status(self, predicate, timeout=10, what="the status"):
        deadline = time.monotonic() + timeout
        while True:
            status = self.status()
            if predicate(status):
                return status
            if time.monotonic() > deadline:
                raise AssertionError(self.report(f"{self.what}: {what} not reached within {timeout} s: {status}"))
            time.sleep(0.05)


class StandInSpeakerd(LoggedProcess):
    """tests/vpair/stand_in_speakerd.py on `env`'s socket ($XDG_RUNTIME_DIR/speakerd/control.sock):
    the local link of a speakerd, no audio. set_control() tells it what to fail; events() is
    every line it read ("in") and the answers it sent ("out")."""

    def __init__(self, env, state, control=None):
        run = Path(env["XDG_RUNTIME_DIR"]) / "speakerd"
        self.socket = run / "control.sock"
        self.events_path = Path(state) / ".stand-in-events.jsonl"
        self.control_path = Path(state) / ".stand-in-control.json"
        self.events_path.write_text("")
        self.set_control(control or {})
        super().__init__([sys.executable, STAND_IN, "--socket", self.socket, "--state-dir", state,
                          "--events", self.events_path, "--control", self.control_path], dict(env),
                         "stand-in speakerd: ", "layer ", 10, "the stand-in speakerd")

    def set_control(self, control):
        self.control_path.write_text(json.dumps(control))

    def events(self, op=None, direction=None):
        out = []
        # Complete lines only: the stand-in may be in the middle of writing the last one.
        for line in self.events_path.read_text().split("\n")[:-1]:
            event = json.loads(line)
            if (op is None or event.get("op") == op) and (direction is None or event["dir"] == direction):
                out.append(event)
        return out

    def wait_event(self, predicate, timeout=10, what="an event"):
        deadline = time.monotonic() + timeout
        while True:
            for event in self.events():
                if predicate(event):
                    return event
            if time.monotonic() > deadline:
                raise AssertionError(f"the stand-in speakerd saw no {what} within {timeout} s; it saw:\n"
                                     + "\n".join(json.dumps(e)[:300] for e in self.events()))
            time.sleep(0.02)


class Controller:
    """A harness client acting as a controller (docs/protocol.md): the control link to the hub,
    with hello and state, and an OSC socket for the knobs and the hub's echoes. Every message it
    gets is kept with the time it arrived (time.monotonic()), `via` "control" or "osc"; `params`
    is what it knows of each parameter, newest seq wins, from the state and the echoes."""

    def __init__(self, name, hub=ADDRESS["left"], port=CONTROL_PORT, knob_port=KNOB_PORT, osc=True, timeout=10,
                 secret=TEST_SECRET, expect_state=True):
        self.name, self.hub, self.knob_port = name, hub, knob_port
        _ampd_module()      # its OSC encoder, loaded now: not in the time of a first send
        self.messages, self._changed = [], threading.Condition()
        self.params, self.gestures, self.closed = {}, {}, False
        self.udp = None
        if osc:
            self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp.bind(("127.0.0.1", 0))
            self.udp.settimeout(0.2)
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.tcp = socket.create_connection((hub, port), timeout=2)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise AssertionError(f"controller {name}: cannot connect to the hub at {hub}:{port}")
                time.sleep(0.1)
        self.tcp.settimeout(None)
        self._file = self.tcp.makefile("rb")
        self._threads = [threading.Thread(target=self._read_tcp, daemon=True)]
        if osc:
            self._threads.append(threading.Thread(target=self._read_udp, daemon=True))
        for thread in self._threads:
            thread.start()
        # The hub says hello first, with a nonce; the controller proves the pair's secret with it.
        self.hub_hello = self.wait(lambda m: m["op"] == "hello", timeout, "the hub's hello")
        hello = {"op": "hello", "role": "controller", "name": name, "protocol": 1}
        if secret is not None:
            hello["auth"] = ampd_auth(str(self.hub_hello.get("nonce", "")), "controller", name, secret)
        if osc:
            hello["osc_port"] = self.udp.getsockname()[1]
        self.send(hello)
        self.state = self.wait(lambda m: m["op"] == "state", timeout, "the state") if expect_state else None

    def _keep(self, obj, via):
        obj = dict(obj, via=via, arrived=time.monotonic())
        with self._changed:
            self.messages.append(obj)
            op = obj.get("op")
            if op == "state":
                # A full refresh (docs/protocol.md, clarification 2): what it knew goes.
                self.params = {}
                for node in obj["document"].get("nodes", []):
                    for pid, value in (node.get("params") or {}).items():
                        self.params[(node["uid"], pid)] = (obj.get("seq", 0), value)
                self.gestures = {(g["node"], g["id"]): g["origin"] for g in obj.get("gestures", [])}
            elif op == "param":
                key = (obj["node"], obj["id"])
                seq = obj.get("seq", 0)
                known = self.params.get(key, (None, None))[0]
                # Newer wins; for one seq, the control link's copy (exact) over OSC's (a float32).
                if known is None or 0 < ((seq - known) & 0xFFFFFFFF) < 0x80000000 or (seq == known and via == "control"):
                    self.params[key] = (seq, obj["value"])
            elif op == "gesture":
                key = (obj["node"], obj["id"])
                if obj["state"] == "begin":
                    self.gestures[key] = obj["origin"]
                else:
                    self.gestures.pop(key, None)
            self._changed.notify_all()

    def _read_tcp(self):
        try:
            for line in self._file:
                self._keep(json.loads(line), "control")
        except (OSError, ValueError):
            pass
        with self._changed:
            self.closed = True
            self._changed.notify_all()

    def _read_udp(self):
        while not self.closed:
            try:
                data, _ = self.udp.recvfrom(65536)
            except OSError:
                continue
            path, args = osc_decode(data)
            if path == "/bpi/param":
                self._keep({"op": "param", "node": args[0], "id": args[1], "value": args[2], "seq": args[3],
                            "origin": args[4]}, "osc")
            elif path == "/bpi/gesture":
                self._keep({"op": "gesture", "node": args[0], "id": args[1], "state": args[2], "origin": args[3]}, "osc")

    def send(self, obj):
        self.tcp.sendall(json.dumps(obj).encode() + b"\n")

    def param(self, node, pid, value):
        """A typed value, or the last of a gesture: over the control link."""
        self.send({"op": "param", "node": node, "id": pid, "value": value, "seq": 0, "origin": self.name})

    def osc_param(self, node, pid, value):
        """A moving knob: OSC to the hub's knob port."""
        sock = self.udp or socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(osc_encode("/bpi/param", "isfis", node, pid, float(value), 0, self.name), (self.hub, self.knob_port))

    def gesture(self, node, pid, state, osc=False):
        if osc:
            self.udp.sendto(osc_encode("/bpi/gesture", "isss", node, pid, state, self.name), (self.hub, self.knob_port))
        else:
            self.send({"op": "gesture", "node": node, "id": pid, "state": state, "origin": self.name})

    def mark(self):
        with self._changed:
            return len(self.messages)

    def wait(self, predicate, timeout=10, what="a message", after=0):
        deadline = time.monotonic() + timeout
        with self._changed:
            while True:
                for message in self.messages[after:]:
                    if predicate(message):
                        return message
                if time.monotonic() > deadline or self.closed:
                    raise AssertionError(f"controller {self.name}: {what} did not come within {timeout} s"
                                         + (" (the hub closed the connection)" if self.closed else "")
                                         + "; it got:\n" + "\n".join(json.dumps(m)[:300] for m in self.messages[-20:]))
                self._changed.wait(0.1)

    def propose(self, document, base=None, timeout=30):
        """A revision, base + 1 of the current one (or of `base`); returns the hub's answer,
        applied or rejected."""
        base = self.current_revision() if base is None else base
        after = self.mark()
        self.send({"op": "graph", "revision": base + 1, "base": base, "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "origin": self.name, "document": document})
        answer = self.wait(lambda m: m["op"] in ("applied", "rejected") and m.get("revision") == base + 1, timeout,
                           f"the answer to revision {base + 1}", after)
        if answer["op"] == "applied":
            self.wait(lambda m: m["op"] == "graph" and m.get("revision") == base + 1, timeout,
                      f"the graph of revision {base + 1}", after)
        return answer

    def current_revision(self):
        """The revision of the last state or graph it got."""
        with self._changed:
            revision = self.state["revision"]
            for message in self.messages:
                if message["op"] in ("graph", "state"):
                    revision = message["revision"]
            return revision

    def last_state(self):
        with self._changed:
            return [m for m in self.messages if m["op"] == "state"][-1]

    def values(self):
        """What it knows of each parameter: {(node, id): value}."""
        with self._changed:
            return {key: value for key, (_, value) in self.params.items()}

    def close(self):
        """Ends both links; the reading threads end with them."""
        self.closed = True
        try:
            self.tcp.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        for thread in self._threads:
            thread.join(timeout=5)
        self._file.close()
        for sock in (self.tcp, self.udp):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class MonitorClient:
    """A monitor subscriber, as a plugin window (docs/protocol.md, "Monitoring"): the monitor link
    to one speaker's ampd. With `reconnect`, it connects again every 0.1 s when the link drops and
    subscribes again, as an open window does. `values` is every value with its arrival time."""

    def __init__(self, address, port=MONITOR_PORT, reconnect=False, timeout=10, secret=TEST_SECRET, name="vpair-monitor"):
        self.address, self.port, self.reconnect = address, port, reconnect
        self.secret, self.name, self._file = secret, name, None
        self.subs, self.values, self.errors = {}, [], []
        self.connects, self.closed, self.sock = 0, False, None
        self._changed = threading.Condition()
        self._lock = threading.Lock()
        self._connect(timeout)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _connect(self, timeout):
        deadline = time.monotonic() + timeout
        while not self.closed:
            try:
                sock = socket.create_connection((self.address, self.port), timeout=1)
                sock.settimeout(5)
                file = sock.makefile("rb")
                hello = json.loads(file.readline())      # the speaker's, with its nonce
                sock.settimeout(None)
                mine = {"op": "hello", "role": "controller", "name": self.name, "protocol": 1}
                if self.secret is not None:
                    mine["auth"] = ampd_auth(str(hello.get("nonce", "")), "controller", self.name, self.secret)
                sock.sendall(json.dumps(mine).encode() + b"\n")
                with self._lock:
                    self.sock, self._file = sock, file
                    self.connects += 1
                    for stream, rate in self.subs.items():
                        sock.sendall(json.dumps({"op": "subscribe", "stream": stream, "rate_hz": rate}).encode() + b"\n")
                return True
            except (OSError, ValueError):
                if time.monotonic() > deadline:
                    raise AssertionError(f"cannot connect to the monitor link at {self.address}:{self.port}")
                time.sleep(0.1)
        return False

    def _run(self):
        while not self.closed:
            try:
                for line in self._file:
                    obj = json.loads(line)
                    with self._changed:
                        if obj.get("op") == "value":
                            self.values.append((time.monotonic(), obj))
                        elif obj.get("op") == "error":
                            self.errors.append(obj)
                        self._changed.notify_all()
            except (OSError, ValueError):
                pass
            if self.closed or not self.reconnect:
                break
            time.sleep(0.1)
            try:
                self._connect(timeout=3600)
            except AssertionError:
                break

    def subscribe(self, stream, rate_hz=30):
        with self._lock:
            self.subs[stream] = rate_hz
            self.sock.sendall(json.dumps({"op": "subscribe", "stream": stream, "rate_hz": rate_hz}).encode() + b"\n")

    def unsubscribe(self, stream):
        with self._lock:
            self.subs.pop(stream, None)
            self.sock.sendall(json.dumps({"op": "unsubscribe", "stream": stream}).encode() + b"\n")

    def of(self, stream, since=None):
        """[(arrival, value line)] of a stream, those that arrived after `since`."""
        with self._changed:
            return [(t, v) for t, v in self.values if v["stream"] == stream and (since is None or t > since)]

    def wait_value(self, stream, since=None, timeout=10):
        deadline = time.monotonic() + timeout
        with self._changed:
            while True:
                found = [(t, v) for t, v in self.values if v["stream"] == stream and (since is None or t > since)]
                if found:
                    return found[0]
                if time.monotonic() > deadline:
                    raise AssertionError(f"no value of {stream} from {self.address} within {timeout} s"
                                         + (f"; errors: {self.errors}" if self.errors else ""))
                self._changed.wait(0.05)

    def close(self):
        self.closed = True
        if self.sock is not None:
            try:
                self.sock.shutdown(2)
                self.sock.close()
            except OSError:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class TimedRecorder:
    """pw-record of one node's monitor, as raw 32-bit floats on a pipe, each chunk stamped with
    the time it arrived (time.monotonic()). stop() gives t0: sample i was delivered at t0 + i /
    RATE, from the 5th percentile of (arrival - samples so far / RATE) over the chunks, a lower
    envelope that a late chunk or an early burst does not move. `latency` (samples) is what
    pw-record asks for: its chunks are that long (by default it asks 100 ms and gets 1024).
    Linked before start() returns, like Recorder."""

    def __init__(self, speaker, target="amplifier", timeout=5.0, latency=None):
        self.speaker, self.target, self.timeout, self.latency = speaker, target, timeout, latency
        self.name = f"vpair-trecord-{os.getpid()}-{next(_names)}"
        self.chunks, self.stamps, self.process = [], [], None

    def start(self):
        self.process = subprocess.Popen(
            ["pw-record", "--target", self.target, "-P", "{ stream.capture.sink = true node.name = " + self.name + " }",
             *(["--latency", str(self.latency)] if self.latency else []),
             "--rate", str(RATE), "--channels", "2", "--format", "f32", "-"],
            env=self.speaker.env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            bufsize=0)
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()
        try:
            self.speaker.wait_for_links({(f"{self.target}:monitor_{c}", f"{self.name}:input_{c}") for c in ("FL", "FR")},
                                        self.timeout, f"the timed recorder of {self.target}", self.process)
        except BaseException:
            self.close()
            raise
        return self

    def _read(self):
        total = 0
        fd = self.process.stdout.fileno()
        while True:
            data = os.read(fd, 1 << 16)
            if not data:
                break
            now = time.monotonic()
            self.chunks.append(data)
            total += len(data)
            self.stamps.append((now, total // 8))

    def stop(self):
        """Ends the recording; returns (samples (n, 2), t0): sample i was in the graph at
        t0 + i / RATE."""
        if self.process.poll() is None:
            self.process.terminate()
        self.process.wait(timeout=5)
        self._thread.join(timeout=5)
        raw = b"".join(self.chunks)
        x = np.frombuffer(raw[:len(raw) // 8 * 8], dtype="<f4").reshape(-1, 2).astype(np.float64)
        offsets = sorted(t - n / RATE for t, n in self.stamps)
        t0 = offsets[len(offsets) // 20] if offsets else None
        return x, t0

    def close(self):
        if self.process is not None and self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=5)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()
        return False


def speaker_conf_with(path, **properties):
    """A copy of speaker.conf with some of its context.properties changed (for example
    default_clock_quantum=128 writes default.clock.quantum = 128), written to `path`."""
    text = (HERE / "speaker.conf").read_text()
    for key, value in properties.items():
        name = key.replace("_", ".")
        text, n = re.subn(rf"(?m)^(\s*){re.escape(name)}(\s*)=.*$", rf"\g<1>{name}\g<2>= {value}", text)
        if n != 1:
            raise ValueError(f"speaker.conf has no single {name}")
    Path(path).write_text(text)
    return Path(path)


def revision_document(document, revision, origin="vpair", rev_time="2026-01-01T00:00:00Z"):
    """`document` as ampd keeps it in graph.json: its revision, time and origin."""
    return dict(json.loads(json.dumps(document)), revision=revision, time=rev_time, origin=origin)


class StandInPair:
    """The hub (left) and an agent (right), each ampd on a stand-in speakerd: the protocol with no
    PipeWire and no audio. `documents` gives each speaker's graph.json ({"left": doc, ...}, as
    revision_document() makes them). Entering waits until the agent has said hello to the hub
    and the hub has caught it up."""

    def __init__(self, tmp, documents, ampd_args=(), control=None, limits=None, secret=TEST_SECRET):
        self.tmp, self.documents, self.ampd_args = Path(tmp), documents, ampd_args
        self.limits, self.secret = limits or {}, secret
        self.control = control or {}
        self.env, self.state, self.sd, self.ampd = {}, {}, {}, {}
        self._stack = None

    def __enter__(self):
        import contextlib
        self._stack = contextlib.ExitStack().__enter__()
        try:
            for name in ("left", "right"):
                run = self.tmp / name / "run"
                run.mkdir(parents=True, exist_ok=True)
                self.env[name] = dict(os.environ, XDG_RUNTIME_DIR=str(run))
                self.state[name] = state_dir(self.tmp / name / "state", role=name, current=self.documents.get(name),
                                             limits=self.limits.get(name, TWEETER_MUTED))
                self.sd[name] = self._stack.enter_context(
                    StandInSpeakerd(self.env[name], self.state[name], self.control.get(name)))
            for name in ("left", "right"):
                self.start_ampd(name)
            self.ampd["right"].wait_for("hub: connected")
            self.ampd["left"].wait_for(("agent right: plays revision", "agent right: revision"))
        except BaseException:
            self._stack.__exit__(*sys.exc_info())
            raise
        return self

    def start_ampd(self, name, args=()):
        self.ampd[name] = Ampd(self.env[name], self.state[name], name, args=[*self.ampd_args, *args], secret=self.secret)
        self._stack.enter_context(self.ampd[name])
        return self.ampd[name]

    def restart_speakerd(self, name):
        self.sd[name].stop()
        self.sd[name] = self._stack.enter_context(StandInSpeakerd(self.env[name], self.state[name],
                                                                  json.loads(self.sd[name].control_path.read_text())))
        return self.sd[name]

    def __exit__(self, *exc):
        return self._stack.__exit__(*exc)


SPEAKERD_SOCKET_VERSION = "0.3.0"


def speakerd_version():
    """speakerd's version, from `speakerd --version` ("0.2.1")."""
    out = subprocess.run([str(built("speakerd")), "--version"], capture_output=True, text=True, timeout=30,
                         env=dict(os.environ, HOME=tempfile.gettempdir())).stdout
    found = re.search(r"(\d+\.\d+\.\d+)", out)
    return found.group(1) if found else "0"


def require_speakerd_local_link(test):
    """Fails `test` (a unittest.TestCase) with the reason when the built speakerd has no local
    socket: step 3's speakerd (0.3.0, host-dev's) is what these scenarios drive."""
    version = speakerd_version()
    if tuple(map(int, version.split("."))) < tuple(map(int, SPEAKERD_SOCKET_VERSION.split("."))):
        test.fail(f"speakerd {version} has no local socket to ampd: step 3's scenarios need speakerd "
                  f"{SPEAKERD_SOCKET_VERSION} or later (docs/protocol.md). Until it is merged, this scenario fails.")


class SpeakerPair:
    """The virtual pair as step 3 runs it: on each speaker its PipeWire, speakerd (from
    `documents[name]` as graph.json, with `bundles`) and ampd; the left is the hub on 127.0.1.1,
    the right an agent on 127.0.1.2. Entering waits until each speakerd plays and the hub has
    caught the agent up. `setup` is Pair's (another speaker.conf)."""

    def __init__(self, tmp, documents, bundles, setup=None, ampd_args=(), roles=None, limits=None):
        self.tmp, self.documents, self.bundles = Path(tmp), documents, bundles
        self.limits = limits or {}
        self.setup, self.ampd_args = setup, ampd_args
        self.roles = roles or {"left": "left", "right": "right"}
        self.state, self.speakerd, self.ampd = {}, {}, {}
        self._stack = None

    def __enter__(self):
        import contextlib
        self._stack = contextlib.ExitStack().__enter__()
        try:
            self.pair = self._stack.enter_context(Pair(self.setup))
            for name in ("left", "right"):
                self.state[name] = state_dir(self.tmp / name, role=self.roles[name], current=self.documents[name],
                                             limits=self.limits.get(name, TWEETER_MUTED))
                self.start_speakerd(name)
            for name in ("left", "right"):
                self.start_ampd(name)
            self.ampd["right"].wait_for("hub: connected")
            self.ampd["left"].wait_for(("agent right: plays revision", "agent right: revision"))
        except BaseException:
            self._stack.__exit__(*sys.exc_info())
            raise
        return self

    def __getitem__(self, name):
        return self.pair[name]

    def start_speakerd(self, name, wait_for="playing: "):
        self.speakerd[name] = Speakerd(self.pair[name], self.state[name], bundles=self.bundles, wait_for=wait_for)
        self._stack.enter_context(self.speakerd[name])
        return self.speakerd[name]

    def start_ampd(self, name, args=()):
        self.ampd[name] = Ampd(self.pair[name].env, self.state[name], name, args=[*self.ampd_args, *args])
        self._stack.enter_context(self.ampd[name])
        return self.ampd[name]

    def graph(self, name):
        """The speaker's graph.json, as ampd wrote it."""
        return json.loads((self.state[name] / "graph.json").read_text())

    def __exit__(self, *exc):
        return self._stack.__exit__(*exc)
