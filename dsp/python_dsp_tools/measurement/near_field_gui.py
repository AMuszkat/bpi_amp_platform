"""
near_field_gui.py -- PyQt/pyqtgraph GUI for the near-field loudspeaker measurement.

The near-field method reconstructs a woofer's anechoic far-field low-frequency response
from three measurements:

  * NF woofer  (near-field, mic close to the cone -- clean lows, no room reflections)
  * FF woofer  (far-field, at distance -- windowed to gate out room reflections)
  * FF tweeter (far-field high-frequency / "HF" response)

The NF and FF woofer responses are spliced at a matching frequency (`fmatch`) to produce
the resulting far-field LF response (`compute_total_far_field_response` in nearfield/lib.py). The
tweeter gives the HF response. This GUI lets you capture (or load) each measurement, tune
the time-gating window for each one live, and view the impulse responses, magnitude and
phase together.

Usage:
    python near_field_gui.py                      # open the GUI, auto-load existing wavs
    python near_field_gui.py --measure ff         # open and immediately measure FF woofer
    python near_field_gui.py --measure all         # measure NF, FF woofer, FF tweeter in order
    python near_field_gui.py --input-device "USB"  # override input device (name substring)
    python near_field_gui.py --data-dir ~/meas     # read/write the wavs somewhere else

Default audio devices and the data folder come from config/local.env (NF_INPUT_DEVICE,
NF_OUTPUT_DEVICE, NF_DATA_DIR -- see nearfield/config.py); command-line flags override them.

Capture needs the optional `sounddevice` dependency; loading/plotting/tuning does not.
"""

import os
import sys
import glob
import time
import argparse
import platform


def _configure_alsa_for_pipewire():
    """Let conda's libasound reach the system's PipeWire ALSA plugin, so a 'pipewire'
    (and 'default') output device shows up and can route audio into PipeWire.

    conda-forge ships no PipeWire ALSA plugin, so conda's libasound can't open the ALSA
    'default'/'pipewire' PCM (which on a PipeWire system routes into PipeWire) and PortAudio
    drops it -- leaving only raw hw:* cards. Pointing ALSA_PLUGIN_DIR/ALSA_CONFIG_PATH at the
    system install fixes it. Must run before sounddevice/libasound loads (hence, at import).
    Opt out with NEAR_FIELD_NO_ALSA_FIX=1, or override ALSA_PLUGIN_DIR yourself.
    """
    if platform.system() != "Linux":
        return
    if os.environ.get("ALSA_PLUGIN_DIR") or os.environ.get("NEAR_FIELD_NO_ALSA_FIX"):
        return
    for d in ("/usr/lib/x86_64-linux-gnu/alsa-lib", "/usr/lib64/alsa-lib",
              "/usr/lib/aarch64-linux-gnu/alsa-lib", "/usr/lib/alsa-lib"):
        if glob.glob(os.path.join(d, "libasound_module_pcm_pipewire.so")):
            os.environ["ALSA_PLUGIN_DIR"] = d
            if "ALSA_CONFIG_PATH" not in os.environ and os.path.exists("/usr/share/alsa/alsa.conf"):
                os.environ["ALSA_CONFIG_PATH"] = "/usr/share/alsa/alsa.conf"
            break


_configure_alsa_for_pipewire()

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets

from nearfield import config, lib
from nearfield.lib import (
    generate_sine_sweep,
    compute_impulse_response_farina,
    apply_asymmetric_hanning_window,
    compute_magnitude_phase,
    compute_total_far_field_response,
    find_nmatch,
    save_ir_to_wav,
)

# ---------------------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------------------

# The three measurement slots. Each is measured independently (own button + mic channel);
# `channel` is the default input channel. Window defaults mirror scripts/comparison.py.
# The wav names are file names inside the data folder (config.data_dir()).
SLOTS = {
    "nr_wf": dict(label="NF woofer",  wav="ir_nr_wf.wav", left=100.0, right=250.0, color="#4C9BE8", channel=1),
    "ff_wf": dict(label="FF woofer",  wav="ir_ff_wf.wav", left=20.0,  right=6.0,   color="#E8954C", channel=2),
    "ff_tw": dict(label="FF tweeter", wav="ir_ff_tw.wav", left=20.0,  right=6.0,   color="#5FD08C", channel=1),
}

# The reconstructed far-field woofer IR (NF+FF splice, the deliverable of the method) is
# exported here alongside the raw captures. The windowed (time-gated) tweeter response is also
# exported as an IR, so downstream tools (tuning/dsp_gui.py) get a clean, reflection-free tweeter to
# match the woofer result -- the raw ir_ff_tw.wav still contains room reflections + the long
# deconvolution noise tail.
RESULT_WAV = "ir_result_wf.wav"
RESULT_TW_WAV = "ir_result_tw.wav"

# Default capture/playback devices, matched as case-insensitive substrings against the
# names sounddevice reports. Machine-specific, so they live in config/local.env
# (NF_INPUT_DEVICE / NF_OUTPUT_DEVICE), not here; --input-device/--output-device override
# them. "default" picks the ALSA/PipeWire default on Linux; where nothing matches (e.g. on
# macOS) the dropdown falls back to the system default device.
FALLBACK_DEVICE_NAME = "default"

# Curve colours for the magnitude/phase plots (woofer-NF, woofer-FF, spliced result, tweeter).
MP_CURVES = {
    "nf":     dict(name="Near field (woofer)",          color="#4C9BE8"),
    "ff":     dict(name="Windowed far-field (woofer)",  color="#E8954C"),
    "result": dict(name="Far-field result (LF)",        color="#F2D34B"),
    "tw":     dict(name="Tweeter (HF)",                 color="#5FD08C"),
}

# How much of the (multi-second) impulse response to actually hand to the time plot, around
# the peak. The rest is silence no one gates against; not plotting it keeps resize/zoom cheap
# because pyqtgraph re-downsamples the *clipped data region* on every resize event, and that
# cost scales with the number of points it holds -- not with what's on screen.
IR_DISPLAY_BEFORE_MS = 500.0
IR_DISPLAY_AFTER_MS = 2000.0

pg.setConfigOption("background", "#1e1e1e")
pg.setConfigOption("foreground", "#c8c8c8")
pg.setConfigOptions(antialias=True)


# Frequency-axis labelling. Major (labelled) ticks at these mantissas per decade -> e.g.
# 10, 20, 30, 50, 100, 200, 300, 500, 1k, 2k, 3k, 5k, 10k, 20k, 30k. The rest are minor
# (gridline only). Labels are suppressed outside [FREQ_LABEL_MIN, FREQ_LABEL_MAX].
FREQ_LABEL_MANTISSAS = (1, 2, 3, 5)
FREQ_LABEL_MIN = 10.0
FREQ_LABEL_MAX = 40000.0


def _fmt_freq(f):
    """Format a frequency as a plain audio label: 10, 200, 1k, 20k (no scientific notation)."""
    f = round(f)
    if f < 1000:
        return f"{int(f)}"
    k = f / 1000.0
    return f"{int(k)}k" if k == int(k) else f"{k:g}k"


class AudioLogAxis(pg.AxisItem):
    """Log frequency axis with plain audio labels (10/20/.../1k/20k) instead of 10ⁿ powers.

    Major ticks (labelled) sit at FREQ_LABEL_MANTISSAS per decade; the others are minor
    (gridline only). Labels outside [FREQ_LABEL_MIN, FREQ_LABEL_MAX] are blanked.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.enableAutoSIPrefix(False)  # don't let pyqtgraph add its own k/M prefix or scale

    def logTickValues(self, minVal, maxVal, size, stdTicks):
        # minVal/maxVal are in log10(Hz). Build major/minor tick lists ourselves.
        majors, minors = [], []
        for decade in range(int(np.floor(minVal)), int(np.ceil(maxVal)) + 1):
            for m in range(1, 10):
                lv = decade + np.log10(m)
                if lv < minVal - 1e-9 or lv > maxVal + 1e-9:
                    continue
                (majors if m in FREQ_LABEL_MANTISSAS else minors).append(lv)
        return [(1.0, majors), (None, minors)]

    def logTickStrings(self, values, scale, spacing):
        out = []
        for v in values:
            f = 10.0 ** v
            mant = int(round(f / 10.0 ** np.floor(v + 1e-9)))
            if f < FREQ_LABEL_MIN - 0.01 or f > FREQ_LABEL_MAX + 0.5 or mant not in FREQ_LABEL_MANTISSAS:
                out.append("")
            else:
                out.append(_fmt_freq(f))
        return out


# ---------------------------------------------------------------------------------------
# A plot widget with a row of curve-toggle checkboxes above it
# ---------------------------------------------------------------------------------------

class CurvePlot(QtWidgets.QWidget):
    """A pyqtgraph plot with one toggle checkbox per curve (curves are toggleable)."""

    def __init__(self, title, xlabel, ylabel, logx=False):
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self._toggle_bar = QtWidgets.QHBoxLayout()
        self._toggle_bar.setContentsMargins(6, 0, 6, 0)
        self._toggle_bar.addStretch()
        layout.addLayout(self._toggle_bar)

        axis_items = {"bottom": AudioLogAxis(orientation="bottom")} if logx else None
        self.plot = pg.PlotWidget(axisItems=axis_items)
        self.plot.setTitle(title)
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.setLabel("bottom", xlabel)
        self.plot.setLabel("left", ylabel)
        if logx:
            self.plot.setLogMode(x=True, y=False)
        self.plot.addLegend(offset=(-10, 10))
        # Large-dataset rendering: peak-preserving downsampling to the viewport width and
        # clipping to the visible x-range. This is what lets million-point curves stay
        # interactive (the main reason pyqtgraph is used here).
        self.plot.setDownsampling(auto=True, mode="peak")
        self.plot.setClipToView(True)
        layout.addWidget(self.plot, 1)

        self.curves = {}

    def add_curve(self, key, name, color, width=1.6, dashed=False):
        style = QtCore.Qt.DashLine if dashed else QtCore.Qt.SolidLine
        pen = pg.mkPen(color=color, width=width, style=style)
        curve = self.plot.plot([], [], name=name, pen=pen)
        self.curves[key] = curve

        cb = QtWidgets.QCheckBox(name)
        cb.setChecked(True)
        cb.setStyleSheet(f"color: {color};")
        cb.toggled.connect(curve.setVisible)
        self._toggle_bar.insertWidget(self._toggle_bar.count() - 1, cb)
        return curve

    def set_data(self, key, x, y):
        self.curves[key].setData(np.asarray(x), np.asarray(y))

    def clear_curve(self, key):
        self.curves[key].setData([], [])


# ---------------------------------------------------------------------------------------
# Background worker: play sweep, record, deconvolve to an impulse response
# ---------------------------------------------------------------------------------------

class MeasureWorker(QtCore.QObject):
    """Plays one sweep and records one or more input channels *simultaneously*, then
    deconvolves each channel into an impulse response for its target slot.

    `captures` is a list of (slot_key, input_channel). Recording all channels in a single
    playrec keeps them sample-aligned (one shared clock) -- essential for the woofer's
    near-field + far-field pair, whose relative timing/phase the reconstruction relies on.
    """
    finished = QtCore.Signal(object)        # {slot_key: impulse response}
    failed = QtCore.Signal(str)             # error message
    aborted = QtCore.Signal()               # user stopped the measurement
    status = QtCore.Signal(str)

    def __init__(self, captures, params):
        super().__init__()
        self.captures = captures
        self.p = params
        self._abort = False

    def request_abort(self):
        """Request abort (called from the GUI thread). Only sets a flag -- the worker thread
        polls it and stops its own stream. We must NOT touch the audio stream from here:
        calling sd.stop() from another thread against the running capture deadlocks on some
        ALSA/PipeWire backends (the stop races the stream's own teardown)."""
        self._abort = True

    def _record_abortable(self, sweep):
        """Play the sweep and record `self.captures` channels, non-blocking, polling for
        completion or abort. Only this (worker) thread ever touches the stream, so an abort
        via sd.stop() here can't race a blocking playrec's internal teardown."""
        sd = lib.sd
        p = self.p
        data = (np.asarray(sweep, dtype="float32") * p.get("level", 1.0)).reshape(-1, 1)
        # Extend the played (and therefore recorded) buffer with trailing silence so the mic
        # stays open past the end of playback -- this is the recording tail that lets a
        # latency-delayed response (e.g. through snapcast) arrive before the window closes.
        extra = int(p.get("extra_ms", 0) / 1000.0 * p["sr"])
        if extra > 0:
            data = np.vstack([data, np.zeros((extra, 1), dtype="float32")])
        in_map = [ch for _, ch in self.captures]
        rec = sd.playrec(
            data, samplerate=p["sr"],
            input_mapping=in_map, output_mapping=[p["out_ch"]],
            device=[p["in_dev"], p["out_dev"]],
            dtype="float32", blocksize=p["blocksize"], latency=p["latency"],
            blocking=False,
        )
        try:
            stream = sd._last_callback.stream
        except Exception:  # noqa: BLE001 -- fall back to a plain (non-abortable) wait
            stream = None
        if stream is None:
            sd.wait()
            return None if self._abort else rec
        max_seconds = len(data) / p["sr"] + 2.0   # hard safety cap
        t_start = time.monotonic()
        while stream.active:
            if self._abort:
                sd.stop()          # same thread that started the stream -> safe
                return None
            if time.monotonic() - t_start > max_seconds:
                break
            time.sleep(0.03)
        sd.wait()
        return rec

    def run(self):
        try:
            if self._abort:
                self.aborted.emit(); return
            p = self.p
            if lib.sd is None:
                raise RuntimeError("sounddevice is not installed.")
            self.status.emit(f"[{p['label']}] generating sweep...")
            _, sweep = generate_sine_sweep(
                p["start"], p["end"], p["duration"], p["sr"], padding_ms=p["padding"]
            )
            in_map = [ch for _, ch in self.captures]
            self.status.emit(f"[{p['label']}] playing & recording channels {in_map}...")
            rec = self._record_abortable(sweep)
            if self._abort or rec is None:
                self.aborted.emit(); return
            rec = np.atleast_2d(rec)
            if rec.shape[0] < rec.shape[1]:  # ensure (frames, channels)
                rec = rec.T
            self.status.emit(f"[{p['label']}] computing impulse response(s)...")
            results = {}
            for i, (slot, _ch) in enumerate(self.captures):
                col = rec[:, i] if rec.ndim > 1 else rec
                _, ir = compute_impulse_response_farina(sweep, col, p["sr"], p["start"], p["end"])
                results[slot] = np.asarray(ir, dtype=float)
            self.finished.emit(results)
        except Exception as exc:  # noqa: BLE001 -- surface any failure to the GUI
            if self._abort:
                self.aborted.emit()
            else:
                self.failed.emit(str(exc))


# ---------------------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------------------

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, default_names):
        super().__init__()
        self.setWindowTitle("Near-field loudspeaker measurement")
        self.resize(1500, 950)
        # Stability restriction: never let the window shrink below a size where the controls
        # and the (widest) plot toggle row still fit. Below this the layout would clip.
        self.setMinimumSize(1250, 720)

        self.default_names = default_names
        self.raw = {k: None for k in SLOTS}     # raw impulse responses
        self.result_ir = None                   # reconstructed far-field woofer IR (NF+FF splice)
        self.result_tw_ir = None                # windowed (gated) tweeter IR
        self.sr = 48000
        self.win = {}                           # per-slot window/peak controls
        self._ready = False
        self._queue = []                        # pending measurement keys
        self._thread = None
        self._worker = None
        self._focus_ir = False                  # re-frame the IR plot on next process()
        self._plotted_raw = {}                   # key -> (id, sr) of raw IR last sent to plot

        # Coalesce rapid control changes (e.g. dragging a window spinbox) into a single
        # recompute so the UI never stalls mid-interaction.
        self._recompute_timer = QtCore.QTimer(self)
        self._recompute_timer.setSingleShot(True)
        self._recompute_timer.setInterval(120)
        self._recompute_timer.timeout.connect(self.process)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QHBoxLayout(central)

        # Control column lives in a fixed-width scroll area: when the window is short the
        # controls scroll instead of being squashed/clipped, and they never steal width
        # from the plots when the window is resized.
        controls_scroll = QtWidgets.QScrollArea()
        controls_scroll.setWidget(self._build_controls())
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setFixedWidth(352)
        controls_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        controls_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        root.addWidget(controls_scroll)
        root.addWidget(self._build_plots(), 1)

        self.statusBar().showMessage("Ready.")
        self._ready = True
        self.populate_devices()
        self._apply_freq_view()

    def _schedule(self):
        """Request a (debounced) recompute after a control change."""
        if self._ready:
            self._recompute_timer.start()

    def _apply_freq_view(self):
        """Set the magnitude/phase x-range (and pan/zoom limits) to the selected band."""
        if getattr(self, "mag_plot", None) is None:
            return
        fmin = float(self.fmin_spin.value())
        fmax = float(self.fmax_spin.value())
        if fmax <= fmin:
            fmax = fmin + 1.0
        lo, hi = np.log10(fmin), np.log10(fmax)  # log mode -> ranges are in log10(Hz)
        for cp in (self.mag_plot, self.phase_plot):
            cp.plot.getViewBox().setLimits(xMin=lo, xMax=hi)
        self.mag_plot.plot.setXRange(lo, hi, padding=0)  # phase follows via x-link

    # -- UI construction ----------------------------------------------------------------

    def _build_controls(self):
        panel = QtWidgets.QWidget()
        panel.setFixedWidth(330)
        v = QtWidgets.QVBoxLayout(panel)

        # Devices
        dev_box = QtWidgets.QGroupBox("Audio devices")
        dev_form = QtWidgets.QFormLayout(dev_box)
        self.in_combo = QtWidgets.QComboBox()
        self.out_combo = QtWidgets.QComboBox()
        # Long device names (e.g. "HD-Audio Generic: ALC245 Analog (hw:1,0)") must not push
        # the fixed-width panel around: size the combo to a short content length and let the
        # box elide; the full name is still shown in the dropdown popup.
        for combo in (self.in_combo, self.out_combo):
            combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(8)
        self.out_ch = QtWidgets.QSpinBox(); self.out_ch.setRange(1, 64); self.out_ch.setValue(1)
        refresh = QtWidgets.QPushButton("Refresh devices")
        refresh.clicked.connect(self.populate_devices)
        dev_form.addRow("Input", self.in_combo)
        dev_form.addRow("Output", self.out_combo)
        dev_form.addRow("Output ch", self.out_ch)
        dev_form.addRow(refresh)
        # Warns when input and output are different devices: they run on independent sample
        # clocks that slowly drift apart, which slips buffers and causes clicks/pops on long
        # sweeps. Using one interface for both playback and capture shares a single clock.
        self.clock_warn = QtWidgets.QLabel()
        self.clock_warn.setWordWrap(True)
        self.clock_warn.setStyleSheet("color: #E8A33C;")
        self.clock_warn.setVisible(False)
        dev_form.addRow(self.clock_warn)
        self.in_combo.currentIndexChanged.connect(self._check_clock_domains)
        self.out_combo.currentIndexChanged.connect(self._check_clock_domains)
        v.addWidget(dev_box)

        # Sweep parameters
        sweep_box = QtWidgets.QGroupBox("Sweep")
        sf = QtWidgets.QFormLayout(sweep_box)
        self.start_spin = self._spin(20, 1, 20000, 0)
        self.end_spin = self._spin(20000, 1, 96000, 0)
        self.dur_spin = self._dspin(10.0, 0.5, 60.0, 1)
        self.sr_spin = self._spin(48000, 8000, 192000, 0)
        self.pad_spin = self._spin(1000, 0, 10000, 0)
        # Digital playback level. Default 0.5 (-6 dBFS) leaves headroom so nothing clips; drive
        # the actual SPL with the interface's analog gain instead of a hot digital signal.
        self.level_spin = self._dspin(0.5, 0.0, 1.0, 2)
        sf.addRow("Start (Hz)", self.start_spin)
        sf.addRow("End (Hz)", self.end_spin)
        sf.addRow("Duration (s)", self.dur_spin)
        sf.addRow("Sample rate", self.sr_spin)
        sf.addRow("Padding (ms)", self.pad_spin)
        sf.addRow("Output level", self.level_spin)
        # Inspect the exact signal that will be played, with the current parameters.
        self.show_sweep_btn = QtWidgets.QPushButton("Show sweep (amplitude vs time)")
        self.show_sweep_btn.clicked.connect(self.show_sweep)
        sf.addRow(self.show_sweep_btn)
        self.sr_spin.valueChanged.connect(self._on_sr_changed)
        v.addWidget(sweep_box)

        # Audio buffer / latency. blocksize=64 (the old value) is a ~1.3 ms buffer that
        # underruns easily and forces PipeWire to a tiny quantum -> clicks/pops mid-sweep.
        # For a measurement, latency is irrelevant, so use a big buffer + high latency.
        buf_box = QtWidgets.QGroupBox("Audio buffer")
        bf = QtWidgets.QFormLayout(buf_box)
        self.blocksize_spin = self._spin(1024, 0, 16384, 0)   # frames; 0 = let PortAudio choose
        self.latency_combo = QtWidgets.QComboBox(); self.latency_combo.addItems(["high", "low"])
        bf.addRow("Block size", self.blocksize_spin)
        bf.addRow("Latency", self.latency_combo)
        # Extra recording time: the mic keeps recording this long AFTER the playback signal
        # ends. Playback may run through a buffered system (e.g. snapcast) which delays the acoustic
        # output by its buffer latency (~seconds); the response therefore arrives late. Without
        # a recording tail the late/high-frequency end of the sweep falls outside the record
        # window and is lost -- the measured response then looks band-limited at whatever
        # frequency the sweep had reached when the window closed. Set this >= the system latency.
        self.extra_rec_spin = self._spin(0, 0, 10000, 0)   # ms
        bf.addRow("Extra record (ms)", self.extra_rec_spin)
        note = QtWidgets.QLabel(
            "Mic keeps recording this long after playback ends, to catch responses delayed by "
            "system/playback latency (e.g. snapcast buffering). Set ≥ the round-trip latency so "
            "the delayed sweep tail (high frequencies) isn't cut off.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#888; font-size: 10px;")
        bf.addRow(note)
        v.addWidget(buf_box)

        # Physics / processing parameters
        phys_box = QtWidgets.QGroupBox("Reconstruction")
        pf = QtWidgets.QFormLayout(phys_box)
        self.a_spin = self._dspin(0.04, 0.001, 1.0, 4)
        self.r_spin = self._dspin(0.42, 0.01, 10.0, 3)
        self.fmatch_spin = self._spin(1350, 50, 20000, 0)
        self.smooth_spin = self._spin(6, 0, 48, 0)
        for w in (self.a_spin, self.r_spin, self.fmatch_spin, self.smooth_spin):
            w.valueChanged.connect(self._schedule)
        pf.addRow("Radius a (m)", self.a_spin)
        pf.addRow("Distance r (m)", self.r_spin)
        pf.addRow("Match freq (Hz)", self.fmatch_spin)
        pf.addRow("Smoothing (1/n oct)", self.smooth_spin)
        v.addWidget(phys_box)

        # Frequency-axis view limits (applied to both the magnitude and phase plots).
        fview_box = QtWidgets.QGroupBox("Frequency view (Hz)")
        ff = QtWidgets.QFormLayout(fview_box)
        self.fmin_spin = self._spin(20, 1, 40000, 0)
        self.fmax_spin = self._spin(20000, 2, 96000, 0)
        self.fmin_spin.valueChanged.connect(self._apply_freq_view)
        self.fmax_spin.valueChanged.connect(self._apply_freq_view)
        ff.addRow("View min", self.fmin_spin)
        ff.addRow("View max", self.fmax_spin)
        v.addWidget(fview_box)

        # Each measurement (NF woofer, FF woofer, tweeter) is captured independently, with its
        # own Measure button and mic channel.
        for key, cfg in SLOTS.items():
            v.addWidget(self._build_slot_box(key, cfg, measurable=True))

        # Global actions
        self.stop_btn = QtWidgets.QPushButton("Stop / abort measurement")
        self.stop_btn.setEnabled(False)  # only while a measurement is running
        self.stop_btn.setStyleSheet("QPushButton:enabled { color: #E86C6C; font-weight: bold; }")
        self.stop_btn.clicked.connect(self.stop_measurement)
        v.addWidget(self.stop_btn)

        save_btn = QtWidgets.QPushButton("Save IRs + results (woofer + tweeter)")
        save_btn.clicked.connect(self.save_all)
        v.addWidget(save_btn)
        v.addStretch()
        return panel

    def _build_slot_box(self, key, cfg, measurable=False):
        box = QtWidgets.QGroupBox(cfg["label"])
        grid = QtWidgets.QGridLayout(box)
        row = 0

        status = QtWidgets.QLabel("empty")
        status.setStyleSheet("color: #888;")
        grid.addWidget(status, row, 0, 1, 2); row += 1

        ctrl = dict(status=status, measure_btn=None, channel=None)

        if measurable:
            channel = QtWidgets.QSpinBox(); channel.setRange(1, 64); channel.setValue(cfg.get("channel", 1))
            grid.addWidget(QtWidgets.QLabel("Mic channel"), row, 0)
            grid.addWidget(channel, row, 1); row += 1
            measure_btn = QtWidgets.QPushButton("Measure")
            measure_btn.clicked.connect(lambda _, k=key: self.measure_slot(k))
            load_btn = QtWidgets.QPushButton("Load wav")
            load_btn.clicked.connect(lambda _, k=key: self.load_slot(k))
            grid.addWidget(measure_btn, row, 0)
            grid.addWidget(load_btn, row, 1); row += 1
            ctrl["measure_btn"] = measure_btn
            ctrl["channel"] = channel
        else:
            load_btn = QtWidgets.QPushButton("Load wav")
            load_btn.clicked.connect(lambda _, k=key: self.load_slot(k))
            grid.addWidget(load_btn, row, 0, 1, 2); row += 1
        ctrl["load_btn"] = load_btn

        left = self._dspin(cfg["left"], 0.0, 2000.0, 1)
        right = self._dspin(cfg["right"], 0.0, 2000.0, 1)
        peak = QtWidgets.QComboBox(); peak.addItems(["max", "min", "abs"])
        left.valueChanged.connect(self._schedule)
        right.valueChanged.connect(self._schedule)
        peak.currentIndexChanged.connect(self._schedule)
        grid.addWidget(QtWidgets.QLabel("Window L (ms)"), row, 0)
        grid.addWidget(left, row, 1); row += 1
        grid.addWidget(QtWidgets.QLabel("Window R (ms)"), row, 0)
        grid.addWidget(right, row, 1); row += 1
        grid.addWidget(QtWidgets.QLabel("Peak mode"), row, 0)
        grid.addWidget(peak, row, 1); row += 1

        ctrl.update(left=left, right=right, peak=peak)
        self.win[key] = ctrl
        return box

    def _build_plots(self):
        container = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)

        # Top: impulse responses + windows vs time
        self.ir_plot = CurvePlot("Impulse responses & windows", "Time (ms)", "Amplitude")
        for key, cfg in SLOTS.items():
            self.ir_plot.add_curve(key, f"{cfg['label']} IR", cfg["color"])
            self.ir_plot.add_curve(key + "_win", f"{cfg['label']} window", cfg["color"], width=1.0, dashed=True)

        # Middle: magnitude
        self.mag_plot = CurvePlot("Magnitude response", "Frequency (Hz)", "Magnitude (dB)", logx=True)
        for key, cfg in MP_CURVES.items():
            self.mag_plot.add_curve(key, cfg["name"], cfg["color"],
                                    width=2.2 if key == "result" else 1.6)

        # Bottom: phase
        self.phase_plot = CurvePlot("Phase response", "Frequency (Hz)", "Phase (deg)", logx=True)
        for key, cfg in MP_CURVES.items():
            self.phase_plot.add_curve(key, cfg["name"], cfg["color"],
                                      width=2.2 if key == "result" else 1.6)

        # Minimum heights so no plot can be resized away to nothing.
        self.ir_plot.setMinimumHeight(130)
        self.mag_plot.setMinimumHeight(170)
        self.phase_plot.setMinimumHeight(170)

        # Frequency plots: fixed x (driven by the Frequency-view controls), auto y to the
        # visible band, and zoom/pan the two together.
        for cp in (self.mag_plot, self.phase_plot):
            cp.plot.getViewBox().enableAutoRange(x=False, y=True)
        self.phase_plot.plot.setXLink(self.mag_plot.plot)

        v.addWidget(self.ir_plot, 2)
        v.addWidget(self.mag_plot, 3)
        v.addWidget(self.phase_plot, 3)
        return container

    @staticmethod
    def _spin(val, lo, hi, _decimals):
        w = QtWidgets.QSpinBox(); w.setRange(int(lo), int(hi)); w.setValue(int(val)); return w

    @staticmethod
    def _dspin(val, lo, hi, decimals):
        w = QtWidgets.QDoubleSpinBox(); w.setDecimals(decimals)
        w.setRange(lo, hi); w.setSingleStep(10 ** -decimals if decimals else 1); w.setValue(val)
        return w

    # -- Devices ------------------------------------------------------------------------

    def populate_devices(self):
        self.in_combo.clear(); self.out_combo.clear()
        if lib.sd is None:
            for combo in (self.in_combo, self.out_combo):
                combo.addItem("(sounddevice not installed)", None)
                combo.setEnabled(False)
            self.statusBar().showMessage(
                "sounddevice not installed -- capture disabled. You can still load/tune wavs."
            )
            return
        try:
            devices = lib.sd.query_devices()
        except Exception as exc:  # noqa: BLE001
            self.statusBar().showMessage(f"Could not query audio devices: {exc}")
            return
        for idx, d in enumerate(devices):
            label = f"{idx}: {d['name']}"
            if d["max_input_channels"] > 0:
                self.in_combo.addItem(label, idx)
            if d["max_output_channels"] > 0:
                self.out_combo.addItem(label, idx)
        self._preselect(self.in_combo, self.default_names["input"], "input")
        self._preselect(self.out_combo, self.default_names["output"], "output")
        self._check_clock_domains()

    def _check_clock_domains(self):
        """Warn when input and output are different devices (independent clocks -> drift ->
        clicks/pops on long sweeps)."""
        in_dev = self.in_combo.currentData()
        out_dev = self.out_combo.currentData()
        differ = in_dev is not None and out_dev is not None and in_dev != out_dev
        if differ:
            self.clock_warn.setText(
                "⚠ Input and output are different devices. Their clocks drift apart and "
                "cause clicks/pops on long sweeps. Use one interface for both if you can.")
        self.clock_warn.setVisible(differ)

    def _preselect(self, combo, name, kind):
        # Try matching the platform default name as a case-insensitive substring.
        for i in range(combo.count()):
            if name and name.lower() in combo.itemText(i).lower():
                combo.setCurrentIndex(i)
                return
        # Fall back to the system default device index, if available.
        try:
            default_idx = lib.sd.default.device[0 if kind == "input" else 1]
        except Exception:  # noqa: BLE001
            default_idx = None
        if default_idx is not None and default_idx >= 0:
            for i in range(combo.count()):
                if combo.itemData(i) == default_idx:
                    combo.setCurrentIndex(i)
                    return

    # -- Measurement --------------------------------------------------------------------

    def measure_slot(self, key):
        """Capture a single slot (NF woofer, FF woofer, or tweeter) on its mic channel."""
        self.start_jobs([dict(
            label=SLOTS[key]["label"],
            captures=[(key, self.win[key]["channel"].value())],
        )])

    def start_jobs(self, jobs):
        if self._thread is not None:
            self.statusBar().showMessage("A measurement is already running.")
            return
        if lib.sd is None:
            self.statusBar().showMessage("Cannot measure: sounddevice is not installed.")
            return
        self._queue = list(jobs)
        self._run_next()

    def _run_next(self):
        if not self._queue:
            self.statusBar().showMessage("Measurement sequence complete.")
            self.process()
            return
        job = self._queue.pop(0)
        in_dev = self.in_combo.currentData()
        out_dev = self.out_combo.currentData()
        if in_dev is None or out_dev is None:
            self.statusBar().showMessage("Select valid input and output devices first.")
            self._queue = []
            return
        params = dict(
            label=job["label"],
            start=self.start_spin.value(), end=self.end_spin.value(),
            duration=self.dur_spin.value(), sr=self.sr_spin.value(),
            padding=self.pad_spin.value(),
            in_dev=in_dev, out_dev=out_dev, out_ch=self.out_ch.value(),
            level=self.level_spin.value(),
            blocksize=self.blocksize_spin.value(),
            latency=self.latency_combo.currentText(),
            extra_ms=self.extra_rec_spin.value(),
        )
        self._set_controls_enabled(False)
        self._thread = QtCore.QThread()
        self._worker = MeasureWorker(job["captures"], params)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.status.connect(self.statusBar().showMessage)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.aborted.connect(self._on_aborted)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._worker.aborted.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    def _cleanup_thread(self):
        if self._thread is not None:
            self._thread.deleteLater()
        self._thread = None
        self._worker = None
        self._set_controls_enabled(True)
        # Continue any queued measurements.
        if self._queue:
            self._run_next()

    def _on_finished(self, results):
        self.sr = self.sr_spin.value()
        for key, ir in results.items():
            self.raw[key] = ir
            self.win[key]["status"].setText(f"captured ({len(ir)} samples)")
            self.win[key]["status"].setStyleSheet("color: #5FD08C;")
        labels = ", ".join(SLOTS[k]["label"] for k in results)
        self.statusBar().showMessage(f"Captured: {labels}.")
        self._focus_ir = True
        self.process()

    def _on_failed(self, msg):
        self.statusBar().showMessage(f"Measurement failed: {msg}")
        self._queue = []

    def _on_aborted(self):
        self.statusBar().showMessage("Measurement aborted.")

    def stop_measurement(self):
        """Abort the running measurement and cancel any queued ones."""
        if self._worker is None:
            self.statusBar().showMessage("No measurement running.")
            return
        self._queue = []            # cancel pending queued measurements
        self.stop_btn.setEnabled(False)
        self.statusBar().showMessage("Aborting measurement...")
        self._worker.request_abort()

    def _set_controls_enabled(self, enabled):
        self.stop_btn.setEnabled(not enabled)  # stop is only usable while measuring
        for ctrl in self.win.values():
            if ctrl["measure_btn"] is not None:
                ctrl["measure_btn"].setEnabled(enabled)
            ctrl["load_btn"].setEnabled(enabled)

    # -- Loading ------------------------------------------------------------------------

    def load_slot(self, key):
        default = str(config.data_path(SLOTS[key]["wav"]))
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, f"Load {SLOTS[key]['label']} wav", default, "WAV files (*.wav)"
        )
        if not path:
            return
        self._load_wav_into(key, path)
        self._focus_ir = True
        self.process()

    def _load_wav_into(self, key, path):
        try:
            sr, data = lib.read(path)
        except Exception as exc:  # noqa: BLE001
            self.statusBar().showMessage(f"Could not read {path}: {exc}")
            return False
        data = np.asarray(data, dtype=float)
        if data.ndim > 1:
            data = data[:, 0]
        peak = np.max(np.abs(data))
        if peak > 0:
            data = data / peak
        self.raw[key] = data
        self.sr = sr
        self.sr_spin.blockSignals(True)
        self.sr_spin.setValue(int(sr))
        self.sr_spin.blockSignals(False)
        self.win[key]["status"].setText(f"loaded {sr} Hz ({len(data)} samples)")
        self.win[key]["status"].setStyleSheet("color: #5FD08C;")
        return True

    def load_existing(self):
        """Load any of the default wav files that exist in the data folder (startup
        convenience)."""
        loaded = []
        for key, cfg in SLOTS.items():
            path = config.data_path(cfg["wav"])
            if path.is_file():
                if self._load_wav_into(key, str(path)):
                    loaded.append(cfg["label"])
        if loaded:
            self.statusBar().showMessage(
                f"Loaded existing from {config.data_dir()}: " + ", ".join(loaded))
            self._focus_ir = True
        self.process()

    # -- Saving -------------------------------------------------------------------------

    def save_all(self):
        if lib.sf is None:
            self.statusBar().showMessage("Cannot save: soundfile is not installed.")
            return
        out_dir = config.data_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        saved = []
        for key, cfg in SLOTS.items():
            if self.raw[key] is not None:
                save_ir_to_wav(str(out_dir / cfg["wav"]), self.raw[key], self.sr)
                saved.append(cfg["wav"])
        # Export the reconstructed far-field woofer IR (NF+FF splice) if it's available.
        if self.result_ir is not None:
            save_ir_to_wav(str(out_dir / RESULT_WAV), self.result_ir, self.sr)
            saved.append(RESULT_WAV)
        # Export the windowed (gated) tweeter IR -- the clean tweeter for downstream tools.
        if self.result_tw_ir is not None:
            save_ir_to_wav(str(out_dir / RESULT_TW_WAV), self.result_tw_ir, self.sr)
            saved.append(RESULT_TW_WAV)
        self.statusBar().showMessage(
            (f"Saved to {out_dir}: " + ", ".join(saved)) if saved else "Nothing to save")

    # -- Processing & plotting ----------------------------------------------------------

    def _gate(self, key, ir):
        """Compute the asymmetric-Hanning gate *only over its support* (cheap).

        Mirrors lib.apply_asymmetric_hanning_window (centred on the abs peak, half-Hanning
        on each side) but allocates an array the size of the window, not the whole multi-
        second capture. Returns (lo, hi, window) where ir[lo:hi] is the gated region.
        """
        sr = self.sr
        left = int(self.win[key]["left"].value() / 1000.0 * sr)
        right = int(self.win[key]["right"].value() / 1000.0 * sr)
        peak = int(np.argmax(np.abs(ir)))
        lo = max(0, peak - left)
        hi = min(len(ir), peak + right)
        w = np.zeros(hi - lo)
        ll = peak - lo
        if ll > 0:
            w[:ll] = np.hanning(2 * ll)[:ll]
        rl = hi - peak
        if rl > 0:
            w[ll:ll + rl] = np.hanning(2 * rl)[rl:]
        return lo, hi, w

    def _nfft(self):
        """Common FFT length for all measurements.

        A gated impulse response only carries information over its (short) window; the rest
        of a multi-second capture is zeros. FFT'ing the full length wastes time and yields
        absurd (sub-Hz) resolution. We size the FFT to ~2x the widest window across the
        active slots so every slot shares one frequency vector (needed to splice NF+FF),
        keeping it a power of two and clamped to a sane range.
        """
        max_support = 0
        for key in SLOTS:
            if self.raw[key] is None:
                continue
            width_s = (self.win[key]["left"].value() + self.win[key]["right"].value()) / 1000.0
            max_support = max(max_support, int(width_s * self.sr))
        target = max(2 * max_support, 2)
        n = 1 << int(np.ceil(np.log2(target)))
        return int(np.clip(n, 1 << 15, 1 << 18))

    def _win_fft(self, key, ir, n_fft):
        """Gate `ir` to its window support and zero-pad/truncate to `n_fft`."""
        lo, hi, w = self._gate(key, ir)
        seg = ir[lo:hi] * w
        if len(seg) == 0:
            return None
        out = np.zeros(n_fft)
        m = min(len(seg), n_fft)
        out[:m] = seg[:m]
        return out

    def _set_mag(self, plot_key, freqs, mag):
        freqs = np.asarray(freqs); mag = np.asarray(mag)
        sel = freqs > 0
        db = 20 * np.log10(np.maximum(mag[sel], 1e-12))
        self.mag_plot.set_data(plot_key, freqs[sel], db)

    def _set_phase(self, plot_key, freqs, phase):
        freqs = np.asarray(freqs); phase = np.asarray(phase)
        sel = freqs > 0
        self.phase_plot.set_data(plot_key, freqs[sel], np.degrees(phase[sel]))

    def process(self):
        if not self._ready:
            return
        self.sr = self.sr_spin.value()
        sr = self.sr
        smoothing = self.smooth_spin.value()
        a = self.a_spin.value(); r = self.r_spin.value(); fmatch = self.fmatch_spin.value()

        n_fft = self._nfft()

        # --- top plot: raw IRs + windows -------------------------------------------------
        focus_lo, focus_hi = np.inf, -np.inf
        for key in SLOTS:
            ir = self.raw[key]
            if ir is None:
                self.ir_plot.clear_curve(key)
                self.ir_plot.clear_curve(key + "_win")
                self._plotted_raw.pop(key, None)
                continue
            peak = int(np.argmax(np.abs(ir)))
            peak_ms = peak / sr * 1000.0
            # Re-ingest the raw IR only when it actually changes (not on every window tweak),
            # and only a bounded region around the peak -- this caps the points pyqtgraph must
            # re-downsample on each resize event, which is what makes maximize/resize smooth.
            stamp = (id(ir), sr)
            if self._plotted_raw.get(key) != stamp:
                d_lo = max(0, peak - int(IR_DISPLAY_BEFORE_MS / 1000.0 * sr))
                d_hi = min(len(ir), peak + int(IR_DISPLAY_AFTER_MS / 1000.0 * sr))
                self.ir_plot.set_data(key, np.arange(d_lo, d_hi) / sr * 1000.0, ir[d_lo:d_hi])
                self._plotted_raw[key] = stamp
            # Window overlay: only its support (a few thousand points), updated live.
            lo, hi, w = self._gate(key, ir)
            self.ir_plot.set_data(key + "_win", np.arange(lo, hi) / sr * 1000.0, w)
            # Track the gated region so we can frame the IR plot.
            focus_lo = min(focus_lo, peak_ms - self.win[key]["left"].value() - 10)
            focus_hi = max(focus_hi, peak_ms + self.win[key]["right"].value() + 30)

        if self._focus_ir and np.isfinite(focus_lo):
            self.ir_plot.plot.setXRange(max(0.0, focus_lo), focus_hi, padding=0.05)
            self._focus_ir = False

        nr, ff, tw = self.raw["nr_wf"], self.raw["ff_wf"], self.raw["ff_tw"]
        norm_factor = None
        self.result_ir = None  # only valid when both woofer measurements are present
        self.result_tw_ir = None  # only valid when a tweeter measurement is present

        for k in ("nf", "ff", "result", "tw"):
            self.mag_plot.clear_curve(k)
            self.phase_plot.clear_curve(k)

        # --- woofer: splice near-field + far-field --------------------------------------
        if nr is not None and ff is not None:
            w_nr = self._win_fft("nr_wf", nr, n_fft)
            w_ff = self._win_fft("ff_wf", ff, n_fft)
            peak_nr = self.win["nr_wf"]["peak"].currentText()
            peak_ff = self.win["ff_wf"]["peak"].currentText()
            fr_ff, mag_ff, ph_ff = compute_magnitude_phase(w_ff, sr, mode=peak_ff, smoothing=smoothing, normalize=1)
            fr_nr, mag_nr, ph_nr = compute_magnitude_phase(w_nr, sr, mode=peak_nr, smoothing=smoothing, normalize=1)
            fr = fr_nr  # identical length (shared n_fft)
            mag_tot, ph_tot, norm_factor = compute_total_far_field_response(
                fr, mag_nr, ph_nr, mag_ff, ph_ff, a, r, fmatch, sr, normalize=0
            )
            # Scale the NF/FF curves so they line up with the (1kHz-normalized) result for
            # easy visual checking of the splice at fmatch.
            nmatch = find_nmatch(mag_nr, sr, fmatch)
            c4 = mag_ff[nmatch] / (mag_nr[nmatch] + 1e-12)
            self._set_mag("nf", fr, mag_nr * c4 * norm_factor)
            self._set_mag("ff", fr, mag_ff * norm_factor)
            self._set_mag("result", fr, mag_tot)
            self._set_phase("nf", fr, ph_nr)
            self._set_phase("ff", fr, ph_ff)
            self._set_phase("result", fr, ph_tot)
            # Reconstructed far-field woofer IR (time domain) = inverse rfft of the spliced
            # transfer function that the 'result' curve shows. This is the exportable output.
            Hlf = mag_tot * np.exp(1j * ph_tot)
            self.result_ir = np.fft.irfft(Hlf, n=2 * len(Hlf) - 1)
        else:
            # Show whatever single woofer measurement is available, on its own scale.
            if nr is not None:
                w_nr = self._win_fft("nr_wf", nr, n_fft)
                fr_nr, mag_nr, ph_nr = compute_magnitude_phase(
                    w_nr, sr, mode=self.win["nr_wf"]["peak"].currentText(), smoothing=smoothing, normalize=1)
                self._set_mag("nf", fr_nr, mag_nr); self._set_phase("nf", fr_nr, ph_nr)
            if ff is not None:
                w_ff = self._win_fft("ff_wf", ff, n_fft)
                fr_ff, mag_ff, ph_ff = compute_magnitude_phase(
                    w_ff, sr, mode=self.win["ff_wf"]["peak"].currentText(), smoothing=smoothing, normalize=1)
                self._set_mag("ff", fr_ff, mag_ff); self._set_phase("ff", fr_ff, ph_ff)

        # --- tweeter (HF) ---------------------------------------------------------------
        if tw is not None:
            w_tw = self._win_fft("ff_tw", tw, n_fft)
            norm = norm_factor if norm_factor is not None else 1
            fr_tw, mag_tw, ph_tw = compute_magnitude_phase(
                w_tw, sr, mode=self.win["ff_tw"]["peak"].currentText(), smoothing=smoothing, normalize=norm)
            self._set_mag("tw", fr_tw, mag_tw)
            self._set_phase("tw", fr_tw, ph_tw)
            # Windowed tweeter IR (time-domain of the gated transfer function) -- the clean,
            # reflection-free tweeter deliverable, mirroring the woofer's result_ir.
            Hhf = mag_tw * np.exp(1j * ph_tw)
            self.result_tw_ir = np.fft.irfft(Hhf, n=2 * len(Hhf) - 1)

    def _on_sr_changed(self):
        self.sr = self.sr_spin.value()
        self._schedule()

    def show_sweep(self):
        """Plot the exact signal that will be played (amplitude vs time) for the current sweep
        parameters, so the effect of each parameter -- including padding -- is visible. The
        sweep proper is shaded; the flat regions on either side are the zero-padding."""
        try:
            start = self.start_spin.value()
            end = self.end_spin.value()
            dur = self.dur_spin.value()
            sr = self.sr_spin.value()
            pad = self.pad_spin.value()
            level = self.level_spin.value()
            t, sweep = generate_sine_sweep(start, end, dur, sr, padding_ms=pad)
        except Exception as exc:  # noqa: BLE001
            self.statusBar().showMessage(f"Could not generate sweep: {exc}")
            return
        sweep = np.asarray(sweep, dtype=float) * level

        if getattr(self, "_sweep_dlg", None) is None:
            self._sweep_dlg = QtWidgets.QDialog(self)
            self._sweep_dlg.setWindowTitle("Generated sweep (amplitude vs time)")
            self._sweep_dlg.resize(1000, 460)
            lay = QtWidgets.QVBoxLayout(self._sweep_dlg)
            self._sweep_plot = pg.PlotWidget()
            self._sweep_plot.setLabel("bottom", "Time (s)")
            self._sweep_plot.setLabel("left", "Amplitude")
            self._sweep_plot.showGrid(x=True, y=True, alpha=0.3)
            self._sweep_plot.setDownsampling(auto=True, mode="peak")
            self._sweep_plot.setClipToView(True)
            lay.addWidget(self._sweep_plot, 1)
            self._sweep_info = QtWidgets.QLabel()
            self._sweep_info.setWordWrap(True)
            lay.addWidget(self._sweep_info)

        p = self._sweep_plot
        p.clear()
        p.plot(t, sweep, pen=pg.mkPen("#4C9BE8", width=1.0))
        # Shade the actual sweep span; everything outside it is zero-padding (silence).
        pad_s = pad / 1000.0
        region = pg.LinearRegionItem([pad_s, pad_s + dur], movable=False,
                                     brush=(95, 208, 140, 45), pen=pg.mkPen((95, 208, 140, 120)))
        region.setZValue(-10)
        p.addItem(region)
        p.setXRange(0, t[-1] if len(t) else 1, padding=0.01)
        f_at = lambda frac: start * (end / start) ** frac  # instantaneous freq at a sweep fraction
        self._sweep_info.setText(
            f"Sweep {start}→{end} Hz over {dur:.2f} s  (green shading)  |  "
            f"padding {pad} ms each side  |  total {t[-1]:.2f} s, {len(sweep)} samples @ {sr} Hz.\n"
            f"Padding is silence added around the sweep — it does NOT change the sweep's "
            f"duration or its {start}→{end} Hz range. Instantaneous frequency reaches "
            f"~{f_at(0.5)/1000:.1f} kHz at the sweep midpoint and {end/1000:.1f} kHz at its end.")
        self._sweep_dlg.show()
        self._sweep_dlg.raise_()
        self._sweep_dlg.activateWindow()


# ---------------------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------------------

def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Near-field loudspeaker measurement GUI.")
    p.add_argument("--measure", choices=["nf", "ff", "tweeter", "all"],
                   help="Auto-trigger one measurement on startup: 'nf' (near woofer), "
                        "'ff' (far woofer), 'tweeter', or 'all' (each in sequence).")
    p.add_argument("--input-device",
                   help="Input device name (substring). Default: NF_INPUT_DEVICE from "
                        "config/local.env, else the system default.")
    p.add_argument("--output-device",
                   help="Output device name (substring). Default: NF_OUTPUT_DEVICE from "
                        "config/local.env, else the system default.")
    p.add_argument("--data-dir",
                   help="Folder the measurement wavs are loaded from and saved to. "
                        "Default: NF_DATA_DIR from config/local.env, else python_dsp_tools/data.")
    p.add_argument("--no-load", action="store_true",
                   help="Do not auto-load existing wav files on startup.")
    p.add_argument("--opengl", action="store_true",
                   help="Use GPU/OpenGL line rendering (faster on big datasets; needs "
                        "pyopengl, and may flicker on some Linux GL drivers).")
    return p.parse_args(argv)


def resolve_default_names(args):
    """Device-name substrings to preselect: CLI flag > config/local.env > "default"."""
    return dict(
        input=args.input_device or config.get("NF_INPUT_DEVICE", FALLBACK_DEVICE_NAME),
        output=args.output_device or config.get("NF_OUTPUT_DEVICE", FALLBACK_DEVICE_NAME),
    )


def main(argv=None):
    args = parse_args(argv)
    if args.data_dir:
        os.environ["NF_DATA_DIR"] = os.path.abspath(os.path.expanduser(args.data_dir))
    if args.opengl:
        pg.setConfigOptions(useOpenGL=True, enableExperimental=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = MainWindow(resolve_default_names(args))
    win.show()
    if not args.no_load:
        win.load_existing()
    if args.measure:
        def auto_measure():
            slot_for = {"nf": "nr_wf", "ff": "ff_wf", "tweeter": "ff_tw"}
            keys = list(SLOTS) if args.measure == "all" else [slot_for[args.measure]]
            jobs = [dict(label=SLOTS[k]["label"],
                         captures=[(k, win.win[k]["channel"].value())]) for k in keys]
            win.start_jobs(jobs)
        QtCore.QTimer.singleShot(400, auto_measure)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
