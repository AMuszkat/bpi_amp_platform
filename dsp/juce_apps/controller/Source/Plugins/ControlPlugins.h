#pragma once

#include <juce_audio_processors/juce_audio_processors.h>

#include <functional>
#include <memory>
#include <optional>
#include <vector>

namespace bpi
{
    //==============================================================================
    /** A plugin the controller can show: a control build compiled in (ControlPlugins.cmake
        lists them), or the test probe. */
    struct ControlPluginType
    {
        juce::String name;          // "loudness"
        juce::String headlessUri;   // what a new node names: the build a speaker runs
        juce::StringArray uris;     // every identifier a document may give for it
        std::function<std::unique_ptr<juce::AudioProcessor>()> create;

        juce::PluginDescription describe() const;
    };

    /** The plugins compiled in. Only control builds: there is no plugin scanning. */
    struct ControlPlugins
    {
        static const std::vector<ControlPluginType>& all();

        /** The type a document's plugin identifier names, or nullptr. */
        static const ControlPluginType* forIdentifier (const juce::String& identifier);
        static const ControlPluginType* forName (const juce::String& name);

        /** Adds the test probe (README.md, "Test mode"): a control processor with a gain, a
            choice, a switch and a monitor, mon_level, so the bridge's monitor path can be
            tested before a plugin publishes one. Call before the first all(). */
        static void enableTestProbe();
        static constexpr const char* testProbeUri = "urn:bpi-amp:controller:test-probe";
    };

    //==============================================================================
    /** The speaker's two fixed nodes, as the graph document reserves them: the stream in (uid
        1: FL, FR, Mine) and the amplifier out (uid 2: woofer, tweeter). Never prepared, never
        run: they only carry the pins. */
    class SpeakerIONode final : public juce::AudioProcessor
    {
    public:
        enum class Kind { input, output };
        explicit SpeakerIONode (Kind);

        Kind getKind() const noexcept  { return kind; }
        juce::String getPinName (int channel) const;

        const juce::String getName() const override;
        void prepareToPlay (double, int) override {}
        void releaseResources() override {}
        void processBlock (juce::AudioBuffer<float>&, juce::MidiBuffer&) override {}
        using AudioProcessor::processBlock;
        double getTailLengthSeconds() const override                 { return 0.0; }
        bool acceptsMidi() const override                            { return false; }
        bool producesMidi() const override                           { return false; }
        juce::AudioProcessorEditor* createEditor() override          { return nullptr; }
        bool hasEditor() const override                              { return false; }
        int getNumPrograms() override                                { return 1; }
        int getCurrentProgram() override                             { return 0; }
        void setCurrentProgram (int) override                        {}
        const juce::String getProgramName (int) override             { return {}; }
        void changeProgramName (int, const juce::String&) override   {}
        void getStateInformation (juce::MemoryBlock&) override       {}
        void setStateInformation (const void*, int) override         {}

    private:
        const Kind kind;
    };

    //==============================================================================
    /** A node whose plugin the controller has no control build for. It keeps the node, its
        pins and its place in the graph, so the controller never drops what it cannot show; its
        parameters and state travel on untouched in the document. */
    class PlaceholderNode final : public juce::AudioProcessor
    {
    public:
        PlaceholderNode (const juce::String& name, int numInputs, int numOutputs);

        const juce::String getName() const override                  { return name; }
        void prepareToPlay (double, int) override {}
        void releaseResources() override {}
        void processBlock (juce::AudioBuffer<float>&, juce::MidiBuffer&) override {}
        using AudioProcessor::processBlock;
        double getTailLengthSeconds() const override                 { return 0.0; }
        bool acceptsMidi() const override                            { return false; }
        bool producesMidi() const override                           { return false; }
        juce::AudioProcessorEditor* createEditor() override          { return nullptr; }
        bool hasEditor() const override                              { return false; }
        int getNumPrograms() override                                { return 1; }
        int getCurrentProgram() override                             { return 0; }
        void setCurrentProgram (int) override                        {}
        const juce::String getProgramName (int) override             { return {}; }
        void changeProgramName (int, const juce::String&) override   {}
        void getStateInformation (juce::MemoryBlock&) override       {}
        void setStateInformation (const void*, int) override         {}

    private:
        const juce::String name;
    };

    //==============================================================================
    /** Parameters by the IDs and units of the graph document (docs/protocol.md, "Values"). */
    namespace params
    {
        /** The parameter's ID ("s0_e0_gain"), or empty for one without. */
        juce::String idOf (const juce::AudioProcessorParameter&);

        /** A monitor: read-only, never sent, never in a document. */
        bool isMonitor (const juce::String& id);

        juce::AudioProcessorParameter* find (juce::AudioProcessor&, const juce::String& id);

        /** The value in the plugin's own units: dB, Hz, a choice's index, 0 or 1 for a switch. */
        double plainValue (const juce::AudioProcessorParameter&);
        double plainValue (const juce::AudioProcessorParameter&, float normalised);

        /** A document or message value (a number; a choice's text; true/false for a switch) as
            the parameter's normalised value, or nothing when it does not fit the parameter. */
        std::optional<float> normalisedFor (const juce::AudioProcessorParameter&, const juce::var& value);

        /** A plain value for JSON: integers for choices and switches, 7 significant digits
            otherwise. */
        juce::var toJson (const juce::AudioProcessorParameter&, double plain);
    }
}
