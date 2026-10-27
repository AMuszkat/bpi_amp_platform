# The near-field measurement method

A loudspeaker's *free-field* (anechoic) response is what you want to design filters against.
In a normal room you can't measure it directly at low frequencies. At a typical 0.5–1 m
distance, the first floor or wall reflection arrives a few milliseconds after the direct
sound, and the impulse response has to be cut off (*gated*) before that reflection. A gate
T seconds long can't resolve anything below roughly 1/T, so a 5 ms gate gives nothing
trustworthy under about 200 Hz.

The near-field method fills in that missing low end with a second measurement taken very
close to the woofer cone.

## The three measurements

| Slot | Mic position | What it's good for |
|---|---|---|
| **NF woofer** (`ir_nr_wf.wav`) | a few mm from the woofer's dust cap | Low frequencies. The direct sound is so much louder than any reflection that the room hardly matters. The gate can be long (default 100 ms before / 250 ms after the peak). Above roughly f ≈ c/(2π·a), i.e. ka ≈ 1 (about 1.4 kHz for a cone radius a = 4 cm), the mic hears only part of the cone and the reading stops being representative. |
| **FF woofer** (`ir_ff_wf.wav`) | on axis at listening distance *r* | Mid/high frequencies. Short gate (default 20 ms before / 6 ms after) to cut the room out; valid only above ~1/gate. |
| **FF tweeter** (`ir_ff_tw.wav`) | on axis at *r* | The tweeter's response, gated the same way. |

For the tweeter a short gate is enough: its useful range starts well above the frequency
the gate limits you to.

## Splicing near and far field

`compute_total_far_field_response` (in `python_dsp_tools/nearfield/lib.py`) joins the two
woofer responses at a **matching frequency `fmatch`**, a frequency where both are valid:

- **Below `fmatch`**, the near-field magnitude is used, scaled so it equals the far-field
  magnitude *at* `fmatch`. Its phase is offset by the NF/FF phase difference at `fmatch`.
- **Above `fmatch`**, the far-field magnitude and phase are used unchanged.

Theory predicts the NF-to-FF level ratio from the radiating radius *a* and the distance *r*:
the pressure ratio is about a/(2r) for a piston in half space. The implementation *measures*
the ratio at `fmatch` instead. That is more robust to mic placement, and the theoretical
factors are kept in the code only as comments. In the current code `a` and `r` are
therefore documentation of the rig, not inputs that change the result.

Choosing `fmatch`: it must lie above the far-field gate's lower limit and below the
near-field upper limit. The measurement GUI makes both gates and the splice visible, so you
can see the two curves overlap around `fmatch`. The original scripts used 1350 Hz with a 4 cm
woofer radius, which is right at the ka ≈ 1 limit. If the far-field gate allows it, a
matching point somewhat lower is safer.

The result is normalised to 0 dB at 1 kHz. The tuning GUI applies the **same** normalisation
gain to the tweeter, so the woofer/tweeter level relationship survives.

## What gets saved

The measurement GUI saves the three raw captures and two cleaned-up results into the data
folder:

| File | Contents |
|---|---|
| `ir_result_wf.wav` | The spliced woofer response, inverse-FFT'd back to an impulse response: the deliverable of the method |
| `ir_result_tw.wav` | The **gated** tweeter response as an impulse response |

The raw `ir_ff_tw.wav` still contains room reflections, plus the long noise tail of the
sweep deconvolution (Farina's method boosts high-frequency noise there). Without a gate
that tail looks like HF content above the tweeter's real roll-off. That's why the tuning GUI
prefers `ir_result_tw.wav`.

## Practical notes

- **One interface for input and output.** If the mic input and the speaker output are
  different devices, their sample clocks drift apart during the sweep. The result is clicks
  and a smeared response, getting worse with sweep length. The measurement GUI warns about
  this.
- **Playback latency.** A buffered playback path (network audio, Bluetooth, a streaming
  server) delays the sound. If the delay exceeds the sweep's silent padding, the end of the
  sweep (the highest frequencies) falls outside the recording, and the response looks
  band-limited. Set **Extra record (ms)** to at least that latency.
- **Level.** Keep the digital level moderate (the default −6 dBFS). Get the SPL from the
  amplifier's gain, not from a hot digital signal.
