#include "Render.h"

#include "Engine.h"

namespace speakerd::render
{
    juce::Result openInput (const juce::File& file, std::unique_ptr<juce::AudioFormatReader>& reader)
    {
        if (! file.existsAsFile())
            return juce::Result::fail (file.getFullPathName() + " does not exist");

        juce::WavAudioFormat wav;
        reader.reset (wav.createReaderFor (file.createInputStream().release(), true));

        if (reader == nullptr)
            return juce::Result::fail (file.getFullPathName() + " is not a WAV file JUCE can read");

        if (reader->numChannels < 1 || reader->numChannels > 2)
            return juce::Result::fail (file.getFullPathName() + " has " + juce::String (reader->numChannels)
                                       + " channels; the render takes 1 (mono, played to FL and FR) or 2 (FL, FR)");

        if (reader->sampleRate <= 0.0)
            return juce::Result::fail (file.getFullPathName() + " has no sample rate");

        return juce::Result::ok();
    }

    juce::Result run (Engine& engine, juce::AudioFormatReader& input, const juce::File& output, int blockSize)
    {
        if (output.exists() && ! output.deleteFile())
            return juce::Result::fail ("cannot replace " + output.getFullPathName());

        std::unique_ptr<juce::OutputStream> stream = std::make_unique<juce::FileOutputStream> (output);

        if (! static_cast<juce::FileOutputStream&> (*stream).openedOk())
            return juce::Result::fail ("cannot write " + output.getFullPathName());

        const auto options = juce::AudioFormatWriterOptions{}.withSampleRate (input.sampleRate)
                                                             .withNumChannels (2)
                                                             .withBitsPerSample (32)
                                                             .withSampleFormat (juce::AudioFormatWriterOptions::SampleFormat::integral);

        juce::WavAudioFormat wav;
        auto writer = wav.createWriterFor (stream, options);

        if (writer == nullptr)
            return juce::Result::fail ("cannot write a WAV file to " + output.getFullPathName());

        const auto inputChannels = (int) input.numChannels;
        juce::AudioBuffer<float> in (2, blockSize), out (2, blockSize);

        for (juce::int64 position = 0; position < input.lengthInSamples; position += blockSize)
        {
            const auto n = (int) juce::jmin ((juce::int64) blockSize, input.lengthInSamples - position);

            in.clear();
            input.read (in.getArrayOfWritePointers(), inputChannels, position, n);

            if (inputChannels == 1)
                in.copyFrom (1, 0, in, 0, 0, n);

            const float* inPointers[] { in.getReadPointer (0), in.getReadPointer (1) };
            float* outPointers[]      { out.getWritePointer (0), out.getWritePointer (1) };
            engine.process (inPointers, outPointers, n, input.sampleRate);

            if (! writer->writeFromAudioSampleBuffer (out, 0, n))
                return juce::Result::fail ("writing " + output.getFullPathName() + " failed");
        }

        writer.reset();   // writes the header
        return juce::Result::ok();
    }
}
