/*
  ==============================================================================

   Derived from JUCE's extras/AudioPluginHost/Source/UI/GraphEditorPanel.cpp
   (Raw Material Software Limited, JUCE 8; AGPLv3 or the JUCE licence). See GraphEditorPanel.h.

  ==============================================================================
*/

#include "GraphEditorPanel.h"
#include "MainHostWindow.h"

namespace bpi
{
    using juce::AudioProcessorGraph;

    //==============================================================================
    struct GraphEditorPanel::PinComponent final : public juce::Component,
                                                  public juce::SettableTooltipClient
    {
        PinComponent (GraphEditorPanel& p, AudioProcessorGraph::NodeAndChannel pinToUse, bool isIn)
            : panel (p), graph (p.graph), pin (pinToUse), isInput (isIn)
        {
            if (auto node = graph.graph.getNodeForId (pin.nodeID))
            {
                juce::String tip;
                auto& processor = *node->getProcessor();

                if (auto* io = dynamic_cast<SpeakerIONode*> (&processor))
                {
                    // The speaker's own pins: FL, FR, Mine in; woofer, tweeter out.
                    tip = io->getPinName (pin.channelIndex);
                }
                else
                {
                    auto channel = processor.getOffsetInBusBufferForAbsoluteChannelIndex (isInput, pin.channelIndex, busIdx);

                    if (auto* bus = processor.getBus (isInput, busIdx))
                        tip = bus->getName() + ": " + juce::AudioChannelSet::getAbbreviatedChannelTypeName (bus->getCurrentLayout().getTypeOfChannel (channel));
                    else
                        tip = (isInput ? "Main Input: " : "Main Output: ") + juce::String (pin.channelIndex + 1);
                }

                setTooltip (tip);
            }

            setSize (16, 16);
        }

        void paint (juce::Graphics& g) override
        {
            auto w = (float) getWidth();
            auto h = (float) getHeight();

            juce::Path p;
            p.addEllipse (w * 0.25f, h * 0.25f, w * 0.5f, h * 0.5f);
            p.addRectangle (w * 0.4f, isInput ? (0.5f * h) : 0.0f, w * 0.2f, h * 0.5f);

            g.setColour (juce::Colours::green.withRotatedHue ((float) busIdx / 5.0f));
            g.fillPath (p);
        }

        void mouseDown (const juce::MouseEvent& e) override
        {
            AudioProcessorGraph::NodeAndChannel dummy { {}, 0 };
            panel.beginConnectorDrag (isInput ? dummy : pin, isInput ? pin : dummy, e);
        }

        void mouseDrag (const juce::MouseEvent& e) override   { panel.dragConnector (e); }
        void mouseUp (const juce::MouseEvent& e) override     { panel.endDraggingConnector (e); }

        GraphEditorPanel& panel;
        PluginGraph& graph;
        AudioProcessorGraph::NodeAndChannel pin;
        const bool isInput;
        int busIdx = 0;

        JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (PinComponent)
    };

    //==============================================================================
    struct GraphEditorPanel::PluginComponent final : public juce::Component
    {
        PluginComponent (GraphEditorPanel& p, AudioProcessorGraph::NodeID id) : panel (p), graph (p.graph), pluginID (id)
        {
            shadow.setShadowProperties (juce::DropShadow (juce::Colours::black.withAlpha (0.5f), 3, { 0, 1 }));
            setComponentEffect (&shadow);
            setSize (150, 60);
        }

        PluginComponent (const PluginComponent&) = delete;
        PluginComponent& operator= (const PluginComponent&) = delete;

        void mouseDown (const juce::MouseEvent& e) override
        {
            originalPos = localPointToGlobal (juce::Point<int>());
            toFront (true);

            if (e.mods.isPopupMenu())
                showPopupMenu();
        }

        void mouseDrag (const juce::MouseEvent& e) override
        {
            if (! e.mods.isPopupMenu())
            {
                auto pos = originalPos + e.getOffsetFromDragStart();

                if (getParentComponent() != nullptr)
                    pos = getParentComponent()->getLocalPoint (nullptr, pos);

                pos += getLocalBounds().getCentre();

                // Where a node sits is the canvas's own: it travels with the next revision.
                graph.setNodePosition (pluginID, { pos.x / (double) getParentWidth(), pos.y / (double) getParentHeight() });
                panel.updateComponents();
            }
        }

        void mouseUp (const juce::MouseEvent& e) override
        {
            if (! e.mouseWasDraggedSinceMouseDown() && e.getNumberOfClicks() == 2)
                showWindow (PluginWindow::Type::normal);
        }

        bool hitTest (int x, int y) override
        {
            for (auto* child : getChildren())
                if (child->getBounds().contains (x, y))
                    return true;

            return x >= 3 && x < getWidth() - 6 && y >= pinSize && y < getHeight() - pinSize;
        }

        void paint (juce::Graphics& g) override
        {
            auto boxArea = getLocalBounds().reduced (4, pinSize);
            bool isBypassed = false;
            bool isPlaceholder = false;

            if (auto* f = graph.graph.getNodeForId (pluginID))
            {
                isBypassed = f->isBypassed();
                isPlaceholder = f->properties["kind"].toString() == nodeKind::placeholder;
            }

            auto boxColour = findColour (juce::TextEditor::backgroundColourId);

            if (isBypassed)
                boxColour = boxColour.brighter();

            g.setColour (boxColour);
            g.fillRect (boxArea.toFloat());

            if (isPlaceholder)
            {
                g.setColour (juce::Colours::orange);
                g.drawRect (boxArea, 2);
            }

            g.setColour (findColour (juce::TextEditor::textColourId));
            g.setFont (font);
            g.drawFittedText (getName() + (isBypassed ? "\n(bypassed)" : ""), boxArea, juce::Justification::centred, 2);
        }

        void resized() override
        {
            if (auto f = graph.graph.getNodeForId (pluginID))
            {
                if (auto* processor = f->getProcessor())
                {
                    for (auto* pin : pins)
                    {
                        const bool isInput = pin->isInput;
                        auto channelIndex = pin->pin.channelIndex;
                        int busIdx = 0;
                        processor->getOffsetInBusBufferForAbsoluteChannelIndex (isInput, channelIndex, busIdx);

                        const int total = isInput ? numIns : numOuts;
                        auto totalSpaces = static_cast<float> (total) + (static_cast<float> (juce::jmax (0, processor->getBusCount (isInput) - 1)) * 0.5f);
                        auto indexPos = static_cast<float> (channelIndex) + (static_cast<float> (busIdx) * 0.5f);

                        pin->setBounds (proportionOfWidth ((1.0f + indexPos) / (totalSpaces + 1.0f)) - pinSize / 2,
                                        pin->isInput ? 0 : (getHeight() - pinSize),
                                        pinSize, pinSize);
                    }
                }
            }
        }

        juce::Point<float> getPinPos (int index, bool isInput) const
        {
            for (auto* pin : pins)
                if (pin->pin.channelIndex == index && isInput == pin->isInput)
                    return getPosition().toFloat() + pin->getBounds().getCentre().toFloat();

            return {};
        }

        void update()
        {
            const AudioProcessorGraph::Node::Ptr f (graph.graph.getNodeForId (pluginID));
            jassert (f != nullptr);

            auto& processor = *f->getProcessor();

            numIns = processor.getTotalNumInputChannels();
            numOuts = processor.getTotalNumOutputChannels();

            int w = 100;
            int h = 60;

            w = juce::jmax (w, (juce::jmax (numIns, numOuts) + 1) * 20);

            const auto name = PluginGraph::displayName (*f);
            const auto textWidth = juce::GlyphArrangement::getStringWidthInt (font, name);
            w = juce::jmax (w, 16 + juce::jmin (textWidth, 300));
            if (textWidth > 300)
                h = 100;

            setSize (w, h);
            setName (name);

            {
                auto p = graph.getNodePosition (pluginID);
                setCentreRelative ((float) p.x, (float) p.y);
            }

            if (numIns != numInputs || numOuts != numOutputs)
            {
                numInputs = numIns;
                numOutputs = numOuts;

                pins.clear();

                for (int i = 0; i < processor.getTotalNumInputChannels(); ++i)
                    addAndMakeVisible (pins.add (new PinComponent (panel, { pluginID, i }, true)));

                for (int i = 0; i < processor.getTotalNumOutputChannels(); ++i)
                    addAndMakeVisible (pins.add (new PinComponent (panel, { pluginID, i }, false)));

                resized();
            }

            repaint();
        }

        AudioProcessorGraph::Node* getNode() const   { return graph.graph.getNodeForId (pluginID); }

        void showPopupMenu()
        {
            auto* node = getNode();

            if (node == nullptr)
                return;

            const auto isPlugin = PluginGraph::isPluginNode (*node);
            const auto isControl = node->properties["kind"].toString() == nodeKind::control;

            menu.reset (new juce::PopupMenu);

            if (isPlugin)
                menu->addItem ("Delete this filter", [this] { graph.graph.removeNode (pluginID); });

            menu->addItem ("Disconnect all pins", [this] { graph.graph.disconnectNode (pluginID); });

            if (isPlugin)
                menu->addItem ("Toggle Bypass", [this]
                {
                    if (auto* n = graph.graph.getNodeForId (pluginID))
                        graph.setBypassed (pluginID, ! n->isBypassed());

                    repaint();
                });

            if (isControl)
            {
                menu->addSeparator();

                if (node->getProcessor()->hasEditor())
                    menu->addItem ("Show plugin GUI", [this] { showWindow (PluginWindow::Type::normal); });

                menu->addItem ("Show all parameters", [this] { showWindow (PluginWindow::Type::generic); });
                menu->addItem ("Show debug log", [this] { showWindow (PluginWindow::Type::debug); });
            }
            else if (isPlugin)
            {
                menu->addSeparator();
                menu->addItem ("No control build of " + node->properties["identifier"].toString(), false, false, [] {});
            }

            menu->showMenuAsync ({});
        }

        void showWindow (PluginWindow::Type type)
        {
            if (auto node = graph.graph.getNodeForId (pluginID))
                if (auto* w = graph.getOrCreateWindowFor (node, type))
                    w->toFront (true);
        }

        GraphEditorPanel& panel;
        PluginGraph& graph;
        const AudioProcessorGraph::NodeID pluginID;
        juce::OwnedArray<PinComponent> pins;
        int numInputs = 0, numOutputs = 0;
        int pinSize = 16;
        juce::Point<int> originalPos;
        juce::Font font = juce::FontOptions { 13.0f, juce::Font::bold };
        int numIns = 0, numOuts = 0;
        juce::DropShadowEffect shadow;
        std::unique_ptr<juce::PopupMenu> menu;
    };

    //==============================================================================
    struct GraphEditorPanel::ConnectorComponent final : public juce::Component,
                                                        public juce::SettableTooltipClient
    {
        explicit ConnectorComponent (GraphEditorPanel& p) : panel (p), graph (p.graph)
        {
            setAlwaysOnTop (true);
        }

        void setInput (AudioProcessorGraph::NodeAndChannel newSource)
        {
            if (connection.source != newSource)
            {
                connection.source = newSource;
                update();
            }
        }

        void setOutput (AudioProcessorGraph::NodeAndChannel newDest)
        {
            if (connection.destination != newDest)
            {
                connection.destination = newDest;
                update();
            }
        }

        void dragStart (juce::Point<float> pos)   { lastInputPos = pos;  resizeToFit(); }
        void dragEnd (juce::Point<float> pos)     { lastOutputPos = pos; resizeToFit(); }

        void update()
        {
            juce::Point<float> p1, p2;
            getPoints (p1, p2);

            if (lastInputPos != p1 || lastOutputPos != p2)
                resizeToFit();
        }

        void resizeToFit()
        {
            juce::Point<float> p1, p2;
            getPoints (p1, p2);

            auto newBounds = juce::Rectangle<float> (p1, p2).expanded (4.0f).getSmallestIntegerContainer();

            if (newBounds != getBounds())
                setBounds (newBounds);
            else
                resized();

            repaint();
        }

        void getPoints (juce::Point<float>& p1, juce::Point<float>& p2) const
        {
            p1 = lastInputPos;
            p2 = lastOutputPos;

            if (auto* src = panel.getComponentForPlugin (connection.source.nodeID))
                p1 = src->getPinPos (connection.source.channelIndex, false);

            if (auto* dest = panel.getComponentForPlugin (connection.destination.nodeID))
                p2 = dest->getPinPos (connection.destination.channelIndex, true);
        }

        void paint (juce::Graphics& g) override
        {
            g.setColour (juce::Colours::green);
            g.fillPath (linePath);
        }

        bool hitTest (int x, int y) override
        {
            auto pos = juce::Point<int> (x, y).toFloat();

            if (hitPath.contains (pos))
            {
                double distanceFromStart, distanceFromEnd;
                getDistancesFromEnds (pos, distanceFromStart, distanceFromEnd);

                // avoid clicking the connector when over a pin
                return distanceFromStart > 7.0 && distanceFromEnd > 7.0;
            }

            return false;
        }

        void mouseDown (const juce::MouseEvent&) override   { dragging = false; }

        void mouseDrag (const juce::MouseEvent& e) override
        {
            if (dragging)
            {
                panel.dragConnector (e);
            }
            else if (e.mouseWasDraggedSinceMouseDown())
            {
                dragging = true;

                graph.graph.removeConnection (connection);

                double distanceFromStart, distanceFromEnd;
                getDistancesFromEnds (getPosition().toFloat() + e.position, distanceFromStart, distanceFromEnd);
                const bool isNearerSource = (distanceFromStart < distanceFromEnd);

                AudioProcessorGraph::NodeAndChannel dummy { {}, 0 };

                panel.beginConnectorDrag (isNearerSource ? dummy : connection.source,
                                          isNearerSource ? connection.destination : dummy,
                                          e);
            }
        }

        void mouseUp (const juce::MouseEvent& e) override
        {
            if (dragging)
                panel.endDraggingConnector (e);
        }

        void resized() override
        {
            juce::Point<float> p1, p2;
            getPoints (p1, p2);

            lastInputPos = p1;
            lastOutputPos = p2;

            p1 -= getPosition().toFloat();
            p2 -= getPosition().toFloat();

            linePath.clear();
            linePath.startNewSubPath (p1);
            linePath.cubicTo (p1.x, p1.y + (p2.y - p1.y) * 0.33f,
                              p2.x, p1.y + (p2.y - p1.y) * 0.66f,
                              p2.x, p2.y);

            juce::PathStrokeType wideStroke (8.0f);
            wideStroke.createStrokedPath (hitPath, linePath);

            juce::PathStrokeType stroke (2.5f);
            stroke.createStrokedPath (linePath, linePath);

            auto arrowW = 5.0f;
            auto arrowL = 4.0f;

            juce::Path arrow;
            arrow.addTriangle (-arrowL, arrowW, -arrowL, -arrowW, arrowL, 0.0f);

            arrow.applyTransform (juce::AffineTransform()
                                    .rotated (juce::MathConstants<float>::halfPi - (float) std::atan2 (p2.x - p1.x, p2.y - p1.y))
                                    .translated ((p1 + p2) * 0.5f));

            linePath.addPath (arrow);
            linePath.setUsingNonZeroWinding (true);
        }

        void getDistancesFromEnds (juce::Point<float> p, double& distanceFromStart, double& distanceFromEnd) const
        {
            juce::Point<float> p1, p2;
            getPoints (p1, p2);

            distanceFromStart = p1.getDistanceFrom (p);
            distanceFromEnd   = p2.getDistanceFrom (p);
        }

        GraphEditorPanel& panel;
        PluginGraph& graph;
        AudioProcessorGraph::Connection connection { { {}, 0 }, { {}, 0 } };
        juce::Point<float> lastInputPos, lastOutputPos;
        juce::Path linePath, hitPath;
        bool dragging = false;

        JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (ConnectorComponent)
    };

    //==============================================================================
    GraphEditorPanel::GraphEditorPanel (PluginGraph& g) : graph (g)
    {
        graph.graph.addChangeListener (this);
        setOpaque (true);
    }

    GraphEditorPanel::~GraphEditorPanel()
    {
        graph.graph.removeChangeListener (this);
        draggingConnector = nullptr;
        nodes.clear();
        connectors.clear();
    }

    void GraphEditorPanel::paint (juce::Graphics& g)
    {
        g.fillAll (getLookAndFeel().findColour (juce::ResizableWindow::backgroundColourId));
    }

    void GraphEditorPanel::mouseDown (const juce::MouseEvent& e)
    {
        if (e.mods.isPopupMenu())
            showPopupMenu (e.position.toInt());
    }

    void GraphEditorPanel::createNewPlugin (const ControlPluginType& type, juce::Point<int> position)
    {
        graph.addPlugin (type, position.toDouble() / juce::Point<double> ((double) getWidth(), (double) getHeight()));
    }

    GraphEditorPanel::PluginComponent* GraphEditorPanel::getComponentForPlugin (AudioProcessorGraph::NodeID nodeID) const
    {
        for (auto* fc : nodes)
            if (fc->pluginID == nodeID)
                return fc;

        return nullptr;
    }

    GraphEditorPanel::ConnectorComponent* GraphEditorPanel::getComponentForConnection (const AudioProcessorGraph::Connection& conn) const
    {
        for (auto* cc : connectors)
            if (cc->connection == conn)
                return cc;

        return nullptr;
    }

    GraphEditorPanel::PinComponent* GraphEditorPanel::findPinAt (juce::Point<float> pos) const
    {
        for (auto* fc : nodes)
        {
            auto* comp = fc->getComponentAt (pos.toInt() - fc->getPosition());

            if (auto* pin = dynamic_cast<PinComponent*> (comp))
                return pin;
        }

        return nullptr;
    }

    void GraphEditorPanel::resized()
    {
        updateComponents();
    }

    void GraphEditorPanel::changeListenerCallback (juce::ChangeBroadcaster*)
    {
        updateComponents();
    }

    void GraphEditorPanel::updateComponents()
    {
        for (int i = nodes.size(); --i >= 0;)
            if (graph.graph.getNodeForId (nodes.getUnchecked (i)->pluginID) == nullptr)
                nodes.remove (i);

        for (int i = connectors.size(); --i >= 0;)
            if (! graph.graph.isConnected (connectors.getUnchecked (i)->connection))
                connectors.remove (i);

        for (auto* fc : nodes)
            fc->update();

        for (auto* cc : connectors)
            cc->update();

        for (auto* f : graph.graph.getNodes())
        {
            if (getComponentForPlugin (f->nodeID) == nullptr)
            {
                auto* comp = nodes.add (new PluginComponent (*this, f->nodeID));
                addAndMakeVisible (comp);
                comp->update();
            }
        }

        for (auto& c : graph.graph.getConnections())
        {
            if (getComponentForConnection (c) == nullptr)
            {
                auto* comp = connectors.add (new ConnectorComponent (*this));
                addAndMakeVisible (comp);

                comp->setInput (c.source);
                comp->setOutput (c.destination);
            }
        }
    }

    void GraphEditorPanel::showPopupMenu (juce::Point<int> mousePos)
    {
        menu.reset (new juce::PopupMenu);

        if (auto* mainWindow = findParentComponentOfClass<MainHostWindow>())
        {
            mainWindow->addPluginsToMenu (*menu);

            menu->showMenuAsync ({}, juce::ModalCallbackFunction::create ([this, mousePos] (int r)
            {
                if (auto* mainWin = findParentComponentOfClass<MainHostWindow>())
                    if (const auto* chosen = mainWin->getChosenType (r))
                        createNewPlugin (*chosen, mousePos);
            }));
        }
    }

    void GraphEditorPanel::beginConnectorDrag (AudioProcessorGraph::NodeAndChannel source,
                                               AudioProcessorGraph::NodeAndChannel dest,
                                               const juce::MouseEvent& e)
    {
        auto* c = dynamic_cast<ConnectorComponent*> (e.originalComponent);
        connectors.removeObject (c, false);
        draggingConnector.reset (c);

        if (draggingConnector == nullptr)
            draggingConnector.reset (new ConnectorComponent (*this));

        draggingConnector->setInput (source);
        draggingConnector->setOutput (dest);

        addAndMakeVisible (draggingConnector.get());
        draggingConnector->toFront (false);

        dragConnector (e);
    }

    void GraphEditorPanel::dragConnector (const juce::MouseEvent& e)
    {
        auto e2 = e.getEventRelativeTo (this);

        if (draggingConnector != nullptr)
        {
            draggingConnector->setTooltip ({});

            auto pos = e2.position;

            if (auto* pin = findPinAt (pos))
            {
                auto connection = draggingConnector->connection;

                if (connection.source.nodeID == AudioProcessorGraph::NodeID() && ! pin->isInput)
                    connection.source = pin->pin;
                else if (connection.destination.nodeID == AudioProcessorGraph::NodeID() && pin->isInput)
                    connection.destination = pin->pin;

                if (graph.graph.canConnect (connection))
                {
                    pos = (pin->getParentComponent()->getPosition() + pin->getBounds().getCentre()).toFloat();
                    draggingConnector->setTooltip (pin->getTooltip());
                }
            }

            if (draggingConnector->connection.source.nodeID == AudioProcessorGraph::NodeID())
                draggingConnector->dragStart (pos);
            else
                draggingConnector->dragEnd (pos);
        }
    }

    void GraphEditorPanel::endDraggingConnector (const juce::MouseEvent& e)
    {
        if (draggingConnector == nullptr)
            return;

        draggingConnector->setTooltip ({});

        auto e2 = e.getEventRelativeTo (this);
        auto connection = draggingConnector->connection;

        draggingConnector = nullptr;

        if (auto* pin = findPinAt (e2.position))
        {
            if (connection.source.nodeID == AudioProcessorGraph::NodeID())
            {
                if (pin->isInput)
                    return;

                connection.source = pin->pin;
            }
            else
            {
                if (! pin->isInput)
                    return;

                connection.destination = pin->pin;
            }

            graph.graph.addConnection (connection);
        }
    }

    //==============================================================================
    /** The tooltip under the mouse, or else the bridge's last word. */
    struct GraphDocumentComponent::StatusBar final : public juce::Component,
                                                     private juce::Timer
    {
        explicit StatusBar (Bridge& b) : bridge (b)   { startTimer (100); }

        void paint (juce::Graphics& g) override
        {
            g.fillAll (juce::Colours::lightgrey);
            g.setFont (juce::FontOptions ((float) getHeight() * 0.65f, juce::Font::bold));
            g.setColour (tip.isNotEmpty() ? juce::Colours::black : statusColour());
            g.drawFittedText (tip.isNotEmpty() ? tip : bridge.getStatus(), 10, 0, getWidth() - 12, getHeight(),
                              juce::Justification::centredLeft, 1);
        }

        juce::Colour statusColour() const
        {
            if (! bridge.isSynced() || bridge.isInsecure())   return juce::Colours::darkred;
            if (bridge.getStatus().contains ("rejected"))     return juce::Colours::darkred;
            if (bridge.hasPendingProposal())                  return juce::Colours::darkorange.darker();
            return juce::Colours::darkgreen;
        }

        void timerCallback() override
        {
            juce::String newTip;

            if (auto* underMouse = juce::Desktop::getInstance().getMainMouseSource().getComponentUnderMouse())
                if (auto* ttc = dynamic_cast<juce::TooltipClient*> (underMouse))
                    if (! (underMouse->isMouseButtonDown() || underMouse->isCurrentlyBlockedByAnotherModalComponent()))
                        newTip = ttc->getTooltip();

            if (newTip != tip || bridge.getStatus() != shownStatus)
            {
                tip = newTip;
                shownStatus = bridge.getStatus();
                repaint();
            }
        }

        Bridge& bridge;
        juce::String tip, shownStatus;
    };

    //==============================================================================
    GraphDocumentComponent::GraphDocumentComponent (PluginGraph& g, Bridge& b)
        : graph (g), bridge (b)
    {
        graphPanel.reset (new GraphEditorPanel (graph));
        addAndMakeVisible (graphPanel.get());

        speakersPanel.reset (new SpeakersPanel (bridge));
        addAndMakeVisible (speakersPanel.get());

        statusBar.reset (new StatusBar (bridge));
        addAndMakeVisible (statusBar.get());

        bridge.addListener (this);
        graphPanel->updateComponents();
    }

    GraphDocumentComponent::~GraphDocumentComponent()
    {
        bridge.removeListener (this);
        graphPanel = nullptr;
        speakersPanel = nullptr;
        statusBar = nullptr;
    }

    void GraphDocumentComponent::bridgeStatusChanged()
    {
        statusBar->repaint();
    }

    void GraphDocumentComponent::resized()
    {
        auto r = getLocalBounds();

        statusBar->setBounds (r.removeFromBottom (22));
        speakersPanel->setBounds (r.removeFromRight (260));
        graphPanel->setBounds (r);
    }

    void GraphDocumentComponent::createNewPlugin (const ControlPluginType& type, juce::Point<int> pos)
    {
        graphPanel->createNewPlugin (type, pos);
    }

    bool GraphDocumentComponent::closeAnyOpenPluginWindows()
    {
        return graph.closeAnyOpenPluginWindows();
    }
}
