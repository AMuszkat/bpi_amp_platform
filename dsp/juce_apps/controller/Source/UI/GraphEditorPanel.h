/*
  ==============================================================================

   Derived from JUCE's extras/AudioPluginHost/Source/UI/GraphEditorPanel.h
   (Raw Material Software Limited, JUCE 8; AGPLv3 or the JUCE licence).

   Changes for the controller: the canvas, pins and connection dragging are unchanged. The
   document component around it has no audio player, MIDI keyboard or device settings;
   it shows the speakers panel and the bridge's status instead.

  ==============================================================================
*/

#pragma once

#include "../Plugins/PluginGraph.h"
#include "../Bridge/Bridge.h"
#include "SpeakersPanel.h"

namespace bpi
{
    class MainHostWindow;

    /** A panel that displays and edits a PluginGraph. */
    class GraphEditorPanel final : public juce::Component,
                                   public juce::ChangeListener
    {
    public:
        explicit GraphEditorPanel (PluginGraph& graph);
        ~GraphEditorPanel() override;

        void createNewPlugin (const ControlPluginType&, juce::Point<int> position);

        void paint (juce::Graphics&) override;
        void resized() override;

        void mouseDown (const juce::MouseEvent&) override;

        void changeListenerCallback (juce::ChangeBroadcaster*) override;

        void updateComponents();
        void showPopupMenu (juce::Point<int> position);

        void beginConnectorDrag (juce::AudioProcessorGraph::NodeAndChannel source,
                                 juce::AudioProcessorGraph::NodeAndChannel dest,
                                 const juce::MouseEvent&);
        void dragConnector (const juce::MouseEvent&);
        void endDraggingConnector (const juce::MouseEvent&);

        PluginGraph& graph;

    private:
        struct PluginComponent;
        struct ConnectorComponent;
        struct PinComponent;

        juce::OwnedArray<PluginComponent> nodes;
        juce::OwnedArray<ConnectorComponent> connectors;
        std::unique_ptr<ConnectorComponent> draggingConnector;
        std::unique_ptr<juce::PopupMenu> menu;

        PluginComponent* getComponentForPlugin (juce::AudioProcessorGraph::NodeID) const;
        ConnectorComponent* getComponentForConnection (const juce::AudioProcessorGraph::Connection&) const;
        PinComponent* findPinAt (juce::Point<float>) const;

        JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (GraphEditorPanel)
    };

    //==============================================================================
    /** The canvas, the speakers panel beside it and a status bar below. */
    class GraphDocumentComponent final : public juce::Component,
                                         private Bridge::Listener
    {
    public:
        GraphDocumentComponent (PluginGraph&, Bridge&);
        ~GraphDocumentComponent() override;

        void createNewPlugin (const ControlPluginType&, juce::Point<int> position);
        bool closeAnyOpenPluginWindows();

        void resized() override;

        std::unique_ptr<GraphEditorPanel> graphPanel;

    private:
        void bridgeStatusChanged() override;

        PluginGraph& graph;
        Bridge& bridge;

        struct StatusBar;
        std::unique_ptr<StatusBar> statusBar;
        std::unique_ptr<SpeakersPanel> speakersPanel;

        JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (GraphDocumentComponent)
    };
}
