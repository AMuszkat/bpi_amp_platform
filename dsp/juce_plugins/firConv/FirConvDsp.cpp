// The audio processing of firConv: the gain, the pre-LP FIR, the pre-HP biquad cascade, the
// inverse FIR, and the level-matched bypass (README.md, "Bypass").
//
// Compiled into the complete and headless builds only. The control build, which runs in the
// controller app as a remote control, leaves this file out (see DSP in CMakeLists.txt and
// juce_plugins/cmake/SpeakerPlugin.cmake), and with it the coefficient header.

#include "PluginProcessor.h"
#include "filters.hpp"  // InvFilter[], HPreLpTaps[], HPreHpSos[] (+ HPreHpNum/Den reference)

#include <algorithm>
#include <cmath>
#include <cstring>
#include <iterator>

//==============================================================================
void FirConvProcessor::prepareToPlay (double newSampleRate, int samplesPerBlock)
{
    // Everything is allocated here. The gain and the biquads work sample by sample and the
    // FIRs in chunks of the prepared size, so a block of any size is processed without
    // allocating; a host that prepares before it has a device (sample rate or block size 0)
    // gets sane values.
    juce::dsp::ProcessSpec spec;
    spec.sampleRate = newSampleRate > 0.0 ? newSampleRate : 48000.0;
    spec.maximumBlockSize = (juce::uint32) (samplesPerBlock > 0 ? samplesPerBlock : 512);
    spec.numChannels = 1; // mono in/out

    // Gain: the "gain" parameter, applied at the next block, at once on the first block and
    // over gainRampSeconds after that.
    gain.prepare(spec);
    gain.setRampDurationSeconds (gainRampSeconds);
    gain.setGainDecibels(0.0f);
    appliedGainDb = std::numeric_limits<float>::quiet_NaN();

    // Pre-LP FIR (remez low-pass): rolls the correction off above the driver's usable band.
    preLp.prepare (HPreLpTaps, (int) (sizeof(HPreLpTaps) / sizeof(HPreLpTaps[0])), (int) spec.maximumBlockSize);

    // Pre-HP: Butterworth high-pass, applied as a cascade of biquads, in double.
    loadSosCascade (HPreHpSos, (int) (sizeof(HPreHpSos) / sizeof(HPreHpSos[0])));

    // Inverse FIR (least-squares magnitude inverse of the summed driver response).
    inv.prepare (InvFilter, (int) (sizeof(InvFilter) / sizeof(InvFilter[0])), (int) spec.maximumBlockSize);

    // The bypass: the gained input delayed as the two FIRs delay it. Both are linear phase
    // (symmetric taps, which the tuning GUI exports), so each delays by (N - 1) / 2 samples.
    constexpr int numPreLp = (int) (sizeof (HPreLpTaps) / sizeof (HPreLpTaps[0]));
    constexpr int numInv = (int) (sizeof (InvFilter) / sizeof (InvFilter[0]));
    jassert (std::equal (HPreLpTaps, HPreLpTaps + numPreLp, std::reverse_iterator<const float*> (HPreLpTaps + numPreLp)));
    jassert (std::equal (InvFilter, InvFilter + numInv, std::reverse_iterator<const float*> (InvFilter + numInv)));
    dryDelay.prepare ((numPreLp - 1) / 2 + (numInv - 1) / 2);
    dryScratch.assign (spec.maximumBlockSize, 0.0f);
    bypassFade.prepare (spec.sampleRate, bypassFadeSeconds);

    reset();
}

void FirConvProcessor::loadSosCascade (const float* sos, int numFloats)
{
    const int nSections = numFloats / 6;  // 6 coeffs per biquad: [b0,b1,b2,a0,a1,a2]
    preHp.clear();
    preHp.reserve ((size_t) nSections);
    for (int s = 0; s < nSections; ++s)
    {
        // Normalised by a0 as juce::dsp::IIR::Coefficients does (times 1 / a0); scipy's
        // sections have a0 == 1, so these are the float values exactly.
        const float* c = sos + 6 * s;
        const double a0Inv = 1.0 / (double) c[3];
        preHp.push_back ({ c[0] * a0Inv, c[1] * a0Inv, c[2] * a0Inv, c[4] * a0Inv, c[5] * a0Inv, 0.0, 0.0 });
    }
}

void FirConvProcessor::reset()
{
    gain.reset();
    preLp.reset();
    for (auto& q : preHp)
        q.s1 = q.s2 = 0.0;
    inv.reset();
    dryDelay.reset();
    bypassFade.reset();
}

void FirConvProcessor::processPreHp (float* samples, int numSamples) noexcept
{
    // Sample by sample through every section, so the signal between sections stays double
    // without a buffer. TDF-II, the equations of juce::dsp::IIR::Filter. No snapToZero() at the
    // end of the block as that filter's process() does (on x86 only; elsewhere it is a no-op):
    // processBlock's ScopedNoDenormals already flushes denormals, double ones too, on x86_64
    // (MXCSR FTZ and DAZ) and aarch64 (FPCR.FZ), and setting a state below 1e-8 to zero is a
    // step the poles would carry to the output.
    for (int i = 0; i < numSamples; ++i)
    {
        double v = samples[i];

        for (auto& q : preHp)
        {
            const double y = q.b0 * v + q.s1;
            q.s1 = q.b1 * v - q.a1 * y + q.s2;
            q.s2 = q.b2 * v - q.a2 * y;
            v = y;
        }

        samples[i] = (float) v;
    }
}

//==============================================================================
void FirConvProcessor::processBlock (juce::AudioBuffer<float>& buffer, juce::MidiBuffer&)
{
    process (buffer, false);
}

void FirConvProcessor::processBlockBypassed (juce::AudioBuffer<float>& buffer, juce::MidiBuffer&)
{
    process (buffer, true);
}

void FirConvProcessor::process (juce::AudioBuffer<float>& buffer, bool bypassed) noexcept
{
    juce::ScopedNoDenormals noDenormals;

    // Audio thread: nothing here allocates, locks or makes a syscall.
    const float gainDb = gainParam->load (std::memory_order_relaxed);

    if (! juce::exactlyEqual (gainDb, appliedGainDb))
    {
        const bool first = std::isnan (appliedGainDb);
        gain.setGainDecibels (gainDb);

        if (first)
            gain.reset();          // the first block after prepareToPlay starts at the gain, no ramp

        appliedGainDb = gainDb;
    }

    bypassFade.setBypassed (bypassed);

    if (buffer.getNumChannels() == 0 || dryScratch.empty())
        return;

    // Mono, in place on channel 0, in chunks of the prepared size (the dry path's scratch).
    float* const samples = buffer.getWritePointer (0);
    const int numSamples = buffer.getNumSamples();
    const int chunk = (int) dryScratch.size();

    for (int start = 0; start < numSamples; start += chunk)
        processChunk (samples + start, juce::jmin (chunk, numSamples - start));
}

void FirConvProcessor::processChunk (float* samples, int numSamples) noexcept
{
    // gain -> pre-LP (FIR) -> pre-HP (IIR biquad cascade) -> inverse (FIR): the processed path.
    // The bypassed path: the same gain, then the FIRs' delay. Both run whatever the bypass is.
    auto block = juce::dsp::AudioBlock<float> (&samples, 1, (size_t) numSamples);
    gain.process (juce::dsp::ProcessContextReplacing<float> (block));

    float* const dry = dryScratch.data();
    std::memcpy (dry, samples, sizeof (float) * (size_t) numSamples);
    dryDelay.process (dry, numSamples);

    preLp.process (samples, samples, numSamples);
    processPreHp (samples, numSamples);
    inv.process (samples, samples, numSamples);

    const float* dryChannels[] { dry };
    bypassFade.mix (&samples, dryChannels, 1, numSamples);
}
