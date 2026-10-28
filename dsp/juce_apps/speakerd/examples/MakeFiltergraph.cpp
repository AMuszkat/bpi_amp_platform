// speakerd-make-filtergraph: writes the AudioPluginHost session of one mono LV2 plugin between
// the stream's FL and the woofer, with its parameters at the values of a JSON file, the way
// AudioPluginHost makes and saves one. It is how examples/loudness.filtergraph was made (see
// make-loudness-filtergraph.sh), so that the example is what AudioPluginHost really writes:
//
// - a new AudioPluginHost document has Audio Input, MIDI Input, Audio Output and MIDI Output,
//   added in that order (PluginGraph::newDocument), so they get uids 1 to 4, and the first
//   plugin added gets 5 (AudioProcessorGraph::addNode);
// - the plugin is hosted by JUCE's LV2 hosting, with every bus enabled (addPluginCallback), in
//   a graph prepared at the device's rate and block size, and its parameters are set while the
//   graph runs, as an editor would set them;
// - the file is PluginGraph::createXml's: a <FILTER> per node with its uid, its position, its
//   editor window's properties, the <PLUGIN> its PluginDescription writes, its state as <STATE>
//   (getStateInformation, in MemoryBlock::toBase64Encoding) and its bus <LAYOUT>; then a
//   <CONNECTION> per channel; written by XmlElement::writeTo with the default format, as
//   PluginGraph::saveDocument does.
//
//     speakerd-make-filtergraph --plugin URI --params PARAMS.json OUT.filtergraph [--lv2-bundle PATH]...
//
// PARAMS.json is { "<parameter ID>": value, ... } in the plugin's own units, as the params of a
// graph document node (speakerd's README).

#include "../GraphBuilder.h"

#include <iostream>

namespace
{
    using Graph = juce::AudioProcessorGraph;
    using IO = Graph::AudioGraphIOProcessor;

    constexpr double rate = 48000.0;
    constexpr int block = 1024;

    int fail (const juce::String& message)
    {
        std::cerr << "speakerd-make-filtergraph: " << message << "\n";
        return 1;
    }

    juce::XmlElement* busLayoutXml (const juce::AudioProcessor::BusesLayout& layout, bool isInput)
    {
        const auto& buses = isInput ? layout.inputBuses : layout.outputBuses;
        auto* xml = new juce::XmlElement (isInput ? "INPUTS" : "OUTPUTS");

        for (int i = 0; i < buses.size(); ++i)
        {
            auto* bus = xml->createNewChildElement ("BUS");
            bus->setAttribute ("index", i);
            bus->setAttribute ("layout", buses[i].isDisabled() ? "disabled" : buses[i].getSpeakerArrangementAsString());
        }

        return xml;
    }

    /** One <FILTER>, as PluginGraph's createNodeXml writes it. */
    juce::XmlElement* nodeXml (Graph::Node& node)
    {
        auto* plugin = dynamic_cast<juce::AudioPluginInstance*> (node.getProcessor());
        auto* e = new juce::XmlElement ("FILTER");

        e->setAttribute ("uid", (int) node.nodeID.uid);
        e->setAttribute ("x", node.properties["x"].toString());
        e->setAttribute ("y", node.properties["y"].toString());
        e->setAttribute ("useARA", node.properties["useARA"].toString());

        // The editor window's properties, written once a window was opened (PluginWindow):
        // only the plugin's own editor ("Normal") here.
        if (node.properties.contains ("uiopen_Normal"))
            for (const auto* key : { "uiLastX_Normal", "uiLastY_Normal", "uiopen_Normal" })
                e->setAttribute (key, node.properties[key].toString());

        juce::PluginDescription description;
        plugin->fillInPluginDescription (description);
        e->addChildElement (description.createXml().release());

        juce::MemoryBlock state;
        plugin->getStateInformation (state);
        e->createNewChildElement ("STATE")->addTextElement (state.toBase64Encoding());

        const auto layout = plugin->getBusesLayout();
        auto* layouts = e->createNewChildElement ("LAYOUT");
        layouts->addChildElement (busLayoutXml (layout, true));
        layouts->addChildElement (busLayoutXml (layout, false));
        return e;
    }

    std::unique_ptr<juce::XmlElement> graphXml (Graph& graph)
    {
        auto xml = std::make_unique<juce::XmlElement> ("FILTERGRAPH");

        for (auto* node : graph.getNodes())
            xml->addChildElement (nodeXml (*node));

        for (const auto& connection : graph.getConnections())
        {
            auto* e = xml->createNewChildElement ("CONNECTION");
            e->setAttribute ("srcFilter", (int) connection.source.nodeID.uid);
            e->setAttribute ("srcChannel", connection.source.channelIndex);
            e->setAttribute ("dstFilter", (int) connection.destination.nodeID.uid);
            e->setAttribute ("dstChannel", connection.destination.channelIndex);
        }

        return xml;
    }
}

int main (int argc, char* argv[])
{
    juce::String uri;
    juce::File paramsFile, out;
    juce::StringArray bundles;

    for (int i = 1; i < argc; ++i)
    {
        const juce::String arg (argv[i]);
        const auto cwd = juce::File::getCurrentWorkingDirectory();

        if (arg == "--plugin" && i + 1 < argc)            uri = argv[++i];
        else if (arg == "--params" && i + 1 < argc)       paramsFile = cwd.getChildFile (argv[++i]);
        else if (arg == "--lv2-bundle" && i + 1 < argc)   bundles.add (cwd.getChildFile (argv[++i]).getFullPathName());
        else if (! arg.startsWith ("--") && out == juce::File()) out = cwd.getChildFile (arg);
        else return fail ("usage: speakerd-make-filtergraph --plugin URI --params PARAMS.json OUT.filtergraph [--lv2-bundle PATH]...");
    }

    if (uri.isEmpty() || paramsFile == juce::File() || out == juce::File())
        return fail ("--plugin, --params and the output file are required");

    juce::ScopedJuceInitialiser_GUI juceInitialiser;
    juce::AudioPluginFormatManager formats;
    juce::addHeadlessDefaultFormatsToManager (formats);

    juce::AudioPluginFormat* lv2 = nullptr;
    for (auto* format : formats.getFormats())
        if (format->getName() == "LV2")
            lv2 = format;

    juce::OwnedArray<juce::PluginDescription> found;
    for (const auto& bundle : bundles)
        lv2->findAllTypesForFile (found, bundle);

    found.clear();
    lv2->findAllTypesForFile (found, uri);

    if (found.isEmpty())
        return fail ("no LV2 plugin " + uri);

    juce::var params;
    if (auto r = juce::JSON::parse (paramsFile.loadFileAsString(), params); r.failed() || params.getDynamicObject() == nullptr)
        return fail (paramsFile.getFullPathName() + ": not a JSON object of parameter values");

    std::vector<std::pair<juce::String, juce::var>> values;
    for (const auto& property : params.getDynamicObject()->getProperties())
        values.emplace_back (property.name.toString(), property.value);

    // A new AudioPluginHost document, its graph played by a stereo device.
    Graph graph;
    graph.setPlayConfigDetails (2, 2, rate, block);

    const auto add = [&] (std::unique_ptr<juce::AudioProcessor> processor, double x, double y)
    {
        auto node = graph.addNode (std::move (processor));
        node->properties.set ("x", x);
        node->properties.set ("y", y);
        node->properties.set ("useARA", false);
        return node;
    };

    const auto audioIn  = add (std::make_unique<IO> (IO::audioInputNode),   0.5,  0.1);
    add (std::make_unique<IO> (IO::midiInputNode),                         0.25, 0.1);
    const auto audioOut = add (std::make_unique<IO> (IO::audioOutputNode),  0.5,  0.9);
    add (std::make_unique<IO> (IO::midiOutputNode),                        0.25, 0.9);

    juce::String error;
    auto instance = formats.createPluginInstance (*found.getFirst(), rate, block, error);

    if (instance == nullptr)
        return fail (uri + " did not instantiate: " + error);

    instance->enableAllBuses();
    const auto plugin = add (std::move (instance), 0.5, 0.5);

    // Its editor was opened, moved and closed: AudioPluginHost keeps where it was.
    plugin->properties.set ("uiLastX_Normal", 240);
    plugin->properties.set ("uiLastY_Normal", 180);
    plugin->properties.set ("uiopen_Normal", false);

    graph.addConnection ({ { audioIn->nodeID, 0 }, { plugin->nodeID, 0 } });   // FL
    graph.addConnection ({ { plugin->nodeID, 0 }, { audioOut->nodeID, 0 } });  // the woofer

    graph.prepareToPlay (rate, block);

    juce::StringArray warnings;
    if (auto r = speakerd::setParameters (*plugin->getProcessor(), values, "the plugin", found.getFirst()->name, warnings); r.failed())
        return fail (r.getErrorMessage());

    for (const auto& warning : warnings)
        std::cerr << "speakerd-make-filtergraph: warning: " << warning << "\n";

    // A hosted LV2 plugin takes the values while it processes: a second of silence.
    juce::AudioBuffer<float> buffer (2, block);
    juce::MidiBuffer midi;

    for (int done = 0; done < (int) rate; done += block)
    {
        buffer.clear();
        graph.processBlock (buffer, midi);
        midi.clear();
    }

    if (! graphXml (graph)->writeTo (out, {}))
        return fail ("cannot write " + out.getFullPathName());

    std::cerr << "speakerd-make-filtergraph: wrote " << out.getFileName() << ": " << uri << " with "
              << (int) values.size() << " parameter value(s)\n";
    return 0;
}
