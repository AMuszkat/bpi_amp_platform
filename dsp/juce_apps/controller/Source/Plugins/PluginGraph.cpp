/*
  ==============================================================================

   Derived from JUCE's extras/AudioPluginHost/Source/Plugins/PluginGraph.cpp
   (Raw Material Software Limited, JUCE 8; AGPLv3 or the JUCE licence). See PluginGraph.h.

  ==============================================================================
*/

#include "PluginGraph.h"

namespace bpi
{
    namespace
    {
        using Node = juce::AudioProcessorGraph::Node;
        using NodeID = juce::AudioProcessorGraph::NodeID;
        using Connection = juce::AudioProcessorGraph::Connection;

        constexpr auto noRebuild = juce::AudioProcessorGraph::UpdateKind::none;

        juce::String kindOf (const Node& n)    { return n.properties["kind"].toString(); }

        /** Where a node without a canvas position goes: the stream at the top, the amplifier at
            the bottom, plugins in a row between. */
        juce::Point<double> defaultPosition (juce::uint32 uid, int index)
        {
            if (uid == document::inputNode)  return { 0.5, 0.1 };
            if (uid == document::outputNode) return { 0.5, 0.9 };
            return { 0.2 + 0.2 * (index % 4), 0.35 + 0.2 * ((index / 4) % 3) };
        }
    }

    PluginGraph::PluginGraph()
    {
        // The speaker's fixed nodes. The graph is never prepared: nothing here processes audio.
        if (auto n = graph.addNode (std::make_unique<SpeakerIONode> (SpeakerIONode::Kind::input), NodeID { document::inputNode }, noRebuild))
        {
            n->properties.set ("kind", nodeKind::input);
            setNodePosition (n->nodeID, defaultPosition (document::inputNode, 0));
        }

        if (auto n = graph.addNode (std::make_unique<SpeakerIONode> (SpeakerIONode::Kind::output), NodeID { document::outputNode }, noRebuild))
        {
            n->properties.set ("kind", nodeKind::output);
            setNodePosition (n->nodeID, defaultPosition (document::outputNode, 0));
        }

        graph.addChangeListener (this);
    }

    PluginGraph::~PluginGraph()
    {
        graph.removeChangeListener (this);
        activePluginWindows.clear();
        graph.clear (noRebuild);
    }

    void PluginGraph::changeListenerCallback (juce::ChangeBroadcaster*)
    {
        removeWindowsOfMissingNodes();
    }

    void PluginGraph::removeWindowsOfMissingNodes()
    {
        for (int i = activePluginWindows.size(); --i >= 0;)
            if (! graph.getNodes().contains (activePluginWindows.getUnchecked (i)->node))
                activePluginWindows.remove (i);
    }

    bool PluginGraph::isPluginNode (const Node& n)
    {
        const auto kind = kindOf (n);
        return kind == nodeKind::control || kind == nodeKind::placeholder;
    }

    juce::String PluginGraph::displayName (const Node& n)
    {
        if (isPluginNode (n))
            return n.properties["name"].toString() + " (" + juce::String (n.nodeID.uid) + ")";

        return n.getProcessor()->getName();
    }

    PluginGraph::NodeID PluginGraph::nextFreeUid() const
    {
        juce::uint32 highest = document::firstPluginNode - 1;

        for (auto* n : graph.getNodes())
            highest = juce::jmax (highest, n->nodeID.uid);

        return NodeID { highest + 1 };
    }

    NodeID PluginGraph::addPlugin (const ControlPluginType& type, juce::Point<double> pos)
    {
        auto processor = type.create();

        if (processor == nullptr)
            return {};

        const auto uid = nextFreeUid();

        if (auto node = graph.addNode (std::move (processor), uid, noRebuild))
        {
            node->properties.set ("kind", nodeKind::control);
            node->properties.set ("name", type.name);
            node->properties.set ("format", "LV2");
            node->properties.set ("identifier", type.headlessUri);
            setNodePosition (uid, pos);
            graph.sendChangeMessage();
            return uid;
        }

        return {};
    }

    void PluginGraph::setNodePosition (NodeID nodeID, juce::Point<double> pos)
    {
        if (auto* n = graph.getNodeForId (nodeID))
        {
            n->properties.set ("x", juce::jlimit (0.0, 1.0, pos.x));
            n->properties.set ("y", juce::jlimit (0.0, 1.0, pos.y));
        }
    }

    juce::Point<double> PluginGraph::getNodePosition (NodeID nodeID) const
    {
        if (auto* n = graph.getNodeForId (nodeID))
            return { static_cast<double> (n->properties["x"]),
                     static_cast<double> (n->properties["y"]) };

        return {};
    }

    void PluginGraph::setBypassed (NodeID nodeID, bool bypassed)
    {
        if (auto* n = graph.getNodeForId (nodeID))
        {
            n->setBypassed (bypassed);
            graph.sendChangeMessage();
        }
    }

    //==============================================================================
    document::Structure PluginGraph::structure() const
    {
        document::Structure s;

        for (auto* n : graph.getNodes())
        {
            if (! isPluginNode (*n))
                continue;

            document::Structure::Node node;
            node.format = n->properties["format"].toString();
            node.identifier = n->properties["identifier"].toString();
            node.name = n->properties["name"].toString();
            node.bypassed = n->isBypassed();
            s.nodes[n->nodeID.uid] = node;
        }

        for (const auto& c : graph.getConnections())
            if (! c.source.isMIDI() && ! c.destination.isMIDI())
                s.connections.insert ({ c.source.nodeID.uid, c.source.channelIndex,
                                        c.destination.nodeID.uid, c.destination.channelIndex });

        return s;
    }

    juce::var PluginGraph::currentParams (NodeID nodeID) const
    {
        auto* object = new juce::DynamicObject();

        if (auto* n = graph.getNodeForId (nodeID))
        {
            for (auto* p : n->getProcessor()->getParameters())
            {
                const auto id = params::idOf (*p);

                if (id.isNotEmpty() && ! params::isMonitor (id))
                    object->setProperty (id, params::toJson (*p, params::plainValue (*p)));
            }
        }

        return juce::var (object);
    }

    juce::StringArray PluginGraph::applyDocument (const juce::var& doc,
                                                  const std::function<bool (juce::uint32, const juce::String&)>& skipParameter,
                                                  bool existingNodeValues)
    {
        juce::StringArray problems;
        const auto wanted = document::structureOf (doc);

        // Channel counts a placeholder needs: the highest channel any connection uses.
        std::map<juce::uint32, std::pair<int, int>> placeholderChannels;
        for (const auto& [src, srcChannel, dst, dstChannel] : wanted.connections)
        {
            placeholderChannels[src].second = juce::jmax (placeholderChannels[src].second, srcChannel + 1);
            placeholderChannels[dst].first  = juce::jmax (placeholderChannels[dst].first,  dstChannel + 1);
        }

        // Nodes that went, or now name another plugin, or whose placeholder lacks pins.
        const auto existing = graph.getNodes();   // a copy: nodes are removed below

        for (auto* n : existing)
        {
            if (! isPluginNode (*n))
                continue;

            const auto it = wanted.nodes.find (n->nodeID.uid);
            auto keep = it != wanted.nodes.end()
                        && it->second.identifier == n->properties["identifier"].toString()
                        && it->second.format == n->properties["format"].toString();

            if (keep && kindOf (*n) == nodeKind::placeholder)
            {
                const auto need = placeholderChannels[n->nodeID.uid];
                keep = n->getProcessor()->getTotalNumInputChannels() >= need.first
                    && n->getProcessor()->getTotalNumOutputChannels() >= need.second;
            }

            if (! keep)
                graph.removeNode (n->nodeID, noRebuild);
        }

        // Nodes to add, and every node's state, parameters, bypass and position.
        int index = 0;

        if (auto* nodes = document::nodesOf (doc))
        {
            for (const auto& entry : *nodes)
            {
                const auto uid = document::uidOf (entry);
                const auto wantedNode = wanted.nodes.find (uid);

                if (wantedNode == wanted.nodes.end())
                    continue;

                const auto& w = wantedNode->second;
                auto node = graph.getNodeForId (NodeID { uid });
                const auto isNew = node == nullptr;

                if (node == nullptr)
                {
                    if (auto* type = ControlPlugins::forIdentifier (w.identifier); type != nullptr && w.format == "LV2")
                    {
                        node = graph.addNode (type->create(), NodeID { uid }, noRebuild);
                        if (node != nullptr)
                            node->properties.set ("kind", nodeKind::control);
                    }
                    else
                    {
                        const auto need = placeholderChannels[uid];
                        node = graph.addNode (std::make_unique<PlaceholderNode> (w.name, need.first, need.second), NodeID { uid }, noRebuild);

                        if (node != nullptr)
                            node->properties.set ("kind", nodeKind::placeholder);

                        problems.add ("node " + juce::String (uid) + " (" + w.name + "): no control build of "
                                      + w.identifier + " in this controller, shown as a placeholder");
                    }

                    if (node == nullptr)
                    {
                        problems.add ("node " + juce::String (uid) + ": could not be added");
                        continue;
                    }

                    node->properties.set ("name", w.name);
                    node->properties.set ("format", w.format);
                    node->properties.set ("identifier", w.identifier);
                }

                ++index;
                node->setBypassed (w.bypassed);

                // Canvas position: the document's, when it carries one.
                const auto& ui = entry["ui"];
                if (ui["x"].isDouble() || ui["x"].isInt())
                    setNodePosition (node->nodeID, { (double) ui["x"], (double) ui["y"] });
                else if (! node->properties.contains ("x"))
                    setNodePosition (node->nodeID, defaultPosition (uid, index - 1));

                if (kindOf (*node) != nodeKind::control || (! isNew && ! existingNodeValues))
                    continue;

                auto& processor = *node->getProcessor();

                // The state first, when it is new to this node, then the parameters on top.
                if (const auto& state = entry["state"]; state.isString()
                        && state.toString() != node->properties["appliedState"].toString())
                {
                    juce::MemoryOutputStream decoded;

                    if (juce::Base64::convertFromBase64 (decoded, state.toString().removeCharacters (" \t\r\n")))
                    {
                        processor.setStateInformation (decoded.getData(), (int) decoded.getDataSize());
                        node->properties.set ("appliedState", state);
                    }
                    else
                    {
                        problems.add ("node " + juce::String (uid) + ": state is not base64");
                    }
                }

                if (auto* object = entry["params"].getDynamicObject())
                {
                    for (const auto& property : object->getProperties())
                    {
                        const auto id = property.name.toString();

                        if (params::isMonitor (id) || (skipParameter != nullptr && skipParameter (uid, id)))
                            continue;

                        auto* p = params::find (processor, id);

                        if (p == nullptr)
                        {
                            problems.add ("node " + juce::String (uid) + ": no parameter " + id);
                            continue;
                        }

                        if (const auto normalised = params::normalisedFor (*p, property.value))
                        {
                            if (std::abs (p->getValue() - *normalised) > 1.0e-7f)
                                p->setValueNotifyingHost (*normalised);
                        }
                        else
                        {
                            problems.add ("node " + juce::String (uid) + ": " + id + " = " + property.value.toString() + " does not fit");
                        }
                    }
                }
            }
        }

        // Connections.
        for (const auto& c : graph.getConnections())
        {
            const document::Structure::Connection key { c.source.nodeID.uid, c.source.channelIndex,
                                                        c.destination.nodeID.uid, c.destination.channelIndex };
            if (wanted.connections.count (key) == 0)
                graph.removeConnection (c, noRebuild);
        }

        for (const auto& [src, srcChannel, dst, dstChannel] : wanted.connections)
        {
            const Connection c { { NodeID { src }, srcChannel }, { NodeID { dst }, dstChannel } };

            if (! graph.isConnected (c) && ! graph.addConnection (c, noRebuild))
                problems.add ("connection " + juce::String (src) + ":" + juce::String (srcChannel) + " -> "
                              + juce::String (dst) + ":" + juce::String (dstChannel) + " cannot be shown");
        }

        removeWindowsOfMissingNodes();
        graph.sendChangeMessage();
        return problems;
    }

    //==============================================================================
    PluginWindow* PluginGraph::getOrCreateWindowFor (Node* node, PluginWindow::Type type)
    {
        jassert (node != nullptr);

        for (auto* w : activePluginWindows)
            if (w->node.get() == node && w->type == type)
                return w;

        if (kindOf (*node) != nodeKind::control)
            return nullptr;   // the fixed nodes and placeholders have no editor

        std::unique_ptr<juce::Component> header;
        if (type == PluginWindow::Type::normal && makeWindowHeader != nullptr)
            header.reset (makeWindowHeader (*node));

        auto title = displayName (*node);
        if (type == PluginWindow::Type::generic) title << ": all parameters";
        if (type == PluginWindow::Type::debug)   title << ": debug log";

        auto* w = activePluginWindows.add (new PluginWindow (node, type, activePluginWindows, title, std::move (header)));

        w->onClose = [this, n = Node::Ptr (node)] (PluginWindow& window)
        {
            if (onWindowClosing != nullptr)
                onWindowClosing (*n, window);
        };

        if (onWindowOpened != nullptr)
            onWindowOpened (*node, *w);

        return w;
    }

    void PluginGraph::closeCurrentlyOpenWindowsFor (NodeID nodeID)
    {
        for (int i = activePluginWindows.size(); --i >= 0;)
            if (activePluginWindows.getUnchecked (i)->node->nodeID == nodeID)
                activePluginWindows.remove (i);
    }

    bool PluginGraph::closeAnyOpenPluginWindows()
    {
        const bool wasEmpty = activePluginWindows.isEmpty();
        activePluginWindows.clear();
        return ! wasEmpty;
    }
}
