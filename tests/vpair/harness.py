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
"""

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
    with wave.open(str(path), "rb") as w:
        width, channels, frames = w.getsampwidth(), w.getnchannels(), w.getnframes()
        raw = w.readframes(frames)
    dtype = {2: "<i2", 4: "<i4"}[width]
    x = np.frombuffer(raw, dtype=dtype).reshape(-1, channels).astype(np.float64)
    return x / float(2 ** (8 * width - 1))


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

    def run(self, *args, timeout=10):
        return subprocess.run(list(args), env=self.env, check=True, capture_output=True,
                              text=True, timeout=timeout).stdout

    def _recorder(self, target, path):
        return subprocess.Popen(
            ["pw-record", "--target", target, "-P", "{ stream.capture.sink = true }",
             "--rate", str(RATE), "--channels", "2", "--format", "s32", str(path)],
            env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def play_and_record(self, samples, target="amplifier", margin=0.4, record_target=None):
        """Plays `samples` (mono, or (n, 2) for FL and FR) into `target` and returns what the
        monitor of `record_target` (default: `target` itself) received, as an (n, 2) array:
        column 0 is the woofer channel, column 1 the tweeter channel. The player has finished
        when this returns."""
        with tempfile.TemporaryDirectory() as tmp:
            src, rec = Path(tmp) / "play.wav", Path(tmp) / "rec.wav"
            _write_wav(src, samples)

            recorder = self._recorder(record_target or target, rec)
            try:
                time.sleep(margin)
                subprocess.run(["pw-play", "--target", target, str(src)], env=self.env,
                               check=True, capture_output=True,
                               timeout=len(samples) / RATE + 10)
                time.sleep(margin)
            finally:
                recorder.terminate()
                recorder.wait(timeout=5)

            return _read_wav(rec)

    def record(self, seconds, target="amplifier"):
        """Records `target`'s monitor for `seconds`, playing nothing."""
        with tempfile.TemporaryDirectory() as tmp:
            rec = Path(tmp) / "rec.wav"
            recorder = self._recorder(target, rec)
            try:
                time.sleep(seconds)
            finally:
                recorder.terminate()
                recorder.wait(timeout=5)

            return _read_wav(rec)

    def create_sink(self, name):
        """Adds a null sink like the amplifier (woofer FL, tweeter FR) to this speaker's
        PipeWire. It stays until remove_node() or the speaker goes down."""
        self.run("pw-cli", "create-node", "adapter",
                 "{ factory.name = support.null-audio-sink node.name = " + name
                 + " media.class = Audio/Sink audio.position = [ FL FR ] node.driver = true"
                 + " monitor.channel-volumes = false object.linger = true }")

    def remove_node(self, name):
        self.run("pw-cli", "destroy", name)


class Pair:
    """Brings the virtual pair up for a `with` block and down afterwards."""

    def __enter__(self):
        self._up()
        self.speakers = {name: Speaker(name) for name in ("left", "right")}
        return self

    def __getitem__(self, name):
        return self.speakers[name]

    def _up(self, *names):
        """vpair.sh up; when a speaker does not come up, takes the pair down again and fails
        with what vpair.sh and the speakers' logs said."""
        result = subprocess.run([str(VPAIR), "up", *names], capture_output=True, text=True,
                                timeout=60)
        if result.returncode == 0:
            return
        base = Path(os.environ.get("VPAIR_DIR", "/tmp/vpair"))
        logs = []
        for log in sorted(base.glob("*/*.log")):
            tail = log.read_text(errors="replace").splitlines()[-15:]
            logs.append(f"  {log}:\n" + "\n".join("    " + line for line in tail))
        subprocess.run([str(VPAIR), "down"], check=False, capture_output=True, timeout=30)
        raise RuntimeError(f"vpair.sh up {' '.join(names)} failed (exit {result.returncode}):\n"
                           f"{result.stdout}{result.stderr}" + "\n".join(logs))

    def down(self, name):
        """Takes one speaker down: its PipeWire, WirePlumber and D-Bus session."""
        subprocess.run([str(VPAIR), "down", name], check=True, capture_output=True, timeout=30)

    def up(self, name):
        """Brings one speaker (back) up, and returns it."""
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
    heard). Leaving stops it with SIGTERM and checks that it said "stopping" and exited 0;
    after a failure inside the block it is stopped all the same, killed if need be.

        with Speakerd(speaker, state, bundles=[bundle]) as sd:
            sd.wait_for("linked to amplifier ")
    """

    def __init__(self, speaker, state, bundles=(), args=(), wait_for="playing: ", timeout=30,
                 prefix=()):
        self.speaker = speaker
        self.command = [*prefix, str(built("speakerd")), "--state-dir", str(state)]
        for bundle in bundles:
            self.command += ["--lv2-bundle", str(bundle)]
        self.command += list(args)
        self.first_wait, self.first_timeout = wait_for, timeout
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

    def wait_for(self, text, timeout=30, after=0):
        """Waits for a log line starting with "speakerd: " + `text`, from line `after` on, and
        returns it. Fails, with speakerd's output, on a timeout or when speakerd exits."""
        deadline = time.monotonic() + timeout
        with self._changed:
            while True:
                for line in self.lines[after:]:
                    if line.startswith("speakerd: " + text):
                        return line
                remaining = deadline - time.monotonic()
                if self.process.poll() is not None and not self._reader.is_alive():
                    raise AssertionError(self._report(
                        f"speakerd exited with status {self.process.returncode} before it "
                        f"said '{text}'"))
                if remaining <= 0:
                    raise AssertionError(self._report(
                        f"speakerd did not say '{text}' within {timeout} s"))
                self._changed.wait(min(remaining, 0.5))

    def _report(self, what):
        return (f"{what}\n  command: {' '.join(self.command)}\n  its output:\n"
                + "\n".join("    " + line for line in self.lines))

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
                if self.process.returncode != 0:
                    raise AssertionError(self._report(
                        f"speakerd exited with status {self.process.returncode}, not 0"))
                if not self.lines_starting("stopping"):
                    raise AssertionError(self._report("speakerd did not say 'stopping'"))
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
