#pragma once

#include <juce_audio_processors/juce_audio_processors.h>

#include "AmpTelemetry.h"

//==============================================================================
/** ampScope -- a bench scope for a MAX98396/MAX98397 amplifier.

    The plugin does nothing to the audio: it passes it through untouched and
    exists purely so a scope for the amplifier's own measurements can live in the
    same host as everything else on the board. All of its work happens off the
    audio thread, in amp::Telemetry, which reads the amp through ALSA.

    Every knob a scope has -- timebase, per-trace volts/division and offset,
    trigger -- is a real automatable parameter. Which amplifier is being watched
    is deliberately NOT: it names a piece of hardware on this machine, so it is
    session state that is saved with the session and restored by name, in the
    same way the loudness plugin treats its view limits.
*/
class AmpScopeProcessor  : public juce::AudioProcessor
{
public:
    AmpScopeProcessor();
    ~AmpScopeProcessor() override;

    //--- juce::AudioProcessor -------------------------------------------------
    void prepareToPlay (double sampleRate, int samplesPerBlock) override;
    void releaseResources() override;
    bool isBusesLayoutSupported (const BusesLayout&) const override;
    using juce::AudioProcessor::processBlock;          // keep the double overload visible
    void processBlock (juce::AudioBuffer<float>&, juce::MidiBuffer&) override;

    juce::AudioProcessorEditor* createEditor() override;
    bool hasEditor() const override                          { return true; }

    const juce::String getName() const override              { return JucePlugin_Name; }
    bool acceptsMidi() const override                        { return false; }
    bool producesMidi() const override                       { return false; }
    bool isMidiEffect() const override                       { return false; }
    double getTailLengthSeconds() const override             { return 0.0; }

    int getNumPrograms() override                            { return 1; }
    int getCurrentProgram() override                         { return 0; }
    void setCurrentProgram (int) override                    {}
    const juce::String getProgramName (int) override         { return {}; }
    void changeProgramName (int, const juce::String&) override {}

    void getStateInformation (juce::MemoryBlock&) override;
    void setStateInformation (const void*, int) override;

    //--- the scope ------------------------------------------------------------
    juce::AudioProcessorValueTreeState apvts;
    amp::Telemetry telemetry;

    /** Parameter ids, built the same way everywhere so the editor and the
        parameter layout cannot drift apart. */
    static juce::String showId   (amp::Channel);
    static juce::String scaleId  (amp::Channel);
    static juce::String offsetId (amp::Channel);

    /** Which amplifier the scope is watching. Session state, not a parameter:
        guarded by a lock because getStateInformation() may run on any thread. */
    struct Selection
    {
        juce::String deviceKey;          ///< amp::Device::key(), e.g. "BPICM4IO/amp1"
        juce::String captureDevice;      ///< override for the auto-detected capture PCM
    };

    Selection getSelection() const;
    void setSelection (const Selection&);
    int  getSelectionVersion() const noexcept   { return selectionVersion.load(); }

    /** Rescan and re-point the acquisition at whatever the selection names.
        Returns everything that was found, for the picker. */
    juce::Array<amp::Device> refreshDevices();
    juce::Array<amp::Device> getKnownDevices() const;

    /** Push the acquisition-shaping parameters (source, rates, alignment) into
        the telemetry object. Called on any change to those parameters. */
    void applyAcquisitionParameters();

    /** Move one data type to a TDM slot, or to -1 for "not transmitted", and
        pick the new layout up. The slots are hardware state shared by every amp
        on the bus, so they are NOT plugin state: they are read back from the
        driver, never restored over it. */
    void setSlotFor (amp::Channel, int ampSlot);

    /** Whatever went wrong configuring the amp, for the status line. */
    juce::String getConfigurationError() const;

private:
    static juce::AudioProcessorValueTreeState::ParameterLayout createParameterLayout();

    mutable juce::CriticalSection selectionLock;
    Selection selection;
    juce::String configurationError;
    juce::Array<amp::Device> knownDevices;
    std::atomic<int> selectionVersion { 0 };

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (AmpScopeProcessor)
};
