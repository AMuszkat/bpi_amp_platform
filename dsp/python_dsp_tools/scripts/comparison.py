import numpy as np
from scipy import signal
from scipy.fft import irfft
import matplotlib.pyplot as plt

def firls_inverse_magnitude_filter(H, freqs, sample_rate, numtaps, f_start, f_end, max_gain=100, export_coeffs_to_cpp=None):
    """
    Create an inverse filter using the least-squares FIR design (firls) to approximate the inverse of a given
    magnitude response and group delay for the positive frequency side of the transfer function.

    :param H: Numpy array containing the magnitude response (positive frequencies, 0 to sample_rate/2)
    :param freqs: Numpy array of frequencies corresponding to H (in Hz, 0 to sample_rate/2)
    :param sample_rate: Sampling rate in Hz
    :param numtaps: Number of filter taps (odd for linear-phase FIR)
    :param f_start: Starting frequency of the filter in Hz
    :param f_end: Ending frequency of the filter in Hz
    :param max_gain: Maximum allowed inverse gain to prevent instability (default: 100)
    :return: Numpy array containing the filter coefficients
    :raises ValueError: If inputs are invalid
    :raises RuntimeError: If filter design fails
    """

    # Note: Added the inversion and clipping to match the intended "inverse" behavior
    desired = np.clip(1 / (np.abs(H) + 1e-10), a_min=1e-6, a_max=max_gain)

    inverse_filter = signal.firls(
        numtaps,
        freqs,
        desired,  # Approximate 1 / |H|
        fs=sample_rate
    )

    inv_filter_b, inv_filter_a = inverse_filter, [1.0]  # FIR, so a=[1]; fixed the normalize call which seemed incorrect

    w, H_approx = signal.freqz(inv_filter_b, inv_filter_a, worN=len(H), fs=sample_rate)

    # Compute the complex inverse of the approximated response (this negates the phase, leading to negative GD)
    Hinv = 1 / (H_approx + 1e-10)

    # Clip the magnitude again after inversion for safety
    mag_inv = np.abs(Hinv)
    mag_inv_clipped = np.clip(mag_inv, a_min=0, a_max=max_gain)
    Hinv = mag_inv_clipped * (Hinv / (mag_inv + 1e-10))

    mag = np.abs(H)
    mag_inv = np.abs(Hinv)

    # Find closest indexes to f_start and f_end (unused in original, but computed)
    i_start = np.argmin(np.abs(freqs - f_start))
    i_end = np.argmin(np.abs(freqs - f_end))

    # Prefilter
    # Parks-McClellan (remez) prefilter
    Hprelp_taps = signal.remez(61, [0, 15e3, 15e3 + 6.5e3, 0.5*sample_rate], [1, 0], fs=sample_rate)
    w_pre, Hprelp = signal.freqz(Hprelp_taps, [1], worN=len(H), fs=sample_rate)
    # Design a high-pass filter with cutoff around 30 Hz using firwin
    # We'll use a transition band from 15 Hz to 30 Hz (stopband to passband)
    firwin_numtaps = 101
    firwin_cutoff = 35  # High-pass cutoff frequency in Hz
    Hprehp_taps = signal.firwin(firwin_numtaps, firwin_cutoff, pass_zero=False, fs=sample_rate)
    w_pre, Hprehp = signal.freqz(Hprehp_taps, [1], worN=len(H), fs=sample_rate)

    Hinv *= Hprelp * Hprehp
    hinv = irfft(Hinv)  # Inverse FFT to get time-domain filter coefficients

    # Removed the export block as it's not defined and not needed for this demo
    # if export_coeffs_to_cpp != None or export_coeffs_to_cpp == False:
    #     write_cpp_float_arrays_to_header([inv_filter_a, Hprehp_taps, Hprelp_taps], ['InvFilter', 'HPreHpTaps', 'HPreLpTaps'], "../juce_plugins/firConv/", export_coeffs_to_cpp)
    
    return inv_filter_b, inv_filter_a, Hinv, Hprelp, Hprehp, hinv

# Main script to demonstrate group delay in FIR (time-domain) vs frequency-domain transfer function

# Parameters
sample_rate = 48000
num_points = 512
freqs = np.linspace(0, sample_rate / 2, num_points)
numtaps = 201  # Odd number
f_start = 20
f_end = 20000
max_gain = 100

# Create a sample magnitude response H (e.g., from a lowpass filter)
original_cutoff = 5000 / (sample_rate / 2)  # Normalized
original_taps = signal.firwin(51, original_cutoff, window='hamming')
_, original_H = signal.freqz(original_taps, 1, worN=freqs, fs=sample_rate)
H = np.abs(original_H)

# Generate the inverse filter
inv_filter_b, inv_filter_a, Hinv, Hprelp, Hprehp, hinv = firls_inverse_magnitude_filter(
    H, freqs, sample_rate, numtaps, f_start, f_end, max_gain
)

# Compute group delay from the FIR coefficients (time-domain implementation)
w_fir_hz, gd_fir = signal.group_delay((hinv, [1]), fs=sample_rate)
# Convert w_fir_hz from Hz to Hz (it's already in Hz if fs given, but group_delay returns w in rad/sample, gd in samples)
# Correction: signal.group_delay returns w in rad/sample, gd in samples regardless of fs
# To get freq in Hz: f = w_fir * sample_rate / (2 * np.pi)
f_fir = w_fir_hz * sample_rate / (2 * np.pi)  # w_fir_hz is actually w_rad
gd_fir_seconds = gd_fir / sample_rate  # Optional, but we'll plot in samples

# Compute group delay from the frequency-domain transfer function Hinv
w_hz = w_fir_hz * sample_rate / (2 * np.pi)  # Wait, align with the w from freqz, which is in Hz
# But for Hinv, w from freqz is in Hz
# To compute gd:
phase = np.unwrap(np.angle(Hinv))
w_rad = 2 * np.pi * freqs / sample_rate  # w in rad/sample for diff
gd_freq = -np.diff(phase) / np.diff(w_rad)  # in samples
f_freq = freqs[:-1]  # or average

# Plot the results for comparison
plt.figure(figsize=(12, 6))
plt.plot(f_fir, gd_fir, label='Group Delay from FIR Coefficients (Time-Domain Impl)')
plt.title('Group Delay - FIR Structure')
plt.xlabel('Frequency (Hz)')
plt.ylabel('Group Delay (samples)')
plt.grid(True)
plt.legend()
plt.xlim([freqs[0], freqs[-1]])
plt.tight_layout()
plt.show()

plt.figure(figsize=(12, 6))
plt.plot(f_freq, gd_freq, label='Group Delay from Hinv (Freq-Domain Multiplication)')
plt.title('Group Delay - Frequency Domain Transfer Function')
plt.xlabel('Frequency (Hz)')
plt.ylabel('Group Delay (samples)')
plt.grid(True)
plt.legend()
plt.xlim([freqs[0], freqs[-1]])
plt.tight_layout()
plt.show()