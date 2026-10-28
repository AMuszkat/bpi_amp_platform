#include "FilterGraph.h"

#include "GraphDocument.h"
#include "LV2State.h"

#include <map>
#include <optional>
#include <set>
#include <tuple>

namespace speakerd::filtergraph
{
    namespace
    {
        using juce::Result;
        using juce::String;

        constexpr int midiChannel = juce::AudioProcessorGraph::midiChannelIndex;

        enum class Kind { input, output, midi, plugin };

        struct Node
        {
            int sessionUid = 0;
            Kind kind = Kind::plugin;
            juce::PluginDescription plugin;
            juce::uint32 uid = 0;               // in the document; 0 for a MIDI node
            String identifier;                  // the URI the document names
            std::optional<juce::MemoryBlock> state;

            String describe() const
            {
                return "node " + String (sessionUid) + " (" + plugin.name + ")";
            }
        };

        const char* channelName (juce::uint32 documentNode, int channel)
        {
            if (documentNode == GraphDocument::inputNode)
                return channel == 0 ? " (FL)" : " (FR)";

            if (documentNode == GraphDocument::outputNode)
                return channel == 0 ? " (woofer)" : " (tweeter)";

            return "";
        }

        /** A session's bus layout, checked: speakerd enables every bus of a plugin, as
            AudioPluginHost does when it adds one, so a disabled bus would move the channels. */
        Result checkLayout (const juce::XmlElement* layout, const String& where)
        {
            if (layout == nullptr)
                return Result::ok();

            for (const auto* side : { "INPUTS", "OUTPUTS" })
                if (const auto* buses = layout->getChildByName (side))
                    for (const auto* bus : buses->getChildWithTagNameIterator ("BUS"))
                        if (bus->getStringAttribute ("layout") == "disabled")
                            return Result::fail (where + ": bus " + String (bus->getIntAttribute ("index")) + " of its "
                                                 + String (side).toLowerCase() + " is disabled; speakerd enables every bus, "
                                                 "so the channels would not be the session's");

            return Result::ok();
        }

        Result readNode (const juce::XmlElement& filter, Node& node)
        {
            node.sessionUid = filter.getIntAttribute ("uid");
            const auto where = "node " + String (node.sessionUid);

            if (node.sessionUid <= 0)
                return Result::fail ("a <FILTER> without a uid of 1 or more");

            // As PluginGraph::createNodeFromXml: the first child that is a <PLUGIN>.
            const auto* description = filter.getChildByName ("PLUGIN");

            if (description == nullptr || ! node.plugin.loadFromXml (*description))
                return Result::fail (where + ": no <PLUGIN>");

            const auto& format = node.plugin.pluginFormatName;
            const auto& name = node.plugin.name;

            if (format == "Internal")
            {
                if      (name.equalsIgnoreCase ("Audio Input"))  node.kind = Kind::input;
                else if (name.equalsIgnoreCase ("Audio Output")) node.kind = Kind::output;
                else if (name.equalsIgnoreCase ("MIDI Input") || name.equalsIgnoreCase ("MIDI Output")) node.kind = Kind::midi;
                else return Result::fail (node.describe() + ": an internal plugin of AudioPluginHost, which speakerd does not "
                                          "have (it has the stream in, as Audio Input, and the amplifier out, as Audio Output)");

                return Result::ok();
            }

            if (format != "LV2")
                return Result::fail (node.describe() + ": a " + format + " plugin; speakerd hosts LV2 plugins only");

            if (filter.getBoolAttribute ("useARA"))
                return Result::fail (node.describe() + ": hosted with ARA, which speakerd does not do");

            node.kind = Kind::plugin;
            node.identifier = node.plugin.fileOrIdentifier;

            if (node.identifier.isEmpty())
                return Result::fail (node.describe() + ": its <PLUGIN> has no file attribute (for LV2, the plugin URI)");

            if (auto r = checkLayout (filter.getChildByName ("LAYOUT"), node.describe()); r.failed())
                return r;

            if (const auto* state = filter.getChildByName ("STATE"))
            {
                // What the host saved: the hosted LV2 instance's getStateInformation(), lilv's
                // Turtle, in JUCE's own base64 (MemoryBlock::toBase64Encoding).
                juce::MemoryBlock lv2State;

                if (! lv2State.fromBase64Encoding (state->getAllSubText().trim()))
                    return Result::fail (node.describe() + ": its STATE is not the base64 MemoryBlock::toBase64Encoding writes");

                juce::MemoryBlock own;
                if (auto r = lv2state::unwrap (String::fromUTF8 ((const char*) lv2State.getData(), (int) lv2State.getSize()),
                                               node.identifier, own); r.failed())
                    return Result::fail (node.describe() + ": its STATE is not the LV2 state of a JUCE plugin saved under "
                                         + node.identifier + ": " + r.getErrorMessage());

                node.state = own;
            }

            return Result::ok();
        }

        juce::var end (juce::uint32 node, int channel)
        {
            auto* object = new juce::DynamicObject();
            object->setProperty ("node", (juce::int64) node);
            object->setProperty ("channel", channel);
            return object;
        }
    }

    Result import (const juce::File& session, const ImportOptions& options, String& document)
    {
        const auto note = [&] (const String& line) { if (options.note) options.note (line); };

        if (! session.existsAsFile())
            return Result::fail (session.getFullPathName() + " does not exist");

        juce::XmlDocument parser (session);
        const auto xml = parser.getDocumentElement();

        if (xml == nullptr)
            return Result::fail (session.getFileName() + " is not XML: "
                                 + (parser.getLastParseError().isNotEmpty() ? parser.getLastParseError() : String ("it has no element")));

        if (! xml->hasTagName ("FILTERGRAPH"))
            return Result::fail (session.getFileName() + " is not an AudioPluginHost session: its root is <"
                                 + xml->getTagName() + ">, not <FILTERGRAPH>");

        // 1. The nodes.
        std::vector<Node> nodes;
        std::map<int, size_t> bySessionUid;
        int connectionCount = 0;
        juce::uint32 nextUid = GraphDocument::firstPluginNode;

        for (const auto* child : xml->getChildIterator())
        {
            if (child->hasTagName ("CONNECTION"))
            {
                ++connectionCount;
                continue;
            }

            if (! child->hasTagName ("FILTER"))
                return Result::fail ("<" + child->getTagName() + ">: an AudioPluginHost session has <FILTER>s and <CONNECTION>s only");

            Node node;
            if (auto r = readNode (*child, node); r.failed())
                return r;

            if (! bySessionUid.emplace (node.sessionUid, nodes.size()).second)
                return Result::fail ("node " + String (node.sessionUid) + " is in the session twice");

            switch (node.kind)
            {
                case Kind::input:  node.uid = GraphDocument::inputNode;  break;
                case Kind::output: node.uid = GraphDocument::outputNode; break;
                case Kind::midi:   node.uid = 0; break;
                case Kind::plugin: node.uid = nextUid++; break;
            }

            nodes.push_back (std::move (node));
        }

        note (session.getFileName() + ": an AudioPluginHost session, " + String ((int) nodes.size()) + " node(s), "
              + String (connectionCount) + " connection(s)");

        // 2. The connections, per channel, mapped to the document's nodes: all checked before
        //    anything is said about them.
        juce::Array<juce::var> connections;
        juce::StringArray mappedConnections, warnings;
        std::set<std::tuple<juce::uint32, int, juce::uint32, int>> made;
        std::set<int> connected;
        bool reachesAmplifier = false;

        for (const auto* c : xml->getChildWithTagNameIterator ("CONNECTION"))
        {
            const auto srcSession = c->getIntAttribute ("srcFilter"), dstSession = c->getIntAttribute ("dstFilter");
            const auto srcChannel = c->getIntAttribute ("srcChannel", -1), dstChannel = c->getIntAttribute ("dstChannel", -1);
            const auto where = "connection " + String (srcSession) + ":" + String (srcChannel) + " -> "
                             + String (dstSession) + ":" + String (dstChannel);

            if (srcChannel == midiChannel || dstChannel == midiChannel)
                return Result::fail (where + ": a MIDI connection; speakerd's graph carries audio only");

            const auto srcIt = bySessionUid.find (srcSession), dstIt = bySessionUid.find (dstSession);

            if (srcIt == bySessionUid.end() || dstIt == bySessionUid.end())
                return Result::fail (where + ": no node " + String (srcIt == bySessionUid.end() ? srcSession : dstSession)
                                     + " in the session");

            const auto& src = nodes[srcIt->second];
            const auto& dst = nodes[dstIt->second];

            if (src.kind == Kind::midi || dst.kind == Kind::midi)
                return Result::fail (where + ": a connection of " + (src.kind == Kind::midi ? src : dst).describe()
                                     + "; speakerd's graph carries audio only");

            if (src.kind == Kind::output)
                return Result::fail (where + ": from " + src.describe() + ", which has no outputs");

            if (dst.kind == Kind::input)
                return Result::fail (where + ": into " + dst.describe() + ", which has no inputs");

            if (srcChannel < 0 || dstChannel < 0)
                return Result::fail (where + ": a channel is missing");

            if (src.kind == Kind::input && srcChannel >= GraphDocument::inputChannels)
                return Result::fail (where + ": the stream has channels 0 (FL) and 1 (FR) only");

            if (dst.kind == Kind::output && dstChannel >= GraphDocument::outputChannels)
                return Result::fail (where + ": the amplifier has channels 0 (woofer) and 1 (tweeter) only");

            if (! made.emplace (src.uid, srcChannel, dst.uid, dstChannel).second)
                return Result::fail (where + ": maps to a connection that is there already (the session has more than one "
                                     "Audio Input or Audio Output node); AudioPluginHost adds the two, a graph document cannot");

            const auto mapped = String (src.uid) + ":" + String (srcChannel) + channelName (src.uid, srcChannel) + " -> "
                              + String (dst.uid) + ":" + String (dstChannel) + channelName (dst.uid, dstChannel);
            mappedConnections.add (where + " becomes " + mapped);

            if (src.kind == Kind::input && dst.kind == Kind::output && dstChannel == 1)
                warnings.add (mapped + ": the stream, full range, goes straight to the tweeter; only a crossover may feed it");

            connected.insert (srcSession);
            connected.insert (dstSession);
            reachesAmplifier = reachesAmplifier || dst.kind == Kind::output;

            auto* object = new juce::DynamicObject();
            object->setProperty ("src", end (src.uid, srcChannel));
            object->setProperty ("dst", end (dst.uid, dstChannel));
            connections.add (object);
        }

        // 3. The nodes, as the document has them.
        juce::Array<juce::var> documentNodes;

        for (auto& node : nodes)
        {
            switch (node.kind)
            {
                case Kind::input:
                    note (node.describe() + " becomes node 1, the stream in: channel 0 = FL, 1 = FR");
                    continue;

                case Kind::output:
                    note (node.describe() + " becomes node 2, the amplifier: channel 0 = woofer, 1 = tweeter");
                    continue;

                case Kind::midi:
                    note (node.describe() + ": nothing connected to it, left out");   // connected, it failed above
                    continue;

                case Kind::plugin:
                    break;
            }

            auto line = node.describe() + ", LV2 " + node.identifier + ", becomes node " + String (node.uid);
            const auto last = node.identifier.fromLastOccurrenceOf ("/", false, false);

            if (last.endsWith ("_control"))
                warnings.add (node.describe() + " is a control build: it has no DSP");

            if (options.preferHeadless && ! last.endsWith ("_headless") && ! last.endsWith ("_control"))
            {
                const auto sibling = node.identifier + "_headless";
                juce::OwnedArray<juce::PluginDescription> found;

                if (options.formats != nullptr)
                    for (auto* format : options.formats->getFormats())
                        if (format->getName() == "LV2")
                            format->findAllTypesForFile (found, sibling);

                if (found.isEmpty())
                {
                    line += ", keeping " + node.identifier + ": no " + sibling + " found";
                }
                else
                {
                    node.identifier = sibling;
                    line += ", " + sibling + " (--prefer-headless)";
                }
            }

            line += node.state.has_value() ? ", state: " + String ((int) node.state->getSize()) + " bytes of the plugin's own"
                                           : String (", no STATE: the plugin starts from its defaults");
            note (line);

            if (! connected.count (node.sessionUid))
                warnings.add (node.describe() + " is not connected");

            auto* plugin = new juce::DynamicObject();
            plugin->setProperty ("format", "LV2");
            plugin->setProperty ("identifier", node.identifier);

            auto* object = new juce::DynamicObject();
            object->setProperty ("uid", (juce::int64) node.uid);
            object->setProperty ("name", node.plugin.name.isNotEmpty() ? node.plugin.name : last);
            object->setProperty ("plugin", plugin);

            if (node.state.has_value())
                object->setProperty ("state", juce::Base64::toBase64 (node.state->getData(), node.state->getSize()));

            documentNodes.add (object);
        }

        for (const auto& line : mappedConnections)
            note (line);

        if (! reachesAmplifier)
            warnings.add ("no connection reaches the amplifier: it would get silence");

        for (const auto& warning : warnings)
            note ("warning: " + warning);

        auto* root = new juce::DynamicObject();
        root->setProperty ("format", "speakerd-graph");
        root->setProperty ("version", 1);
        root->setProperty ("revision", 1);
        root->setProperty ("nodes", documentNodes);
        root->setProperty ("connections", connections);
        document = juce::JSON::toString (juce::var (root));

        // What speakerd reads back, checked as a layer is.
        GraphDocument check;
        if (auto r = GraphDocument::parse (document, check); r.failed())
            return Result::fail ("the document made is not valid: " + r.getErrorMessage());

        note ("a graph document, version 1, revision 1: " + String (check.nodes.size()) + " plugin node(s), "
              + String (check.connections.size()) + " connection(s)");
        return Result::ok();
    }
}
