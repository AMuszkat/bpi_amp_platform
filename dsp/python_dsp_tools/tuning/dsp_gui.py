"""
dsp_gui.py -- PyQt/pyqtgraph GUI for DSP analysis & filter design/simulation.

Companion to measurement/near_field_gui.py. Where that GUI *measures* a loudspeaker (captures sweeps,
reconstructs the anechoic response, tunes the time gate), this one *designs and simulates
filters* against those already-measured responses.

It takes the driver-design chain of scripts/analysis.py -> lib.process_2_way_speaker and makes every
filter-design parameter live-adjustable, showing the result across three stacked, log-frequency
plots (magnitude / phase / group delay) with per-curve toggles.

Data flow
---------
  1. Load TWO impulse-response wavs -- whatever the user wants:
       * "Woofer"  -- e.g. the reconstructed far-field woofer saved by near_field_gui.py
                      (ir_result_wf.wav), or any low/mid driver IR.
       * "Tweeter" -- e.g. the windowed tweeter ir_result_tw.wav, or any high driver IR.
     The IRs are used as loaded (no time gating). The woofer is normalised to 0 dB at 1 kHz,
     and that same normalisation gain is applied to the tweeter, so the woofer/tweeter level
     relationship is preserved; the module's HF-gain control fine-tunes the tweeter on top.
  2. Build (freqs, Hlf, Hhf) from the two responses.
  3. Run the active *processing module* on (freqs, Hlf, Hhf) and plot every transfer
     function it produces. The only module for now is the two-way crossover designer.

Extensibility
-------------
A processing module is a small self-contained object (see `ProcessingModule`): it owns its
parameter controls, declares the set of curves it draws (`curve_specs`), and computes them
from a `ModuleContext` (`process`). Adding a new kind of filter analysis (e.g. a single-
driver FIR correction, parametric EQ, a linear-phase brickwall, room-target shaping...) is:
add a `ProcessingModule` subclass and register it in `MODULES`. The plotting, curve toggles,
source loading, and C++ export are all module-agnostic and shared. Only the crossover module
is implemented for now, by request.

Usage:
    python dsp_gui.py                 # open, auto-load ir_result_wf.wav + ir_result_tw.wav
    python dsp_gui.py --no-load       # open empty
    python dsp_gui.py --opengl        # GPU line rendering (needs pyopengl)
    python dsp_gui.py --data-dir DIR  # look for the measurement wavs in DIR
    python dsp_gui.py --export-dir DIR  # write every exported header flat into DIR

The measurement wavs are looked up in the data folder (NF_DATA_DIR in config/local.env,
default python_dsp_tools/data), then in python_dsp_tools/examples. "Export C++ headers"
writes each header straight into the plugin that compiles it (see EXPORT_TARGETS).

Needs only the DSP core (numpy + scipy) plus pyqtgraph/Qt; no audio dependencies.
"""

import os
import sys
import argparse
from pathlib import Path

import numpy as np
from scipy import signal
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets

from nearfield import config, lib
from nearfield.lib import (
    compute_magnitude_phase,
    create_crossover_filters,
    inverse_gd_filter,
    write_cpp_float_arrays_to_header,
)

# Which plugin compiles each exported header: "Export C++ headers" writes the file into
# <export dir>/<plugin>/ (export dir = NF_EXPORT_DIR, default juce_plugins/), so a rebuild of
# that plugin picks the new coefficients up with no copying. With --export-dir every header
# is written flat into the given folder instead.
EXPORT_TARGETS = {
    "crossover_coefs.hpp": "crossover",
    "filters.hpp": "firConv",
}

pg.setConfigOption("background", "#1e1e1e")
pg.setConfigOption("foreground", "#c8c8c8")
pg.setConfigOptions(antialias=True)


# ---------------------------------------------------------------------------------------
# Log-frequency axis with plain audio labels (mirrors near_field_gui.AudioLogAxis; kept
# local so this GUI stands alone and editing one GUI can't break the other).
# ---------------------------------------------------------------------------------------

FREQ_LABEL_MANTISSAS = (1, 2, 3, 5)
FREQ_LABEL_MIN = 10.0
FREQ_LABEL_MAX = 40000.0


def _fmt_freq(f):
    f = round(f)
    if f < 1000:
        return f"{int(f)}"
    k = f / 1000.0
    return f"{int(k)}k" if k == int(k) else f"{k:g}k"


class AudioLogAxis(pg.AxisItem):
    """Log frequency axis with plain audio labels (10/20/.../1k/20k)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.enableAutoSIPrefix(False)

    def logTickValues(self, minVal, maxVal, size, stdTicks):
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
# Sources -- two freely-loaded IRs -> Hlf (woofer) and Hhf (tweeter)
# ---------------------------------------------------------------------------------------

# The two responses this GUI consumes. `wav` is the primary auto-load / file-dialog default,
# `wav_fallbacks` are tried next on auto-load; the user can load any IR. `peak` is the
# phase-reference mode (see compute_magnitude_phase). The IRs are used as loaded (no time
# gating), so the tweeter defaults to the *windowed* result (ir_result_tw.wav) the measurement
# GUI saves -- the raw ir_ff_tw.wav still carries room reflections + the deconvolution noise
# tail (which would masquerade as HF content without a gate).
SOURCE_SLOTS = {
    "woofer":  dict(label="Woofer",  wav="ir_result_wf.wav", peak="max"),
    "tweeter": dict(label="Tweeter", wav="ir_result_tw.wav", wav_fallbacks=["ir_ff_tw.wav"], peak="max"),
}


def choose_nfft(needed_lengths):
    """Shared FFT length: next power of two >= the longest source need, clamped to
    [2^15, 2^18]. One common length gives both sources the same frequency vector, so their
    transfer functions can be summed by the crossover."""
    target = max(max(needed_lengths, default=2), 2)
    n = 1 << int(np.ceil(np.log2(target)))
    return int(np.clip(n, 1 << 15, 1 << 18))


def source_fft(ir, n_fft):
    """Turn one source IR into a length-`n_fft` real signal ready for compute_magnitude_phase:
    the whole IR as loaded, cropped/padded to n_fft (phase is peak-referenced later)."""
    seg = np.asarray(ir, dtype=float)
    if len(seg) == 0:
        return None
    out = np.zeros(n_fft)
    m = min(len(seg), n_fft)
    out[:m] = seg[:m]
    return out


def build_sources(raw, sr, params):
    """Build (freqs, Hlf, Hhf, norm_factor) from the two loaded IRs (used as-is, no gating).
    The woofer is normalised to 0 dB @ 1 kHz; the SAME normalisation gain (norm_factor) is then
    applied to the tweeter, so the woofer/tweeter level relationship is preserved (a -3 dB
    woofer normalisation shifts the tweeter -3 dB too). Returns None if either source is missing.
    """
    woof, tw = raw.get("woofer"), raw.get("tweeter")
    if woof is None or tw is None:
        return None

    pw, pt = params["woofer"], params["tweeter"]
    n_fft = choose_nfft([len(woof), len(tw)])
    smoothing = params["smoothing"]

    w_woof = source_fft(woof, n_fft)
    w_tw = source_fft(tw, n_fft)

    # Woofer -> 0 dB @ 1 kHz (normalize=0 returns the gain it applied); the tweeter is then
    # scaled by that same gain (normalize=norm_factor) so relative level is preserved.
    # Mirrors analysis.py / near_field_gui.py.
    fr, mag_w, ph_w, norm_factor = compute_magnitude_phase(
        w_woof, sr, mode=pw["peak"], smoothing=smoothing, normalize=0)
    _, mag_t, ph_t = compute_magnitude_phase(
        w_tw, sr, mode=pt["peak"], smoothing=smoothing, normalize=norm_factor)

    Hlf = mag_w * np.exp(1j * ph_w)
    Hhf = mag_t * np.exp(1j * ph_t)
    return fr, Hlf, Hhf, norm_factor


# ---------------------------------------------------------------------------------------
# Module framework
# ---------------------------------------------------------------------------------------

class ModuleContext:
    """Everything a processing module needs: the two source responses on a shared frequency
    grid. `norm_factor` is the woofer's 0 dB @ 1 kHz normalisation gain (also applied to the
    tweeter), exposed for modules that want the level reference."""

    def __init__(self, freqs, sr, Hlf, Hhf, norm_factor):
        self.freqs = freqs
        self.sr = sr
        self.Hlf = Hlf
        self.Hhf = Hhf
        self.norm_factor = norm_factor


class ModuleResult:
    """What a module returns from process(): the curves to draw, exportable FIR/IIR tap
    arrays, and a few scalar readouts for the status line."""

    def __init__(self):
        self.curves = {}     # curve_key -> (freqs, complex H)
        self.exports = []    # list of (filename, [array, ...], [name, ...])
        self.info = {}       # label -> value (shown in the readout line)
        self.error = None    # str; set to report a design failure


class ProcessingModule(QtCore.QObject):
    """Base class for a filter-design/analysis module.

    Subclasses:
      * set `display_name`,
      * build their control panel in `build_panel()` (wiring every control to emit
        `self.changed` so the GUI recomputes),
      * declare the curves they draw in `curve_specs()`,
      * compute those curves in `process(ctx)`.
    Everything else (plots, toggles, source loading, export) is shared by the GUI.
    """

    display_name = "Module"
    changed = QtCore.Signal()

    def build_panel(self):
        raise NotImplementedError

    def curve_specs(self):
        """Return list of dicts: key, label, color, width(optional), dashed(optional),
        default_on(optional). The GUI creates one toggleable curve per entry, in all three
        (magnitude/phase/group-delay) plots."""
        raise NotImplementedError

    def process(self, ctx):
        raise NotImplementedError


# ---------------------------------------------------------------------------------------
# Two-way crossover module
# ---------------------------------------------------------------------------------------

class CrossoverModule(ProcessingModule):
    """Interactive version of lib.process_2_way_speaker.

    Splits the woofer (Hlf) and tweeter (Hhf) with a linear-phase Parks-McClellan crossover,
    time-aligns the HF band, sums the two bands, then optionally flattens the summed response
    with a least-squares magnitude-inverse filter and a group-delay-inverse filter -- exactly
    the chain used to generate crossover_coefs.hpp and filters.hpp, but live.
    """

    display_name = "Two-way crossover"

    # Curve palette. keys must match what process() fills in.
    CURVES = [
        dict(key="Hlf",          label="Woofer",                  color="#4C9BE8", width=1.8, default_on=True),
        dict(key="Hhf",          label="Tweeter",                 color="#5FD08C", width=1.8, default_on=True),
        dict(key="Hlp",          label="Crossover LP",            color="#3B6FA0", dashed=True),
        dict(key="Hhp",          label="Crossover HP",            color="#3E8E63", dashed=True),
        dict(key="Hcross_total", label="Crossover LP+HP",         color="#8892A0", dashed=True),
        dict(key="Hlf_proc",     label="Woofer processed",        color="#7FB2E0"),
        dict(key="Hhf_proc",     label="Tweeter processed",       color="#8FD8AE"),
        dict(key="Houtput_raw",  label="Sum (no correction)",     color="#E8954C", width=1.8, default_on=True),
        dict(key="Hmag_inv",     label="Magnitude correction",    color="#D65DB1", dashed=True),
        dict(key="Hmag_bandlimit", label="Correction band-limit", color="#B5A0E0", dashed=True),
        dict(key="Hgd_inv",      label="Group-delay correction",  color="#C0A33C", dashed=True),
        dict(key="Houtput",      label="Corrected output",        color="#F2D34B", width=2.6, default_on=True),
    ]

    def __init__(self):
        super().__init__()
        # taps from the last successful design, for C++ export
        self._taps = None
        self._detected = (None, 0.0)   # (earlier_band, delay_us) from the last detection

    # -- controls -----------------------------------------------------------------------

    def build_panel(self):
        panel = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(panel)
        v.setContentsMargins(0, 0, 0, 0)

        # Crossover
        xo = QtWidgets.QGroupBox("Crossover (linear-phase, Parks-McClellan)")
        xf = QtWidgets.QFormLayout(xo)
        self.fc = self._spin(2000, 50, 20000)
        self.tw = self._spin(1000, 10, 20000)
        self.taps = self._spin(201, 3, 2001)   # odd -> linear phase
        xf.addRow("Crossover (Hz)", self.fc)
        xf.addRow("Transition width (Hz)", self.tw)
        xf.addRow("Taps", self.taps)
        v.addWidget(xo)

        # Band alignment (per-band delay) + tweeter level.
        al = QtWidgets.QGroupBox("Alignment && level")
        af = QtWidgets.QFormLayout(al)
        # Independent delays applied AFTER the crossover LP/HP, one per band. Only non-negative
        # (a delay can lag a band, not advance it) -- delay whichever band arrives *earlier*.
        self.woofer_delay = self._dspin(0.0, 0.0, 20000.0, 1)
        self.tweeter_delay = self._dspin(0.0, 0.0, 20000.0, 1)
        # Detection readout: which band leads (arrives earlier) and by how much, measured by
        # cross-correlating the two crossover-band impulse responses (current delays included).
        self.align_readout = QtWidgets.QLabel("-")
        self.align_readout.setStyleSheet("color: #B07CC6;")
        self.align_readout.setWordWrap(True)
        self.apply_align_btn = QtWidgets.QPushButton("Apply detected delay to earlier band")
        # Fine-tune of the tweeter level on top of the woofer-coupled normalisation done in
        # build_sources. Leave at 1.0 to keep the measured relationship.
        self.hf_gain = self._dspin(1.0, 0.0, 100.0, 3)
        af.addRow("Woofer delay (µs)", self.woofer_delay)
        af.addRow("Tweeter delay (µs)", self.tweeter_delay)
        af.addRow("Detected", self.align_readout)
        af.addRow(self.apply_align_btn)
        af.addRow("HF gain (×)", self.hf_gain)
        v.addWidget(al)

        # Magnitude correction
        self.mag_box = QtWidgets.QGroupBox("Magnitude correction (firls inverse)")
        self.mag_box.setCheckable(True); self.mag_box.setChecked(True)
        mf = QtWidgets.QFormLayout(self.mag_box)
        self.mag_taps = self._spin(411, 3, 2001)
        self.design_pts = self._spin(4096, 256, 32768)
        mf.addRow("Taps", self.mag_taps)
        mf.addRow("Design points", self.design_pts)
        # Band-limiting pre-filters, applied on top of the firls inverse exactly as in
        # lib.firls_inverse_magnitude_filter (Hinv *= Hpre_lp * Hpre_hp): a remez low-pass
        # rolls the correction off at HF and a Butterworth high-pass rolls it off at LF, so the
        # inverse only acts across the driver's usable band. Both are exported to filters.hpp
        # (HPreLpTaps for the FIR low-pass; HPreHpSos -- biquad second-order sections -- for the
        # high-pass, which the C++ plugin runs as an IIR cascade) and applied there as a cascade.
        # Defaults reproduce the original hardcoded filters (61-tap LP @15k/6.5k, 4th-order HP @25 Hz).
        self.lp_taps = self._spin(61, 3, 1001)
        self.lp_pass = self._spin(15000, 500, 40000)
        self.lp_trans = self._spin(6500, 100, 20000)
        self.hp_order = self._spin(4, 1, 8)
        self.hp_cut = self._dspin(25.0, 1.0, 2000.0, 1)
        mf.addRow(QtWidgets.QLabel("HF limit — low-pass (remez FIR):"))
        mf.addRow("LP taps", self.lp_taps)
        mf.addRow("LP passband (Hz)", self.lp_pass)
        mf.addRow("LP transition (Hz)", self.lp_trans)
        mf.addRow(QtWidgets.QLabel("LF limit — high-pass (Butterworth IIR):"))
        mf.addRow("HP order", self.hp_order)
        mf.addRow("HP cutoff (Hz)", self.hp_cut)
        v.addWidget(self.mag_box)

        # Group-delay correction
        self.gd_box = QtWidgets.QGroupBox("Group-delay correction (phase inverse)")
        self.gd_box.setCheckable(True); self.gd_box.setChecked(True)
        gf = QtWidgets.QFormLayout(self.gd_box)
        gf.addRow(QtWidgets.QLabel("Flattens the summed group delay\n(magnitude untouched)."))
        v.addWidget(self.gd_box)

        # Wire everything to a single change signal.
        for w in (self.fc, self.tw, self.taps, self.woofer_delay, self.tweeter_delay,
                  self.hf_gain, self.mag_taps, self.design_pts,
                  self.lp_taps, self.lp_pass, self.lp_trans, self.hp_order, self.hp_cut):
            w.valueChanged.connect(self.changed)
        self.mag_box.toggled.connect(self.changed)
        self.gd_box.toggled.connect(self.changed)
        self.apply_align_btn.clicked.connect(self._apply_detected)
        return panel

    def curve_specs(self):
        return self.CURVES

    # -- design -------------------------------------------------------------------------

    def process(self, ctx):
        res = ModuleResult()
        freqs, sr = ctx.freqs, ctx.sr
        Hlf, Hhf = ctx.Hlf, ctx.Hhf
        n = len(freqs)

        try:
            fc = self.fc.value()
            trans = self.tw.value()
            ntaps = self._odd(self.taps.value())
            # Parks-McClellan crossover (same call lib.create_crossover_filters makes).
            lp, hp = create_crossover_filters(fc, sr, ntaps, trans, export_coeffs_to_cpp=False)
        except Exception as exc:  # noqa: BLE001 -- bad band edges etc.: report, don't crash
            res.error = f"Crossover design failed: {exc}"
            return res

        _, Hlp = signal.freqz(lp, [1], worN=n, fs=sr)
        _, Hhp = signal.freqz(hp, [1], worN=n, fs=sr)
        Hcross_total = Hlp + Hhp

        # Independent per-band delays. Applied to each response; the crossover LP/HP is then
        # applied on top.
        Hdelay_w = self._delay_response(self.woofer_delay.value(), freqs)
        Hdelay_t = self._delay_response(self.tweeter_delay.value(), freqs)
        w_delayed = Hlf * Hdelay_w
        t_delayed = Hhf * Hdelay_t
        Hlf_proc = w_delayed * Hlp                    # woofer band (post-LP, delay applied)
        t_band = t_delayed * Hhp                       # tweeter band (post-HP, delay applied), pre-gain
        Hhf_proc = t_band * self.hf_gain.value()
        Houtput_raw = Hlf_proc + Hhf_proc

        # Detect the extra per-band delay that best aligns the two crossover bands (max coherence
        # of their sum through the crossover region). This is measured on the *post-crossover*
        # bands, because the LP/HP filters add phase in the transition region that -- unlike the
        # raw driver phase alone -- is part of what makes the summed response align. Symmetric:
        # either band can be the earlier one.
        earlier, rel_us = self._detect_relative_delay(Hlf_proc, t_band, freqs, sr, fc)
        self._detected = (earlier, rel_us)
        if earlier is None:
            self.align_readout.setText("aligned (< 1 µs)")
        else:
            later = "tweeter" if earlier == "woofer" else "woofer"
            # You can only delay (push later), so delay whichever arrives EARLIER to wait for
            # the other -- delaying the later band would only deepen the crossover dip.
            self.align_readout.setText(
                f"{earlier} arrives {rel_us:.0f} µs before {later} → delay {earlier}")

        res.curves.update(
            Hlf=(freqs, Hlf), Hhf=(freqs, Hhf),
            Hlp=(freqs, Hlp), Hhp=(freqs, Hhp), Hcross_total=(freqs, Hcross_total),
            Hlf_proc=(freqs, Hlf_proc), Hhf_proc=(freqs, Hhf_proc),
            Houtput_raw=(freqs, Houtput_raw),
        )

        Houtput = Houtput_raw
        taps = dict(lp=lp, hp=hp)

        # Magnitude correction (least-squares FIR inverse of |Houtput|), designed on a
        # coarse grid for speed and evaluated on the full display grid.
        if self.mag_box.isChecked():
            try:
                inv_b, Hmag_inv, pre, Hband = self._design_mag_inverse(
                    Houtput, freqs, sr, self._odd(self.mag_taps.value()), self.design_pts.value(),
                    self.lp_taps.value(), self.lp_pass.value(), self.lp_trans.value(),
                    self.hp_order.value(), self.hp_cut.value())
            except Exception as exc:  # noqa: BLE001 -- bad prefilter edges etc.: report, keep last plot
                res.error = f"Magnitude correction failed: {exc}"
                return res
            Houtput = Houtput * Hmag_inv
            res.curves["Hmag_inv"] = (freqs, Hmag_inv)
            res.curves["Hmag_bandlimit"] = (freqs, Hband)
            taps.update(InvFilter=inv_b, HPreHpNum=pre[0], HPreHpDen=pre[1],
                        HPreLpTaps=pre[2], HPreHpSos=pre[3])

        # Group-delay correction: flatten the (post-magnitude) phase, leaving |H| alone.
        if self.gd_box.isChecked():
            Hgd_inv, _ = inverse_gd_filter(Houtput, freqs)
            Houtput = Houtput * Hgd_inv
            res.curves["Hgd_inv"] = (freqs, Hgd_inv)

        res.curves["Houtput"] = (freqs, Houtput)
        self._taps = taps

        res.info["delay w/t"] = f"{self.woofer_delay.value():.0f}/{self.tweeter_delay.value():.0f} µs"
        res.exports = self._export_list()
        return res

    @staticmethod
    def _delay_response(delay_us, freqs):
        """Frequency response of a pure (fractional) delay of `delay_us` microseconds: a linear
        phase ramp exp(-j·2π·f·τ). Exact to sub-sample -- used only for the alignment
        simulation/display (the delay is not part of the exported filters). 0 µs -> flat."""
        return np.exp(-1j * 2.0 * np.pi * np.asarray(freqs) * (delay_us / 1e6))

    @staticmethod
    def _detect_relative_delay(A_band, B_band, freqs, sr, fc):
        """Find the extra relative delay that best time-aligns the two crossover bands, by
        maximising the coherence of their sum over the crossover region. `A_band` is the woofer
        after the LP, `B_band` the tweeter after the HP (both with any delays already applied,
        pre-gain). A trial delay is swept on the tweeter band; the value that maximises the
        (overlap-weighted) summed magnitude is the alignment. Returns (earlier_band, delay_us):
        the band that arrives earlier -- delay it -- or (None, 0.0) if already aligned to ~1 us.

        This works on the *post-crossover* bands on purpose: the LP/HP add phase in the
        transition region that is part of the aligned sum, so the raw pre-crossover driver phase
        alone gives the wrong answer. It directly optimises what you'd do by hand (delay a band
        until the summed response lines up), so it is robust to phase wrapping / driver shape."""
        freqs = np.asarray(freqs)
        band = (freqs >= fc / 2.0) & (freqs <= min(fc * 2.0, sr / 2.0 - 1.0))
        nb = int(band.sum())
        if nb < 8:
            return None, 0.0
        idx = np.linspace(0, nb - 1, min(512, nb)).astype(int)   # downsample for a fast scan
        om = (2.0 * np.pi * freqs[band])[idx]
        a, b = A_band[band][idx], B_band[band][idx]
        wt = np.abs(a) * np.abs(b)                               # weight where both have output
        if wt.sum() <= 0:
            return None, 0.0
        # sweep a trial delay on the tweeter band over ~+/-1.2 periods of the crossover; the
        # broadband-flattest alignment wins (its near-period neighbours only align at fc).
        period_us = 1e6 / max(fc, 1.0)
        span = 1.2 * period_us
        deltas = np.linspace(-span, span, 1200) * 1e-6
        S = a[:, None] + b[:, None] * np.exp(-1j * np.outer(om, deltas))
        score = (wt[:, None] * np.abs(S)).sum(0)
        k = int(np.argmax(score))
        d = deltas[k]
        if 0 < k < len(deltas) - 1:                              # sub-step parabolic refine
            y0, y1, y2 = score[k - 1], score[k], score[k + 1]
            den = y0 - 2.0 * y1 + y2
            if den != 0:
                d = deltas[k] + 0.5 * (y0 - y2) / den * (deltas[1] - deltas[0])
        delay_us = abs(d) * 1e6
        if delay_us < 1.0:
            return None, 0.0
        # d > 0 => delaying the tweeter aligns => the tweeter arrives earlier; else the woofer.
        earlier = "tweeter" if d > 0 else "woofer"
        return earlier, delay_us

    def _apply_detected(self):
        """Add the detected relative delay to whichever band leads, converging toward alignment."""
        earlier, rel_us = getattr(self, "_detected", (None, 0.0))
        if earlier is None:
            return
        spin = self.woofer_delay if earlier == "woofer" else self.tweeter_delay
        spin.setValue(spin.value() + rel_us)   # valueChanged -> changed -> reprocess

    def _design_mag_inverse(self, Houtput, freqs, sr, numtaps, design_pts,
                            lp_taps, lp_pass, lp_trans, hp_order, hp_cut):
        """Reproduce lib.firls_inverse_magnitude_filter, but design on a coarse grid
        (`design_pts` frequency points) and evaluate on the full `freqs` grid so the live
        recompute stays fast even when the display FFT is large.

        The firls inverse is band-limited by two pre-filters, applied by multiplying its
        response (Hinv *= Hpre_lp * Hpre_hp), exactly as lib does. Both are parameterised here:
          * a remez low-pass -- `lp_taps` taps, passband edge `lp_pass` Hz, transition `lp_trans`
            Hz -- rolls the correction off at HF;
          * a `hp_order`-order Butterworth high-pass at `hp_cut` Hz rolls it off at LF.
        The defaults (61 / 15000 / 6500 / 4 / 25) reproduce lib's original hardcoded filters.

        Returns (inv_b taps, Hmag_inv on freqs, (hp_num, hp_den, prelp_taps, hp_sos),
        Hbandlimit on freqs). hp_sos is the biquad-cascade form of the high-pass for export.
        """
        nyq = 0.5 * sr

        # Band-limiting pre-filters (parameterised; same structure lib uses). Validate the band
        # edges so a transient bad value while dragging is reported, not raised into a crash.
        lp_stop = lp_pass + lp_trans
        if not (0 < lp_pass < lp_stop < nyq):
            raise ValueError(
                f"LP band edges need 0 < passband ({lp_pass:.0f}) < "
                f"passband+transition ({lp_stop:.0f}) < Nyquist ({nyq:.0f} Hz)")
        if not (0 < hp_cut < nyq):
            raise ValueError(f"HP cutoff {hp_cut:.0f} Hz must be < Nyquist ({nyq:.0f} Hz)")
        prelp_taps = signal.remez(int(lp_taps), [0, lp_pass, lp_stop, nyq], [1, 0], fs=sr)
        hp_num, hp_den = signal.butter(int(hp_order), hp_cut / nyq, btype="highpass", analog=False)
        # Second-order-section form of the same high-pass. The exported b/a is float32-unstable
        # in the plugin at low cutoffs (poles near z=1 -> the direct-form recursion overflows),
        # so the plugin runs the high-pass as a biquad cascade -- export SOS for it to consume.
        hp_sos = signal.butter(int(hp_order), hp_cut / nyq, btype="highpass", output="sos", analog=False)

        # Coarse, even-length design grid (firls consumes consecutive freq pairs as bands).
        m = int(design_pts) & ~1
        fd = np.linspace(0.0, nyq, m)
        Hd_mag = np.interp(fd, freqs, np.abs(Houtput))
        desired = np.abs(1.0 / (Hd_mag + 1e-12))

        # Per-band weights (same schedule as lib): kill the sub-40 Hz inverse, favour 40-200 Hz.
        weights = []
        for i in range(len(fd) // 2):
            center = 0.5 * (fd[2 * i] + fd[2 * i + 1])
            if center < 40.0:
                weights.append(1e-6)
            elif center <= 200.0:
                weights.append(1.0)
            else:
                weights.append(0.3)

        inv_b = signal.firls(numtaps, fd, desired, weight=weights, fs=sr)

        _, Hinv = signal.freqz(inv_b, [1], worN=len(freqs), fs=sr)
        _, Hprelp = signal.freqz(prelp_taps, [1], worN=len(freqs), fs=sr)
        _, Hprehp = signal.freqz(hp_num, hp_den, worN=len(freqs), fs=sr)
        Hband = Hprelp * Hprehp                 # the band-limit envelope (toggleable curve)
        Hmag_inv = Hinv * Hband
        return inv_b, Hmag_inv, (hp_num, hp_den, prelp_taps, hp_sos), Hband

    def _export_list(self):
        """Header files to write, matching the names lib emits."""
        if not self._taps:
            return []
        exports = [("crossover_coefs.hpp", [self._taps["lp"], self._taps["hp"]], ["lp", "hp"])]
        if "InvFilter" in self._taps:
            # HPreHpSos is (nSections, 6); flatten to a single float array [b0,b1,b2,a0,a1,a2,...]
            # so the writer emits one C array. The plugin reads it back 6 floats per biquad.
            hp_sos_flat = np.asarray(self._taps["HPreHpSos"]).reshape(-1)
            exports.append((
                "filters.hpp",
                [self._taps["InvFilter"], self._taps["HPreHpNum"], self._taps["HPreHpDen"],
                 hp_sos_flat, self._taps["HPreLpTaps"]],
                ["InvFilter", "HPreHpNum", "HPreHpDen", "HPreHpSos", "HPreLpTaps"],
            ))
        return exports

    # -- helpers ------------------------------------------------------------------------

    @staticmethod
    def _odd(n):
        n = int(n)
        return n if n % 2 == 1 else n + 1

    @staticmethod
    def _spin(val, lo, hi):
        w = QtWidgets.QSpinBox(); w.setRange(int(lo), int(hi)); w.setValue(int(val)); return w

    @staticmethod
    def _dspin(val, lo, hi, dec):
        w = QtWidgets.QDoubleSpinBox(); w.setDecimals(dec)
        w.setRange(lo, hi); w.setSingleStep(10 ** -dec if dec else 1); w.setValue(val)
        return w


# Registry of available modules. Add a class here to expose a new filter-analysis module.
MODULES = {
    "crossover": CrossoverModule,
}


# ---------------------------------------------------------------------------------------
# Three stacked response plots (magnitude / phase / group delay), one signal = 3 curves
# ---------------------------------------------------------------------------------------

class TripleResponsePlot(QtWidgets.QWidget):
    """Magnitude (dB), phase (deg) and group-delay (ms) plots sharing a log-frequency x
    axis. Each named signal owns one curve in each plot; visibility is toggled per signal."""

    def __init__(self):
        super().__init__()
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)

        self.mag = self._plot("Magnitude", "Magnitude (dB)")
        self.phase = self._plot("Phase", "Phase (deg)")
        self.gd = self._plot("Group delay", "Group delay (ms)")
        self.phase.setXLink(self.mag)
        self.gd.setXLink(self.mag)
        for p in (self.mag, self.phase, self.gd):
            p.setMinimumHeight(150)
            p.getViewBox().enableAutoRange(x=False, y=True)
        v.addWidget(self.mag, 1)
        v.addWidget(self.phase, 1)
        v.addWidget(self.gd, 1)

        self._curves = {}   # key -> dict(mag, phase, gd)
        self._unwrap = False

    def _plot(self, title, ylabel):
        p = pg.PlotWidget(axisItems={"bottom": AudioLogAxis(orientation="bottom")})
        p.setTitle(title)
        p.showGrid(x=True, y=True, alpha=0.3)
        p.setLabel("bottom", "Frequency (Hz)")
        p.setLabel("left", ylabel)
        p.setLogMode(x=True, y=False)
        p.setDownsampling(auto=True, mode="peak")
        p.setClipToView(True)
        return p

    def set_unwrap(self, on):
        self._unwrap = bool(on)

    def add_signal(self, key, label, color, width=1.6, dashed=False):
        style = QtCore.Qt.DashLine if dashed else QtCore.Qt.SolidLine
        pen = pg.mkPen(color=color, width=width, style=style)
        self._curves[key] = dict(
            mag=self.mag.plot([], [], pen=pen, name=label),
            phase=self.phase.plot([], [], pen=pen, name=label),
            gd=self.gd.plot([], [], pen=pg.mkPen(color=color, width=width, style=style)),
        )

    def set_signal(self, key, freqs, H):
        freqs = np.asarray(freqs)
        H = np.asarray(H)
        sel = freqs > 0
        f = freqs[sel]
        H = H[sel]
        mag_db = 20.0 * np.log10(np.maximum(np.abs(H), 1e-12))
        ang = np.unwrap(np.angle(H))
        phase_deg = np.degrees(ang if self._unwrap else np.angle(H))
        # group delay = -d(phase)/d(omega); omega = 2*pi*f
        gd_ms = -np.gradient(ang, 2.0 * np.pi * f) * 1000.0
        c = self._curves[key]
        c["mag"].setData(f, mag_db)
        c["phase"].setData(f, phase_deg)
        c["gd"].setData(f, gd_ms)

    def clear_signal(self, key):
        c = self._curves[key]
        for name in ("mag", "phase", "gd"):
            c[name].setData([], [])

    def set_visible(self, key, on):
        c = self._curves[key]
        for name in ("mag", "phase", "gd"):
            c[name].setVisible(on)

    def refresh_phase_gd(self, results):
        """Re-render phase/GD for all currently-set signals (used when the unwrap toggle
        flips). `results` maps key -> (freqs, H)."""
        for key, (freqs, H) in results.items():
            self.set_signal(key, freqs, H)


# ---------------------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------------------

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.export_dir_override = None     # set from --export-dir; see _export_path
        self.setWindowTitle("DSP analysis & filter design")
        self.resize(1500, 950)
        self.setMinimumSize(1150, 700)

        self.raw = {k: None for k in SOURCE_SLOTS}
        self.sr = 48000
        self._ready = False
        self._sources_dirty = True
        self._ctx = None
        self._last_results = {}     # key -> (freqs, H) from last process(), for unwrap redraw

        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(140)
        self._timer.timeout.connect(self.recompute)

        # Active module.
        self.module = CrossoverModule()
        self.module.changed.connect(self._schedule)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QHBoxLayout(central)

        controls_scroll = QtWidgets.QScrollArea()
        controls_scroll.setWidget(self._build_controls())
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setFixedWidth(360)
        controls_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        controls_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        root.addWidget(controls_scroll)
        root.addWidget(self._build_plots(), 1)

        self.statusBar().showMessage("Ready. Load the woofer/tweeter measurement wavs.")
        self._ready = True
        self._apply_freq_view()

    # -- controls -----------------------------------------------------------------------

    def _build_controls(self):
        panel = QtWidgets.QWidget()
        panel.setFixedWidth(338)
        v = QtWidgets.QVBoxLayout(panel)

        # Sources: two freely-loaded IRs (woofer + tweeter), used as loaded (no time gating).
        src_box = QtWidgets.QGroupBox("Input responses")
        sv = QtWidgets.QVBoxLayout(src_box)
        self.src_status = {}
        self.src_win = {}
        for key, cfg in SOURCE_SLOTS.items():
            gb = QtWidgets.QGroupBox(cfg["label"])
            g = QtWidgets.QVBoxLayout(gb)
            status = QtWidgets.QLabel("empty"); status.setStyleSheet("color:#888;")
            g.addWidget(status)
            load = QtWidgets.QPushButton("Load wav")
            load.clicked.connect(lambda _, k=key: self.load_slot(k))
            g.addWidget(load)

            peak = QtWidgets.QComboBox(); peak.addItems(["max", "min", "abs"])
            peak.setCurrentText(cfg["peak"])
            prow = QtWidgets.QFormLayout(); prow.addRow("Peak mode", peak)
            g.addLayout(prow)

            peak.currentIndexChanged.connect(self._sources_changed)
            sv.addWidget(gb)
            self.src_status[key] = status
            self.src_win[key] = dict(peak=peak)
        v.addWidget(src_box)

        # Analysis parameters (shared by both sources).
        rec_box = QtWidgets.QGroupBox("Analysis")
        rf = QtWidgets.QFormLayout(rec_box)
        self.smooth_spin = self._spin(6, 0, 48)
        self.smooth_spin.valueChanged.connect(self._sources_changed)
        rf.addRow("Smoothing (1/n oct)", self.smooth_spin)
        v.addWidget(rec_box)

        # Module selector (only crossover for now).
        mod_box = QtWidgets.QGroupBox("Filter module")
        mvl = QtWidgets.QFormLayout(mod_box)
        self.module_combo = QtWidgets.QComboBox()
        self.module_combo.addItem(self.module.display_name, "crossover")
        self.module_combo.setEnabled(len(MODULES) > 1)
        mvl.addRow("Module", self.module_combo)
        v.addWidget(mod_box)

        # Module's own controls.
        self.module_panel = self.module.build_panel()
        v.addWidget(self.module_panel)

        # Frequency view.
        fview = QtWidgets.QGroupBox("Frequency view (Hz)")
        ff = QtWidgets.QFormLayout(fview)
        self.fmin_spin = self._spin(20, 1, 40000)
        self.fmax_spin = self._spin(20000, 2, 96000)
        self.unwrap_cb = QtWidgets.QCheckBox("Unwrap phase")
        self.fmin_spin.valueChanged.connect(self._apply_freq_view)
        self.fmax_spin.valueChanged.connect(self._apply_freq_view)
        self.unwrap_cb.toggled.connect(self._toggle_unwrap)
        ff.addRow("View min", self.fmin_spin)
        ff.addRow("View max", self.fmax_spin)
        ff.addRow(self.unwrap_cb)
        v.addWidget(fview)

        # Actions.
        self.export_btn = QtWidgets.QPushButton("Export C++ headers")
        self.export_btn.clicked.connect(self.export_headers)
        v.addWidget(self.export_btn)

        self.readout = QtWidgets.QLabel("")
        self.readout.setWordWrap(True)
        self.readout.setStyleSheet("color:#9fb;")
        v.addWidget(self.readout)

        v.addStretch()
        return panel

    def _build_plots(self):
        container = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)

        # Curve toggle bar (acts as the legend) -- rebuilt when the module changes.
        self.curve_bar = QtWidgets.QWidget()
        self.curve_bar_layout = QtWidgets.QHBoxLayout(self.curve_bar)
        self.curve_bar_layout.setContentsMargins(6, 2, 6, 2)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidget(self.curve_bar)
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(42)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        self.plots = TripleResponsePlot()
        outer.addWidget(self.plots, 1)

        self._install_module_curves()
        return container

    def _install_module_curves(self):
        """(Re)build the curve set + toggle checkboxes from the active module's curve_specs."""
        # clear old checkboxes
        while self.curve_bar_layout.count():
            item = self.curve_bar_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.curve_bar_layout.addStretch()

        self._curve_visible = {}
        for spec in self.module.curve_specs():
            key = spec["key"]
            self.plots.add_signal(
                key, spec["label"], spec["color"],
                width=spec.get("width", 1.6), dashed=spec.get("dashed", False),
            )
            on = spec.get("default_on", False)
            self._curve_visible[key] = on
            self.plots.set_visible(key, on)
            cb = QtWidgets.QCheckBox(spec["label"])
            cb.setChecked(on)
            cb.setStyleSheet(f"color: {spec['color']};")
            cb.toggled.connect(lambda v, k=key: self._toggle_curve(k, v))
            self.curve_bar_layout.insertWidget(self.curve_bar_layout.count() - 1, cb)

    def _toggle_curve(self, key, on):
        self._curve_visible[key] = on
        self.plots.set_visible(key, on)

    def _toggle_unwrap(self, on):
        self.plots.set_unwrap(on)
        if self._last_results:
            self.plots.refresh_phase_gd(self._last_results)

    def _apply_freq_view(self):
        if getattr(self, "plots", None) is None:
            return
        fmin = float(self.fmin_spin.value())
        fmax = float(self.fmax_spin.value())
        if fmax <= fmin:
            fmax = fmin + 1.0
        lo, hi = np.log10(fmin), np.log10(fmax)
        for p in (self.plots.mag, self.plots.phase, self.plots.gd):
            p.getViewBox().setLimits(xMin=lo, xMax=hi)
        self.plots.mag.setXRange(lo, hi, padding=0)

    # -- source loading -----------------------------------------------------------------

    def load_slot(self, key):
        default = str(config.data_path(SOURCE_SLOTS[key]["wav"]))
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, f"Load {SOURCE_SLOTS[key]['label']} wav", default, "WAV files (*.wav)")
        if not path:
            return
        if self._load_wav_into(key, path):
            self._sources_changed()

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
        self.sr = int(sr)
        self.src_status[key].setText(f"loaded {sr} Hz ({len(data)} samples)")
        self.src_status[key].setStyleSheet("color:#5FD08C;")
        return True

    def load_existing(self):
        """On startup, auto-load the default wavs if present: ir_result_wf.wav as the woofer
        and ir_result_tw.wav (falling back to ir_ff_tw.wav) as the tweeter -- each looked up in
        the data folder, then in the bundled examples."""
        loaded = []
        for key, cfg in SOURCE_SLOTS.items():
            path = config.find_data_file([cfg["wav"]] + cfg.get("wav_fallbacks", []))
            if path and self._load_wav_into(key, str(path)):
                loaded.append(f"{cfg['label']} ({os.path.basename(path)})")
        if loaded:
            self.statusBar().showMessage("Auto-loaded: " + ", ".join(loaded))
        self._sources_dirty = True
        self.recompute()

    # -- recompute ----------------------------------------------------------------------

    def _sources_changed(self):
        self._sources_dirty = True
        self._schedule()

    def _schedule(self):
        if self._ready:
            self._timer.start()

    def _source_params(self):
        p = dict(smoothing=self.smooth_spin.value())
        for key in SOURCE_SLOTS:
            p[key] = dict(peak=self.src_win[key]["peak"].currentText())
        return p

    def recompute(self):
        if not self._ready:
            return
        if self._sources_dirty:
            recon = build_sources(self.raw, self.sr, self._source_params())
            if recon is None:
                self._ctx = None
                self.statusBar().showMessage(
                    "Load both responses (woofer + tweeter) to design filters.")
                for key in self._curve_visible:
                    self.plots.clear_signal(key)
                self._last_results = {}
                self._sources_dirty = False
                return
            freqs, Hlf, Hhf, norm = recon
            self._ctx = ModuleContext(freqs, self.sr, Hlf, Hhf, norm)
            self._sources_dirty = False

        if self._ctx is None:
            return

        result = self.module.process(self._ctx)
        if result.error:
            self.statusBar().showMessage(result.error)
            return

        self._last_results = dict(result.curves)
        for key in self._curve_visible:
            if key in result.curves:
                fr, H = result.curves[key]
                self.plots.set_signal(key, fr, H)
            else:
                self.plots.clear_signal(key)

        info = "   ".join(f"{k}: {v}" for k, v in result.info.items())
        self.readout.setText(info)
        self.statusBar().showMessage("Design updated. " + info)

    # -- export -------------------------------------------------------------------------

    def export_headers(self):
        if self._ctx is None:
            self.statusBar().showMessage("Nothing to export -- load measurements first.")
            return
        result = self.module.process(self._ctx)
        if result.error:
            self.statusBar().showMessage(result.error)
            return
        if not result.exports:
            self.statusBar().showMessage("This module produced no exportable coefficients.")
            return
        written = []
        for filename, arrays, names in result.exports:
            path = self._export_path(filename)
            path.parent.mkdir(parents=True, exist_ok=True)
            write_cpp_float_arrays_to_header(arrays, names, str(path))
            written.append(str(path))
        self.statusBar().showMessage("Wrote " + ", ".join(written))

    def _export_path(self, filename):
        """Where an exported header goes: flat into --export-dir when given, otherwise into
        the plugin folder that compiles it (EXPORT_TARGETS), or flat into the export dir when
        that folder does not exist there."""
        if self.export_dir_override:
            return Path(self.export_dir_override) / filename
        root = config.export_dir()
        plugin = EXPORT_TARGETS.get(filename)
        if plugin and (root / plugin).is_dir():
            return root / plugin / filename
        return root / filename

    # -- small helpers ------------------------------------------------------------------

    @staticmethod
    def _spin(val, lo, hi):
        w = QtWidgets.QSpinBox(); w.setRange(int(lo), int(hi)); w.setValue(int(val)); return w

    @staticmethod
    def _dspin(val, lo, hi, dec):
        w = QtWidgets.QDoubleSpinBox(); w.setDecimals(dec)
        w.setRange(lo, hi); w.setSingleStep(10 ** -dec if dec else 1); w.setValue(val)
        return w


# ---------------------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------------------

def parse_args(argv=None):
    p = argparse.ArgumentParser(description="DSP analysis & filter-design GUI.")
    p.add_argument("--no-load", action="store_true",
                   help="Do not auto-load the measurement wavs on startup.")
    p.add_argument("--opengl", action="store_true",
                   help="Use GPU/OpenGL line rendering (needs pyopengl).")
    p.add_argument("--data-dir",
                   help="Folder to auto-load the measurement wavs from. Default: NF_DATA_DIR "
                        "from config/local.env, else python_dsp_tools/data.")
    p.add_argument("--export-dir",
                   help="Write every exported C++ header flat into this folder instead of "
                        "into the plugin folders under juce_plugins/.")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.data_dir:
        os.environ["NF_DATA_DIR"] = os.path.abspath(os.path.expanduser(args.data_dir))
    if args.opengl:
        pg.setConfigOptions(useOpenGL=True, enableExperimental=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.export_dir_override = (os.path.abspath(os.path.expanduser(args.export_dir))
                               if args.export_dir else None)
    win.show()
    if not args.no_load:
        win.load_existing()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
