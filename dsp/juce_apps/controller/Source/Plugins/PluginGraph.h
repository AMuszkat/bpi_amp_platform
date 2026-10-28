/*
  ==============================================================================

   Derived from JUCE's extras/AudioPluginHost/Source/Plugins/PluginGraph.h
   (Raw Material Software Limited, JUCE 8; AGPLv3 or the JUCE licence).

   Changes for the controller (dsp/juce_apps/controller/README.md): the graph mirrors the
   hub's graph document instead of a .filtergraph file. Node uids are the document's; the
   stream in (1) and the amplifier out (2) are fixed; plugins are the control builds compiled
   in, and a plugin the controller has no build for is kept as a placeholder. The graph is
   never prepared and never run.

  ==============================================================================
*/

#pragma once

#include "ControlPlugins.h"
#include "../Bridge/GraphDocument.h"
#include "../UI/PluginWindow.h"

namespace bpi
{
    /** A node's role, in node->properties["kind"]. */
    namespace nodeKind
    {
        constexpr const char* input = "input";
        constexpr const char* output = "output";
        constexpr const char* control = "control";          // a control build compiled in
        constexpr const char* placeholder = "placeholder";  // a plugin without one
    }

    class PluginGraph final : private juce::ChangeListener
    {
    public:
        PluginGraph();
        ~PluginGraph() override;

        using NodeID = juce::AudioProcessorGraph::NodeID;

        //==============================================================================
        /** Adds a node of a control build on the canvas, at the next free uid. A user's edit: the
            bridge proposes it. */
        NodeID addPlugin (const ControlPluginType&, juce::Point<double> position);

        void setNodePosition (NodeID, juce::Point<double>);
        juce::Point<double> getNodePosition (NodeID) const;

        /** Toggles a node's bypass: a user's edit, which the bridge proposes. */
        void setBypassed (NodeID, bool);

        static bool isPluginNode (const juce::AudioProcessorGraph::Node&);
        static juce::String displayName (const juce::AudioProcessorGraph::Node&);

        //==============================================================================
        /** Makes the canvas what `document` says: removes and adds nodes by uid (keeping, with
            their windows, the ones that stay), sets each node's state when it changed and its
            parameters, and the connections. `skipParameter` says which parameters not to set (a
            gesture of this controller in progress). With `existingNodeValues` false, the nodes that
            are already on the canvas keep their values (an undo: the document's values may be
            older than the ones the hub has since echoed). Returns what could not be shown. */
        juce::StringArray applyDocument (const juce::var& document,
                                         const std::function<bool (juce::uint32, const juce::String&)>& skipParameter,
                                         bool existingNodeValues = true);

        /** The canvas as a structure, to compare with the document's. */
        document::Structure structure() const;

        /** A node's parameters as a document's `params`, monitors left out. */
        juce::var currentParams (NodeID) const;

        //==============================================================================
        PluginWindow* getOrCreateWindowFor (juce::AudioProcessorGraph::Node*, PluginWindow::Type);
        void closeCurrentlyOpenWindowsFor (NodeID);
        bool closeAnyOpenPluginWindows();
        const juce::OwnedArray<PluginWindow>& getWindows() const   { return activePluginWindows; }

        /** Window open and close, for the bridge's monitor subscriptions. */
        std::function<void (juce::AudioProcessorGraph::Node&, PluginWindow&)> onWindowOpened;
        std::function<void (juce::AudioProcessorGraph::Node&, PluginWindow&)> onWindowClosing;
        std::function<juce::Component* (juce::AudioProcessorGraph::Node&)> makeWindowHeader;

        //==============================================================================
        juce::AudioProcessorGraph graph;

    private:
        juce::OwnedArray<PluginWindow> activePluginWindows;

        NodeID nextFreeUid() const;
        void changeListenerCallback (juce::ChangeBroadcaster*) override;
        void removeWindowsOfMissingNodes();

        JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (PluginGraph)
    };
}
