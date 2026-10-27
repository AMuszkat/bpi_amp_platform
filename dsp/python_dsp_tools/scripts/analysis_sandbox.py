from nearfield.lib import *
from nearfield.config import data_path
import librosa

if __name__ == "__main__":
    pio.templates.default = 'plotly_dark'
    
    # Parameters for the sine sweep
    start_frequency = 20       # Start frequency in Hz
    end_frequency = 2000      # End frequency in Hz
    duration = 20               # Duration in seconds
    sample_rate = 44100       # Sampling rate in Hz

    # Speaker and measurement parameters
    a = 0.04 # speaker radius in meters
    r = 0.42 # measurement distance in meters
    #plot_ka_vs_f(a)

    time_ir_inv, ir_inv = load_impulse_response_from_wav(data_path("hinv.wav"))

    w_ir_inv, window_ir_inv = apply_asymmetric_hanning_window(ir_inv, sample_rate, left_ms=20, right_ms=20, zero_pad_to_original_size=False)

    # ttp_ir_inv = time_to_peak(ir_inv, sample_rate, mode="max")  # Time to peak in milliseconds
    # ttp_w_ir_inv = time_to_peak(w_ir_inv, sample_rate, mode="max")

    freqs_ir_inv, magnitude_ir_inv, phase_ir_inv = compute_magnitude_phase(ir_inv, sample_rate, shift_ms=None, mode='max', smoothing=6, normalize=1)
    freqs_w_ir_inv, magnitude_w_ir_inv, phase_w_ir_inv = compute_magnitude_phase(w_ir_inv, sample_rate, shift_ms=None, mode='min', smoothing=6, normalize=1)


    print("ir_inv size: ", len(ir_inv))
    print("w_ir_inv size: ", len(w_ir_inv))
    print("magnitude_ir_inv: ", len(magnitude_ir_inv))
    print("magnitude_w_ir_inv: ", len(magnitude_w_ir_inv))
    print("freqs_ir_inv size: ", len(freqs_ir_inv))
    print("freqs_w_ir_inv size: ", len(freqs_w_ir_inv))


    #Plot______________________________________________________________________________________________________________________#
    plot_impulse_response(time_ir_inv, ir_inv, window=None, irWindowed=None, vertical_time_ms=None)
    plot_impulse_response(time_ir_inv, w_ir_inv, window=window_ir_inv, irWindowed=None, vertical_time_ms=None)
    #plot_impulse_response(time_ir, ir_ff_tw, window=window_ff_tw, irWindowed=w_ir_ff_tw, vertical_time_ms=ttp)
    
    #plot_magnitude_phase(freqs, magnitude_ff_wf, phase_ff_wf)
    #plot_magnitude_phase(freqs, magnitude_ff_tw, phase_ff_tw)
    #magnitude_nr_wf = smooth_magnitude_octave_average(freqs_nr_wf, magnitude_nr_wf, 48)
    mags_to_graph = {
        "Mag_ir_inv": magnitude_ir_inv,
        "Mag_w_ir_inv": magnitude_w_ir_inv,
    }
    
    phases_to_graph = {
        "Phase_ir_inv": phase_ir_inv,
        "Phase_w_ir_inv": phase_w_ir_inv,
    }

    freqs = [freqs_ir_inv, freqs_w_ir_inv]

    plot_multiple_magnitude_phase(freqs, mags_to_graph, phases_to_graph, resample=False, single_freq_vector=False)