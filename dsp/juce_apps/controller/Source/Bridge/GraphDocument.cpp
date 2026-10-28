#include "GraphDocument.h"

#include <ctime>

namespace bpi::document
{
    namespace
    {
        juce::var makeEnd (juce::uint32 node, int channel)
        {
            auto* o = new juce::DynamicObject();
            o->setProperty ("node", (int) node);
            o->setProperty ("channel", channel);
            return juce::var (o);
        }

        bool readEnd (const juce::var& end, juce::uint32& node, int& channel)
        {
            if (! end.isObject())
                return false;

            const auto& n = end["node"];
            const auto& c = end["channel"];

            if (! (n.isInt() || n.isInt64()) || ! (c.isInt() || c.isInt64()))
                return false;

            node = (juce::uint32) (juce::int64) n;
            channel = (int) c;
            return true;
        }

        juce::String describe (const Structure::Connection& c)
        {
            return juce::String (std::get<0> (c)) + ":" + juce::String (std::get<1> (c)) + "->"
                 + juce::String (std::get<2> (c)) + ":" + juce::String (std::get<3> (c));
        }
    }

    juce::String Structure::describeChangeTo (const Structure& after) const
    {
        juce::StringArray parts;

        for (const auto& [uid, node] : nodes)
        {
            const auto it = after.nodes.find (uid);

            if (it == after.nodes.end() || ! it->second.sameKind (node))
                parts.add ("-node " + juce::String (uid));
            else if (it->second.bypassed != node.bypassed)
                parts.add ((it->second.bypassed ? "bypass " : "unbypass ") + juce::String (uid));
        }

        for (const auto& [uid, node] : after.nodes)
        {
            const auto it = nodes.find (uid);

            if (it == nodes.end() || ! it->second.sameKind (node))
                parts.add ("+node " + juce::String (uid) + " (" + node.name + ")");
        }

        for (const auto& c : connections)
            if (after.connections.count (c) == 0)
                parts.add ("-" + describe (c));

        for (const auto& c : after.connections)
            if (connections.count (c) == 0)
                parts.add ("+" + describe (c));

        return parts.joinIntoString (", ");
    }

    juce::var empty()
    {
        auto* o = new juce::DynamicObject();
        o->setProperty ("format", "speakerd-graph");
        o->setProperty ("version", 1);
        o->setProperty ("revision", 0);
        o->setProperty ("nodes", juce::Array<juce::var>());
        o->setProperty ("connections", juce::Array<juce::var>());
        return juce::var (o);
    }

    juce::var copy (const juce::var& doc)
    {
        return juce::JSON::parse (toJson (doc));
    }

    juce::int64 revisionOf (const juce::var& doc)
    {
        const auto& r = doc["revision"];
        return (r.isInt() || r.isInt64()) ? (juce::int64) r : 0;
    }

    const juce::Array<juce::var>* nodesOf (const juce::var& doc)        { return doc["nodes"].getArray(); }
    const juce::Array<juce::var>* connectionsOf (const juce::var& doc)  { return doc["connections"].getArray(); }

    juce::uint32 uidOf (const juce::var& node)
    {
        const auto& u = node["uid"];
        return (u.isInt() || u.isInt64()) ? (juce::uint32) (juce::int64) u : 0;
    }

    juce::var findNode (const juce::var& doc, juce::uint32 uid)
    {
        if (auto* nodes = nodesOf (doc))
            for (const auto& n : *nodes)
                if (uidOf (n) == uid)
                    return n;

        return {};
    }

    Structure structureOf (const juce::var& doc)
    {
        Structure s;

        if (auto* nodes = nodesOf (doc))
        {
            for (const auto& n : *nodes)
            {
                const auto uid = uidOf (n);

                if (uid < firstPluginNode)
                    continue;

                Structure::Node node;
                node.format = n["plugin"]["format"].toString();
                node.identifier = n["plugin"]["identifier"].toString();
                node.name = n["name"].toString();

                if (node.name.isEmpty())
                    node.name = node.identifier.fromLastOccurrenceOf ("/", false, false);

                node.bypassed = n["bypassed"].isBool() && (bool) n["bypassed"];
                s.nodes[uid] = node;
            }
        }

        if (auto* connections = connectionsOf (doc))
        {
            for (const auto& c : *connections)
            {
                juce::uint32 src = 0, dst = 0;
                int srcChannel = 0, dstChannel = 0;

                if (readEnd (c["src"], src, srcChannel) && readEnd (c["dst"], dst, dstChannel))
                    s.connections.insert ({ src, srcChannel, dst, dstChannel });
            }
        }

        return s;
    }

    juce::var applyEdit (const juce::var& accepted, const Structure& before, const Structure& after,
                         juce::int64 newRevision, const std::function<void (juce::uint32, juce::DynamicObject&)>& fill)
    {
        auto doc = copy (accepted.isObject() ? accepted : empty());
        auto* root = doc.getDynamicObject();

        // Nodes: drop the removed (or replaced), keep the rest as they were, add the new.
        juce::Array<juce::var> nodes;

        if (auto* old = nodesOf (doc))
        {
            for (const auto& n : *old)
            {
                const auto uid = uidOf (n);
                const auto b = before.nodes.find (uid);
                const auto a = after.nodes.find (uid);

                if (b != before.nodes.end() && (a == after.nodes.end() || ! a->second.sameKind (b->second)))
                    continue;   // removed on the canvas

                if (b != before.nodes.end() && a != after.nodes.end() && a->second.bypassed != b->second.bypassed)
                    if (auto* o = n.getDynamicObject())
                        o->setProperty ("bypassed", a->second.bypassed);

                nodes.add (n);
            }
        }

        for (const auto& [uid, node] : after.nodes)
        {
            const auto b = before.nodes.find (uid);

            if (b != before.nodes.end() && b->second.sameKind (node))
                continue;

            auto* plugin = new juce::DynamicObject();
            plugin->setProperty ("format", node.format);
            plugin->setProperty ("identifier", node.identifier);

            auto* o = new juce::DynamicObject();
            o->setProperty ("uid", (int) uid);
            o->setProperty ("name", node.name);
            o->setProperty ("plugin", juce::var (plugin));

            if (node.bypassed)
                o->setProperty ("bypassed", true);

            nodes.add (juce::var (o));
        }

        for (const auto& n : nodes)
            if (auto* o = n.getDynamicObject())
                if (fill != nullptr && uidOf (n) >= firstPluginNode)
                    fill (uidOf (n), *o);

        // Connections: the same, per connection.
        juce::Array<juce::var> connections;

        if (auto* old = connectionsOf (doc))
        {
            for (const auto& c : *old)
            {
                juce::uint32 src = 0, dst = 0;
                int srcChannel = 0, dstChannel = 0;

                if (readEnd (c["src"], src, srcChannel) && readEnd (c["dst"], dst, dstChannel))
                {
                    const Structure::Connection key { src, srcChannel, dst, dstChannel };

                    if (before.connections.count (key) > 0 && after.connections.count (key) == 0)
                        continue;
                }

                connections.add (c);
            }
        }

        for (const auto& c : after.connections)
        {
            if (before.connections.count (c) > 0)
                continue;

            auto* o = new juce::DynamicObject();
            o->setProperty ("src", makeEnd (std::get<0> (c), std::get<1> (c)));
            o->setProperty ("dst", makeEnd (std::get<2> (c), std::get<3> (c)));
            connections.add (juce::var (o));
        }

        root->setProperty ("version", 1);
        root->setProperty ("revision", newRevision);
        root->setProperty ("nodes", nodes);
        root->setProperty ("connections", connections);
        return doc;
    }

    juce::String toJson (const juce::var& v)
    {
        return juce::JSON::toString (v, juce::JSON::FormatOptions{}.withSpacing (juce::JSON::Spacing::none));
    }

    juce::String nowIso8601()
    {
        const auto seconds = (std::time_t) (juce::Time::currentTimeMillis() / 1000);
        std::tm utc {};
       #if JUCE_WINDOWS
        gmtime_s (&utc, &seconds);
       #else
        gmtime_r (&seconds, &utc);
       #endif
        char text[32] {};
        std::strftime (text, sizeof (text), "%Y-%m-%dT%H:%M:%SZ", &utc);
        return text;
    }
}
