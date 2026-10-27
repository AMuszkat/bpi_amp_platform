#pragma once

#include "PluginProcessor.h"
#include "ScopeView.h"
#include "Theme.h"

//==============================================================================
/** The front panel.

    Laid out like the instrument it imitates: screen on the left, the knobs that
    shape what the screen shows down the right, one row per trace underneath, and
    a status line along the bottom saying what the acquisition is actually doing
    (which is where the answer lives whenever the screen is empty).

    The panel is a fixed size. A scope's graticule is only honest if the screen
    keeps its proportions, and a host that stretches a plugin editor would ruin
    that; setResizeLimits pins it, the same defence the loudness editor uses.
*/
class AmpScopeEditor  : public juce::AudioProcessorEditor,
                        private juce::Timer
{
public:
    explicit AmpScopeEditor (AmpScopeProcessor&);
    ~AmpScopeEditor() override;

    void paint (juce::Graphics&) override;
    void resized() override;

    static constexpr int cardWidth  = 880;
    static constexpr int cardHeight = 540;

private:
    //==========================================================================
    /** Dark theming for the stock widgets. The loudness plugin draws everything
        by hand because its pad is a bespoke instrument; here the controls are
        ordinary sliders and combo boxes, so colouring them is enough. */
    struct ScopeLookAndFeel  : public juce::LookAndFeel_V4
    {
        ScopeLookAndFeel();
    };

    /** A labelled control: caption on the left, widget filling the rest. */
    struct Field  : public juce::Component
    {
        Field (const juce::String& caption, juce::Component& widget, int captionWidth);

        void paint (juce::Graphics&) override;
        void resized() override;

        juce::String caption;
        juce::Component& control;
        int captionWidth;
    };

    /** One line of the trace table: colour swatch, name, the TDM slot this
        quantity is transmitted in, units/div, offset, and the min/max/mean
        measured over the window on screen.

        The slot menu belongs here rather than in a settings panel because it is
        per-quantity, exactly like everything else on the row -- and because
        seeing "Vout: ch0 hi" next to the Vout trace is the whole point. */
    struct TraceRow  : public juce::Component
    {
        TraceRow (AmpScopeProcessor&, amp::Channel);

        void paint (juce::Graphics&) override;
        void resized() override;

        /** Called from the editor's timer with what the screen just measured. */
        void update (const ScopeView::Measurement&, bool sourceProvidesIt);

        /** Rebuild the slot menu from the current hardware layout, marking the
            slots another amp on the same DOUT line has already claimed. */
        void refreshSlots (const amp::Device& mine, const juce::Array<amp::Device>& all);

        AmpScopeProcessor& processor;
        amp::Channel channel;
        juce::ToggleButton enabled;
        juce::ComboBox slot;
        juce::Slider perDiv { juce::Slider::LinearHorizontal, juce::Slider::TextBoxRight };
        juce::Slider offset { juce::Slider::LinearHorizontal, juce::Slider::TextBoxRight };
        juce::String readout;
        bool available = false;
        juce::String slotLayout;        ///< what the menu was last built against

        std::unique_ptr<juce::AudioProcessorValueTreeState::ButtonAttachment> enabledAttachment;
        std::unique_ptr<juce::AudioProcessorValueTreeState::SliderAttachment> perDivAttachment;
        std::unique_ptr<juce::AudioProcessorValueTreeState::SliderAttachment> offsetAttachment;
    };

    //==========================================================================
    void timerCallback() override;
    void rebuildDeviceList();
    void pushParametersToScope();

    AmpScopeProcessor& processor;
    ScopeLookAndFeel lookAndFeel;

    ScopeView scope;

    //--- header ---------------------------------------------------------------
    juce::ComboBox deviceBox;
    juce::TextButton runButton   { "RUN" };
    juce::TextButton singleButton { "SINGLE" };
    juce::TextButton rescanButton { "RESCAN" };

    //--- right-hand controls --------------------------------------------------
    juce::Slider   timePerDiv { juce::Slider::LinearHorizontal, juce::Slider::TextBoxRight };
    juce::ComboBox trigMode, trigSource, trigEdge;
    juce::Slider   trigLevel { juce::Slider::LinearHorizontal, juce::Slider::TextBoxRight };
    juce::ComboBox acqSource, captureRate, supplyAlign;

    juce::OwnedArray<Field> fields;
    juce::OwnedArray<TraceRow> rows;

    juce::String statusText;
    bool statusIsError = false;

    using SliderAttachment = juce::AudioProcessorValueTreeState::SliderAttachment;
    using ComboAttachment  = juce::AudioProcessorValueTreeState::ComboBoxAttachment;
    using ButtonAttachment = juce::AudioProcessorValueTreeState::ButtonAttachment;

    std::unique_ptr<SliderAttachment> timePerDivAttachment, trigLevelAttachment;
    std::unique_ptr<ComboAttachment>  trigModeAttachment, trigSourceAttachment, trigEdgeAttachment;
    std::unique_ptr<ComboAttachment>  acqSourceAttachment, captureRateAttachment, supplyAlignAttachment;
    std::unique_ptr<ButtonAttachment> runAttachment;

    int lastSelectionVersion = -1;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (AmpScopeEditor)
};
