from nearfield.lib import *
from nearfield.config import data_path
import librosa

if __name__ == "__main__":
    pio.templates.default = 'plotly_dark'
    
    # Parameters for the sine sweep
    start_frequency = 20       # Start frequency in Hz
    end_frequency = 2000      # End frequency in Hz
    duration = 20               # Duration in seconds
    sample_rate = 48000       # Sampling rate in Hz

    # Speaker and measurement parameters
    a = 0.04 # speaker radius in meters
    r = 0.42 # measurement distance in meters
    #plot_ka_vs_f(a)

    time_ir, ir_nr_wf = load_impulse_response_from_wav(data_path("ir_nr_wf.wav"))
    time_ir, ir_ff_wf = load_impulse_response_from_wav(data_path("ir_ff_wf.wav"))
    time_ir, ir_ff_tw = load_impulse_response_from_wav(data_path("ir_ff_tw.wav"))
    
    w_ir_nr_wf, window_nr_wf = apply_asymmetric_hanning_window(ir_nr_wf, sample_rate, left_ms=100, right_ms=250, zero_pad='even')
    w_ir_ff_wf, window_ff_wf = apply_asymmetric_hanning_window(ir_ff_wf, sample_rate, left_ms=20, right_ms=6, zero_pad='even')
    w_ir_ff_tw, window_ff_tw = apply_asymmetric_hanning_window(ir_ff_tw, sample_rate, left_ms=20, right_ms=6, zero_pad='even')

    ttp = time_to_peak(w_ir_nr_wf, sample_rate, mode="min")  # Time to peak in milliseconds
    
    freqs_ff_wf, magnitude_ff_wf, phase_ff_wf = compute_magnitude_phase(w_ir_ff_wf, sample_rate, shift_ms=None, mode="max", smoothing=6, normalize=1)
    freqs_nr_wf, magnitude_nr_wf, phase_nr_wf = compute_magnitude_phase(w_ir_nr_wf, sample_rate, shift_ms=None, mode="max", smoothing=6, normalize=1)
    magnitude_tot_wf, phase_tot_wf, norm_factor = compute_total_far_field_response(freqs_nr_wf, magnitude_nr_wf, phase_nr_wf, magnitude_ff_wf, phase_ff_wf, a, r, 1350, sample_rate, normalize=0)
    print("len of w_ir_nr_wf:", len(w_ir_nr_wf))
    print("norm_factor:", 20*np.log10(norm_factor))
    freqs, magnitude_ff_tw, phase_ff_tw = compute_magnitude_phase(w_ir_ff_tw, sample_rate, shift_ms=None, mode="max", smoothing=6, normalize=norm_factor)

    Hlf = magnitude_tot_wf * np.exp(1j * phase_tot_wf)
    Hhf = magnitude_ff_tw * np.exp(1j * phase_ff_tw)

    #Plot______________________________________________________________________________________________________________________#
    #plot_impulse_response(time_ir, ir_nr_wf, window=window_nr_wf, irWindowed=w_ir_nr_wf, vertical_time_ms=ttp)
    #plot_impulse_response(time_ir, ir_ff_wf, window=window_ff_wf, irWindowed=w_ir_ff_wf, vertical_time_ms=ttp)
    #plot_impulse_response(time_ir, ir_ff_tw, window=window_ff_tw, irWindowed=w_ir_ff_tw, vertical_time_ms=ttp)
    
    # plot_magnitude_phase(freqs, magnitude_ff_wf, phase_ff_wf)
    # plot_magnitude_phase(freqs, magnitude_ff_tw, phase_ff_tw)
    # plot_total_magnitude_phase(freqs, magnitude_tot_wf, phase_tot_wf, 
    #                                         magnitude_nr_wf, phase_nr_wf, 
    #                                         magnitude_ff_wf, phase_ff_wf, 
    #                                         magnitude_ff_tw, phase_ff_tw, 
    #                                         resample_n_points=1000)
    process_2_way_speaker(Hlf, Hhf, sample_rate, freqs)