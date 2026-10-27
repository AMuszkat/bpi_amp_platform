// The audio processing of the loudness plugin: detector, setting selection, glide, EQ.
//
// Compiled into the complete and headless builds only. The control build, which runs in the
// controller app as a remote control, leaves this file out (see DSP in CMakeLists.txt and
// juce_plugins/cmake/SpeakerPlugin.cmake).

#include "PluginProcessor.h"

//==============================================================================
void LoudnessProcessor::prepareToPlay (double sampleRate, int samplesPerBlock)
{
    // A host can prepare the plugin before any audio device exists: JUCE's own LV2 wrapper
    // instantiates with sampleRate 0 / samplesPerBlock 0 when the graph has no device yet
    // (LV2PluginInstance ctor -> prepare). Every filter coefficient below divides by the
    // sample rate, so 0 would put inf/NaN into the biquads and trip JUCE's own assertions.
    // Fall back to something sane; the host prepares again for real once a device opens.
    currentSampleRate      = sampleRate > 0.0 ? sampleRate : 48000.0;
    const int preparedBlock = samplesPerBlock > 0 ? samplesPerBlock : 512;

    juce::dsp::ProcessSpec spec;
    spec.sampleRate       = currentSampleRate;
    spec.maximumBlockSize = (juce::uint32) preparedBlock;
    spec.numChannels      = 1;                       // mono, like crossover/firConv

    detector.prepare (spec);
    lastAttack = lastRelease = -1.0f;
    lastDetectorType = -1;
    refreshDetectorSettings();

    for (auto& f : eq)
    {
        // Allocate the coefficients once here. Later updates assign from a
        // std::array, which reuses this storage and so never allocates on the
        // audio thread.
        f.coefficients = new juce::dsp::IIR::Coefficients<float> (
            juce::dsp::IIR::ArrayCoefficients<float>::makeLowShelf (currentSampleRate, 100.0, 0.7, 1.0));
        f.prepare (spec);
    }

    // Start already settled on whichever setting the current parameters select,
    // so the plugin does not sweep from a wrong contour when playback begins.
    currentSetting = juce::jlimit (0, LoudnessIDs::numSettings - 1,
                                   (int) modeParam->load() - 1);
    if ((int) modeParam->load() == 0)
        currentSetting = 0;

    const float glideSeconds = glideParam->load() * 0.001f;
    lastGlide = glideParam->load();

    for (int b = 0; b < LoudnessIDs::numBands; ++b)
    {
        gainSmoothed[(size_t) b].reset (currentSampleRate, glideSeconds);
        freqSmoothed[(size_t) b].reset (currentSampleRate, glideSeconds);
        qSmoothed[(size_t) b].reset (currentSampleRate, glideSeconds);

        gainSmoothed[(size_t) b].setCurrentAndTargetValue (gainParam[(size_t) currentSetting][(size_t) b]->load());
        freqSmoothed[(size_t) b].setCurrentAndTargetValue (freqParam[(size_t) currentSetting][(size_t) b]->load());
        qSmoothed[(size_t) b].setCurrentAndTargetValue (qParam[(size_t) currentSetting][(size_t) b]->load());
    }

    reset();
}

void LoudnessProcessor::reset()
{
    detector.reset();
    for (auto& f : eq)
        f.reset();
}

void LoudnessProcessor::refreshDetectorSettings() noexcept
{
    const float atk = attackParam->load();
    const float rel = releaseParam->load();
    const int type  = (int) detectorParam->load();

    if (! juce::approximatelyEqual (atk, lastAttack))
    {
        detector.setAttackTime (atk);
        lastAttack = atk;
    }

    if (! juce::approximatelyEqual (rel, lastRelease))
    {
        detector.setReleaseTime (rel);
        lastRelease = rel;
    }

    if (type != lastDetectorType)
    {
        detector.setLevelCalculationType (
            type == 0 ? juce::dsp::BallisticsFilterLevelCalculationType::RMS
                      : juce::dsp::BallisticsFilterLevelCalculationType::peak);
        lastDetectorType = type;
    }
}

int LoudnessProcessor::chooseSetting (float detectedDb) noexcept
{
    // Manual override -- indispensable for auditioning a contour while editing it.
    const int mode = (int) modeParam->load();
    if (mode > 0)
    {
        currentSetting = juce::jlimit (0, LoudnessIDs::numSettings - 1, mode - 1);
        return currentSetting;
    }

    std::array<float, LoudnessIDs::numSettings> thresholds;
    for (int s = 0; s < LoudnessIDs::numSettings; ++s)
        thresholds[(size_t) s] = thresholdParam[(size_t) s]->load();

    // The active setting is the one with the highest threshold at or below the
    // level. Thresholds are free-running parameters, so never assume they are
    // ordered -- below every threshold, fall back to the lowest one.
    int candidate = -1;
    for (int s = 0; s < LoudnessIDs::numSettings; ++s)
        if (detectedDb >= thresholds[(size_t) s] && (candidate < 0 || thresholds[(size_t) s] > thresholds[(size_t) candidate]))
            candidate = s;

    if (candidate < 0)
    {
        candidate = 0;
        for (int s = 1; s < LoudnessIDs::numSettings; ++s)
            if (thresholds[(size_t) s] < thresholds[(size_t) candidate])
                candidate = s;
    }

    if (candidate != currentSetting)
    {
        // Only commit once the level has cleared the boundary by the hysteresis
        // amount, otherwise a level parked on a threshold chatters.
        const float hyst = hysteresisParam->load();

        if (thresholds[(size_t) candidate] > thresholds[(size_t) currentSetting])
        {
            if (detectedDb >= thresholds[(size_t) candidate] + hyst)
                currentSetting = candidate;
        }
        else if (detectedDb < thresholds[(size_t) currentSetting] - hyst)
        {
            currentSetting = candidate;
        }
    }

    return currentSetting;
}

void LoudnessProcessor::updateGlideTargets (int setting) noexcept
{
    for (int b = 0; b < LoudnessIDs::numBands; ++b)
    {
        gainSmoothed[(size_t) b].setTargetValue (gainParam[(size_t) setting][(size_t) b]->load());
        freqSmoothed[(size_t) b].setTargetValue (freqParam[(size_t) setting][(size_t) b]->load());
        qSmoothed[(size_t) b].setTargetValue (qParam[(size_t) setting][(size_t) b]->load());
    }
}

void LoudnessProcessor::processBlock (juce::AudioBuffer<float>& buffer, juce::MidiBuffer&)
{
    juce::ScopedNoDenormals noDenormals;

    for (int i = getTotalNumInputChannels(); i < getTotalNumOutputChannels(); ++i)
        buffer.clear (i, 0, buffer.getNumSamples());

    if (buffer.getNumChannels() == 0)
        return;

    refreshDetectorSettings();

    const float glideMs = glideParam->load();
    if (! juce::approximatelyEqual (glideMs, lastGlide))
    {
        const double seconds = glideMs * 0.001;
        for (int b = 0; b < LoudnessIDs::numBands; ++b)
        {
            gainSmoothed[(size_t) b].reset (currentSampleRate, seconds);
            freqSmoothed[(size_t) b].reset (currentSampleRate, seconds);
            qSmoothed[(size_t) b].reset (currentSampleRate, seconds);
        }
        lastGlide = glideMs;
    }

    const bool bypassed = bypassParam->load() > 0.5f;
    auto* samples = buffer.getWritePointer (0);
    const int numSamples = buffer.getNumSamples();

    for (int pos = 0; pos < numSamples; pos += controlBlockSize)
    {
        const int todo = juce::jmin (controlBlockSize, numSamples - pos);

        // 1. Detect on the INPUT, before the EQ touches it. Reading the output
        //    instead would let the bass boost feed its own level back.
        float envelope = 0.0f;
        for (int i = 0; i < todo; ++i)
            envelope = detector.processSample (0, samples[pos + i]);

        const float db = juce::Decibels::gainToDecibels (envelope, -120.0f);
        levelDb.store (db, std::memory_order_relaxed);

        // 2. Pick the contour set, then 3. glide the EQ toward it.
        const int setting = chooseSetting (db);
        activeSetting.store (setting, std::memory_order_relaxed);
        updateGlideTargets (setting);

        for (int b = 0; b < LoudnessIDs::numBands; ++b)
        {
            const float g = gainSmoothed[(size_t) b].skip (todo);
            const float f = juce::jlimit (10.0f, (float) (currentSampleRate * 0.45),
                                          freqSmoothed[(size_t) b].skip (todo));
            const float q = juce::jmax (0.05f, qSmoothed[(size_t) b].skip (todo));

            liveGain[(size_t) b].store (g, std::memory_order_relaxed);
            liveFreq[(size_t) b].store (f, std::memory_order_relaxed);
            liveQ[(size_t) b].store (q, std::memory_order_relaxed);

            // Assigning from a std::array reuses the storage allocated in
            // prepareToPlay, so this does not allocate.
            *eq[(size_t) b].coefficients = juce::dsp::IIR::ArrayCoefficients<float>::makeLowShelf (
                currentSampleRate, f, q, juce::Decibels::decibelsToGain (g));
        }

        if (! bypassed)
        {
            auto block = juce::dsp::AudioBlock<float> (buffer)
                            .getSubsetChannelBlock (0, 1)
                            .getSubBlock ((size_t) pos, (size_t) todo);
            juce::dsp::ProcessContextReplacing<float> context (block);

            for (auto& f : eq)
                f.process (context);
        }
    }
}
