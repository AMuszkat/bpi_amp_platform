#pragma once

#include "DeviceConfig.h"

#include <juce_core/juce_core.h>

#include <memory>
#include <utility>

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
        come back, so nobody has to run pw-link, and the amplifier may appear after speakerd, or
        go away and return. It links once the registry has settled (one round trip after a
        change), so it never links to a port that is going away.

        Only a lost connection (the core's -EPIPE), a failed filter or a failed input sink is
        fatal (hasFailed()). PipeWire also reports errors about speakerd's own objects on the
        core, for instance when a link and its peer go away together; those are logged and
        speakerd carries on.

        Everything except the process callback runs on PipeWire's own (non-real-time) loop
        thread. This class opens no network socket: only PipeWire's local one. */
    class PipeWireNode
    {
    public:
        /** The clock settings of the PipeWire daemon, read from its core properties. */
        struct Clock
        {
            double rate = 48000.0;
            int quantum = 1024;
            int quantumLimit = 8192;   // the largest block a cycle can bring (clock.force-quantum included)
            int maxQuantum = 0;        // the largest quantum the graph picks by itself; 0: not in the properties
            int minQuantum = 32;       // the smallest; PipeWire 0.3.65's default when not in the properties

            /** The block size the graph is prepared for at start: the largest quantum PipeWire
                brings unforced, max (quantum, min (max-quantum, quantum-limit)), or the quantum
                limit when max-quantum is not known. A larger cycle (a forced quantum) makes
                speakerd re-prepare (Engine::service). */
            int preparedBlock() const noexcept
            {
                return maxQuantum > 0 ? juce::jmax (quantum, juce::jmin (maxQuantum, quantumLimit))
                                      : juce::jmax (quantum, quantumLimit);
            }

            /** The buffer sizes a graph document may ask for here (its `quantum`): the powers of
                two p with max (64, min-quantum) <= p <= min (2048, max-quantum, quantum-limit),
                max-quantum being 2048 when it is not in the properties. All of them fit what
                the graph is prepared for (preparedBlock()), so a change between them only
                re-primes. Empty (lo > hi) only on a PipeWire that allows none of them. */
            std::pair<int, int> quantumRange() const noexcept
            {
                const auto low  = juce::jmax (64, minQuantum);
                const auto high = juce::jmin (2048, maxQuantum > 0 ? maxQuantum : 2048, quantumLimit);
                auto lo = juce::nextPowerOfTwo (low);
                auto hi = 1;

                while (hi * 2 <= high)
                    hi *= 2;

                return { lo, hi };
            }
        };

        PipeWireNode (Engine&, const juce::String& name, const DeviceConfig&);
        ~PipeWireNode();

        /** Connects to the PipeWire daemon (the one PIPEWIRE_REMOTE / XDG_RUNTIME_DIR name) and
            reads its clock settings. */
        juce::Result connect (Clock&);

        /** Creates the input sink if there is none, registers the filter node, and starts
            linking. Fails if the filter node of another speakerd (`<name>-dsp`) is there. */
        juce::Result start();

        /** From now on, makes no link and logs nothing about links; the links that exist stay,
            so the output can fade out through them. For a stop. */
        void stopLinking();

        /** Unlinks, destroys the filter, and disconnects. A sink speakerd created stays. */
        void stop();

        /** Sets PipeWire's `settings` metadata, subject 0, key clock.force-quantum, to `quantum`
            (0: not forced, PipeWire's own default), and keeps it: it is written again whenever the
            settings metadata (re)appears in the registry, and at no other time. Message thread,
            never the audio thread (it takes the PipeWire loop's lock). Returns false when the
            settings metadata is not there (yet): then it is written when it appears. */
        bool setForceQuantum (int quantum);

        /** True once the connection to PipeWire is lost, or the filter or the input sink failed,
            or the filter node went away (destroyed by the server or by pw-cli). */
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
