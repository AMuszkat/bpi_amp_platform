#include "FilterGraph.h"

#include "GraphDocument.h"
#include "LV2State.h"

#include <cstring>
#include <map>
#include <optional>
#include <set>
#include <string>
#include <tuple>

namespace speakerd::filtergraph
{
    namespace
    {
        using juce::Result;
        using juce::String;

        constexpr int midiChannel = juce::AudioProcessorGraph::midiChannelIndex;

        /** An AudioPluginHost session nests 5 elements deep. */
        constexpr int maxDepth = 32;

        enum class Kind { input, output, midi, plugin };

        struct Node
        {
            int sessionUid = 0;
            Kind kind = Kind::plugin;
            juce::PluginDescription plugin;
            juce::uint32 uid = 0;               // in the document; 0 for a MIDI node
            String identifier;                  // the URI the document names
            std::optional<juce::MemoryBlock> state;
            bool emptyState = false;            // a STATE whose plugin state is empty: left out

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

        /** The file's structure, before JUCE parses it. No declaration: a <!DOCTYPE> with an
            entity makes JUCE's parser read other files into the document, and AudioPluginHost
            writes none. No nesting deeper than maxDepth: JUCE's parser recurses per element. */
        Result checkStructure (const String& text)
        {
            const std::string s (text.toRawUTF8());
            int depth = 0;

            for (size_t i = 0; i < s.size(); ++i)
            {
                if (s[i] != '<')
                    continue;

                const auto startsWith = [&] (const char* prefix) { return s.compare (i, std::strlen (prefix), prefix) == 0; };
                const auto skipPast = [&] (const char* end)
                {
                    const auto at = s.find (end, i + 2);

                    if (at == std::string::npos)
                        return false;

                    i = at + std::strlen (end) - 1;
                    return true;
                };

                if (startsWith ("<!--") || startsWith ("<![CDATA[") || startsWith ("<?"))
                {
                    if (! skipPast (startsWith ("<!--") ? "-->" : startsWith ("<?") ? "?>" : "]]>"))
                        return Result::fail ("an unterminated comment, CDATA section or processing instruction");

                    continue;
                }

                if (startsWith ("<!"))
                    return Result::fail ("it has a <!DOCTYPE>, <!ENTITY> or other declaration, which AudioPluginHost "
                                         "never writes and speakerd does not read");

                const bool closing = i + 1 < s.size() && s[i + 1] == '/';
                char quote = 0;
                size_t end = i + 1;

                for (; end < s.size(); ++end)
                {
                    const auto c = s[end];

                    if (quote != 0)
                        quote = c == quote ? 0 : quote;
                    else if (c == '"' || c == '\'')
                        quote = c;
                    else if (c == '>')
                        break;
                }

                if (end >= s.size())
                    return Result::fail ("an unterminated tag");

                if (closing)
                    --depth;
                else if (s[end - 1] != '/' && ++depth > maxDepth)
                    return Result::fail ("elements nested deeper than " + String (maxDepth) + "; an AudioPluginHost session "
                                         "nests 5");

                i = end;
            }

            return Result::ok();
        }

        /** An index attribute (a uid, a channel) as AudioPluginHost writes it: digits only.
            XmlElement::getIntAttribute would read "tweeter" as 0 and "1x" as 1. */
        Result readIndex (const juce::XmlElement& e, const char* name, const String& where, int& out)
        {
            if (! e.hasAttribute (name))
                return Result::fail (where + ": no " + name + " attribute");

            const auto text = e.getStringAttribute (name);

            if (text.isEmpty() || text.length() > 9 || ! text.containsOnly ("0123456789"))
                return Result::fail (where + ": " + name + "=\"" + text + "\" is not a number (digits only)");

            out = text.getIntValue();
            return Result::ok();
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
                            return Result::fail (where + ": a bus of its " + String (side).toLowerCase() + " is disabled; "
                                                 "speakerd enables every bus, so the channels would not be the session's");

            return Result::ok();
        }

        Result readNode (const juce::XmlElement& filter, Node& node)
        {
            if (auto r = readIndex (filter, "uid", "a <FILTER>", node.sessionUid); r.failed())
                return r;

            const auto where = "node " + String (node.sessionUid);

            if (node.sessionUid <= 0)
                return Result::fail (where + ": uids start at 1");

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

                if (auto r = lv2state::decodeJuceBase64 (state->getAllSubText().trim(), lv2state::maxLV2StateBytes, lv2State); r.failed())
                    return Result::fail (node.describe() + ": its STATE is " + r.getErrorMessage());

                juce::MemoryBlock own;
                if (auto r = lv2state::unwrap (String::fromUTF8 ((const char*) lv2State.getData(), (int) lv2State.getSize()),
                                               node.identifier, own); r.failed())
                    return Result::fail (node.describe() + ": its STATE is not the LV2 state of a JUCE plugin saved under "
                                         + node.identifier + ": " + r.getErrorMessage());

                if (own.isEmpty())
                    node.emptyState = true;   // a document state of no bytes could never be taken
                else
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

        /** A plugin found by URI, by the LV2 format of `formats`. */
        bool isFound (juce::AudioPluginFormatManager* formats, const String& uri)
        {
            juce::OwnedArray<juce::PluginDescription> found;

            if (formats != nullptr)
                for (auto* format : formats->getFormats())
                    if (format->getName() == "LV2")
                        format->findAllTypesForFile (found, uri);

            return ! found.isEmpty();
        }
    }

    Result import (const juce::File& session, const ImportOptions& options, String& document)
    {
        const auto note = [&] (const String& line) { if (options.note) options.note (line); };
        const String unsafeHint = " (--allow-unsafe-routing imports it all the same)";

        if (! session.existsAsFile())
            return Result::fail (session.getFullPathName() + " does not exist");

        if (session.getSize() > maxSessionBytes)
            return Result::fail (session.getFileName() + " is larger than " + String (maxSessionBytes / (1024 * 1024)) + " MB");

        // Parsed from the text, never from the file: JUCE's parser could then open others.
        const auto text = session.loadFileAsString();

        if (auto r = checkStructure (text); r.failed())
            return Result::fail (session.getFileName() + ": " + r.getErrorMessage());

        juce::XmlDocument parser (text);
        const auto xml = parser.getDocumentElement();

        if (xml == nullptr)
            return Result::fail (session.getFileName() + " is not XML: "
                                 + (parser.getLastParseError().isNotEmpty() ? parser.getLastParseError() : String ("it has no element")));

        if (! xml->hasTagName ("FILTERGRAPH"))
            return Result::fail (session.getFileName() + " is not an AudioPluginHost session: its root is <"
                                 + xml->getTagName() + ">, not <FILTERGRAPH>");

        // Which document channel each of the session's output channels becomes. AudioPluginHost's
        // output channel i reached the amplifier's position i (FL, FR: ports playback_FL,
        // playback_FR); speakerd sends document channel 0 to the woofer port, 1 to the tweeter's.
        int outputChannel[2] { 0, 1 };
        String outputMapping;

        if (options.device != nullptr)
        {
            const auto& woofer = options.device->wooferPort;
            const auto& tweeter = options.device->tweeterPort;

            if (! ((woofer == "playback_FL" && tweeter == "playback_FR") || (woofer == "playback_FR" && tweeter == "playback_FL")))
                return Result::fail ("--device: the woofer is on " + woofer + " and the tweeter on " + tweeter + "; "
                                     "AudioPluginHost's output channels 0 and 1 reached the amplifier's playback_FL and "
                                     "playback_FR, so the import maps them only for a woofer and a tweeter on those two ports");

            outputChannel[0] = woofer == "playback_FL" ? 0 : 1;
            outputChannel[1] = 1 - outputChannel[0];
            outputMapping = String ("session channel 0 (playback_FL) = ") + (outputChannel[0] == 0 ? "woofer" : "tweeter")
                          + ", 1 (playback_FR) = " + (outputChannel[1] == 0 ? "woofer" : "tweeter")
                          + ", as --device says (woofer " + woofer + ", tweeter " + tweeter + ")";
        }
        else
        {
            outputMapping = "session channel 0 (playback_FL) = woofer, 1 (playback_FR) = tweeter, assumed: no --device, "
                            "and playback_FL is the woofer unless device.json says otherwise";
        }

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
            int srcSession = 0, dstSession = 0, srcChannel = 0, dstChannel = 0;

            for (auto [name, value] : { std::pair { "srcFilter", &srcSession }, std::pair { "srcChannel", &srcChannel },
                                        std::pair { "dstFilter", &dstSession }, std::pair { "dstChannel", &dstChannel } })
                if (auto r = readIndex (*c, name, "a <CONNECTION>", *value); r.failed())
                    return r;

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

            // An AudioPluginHost session's Audio Input is the stream: FL and FR, no Mine.
            if (src.kind == Kind::input && srcChannel >= GraphDocument::streamChannels)
                return Result::fail (where + ": the stream has channels 0 (FL) and 1 (FR) only");

            if (dst.kind == Kind::output && dstChannel >= GraphDocument::outputChannels)
                return Result::fail (where + ": the amplifier has channels 0 (playback_FL) and 1 (playback_FR) only");

            const auto documentDstChannel = dst.kind == Kind::output ? outputChannel[dstChannel] : dstChannel;

            if (! made.emplace (src.uid, srcChannel, dst.uid, documentDstChannel).second)
                return Result::fail (where + ": maps to a connection that is there already (the session has more than one "
                                     "Audio Input or Audio Output node); AudioPluginHost adds the two, a graph document cannot");

            const auto mapped = String (src.uid) + ":" + String (srcChannel) + channelName (src.uid, srcChannel) + " -> "
                              + String (dst.uid) + ":" + String (documentDstChannel) + channelName (dst.uid, documentDstChannel);
            mappedConnections.add (where + " becomes " + mapped);

            if (src.kind == Kind::input && dst.kind == Kind::output && documentDstChannel == 1)
            {
                const auto unsafe = mapped + ": the stream, full range, goes straight to the tweeter; only a crossover may feed it";

                if (! options.allowUnsafeRouting)
                    return Result::fail (where + " becomes " + unsafe + unsafeHint);

                warnings.add (unsafe + " (--allow-unsafe-routing)");
            }

            connected.insert (srcSession);
            connected.insert (dstSession);
            reachesAmplifier = reachesAmplifier || dst.kind == Kind::output;

            auto* object = new juce::DynamicObject();
            object->setProperty ("src", end (src.uid, srcChannel));
            object->setProperty ("dst", end (dst.uid, documentDstChannel));
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
                    note (node.describe() + " becomes node 2, the amplifier: " + outputMapping);
                    continue;

                case Kind::midi:
                    note (node.describe() + ": nothing connected to it, left out");   // connected, it failed above
                    continue;

                case Kind::plugin:
                    break;
            }

            auto line = node.describe() + ", LV2 " + node.identifier + ", becomes node " + String (node.uid);
            const auto last = node.identifier.fromLastOccurrenceOf ("/", false, false);
            bool control = last.endsWith ("_control");
            const bool complete = ! control && ! last.endsWith ("_headless");

            if (options.preferHeadless && (complete || control))
            {
                const auto sibling = (control ? node.identifier.dropLastCharacters (8) : node.identifier) + "_headless";

                if (isFound (options.formats, sibling))
                {
                    node.identifier = sibling;
                    control = false;
                    line += ", " + sibling + " (--prefer-headless)";
                }
                else if (complete)
                {
                    line += ", keeping " + node.identifier + ": no " + sibling + " found";
                }
            }

            if (control)
            {
                const auto unsafe = node.describe() + ": " + node.identifier + " is a control build: it has no DSP, so it "
                                    "passes the stream through at full level";

                if (! options.allowUnsafeRouting)
                    return Result::fail (unsafe + (options.preferHeadless ? String (", and no _headless build of it was found")
                                                                          : String ("; --prefer-headless takes its _headless build"))
                                         + unsafeHint);

                warnings.add (unsafe + " (--allow-unsafe-routing)");
            }

            line += node.state.has_value() ? ", state: " + String ((int) node.state->getSize()) + " bytes of the plugin's own"
                  : node.emptyState        ? String (", state: empty, left out")
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
