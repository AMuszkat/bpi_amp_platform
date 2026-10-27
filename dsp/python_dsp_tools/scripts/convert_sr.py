from nearfield.lib import *
from nearfield.config import data_path
import librosa
import soundfile as sf

if __name__ == "__main__":
    pio.templates.default = 'plotly_dark'
    
    # Parameters for the sine sweep
    start_frequency = 20       # Start frequency in Hz
    end_frequency = 2000      # End frequency in Hz
    duration = 20               # Duration in seconds
    sample_rate = 48000        # Sampling rate in Hz

    # Speaker and measurement parameters
    a = 0.04 # speaker radius in meters
    r = 0.42 # measurement distance in meters
    #plot_ka_vs_f(a)

    orig_sr = 88200
    target_sr = 48000

    time_ir, ir_nr_wf = load_impulse_response_from_wav(data_path("ir_nr_wf.wav"))
    time_ir, ir_ff_wf = load_impulse_response_from_wav(data_path("ir_ff_wf.wav"))
    time_ir, ir_ff_tw = load_impulse_response_from_wav(data_path("ir_ff_tw.wav"))
    
    ir_nr_wf = librosa.resample(ir_nr_wf, orig_sr=orig_sr, target_sr=target_sr)
    ir_ff_wf = librosa.resample(ir_ff_wf, orig_sr=orig_sr, target_sr=target_sr)
    ir_ff_tw = librosa.resample(ir_ff_tw, orig_sr=orig_sr, target_sr=target_sr)

    sf.write(data_path("ir_nr_wf.wav"), ir_nr_wf, target_sr)
    sf.write(data_path("ir_ff_wf.wav"), ir_ff_wf, target_sr)
    sf.write(data_path("ir_ff_tw.wav"), ir_ff_tw, target_sr)
