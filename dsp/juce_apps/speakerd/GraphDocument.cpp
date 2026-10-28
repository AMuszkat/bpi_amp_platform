#include "GraphDocument.h"

#include "LV2State.h"

#include <limits>
#include <set>

namespace speakerd
{
    juce::String GraphDocument::Node::describe() const
    {
        return "node " + juce::String (uid) + (name.isNotEmpty() ? " (" + name + ")" : juce::String());
    }

    juce::String GraphDocument::Connection::describe() const
    {
        return juce::String (srcNode) + ":" + juce::String (srcChannel) + " -> "
             + juce::String (dstNode) + ":" + juce::String (dstChannel);
    }

    namespace
    {
        using juce::Result;
        using juce::String;
        using juce::var;

        Result fail (const String& where, const String& what)
        {
            return Result::fail (where + ": " + what);
        }

        bool isInteger (const var& v) { return v.isInt() || v.isInt64(); }

        Result readInteger (const var& v, const String& where, juce::int64 minimum, juce::int64 maximum, juce::int64& out)
        {
            if (! isInteger (v))
                return fail (where, v.isVoid() ? "missing" : "must be an integer");

            const auto value = (juce::int64) v;

            if (value < minimum || value > maximum)
                return fail (where, "must be between " + String (minimum) + " and " + String (maximum));

            out = value;
            return Result::ok();
        }

        Result readString (const var& v, const String& where, String& out)
        {
            if (! v.isString() || v.toString().isEmpty())
                return fail (where, v.isVoid() ? "missing" : "must be a non-empty string");

            out = v.toString();
            return Result::ok();
        }

        /** RFC 4648 base64, only when it is exactly what Base64::toBase64 writes, for at most
            lv2state::maxStateBytes bytes. */
        Result decodeBase64 (const String& text, juce::MemoryBlock& out)
        {
            if ((size_t) text.length() > (lv2state::maxStateBytes + 2) / 3 * 4)
                return Result::fail ("more than " + String ((juce::int64) lv2state::maxStateBytes) + " bytes");

            juce::MemoryOutputStream decoded;

            if (! juce::Base64::convertFromBase64 (decoded, text))
                return Result::fail ("is not valid base64 (RFC 4648)");

            out = decoded.getMemoryBlock();

            if (juce::Base64::toBase64 (out.getData(), out.getSize()) != text)
                return Result::fail ("is not canonical base64 (RFC 4648, with its padding)");

            return Result::ok();
        }

        Result parseNode (const var& json, const String& where, GraphDocument::Node& node)
        {
            if (! json.isObject())
                return fail (where, "must be an object");

            juce::int64 uid = 0;
            if (auto r = readInteger (json["uid"], where + ".uid", 1, 0xffffffffLL, uid); r.failed())
                return r;

            node.uid = (juce::uint32) uid;

            if (node.uid < GraphDocument::firstPluginNode)
                return fail (where + ".uid", "uids 1 to " + String (GraphDocument::firstPluginNode - 1)
                                             + " are reserved (1 = input, 2 = output); plugin nodes start at "
                                             + String (GraphDocument::firstPluginNode));

            const auto& plugin = json["plugin"];

            if (! plugin.isObject())
                return fail (where + ".plugin", "missing: { \"format\": \"LV2\", \"identifier\": \"<plugin URI>\" }");

            if (auto r = readString (plugin["format"], where + ".plugin.format", node.format); r.failed())
                return r;

            if (auto r = readString (plugin["identifier"], where + ".plugin.identifier", node.identifier); r.failed())
                return r;

            if (const auto& name = json["name"]; ! name.isVoid())
                if (auto r = readString (name, where + ".name", node.name); r.failed())
                    return r;

            if (node.name.isEmpty())
                node.name = node.identifier.fromLastOccurrenceOf ("/", false, false);

            if (const auto& params = json["params"]; ! params.isVoid())
            {
                auto* object = params.getDynamicObject();

                if (object == nullptr)
                    return fail (where + ".params", "must be an object of parameter ID: value");

                for (const auto& property : object->getProperties())
                {
                    const auto& value = property.value;

                    if (! (value.isInt() || value.isInt64() || value.isDouble() || value.isBool() || value.isString()))
                        return fail (where + ".params." + property.name.toString(), "must be a number, a bool or a string");

                    node.params.emplace_back (property.name.toString(), value);
                }
            }

            if (const auto& state = json["state"]; ! state.isVoid())
            {
                if (! state.isString())
                    return fail (where + ".state", "must be a base64 string");

                juce::MemoryBlock decoded;
                if (auto r = decodeBase64 (state.toString().removeCharacters (" \t\r\n"), decoded); r.failed())
                    return fail (where + ".state", r.getErrorMessage());

                node.state = decoded;
            }

            if (const auto& bypassed = json["bypassed"]; ! bypassed.isVoid())
            {
                if (! bypassed.isBool())
                    return fail (where + ".bypassed", "must be true or false");

                node.bypassed = (bool) bypassed;
            }

            return Result::ok();
        }

        Result parseEnd (const var& json, const String& where, juce::uint32& nodeOut, int& channelOut)
        {
            if (! json.isObject())
                return fail (where, "must be { \"node\": <uid>, \"channel\": <index> }");

            juce::int64 node = 0, channel = 0;

            if (auto r = readInteger (json["node"], where + ".node", 1, 0xffffffffLL, node); r.failed())
                return r;

            if (auto r = readInteger (json["channel"], where + ".channel", 0, 1023, channel); r.failed())
                return r;

            nodeOut = (juce::uint32) node;
            channelOut = (int) channel;
            return Result::ok();
        }
    }

    Result GraphDocument::parse (const String& text, GraphDocument& out)
    {
        out = {};

        var json;
        if (auto r = juce::JSON::parse (text, json); r.failed())
            return Result::fail ("not valid JSON: " + r.getErrorMessage());

        return parse (json, out);
    }

    Result GraphDocument::parse (const var& json, GraphDocument& out)
    {
        out = {};

        if (! json.isObject())
            return Result::fail ("not a JSON object");

        if (const auto& format = json["format"]; ! format.isVoid() && format.toString() != "speakerd-graph")
            return fail ("format", "must be \"speakerd-graph\"");

        juce::int64 version = 0;
        if (auto r = readInteger (json["version"], "version", 0, std::numeric_limits<int>::max(), version); r.failed())
            return r;

        if (version != 1)
            return fail ("version", String (version) + " is not supported (this speakerd reads version 1)");

        if (auto r = readInteger (json["revision"], "revision", 0, std::numeric_limits<juce::int64>::max(), out.revision); r.failed())
            return r;

        const auto* nodes = json["nodes"].getArray();
        if (nodes == nullptr)
            return fail ("nodes", "missing: an array of plugin nodes (it may be empty)");

        std::set<juce::uint32> uids;

        for (int i = 0; i < nodes->size(); ++i)
        {
            Node node;
            if (auto r = parseNode (nodes->getReference (i), "nodes[" + String (i) + "]", node); r.failed())
                return r;

            if (! uids.insert (node.uid).second)
                return fail ("nodes[" + String (i) + "].uid", String (node.uid) + " is used twice");

            out.nodes.push_back (std::move (node));
        }

        const auto* connections = json["connections"].getArray();
        if (connections == nullptr)
            return fail ("connections", "missing: an array of connections (it may be empty)");

        for (int i = 0; i < connections->size(); ++i)
        {
            const auto where = "connections[" + String (i) + "]";
            const auto& c = connections->getReference (i);

            if (! c.isObject())
                return fail (where, "must be { \"src\": { \"node\": ..., \"channel\": ... }, \"dst\": { ... } }");

            Connection connection;

            if (auto r = parseEnd (c["src"], where + ".src", connection.srcNode, connection.srcChannel); r.failed())
                return r;

            if (auto r = parseEnd (c["dst"], where + ".dst", connection.dstNode, connection.dstChannel); r.failed())
                return r;

            const auto known = [&] (juce::uint32 uid) { return uids.count (uid) > 0; };

            if (connection.srcNode == outputNode)
                return fail (where + ".src", "the output node (2) has no outputs");

            if (connection.dstNode == inputNode)
                return fail (where + ".dst", "the input node (1) has no inputs");

            if (connection.srcNode == inputNode && connection.srcChannel >= inputChannels)
                return fail (where + ".src.channel", "the input node has channels 0 (FL), 1 (FR) and 2 (Mine)");

            if (connection.dstNode == outputNode && connection.dstChannel >= outputChannels)
                return fail (where + ".dst.channel", "the output node has channels 0 (woofer) and 1 (tweeter)");

            if (connection.srcNode != inputNode && ! known (connection.srcNode))
                return fail (where + ".src.node", "no node " + String (connection.srcNode));

            if (connection.dstNode != outputNode && ! known (connection.dstNode))
                return fail (where + ".dst.node", "no node " + String (connection.dstNode));

            for (const auto& other : out.connections)
                if (other.srcNode == connection.srcNode && other.srcChannel == connection.srcChannel
                    && other.dstNode == connection.dstNode && other.dstChannel == connection.dstChannel)
                    return fail (where, connection.describe() + " is listed twice");

            out.connections.push_back (connection);
        }

        return Result::ok();
    }
}
