#pragma once

#include <juce_audio_formats/juce_audio_formats.h>

namespace speakerd
{
    class Engine;

    /** The offline render mode: the same Engine as the live node, fed from a WAV file instead of
        PipeWire, in blocks of `blockSize` (the live node's quantum, 1024 on a speaker).

        A mono input feeds both FL and FR, as a mono player does through PipeWire; a stereo input
        feeds FL and FR. The output has two channels, woofer and tweeter, at the input's rate, as
        32-bit integer PCM WAV. */
    namespace render
    {
        /** Opens the input. Fails unless it is a WAV file with one or two channels. */
        juce::Result openInput (const juce::File&, std::unique_ptr<juce::AudioFormatReader>&);

        /** Runs every sample of `input` through `engine` and writes `output`. */
        juce::Result run (Engine&, juce::AudioFormatReader& input, const juce::File& output, int blockSize);
    }
}
