// The audio processing of the crossover: the two bands' gain and FIR, and the level-matched
// bypass (README.md, "Bypass").
//
// Compiled into the complete and headless builds only. The control build, which runs in the
// controller app as a remote control, leaves this file out (see DSP in CMakeLists.txt and
// juce_plugins/cmake/SpeakerPlugin.cmake), and with it the coefficient header.

#include "PluginProcessor.h"
#include "crossover_coefs.hpp" // provides lp[] and hp[] FIR coefficient arrays

#include <algorithm>
#include <cmath>
#include <cstring>
#include <iterator>

//==============================================================================
void PluginProcessor::prepareToPlay (double newSampleRate, int samplesPerBlock)
{
    // A host may prepare before it has an audio device (JUCE's LV2 wrapper instantiates with a
    // block size of 0); a later block of any size is still handled, in chunks.
    const int preparedBlock = samplesPerBlock > 0 ? samplesPerBlock : 512;

    juce::dsp::ProcessSpec spec;
    spec.sampleRate = newSampleRate > 0.0 ? newSampleRate : 48000.0;
    spec.maximumBlockSize = (juce::uint32) preparedBlock;
    // Process internally as mono; we'll route to two outputs later
    spec.numChannels = 1;

    // Allocate temporary mono buffers for LP/HP outputs: here, never in processBlock.
    lpBuffer.setSize(1, preparedBlock);
    hpBuffer.setSize(1, preparedBlock);
    dryBuffer.setSize(1, preparedBlock);

    // The low band: its gain, then its FIR.
    lpGain.prepare(spec);
    lpGain.setRampDurationSeconds(gainRampSeconds);
    lpGain.setGainDecibels(0.0f);
    lpFir.prepare(lp, (int)(sizeof(lp)/sizeof(lp[0])), preparedBlock);

    // The high band, the same way.
    hpGain.prepare(spec);
    hpGain.setRampDurationSeconds(gainRampSeconds);
    hpGain.setGainDecibels(0.0f);
    hpFir.prepare(hp, (int)(sizeof(hp)/sizeof(hp[0])), preparedBlock);

    // The bypass: the low band's gained input delayed as its FIR delays it. Both FIRs are
    // linear phase (symmetric taps, which the tuning GUI exports) and of one length, so each
    // delays by (N - 1) / 2 samples.
    constexpr int numLp = (int) (sizeof (lp) / sizeof (lp[0]));
    static_assert (numLp == (int) (sizeof (hp) / sizeof (hp[0])), "the two bands' FIRs must have one delay");
    jassert (std::equal (lp, lp + numLp, std::reverse_iterator<const float*> (lp + numLp)));
    jassert (std::equal (hp, hp + numLp, std::reverse_iterator<const float*> (hp + numLp)));
    dryDelay.prepare ((numLp - 1) / 2);
    bypassFade.prepare (spec.sampleRate, bypassFadeSeconds);

    // The gain parameter is applied again at the next block, whatever it is now: at once on the
    // first block, and over gainRampSeconds after that.
    appliedGainDb = std::numeric_limits<float>::quiet_NaN();

    reset();
}

void PluginProcessor::reset()
{
    lpGain.reset();
    lpFir.reset();
    hpGain.reset();
    hpFir.reset();
    dryDelay.reset();
    bypassFade.reset();
}

//==============================================================================
void PluginProcessor::processBlock (juce::AudioBuffer<float>& buffer, juce::MidiBuffer&)
{
    process (buffer, false);
}

void PluginProcessor::processBlockBypassed (juce::AudioBuffer<float>& buffer, juce::MidiBuffer&)
{
    process (buffer, true);
}

void PluginProcessor::process (juce::AudioBuffer<float>& buffer, bool bypassed) noexcept
{
    juce::ScopedNoDenormals noDenormals;

    // Audio thread: nothing here allocates, locks or makes a syscall.
    const float gainDb = gainParam->load (std::memory_order_relaxed);

    if (! juce::exactlyEqual (gainDb, appliedGainDb))
    {
        const bool first = std::isnan (appliedGainDb);
        lpGain.setGainDecibels (gainDb);
        hpGain.setGainDecibels (gainDb);

        if (first)
        {
            lpGain.reset();        // the first block after prepareToPlay starts at the gain, no ramp
            hpGain.reset();
        }

        appliedGainDb = gainDb;
    }

    bypassFade.setBypassed (bypassed);

    const int numSamples = buffer.getNumSamples();

    if (buffer.getNumChannels() == 0 || lpBuffer.getNumSamples() == 0)
    {
        buffer.clear();
        return;
    }

    // A block larger than the one prepared is processed in prepared-size chunks.
    const int chunk = lpBuffer.getNumSamples();

    for (int start = 0; start < numSamples; start += chunk)
        processChunk (buffer, start, juce::jmin (chunk, numSamples - start));

    // Clear any remaining output channels beyond the first two
    for (int ch = 2; ch < getTotalNumOutputChannels(); ++ch)
        buffer.clear (ch, 0, numSamples);
}

void PluginProcessor::processChunk (juce::AudioBuffer<float>& buffer, int start, int numSamples) noexcept
{
    const auto n = (size_t) numSamples;

    // Copy mono input (channel 0) to both temp buffers' single channel
    const float* inData = buffer.getReadPointer (0, start);
    lpBuffer.copyFrom (0, 0, inData, numSamples);
    hpBuffer.copyFrom (0, 0, inData, numSamples);

    // Process LP and HP over the first numSamples of their buffers: the gain, then the FIR.
    auto lpBlock = juce::dsp::AudioBlock<float> (lpBuffer).getSubsetChannelBlock (0, 1).getSubBlock (0, n);
    lpGain.process (juce::dsp::ProcessContextReplacing<float> (lpBlock));

    // The bypassed low band: the gained input, delayed as the FIR delays it.
    dryBuffer.copyFrom (0, 0, lpBuffer, 0, 0, numSamples);
    dryDelay.process (dryBuffer.getWritePointer (0), numSamples);

    lpFir.process (lpBuffer.getReadPointer (0), lpBuffer.getWritePointer (0), numSamples);
    auto hpBlock = juce::dsp::AudioBlock<float> (hpBuffer).getSubsetChannelBlock (0, 1).getSubBlock (0, n);
    hpGain.process (juce::dsp::ProcessContextReplacing<float> (hpBlock));
    hpFir.process (hpBuffer.getReadPointer (0), hpBuffer.getWritePointer (0), numSamples);

    // Processed, bypassed, or the fade between them; bypassed, the high band is silence.
    float* wet[] { lpBuffer.getWritePointer (0), hpBuffer.getWritePointer (0) };
    const float* dry[] { dryBuffer.getReadPointer (0), nullptr };
    bypassFade.mix (wet, dry, 2, numSamples);

    // Route: output channel 0 = LP, output channel 1 = HP.
    const int outChs = getTotalNumOutputChannels();

    if (outChs >= 1)
        std::memcpy (buffer.getWritePointer (0, start), lpBuffer.getReadPointer (0), sizeof (float) * n);

    if (outChs >= 2 && buffer.getNumChannels() >= 2)
        std::memcpy (buffer.getWritePointer (1, start), hpBuffer.getReadPointer (0), sizeof (float) * n);
}
