#pragma once

#include "PluginProcessor.h"
#include "HostWindowTracker.h"
#include "MorphPad.h"
#include "Theme.h"

//==============================================================================
/** A flat rectangular button in the design's chrome style: BYPASS, SETUP, the
    popover's close cross and its FLAT / COPY / DONE row. */
class FlatButton  : public juce::Component
{
public:
    explicit FlatButton (juce::String buttonText);

    void setColours (juce::Colour text, juce::Colour background, juce::Colour border);
    void setHoverColours (juce::Colour text, juce::Colour border);
    void setFilled (bool shouldFill, juce::Colour fill = {}, juce::Colour textOnFill = {});

    /** Text width plus the design's 11px horizontal padding, for laying out a row. */
    int preferredWidth() const;
    void setButtonFont (juce::Font f)            { font = f; repaint(); }
    void setButtonText (juce::String t)          { text = std::move (t); repaint(); }
    juce::String getButtonText() const           { return text; }
    void setCornerRadius (float r)               { radius = r; repaint(); }

    std::function<void()> onClick;

    void paint (juce::Graphics&) override;
    void mouseEnter (const juce::MouseEvent&) override;
    void mouseExit  (const juce::MouseEvent&) override;
    void mouseUp    (const juce::MouseEvent&) override;

private:
    juce::String text;
    juce::Font font { Theme::mono (10.0f, true) };
    juce::Colour textColour  { Theme::inkDim };
    juce::Colour bgColour    { Theme::popHeadBg };
    juce::Colour borderColour{ Theme::hairline };
    juce::Colour hoverText   { Theme::ink };
    juce::Colour hoverBorder { Theme::hairlineLit };
    juce::Colour fillColour;
    bool filled = false, hovered = false;
    float radius = 3.0f;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (FlatButton)
};

//==============================================================================
/** One of the four S1..S4 chips under the pad: name, caret, threshold readout. */
class SettingChip  : public juce::Component
{
public:
    SettingChip (juce::RangedAudioParameter& thresholdParam, int settingIndex);

    void setActive (bool);
    void setOpen (bool);

    std::function<void()> onClick;

    void paint (juce::Graphics&) override;
    void mouseEnter (const juce::MouseEvent&) override;
    void mouseExit  (const juce::MouseEvent&) override;
    void mouseUp    (const juce::MouseEvent&) override;

private:
    int index;
    juce::Colour colour;
    float threshold = 0.0f;
    bool active = false, open = false, hovered = false;
    juce::ParameterAttachment attachment;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (SettingChip)
};

//==============================================================================
/** Vertical detector meter with a tick per setting threshold, plus its readout.

    The ticks are live controls: drag one to move that setting's threshold, with a pill
    showing its value in dB while hovered or dragged. Each threshold is bound through a
    ParameterAttachment, so a tick also moves the instant the threshold changes anywhere
    else -- the settings panel's row, host automation -- rather than waiting for the
    detector level to change and trigger a repaint. */
class LevelMeter  : public juce::Component
{
public:
    explicit LevelMeter (LoudnessProcessor&);

    /** Pushed by the editor's timer; repaints only when the drawn value moves. */
    void setLevel (float dbfs);

    void paint (juce::Graphics&) override;
    void mouseMove (const juce::MouseEvent&) override;
    void mouseExit (const juce::MouseEvent&) override;
    void mouseDown (const juce::MouseEvent&) override;
    void mouseDrag (const juce::MouseEvent&) override;
    void mouseUp   (const juce::MouseEvent&) override;

    static constexpr float minDb = -90.0f;      // matches the threshold parameter range

private:
    juce::Rectangle<float> barArea() const;
    float proportionFor (float dbfs) const;
    float yForDb (float dbfs) const;
    float dbForY (float y) const;

    /** The threshold tick within grabbing distance of `y`, or -1. */
    int tickAt (float y) const;

    std::array<float, LoudnessIDs::numSettings> thresholds {};
    std::array<std::unique_ptr<juce::ParameterAttachment>, LoudnessIDs::numSettings> attachments;
    float level = minDb;
    int hoverTick = -1, dragTick = -1;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (LevelMeter)
};

//==============================================================================
/** What a panel row edits. Either an automatable parameter -- through a
    ParameterAttachment, so edits are proper host gestures and outside changes show up -- or
    a plain view setting (the plot's range), which is UI state and not a parameter at all. */
class RowBinding
{
public:
    virtual ~RowBinding() = default;

    virtual float get() const = 0;                      // current value, in real units
    virtual float toProportion (float value) const = 0;
    virtual float fromProportion (float proportion) const = 0;
    virtual void beginEdit() {}
    virtual void edit (float value) = 0;
    virtual void endEdit() {}
    virtual juce::String text (float value) const = 0;  // used when the row has no formatter

    /** Apply a typed value. Units and a k multiplier are understood ("1.5 kHz"), and a
        choice parameter takes its own value names ("Force 2"). Unreadable text is ignored. */
    virtual void setFromText (const juce::String&) = 0;

    /** Set by the row; called when the value changed from somewhere else. */
    std::function<void()> onExternalChange;
};

/** A row bound to a parameter. */
class ParameterBinding final : public RowBinding
{
public:
    explicit ParameterBinding (juce::RangedAudioParameter&);

    float get() const override                      { return value; }
    float toProportion (float v) const override     { return param.convertTo0to1 (v); }
    float fromProportion (float p) const override   { return param.convertFrom0to1 (p); }
    void beginEdit() override                       { attachment.beginGesture(); }
    void edit (float v) override                    { attachment.setValueAsPartOfGesture (v); }
    void endEdit() override                         { attachment.endGesture(); }
    juce::String text (float) const override        { return param.getCurrentValueAsText(); }
    void setFromText (const juce::String&) override;

private:
    juce::RangedAudioParameter& param;
    float value = 0.0f;
    juce::ParameterAttachment attachment;
};

/** A row bound to a plain value through a getter and setter. The setter may clamp; the row
    always redraws from the getter, so it shows what was actually applied. */
class ValueBinding final : public RowBinding
{
public:
    ValueBinding (std::function<float()> getterIn, std::function<void (float)> setterIn,
                  juce::NormalisableRange<float> rangeIn)
        : getter (std::move (getterIn)), setter (std::move (setterIn)), range (std::move (rangeIn)) {}

    float get() const override                      { return getter(); }
    float toProportion (float v) const override     { return range.convertTo0to1 (range.snapToLegalValue (v)); }
    float fromProportion (float p) const override   { return range.snapToLegalValue (range.convertFrom0to1 (p)); }
    void edit (float v) override                    { setter (v); }
    juce::String text (float v) const override      { return juce::String (v, 1); }
    void setFromText (const juce::String&) override;

private:
    std::function<float()> getter;
    std::function<void (float)> setter;
    juce::NormalisableRange<float> range;
};

/** One draggable row inside a panel: label, track, value readout. */
class ParamRow  : public juce::Component
{
public:
    ParamRow (std::shared_ptr<RowBinding>, juce::String label, juce::Colour,
              bool emphasised, std::function<juce::String (float)> formatter);
    ~ParamRow() override;

    void paint (juce::Graphics&) override;
    void mouseDown (const juce::MouseEvent&) override;
    void mouseDrag (const juce::MouseEvent&) override;
    void mouseUp   (const juce::MouseEvent&) override;
    void mouseMove (const juce::MouseEvent&) override;

    /** Click the value to type one instead of dragging. */
    void beginTextEntry();
    bool isEditingText() const   { return textEditor.isVisible(); }

    /** Fired when text entry opens (true) and closes (false): the panel floats in its own
        window, which has to be given the keyboard explicitly. */
    std::function<void (bool)> onTextEntryActive;

    static constexpr int normalHeight = 22, emphasisedHeight = 26;

    /** Height this row wants, known before it is ever laid out. */
    int preferredHeight() const  { return emphasised ? emphasisedHeight : normalHeight; }

private:
    juce::Rectangle<float> trackArea() const;
    juce::Rectangle<int> valueArea() const;
    void applyDrag (const juce::MouseEvent&);
    void finishTextEntry (bool apply);

    std::shared_ptr<RowBinding> binding;
    juce::String label;
    juce::Colour colour;
    bool emphasised;
    std::function<juce::String (float)> format;
    juce::TextEditor textEditor;
    bool dragging = false, finishing = false;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (ParamRow)
};

//==============================================================================
/** The settings panel. Used for both a setting's parameters and, with a different row
    list, the detector globals the design has no room for.

    It floats in its own window below the card rather than over it, so the pad and the
    meter stay visible -- and live -- while it is open. */
class ParamPopover  : public juce::Component
{
public:
    struct RowSpec
    {
        juce::String label;
        std::shared_ptr<RowBinding> binding;        // rows with none are skipped
        bool emphasised = false;
        std::function<juce::String (float)> format;

        static RowSpec parameter (juce::String label, juce::RangedAudioParameter*, bool emphasised,
                                  std::function<juce::String (float)> format);

        static RowSpec value (juce::String label, std::function<float()> getter,
                              std::function<void (float)> setter, juce::NormalisableRange<float> range,
                              std::function<juce::String (float)> format);
    };

    struct ButtonSpec
    {
        juce::String text;
        std::function<void()> onClick;
        bool filled = false;
    };

    ParamPopover();

    void setContent (juce::String title, juce::Colour, std::vector<RowSpec>, std::vector<ButtonSpec>);
    int preferredHeight() const;

    std::function<void()> onClose;

    /** Forwarded from whichever row is being typed into. */
    std::function<void (bool)> onTextEntryActive;

    /** Scale to draw at when floating in its own window: the scale of the editor that
        opened it, so it matches the card. Must be set before addToDesktop(). */
    void setDesktopScale (float newScale)            { desktopScale = newScale; }
    float getDesktopScaleFactor() const override     { return desktopScale * juce::Desktop::getInstance().getGlobalScaleFactor(); }

    void paint (juce::Graphics&) override;
    void resized() override;

    static constexpr int width = 282;

private:
    float desktopScale = 1.0f;
    juce::String title;
    juce::Colour colour { Theme::accent() };
    std::vector<std::unique_ptr<ParamRow>> rows;
    std::vector<std::unique_ptr<FlatButton>> buttons;
    FlatButton closeButton { Theme::Glyph::cross() };

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (ParamPopover)
};

//==============================================================================
class LoudnessEditor  : public juce::AudioProcessorEditor,
                        private juce::Timer
{
public:
    explicit LoudnessEditor (LoudnessProcessor&);
    ~LoudnessEditor() override;

    void paint (juce::Graphics&) override;
    void resized() override;

private:
    void timerCallback() override;

    /** Writes a parameter as a complete gesture, by ID. A member rather than a local
        lambda on purpose: the popover's button callbacks outlive openSetting(), so a
        lambda capturing its local `state` reference would dangle. */
    void setParameter (const juce::String& parameterID, float denormalised);

    void openSetting (int index);
    void openGlobals();
    void openView();

    /** Applies a new plot range everywhere: processor (persisted), pad, open panel. */
    void setView (const LoudnessProcessor::ViewLimits&);
    void closePopover();

    /** Puts the panel on screen: its own floating window under the card when there is a
        desktop to float on, otherwise (headless) inside the card. */
    void showPanel();
    void placePanel();
    void refreshChrome();

    /** The design's activeIndex(): the open setting wins, otherwise the live zone. */
    int displayedSetting() const;

    LoudnessProcessor& processor;

    MorphPad pad;
    std::array<std::unique_ptr<SettingChip>, LoudnessIDs::numSettings> chips;
    LevelMeter meter;
    FlatButton bypassButton { "BYPASS" };
    FlatButton setupButton  { "SETUP" };
    FlatButton viewButton   { "VIEW" };
    ParamPopover popover;
    juce::Point<int> panelAnchor;           // editor screen position the panel was placed for
    float panelScale = 1.0f;                // scale the floating panel was created at
    HostWindowTracker hostWindow { *this }; // where the card really is; is its window active
    bool hideWhenInactive = false;          // only if activity could be read when it opened
    int activityCheck = 0, inactiveChecks = 0;

    static constexpr int globalsIndex = LoudnessIDs::numSettings;       // panel id for SETUP
    static constexpr int viewIndex    = LoudnessIDs::numSettings + 1;   // panel id for VIEW
    int openIndex = -1;

    LoudnessProcessor::ViewLimits view;     // the plot range currently shown
    int seenViewVersion = -1;               // processor's view version it reflects

    juce::ParameterAttachment bypassAttachment;
    bool bypassed = false;

    int shownZone = -1;
    float shownLevel = 1000.0f;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (LoudnessEditor)
};
