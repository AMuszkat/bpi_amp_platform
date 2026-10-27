from nearfield.lib import *
from nearfield.config import data_path, get as config_get


if __name__ == "__main__":
    pio.templates.default = 'plotly_dark'
    
    # Parameters for the sine sweep
    start_frequency = 20       # Start frequency in Hz
    end_frequency = 20000      # End frequency in Hz
    duration = 20               # Duration in seconds
    sample_rate = 96000       # Sampling rate in Hz

    # Speaker and measurement parameters
    a = 0.04 # speaker radius in meters
    r = 0.6 # measurement distance in meters

    input_device_name = config_get("NF_INPUT_DEVICE", "default")    # set in config/local.env
    output_device_name = config_get("NF_OUTPUT_DEVICE", "default")  # set in config/local.env

    input_devices = [get_device_id_by_name(input_device_name, kind="input")]
    output_device = get_device_id_by_name(output_device_name, kind="output")
    # Generate the sine sweep
    time, sine_sweep = generate_sine_sweep(start_frequency, end_frequency, duration, sample_rate, padding_ms=5000)

    # Play and record the sine sweep
    print("Playing and recording sine sweep...")
    #recorded_audio = record_from_multiple_devices(sine_sweep, sample_rate, input_devices, output_device, duration)
    recorded_audio = record_with_playrec(sine_sweep, sample_rate, input_devices[0], output_device, in_map=[1], out_map=[1])
    print("Recording finished.")
    print(f"Recorded audio shape: {recorded_audio.shape}")
    print(recorded_audio)

    #time_ir, ir = load_impulse_response_from_wav(data_path("test_ir.wav"))
    time_ir, ir = compute_impulse_response_farina(sine_sweep, recorded_audio[:, 0], sample_rate, start_frequency, end_frequency)
    save_ir_to_wav(data_path("ir_ff_tw.wav"), ir, sample_rate)

    w_ir, window = apply_asymmetric_hanning_window(ir, sample_rate, left_ms=100, right_ms=250)
    ttp = time_to_peak(w_ir, sample_rate, mode="abs")  # Time to peak in milliseconds
    freqs, magnitude, phase = compute_magnitude_phase(w_ir, sample_rate, shift_ms=-ttp)


    
    #Plot______________________________________________________________________________________________________________________#
    
    plot_signals(time, sine_sweep, recorded_audio, sample_rate)
    plot_impulse_response(time_ir, ir, window=window, irWindowed=w_ir)
    plot_magnitude_phase(freqs, magnitude, phase)