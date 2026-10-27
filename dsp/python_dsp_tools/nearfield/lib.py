# Hard dependencies: the DSP core needs only numpy + scipy + stdlib.
import numpy as np
from scipy import signal, integrate
from scipy.integrate import cumulative_trapezoid
from scipy.signal import fftconvolve, lfilter
from scipy.fft import fft, ifft, rfft, irfft
from scipy.io.wavfile import read
from math import sqrt  # Import sqrt from math module
import inspect
import itertools

# Optional dependencies. These are only needed by specific functions (audio capture,
# saving wavs, and lib.py's own plotly/bokeh plotting helpers). Importing lib should not
# fail just because they are absent -- e.g. the pyqtgraph GUI in near_field_gui.py only
# needs the DSP core above. Functions that use a missing dependency raise a clear error
# when actually called (see _require below).
try:
    import sounddevice as sd
except ImportError:
    sd = None
try:
    import soundfile as sf
except ImportError:
    sf = None
try:
    import plotly.graph_objects as go
    import plotly.io as pio
    from plotly.subplots import make_subplots
except ImportError:
    go = pio = make_subplots = None
try:
    from plotly_resampler import FigureResampler, FigureWidgetResampler
except ImportError:
    FigureResampler = FigureWidgetResampler = None
try:
    from bokeh.plotting import figure, show
    from bokeh.layouts import gridplot
    from bokeh.models import ColumnDataSource, LogAxis, LinearAxis, Range1d, Legend
    from bokeh.palettes import Category10_10 as bokeh_palette  # A palette of 10 distinct colors
except ImportError:
    figure = show = gridplot = ColumnDataSource = None
    LogAxis = LinearAxis = Range1d = Legend = bokeh_palette = None


def _require(obj, name):
    """Raise a clear, actionable error if an optional dependency is missing."""
    if obj is None:
        raise ImportError(
            f"This function requires the optional dependency '{name}', which is not "
            f"installed. Install it with: pip install {name}"
        )
    return obj

def save_ir_to_wav(file_name, impulse_response, sample_rate):
    """
    Save the impulse response to a WAV file.

    :param file_path: Path to save the WAV file
    :param impulse_response: Numpy array containing the impulse response
    :param sample_rate: Sampling rate in Hz
    """
    _require(sf, 'soundfile')
    try:
        # Normalize the impulse response to avoid clipping
        normalized_ir = impulse_response / np.max(np.abs(impulse_response))

        # Save the normalized impulse response as a WAV file
        sf.write(file_name, normalized_ir, sample_rate)
        print(f"Impulse response saved as {file_name}")
    except Exception as e:
        print(f"Error saving impulse response to WAV file: {e}")

def get_device_id_by_name(name, kind="input"):
    """
    Get the device ID for a given device name.

    :param name: The name (or part of the name) of the device to search for.
    :param kind: The type of device to search for ("input" or "output").
    :return: The device ID if found, otherwise raises an error.
    """
    _require(sd, 'sounddevice')
    devices = sd.query_devices()
    for idx, device in enumerate(devices):
        if name.lower() in device['name'].lower():
            if kind == "input" and device['max_input_channels'] > 0:
                return idx
            elif kind == "output" and device['max_output_channels'] > 0:
                return idx
    raise ValueError(f"Device with name '{name}' and kind '{kind}' not found.")
#_________________________________________________________________________________________________________________________#

def load_impulse_response_from_wav(file_path):
    """
    Load an impulse response from a WAV file and return the impulse response and its time array.

    :param file_path: Path to the WAV file containing the impulse response
    :return: Tuple (time, impulse_response)
    """
    # Read the WAV file
    sample_rate, impulse_response = read(file_path)

    # Normalize the impulse response if it's not already in float format
    if impulse_response.dtype != np.float32 and impulse_response.dtype != np.float64:
        impulse_response = impulse_response / np.max(np.abs(impulse_response))

    # Create a time array
    time = np.linspace(0, len(impulse_response) / sample_rate, len(impulse_response), endpoint=False)

    return time, impulse_response

def compute_impulse_response(input_signal, output_signal, sample_rate):
    """
    Compute the impulse response of an LTI system given the input and output signals.

    :param input_signal: Numpy array containing the input signal (sine sweep)
    :param output_signal: Numpy array containing the output signal (recorded audio)
    :param sample_rate: Sampling rate in Hz
    :return: Time array and impulse response
    """
    # Perform FFT on both input and output signals
    input_fft = fft(input_signal)
    output_fft = fft(output_signal)

    # Compute the frequency response (H(f) = Y(f) / X(f))
    frequency_response = output_fft / input_fft

    # Compute the impulse response by applying the inverse FFT
    impulse_response = ifft(frequency_response).real

    # Create a time array for the impulse response
    time = np.linspace(0, len(impulse_response) / sample_rate, len(impulse_response))

    return time, impulse_response

def plot_impulse_response(time, impulse_response, window=None, irWindowed=None, vertical_time_ms=None):
    """
    Plot the impulse response of the system, along with an optional window and windowed impulse response.
    The window is plotted on a secondary y-axis. Optionally, plot a vertical line at a specified time.

    :param time: Time array for the impulse response
    :param impulse_response: Numpy array containing the impulse response
    :param window: Numpy array containing the window (optional)
    :param irWindowed: Numpy array containing the windowed impulse response (optional)
    :param vertical_time_ms: Time in milliseconds to plot a vertical line (optional)
    """
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # Add the impulse response trace
    fig.add_trace(go.Scatter(x=time, y=impulse_response, mode='lines', name='Impulse Response'))

    # Add the window trace if provided (on the secondary y-axis)
    if window is not None:
        fig.add_trace(go.Scatter(x=time, y=window, mode='lines', name='Window'), secondary_y=True)

    # Add the windowed impulse response trace if provided
    if irWindowed is not None:
        fig.add_trace(go.Scatter(x=time, y=irWindowed, mode='lines', name='Windowed Impulse Response'))

    # Add a vertical line if vertical_time_ms is provided
    if vertical_time_ms is not None:
        vertical_time_s = vertical_time_ms / 1000  # Convert milliseconds to seconds
        fig.add_shape(
            type="line",
            x0=vertical_time_s, x1=vertical_time_s,
            y0=min(impulse_response), y1=max(impulse_response),
            line=dict(color="red", dash="dash"),
            name=f"Vertical Line at {vertical_time_ms} ms"
        )
        # Add annotation for the vertical line
        fig.add_annotation(
            x=vertical_time_s,
            y=max(impulse_response),
            text=f"{vertical_time_ms} ms",
            showarrow=True,
            arrowhead=2,
            ax=0,
            ay=-40
        )

    # Update layout with a secondary y-axis
    # fig.update_layout(
    #     title="Impulse Response with Window",
    #     xaxis_title="Time (s)",
    #     yaxis=dict(
    #         title="Amplitude (Impulse Response)",
    #         titlefont=dict(color="blue"),
    #         tickfont=dict(color="blue"),
    #     ),
    #     yaxis2=dict(
    #         title="Amplitude (Window)",
    #         titlefont=dict(color="red"),
    #         tickfont=dict(color="red"),
    #         overlaying="y",
    #         side="right"
    #     ),
    #     template="plotly_white"
    # )

    # Show the plot
    fig.show()

def generate_sine_sweep(start_freq, end_freq, duration, sample_rate, plot=False, padding_ms=0):
    """
    Generate a logarithmic sine sweep from start_freq to end_freq over the given duration with fade-in and fade-out effects.
    Optionally plot the sine sweep using Plotly. Adds zero-padding to the left and right of the sweep.

    :param start_freq: Starting frequency of the sweep in Hz
    :param end_freq: Ending frequency of the sweep in Hz
    :param duration: Duration of the sweep in seconds
    :param sample_rate: Sampling rate in Hz
    :param plot: Boolean indicating whether to plot the sine sweep
    :param padding_ms: Amount of zero-padding to add to the left and right of the sweep in milliseconds
    :return: Tuple (time array, sine sweep with fade-in and fade-out)
    """
    # Create the time array
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)

    # Generate the logarithmic sine sweep according to the equation
    K = duration / np.log(end_freq / start_freq)
    sweep = np.sin(
        2 * np.pi * start_freq * K * (np.exp(t / K) - 1)
    )

    # Create fade-in and fade-out windows (1 second each)
    fade_samples = int(sample_rate * 0.1)  # 1 second fade duration
    fade_in = np.linspace(0, 1, fade_samples)
    fade_out = np.linspace(1, 0, fade_samples)

    # Apply fade-in and fade-out to the sine sweep
    sweep[:fade_samples] *= fade_in
    sweep[-fade_samples:] *= fade_out

    # Add zero-padding to the left and right
    padding_samples = int((padding_ms / 1000) * sample_rate)
    sweep = np.pad(sweep, (padding_samples, padding_samples), mode='constant')

    # Update the time array to include the padding
    total_duration = duration + (2 * padding_ms / 1000)
    t = np.linspace(0, total_duration, len(sweep), endpoint=False)

    # Plot the sine sweep if requested
    if plot:
        import plotly.graph_objects as go
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=t, y=sweep, mode='lines', name='Sine Sweep'))
        fig.update_layout(
            title="Logarithmic Sine Sweep with Fade-In, Fade-Out, and Zero-Padding",
            xaxis_title="Time (s)",
            yaxis_title="Amplitude",
            template="plotly_white"
        )
        fig.show()

    return t, sweep

def play_audio(audio, sample_rate):
    """
    Play the given audio array using the sounddevice library.

    :param audio: Numpy array containing the audio signal
    :param sample_rate: Sampling rate in Hz
    """
    sd.play(audio, samplerate=sample_rate)
    sd.wait()  # Wait until the audio finishes playing

def record_from_multiple_devices(output_audio, sample_rate, input_devices, output_device, duration):
    """
    Play audio on a specific output device and record simultaneously from multiple input devices using callback streams.

    :param output_audio: Numpy array containing the audio signal to play
    :param sample_rate: Sampling rate in Hz
    :param input_devices: List of input device IDs to record from
    :param output_device: Output device ID to play audio
    :param duration: Duration of the recording in seconds
    :return: List of NumPy arrays containing the recorded audio from each input device
    """
    # Prepare buffers to store the recorded audio for each input device
    recorded_audio = [[] for _ in input_devices]

    def callback(indata, outdata, frames, time, status):
        if status:
            print(status)
        # Write the output audio to the output stream
        outdata[:] = output_audio[:frames]
        # Append the input audio to the respective buffers
        for i, device_audio in enumerate(indata):
            recorded_audio[i].append(device_audio.copy())

    # Create the stream with the callback
    with sd.Stream(samplerate=sample_rate, channels=len(input_devices), callback=callback,
                   device=(output_device, input_devices)):
        sd.sleep(int(duration * 1000))  # Sleep for the duration of the recording

    # Convert recorded audio buffers to NumPy arrays
    recorded_audio = [np.concatenate(device_audio) for device_audio in recorded_audio]

    return recorded_audio

def plot_signals(time, sine_sweep, recorded_audio, sample_rate):
    """
    Plot the sine sweep and recorded audio using Plotly.

    :param time: Time array for the sine sweep
    :param sine_sweep: Numpy array containing the sine sweep
    :param recorded_audio: Numpy array containing the recorded audio
    :param sample_rate: Sampling rate in Hz
    """
    # Create time arrays for the recorded audio
    recorded_time = np.linspace(0, len(recorded_audio) / sample_rate, len(recorded_audio))

    # Create the figure
    fig = go.Figure()

    # Add the sine sweep trace
    fig.add_trace(go.Scatter(x=time, y=sine_sweep, mode='lines', name='Sine Sweep'))

    # Add the recorded audio trace (first channel)
    fig.add_trace(go.Scatter(x=recorded_time, y=recorded_audio[:, 0], mode='lines', name='Recorded Audio (Mic 1)'))

    # If there are multiple channels, add the second channel
    if recorded_audio.shape[1] > 1:
        fig.add_trace(go.Scatter(x=recorded_time, y=recorded_audio[:, 1], mode='lines', name='Recorded Audio (Mic 2)'))

    # Update layout
    fig.update_layout(
        title="Sine Sweep and Recorded Audio",
        xaxis_title="Time (s)",
        yaxis_title="Amplitude",
        legend_title="Signals",
        template="plotly_white"
    )

    # Show the plot
    fig.show()

def compute_magnitude_phase_freqz(impulse_response, sample_rate, N=1):
    """
    Compute the magnitude and phase response of the impulse response using freqz, making it periodic N times.

    :param impulse_response: Numpy array containing the impulse response
    :param sample_rate: Sampling rate in Hz
    :param N: Number of times to repeat the impulse response to make it periodic
    :return: Frequency array, magnitude response, and phase response
    """
    # Repeat the impulse response N times to make it periodic
    periodic_impulse_response = np.tile(impulse_response, N)

    # Plot the periodic impulse response
    periodic_time = np.linspace(0, len(periodic_impulse_response) / sample_rate, len(periodic_impulse_response))
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=periodic_time, y=periodic_impulse_response, mode='lines', name='Periodic Impulse Response'))
    fig.update_layout(
        title="Periodic Impulse Response",
        xaxis_title="Time (s)",
        yaxis_title="Amplitude",
        template="plotly_white"
    )
    fig.show()

    # Determine the number of frequency points (same as FFT size for consistency)
    freq_points = len(periodic_impulse_response)

    # Compute frequency response using freqz
    w, frequency_response = signal.freqz(periodic_impulse_response, worN=freq_points, fs=sample_rate)

    # Compute magnitude and phase
    magnitude = np.abs(frequency_response)
    phase = np.angle(frequency_response)

    # Frequency array (already in Hz, from 0 to fs/2, matching FFT's positive frequencies)
    freqs = w

    # Calculate and print the frequency resolution
    frequency_resolution = sample_rate / freq_points
    print(f"Frequency resolution per FFT size: {frequency_resolution} Hz")

    # Return frequency array, magnitude, and phase
    return freqs, magnitude, phase

def compute_magnitude_phase(impulse_response, sample_rate, shift_ms=None, mode=None, smoothing=0, normalize=0):
    """
    Compute the magnitude and phase response of the impulse response.
    Optionally shift the phase, apply smoothing, and normalize the magnitude.

    :param impulse_response: Numpy array containing the impulse response
    :param sample_rate: Sampling rate in Hz
    :param shift_ms: Optional manual time shift in milliseconds (used if mode is not provided)
    :param mode: Mode for time-to-peak detection ("max", "min", or "abs"). If provided, shift_ms is calculated automatically.
    :param smoothing: Denominator for octave smoothing (e.g., 3 for 1/3 octave). If 0, no smoothing.
    :param normalize: If 0, normalize magnitude at 1kHz to 0dB (linear 1.0) and return normalization_factor.
                      Otherwise, no normalization factor is returned.
    :return: freqs, magnitude, phase OR freqs, magnitude, phase, normalization_factor (if normalize==0)
    """
    if mode is not None:
        shift_ms = time_to_peak(impulse_response, sample_rate, mode=mode)
    elif shift_ms is None:
        # Default to 0 shift if neither mode nor shift_ms is provided, to avoid error
        # Or raise ValueError("Either 'mode' or 'shift_ms' must be provided.") if a shift is always expected
        shift_ms = 0 
        print("Warning: No shift mode or shift_ms provided. Defaulting to 0ms shift.")


    if len(impulse_response) % 2 != 0:
        fft_size = len(impulse_response)+1
    else:
        fft_size = len(impulse_response)-1

    frequency_response = rfft(impulse_response, n=fft_size)  # Use rfft for real-valued input
    freqs = np.fft.rfftfreq(fft_size, d=(1 / sample_rate))

    if shift_ms != 0: # Apply phase compensation only if shift_ms is non-zero
        t_shift = shift_ms / 1000
        frequency_response *= np.exp(-1j * 2 * np.pi * freqs * -t_shift)

    magnitude = np.abs(frequency_response)
    phase = np.angle(frequency_response)

    # Take only positive frequencies
    # positive_freq_indices = np.where(freqs >= 0)
    # freqs = freqs[positive_freq_indices][:fft_size // 2]
    # magnitude = magnitude[positive_freq_indices][:fft_size // 2]
    # phase = phase[positive_freq_indices][:fft_size // 2]
    
    # Apply smoothing if requested
    if smoothing != 0:
        if len(freqs) > 0:
            # Ensure smoothing denominator is positive
            if smoothing < 0:
                print(f"Warning: Smoothing value {smoothing} is negative. Using absolute value.")
                smoothing = abs(smoothing)
            if smoothing == 0: # Should be caught by outer if, but as a safeguard
                 print("Warning: Smoothing value is 0, skipping smoothing.")
            else:
                octave_res = 1.0 / smoothing
                # Smooth magnitude in log domain (dB)
                log_magnitude = 20 * np.log10(magnitude)
                smoothed_log_magnitude = smooth_octave_average(freqs, log_magnitude, octave_res)
                magnitude = 10 ** (smoothed_log_magnitude / 20)
                # Smooth phase as before
                unwrapped_phase = np.unwrap(phase)
                unwrapped_phase = smooth_octave_average(freqs, unwrapped_phase, octave_res)
                phase = (unwrapped_phase + np.pi) % (2 * np.pi) - np.pi
        else:
            print("Warning: freqs array is empty, skipping smoothing.")

    if normalize == 0:

        idx_1khz = np.argmin(np.abs(freqs - 1000.0)) # Find index closest to 1kHz
        magnitude_at_1khz = magnitude[idx_1khz]
        normalization_factor = 1.0 / magnitude_at_1khz
        magnitude = magnitude * normalization_factor
        print("normalize at 1kHz: ", 20 * np.log10(magnitude[idx_1khz]))
        print("freqs[idx_1khz]: ", freqs[idx_1khz])
        
        return freqs, magnitude, phase, normalization_factor
    else: # normalize is not 0 (e.g., None or any other value)
        return freqs, magnitude*normalize, phase

def plot_magnitude_phase(freqs, magnitude, phase):
    """
    Plot the magnitude and phase response of the system in separate subplots with a logarithmic frequency axis.

    :param freqs: Frequency array
    :param magnitude: Magnitude response
    :param phase: Phase response
    """
    # Create subplots: 2 rows, 1 column
    fig = make_subplots(rows=2, cols=1, 
                        subplot_titles=("Magnitude Response (dB)", "Phase Response (degrees)"))

    # Add magnitude response trace (top plot)
    fig.add_trace(
        go.Scatter(x=freqs, y=20 * np.log10(magnitude), mode='lines', name='Magnitude (dB)'),
        row=1, col=1
    )

    # Add phase response trace (bottom plot)
    fig.add_trace(
        go.Scatter(x=freqs, y=np.degrees(phase), mode='lines', name='Phase (degrees)'),
        row=2, col=1
    )

    # Update x-axis to be logarithmic
    fig.update_xaxes(type="log", title_text="Frequency (Hz)", range=[np.log10(20), np.log10(20e3)], row=1, col=1)
    fig.update_xaxes(type="log", title_text="Frequency (Hz)", range=[np.log10(20), np.log10(20e3)], row=2, col=1)

    # Update y-axes
    fig.update_yaxes(title_text="Magnitude (dB)", row=1, col=1)
    fig.update_yaxes(title_text="Phase (degrees)", row=2, col=1)

    # Update layout
    fig.update_layout(
        title="Magnitude and Phase Response",
        height=2400,  # Adjust height for better visualization
    )

    # Show the plot
    fig.show()

def apply_asymmetric_hanning_window(impulse_response, sample_rate, left_ms, right_ms, zero_pad='keep_original'):
    """
    Apply an asymmetric Hanning window to an impulse response with the absolute maximum peak as the center.
    Optionally zero-pad the windowed impulse response to match the size of the original impulse response.

    :param impulse_response: Numpy array containing the impulse response
    :param sample_rate: Sampling rate in Hz
    :param left_ms: Length of the window to the left of the maximum peak in milliseconds
    :param right_ms: Length of the window to the right of the maximum peak in milliseconds
    :param zero_pad_to_original_size: If True, zero-pad the windowed impulse response to the original length
    :return: Tuple (windowed_impulse_response, window)
    """
    # Convert lengths from milliseconds to samples
    left_samples = int((left_ms / 1000) * sample_rate)
    right_samples = int((right_ms / 1000) * sample_rate)

    # Find the index of the absolute maximum peak
    peak_index = np.argmax(np.abs(impulse_response))

    # Calculate the start and end indices for the window
    left_start = max(0, peak_index - left_samples)
    right_end = min(len(impulse_response), peak_index + right_samples)

    # Create the left half Hanning window
    left_window_length = peak_index - left_start
    left_hanning = np.hanning(2 * left_window_length)[:left_window_length] if left_window_length > 0 else np.array([])

    # Create the right half Hanning window
    right_window_length = right_end - peak_index
    right_hanning = np.hanning(2 * right_window_length)[right_window_length:] if right_window_length > 0 else np.array([])

    # Combine the left and right windows into a full asymmetric window
    full_window = np.zeros_like(impulse_response)
    full_window[left_start:peak_index] = left_hanning
    full_window[peak_index:right_end] = right_hanning

    # Apply the window to the impulse response
    windowed_impulse_response = impulse_response * full_window

    if zero_pad=='keep_original': #Keep original size
        # Zero-pad the windowed impulse response to match the original length
        padded_impulse_response = np.zeros_like(impulse_response)
        padded_impulse_response[:len(windowed_impulse_response)] = windowed_impulse_response
        windowed_impulse_response = padded_impulse_response
        return windowed_impulse_response, full_window
    elif zero_pad=='nextpow2':
        # Zero-pad the windowed impulse response to the next power of 2
        next_pow2 = 2 ** np.ceil(np.log2(len(windowed_impulse_response)))
        padded_impulse_response = np.zeros(int(next_pow2))
        padded_impulse_response[:len(windowed_impulse_response)] = windowed_impulse_response
        window = full_window[:len(padded_impulse_response)]
        return padded_impulse_response, window
    elif zero_pad=='even':
        # Zero-pad the windowed impulse response to the next length ending in 4 or 8
        orig_len = len(windowed_impulse_response)
        last_digit = orig_len % 10
        if last_digit <= 4:
            next_even = orig_len + (4 - last_digit)
        elif last_digit <= 8:
            next_even = orig_len + (8 - last_digit)
        else:
            next_even = orig_len + (14 - last_digit)  # Next possible ending in 4
        padded_impulse_response = np.zeros(next_even)
        padded_impulse_response[:orig_len] = windowed_impulse_response
        window = full_window[:next_even]
        return padded_impulse_response, window
    else:
        # Return only the nonzero region
        windowed_impulse_response = windowed_impulse_response[left_start:right_end]
        window = full_window[left_start:right_end]
        return windowed_impulse_response, window

def find_nmatch(array, samplerate, fmatch):
    """
    Calculate the index of the frequency bin closest to the target frequency.

    This function computes the index (`nmatch`) of the frequency bin in a 
    Fourier-transformed array that corresponds to the given target frequency (`fmatch`).
    The calculation is based on the frequency resolution determined by the 
    sampling rate (`samplerate`) and the length of the input array.

    Args:
        array (list or numpy.ndarray): The input array, typically representing 
            a signal in the time domain or its Fourier-transformed counterpart.
        samplerate (float): The sampling rate of the signal in Hz.
        fmatch (float): The target frequency to match in Hz.

    Returns:
        int: The index of the frequency bin closest to the target frequency.
    """
    freq_resolution = samplerate / len(array)
    nmatch = int(round(fmatch / freq_resolution))

    return 2*nmatch

def compute_total_far_field_response(freqs, nf_magnitude, nf_phase, ff_magnitude, ff_phase, a, r, fmatch, samplerate, normalize=0):
    """
    Compute the total far-field response by combining near-field and far-field data.
    This function calculates the magnitude and phase of the far-field response 
    based on the given near-field and far-field magnitudes and phases, along with 
    other parameters such as distance, matching frequency, and sampling rate.
    Parameters:
    -----------
    nf_magnitude : numpy.ndarray
        Array containing the magnitude of the near-field response.
    nf_phase : numpy.ndarray
        Array containing the phase of the near-field response.
    ff_magnitude : numpy.ndarray
        Array containing the magnitude of the far-field response.
    ff_phase : numpy.ndarray
        Array containing the phase of the far-field response.
    a : float
        Radio of the radiating surface.
    r : float
        Distance between the source and the receiver.
    fmatch : float
        Frequency at which the near-field and far-field responses are matched.
    samplerate : float
        Sampling rate of the signal.
    Returns:
    --------
    magnitude : numpy.ndarray
        Array containing the combined magnitude of the far-field response.
    phase : numpy.ndarray
        Array containing the combined phase of the far-field response.
    Notes:
    ------
    - The near-field response is scaled by a factor of `a/(2*r)` for frequencies 
      up to `fmatch * samplerate`.
    - The phase of the near-field response is adjusted to account for the 
      propagation delay based on the distance `r` and the speed of sound (343 m/s).
    - For frequencies beyond `fmatch * samplerate`, the far-field magnitude and 
      phase are used directly.
    """
    
    magnitude = np.zeros_like(nf_magnitude)
    phase = np.zeros_like(nf_phase)

    nmatch = find_nmatch(nf_magnitude, samplerate, fmatch)
    
    #c1 = a/(2*r) # scaling factor for near-field response in half space
    #c2 = a/(4*r) # scaling factor for far-field response in full space
    #c3 = sqrt((r/a)**2 + 1) - (r/a)  # scaling factor for near-field response according to distance
    c4 = ff_magnitude[nmatch] / nf_magnitude[nmatch]  # scaling factor based on pure measurements
    #c5 = average_magnitude_difference(ff_magnitude, nf_magnitude, samplerate, [1500, 1600])  # scaling factor based on average magnitude difference

    magnitude[0:nmatch] = c4 * nf_magnitude[0:nmatch]
    magnitude[(nmatch):] = ff_magnitude[(nmatch):]

    c4rad = ff_phase[nmatch] - nf_phase[nmatch]  # phase difference at the matching frequency
    # Determine the sign of c5rad based on the relative phase difference
    if np.mean(ff_phase[:nmatch] - nf_phase[:nmatch]) < 0:
    # ff_phase is greater than nf_phase, apply positive c5rad
        c4rad = -c4rad
 

# Apply the phase difference and wrap the result to [-π, π]
    phase[0:nmatch] = (nf_phase[0:nmatch] + c4rad + np.pi) % (2 * np.pi) - np.pi 
    phase[nmatch] = (nf_phase[nmatch] + c4rad + np.pi) % (2 * np.pi) - np.pi
    phase[nmatch+1:] = ff_phase[nmatch+1:]

    if normalize == 0:

        idx_1khz = np.argmin(np.abs(freqs - 1000.0)) # Find index closest to 1kHz
        magnitude_at_1khz = magnitude[idx_1khz]
        normalization_factor = 1.0 / magnitude_at_1khz
        magnitude = magnitude * normalization_factor

        return magnitude, phase, normalization_factor
    
    else:
        # If normalize is not 0, just return the magnitude and phase without normalization
        return magnitude*normalize, phase

def trim_impulse_response(impulse_response, threshold_percent, sample_rate):
    """
    Trim the left side of the impulse response based on a threshold percentage of the maximum peak,
    starting from the first sample before the maximum peak that exceeds the threshold.

    :param impulse_response: Numpy array containing the impulse response
    :param threshold_percent: Threshold percentage (0-100) of the maximum peak to determine the start of the trimmed response
    :param sample_rate: Sampling rate in Hz
    :return: Tuple (trimmed_time, trimmed_impulse_response)
    """
    # Calculate the threshold value
    max_peak = np.max(np.abs(impulse_response))
    threshold_value = (threshold_percent / 100) * max_peak

    # Find the index of the maximum peak
    peak_index = np.argmax(np.abs(impulse_response))

    # Search backward from the peak index to find the first sample above the threshold
    for i in range(peak_index, -1, -1):
        if np.abs(impulse_response[i]) < threshold_value:
            start_index = i + 1  # The first index above the threshold
            break
    else:
        # If no such index is found, start from the beginning
        start_index = 0

    # Trim the impulse response
    trimmed_impulse_response = impulse_response[start_index:]

    # Create a new time array for the trimmed impulse response
    trimmed_time = np.linspace(0, len(trimmed_impulse_response) / sample_rate, len(trimmed_impulse_response), endpoint=False)

    # Debugging output
    print(f"Maximum peak value: {max_peak}")
    print(f"Threshold value ({threshold_percent}%): {threshold_value}")
    print(f"Peak index: {peak_index}")
    print(f"Start index of trimmed impulse response: {start_index}")

    return trimmed_time, trimmed_impulse_response

def apply_right_side_hanning_window(impulse_response, sample_rate, right_ms, zero_pad=False):
    """
    Apply a half Hanning window to the right side of the impulse response, starting at the absolute maximum peak.
    Optionally zero-pad the windowed impulse response to the length of the original signal.

    :param impulse_response: Numpy array containing the impulse response
    :param sample_rate: Sampling rate in Hz
    :param right_ms: Length of the window to the right of the maximum peak in milliseconds
    :param zero_pad: Boolean indicating whether to zero-pad the windowed impulse response to the original length
    :return: Tuple (windowed_impulse_response, hanning_window, time_array)
    """
    # Find the index of the absolute maximum peak
    peak_index = np.argmax(np.abs(impulse_response))

    # Convert the right window length from milliseconds to samples
    right_samples = int((right_ms / 1000) * sample_rate)

    # Ensure the window length does not exceed the remaining signal length
    right_samples = min(right_samples, len(impulse_response) - peak_index)

    # Create the half Hanning window
    hanning_window = np.hanning(2 * right_samples)[right_samples:]

    # Apply the window to the impulse response starting at the peak
    windowed_impulse_response = impulse_response[peak_index:peak_index + right_samples] * hanning_window

    # Handle zero padding
    if zero_pad:
        padded_impulse_response = np.zeros_like(impulse_response)
        padded_impulse_response[peak_index:peak_index + right_samples] = windowed_impulse_response
        windowed_impulse_response = padded_impulse_response
    else:
        # Trim the windowed impulse response to the length of the window
        windowed_impulse_response = windowed_impulse_response[:right_samples]

    # Create a time array for the windowed impulse response
    time_array = np.linspace(0, len(windowed_impulse_response) / sample_rate, len(windowed_impulse_response), endpoint=False)

    # Debugging output
    print(f"Peak index: {peak_index} samples ({peak_index / sample_rate * 1000:.2f} ms)")
    print(f"Right window length: {right_samples} samples ({right_samples / sample_rate * 1000:.2f} ms)")
    if zero_pad:
        print(f"Windowed impulse response zero-padded to original length: {len(impulse_response)} samples")

    return windowed_impulse_response, hanning_window, time_array

def trim_impulse_response_by_time(impulse_response, sample_rate, trim_ms):
    """
    Trim the left side of the impulse response by the first X milliseconds.

    :param impulse_response: Numpy array containing the impulse response
    :param sample_rate: Sampling rate in Hz
    :param trim_ms: Number of milliseconds to trim from the start of the impulse response
    :return: Tuple (trimmed_time, trimmed_impulse_response)
    """
    # Convert trim time from milliseconds to samples
    trim_samples = int((trim_ms / 1000) * sample_rate)

    # Ensure the trim length does not exceed the impulse response length
    trim_samples = min(trim_samples, len(impulse_response))

    # Trim the impulse response
    trimmed_impulse_response = impulse_response[trim_samples:]

    # Create a new time array for the trimmed impulse response
    trimmed_time = np.linspace(0, len(trimmed_impulse_response) / sample_rate, len(trimmed_impulse_response), endpoint=False)

    # Debugging output
    print(f"Trimmed {trim_samples} samples ({trim_ms} ms) from the start of the impulse response.")

    return trimmed_time, trimmed_impulse_response

def trim_impulse_response_from_peak(impulse_response, sample_rate):
    """
    Trim the impulse response starting from its absolute maximum peak.

    :param impulse_response: Numpy array containing the impulse response
    :param sample_rate: Sampling rate in Hz
    :return: Tuple (trimmed_time, trimmed_impulse_response)
    """
    # Find the index of the absolute maximum peak
    peak_index = np.argmax(np.abs(impulse_response))

    # Trim the impulse response starting from the peak
    trimmed_impulse_response = impulse_response[peak_index:]

    # Create a new time array for the trimmed impulse response
    trimmed_time = np.linspace(0, len(trimmed_impulse_response) / sample_rate, len(trimmed_impulse_response), endpoint=False)

    # Debugging output
    print(f"Peak index: {peak_index}")
    print(f"Time at peak: {peak_index / sample_rate:.6f} seconds")

    return trimmed_time, trimmed_impulse_response

def rearrange_signal_to_peak(signal):
    """
    Rearrange a signal so that it starts at its absolute maximum peak.
    The part of the signal before the peak is moved to the end.

    :param signal: Numpy array containing the signal
    :return: Rearranged signal
    """
    # Find the index of the absolute maximum peak
    peak_index = np.argmax(np.abs(signal))

    # Rearrange the signal
    rearranged_signal = np.concatenate((signal[peak_index:], signal[:peak_index]))

    # Debugging output
    print(f"Peak index: {peak_index}")
    print(f"Value at peak: {signal[peak_index]}")

    return rearranged_signal

def time_to_peak(impulse_response, sample_rate, mode="abs"):
    """
    Calculate the time in milliseconds from the start of the impulse response to its peak based on the specified mode.

    :param impulse_response: Numpy array containing the impulse response
    :param sample_rate: Sampling rate in Hz
    :param mode: Mode of peak detection ("max", "min", or "abs")
                 - "max": Detect the time to the maximum positive peak
                 - "min": Detect the time to the maximum negative peak
                 - "abs": Detect the time to the maximum absolute peak (positive or negative)
    :return: Time to the peak in milliseconds
    """
    if mode == "max":
        # Find the index of the maximum positive peak
        peak_index = np.argmax(impulse_response)
    elif mode == "min":
        # Find the index of the maximum negative peak
        peak_index = np.argmin(impulse_response)
    elif mode == "abs":
        # Find the index of the maximum absolute peak
        peak_index = np.argmax(np.abs(impulse_response))
    else:
        raise ValueError("Invalid mode. Choose 'max', 'min', or 'abs'.")

    # Calculate the time to the peak in milliseconds
    time_to_peak_ms = (peak_index / sample_rate) * 1000

    return time_to_peak_ms

def record_with_playrec(output_audio, sample_rate, input_device, output_device, in_map, out_map,
                        blocksize=1024, latency='high'):
    """
    Play audio on a specific output device and record simultaneously from a single input device using playrec.

    :param output_audio: Numpy array containing the audio signal to play
    :param sample_rate: Sampling rate in Hz
    :param input_device: Input device ID to record from
    :param output_device: Output device ID to play audio
    :param blocksize: Frames per buffer. Large (e.g. 1024) avoids underruns/clicks; 0 lets
                      PortAudio choose. (The old value of 64 was ~1.3ms and clicked badly.)
    :param latency: PortAudio latency hint ('high' for robust buffering, 'low', or seconds).
    :return: Numpy array containing the recorded audio
    """
    _require(sd, 'sounddevice')
    try:
        # Play and record simultaneously
        recorded_audio = sd.playrec(
            output_audio,
            samplerate=sample_rate,
            input_mapping=in_map,
            output_mapping=out_map,
            device=[input_device, output_device],
            dtype='float32',
            blocksize=blocksize,
            latency=latency,
            blocking=True  # Ensure consistent data type
        )
        return recorded_audio
    except sd.PortAudioError as e:
        print(f"Error during playback/recording: {e}")
        return None

def compute_impulse_response_farina(input_signal, output_signal, sample_rate, f_start, f_end):
    """
    Compute the impulse response using the inverse filter deconvolution method (Farina technique).

    :param input_signal: Numpy array containing the input signal (sine sweep)
    :param output_signal: Numpy array containing the output signal (recorded audio)
    :param sample_rate: Sampling rate in Hz
    :param f_start: Starting frequency of the sweep in Hz
    :param f_end: Ending frequency of the sweep in Hz
    :return: Time array and impulse response
    """
    # --- Generate inverse filter ---
    duration = len(input_signal) / sample_rate  # Duration of the sweep in seconds
    t = np.linspace(0, duration, len(input_signal), endpoint=False)
    K = duration / np.log(f_end / f_start)
    L = 2 * np.pi * f_start * K
    exp_decay = np.exp(t / K)
    inverse_filter = input_signal[::-1] / exp_decay  # Reverse sweep and apply exponential decay

    # --- Perform deconvolution using FFT convolution ---
    ir = signal.fftconvolve(output_signal, inverse_filter, mode='full')
    ir = ir / np.max(np.abs(ir))  # Normalize the impulse response

    # --- Create time array for the impulse response ---
    time = np.linspace(0, len(ir) / sample_rate, len(ir), endpoint=False)

    return time, ir

def plot_ka_vs_f(a):

    """
    Plot the wave number (ka) against frequency (f) with a logarithmic X-axis using Plotly.
    Includes a horizontal line at ka = 1.

    :param a: Speaker radius in meters
    """
    # Frequency array from 20 Hz to 20,000 Hz with a resolution of 1 Hz
    f = np.arange(20, 20001, 1)

    # Calculate ka
    ka = ((2 * np.pi * f) / 343) * a

    # Create the plot
    fig = go.Figure()

    # Add the ka vs frequency line
    fig.add_trace(go.Scatter(x=f, y=ka, mode='lines', name=f"ka (a = {a} m)", line=dict(color='blue')))

    # Add a horizontal line at ka = 1
    fig.add_hline(y=1, line_dash="dash", line_color="red", annotation_text="ka = 1", annotation_position="top left")

    # Update layout for logarithmic X-axis
    fig.update_layout(
        title="Wave Number (ka) vs Frequency",
        xaxis=dict(
            title="Frequency (Hz)",
            type="log",  # Logarithmic scale
            tickmode="array",
            tickvals=[20, 100, 1000, 10000, 20000],
            ticktext=["20", "100", "1k", "10k", "20k"]
        ),
        yaxis=dict(
            title="ka",
            tickmode="auto",
            range=[0, 2]
        ),
        template="plotly_white"
    )

    # Show the plot
    fig.show()

def plot_total_magnitude_phase(freqs, total_magnitude, total_phase, 
                               nf_magnitude, nf_phase, 
                               ff_magnitude, ff_phase, 
                               tw_magnitude, tw_phase,
                               resample_n_points=0):
    """
    Plot the total magnitude, phase, and group delay responses along with the near-field, far-field, 
    and time-windowed responses in the same graphs using Plotly with resampling.

    :param freqs: Frequency array (in Hz)
    :param total_magnitude: Total magnitude response
    :param total_phase: Total phase response (in radians)
    :param nf_magnitude: Near-field magnitude response
    :param nf_phase: Near-field phase response (in radians)
    :param ff_magnitude: Far-field magnitude response
    :param ff_phase: Far-field phase response (in radians)
    :param tw_magnitude: Time-windowed magnitude response
    :param tw_phase: Time-windowed phase response (in radians)
    :param resample_n_points: Target number of points to display after resampling.
    """
    # Create subplots and wrap with FigureResampler
    fig_resampler = FigureResampler(
        make_subplots(
            rows=3, cols=1,
            subplot_titles=("Magnitude Response (dB)", "Phase Response (degrees)", "Group Delay (s)")
        )
    )

    # --- Magnitude Responses ---
    # Add total magnitude response trace
    fig_resampler.add_trace(
        go.Scatter(x=freqs, y=20 * np.log10(total_magnitude), mode='lines', name='Total Magnitude (dB)', line=dict(color='blue')),
        row=1, col=1
    )
    fig_resampler.add_trace(
        go.Scatter(x=freqs, y=20 * np.log10(nf_magnitude), mode='lines', name='Near-Field Magnitude (dB)', line=dict(color='green')),
        row=1, col=1
    )
    fig_resampler.add_trace(
        go.Scatter(x=freqs, y=20 * np.log10(ff_magnitude), mode='lines', name='Far-Field Magnitude (dB)', line=dict(color='red')),
        row=1, col=1
    )
    fig_resampler.add_trace(
        go.Scatter(x=freqs, y=20 * np.log10(tw_magnitude), mode='lines', name='Twitter Magnitude (dB)', line=dict(color='purple')),
        row=1, col=1
    )

    # --- Phase Responses ---
    fig_resampler.add_trace(
        go.Scatter(x=freqs, y=np.degrees(total_phase), mode='lines', name='Total Phase (degrees)', line=dict(color='blue')),
        row=2, col=1
    )
    fig_resampler.add_trace(
        go.Scatter(x=freqs, y=np.degrees(nf_phase), mode='lines', name='Near-Field Phase (degrees)', line=dict(color='green')),
        row=2, col=1
    )
    fig_resampler.add_trace(
        go.Scatter(x=freqs, y=np.degrees(ff_phase), mode='lines', name='Far-Field Phase (degrees)', line=dict(color='red')),
        row=2, col=1
    )
    fig_resampler.add_trace(
        go.Scatter(x=freqs, y=np.degrees(tw_phase), mode='lines', name='Time-Windowed Phase (degrees)', line=dict(color='purple')),
        row=2, col=1
    )

    # --- Group Delay Responses ---
    # Prepare frequencies and omega for group delay calculation (positive frequencies only)
    valid_indices_gd = np.where(freqs > 1e-9)[0] # Avoid zero or negative frequencies for omega
    
    if len(valid_indices_gd) < 2:
        print("Warning: Not enough positive frequency points to calculate group delay robustly.")
    else:
        freqs_gd = freqs[valid_indices_gd]
        omega_gd = 2 * np.pi * freqs_gd

        # Check if omega_gd is monotonic and has varying values for gradient calculation
        if len(omega_gd) < 2 or (len(omega_gd) >=2 and omega_gd[0] == omega_gd[-1] and np.all(omega_gd == omega_gd[0])) :
             print("Warning: Omega for group delay is constant or has too few points. Cannot calculate gradient robustly.")
        else:
            phases_for_gd = {
                'Total': (total_phase[valid_indices_gd], 'blue'),
                'Near-Field': (nf_phase[valid_indices_gd], 'green'),
                'Far-Field': (ff_phase[valid_indices_gd], 'red'),
                'Time-Windowed': (tw_phase[valid_indices_gd], 'purple')
            }

            for label, (phase_resp_rad_gd, color) in phases_for_gd.items():
                if len(phase_resp_rad_gd) == len(omega_gd) and len(omega_gd) >= 2:
                    unwrapped_phase_gd = np.unwrap(phase_resp_rad_gd)
                    group_delay_sec = -np.gradient(unwrapped_phase_gd, omega_gd)
                    fig_resampler.add_trace(
                        go.Scatter(x=freqs_gd, y=group_delay_sec, mode='lines', name=f'{label} Group Delay (s)', line=dict(color=color)),
                        row=3, col=1
                    )
                else:
                    print(f"Warning: Skipping group delay for {label} due to length mismatch or insufficient points after filtering.")

    # Update x-axis to be logarithmic for all subplots
    # Ensure log range is valid
    min_freq_log = 20
    max_freq_log = 20000
    
    log_range = [np.log10(min_freq_log), np.log10(max_freq_log)]

    fig_resampler.update_xaxes(type="log", title_text="Frequency (Hz)", range=log_range, row=1, col=1)
    fig_resampler.update_xaxes(type="log", title_text="Frequency (Hz)", range=log_range, row=2, col=1)
    fig_resampler.update_xaxes(type="log", title_text="Frequency (Hz)", range=log_range, row=3, col=1)

    # Update y-axes
    fig_resampler.update_yaxes(title_text="Magnitude (dB)", row=1, col=1, range=[-15, 30])
    fig_resampler.update_yaxes(title_text="Phase (degrees)", row=2, col=1)
    fig_resampler.update_yaxes(title_text="Group Delay (s)", row=3, col=1) # You might want to set a range here, e.g., range=[-0.01, 0.03]

    # Update layout
    fig_resampler.update_layout(
        title="Total, Near-Field, Far-Field, and Time-Windowed Responses (Resampled)",
        height=1800, 
        template="plotly_white",
        legend_title_text='Traces'
    )

    fig_resampler.show()

def average_magnitude_difference(magnitude1, magnitude2, sample_rate, freq_range):
    """
    Calculate the average difference between two magnitude responses within a specified frequency range.

    :param magnitude1: First magnitude response (in linear or dB scale)
    :param magnitude2: Second magnitude response (in linear or dB scale)
    :param sample_rate: Sampling rate in Hz
    :param freq_range: Tuple specifying the frequency range (start_freq, end_freq) in Hz
    :return: Average difference between the two magnitude responses within the specified range
    """
    # Calculate the frequency array manually
    n = len(magnitude1)
    freqs = np.linspace(0, sample_rate / 2, n)
    print("Len of freqs:", len(freqs))
    # Extract the indices corresponding to the specified frequency range
    start_freq, end_freq = freq_range
    indices = np.where((freqs >= start_freq) & (freqs <= end_freq))[0]

    # Calculate the sample-by-sample differences within the range
    differences = np.abs(magnitude1[indices] / magnitude2[indices])

    # Compute the average difference
    avg_difference = np.mean(differences)

    return avg_difference

def average_phase_difference(phase1, phase2, sample_rate, freq_range):
    """
    Calculate the average phase difference between two phase responses within a specified frequency range.

    :param phase1: First phase response (in radians)
    :param phase2: Second phase response (in radians)
    :param sample_rate: Sampling rate in Hz
    :param freq_range: Tuple specifying the frequency range (start_freq, end_freq) in Hz
    :return: Average phase difference (in degrees) between the two phase responses within the specified range
    """
    # Calculate the frequency array manually
    n = len(phase1)
    freqs = np.linspace(0, sample_rate / 2, n)

    # Extract the indices corresponding to the specified frequency range
    start_freq, end_freq = freq_range
    indices = np.where((freqs >= start_freq) & (freqs <= end_freq))[0]

    # Calculate the sample-by-sample phase differences within the range
    phase_diff = abs(np.degrees(phase1[indices] - phase2[indices]))  # Convert to degrees

    # Wrap the phase differences to the range [-180, 180]
    phase_diff = (phase_diff + 180) % 360 - 180

    # Compute the average phase difference
    avg_phase_diff = np.mean(phase_diff)

    return avg_phase_diff

def process_2_way_speaker(Hlf, Hhf, sample_rate, freqs):
    """
    Process the low-frequency (Hlf) and high-frequency (Hhf) transfer functions and combine them.
    Plot the magnitude and phase of the resulting transfer function.

    :param Hlf: Low-frequency transfer function (complex array)
    :param Hhf: High-frequency transfer function (complex array)
    :param sample_rate: Sampling rate in Hz
    """
    #hlf = irfft(Hlf, n=2*len(Hlf))
    #hhf = irfft(Hhf, n=2*len(Hhf))

    #Filters_____________________________________________________________________________________
    delayhf = get_delay_from_phase_difference(phase_difference_at_freq(np.angle(Hlf), np.angle(Hhf), 2000, sample_rate, debug=False),2000, debug=False)
    print("HF delay (ms):", delayhf)
    delay_filter = list(create_delay_filter(delayhf, sample_rate))
    a_cross_lp, a_cross_hp = create_crossover_filters(2000, sample_rate, 201, 1000, export_coeffs_to_cpp=True)
    
    # Combine the low-frequency and high-frequency transfer functions
    w, Hcrosslp = signal.freqz(a_cross_lp, [1], worN=len(Hlf), fs=sample_rate)
    w, Hcrosshp = signal.freqz(a_cross_hp, [1], worN=len(Hlf), fs=sample_rate)
    w, Hdelay = signal.freqz(delay_filter[0], delay_filter[1], worN=len(Hlf), fs=sample_rate)
    
    Hcross_total = Hcrosslp + Hcrosshp

    
    #Process____________________________________________________________________________________
 
    Hlf_processed = Hlf*Hcrosslp*Hdelay
    Hhf_processed = Hhf*Hcrosshp*1.5

    Houtput = Hlf_processed + Hhf_processed
    houtput = irfft(Houtput, n=2*len(Houtput)-1)
    print("houtput len:", len(houtput))

    inv_filter_b, inv_filter_a, Hmag_inv, Hprelp, Hprehp = firls_inverse_magnitude_filter(Houtput, freqs, sample_rate, 411, 30, 18200, export_coeffs_to_cpp=True)
    Hgd_inv, hgd_inv = inverse_gd_filter(Houtput*Hmag_inv, freqs, sample_rate)

    Houtput_no_inv = Houtput #Only to plots
    Houtput = Houtput * Hmag_inv * Hgd_inv

    # Compute magnitude and phase
    magnitude = np.abs(Houtput)
    phase = np.angle(Houtput)
    #___________________________________________________________________________________________
    # Plot magnitude and phase
    mags_to_graph = {
        "Magnitude": magnitude,
        "Houtput_no_inv": np.abs(Houtput_no_inv),
        "Hlf": np.abs(Hlf),
        "Hhf": np.abs(Hhf),
        "Hlf_processed": np.abs(Hlf_processed),
        "Hhf_processed": np.abs(Hhf_processed),
        "Hcrosslp": np.abs(Hcrosslp),
        "Hcrosshp": np.abs(Hcrosshp),
        "Hcross_total": np.abs(Hcross_total),
        "Hmag_inv": np.abs(Hmag_inv),
        "Hgd_inv": np.abs(Hgd_inv),
        "Hprelp": np.abs(Hprelp),
        "Hprehp": np.abs(Hprehp)
    }
    
    phases_to_graph = {
        "Phase": phase,
        "Houtput_no_inv": np.angle(Houtput_no_inv),
        "Hlf": np.angle(Hlf),
        "Hhf": np.angle(Hhf),
        "Hlf_processed": np.angle(Hlf_processed),
        "Hhf_processed": np.angle(Hhf_processed),
        "Hmag_inv": np.angle(Hmag_inv),
        "Hgd_inv": np.angle(Hgd_inv)
    }
    

    plot_multiple_magnitude_phase(freqs, mags_to_graph, phases_to_graph, resample=True)

def convert_dict_arrays_to_float32(*dictionaries):
    """
    Converts NumPy array values within the given dictionaries to float32 data type in-place.

    Args:
        *dictionaries: A variable number of dictionaries to process.
    """
    for D_in in dictionaries:
        if not isinstance(D_in, dict):
            print(f"Warning: Provided item {type(D_in)} is not a dictionary. Skipping.")
            continue
        for key, value in D_in.items():
            if isinstance(value, np.ndarray):
                if value.dtype != np.float32:
                    D_in[key] = value.astype(np.float32)

def get_var_name(var):
    """
    Get the variable name of a given variable from the local scope using the inspect module.

    :param var: The variable whose name is to be retrieved.
    :return: The name of the variable as a string, or None if not found.
    """
    frame = inspect.currentframe().f_back
    args, _, _, local_vars = inspect.getargvalues(frame)
    for arg_name in args:
        if local_vars[arg_name] is var:
            return arg_name
    return None


def plot_multiple_magnitude_phase(freqs, magnitudes_dict, phases_dict, resample=False, single_freq_vector=True):
    """
    Plot multiple magnitude, phase, and optionally calculate and plot group delay responses 
    in separate subplots with a logarithmic frequency axis.
    Uses dictionary keys for trace labels, prefixed accordingly.

    :param freqs: Frequency array (in Hz) or list of arrays if single_freq_vector=False
    :param magnitudes_dict: Dictionary where keys are labels and values are magnitude responses (numpy arrays)
    :param phases_dict: Dictionary where keys are labels and values are phase responses (numpy arrays, in radians)
    :param resample: Use plotly-resampler if True
    :param single_freq_vector: If True, use freqs for all traces; if False, use freqs[n] for each trace
    """
    num_subplots = 3
    subplot_titles = ["Magnitude Responses (dB)", "Phase Responses (degrees)", "Group Delay (s)"]
    if resample:
        fig = FigureResampler(make_subplots(rows=num_subplots, cols=1, 
                                        subplot_titles=subplot_titles))
    else:
        fig = make_subplots(rows=num_subplots, cols=1, 
                            subplot_titles=subplot_titles)

    # Magnitude responses
    for n, (label, magnitude_resp) in enumerate(magnitudes_dict.items()):
        x_freq = freqs if single_freq_vector else freqs[n]
        fig.add_trace(
            go.Scatter(
                x=x_freq, 
                y=20 * np.log10(magnitude_resp), 
                mode='lines', 
                name=f"Mag {label}"
            ),
            row=1, col=1
        )

    # Phase responses
    for n, (label, phase_resp_rad) in enumerate(phases_dict.items()):
        x_freq = freqs if single_freq_vector else freqs[n]
        fig.add_trace(
            go.Scatter(
                x=x_freq, 
                y=np.degrees(phase_resp_rad), 
                mode='lines', 
                name=f"Phase {label}"
            ),
            row=2, col=1
        )

    # Group delay responses
    for n, (label, phase_resp_rad) in enumerate(phases_dict.items()):
        x_freq = freqs if single_freq_vector else freqs[n]
        unwrapped_phase = np.unwrap(phase_resp_rad)
        omega = 2 * np.pi * np.array(x_freq)
        # Avoid division by zero in omega
        group_delay_sec = -np.gradient(unwrapped_phase, omega)
        fig.add_trace(
            go.Scatter(
                x=x_freq, 
                y=group_delay_sec, 
                mode='lines', 
                name=f"GD {label}"
            ),
            row=3, col=1
        )

    log_freq_min = np.log10(20)
    log_freq_max = np.log10(20e3)
    for i in range(1, num_subplots+1):
        fig.update_xaxes(type="log", title_text="Frequency (Hz)", 
                         range=[log_freq_min, log_freq_max], 
                         row=i, col=1)

    fig.update_yaxes(title_text="Magnitude (dB)", row=1, col=1)
    fig.update_yaxes(title_text="Phase (degrees)", row=2, col=1)
    fig.update_yaxes(title_text="Group Delay (s)", range=[-0.01, 0.03], row=3, col=1)

    fig.update_layout(
        title="Multiple Magnitude, Phase, and Group Delay Responses",
        height=600 * num_subplots,
        template="plotly_white",
        legend_title_text='Traces'
    )

    fig.show()

def create_delay_filter(delay_us, sample_rate):
    """
    Create filter coefficients for a delay in microseconds.

    :param delay_us: Delay in microseconds
    :param sample_rate: Sampling rate in Hz
    :return: Tuple (b, a) filter coefficients
    """
    # Convert delay from microseconds to samples
    delay_samples = int((delay_us / 1e6) * sample_rate)

    # Create filter coefficients
    b = [0] * delay_samples + [1]  # Delay by `delay_samples` with a single impulse
    a = [1]  # No feedback

    return b, a

def calculate_delay_difference_us(ir1, ir2, sample_rate, debug=False):
    """
    Calculate the difference in delay (in microseconds) between the absolute maximum peaks of two impulse responses.

    :param ir1: First impulse response (numpy array)
    :param ir2: Second impulse response (numpy array)
    :param sample_rate: Sampling rate in Hz
    :return: Difference in delay (in microseconds)
    """
    # Find the indices of the absolute maximum peaks
    peak_index_ir1 = np.argmax(np.abs(ir1))
    peak_index_ir2 = np.argmax(np.abs(ir2))

    # Calculate the time of the peaks in microseconds
    time_peak_ir1_us = (peak_index_ir1 / sample_rate) * 1e6
    time_peak_ir2_us = (peak_index_ir2 / sample_rate) * 1e6

    # Calculate the difference in delay
    delay_difference_us = abs(time_peak_ir1_us - time_peak_ir2_us)

    if debug:
        print(f"Peak index IR1: {peak_index_ir1}, Time IR1: {time_peak_ir1_us:.2f} µs")
        print(f"Peak index IR2: {peak_index_ir2}, Time IR2: {time_peak_ir2_us:.2f} µs")
        print(f"Delay difference: {delay_difference_us:.2f} µs")

    return delay_difference_us

def plot(arrays, labels=None):
    """
    Plot a list of arrays on the same graph using Plotly.

    :param arrays: List of arrays to plot
    :param labels: List of labels for each array (optional)
    """
    fig = go.Figure()

    for i, array in enumerate(arrays):
        label = labels[i] if labels else f"Array {i+1}"
        fig.add_trace(go.Scatter(y=array, mode='lines', name=label))

    # Update layout
    fig.update_layout(
        title="Simple Plot",
        xaxis_title="Index",
        yaxis_title="Value",
        template="plotly_white"
    )

    # Show the plot
    fig.show()

def create_crossover_filters(crossover, fs, numtaps, trans_width, export_coeffs_to_cpp=False):
    """
    Create a crossover filter using the Parks-McClellan method.

    :param crossover: Crossover frequency in Hz
    :param fs: Sampling frequency in Hz
    :param numtaps: Number of filter taps (filter length)
    :param trans_width: Transition width in Hz
    :return: Tuple (lp_taps, hp_taps) containing low-pass and high-pass filter coefficients
    """
    # Define frequency bands for the low-pass filter
    cross_drift = trans_width / 2  # Drift to avoid overlap
    f_cross_lp = crossover - cross_drift
    lp_bands = [0, f_cross_lp, f_cross_lp + trans_width, fs / 2]
    lp_gains = [1, 0]  # Passband = 1, stopband = 0

    # Define frequency bands for the high-pass filter
    f_cross_hp = crossover + cross_drift
    hp_bands = [0, f_cross_hp - trans_width, f_cross_hp, fs / 2]
    hp_gains = [0, 1]  # Stopband = 0, passband = 1

    # Design filters using Parks-McClellan algorithm
    try:
        lp_taps = signal.remez(numtaps, lp_bands, lp_gains, fs=fs)
        hp_taps = signal.remez(numtaps, hp_bands, hp_gains, fs=fs)
    except Exception as e:
        raise RuntimeError(f"Filter design failed: {e}")

    if export_coeffs_to_cpp:
        write_cpp_float_arrays_to_header([lp_taps, hp_taps], ['lp', 'hp'], 'crossover_coefs.hpp')

    return lp_taps, hp_taps

def get_delay_from_phase_difference(phase_difference_deg, frequency, debug=False):
    """
    Calculate the delay in microseconds corresponding to a given phase difference in degrees.

    :param phase_difference_deg: Phase difference in degrees
    :param frequency: Frequency of the signal in Hz
    :param debug: Boolean to enable debugging output
    :return: Delay in microseconds
    """
    # Convert phase difference from degrees to radians
    phase_difference_rad = np.radians(phase_difference_deg)

    # Calculate the period of the signal in seconds
    period_s = 1 / frequency

    # Calculate the delay in seconds
    delay_s = (phase_difference_rad / (2 * np.pi)) * period_s

    # Convert delay to microseconds
    delay_us = delay_s * 1e6

    if debug:
        print(f"Phase difference: {phase_difference_deg} degrees")
        print(f"Frequency: {frequency} Hz")
        print(f"Period: {period_s:.6f} seconds")
        print(f"Delay: {delay_us:.2f} µs")

    return delay_us

def half2full_freqz(positive_freq_response):
    """
    Reconstruct the full frequency response (positive and negative frequencies)
    from the positive frequency response.

    :param positive_freq_response: Numpy array containing the positive frequency response
    :return: Numpy array containing the full frequency response
    """
    positive_freqs = positive_freq_response[:-1]  # Exclude the last element (Nyquist frequency)
    negative_freqs = np.conj(positive_freqs[::-1])  # Mirror and conjugate
    full_freq_response = np.concatenate((positive_freq_response, negative_freqs))
    return full_freq_response

def phase_difference_at_freq(phase1_rad, phase2_rad, frequency_hz, sample_rate, debug=False):
    """
    Calculates the phase difference in degrees at a specific frequency 
    from two phase responses in radians.

    :param phase1_rad: First phase response (numpy array, in radians)
    :param phase2_rad: Second phase response (numpy array, in radians)
    :param frequency_hz: The specific frequency in Hz to find the phase difference
    :param sample_rate: The sampling rate in Hz
    :return: Phase difference in degrees at the specified frequency
    """
    # Ensure phase arrays are of the same length
    if len(phase1_rad) != len(phase2_rad):
        raise ValueError("Phase response arrays must have the same length.")

    # Create the frequency axis for the phase responses
    # This assumes the phase responses cover the range [0, sample_rate/2]
    n_points = len(phase1_rad)
    freq_axis = np.linspace(0, sample_rate / 2, n_points)

    # Find the index closest to the target frequency
    # np.argmin finds the index of the minimum value, 
    # which corresponds to the frequency in freq_axis closest to frequency_hz
    idx = np.argmin(np.abs(freq_axis - frequency_hz))

    # Get the phase values at the found index
    phase1_at_freq_rad = phase1_rad[idx]
    phase2_at_freq_rad = phase2_rad[idx]

    # Calculate the phase difference in radians
    phase_diff_rad = phase1_at_freq_rad - phase2_at_freq_rad

    # Convert phase difference to degrees
    phase_diff_deg = np.degrees(phase_diff_rad)
    
    # Optionally, wrap the phase difference to the range [-180, 180] degrees
    # phase_diff_deg = (phase_diff_deg + 180) % 360 - 180

    if debug:
        print(f"Closest frequency found: {freq_axis[idx]:.2f} Hz for target {frequency_hz} Hz")
        print(f"Phase1 at {freq_axis[idx]:.2f} Hz: {np.degrees(phase1_at_freq_rad):.2f} degrees")
        print(f"Phase2 at {freq_axis[idx]:.2f} Hz: {np.degrees(phase2_at_freq_rad):.2f} degrees")
    
    return phase_diff_deg

def smooth_octave_average(freq, response, octave_resolution):
    """
    Apply a moving average filter to a magnitude response with a constant octave resolution.
    
    Parameters:
    - freq: Array of frequency points (sorted in ascending order)
    - response: Array of magnitude response values corresponding to freq
    - octave_resolution: Fractional octave resolution (e.g., 1/3 for third-octave smoothing)
    
    Returns:
    - smoothed_response: Array of smoothed magnitude response values
    """
    # Calculate the frequency ratio for the given octave resolution
    ratio_edge = 2 ** (octave_resolution / 2)
    
    # Compute lower and upper frequency limits for all points at once
    freq_lower = freq / ratio_edge
    freq_upper = freq * ratio_edge
    
    # Find the start and end indices for all smoothing bands
    start_indices = np.searchsorted(freq, freq_lower, side='left')
    end_indices = np.searchsorted(freq, freq_upper, side='right')
    
    # Compute the cumulative sum of the response (with a 0 prepended for convenience)
    cumsum = np.concatenate(([0], np.cumsum(response)))
    
    # Compute the sum and count for each band using cumulative sum
    band_sums = cumsum[end_indices] - cumsum[start_indices]
    band_counts = end_indices - start_indices
    
    # Avoid division by zero and compute the smoothed response
    smoothed_response = np.where(band_counts > 0, band_sums / band_counts, response)
    
    return smoothed_response

def firls_inverse_magnitude_filter(H, freqs, sample_rate, numtaps, f_start, f_end, export_coeffs_to_cpp=None):
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

        #prefilter
    # Parks-McClellan (remez) prefilter
    Hprelp_taps = signal.remez(61, [0, 15e3, 15e3 + 6.5e3, 0.5*sample_rate], [1, 0], fs=sample_rate)
    w, Hprelp = signal.freqz(Hprelp_taps, [1], worN=len(H), fs=sample_rate)
    # Design a high-pass filter with cutoff around 30 Hz using firls
    # We'll use a transition band from 15 Hz to 30 Hz (stopband to passband)
    # Design a 4th-order Butterworth high-pass filter at 25 Hz
    hp_order = 4
    hp_cutoff = 25.0
    Wn = hp_cutoff / (0.5 * sample_rate)
    Hprehp_b, Hprehp_a = signal.butter(hp_order, Wn, btype='highpass', analog=False)
    w, Hprehp = signal.freqz(Hprehp_b, Hprehp_a, worN=len(H), fs=sample_rate)
    # Second-order-section (biquad) form of the SAME high-pass. A single high-order
    # direct-form IIR is numerically unstable in float32 (the plugin's sample type) at these
    # low cutoffs -- the poles cluster near z=1 and the recursion overflows to NaN. The C++
    # plugin therefore applies the high-pass as a cascade of biquads, so export it as SOS.
    Hprehp_sos = signal.butter(hp_order, Wn, btype='highpass', output='sos', analog=False)

    # Build weights for firls: one weight per band using contiguous pairs of freqs
    # Below 20 Hz -> weight_low, 20-300 Hz -> weight_high, above 300 Hz -> weight_med
    weight_low=1e-6
    weight_med=0.3
    weight_high=1.0
    weights = None
    if len(freqs) >= 2:
        nb = len(freqs) // 2
        wlist = []
        for i in range(nb):
            f0 = freqs[2 * i]
            f1 = freqs[2 * i + 1]
            center = 0.5 * (f0 + f1)
            if center < 40.0:
                wlist.append(float(weight_low))
            elif center <= 200.0:
                wlist.append(float(weight_high))
            else:
                wlist.append(float(weight_med))
        weights = wlist

    inverse_filter = signal.firls(
        numtaps,
        freqs,
        np.abs(1/H),  # Target magnitude
        weight=weights,
        fs=sample_rate
    )

    #inv_filter_b, inv_filter_a = signal.normalize(inverse_filter, 1)

    inv_filter_b = inverse_filter
    inv_filter_a = [1]

    w, Hinv = signal.freqz(inv_filter_b, inv_filter_a, worN=len(H), fs=sample_rate)
    # inv_filter_b = signal.firls(
    #     numtaps,
    #     freqs,
    #     np.abs(Hinv),  # Normalize the magnitude response
    #     fs=sample_rate
    # )
    # inv_filter_a = [1]

    # inv_filter_b, inv_filter_a = signal.normalize(inv_filter_b, inv_filter_a)

    w, Hinv = signal.freqz(inv_filter_b, inv_filter_a, worN=len(H), fs=sample_rate)

    Hinv *= (Hprelp*Hprehp)

    if export_coeffs_to_cpp == True:
        # Use default filename when flag is True. Export the high-pass in two forms: the raw
        # b/a (HPreHpNum/HPreHpDen, reference only) and the second-order sections HPreHpSos --
        # 6 floats per biquad, [b0,b1,b2,a0,a1,a2] -- which is what the C++ plugin actually runs
        # (a direct-form high-order IIR is float32-unstable; a biquad cascade is not).
        write_cpp_float_arrays_to_header(
            [inv_filter_b, Hprehp_b, Hprehp_a, Hprehp_sos.reshape(-1), Hprelp_taps],
            ['InvFilter', 'HPreHpNum', 'HPreHpDen', 'HPreHpSos', 'HPreLpTaps'],
            'filters.hpp')

    return inv_filter_b, inv_filter_a, Hinv, Hprelp, Hprehp
    # except Exception as e:
    #     raise RuntimeError(f"Inverse filter design failed: {str(e)}")

def inverse_gd_filter(H, freqs, low_freq_limit=100):

    unwrapped_phase = np.unwrap(np.angle(H))
    omega = 2 * np.pi * freqs
    gd = np.gradient(unwrapped_phase, omega)
    gd = trim_group_delay(gd, omega, 50)
    phase = -integrate.cumulative_trapezoid(gd, omega, initial=0)

    # Adjust integration constant / Example: Set phase at omega=0 to match original phase (or set to 0 if desired)
    phase += unwrapped_phase[0]  # Align with original phase at omega=0

    Hinv = 1 * np.exp(1j * phase) #MAgnitude is untouched, so it it always one.
    hinv = irfft(Hinv)  # Inverse FFT to get time-domain filter coefficients

    return Hinv, hinv

def complex_fir_ls(N, f, desired_complex, fs=48000, weights=None):
    """
    Design complex FIR with least-squares fit to arbitrary complex response.
    
    Parameters:
    - N: int, number of taps (can be even or odd)
    - f: array, frequency points (Hz, monotonic, 0 to fs/2)
    - desired_complex: array, complex desired response at each f (same len as f)
    - fs: float, sample rate
    - weights: array or None, optional weights per frequency (same len as f)
    
    Returns:
    - h: complex array, FIR coefficients
    """
    M = len(f)
    omega = 2 * np.pi * f / fs  # rad/sample
    
    # Build the system matrix A: M x N, A[k, n] = exp(-j * omega[k] * n)
    n = np.arange(N)
    A = np.exp(-1j * omega[:, np.newaxis] @ n[np.newaxis, :])
    
    d = desired_complex  # target vector
    
    if weights is not None:
        # Weight the equations
        W = np.diag(np.sqrt(weights))
        A = W @ A
        d = W @ d
    
    # Solve least-squares: h = argmin ||A h - d||^2
    h, _, _, _ = np.linalg.lstsq(A, d, rcond=None)
    
    return h

def zero_gd_inverse_transfer(Hin, max_gain=100.0):
    """
    Build a frequency-domain transfer function H whose magnitude is the regularized inverse
    of the input transfer function Hin and whose phase is constant (linear with zero slope),
    which results in zero group delay across the provided frequency grid.

    Args:
        Hin (np.ndarray): Complex frequency response (positive frequencies, 0..fs/2)
        freqs (np.ndarray): Frequency grid (Hz) matching Hin
        sample_rate (float): Sampling rate in Hz
        max_gain (float): Maximum allowed magnitude for the inverse (clip to avoid instability)

    Returns:
        H (np.ndarray): Complex frequency response with |H| = 1/|Hin| (clipped) and constant phase
    """
    # Regularized inverse magnitude
    mag = np.abs(Hin)
    eps = 1e-10
    inv_mag = 1.0 / np.maximum(mag, eps)
    inv_mag = np.clip(inv_mag, a_min=0.0, a_max=max_gain)

    # Constant phase -> linear phase with zero slope -> zero group delay
    phase_const = 0.0  # radians
    H = inv_mag * np.exp(1j * float(phase_const))

    return H

def trim_group_delay(group_delay, omega, cutoff_hz):
    """
    Replaces the group delay response with zeros from 0 Hz up to a specified cutoff frequency.

    Args:
        group_delay (np.ndarray): The original group delay response array.
        omega (np.ndarray): The corresponding angular frequency array (2 * pi * f).
                            Must be sorted in ascending order.
        cutoff_hz (float): The cutoff frequency in Hz. Below this frequency,
                           the group delay will be set to zero.

    Returns:
        np.ndarray: A new group delay array with the specified portion zeroed out.
    """
    if not isinstance(group_delay, np.ndarray) or not isinstance(omega, np.ndarray):
        raise TypeError("group_delay and omega must be NumPy arrays.")
    if group_delay.shape != omega.shape:
        raise ValueError("group_delay and omega arrays must have the same shape.")
    if len(omega) > 1 and not np.all(np.diff(omega) >= 0):
        raise ValueError("The 'omega' array must be sorted in ascending order.")

    # Calculate the cutoff angular frequency
    cutoff_omega = 2 * np.pi * cutoff_hz

    # Find the index corresponding to the cutoff frequency.
    # np.searchsorted finds the index where cutoff_omega would be inserted
    # to maintain the sorted order. All indices before this are below the cutoff.
    cutoff_index = np.searchsorted(omega, cutoff_omega, side='right')

    # Create a copy of the group delay array to avoid modifying the original
    modified_gd = group_delay.copy()

    # Set the group delay to zero from the beginning up to the cutoff index
    modified_gd[:cutoff_index] = 0

    return modified_gd

import os

def write_cpp_float_arrays_to_header(lists, names, filename):
    """
    Writes each list in 'lists' as a float array in a C++ header file at the specified location.

    Args:
        lists (list of lists): Each inner list will become a float array.
        names (list of str): Variable names for each array.
        output_path (str): Directory to save the header file (can use '~' for home).
        filename (str): Name of the header file (e.g., 'arrays.h').
    """
    import os
    import platform

    # Use current working directory for output
    output_path = os.getcwd()

    # Determine OS-specific line ending (though most compilers handle \n fine)
    system = platform.system().lower()
    if 'windows' in system:
        line_ending = '\r\n'
    else:
        line_ending = '\n'

    # Start header content
    header_lines = [f'#pragma once{line_ending}']

    # Write arrays with fixed 8 decimal precision and trailing 'f'
    for arr, name in zip(lists, names):
        # Ensure iterable numeric
        arr_str = ', '.join(f'{float(x):.8f}f' for x in arr)
        header_lines.append(f'float {name}[{len(arr)}] = {{ {arr_str} }};{line_ending}')

    header_content = ''.join(header_lines)
    full_path = os.path.join(output_path, filename)

    # Write (overwrite) file
    with open(full_path, 'w', newline='') as f:  # newline='' to avoid translation
        f.write(header_content)

    print(f'C++ header file written to: {full_path}')

