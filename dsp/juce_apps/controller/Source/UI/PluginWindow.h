/*
  ==============================================================================

   Derived from JUCE's extras/AudioPluginHost/Source/UI/PluginWindow.h
   (Raw Material Software Limited, JUCE 8; AGPLv3 or the JUCE licence).

   Changes for the controller: a window can carry a header above the editor (the bridge's
   monitor speaker selector); closing a window says so (the bridge unsubscribes its monitors);
   the program list, the audio I/O configuration and the ARA host windows are gone.

  ==============================================================================
*/

#pragma once

#include <juce_audio_processors/juce_audio_processors.h>

#include <functional>

namespace bpi
{
    /** A window that shows a log of parameter change messages sent by the plugin. */
    class PluginDebugWindow final : public juce::AudioProcessorEditor,
                                    public juce::AudioProcessorParameter::Listener,
                                    public juce::ListBoxModel,
                                    public juce::AsyncUpdater
    {
    public:
        explicit PluginDebugWindow (juce::AudioProcessor& proc)
            : AudioProcessorEditor (proc), audioProc (proc)
        {
            setSize (500, 200);
            addAndMakeVisible (list);

            for (auto* p : audioProc.getParameters())
                p->addListener (this);

            log.add ("Parameter debug log started");
        }

        ~PluginDebugWindow() override
        {
            for (auto* p : audioProc.getParameters())
                p->removeListener (this);
        }

        void parameterValueChanged (int parameterIndex, float newValue) override
        {
            auto* param = audioProc.getParameters()[parameterIndex];
            auto value = param->getCurrentValueAsText().quoted() + " (" + juce::String (newValue, 4) + ")";
            appendToLog ("parameter change", *param, value);
        }

        void parameterGestureChanged (int parameterIndex, bool gestureIsStarting) override
        {
            auto* param = audioProc.getParameters()[parameterIndex];
            appendToLog ("gesture", *param, gestureIsStarting ? "start" : "end");
        }

    private:
        void appendToLog (juce::StringRef action, juce::AudioProcessorParameter& param, juce::StringRef value)
        {
            juce::String entry (action + " " + param.getName (30).quoted() + " [" + juce::String (param.getParameterIndex()) + "]: " + value);

            {
                const juce::ScopedLock lock (pendingLogLock);
                pendingLogEntries.add (entry);
            }

            triggerAsyncUpdate();
        }

        void resized() override    { list.setBounds (getLocalBounds()); }
        int getNumRows() override  { return log.size(); }

        void paintListBoxItem (int rowNumber, juce::Graphics& g, int width, int height, bool) override
        {
            g.setColour (getLookAndFeel().findColour (juce::TextEditor::textColourId));

            if (juce::isPositiveAndBelow (rowNumber, log.size()))
                g.drawText (log[rowNumber], juce::Rectangle<int> { 0, 0, width, height }, juce::Justification::left, true);
        }

        void handleAsyncUpdate() override
        {
            if (log.size() > logSizeTrimThreshold)
                log.removeRange (0, log.size() - maxLogSize);

            {
                const juce::ScopedLock lock (pendingLogLock);
                log.addArray (pendingLogEntries);
                pendingLogEntries.clear();
            }

            list.updateContent();
            list.scrollToEnsureRowIsOnscreen (log.size() - 1);
        }

        static constexpr int maxLogSize = 300;
        static constexpr int logSizeTrimThreshold = 400;

        juce::ListBox list { "Log", this };
        juce::StringArray log, pendingLogEntries;
        juce::CriticalSection pendingLogLock;
        juce::AudioProcessor& audioProc;
    };

    //==============================================================================
    /** A desktop window containing a plugin's GUI, with an optional header above it. */
    class PluginWindow final : public juce::DocumentWindow
    {
    public:
        enum class Type
        {
            normal = 0,
            generic,
            debug,
            numTypes
        };

        PluginWindow (juce::AudioProcessorGraph::Node* n, Type t, juce::OwnedArray<PluginWindow>& windowList,
                      const juce::String& title, std::unique_ptr<juce::Component> header)
            : DocumentWindow (title,
                              juce::LookAndFeel::getDefaultLookAndFeel().findColour (juce::ResizableWindow::backgroundColourId),
                              DocumentWindow::minimiseButton | DocumentWindow::closeButton),
              activeWindowList (windowList),
              node (n), type (t)
        {
            setSize (400, 300);

            if (auto* ui = createProcessorEditor (*node->getProcessor(), type))
            {
                const auto editorResizable = ui->isResizable();
                auto* holder = new Holder (std::move (header), ui);
                setContentOwned (holder, true);
                setResizable (editorResizable, false);

                if (editorResizable)
                    if (auto* c = ui->getConstrainer())
                        setResizeLimits (c->getMinimumWidth(), c->getMinimumHeight() + holder->headerHeight(),
                                         c->getMaximumWidth(), c->getMaximumHeight() + holder->headerHeight());
            }

            setTopLeftPosition (node->properties.getWithDefault (getLastXProp (type), 40 + juce::Random::getSystemRandom().nextInt (300)),
                                node->properties.getWithDefault (getLastYProp (type), 40 + juce::Random::getSystemRandom().nextInt (200)));

            node->properties.set (getOpenProp (type), true);
            setVisible (true);
        }

        ~PluginWindow() override
        {
            if (onClose != nullptr)
                onClose (*this);

            clearContentComponent();
        }

        void moved() override
        {
            node->properties.set (getLastXProp (type), getX());
            node->properties.set (getLastYProp (type), getY());
        }

        void closeButtonPressed() override
        {
            node->properties.set (getOpenProp (type), false);
            activeWindowList.removeObject (this);
        }

        juce::AudioProcessorEditor* getEditor() const
        {
            if (auto* holder = dynamic_cast<Holder*> (getContentComponent()))
                return holder->editor.get();

            return nullptr;
        }

        static juce::String getLastXProp (Type type)    { return "uiLastX_" + getTypeName (type); }
        static juce::String getLastYProp (Type type)    { return "uiLastY_" + getTypeName (type); }
        static juce::String getOpenProp  (Type type)    { return "uiopen_"  + getTypeName (type); }

        juce::OwnedArray<PluginWindow>& activeWindowList;
        const juce::AudioProcessorGraph::Node::Ptr node;
        const Type type;
        std::function<void (PluginWindow&)> onClose;

    private:
        /** The header (when there is one) above the editor; follows the editor's size. */
        struct Holder final : public juce::Component,
                              private juce::ComponentListener
        {
            Holder (std::unique_ptr<juce::Component> h, juce::AudioProcessorEditor* e)
                : header (std::move (h)), editor (e)
            {
                if (header != nullptr)
                    addAndMakeVisible (*header);

                addAndMakeVisible (*editor);
                editor->addComponentListener (this);
                fitToEditor();
            }

            ~Holder() override
            {
                editor->removeComponentListener (this);
            }

            int headerHeight() const    { return header != nullptr ? 30 : 0; }

            void fitToEditor()
            {
                const juce::ScopedValueSetter<bool> svs (layingOut, true);
                setSize (editor->getWidth(), editor->getHeight() + headerHeight());
                layout();
            }

            void layout()
            {
                auto r = getLocalBounds();

                if (header != nullptr)
                    header->setBounds (r.removeFromTop (headerHeight()));

                editor->setBounds (r);
            }

            void resized() override
            {
                if (! layingOut)
                {
                    const juce::ScopedValueSetter<bool> svs (layingOut, true);
                    layout();
                }
            }

            void componentMovedOrResized (juce::Component&, bool, bool wasResized) override
            {
                if (wasResized && ! layingOut)
                    fitToEditor();
            }

            std::unique_ptr<juce::Component> header;
            std::unique_ptr<juce::AudioProcessorEditor> editor;
            bool layingOut = false;
        };

        float getDesktopScaleFactor() const override     { return 1.0f; }

        static juce::AudioProcessorEditor* createProcessorEditor (juce::AudioProcessor& processor, Type type)
        {
            if (type == Type::normal)
            {
                if (processor.hasEditor())
                    if (auto* ui = processor.createEditorIfNeeded())
                        return ui;

                type = Type::generic;
            }

            if (type == Type::generic)
            {
                auto* result = new juce::GenericAudioProcessorEditor (processor);
                result->setResizeLimits (200, 300, 1'000, 10'000);
                return result;
            }

            if (type == Type::debug)
                return new PluginDebugWindow (processor);

            jassertfalse;
            return {};
        }

        static juce::String getTypeName (Type type)
        {
            switch (type)
            {
                case Type::normal:     return "Normal";
                case Type::generic:    return "Generic";
                case Type::debug:      return "Debug";
                case Type::numTypes:
                default:               return {};
            }
        }

        JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (PluginWindow)
    };
}
