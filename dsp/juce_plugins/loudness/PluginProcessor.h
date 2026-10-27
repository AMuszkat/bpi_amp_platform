#pragma once

#include <juce_audio_processors/juce_audio_processors.h>
#include <juce_dsp/juce_dsp.h>

#include <array>
#include <atomic>

//==============================================================================
/** Parameter IDs.

    The four setting sets are stored as flat, individually-named parameters
    (s<n>_e<b>_gain and friends) rather than as a nested ValueTree, so the whole
    plugin state round-trips through APVTS with no custom XML, and every control
    is automatable by the host.
*/
namespace LoudnessIDs
{
    static constexpr int numSettings = 4;   // the four registered contour sets
    static constexpr int numBands    = 2;   // two low-shelf parametric EQs

    inline juce::String band (int setting, int bandIndex, juce::StringRef what)
    {
        return "s" + juce::String (setting) + "_e" + juce::String (bandIndex) + "_" + what;
    }

    inline juce::String threshold (int setting) { return "s" + juce::String (setting) + "_thr"; }

    static constexpr const char* mode       = "mode";
    static constexpr const char* attack     = "atk";
    static constexpr const char* release    = "rel";
    static constexpr const char* hysteresis = "hyst";
    static constexpr const char* glide      = "glide";
    static constexpr const char* detector   = "det";
    static constexpr const char* bypass     = "bypass";
}

//==============================================================================
/** Level-dependent loudness contour: two low-shelf parametric EQs whose
    gain/frequency/Q are taken from one of four registered setting sets, chosen
    by the detected input level.

    Design notes that matter:

    - The level detector is an RMS ballistics filter (the traditional averaging
      detector -- a VU meter is quasi-RMS with ~300 ms integration, and BS.1770
      loudness is a mean-square over 400 ms; peak detection is the wrong tool for
      a *loudness* control).
    - It runs on the INPUT, ahead of the EQ. That keeps the control loop open: if
      it read the output, the bass boost would raise the measured level, pick a
      flatter setting, drop the level again, and oscillate.
    - Threshold comparisons are hysteretic, otherwise a level parked on a boundary
      chatters between two settings.
    - Switching glides the six EQ parameters to the new set rather than swapping
      coefficients, so there is no click. Plain IIR biquads are used deliberately:
      measured against a Simper/Cytomic TPT shelving SVF, a transposed-direct-form-II
      biquad produces ~30 dB LESS modulation splatter under *gain* modulation
      (-126 vs -97 dB), because the SVF's integrator coefficient g = tan(w0)/sqrt(A)
      is itself dragged around by the gain. An SVF only wins for cutoff modulation.
      At the glide times used here every topology is far below audibility anyway.
*/
class LoudnessProcessor  : public juce::AudioProcessor
{
public:
    //==============================================================================
    LoudnessProcessor();
    ~LoudnessProcessor() override;

    //==============================================================================
    void prepareToPlay (double sampleRate, int samplesPerBlock) override;
    void reset() override;
    void releaseResources() override;

   #ifndef JucePlugin_PreferredChannelConfigurations
    bool isBusesLayoutSupported (const BusesLayout& layouts) const override;
   #endif

    void processBlock (juce::AudioBuffer<float>&, juce::MidiBuffer&) override;

    //==============================================================================
    juce::AudioProcessorEditor* createEditor() override;
    bool hasEditor() const override;

    const juce::String getName() const override;
    bool acceptsMidi() const override;
    bool producesMidi() const override;
    bool isMidiEffect() const override;
    double getTailLengthSeconds() const override;

    int getNumPrograms() override;
    int getCurrentProgram() override;
    void setCurrentProgram (int index) override;
    const juce::String getProgramName (int index) override;
    void changeProgramName (int index, const juce::String& newName) override;

    void getStateInformation (juce::MemoryBlock& destData) override;
    void setStateInformation (const void* data, int sizeInBytes) override;

    //==============================================================================
    juce::AudioProcessorValueTreeState& getState() noexcept { return apvts; }

    /** Detected input level in dBFS, for the meter. */
    float getLevelDb() const noexcept { return levelDb.load (std::memory_order_relaxed); }

    /** Which of the four settings is currently applied (0-based). */
    int getActiveSetting() const noexcept { return activeSetting.load (std::memory_order_relaxed); }

    /** The EQ parameters actually in force right now -- mid-glide these sit between
        two settings, which is what the editor's "Live" curve draws.
    */
    void getLiveBandParams (int bandIndex, float& gainDb, float& freqHz, float& q) const noexcept;

    //==============================================================================
    /** The response plot's visible range. UI state, deliberately NOT parameters: it persists
        with the session (a VIEW child in the saved state) but is never host-visible or
        automatable. Held here rather than in the editor so it survives the editor closing,
        and behind a lock so getStateInformation() may run on any thread. */
    struct ViewLimits
    {
        float minHz = 20.0f, maxHz = 20000.0f;
        float bottomDb = -28.0f, topDb = 28.0f;

        static constexpr float lowestHz = 10.0f, highestHz = 24000.0f, minRatio = 2.0f;
        static constexpr float lowestDb = -48.0f, highestDb = 48.0f, minSpanDb = 3.0f;

        /** A valid view: each axis in range and at least an octave / 3 dB wide. An axis that
            cannot be made valid falls back to its default. */
        ViewLimits sanitised() const noexcept;
    };

    ViewLimits getViewLimits() const noexcept;
    void setViewLimits (const ViewLimits&) noexcept;

    /** Bumped on every setViewLimits(), so an open editor notices a restored session. */
    int getViewVersion() const noexcept { return viewVersion.load (std::memory_order_acquire); }

private:
    //==============================================================================
    juce::AudioProcessorValueTreeState::ParameterLayout createParameterLayout();
    int chooseSetting (float detectedDb) noexcept;
    void updateGlideTargets (int setting) noexcept;
    void refreshDetectorSettings() noexcept;

    juce::AudioProcessorValueTreeState apvts;

    // Coefficients are recomputed on this granularity; small enough that a glide
    // is stepped finely, large enough that the cost is irrelevant.
    static constexpr int controlBlockSize = 32;

    juce::dsp::BallisticsFilter<float> detector;
    std::array<juce::dsp::IIR::Filter<float>, LoudnessIDs::numBands> eq;

    using Smoothed = juce::SmoothedValue<float, juce::ValueSmoothingTypes::Linear>;
    std::array<Smoothed, LoudnessIDs::numBands> gainSmoothed, freqSmoothed, qSmoothed;

    // Cached raw parameter pointers -- APVTS string lookups are far too slow for
    // the audio thread.
    std::array<std::atomic<float>*, LoudnessIDs::numSettings> thresholdParam { };
    std::array<std::array<std::atomic<float>*, LoudnessIDs::numBands>, LoudnessIDs::numSettings>
        gainParam { }, freqParam { }, qParam { };
    std::atomic<float>* modeParam       = nullptr;
    std::atomic<float>* attackParam     = nullptr;
    std::atomic<float>* releaseParam    = nullptr;
    std::atomic<float>* hysteresisParam = nullptr;
    std::atomic<float>* glideParam      = nullptr;
    std::atomic<float>* detectorParam   = nullptr;
    std::atomic<float>* bypassParam     = nullptr;

    double currentSampleRate = 44100.0;
    int currentSetting = 0;             // audio-thread only; hysteresis state

    // Cached so we only push changes into the detector when they actually move.
    float lastAttack = -1.0f, lastRelease = -1.0f, lastGlide = -1.0f;
    int lastDetectorType = -1;

    mutable juce::SpinLock viewLock;
    ViewLimits viewLimits;
    std::atomic<int> viewVersion { 0 };

    std::atomic<float> levelDb { -120.0f };
    std::atomic<int> activeSetting { 0 };
    std::array<std::atomic<float>, LoudnessIDs::numBands> liveGain { }, liveFreq { }, liveQ { };

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (LoudnessProcessor)
};
