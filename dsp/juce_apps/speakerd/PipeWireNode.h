#pragma once

#include "DeviceConfig.h"

#include <juce_core/juce_core.h>

#include <memory>

namespace speakerd
{
    class Engine;

    /*  speakerd in PipeWire.

        The graph runs in one pw_filter node, `<name>-dsp`, inside the PipeWire cycle on
        PipeWire's real-time data thread: two input ports, input_FL and input_FR, and two output
        ports, output_woofer and output_tweeter.

        Players play into a sink called `<name>` (pw-play --target speakerd). The session manager
        only links a player into an adapter node, which a pw_filter cannot be in PipeWire 0.3.65
        (it has no node format for WirePlumber 0.4.13 to read), so the sink is a separate node in
        front of the filter: an existing sink of that name if there is one (the speaker's
        PipeWire configuration defines it, or an earlier speakerd left it), otherwise a
        null-audio-sink that speakerd creates with object.linger, so that it stays when speakerd
        goes: a stream playing into it stays there, the amplifier gets silence, and the next
        speakerd adopts the sink. Its monitor ports carry what is played into it, in the same
        cycle.

        speakerd links everything itself, watching the registry: the sink's monitor_FL and
        monitor_FR to the filter's inputs, and the filter's outputs to the amplifier's two input
        ports named in device.json. It links as soon as the ports exist and again whenever they
        come back, so nobody has to run pw-link, and the amplifier may appear after speakerd.

        Everything except the process callback runs on PipeWire's own (non-real-time) loop
        thread. This class never opens a socket other than PipeWire's. */
    class PipeWireNode
    {
    public:
        /** The clock settings of the PipeWire daemon, read from its core properties. */
        struct Clock
        {
            double rate = 48000.0;
            int quantum = 1024;
            int quantumLimit = 8192;   // the largest block a cycle can bring
        };

        PipeWireNode (Engine&, const juce::String& name, const DeviceConfig&);
        ~PipeWireNode();

        /** Connects to the PipeWire daemon (the one PIPEWIRE_REMOTE / XDG_RUNTIME_DIR name) and
            reads its clock settings. */
        juce::Result connect (Clock&);

        /** Creates the input sink if there is none, registers the filter node, and starts
            linking. */
        juce::Result start();

        /** Unlinks, destroys the filter, and disconnects. A sink speakerd created stays. */
        void stop();

        /** True once the connection to PipeWire is lost, or the node failed. */
        bool hasFailed() const noexcept;

        /** True while the filter is linked on both sides: the input sink to its inputs, its
            outputs to the amplifier. getLinkCount() goes up every time this becomes true. */
        bool isLinked() const noexcept;
        juce::uint64 getLinkCount() const noexcept;

    private:
        struct Impl;
        std::unique_ptr<Impl> impl;
    };
}
