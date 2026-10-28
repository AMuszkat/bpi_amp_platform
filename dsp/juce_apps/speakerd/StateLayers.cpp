#include "StateLayers.h"

#include "Log.h"

#include <iterator>

namespace speakerd
{
    juce::String LoadedLayer::describe() const
    {
        if (fileName.isEmpty())
            return name;

        return name + " (" + fileName + "), revision " + juce::String (revision);
    }

    namespace
    {
        struct Layer { const char* name; const char* file; };
        constexpr Layer layers[] { { "current",         "graph.json" },
                                   { "last-known-good", "graph.lkg.json" },
                                   { "factory",         "factory.json" } };
    }

    const juce::StringArray& layerNames()
    {
        static const juce::StringArray names { "current", "last-known-good", "factory", "passthrough" };
        return names;
    }

    LoadedLayer loadFirstWorkingLayer (const juce::File& stateDir, GraphBuilder& builder, Role role,
                                       double sampleRate, int maxBlock, const juce::String& firstLayer)
    {
        const auto first = juce::jmax (0, layerNames().indexOf (firstLayer));

        if (first > 0)
            log ("starting at layer " + layerNames()[first] + " (--from)");

        for (int i = first; i < (int) std::size (layers); ++i)
        {
            const auto& layer = layers[i];
            const auto prefix = juce::String ("layer ") + layer.name + " (" + layer.file + "): ";
            const auto file = stateDir.getChildFile (layer.file);

            if (! file.existsAsFile())
            {
                log (prefix + "FAILED: no such file");
                continue;
            }

            GraphDocument doc;
            if (const auto r = GraphDocument::parse (file.loadFileAsString(), doc); r.failed())
            {
                log (prefix + "FAILED: " + r.getErrorMessage());
                continue;
            }

            LoadedLayer loaded;
            if (const auto r = builder.build (doc, sampleRate, maxBlock, loaded.built); r.failed())
            {
                log (prefix + "FAILED: " + r.getErrorMessage());
                continue;
            }

            for (const auto& warning : loaded.built.warnings)
                log (prefix + "warning: " + warning);

            log (prefix + "loaded, revision " + juce::String (doc.revision) + ", "
                 + juce::String (loaded.built.pluginNodes) + " plugin node(s)");

            loaded.name = layer.name;
            loaded.fileName = layer.file;
            loaded.revision = doc.revision;
            log ("using layer " + loaded.describe());
            return loaded;
        }

        const auto channel = role == Role::left  ? juce::String ("FL")
                           : role == Role::right ? juce::String ("FR")
                                                 : juce::String ("(FL + FR) / 2");

        LoadedLayer passthrough;
        passthrough.name = "passthrough";
        passthrough.built = builder.buildPassthrough (sampleRate, maxBlock);

        log ("*** PASSTHROUGH: no state layer loaded ***");
        log ("using layer passthrough: woofer = " + channel + " at " + juce::String (passthroughGainDb, 0)
             + " dB, tweeter muted");
        return passthrough;
    }
}
