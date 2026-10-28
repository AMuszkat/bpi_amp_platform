/*
  ==============================================================================

   Derived from JUCE's extras/AudioPluginHost/Source/UI/MainHostWindow.h
   (Raw Material Software Limited, JUCE 8; AGPLv3 or the JUCE licence).

   Changes for the controller: no audio device, no plugin scanning, no file document. The
   Plugins menu lists the control builds compiled in; a Hub menu connects to a hub.

  ==============================================================================
*/

#pragma once

#include "GraphEditorPanel.h"

namespace bpi
{
    class MainHostWindow final : public juce::DocumentWindow,
                                 public juce::MenuBarModel,
                                 private Bridge::Listener
    {
    public:
        MainHostWindow (PluginGraph&, Bridge&, juce::PropertiesFile* settings);
        ~MainHostWindow() override;

        void closeButtonPressed() override;

        juce::StringArray getMenuBarNames() override;
        juce::PopupMenu getMenuForIndex (int topLevelMenuIndex, const juce::String& menuName) override;
        void menuItemSelected (int menuItemID, int topLevelMenuIndex) override;

        void addPluginsToMenu (juce::PopupMenu&);
        const ControlPluginType* getChosenType (int menuID) const;

        /** Asks for a hub address, and calls this with it. */
        std::function<void (const juce::String&)> onConnectRequested;
        std::function<void()> onQuitRequested;

        std::unique_ptr<GraphDocumentComponent> graphHolder;

    private:
        void bridgeStatusChanged() override;
        void proposalRejected (juce::int64 revision, const juce::String& reason) override;
        void askForHub();

        PluginGraph& graph;
        Bridge& bridge;
        juce::PropertiesFile* settings;
        juce::ScopedMessageBox messageBox;
        std::unique_ptr<juce::AlertWindow> hubDialog;

        JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (MainHostWindow)
    };
}
