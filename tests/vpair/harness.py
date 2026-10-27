"""Test harness for the virtual pair (see README.md).

It plays into and records from the virtual speakers that vpair.sh starts, with the same pw-*
tools a person would use, and analyses what the "amplifier" of each speaker received.

    from harness import Pair, tone
    with Pair() as pair:
        received = pair["left"].play_and_record(tone(440.0, -20.0, 1.0))
"""

import os
import subprocess
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
VPAIR = HERE / "vpair.sh"
RATE = 48000


def tone(freq_hz, level_dbfs, seconds, rate=RATE):
    """A mono sine, `level_dbfs` peak."""
    t = np.arange(int(seconds * rate)) / rate
    return (10 ** (level_dbfs / 20.0)) * np.sin(2 * np.pi * freq_hz * t)


def rms_dbfs(x):
    return 20 * np.log10(max(np.sqrt(np.mean(np.square(x))), 1e-12))


def peak_frequency(x, rate=RATE):
    spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return np.fft.rfftfreq(len(x), 1.0 / rate)[int(np.argmax(spectrum))]


def active(x, threshold_dbfs=-60.0, block=480):
    """The part of a recording between the first and last block above the threshold."""
    n = len(x) // block
    levels = [rms_dbfs(x[i * block:(i + 1) * block]) for i in range(n)]
    loud = [i for i, level in enumerate(levels) if level > threshold_dbfs]
    if not loud:
        return x[:0]
    return x[loud[0] * block:(loud[-1] + 1) * block]


def _write_wav(path, samples, rate=RATE):
    data = np.clip(samples, -1.0, 1.0)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(4)
        w.setframerate(rate)
        w.writeframes((data * (2 ** 31 - 1)).astype("<i4").tobytes())


def _read_wav(path):
    with wave.open(str(path), "rb") as w:
        width, channels, frames = w.getsampwidth(), w.getnchannels(), w.getnframes()
        raw = w.readframes(frames)
    dtype = {2: "<i2", 4: "<i4"}[width]
    x = np.frombuffer(raw, dtype=dtype).reshape(-1, channels).astype(np.float64)
    return x / float(2 ** (8 * width - 1))


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

    def play_and_record(self, samples, target="amplifier", margin=0.4):
        """Plays `samples` into `target` and returns what its monitor received, as an
        (n, 2) array: column 0 is the woofer channel, column 1 the tweeter channel."""
        with tempfile.TemporaryDirectory() as tmp:
            src, rec = Path(tmp) / "play.wav", Path(tmp) / "rec.wav"
            _write_wav(src, samples)

            recorder = subprocess.Popen(
                ["pw-record", "--target", target, "-P", "{ stream.capture.sink = true }",
                 "--rate", str(RATE), "--channels", "2", "--format", "s32", str(rec)],
                env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
            recorder = subprocess.Popen(
                ["pw-record", "--target", target, "-P", "{ stream.capture.sink = true }",
                 "--rate", str(RATE), "--channels", "2", "--format", "s32", str(rec)],
                env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                time.sleep(seconds)
            finally:
                recorder.terminate()
                recorder.wait(timeout=5)

            return _read_wav(rec)


class Pair:
    """Brings the virtual pair up for a `with` block and down afterwards."""

    def __enter__(self):
        subprocess.run([str(VPAIR), "up"], check=True, capture_output=True, timeout=60)
        self.speakers = {name: Speaker(name) for name in ("left", "right")}
        return self

    def __getitem__(self, name):
        return self.speakers[name]

    def __exit__(self, *exc):
        subprocess.run([str(VPAIR), "down"], check=False, capture_output=True, timeout=30)
        return False
