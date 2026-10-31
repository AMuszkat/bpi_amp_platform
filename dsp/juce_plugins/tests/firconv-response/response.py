"""Signals, renders and spectra of the response test (test_firconv_response.py).

The estimator. The input is two excitations, an exponential sine sweep and a segment of white
noise, each followed by silence long enough for the plugin's response to die away, and each
transformed whole, with its silence, by one FFT long enough to hold it: so every segment starts
and ends in silence and its output is the plugin's linear response to it, with no window and no
leakage. Per frequency bin, summed over the two segments:

    Sxx = sum |X|^2,   Syy = sum |Y|^2,   Sxy = sum conj(X) Y,   H = Sxy / Sxx

which is the averaged cross-spectrum over the input spectrum (H1), averaged over the two
excitations, and for these segments the deconvolution Y / X of each. The difference of two
builds, d = y_a - y_b, goes through the same transforms: Sdd = sum |Y_a - Y_b|^2. The curves are
smoothed over fractional-octave bands as power sums: a build's gain in a band is
sqrt(sum Syy / sum Sxx) (its |H|^2 weighted by the input's power), a signal's band power is its
energy in the band per sample of excitation, and the difference relative to the output is
sum Sdd / sum Syy, the error-to-signal ratio in the band. The design response is smoothed with
the same weights (sum |H_design|^2 Sxx / sum Sxx), so every curve of a figure is smoothed alike.
"""

import json
import os
import re
import shutil
import struct
import subprocess
import wave
from pathlib import Path

import numpy as np

RATE = 48000
BLOCK = 1024
N_FFT = 1 << 19                      # 10.9 s at 48 kHz: holds the longer segment, 7.25 s
SMOOTHING_OCTAVES = 1.0 / 6.0        # the width of a band
GRID_PER_OCTAVE = 48                 # band centres per octave on the plots and in the CSVs
F_MIN, F_MAX = 10.0, 24000.0         # the plots' frequency axis


# ---------------------------------------------------------------------------------------------
# The input

class Excitation:
    """The input: silence, an exponential sweep from `f1` to `f2`, silence, white noise (uniform,
    fixed seed), silence; both at `peak_dbfs`. `segments` are the (start, end) sample ranges the
    spectra are taken over, each excitation with the silence after it."""

    def __init__(self, peak_dbfs=-9.0, f1=10.0, f2=22000.0, sweep_s=6.0, noise_s=4.0, lead_s=0.25,
                 tail_s=1.0, seed=20261031):
        amplitude = 10.0 ** (peak_dbfs / 20.0)
        n_sweep, n_noise = int(sweep_s * RATE), int(noise_s * RATE)
        n_lead, n_tail = int(lead_s * RATE), int(tail_s * RATE)
        t = np.arange(n_sweep) / RATE
        rate = sweep_s / np.log(f2 / f1)
        sweep = np.sin(2.0 * np.pi * f1 * rate * (np.exp(t / rate) - 1.0))
        sweep *= _fades(n_sweep, int(RATE / f1), int(0.005 * RATE))   # one cycle in, 5 ms out
        noise = np.random.default_rng(seed).uniform(-1.0, 1.0, n_noise)
        noise *= _fades(n_noise, int(0.01 * RATE), int(0.01 * RATE))
        self.x = amplitude * np.concatenate([np.zeros(n_lead), sweep, np.zeros(n_tail), noise, np.zeros(n_tail)])
        first = n_lead + n_sweep + n_tail
        self.segments = [(0, first), (first, len(self.x))]
        self.excitation_samples = n_sweep + n_noise
        self.peak_dbfs, self.f1, self.f2, self.sweep_s, self.noise_s = peak_dbfs, f1, f2, sweep_s, noise_s
        if max(end - start for start, end in self.segments) > N_FFT:
            raise ValueError("a segment is longer than the FFT")

    def describe(self):
        return (f"exponential sweep {self.f1:g} Hz-{self.f2 / 1000:g} kHz, {self.sweep_s:g} s, and white noise, "
                f"{self.noise_s:g} s, both at {self.peak_dbfs:g} dBFS peak")


def _fades(n, n_in, n_out):
    """Half-Hann fades over the first `n_in` and the last `n_out` of `n` samples."""
    w = np.ones(n)
    w[:n_in] = 0.5 - 0.5 * np.cos(np.pi * np.arange(n_in) / n_in)
    w[n - n_out:] = 0.5 + 0.5 * np.cos(np.pi * np.arange(1, n_out + 1) / n_out)
    return w


# ---------------------------------------------------------------------------------------------
# WAV files: speakerd reads and writes 32-bit integer PCM

FULL_SCALE = 2.0 ** 31


def write_wav(path, x):
    """Mono `x`, full scale 1.0, as 32-bit integer PCM."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(4)
        w.setframerate(RATE)
        w.writeframes(np.clip(np.round(x * (FULL_SCALE - 1)), -FULL_SCALE, FULL_SCALE - 1).astype("<i4").tobytes())


def read_wav_int32(path):
    """speakerd's output: (n, channels) of int32, exactly as written."""
    data = Path(path).read_bytes()
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError(f"{path} is not a WAV file")
    fmt, frames, at = None, None, 12
    while at + 8 <= len(data):
        chunk, size = data[at:at + 4], struct.unpack("<I", data[at + 4:at + 8])[0]
        if chunk == b"fmt ":
            fmt = struct.unpack("<HHIIHH", data[at + 8:at + 24])
            if fmt[0] == 0xFFFE:     # WAVE_FORMAT_EXTENSIBLE: the format is in the sub-format GUID
                fmt = (struct.unpack("<H", data[at + 32:at + 34])[0],) + fmt[1:]
        elif chunk == b"data":
            frames = data[at + 8:at + 8 + size]
            break
        at += 8 + size + (size & 1)
    if fmt is None or frames is None:
        raise ValueError(f"{path} has no fmt or data chunk")
    tag, channels, rate, _, _, bits = fmt
    if (tag, bits) != (1, 32) or rate != RATE:
        raise ValueError(f"{path}: format {tag}, {bits} bits, {rate} Hz; expected 32-bit integer PCM at {RATE} Hz")
    return np.frombuffer(frames, dtype="<i4").reshape(-1, channels)


# ---------------------------------------------------------------------------------------------
# The render

def graph_document(uri, params, outputs):
    """One plugin node (uid 16) between the stream's FL and the amplifier: its output channel c
    to the amplifier's input `outputs[c]` (0 the woofer, 1 the tweeter)."""
    connections = [{"src": {"node": 1, "channel": 0}, "dst": {"node": 16, "channel": 0}}]
    connections += [{"src": {"node": 16, "channel": c}, "dst": {"node": 2, "channel": out}}
                    for c, out in enumerate(outputs)]
    return {"format": "speakerd-graph", "version": 1, "revision": 1,
            "nodes": [{"uid": 16, "plugin": {"format": "LV2", "identifier": uri}, "params": params}],
            "connections": connections}


def start_render(speakerd, arch, bundle, document, wav_in, workdir):
    """Starts `speakerd --render` on `wav_in` with the one `bundle`, from a state directory in
    `workdir` holding `document` as its factory layer, and an empty HOME (so lilv loads exactly
    that bundle); the aarch64 build under qemu-aarch64-static. finish_render() waits for it."""
    workdir = Path(workdir)
    shutil.rmtree(workdir, ignore_errors=True)
    state, home = workdir / "state", workdir / "home"
    state.mkdir(parents=True)
    home.mkdir()
    tweeter = "on" if 1 in [c["dst"]["channel"] for c in document["connections"] if c["dst"]["node"] == 2] else "muted"
    # No limit: the plugin's output as it is (an offline render, nothing is played).
    (state / "device.json").write_text(json.dumps(
        {"version": 1, "role": "left", "amplifier": "amplifier", "limits": {"gain_db": 0, "tweeter": tweeter}}) + "\n")
    (state / "factory.json").write_text(json.dumps(document, indent=1) + "\n")
    out, log = workdir / "out.wav", workdir / "speakerd.log"
    command = [str(speakerd), "--state-dir", str(state), "--render", str(wav_in), str(out),
               "--block", str(BLOCK), "--lv2-bundle", str(bundle)]
    if arch == "aarch64":
        command.insert(0, shutil.which("qemu-aarch64-static") or "qemu-aarch64-static")
    log_file = open(log, "w")
    log_file.write("$ " + " ".join(command) + "\n")
    log_file.flush()
    process = subprocess.Popen(command, env=dict(os.environ, HOME=str(home)), stdin=subprocess.DEVNULL,
                               stdout=log_file, stderr=subprocess.STDOUT)
    return {"process": process, "log_file": log_file, "out": out, "log": log, "bundle": Path(bundle)}


def finish_render(started, timeout=900):
    """Waits for a render; returns its output as int32 (n, 2). Fails unless it exited 0 and its
    log says the plugin node ran from the bundle named."""
    process, log, bundle = started["process"], started["log"], started["bundle"]
    try:
        status = process.wait(timeout=timeout)
    finally:
        started["log_file"].close()
    text = log.read_text(errors="replace")
    if status != 0:
        raise AssertionError(f"the render failed with status {status}; log {log}:\n{text}")
    ran = re.findall(r"plugin node 16: .*, bundle (.*)$", text, re.MULTILINE)
    if [os.path.realpath(p) for p in ran] != [os.path.realpath(bundle)]:
        raise AssertionError(f"the render did not run {bundle} (it ran {ran}); log {log}")
    return read_wav_int32(started["out"])


# ---------------------------------------------------------------------------------------------
# Spectra and bands

def spectra(signal, segments):
    """The rfft of each segment of `signal`, zero-padded to N_FFT."""
    return [np.fft.rfft(signal[start:end], N_FFT) for start, end in segments]


def power(transforms):
    return sum(np.abs(t) ** 2 for t in transforms)


def bin_frequencies():
    return np.arange(N_FFT // 2 + 1) * (RATE / N_FFT)


def grid(f_min=F_MIN, f_max=F_MAX, per_octave=GRID_PER_OCTAVE):
    """Band centres, `per_octave` to an octave, from `f_min` to at most `f_max`."""
    count = int(np.floor(per_octave * np.log2(f_max / f_min) + 1e-9)) + 1
    return f_min * 2.0 ** (np.arange(count) / per_octave)


class Bands:
    """Sums of a per-bin array over the bands of `width` octaves centred on `centres` (the bins
    whose frequency lies in [centre * 2^-width/2, centre * 2^width/2), at most Nyquist)."""

    def __init__(self, centres, width=SMOOTHING_OCTAVES):
        df = RATE / N_FFT
        nbins = N_FFT // 2 + 1
        lo = np.ceil(centres * 2.0 ** (-width / 2) / df).astype(int)
        hi = np.minimum(np.ceil(centres * 2.0 ** (width / 2) / df).astype(int), nbins)
        if np.any(hi <= lo):
            raise ValueError("a band holds no bin")
        self.centres, self.count = centres, hi - lo
        self._indices = np.column_stack([lo, hi]).ravel()

    def sum(self, per_bin):
        # reduceat over (lo, hi) pairs: the even entries are the band sums, summed directly (a
        # difference of cumulative sums would lose the small bands next to the large ones).
        return np.add.reduceat(np.append(per_bin, 0.0), self._indices)[::2]


def db_power(x):
    """10 log10 of a power ratio; -inf for 0."""
    with np.errstate(divide="ignore"):
        return 10.0 * np.log10(x)


def band_power_dbfs(band_sums, excitation_samples):
    """A signal's power in each band from its band sums of |Y|^2, dB re full scale (1.0^2) per
    sample of excitation: its energy there, (2 / N_FFT) sum |Y|^2 by Parseval, over the samples
    the excitations last."""
    return db_power(band_sums * (2.0 / N_FFT) / excitation_samples)


def peak_db(x):
    with np.errstate(divide="ignore"):
        return 20.0 * np.log10(np.max(np.abs(x)))


# ---------------------------------------------------------------------------------------------
# The design: the plugin's coefficients as the tuning GUI graphs them, with scipy

SCIPY_MISSING = ("scipy is missing: the design response is computed with scipy.signal (freqz, "
                 "sosfreqz). Install python3-scipy (Debian bookworm's package; it is in "
                 "dsp/juce_plugins/tools/Containerfile)")


def scipy_signal():
    """scipy.signal, or an AssertionError that names the package."""
    try:
        from scipy import signal
    except ImportError as error:
        raise AssertionError(SCIPY_MISSING) from error
    return signal


def header_arrays(path):
    """The float arrays of a generated coefficient header (filters.hpp), as float64 from their
    decimal text: {name: array}."""
    text = Path(path).read_text()
    arrays = {}
    for name, size, body in re.findall(r"float\s+(\w+)\s*\[\s*(\d+)\s*\]\s*=\s*\{([^}]*)\}", text):
        values = np.array([float(v.strip().rstrip("fF")) for v in body.split(",") if v.strip()])
        if len(values) != int(size):
            raise ValueError(f"{path}: {name}[{size}] has {len(values)} values")
        arrays[name] = values
    return arrays


def firconv_design(src_dir, freqs, gain_db):
    """firConv's chain from its filters.hpp, as dsp/python_dsp_tools/tuning/dsp_gui.py graphs
    it (freqz of InvFilter and HPreLpTaps, the product), in float64 at RATE, times the render's
    gain, at `freqs` (Hz). The high-pass is the HPreHpSos cascade the plugin runs (sosfreqz).
    Returns {"chain": the design; "firs": the chain without the high-pass; "hp_sos": the
    high-pass; "hp_ba": the GUI's direct form of it, freqz(HPreHpNum, HPreHpDen), from the same
    header; "chain_f32": the chain with every coefficient rounded to float32 first, as the plugin
    stores them}."""
    signal = scipy_signal()
    c = header_arrays(Path(src_dir) / "filters.hpp")
    freqs = np.asarray(freqs, dtype=float)
    on_bins = len(freqs) == N_FFT // 2 + 1 and np.allclose(freqs, bin_frequencies())

    def fir(taps):
        if on_bins:      # the FFT's bins: freqz's own FFT path, whose frequencies they are
            return signal.freqz(taps, [1.0], worN=len(freqs), include_nyquist=True, fs=RATE)[1]
        return signal.freqz(taps, [1.0], worN=freqs, fs=RATE)[1]

    def chain(cast):
        gain = cast(np.array([10.0 ** (gain_db / 20.0)]))[0]
        firs = gain * fir(cast(c["InvFilter"])) * fir(cast(c["HPreLpTaps"]))
        return firs, signal.sosfreqz(cast(c["HPreHpSos"]).reshape(-1, 6), worN=freqs, fs=RATE)[1]

    firs, hp_sos = chain(lambda a: a)
    firs32, hp_sos32 = chain(lambda a: a.astype(np.float32).astype(np.float64))
    hp_ba = signal.freqz(c["HPreHpNum"], c["HPreHpDen"], worN=freqs, fs=RATE)[1]
    return {"chain": firs * hp_sos, "firs": firs, "hp_sos": hp_sos, "hp_ba": hp_ba,
            "chain_f32": firs32 * hp_sos32}


def crossing(freqs, values_db, level_db):
    """The lowest frequency where `values_db` rises through `level_db`, interpolated on a log
    frequency axis; None when it never does."""
    above = np.nonzero((values_db[:-1] < level_db) & (values_db[1:] >= level_db))[0]
    if len(above) == 0:
        return None
    i = above[0]
    t = (level_db - values_db[i]) / (values_db[i + 1] - values_db[i])
    return float(np.exp(np.log(freqs[i]) + t * (np.log(freqs[i + 1]) - np.log(freqs[i]))))
