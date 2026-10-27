import numpy as np
from scipy import signal
import matplotlib.pyplot as plt

def plot_response(w, h, title):
    """
    Utility function to plot the frequency response of a filter.
    """
    plt.figure()
    plt.plot(w, 20 * np.log10(np.maximum(np.abs(h), 1e-10)), 'b')
    plt.ylim(-60, 5)
    plt.grid(True)
    plt.xlabel('Frequency (Hz)')
    plt.ylabel('Gain (dB)')
    plt.title(title)
    plt.show()

if __name__ == "__main__":
    # Sampling frequency
    fs = 44100  # Hz

    # Crossover frequency
    crossover = 1000.0  # Hz

    # Transition width
    trans_width = 200  # Hz

    # Filter length
    numtaps = 501  # Adjusted for sharper transition at higher fs

    # Define frequency bands (in Hz, since fs is provided)
    lp_bands = [0, crossover, crossover + trans_width, fs / 2]
    lp_gains = [1, 0]  # Passband = 1, stopband = 0

    hp_bands = [0, max(0, crossover - trans_width), crossover, fs / 2]
    hp_gains = [0, 1]  # Stopband = 0, passband = 1

    # Design filters using Parks-McClellan algorithm
    try:
        lp_taps = signal.remez(numtaps, lp_bands, lp_gains, fs=fs)
        hp_taps = signal.remez(numtaps, hp_bands, hp_gains, fs=fs)
    except Exception as e:
        print(f"Filter design failed: {e}")
        exit()

    # Compute frequency responses
    w_lp, h_lp = signal.freqz(lp_taps, worN=2000, fs=fs)
    w_hp, h_hp = signal.freqz(hp_taps, worN=2000, fs=fs)

    # Compute total response
    h_total = h_lp + h_hp

    # Plot responses
    plot_response(w_lp, h_lp, "Low-pass Filter (Crossover at 1000 Hz)")
    plot_response(w_hp, h_hp, "High-pass Filter (Crossover at 1000 Hz)")
    plot_response(w_lp, h_total, "Total Response (Low-pass + High-pass)")
