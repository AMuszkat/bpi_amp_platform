/*
  ==============================================================================

   Derived from JUCE's extras/AudioPluginHost/Source/UI/MainHostWindow.cpp
   (Raw Material Software Limited, JUCE 8; AGPLv3 or the JUCE licence). See MainHostWindow.h.

  ==============================================================================
*/

#include "MainHostWindow.h"

namespace bpi
{
    namespace
    {
        enum MenuIds
        {
            quitId = 0x1000,
            deleteAllId,
            connectId,
            allWindowsForwardId,
            closeAllWindowsId
        };
    }

    MainHostWindow::MainHostWindow (PluginGraph& g, Bridge& b, juce::PropertiesFile* s)
        : DocumentWindow (juce::JUCEApplication::getInstance()->getApplicationName(),
                          juce::LookAndFeel::getDefaultLookAndFeel().findColour (juce::ResizableWindow::backgroundColourId),
                          DocumentWindow::allButtons),
          graph (g), bridge (b), settings (s)
    {
        setResizable (true, false);
        setResizeLimits (640, 400, 10000, 10000);
        centreWithSize (1000, 680);

        graphHolder.reset (new GraphDocumentComponent (graph, bridge));
        setContentNonOwned (graphHolder.get(), false);
        setUsingNativeTitleBar (true);

        if (settings != nullptr)
            restoreWindowStateFromString (settings->getValue ("mainWindowPos"));

        setVisible (true);
        bridge.addListener (this);
        bridgeStatusChanged();

       #if JUCE_MAC
        setMacMainMenu (this);
       #else
        setMenuBar (this);
       #endif
    }

    MainHostWindow::~MainHostWindow()
    {
        bridge.removeListener (this);

        if (settings != nullptr)
            settings->setValue ("mainWindowPos", getWindowStateAsString());

        clearContentComponent();

       #if JUCE_MAC
        setMacMainMenu (nullptr);
       #else
        setMenuBar (nullptr);
       #endif

        graphHolder = nullptr;
    }

    void MainHostWindow::closeButtonPressed()
    {
        // Nothing to save: the graph and its values live on the speakers.
        if (onQuitRequested != nullptr)
            onQuitRequested();
    }

    void MainHostWindow::bridgeStatusChanged()
    {
        auto title = juce::JUCEApplication::getInstance()->getApplicationName();

        if (bridge.isSynced())
            title << " - hub " << bridge.getHubName() << ", revision " << juce::String (bridge.getRevision());
        else if (bridge.getOptions().hubHost.isNotEmpty())
            title << " - " << bridge.getOptions().hubHost << " (not connected)";

        if (title != getName())
            setName (title);
    }

    void MainHostWindow::proposalRejected (juce::int64 revision, const juce::String& reason)
    {
        auto options = juce::MessageBoxOptions::makeOptionsOk (juce::MessageBoxIconType::WarningIcon,
                                                               "The hub rejected the edit",
                                                               "Revision " + juce::String (revision) + " was rejected:\n\n" + reason
                                                                   + "\n\nThe edit was undone.");
        messageBox = juce::AlertWindow::showScopedAsync (options, nullptr);
    }

    juce::StringArray MainHostWindow::getMenuBarNames()
    {
        return { "File", "Plugins", "Hub", "Windows" };
    }

    juce::PopupMenu MainHostWindow::getMenuForIndex (int topLevelMenuIndex, const juce::String&)
    {
        juce::PopupMenu menu;

        if (topLevelMenuIndex == 0)
        {
            menu.addItem (quitId, "Quit");
        }
        else if (topLevelMenuIndex == 1)
        {
            juce::PopupMenu pluginsMenu;
            addPluginsToMenu (pluginsMenu);
            menu.addSubMenu ("Create Plug-in", pluginsMenu, bridge.isSynced());
            menu.addSeparator();
            menu.addItem (deleteAllId, "Delete All Plug-ins", bridge.isSynced());
        }
        else if (topLevelMenuIndex == 2)
        {
            const auto host = bridge.getOptions().hubHost;
            menu.addItem (connectId, "Connect to a Hub...");
            menu.addSeparator();
            menu.addItem (-1, host.isEmpty() ? juce::String ("No hub")
                                             : "Hub " + host + (bridge.isSynced() ? ", revision " + juce::String (bridge.getRevision())
                                                                                  : juce::String (", not connected")),
                          false, false);
            menu.addItem (-1, "This controller: " + bridge.getOptions().name, false, false);
        }
        else if (topLevelMenuIndex == 3)
        {
            menu.addItem (allWindowsForwardId, "All Windows Forward");
            menu.addItem (closeAllWindowsId, "Close All Plug-in Windows");
        }

        return menu;
    }

    void MainHostWindow::menuItemSelected (int menuItemID, int)
    {
        if (menuItemID == quitId)
        {
            closeButtonPressed();
        }
        else if (menuItemID == deleteAllId)
        {
            // One edit: every plugin node goes, in one proposal.
            const auto nodes = graph.graph.getNodes();
            for (auto* n : nodes)
                if (PluginGraph::isPluginNode (*n))
                    graph.graph.removeNode (n->nodeID);
        }
        else if (menuItemID == connectId)
        {
            askForHub();
        }
        else if (menuItemID == allWindowsForwardId)
        {
            auto& desktop = juce::Desktop::getInstance();

            for (int i = 0; i < desktop.getNumComponents(); ++i)
                desktop.getComponent (i)->toBehind (this);
        }
        else if (menuItemID == closeAllWindowsId)
        {
            graph.closeAnyOpenPluginWindows();
        }
        else if (const auto* chosen = getChosenType (menuItemID))
        {
            graphHolder->createNewPlugin (*chosen, { proportionOfWidth (0.3f + juce::Random::getSystemRandom().nextFloat() * 0.4f),
                                                     proportionOfHeight (0.3f + juce::Random::getSystemRandom().nextFloat() * 0.4f) });
        }
    }

    void MainHostWindow::askForHub()
    {
        hubDialog = std::make_unique<juce::AlertWindow> ("Connect to a hub",
                                                         "The hub is the left speaker's ampd. Its address, as a name or an IP:",
                                                         juce::MessageBoxIconType::QuestionIcon, this);
        hubDialog->addTextEditor ("hub", bridge.getOptions().hubHost, "Hub");
        // A path, never the secret: the file holds it (mode 600), as on the speakers.
        hubDialog->addTextEditor ("secretFile", settings != nullptr ? settings->getValue ("secretFile") : juce::String(),
                                  "File with the pair's secret");
        hubDialog->addButton ("Connect", 1, juce::KeyPress (juce::KeyPress::returnKey));
        hubDialog->addButton ("Cancel", 0, juce::KeyPress (juce::KeyPress::escapeKey));

        hubDialog->enterModalState (true, juce::ModalCallbackFunction::create ([safe = SafePointer<MainHostWindow> (this)] (int result)
        {
            if (safe == nullptr || safe->hubDialog == nullptr)
                return;

            const auto host = safe->hubDialog->getTextEditorContents ("hub").trim();
            const auto secretFile = safe->hubDialog->getTextEditorContents ("secretFile").trim();
            safe->hubDialog.reset();

            if (result == 1 && host.isNotEmpty() && safe->onConnectRequested != nullptr)
                safe->onConnectRequested (host, secretFile);
        }), false);
    }

    void MainHostWindow::addPluginsToMenu (juce::PopupMenu& m)
    {
        int i = 0;

        for (const auto& t : ControlPlugins::all())
            m.addItem (++i, t.name + " (control build)");
    }

    const ControlPluginType* MainHostWindow::getChosenType (int menuID) const
    {
        const auto& all = ControlPlugins::all();
        const auto index = menuID - 1;
        return juce::isPositiveAndBelow (index, (int) all.size()) ? &all[(size_t) index] : nullptr;
    }
}
