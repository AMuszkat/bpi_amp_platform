#include "PluginEditor.h"

namespace
{
    /** Geometry of the "Morph Pad" design, in its own pixels. The card is fixed size:
        header, a body of pad + chips beside the meter column, then the footer. */
    namespace Layout
    {
        constexpr int cardWidth  = 620;
        constexpr int headerH    = 43;
        constexpr int footerH    = 29;
        constexpr int bodyPad    = 12;
        constexpr int padH       = 320;
        constexpr int chipsH     = 46;
        constexpr int chipGap    = 6;
        constexpr int meterW     = 60;
        constexpr int colGap     = 12;
        constexpr int rowGap     = 12;
        constexpr int chromeH    = 21;      // BYPASS / SETUP chip height
        constexpr int bodyH      = bodyPad * 2 + padH + rowGap + chipsH;
        constexpr int cardHeight = headerH + bodyH + footerH;
        constexpr int popBottomInset = 74;  // popover's distance above the body's bottom
    }

    juce::String signedDb (float v)   { return (v > 0.0f ? "+" : "") + juce::String (v, 1) + " dB"; }
    juce::String plainDb (float v)    { return juce::String (v, 1) + " dB"; }
    juce::String hertz (float v)      { return juce::String (v, 1) + " Hz"; }
    juce::String quality (float v)    { return juce::String (v, 2); }
    juce::String millis1 (float v)    { return juce::String (v, 1) + " ms"; }
    juce::String millis0 (float v)    { return juce::String (juce::roundToInt (v)) + " ms"; }

    /** Reads a number out of typed text: "7.0 dB" -> 7, "1.5 kHz" -> 1500, "-12" -> -12,
        "20k" -> 20000. Text with no number at all leaves the value alone. */
    float parseNumber (const juce::String& text, float fallback)
    {
        const auto lower = text.trim().toLowerCase();
        const auto digits = lower.retainCharacters ("0123456789.,+-").replaceCharacter (',', '.');

        if (digits.containsAnyOf ("0123456789"))
            return digits.getFloatValue() * (lower.contains ("k") ? 1000.0f : 1.0f);

        return fallback;
    }

    void drawBaselineText (juce::Graphics& g, const juce::String& text, float x, float baseline)
    {
        g.drawSingleLineText (text, juce::roundToInt (x), juce::roundToInt (baseline));
    }
}

//==============================================================================
FlatButton::FlatButton (juce::String buttonText)
    : text (std::move (buttonText))
{
    setMouseCursor (juce::MouseCursor::PointingHandCursor);
}

void FlatButton::setColours (juce::Colour t, juce::Colour background, juce::Colour border)
{
    textColour = t; bgColour = background; borderColour = border;
    repaint();
}

void FlatButton::setHoverColours (juce::Colour t, juce::Colour border)
{
    hoverText = t; hoverBorder = border;
    repaint();
}

void FlatButton::setFilled (bool shouldFill, juce::Colour fill, juce::Colour textOnFill)
{
    filled = shouldFill;

    if (shouldFill)
    {
        fillColour = fill;
        textColour = textOnFill;
    }

    repaint();
}

int FlatButton::preferredWidth() const
{
    return juce::GlyphArrangement::getStringWidthInt (font, text) + 22;
}

void FlatButton::paint (juce::Graphics& g)
{
    const auto b = getLocalBounds().toFloat();

    g.setColour (filled ? fillColour : bgColour);
    g.fillRoundedRectangle (b, radius);

    g.setColour (filled ? fillColour : (hovered ? hoverBorder : borderColour));
    g.drawRoundedRectangle (b.reduced (0.5f), radius, 1.0f);

    g.setColour (filled ? textColour : (hovered ? hoverText : textColour));
    g.setFont (font);
    g.drawText (text, getLocalBounds(), juce::Justification::centred);
}

void FlatButton::mouseEnter (const juce::MouseEvent&) { hovered = true;  repaint(); }
void FlatButton::mouseExit  (const juce::MouseEvent&) { hovered = false; repaint(); }

void FlatButton::mouseUp (const juce::MouseEvent& e)
{
    if (onClick != nullptr && getLocalBounds().contains (e.getPosition()))
        onClick();
}

//==============================================================================
SettingChip::SettingChip (juce::RangedAudioParameter& thresholdParam, int settingIndex)
    : index (settingIndex),
      colour (Theme::setting (settingIndex)),
      attachment (thresholdParam, [this] (float v) { threshold = v; repaint(); })
{
    setMouseCursor (juce::MouseCursor::PointingHandCursor);
    attachment.sendInitialUpdate();
}

void SettingChip::setActive (bool shouldBeActive)
{
    if (active != shouldBeActive) { active = shouldBeActive; repaint(); }
}

void SettingChip::setOpen (bool shouldBeOpen)
{
    if (open != shouldBeOpen) { open = shouldBeOpen; repaint(); }
}

void SettingChip::paint (juce::Graphics& g)
{
    const auto b = getLocalBounds().toFloat();

    g.setColour (active ? Theme::chipBgOn : Theme::chipBg);
    g.fillRoundedRectangle (b, 3.0f);

    const auto border = open      ? colour
                      : hovered   ? colour
                      : active    ? Theme::chipBorderOn
                                  : Theme::chipBorder;
    g.setColour (border);
    g.drawRoundedRectangle (b.reduced (0.5f), 3.0f, 1.0f);

    auto content = getLocalBounds().reduced (9, 8);
    auto topRow  = content.removeFromTop (12);

    g.setColour (colour);
    g.setFont (Theme::mono (9.0f, true));
    g.drawText ("S" + juce::String (index + 1), topRow, juce::Justification::centredLeft);

    g.setColour (open ? colour : Theme::caretOff);
    g.setFont (Theme::mono (9.0f));
    g.drawText (open ? Theme::Glyph::caretDown() : Theme::Glyph::caretUp(),
                topRow, juce::Justification::centredRight);

    content.removeFromTop (4);

    g.setColour (active ? Theme::ink : Theme::inkDim);
    g.setFont (Theme::mono (11.0f, true));
    g.drawText (juce::String (threshold, 1), content, juce::Justification::topLeft);
}

void SettingChip::mouseEnter (const juce::MouseEvent&) { hovered = true;  repaint(); }
void SettingChip::mouseExit  (const juce::MouseEvent&) { hovered = false; repaint(); }

void SettingChip::mouseUp (const juce::MouseEvent& e)
{
    if (onClick != nullptr && getLocalBounds().contains (e.getPosition()))
        onClick();
}

//==============================================================================
LevelMeter::LevelMeter (LoudnessProcessor& p)
{
    auto& state = p.getState();

    for (int s = 0; s < LoudnessIDs::numSettings; ++s)
    {
        if (auto* param = state.getParameter (LoudnessIDs::threshold (s)))
        {
            // Bound, not polled: the tick follows the parameter the moment it changes,
            // whoever changed it (this meter, the settings panel, host automation).
            attachments[(size_t) s] = std::make_unique<juce::ParameterAttachment> (
                *param, [this, s] (float v) { thresholds[(size_t) s] = v; repaint(); });
            attachments[(size_t) s]->sendInitialUpdate();
        }
    }
}

juce::Rectangle<float> LevelMeter::barArea() const
{
    auto area = getLocalBounds();
    area.removeFromBottom (13 + 8);         // readout + gap

    return juce::Rectangle<float> (14.0f, (float) area.getHeight())
               .withCentre ({ (float) area.getCentreX(), (float) area.getCentreY() });
}

float LevelMeter::proportionFor (float dbfs) const
{
    return juce::jlimit (0.0f, 1.0f, (dbfs - minDb) / (0.0f - minDb));
}

float LevelMeter::yForDb (float dbfs) const
{
    const auto bar = barArea();
    return bar.getBottom() - bar.getHeight() * proportionFor (dbfs);
}

float LevelMeter::dbForY (float y) const
{
    const auto bar = barArea();
    const auto proportion = juce::jlimit (0.0f, 1.0f, (bar.getBottom() - y) / bar.getHeight());
    return minDb + proportion * (0.0f - minDb);
}

int LevelMeter::tickAt (float y) const
{
    constexpr float grabDistance = 6.0f;
    int best = -1;
    float bestDistance = grabDistance;

    // Ties go to the later setting, which is the one drawn on top.
    for (int s = 0; s < LoudnessIDs::numSettings; ++s)
    {
        if (attachments[(size_t) s] == nullptr)
            continue;

        const auto distance = std::abs (yForDb (thresholds[(size_t) s]) - y);

        if (distance <= bestDistance)
        {
            best = s;
            bestDistance = distance;
        }
    }

    return best;
}

void LevelMeter::setLevel (float dbfs)
{
    if (! juce::approximatelyEqual (dbfs, level))
    {
        level = dbfs;
        repaint();
    }
}

void LevelMeter::paint (juce::Graphics& g)
{
    const auto bar = barArea();
    const auto labelArea = getLocalBounds().removeFromBottom (13);

    g.setColour (Theme::track);
    g.fillRoundedRectangle (bar, 3.0f);

    const auto filledHeight = bar.getHeight() * proportionFor (level);

    if (filledHeight > 0.5f)
    {
        const auto fill = bar.withTop (bar.getBottom() - filledHeight);

        g.setGradientFill (juce::ColourGradient (Theme::accent(), fill.getCentreX(), fill.getY(),
                                                 Theme::track,    fill.getCentreX(), bar.getBottom(),
                                                 false));
        g.fillRoundedRectangle (fill, 3.0f);
    }

    const int focusTick = dragTick >= 0 ? dragTick : hoverTick;

    // One tick per threshold, overhanging the bar by 5px each side as the design does. The
    // one under the pointer is drawn last and heavier, so it reads as the grabbed one.
    for (int pass = 0; pass < 2; ++pass)
    {
        for (int s = 0; s < LoudnessIDs::numSettings; ++s)
        {
            if (attachments[(size_t) s] == nullptr || (pass == 1) != (s == focusTick))
                continue;

            const auto y = yForDb (thresholds[(size_t) s]);
            const auto grabbed = (s == focusTick);
            const auto overhang = grabbed ? 7.0f : 5.0f;
            const auto thickness = grabbed ? 3.0f : 2.0f;

            g.setColour (Theme::setting (s));
            g.fillRect (juce::Rectangle<float> (bar.getX() - overhang, y - thickness * 0.5f,
                                                bar.getWidth() + 2.0f * overhang, thickness));
        }
    }

    // While a tick is hovered or dragged, a pill carries its value -- above the tick, or
    // below it when there is no room at the top of the bar.
    if (focusTick >= 0)
    {
        const auto y = yForDb (thresholds[(size_t) focusTick]);
        const auto font = Theme::mono (9.0f, true);
        const auto text = juce::String (thresholds[(size_t) focusTick], 1);
        const auto width = (float) juce::GlyphArrangement::getStringWidthInt (font, text) + 8.0f;
        constexpr float height = 13.0f;

        auto pill = juce::Rectangle<float> (width, height).withCentre ({ bar.getCentreX(), y - 4.0f - height * 0.5f });

        if (pill.getY() < bar.getY())
            pill = pill.withY (y + 4.0f);

        pill = pill.withX (juce::jlimit (0.0f, (float) getWidth() - width, pill.getX()));

        g.setColour (Theme::setting (focusTick));
        g.fillRoundedRectangle (pill, 3.0f);
        g.setColour (Theme::onAccent);
        g.setFont (font);
        g.drawText (text, pill, juce::Justification::centred);
    }

    g.setColour (Theme::inkDim);
    g.setFont (Theme::mono (10.0f, true));
    g.drawText (juce::String (juce::roundToInt (level)), labelArea, juce::Justification::centred);
}

void LevelMeter::mouseMove (const juce::MouseEvent& e)
{
    const int tick = tickAt (e.position.y);

    if (tick != hoverTick)
    {
        hoverTick = tick;
        repaint();
    }

    setMouseCursor (tick >= 0 ? juce::MouseCursor::UpDownResizeCursor
                              : juce::MouseCursor::NormalCursor);
}

void LevelMeter::mouseExit (const juce::MouseEvent&)
{
    if (hoverTick >= 0 && dragTick < 0)
    {
        hoverTick = -1;
        repaint();
    }
}

void LevelMeter::mouseDown (const juce::MouseEvent& e)
{
    dragTick = tickAt (e.position.y);

    if (dragTick < 0)
        return;

    attachments[(size_t) dragTick]->beginGesture();
    mouseDrag (e);
}

void LevelMeter::mouseDrag (const juce::MouseEvent& e)
{
    if (dragTick >= 0)
        attachments[(size_t) dragTick]->setValueAsPartOfGesture (dbForY (e.position.y));
}

void LevelMeter::mouseUp (const juce::MouseEvent& e)
{
    if (dragTick < 0)
        return;

    attachments[(size_t) dragTick]->endGesture();
    dragTick = -1;
    hoverTick = tickAt (e.position.y);
    repaint();
}

//==============================================================================
ParameterBinding::ParameterBinding (juce::RangedAudioParameter& p)
    : param (p),
      attachment (p, [this] (float v)
                  {
                      value = v;

                      if (onExternalChange != nullptr)
                          onExternalChange();
                  })
{
    attachment.sendInitialUpdate();
}

void ParameterBinding::setFromText (const juce::String& text)
{
    // A choice parameter knows its own value names, so let it do the reading; anything else
    // is a number in the parameter's own units.
    const auto normalised = dynamic_cast<const juce::AudioParameterChoice*> (&param) != nullptr
                              ? param.getValueForText (text)
                              : param.convertTo0to1 (parseNumber (text, value));

    attachment.setValueAsCompleteGesture (param.convertFrom0to1 (juce::jlimit (0.0f, 1.0f, normalised)));
}

void ValueBinding::setFromText (const juce::String& text)
{
    setter (range.snapToLegalValue (parseNumber (text, getter())));
}

ParamPopover::RowSpec ParamPopover::RowSpec::parameter (juce::String label, juce::RangedAudioParameter* p,
                                                       bool emphasised, std::function<juce::String (float)> format)
{
    return { std::move (label), p != nullptr ? std::make_shared<ParameterBinding> (*p) : nullptr,
             emphasised, std::move (format) };
}

ParamPopover::RowSpec ParamPopover::RowSpec::value (juce::String label, std::function<float()> getter,
                                                   std::function<void (float)> setter,
                                                   juce::NormalisableRange<float> range,
                                                   std::function<juce::String (float)> format)
{
    return { std::move (label),
             std::make_shared<ValueBinding> (std::move (getter), std::move (setter), std::move (range)),
             false, std::move (format) };
}

//==============================================================================
ParamRow::ParamRow (std::shared_ptr<RowBinding> b, juce::String rowLabel, juce::Colour c,
                    bool isEmphasised, std::function<juce::String (float)> formatter)
    : binding (std::move (b)),
      label (std::move (rowLabel)),
      colour (c),
      emphasised (isEmphasised),
      format (std::move (formatter))
{
    setMouseCursor (juce::MouseCursor::LeftRightResizeCursor);
    binding->onExternalChange = [this] { if (! isEditingText()) repaint(); };

    textEditor.setFont (Theme::mono (11.0f, true));
    textEditor.setJustification (juce::Justification::centredRight);
    textEditor.setMultiLine (false);
    textEditor.setReturnKeyStartsNewLine (false);
    textEditor.setSelectAllWhenFocused (true);
    textEditor.setBorder (juce::BorderSize<int> (1));
    textEditor.setIndents (3, 0);
    textEditor.setColour (juce::TextEditor::backgroundColourId, Theme::sunken);
    textEditor.setColour (juce::TextEditor::textColourId, Theme::ink);
    textEditor.setColour (juce::TextEditor::outlineColourId, colour);
    textEditor.setColour (juce::TextEditor::focusedOutlineColourId, colour);
    textEditor.setColour (juce::TextEditor::highlightColourId, colour.withAlpha (0.35f));
    textEditor.setColour (juce::TextEditor::highlightedTextColourId, Theme::ink);
    textEditor.setColour (juce::CaretComponent::caretColourId, colour);
    textEditor.onReturnKey = [this] { finishTextEntry (true); };
    textEditor.onEscapeKey = [this] { finishTextEntry (false); };
    textEditor.onFocusLost = [this] { finishTextEntry (true); };   // clicking away commits
    addChildComponent (textEditor);
}

juce::Rectangle<int> ParamRow::valueArea() const
{
    return getLocalBounds().removeFromRight (62);
}

void ParamRow::beginTextEntry()
{
    if (isEditingText())
        return;

    textEditor.setBounds (valueArea());
    textEditor.setText (format != nullptr ? format (binding->get()) : binding->text (binding->get()), false);
    textEditor.setVisible (true);
    textEditor.grabKeyboardFocus();

    if (onTextEntryActive != nullptr)
        onTextEntryActive (true);
    textEditor.selectAll();
    repaint();
}

void ParamRow::finishTextEntry (bool apply)
{
    if (finishing || ! isEditingText())
        return;

    const juce::ScopedValueSetter<bool> scope (finishing, true);   // hiding it fires onFocusLost
    const auto typed = textEditor.getText();

    textEditor.setVisible (false);

    if (onTextEntryActive != nullptr)
        onTextEntryActive (false);

    if (apply)
        binding->setFromText (typed);

    repaint();
}

ParamRow::~ParamRow()
{
    binding->onExternalChange = nullptr;    // the binding is shared; do not leave it calling us
}

juce::Rectangle<float> ParamRow::trackArea() const
{
    auto b = getLocalBounds().toFloat();
    b.removeFromLeft (56.0f + 10.0f);
    b.removeFromRight (62.0f + 10.0f);
    return b;
}

void ParamRow::paint (juce::Graphics& g)
{
    auto b = getLocalBounds();
    const auto value = binding->get();

    g.setColour (emphasised ? Theme::inkLabel : Theme::inkDim);
    g.setFont (Theme::mono (10.0f));
    g.drawText (label, b.removeFromLeft (56), juce::Justification::centredLeft);

    auto valueBox = b.removeFromRight (62);

    const auto track = trackArea();
    const auto line = juce::Rectangle<float> (track.getX(), track.getCentreY() - 2.0f,
                                              track.getWidth(), 4.0f);

    g.setColour (Theme::track);
    g.fillRoundedRectangle (line, 2.0f);

    const auto proportion = juce::jlimit (0.0f, 1.0f, binding->toProportion (value));

    if (proportion > 0.0f)
    {
        g.setColour (colour);
        g.fillRoundedRectangle (line.withWidth (line.getWidth() * proportion), 2.0f);
    }

    g.setColour (Theme::ink);
    g.fillRoundedRectangle (juce::Rectangle<float> (3.0f, 14.0f)
                                .withCentre ({ line.getX() + line.getWidth() * proportion,
                                               line.getCentreY() }),
                            2.0f);

    if (! isEditingText())
    {
        g.setFont (Theme::mono (11.0f, true));
        g.drawText (format != nullptr ? format (value) : binding->text (value),
                    valueBox, juce::Justification::centredRight);
    }
}

void ParamRow::applyDrag (const juce::MouseEvent& e)
{
    const auto track = trackArea();
    const auto u = juce::jlimit (0.0f, 1.0f, (e.position.x - track.getX()) / track.getWidth());
    binding->edit (binding->fromProportion (u));
    repaint();
}

void ParamRow::mouseDown (const juce::MouseEvent& e)
{
    // Click the value to type it. Clicking the label does nothing -- it used to be taken as a
    // drag to the far left, which slammed the parameter to its minimum.
    if (valueArea().contains (e.getPosition()))
    {
        beginTextEntry();
        return;
    }

    if (! trackArea().contains (e.position))
        return;

    dragging = true;
    binding->beginEdit();
    applyDrag (e);
}

void ParamRow::mouseDrag (const juce::MouseEvent& e)
{
    if (dragging)
        applyDrag (e);
}

void ParamRow::mouseUp (const juce::MouseEvent&)
{
    if (std::exchange (dragging, false))
        binding->endEdit();
}

void ParamRow::mouseMove (const juce::MouseEvent& e)
{
    setMouseCursor (valueArea().contains (e.getPosition()) ? juce::MouseCursor::IBeamCursor
                  : trackArea().contains (e.position)      ? juce::MouseCursor::LeftRightResizeCursor
                                                           : juce::MouseCursor::NormalCursor);
}

//==============================================================================
ParamPopover::ParamPopover()
{
    // Not opaque: as its own window this gives it an ARGB visual (addToDesktop derives
    // windowIsSemiTransparent from isOpaque()), so the rounded corners stay rounded.
    setOpaque (false);

    closeButton.setButtonFont (Theme::mono (11.0f, true));
    closeButton.setColours (Theme::inkDim, Theme::popHeadBg.withAlpha (0.0f), Theme::popHeadBg.withAlpha (0.0f));
    closeButton.setHoverColours (Theme::ink, Theme::track);
    closeButton.onClick = [this] { if (onClose != nullptr) onClose(); };
    addAndMakeVisible (closeButton);
}

void ParamPopover::setContent (juce::String popTitle, juce::Colour c,
                               std::vector<RowSpec> rowSpecs, std::vector<ButtonSpec> buttonSpecs)
{
    title = std::move (popTitle);
    colour = c;

    rows.clear();
    buttons.clear();

    for (auto& spec : rowSpecs)
    {
        if (spec.binding == nullptr)
            continue;

        auto row = std::make_unique<ParamRow> (spec.binding, spec.label, colour,
                                               spec.emphasised, spec.format);
        row->onTextEntryActive = [this] (bool active)
        {
            if (onTextEntryActive != nullptr)
                onTextEntryActive (active);
        };
        addAndMakeVisible (*row);
        rows.push_back (std::move (row));
    }

    for (auto& spec : buttonSpecs)
    {
        auto button = std::make_unique<FlatButton> (spec.text);
        button->setButtonFont (Theme::mono (10.0f, true));

        if (spec.filled)
            button->setFilled (true, colour, Theme::onAccent);
        else
            button->setColours (Theme::inkDim, Theme::popHeadBg, Theme::hairline);

        button->onClick = spec.onClick;
        addAndMakeVisible (*button);
        buttons.push_back (std::move (button));
    }

    resized();
    repaint();
}

int ParamPopover::preferredHeight() const
{
    int height = 32 + 11;       // header + top padding

    for (size_t i = 0; i < rows.size(); ++i)
        height += rows[i]->preferredHeight() + (i > 0 ? 2 : 0);

    height += 13;               // bottom padding of the row block
    height += 25 + 12;          // button row + its bottom padding
    return height;
}

void ParamPopover::resized()
{
    auto b = getLocalBounds();
    auto header = b.removeFromTop (32);

    closeButton.setBounds (header.removeFromRight (34).reduced (6, 9));

    b.removeFromTop (11);
    auto buttonRow = b.removeFromBottom (12 + 25).withTrimmedBottom (12).reduced (12, 0);
    b.removeFromBottom (13);
    b = b.reduced (12, 0);

    for (size_t i = 0; i < rows.size(); ++i)
    {
        if (i > 0)
            b.removeFromTop (2);

        rows[i]->setBounds (b.removeFromTop (rows[i]->preferredHeight()));
    }

    if (! buttons.empty())
    {
        const int n = (int) buttons.size();

        for (int i = 0; i < n; ++i)
        {
            const int x0 = buttonRow.getX() + juce::roundToInt ((float) (buttonRow.getWidth() + 6) * (float) i / (float) n);
            const int x1 = buttonRow.getX() + juce::roundToInt ((float) (buttonRow.getWidth() + 6) * (float) (i + 1) / (float) n) - 6;
            buttons[(size_t) i]->setBounds (x0, buttonRow.getY(), x1 - x0, buttonRow.getHeight());
        }
    }
}

void ParamPopover::paint (juce::Graphics& g)
{
    const auto b = getLocalBounds().toFloat();

    juce::Path shape;
    shape.addRoundedRectangle (b, 5.0f);

    g.setColour (Theme::popBg);
    g.fillPath (shape);

    {
        juce::Graphics::ScopedSaveState clipped (g);
        g.reduceClipRegion (shape);
        g.setColour (Theme::popHeadBg);
        g.fillRect (b.withHeight (32.0f));
    }

    g.setColour (Theme::footerLine);
    g.drawLine (b.getX(), 32.0f, b.getRight(), 32.0f, 1.0f);

    g.setColour (colour);
    g.fillEllipse (juce::Rectangle<float> (7.0f, 7.0f).withCentre ({ b.getX() + 12.0f + 3.5f, 16.0f }));

    g.setColour (Theme::ink);
    g.setFont (Theme::mono (11.0f, true));
    g.drawText (title, juce::Rectangle<int> (12 + 7 + 8, 0, getWidth() - 60, 32),
                juce::Justification::centredLeft);

    g.setColour (colour);
    g.drawRoundedRectangle (b.reduced (0.5f), 5.0f, 1.0f);
}

//==============================================================================
LoudnessEditor::LoudnessEditor (LoudnessProcessor& p)
    : AudioProcessorEditor (&p),
      processor (p),
      pad (p),
      meter (p),
      bypassAttachment (*p.getState().getParameter (LoudnessIDs::bypass),
                        [this] (float v)
                        {
                            bypassed = v > 0.5f;
                            bypassButton.setFilled (bypassed, Theme::accent(), Theme::onAccent);

                            if (! bypassed)
                                bypassButton.setColours (Theme::inkMuted, Theme::sunken, Theme::hairline);
                        })
{
    auto& state = processor.getState();

    addAndMakeVisible (pad);
    pad.onSelectSetting = [this] (int index) { openSetting (index); };

    for (int s = 0; s < LoudnessIDs::numSettings; ++s)
    {
        auto* thresholdParam = state.getParameter (LoudnessIDs::threshold (s));
        jassert (thresholdParam != nullptr);

        chips[(size_t) s] = std::make_unique<SettingChip> (*thresholdParam, s);
        chips[(size_t) s]->onClick = [this, s] { openIndex == s ? closePopover() : openSetting (s); };
        addAndMakeVisible (*chips[(size_t) s]);
    }

    addAndMakeVisible (meter);

    bypassButton.setColours (Theme::inkMuted, Theme::sunken, Theme::hairline);
    bypassButton.setHoverColours (Theme::ink, Theme::hairlineLit);
    bypassButton.onClick = [this]
    {
        if (auto* param = processor.getState().getParameter (LoudnessIDs::bypass))
        {
            param->beginChangeGesture();
            param->setValueNotifyingHost (bypassed ? 0.0f : 1.0f);
            param->endChangeGesture();
        }
    };
    addAndMakeVisible (bypassButton);

    // Not in the design, but the detector globals (Mode / Detector / Attack / Release /
    // Hysteresis / Glide) are real automatable parameters and the morph pad has nowhere
    // to show them. They get the design's own popover rather than a new visual language.
    setupButton.setColours (Theme::inkMuted, Theme::sunken, Theme::hairline);
    setupButton.setHoverColours (Theme::ink, Theme::hairlineLit);
    setupButton.onClick = [this] { openIndex == globalsIndex ? closePopover() : openGlobals(); };
    addAndMakeVisible (setupButton);

    // The plot range: not in the design, and not a parameter (see ViewLimits).
    viewButton.setColours (Theme::inkMuted, Theme::sunken, Theme::hairline);
    viewButton.setHoverColours (Theme::ink, Theme::hairlineLit);
    viewButton.onClick = [this] { openIndex == viewIndex ? closePopover() : openView(); };
    addAndMakeVisible (viewButton);

    view = processor.getViewLimits();
    seenViewVersion = processor.getViewVersion();
    pad.setViewLimits (view);

    // The panel is not a child: showPanel() puts it in its own window below the card.
    popover.onClose = [this] { closePopover(); };

    // A floating panel has to be handed the keyboard explicitly (see HostWindowTracker).
    popover.onTextEntryActive = [this] (bool active)
    {
        if (! popover.isOnDesktop())
            return;

        if (active)
            hostWindow.takeKeyboardFocus (popover);
    };

    bypassAttachment.sendInitialUpdate();

    // Fixed size, and enforced. Without a constrainer, whatever area the host hands the
    // editor -- a maximised plugin window, say -- the LV2 wrapper's setBoundsConstrained()
    // simply stretches it, and this fixed layout smears across the whole screen. min == max
    // also keeps resizableByHost false, so the generated ui.ttl keeps ui:noUserResize.
    setResizeLimits (Layout::cardWidth, Layout::cardHeight, Layout::cardWidth, Layout::cardHeight);
    setSize (Layout::cardWidth, Layout::cardHeight);
    refreshChrome();
    startTimerHz (60);
}

LoudnessEditor::~LoudnessEditor() { stopTimer(); }

//==============================================================================
void LoudnessEditor::setParameter (const juce::String& parameterID, float denormalised)
{
    if (auto* param = processor.getState().getParameter (parameterID))
    {
        param->beginChangeGesture();
        param->setValueNotifyingHost (param->convertTo0to1 (denormalised));
        param->endChangeGesture();
    }
}

int LoudnessEditor::displayedSetting() const
{
    return juce::isPositiveAndBelow (openIndex, LoudnessIDs::numSettings)
             ? openIndex
             : processor.getActiveSetting();
}

void LoudnessEditor::refreshChrome()
{
    const int zone = processor.getActiveSetting();

    for (int s = 0; s < LoudnessIDs::numSettings; ++s)
    {
        chips[(size_t) s]->setActive (s == zone);
        chips[(size_t) s]->setOpen (s == openIndex);
    }

    pad.setDisplayedSetting (displayedSetting());
    repaint (0, getHeight() - Layout::footerH, getWidth(), Layout::footerH);
}

void LoudnessEditor::openSetting (int index)
{
    auto& state = processor.getState();
    const auto colour = Theme::setting (index);

    const auto gainID = [index] (int band) { return LoudnessIDs::band (index, band, "gain"); };
    const auto freqID = [index] (int band) { return LoudnessIDs::band (index, band, "freq"); };
    const auto qID    = [index] (int band) { return LoudnessIDs::band (index, band, "q"); };

    using Row = ParamPopover::RowSpec;
    std::vector<Row> rows {
        Row::parameter ("Threshold", state.getParameter (LoudnessIDs::threshold (index)), true, plainDb),
        Row::parameter ("EQ1 gain",  state.getParameter (gainID (0)), false, signedDb),
        Row::parameter ("EQ1 freq",  state.getParameter (freqID (0)), false, hertz),
        Row::parameter ("EQ1 Q",     state.getParameter (qID (0)),    false, quality),
        Row::parameter ("EQ2 gain",  state.getParameter (gainID (1)), false, signedDb),
        Row::parameter ("EQ2 freq",  state.getParameter (freqID (1)), false, hertz),
        Row::parameter ("EQ2 Q",     state.getParameter (qID (1)),    false, quality)
    };

    std::vector<ParamPopover::ButtonSpec> buttons {
        { "FLAT", [this, index]
                  {
                      // The design's FLAT zeroes both gains and leaves freq / Q alone.
                      for (int b = 0; b < LoudnessIDs::numBands; ++b)
                          setParameter (LoudnessIDs::band (index, b, "gain"), 0.0f);
                  }, false },
        { "COPY " + Theme::Glyph::arrow(),
                  [this, index]
                  {
                      const int next = (index + 1) % LoudnessIDs::numSettings;
                      auto& apvts = processor.getState();

                      for (int b = 0; b < LoudnessIDs::numBands; ++b)
                          for (auto* what : { "gain", "freq", "q" })
                              if (auto* source = apvts.getRawParameterValue (LoudnessIDs::band (index, b, what)))
                                  setParameter (LoudnessIDs::band (next, b, what), source->load());

                      // The design follows the copy into the next setting -- but doing that
                      // here would destroy the very button whose callback is running, so it
                      // is deferred to the next message-loop turn.
                      juce::Component::SafePointer<LoudnessEditor> safeThis (this);
                      juce::MessageManager::callAsync ([safeThis, next]
                      {
                          if (safeThis != nullptr)
                              safeThis->openSetting (next);
                      });
                  }, false },
        { "DONE", [this] { closePopover(); }, true }
    };

    openIndex = index;
    popover.setContent ("SETTING " + juce::String (index + 1), colour,
                        std::move (rows), std::move (buttons));

    showPanel();
    refreshChrome();
}

void LoudnessEditor::openGlobals()
{
    auto& state = processor.getState();

    using Row = ParamPopover::RowSpec;
    std::vector<Row> rows {
        Row::parameter ("Mode",       state.getParameter (LoudnessIDs::mode),       false, nullptr),
        Row::parameter ("Detector",   state.getParameter (LoudnessIDs::detector),   false, nullptr),
        Row::parameter ("Attack",     state.getParameter (LoudnessIDs::attack),     false, millis1),
        Row::parameter ("Release",    state.getParameter (LoudnessIDs::release),    false, millis1),
        Row::parameter ("Hysteresis", state.getParameter (LoudnessIDs::hysteresis), false, plainDb),
        Row::parameter ("Glide",      state.getParameter (LoudnessIDs::glide),      false, millis0)
    };

    std::vector<ParamPopover::ButtonSpec> buttons {
        { "DONE", [this] { closePopover(); }, true }
    };

    openIndex = globalsIndex;
    popover.setContent ("DETECTOR", Theme::accent(), std::move (rows), std::move (buttons));

    showPanel();
    refreshChrome();
}

void LoudnessEditor::setView (const LoudnessProcessor::ViewLimits& newView)
{
    processor.setViewLimits (newView);          // sanitises and persists with the session
    view = processor.getViewLimits();
    seenViewVersion = processor.getViewVersion();

    pad.setViewLimits (view);
    popover.repaint();
}

void LoudnessEditor::openView()
{
    using Limits = LoudnessProcessor::ViewLimits;
    using Row = ParamPopover::RowSpec;

    // Frequency limits move on a log scale and snap to three significant figures, so a drag
    // lands on 125 Hz or 1.25 kHz rather than 1043.7 Hz.
    auto logRange = [] (float lo, float hi)
    {
        return juce::NormalisableRange<float> (
            lo, hi,
            [] (float start, float end, float p) { return start * std::pow (end / start, p); },
            [] (float start, float end, float v) { return std::log (v / start) / std::log (end / start); },
            [] (float start, float end, float v)
            {
                v = juce::jlimit (start, end, v);
                const auto step = std::pow (10.0f, std::floor (std::log10 (v)) - 2.0f);
                return juce::jlimit (start, end, std::round (v / step) * step);
            });
    };

    const auto hz = [] (float v) { return MorphPad::formatFrequency (v); };
    const auto db = [] (float v) { return MorphPad::formatGain (v); };

    // Each bound is clamped against the other rather than pushing it, so the range never
    // collapses below an octave / 3 dB and the other end never moves on its own.
    std::vector<Row> rows {
        Row::value ("Freq low",  [this] { return view.minHz; },
                    [this] (float v) { auto l = view; l.minHz = juce::jmin (v, view.maxHz / Limits::minRatio); setView (l); },
                    logRange (Limits::lowestHz, 10000.0f), hz),
        Row::value ("Freq high", [this] { return view.maxHz; },
                    [this] (float v) { auto l = view; l.maxHz = juce::jmax (v, view.minHz * Limits::minRatio); setView (l); },
                    logRange (100.0f, Limits::highestHz), hz),
        Row::value ("Gain high", [this] { return view.topDb; },
                    [this] (float v) { auto l = view; l.topDb = juce::jmax (v, view.bottomDb + Limits::minSpanDb); setView (l); },
                    juce::NormalisableRange<float> (Limits::lowestDb + Limits::minSpanDb, Limits::highestDb, 1.0f), db),
        Row::value ("Gain low",  [this] { return view.bottomDb; },
                    [this] (float v) { auto l = view; l.bottomDb = juce::jmin (v, view.topDb - Limits::minSpanDb); setView (l); },
                    juce::NormalisableRange<float> (Limits::lowestDb, Limits::highestDb - Limits::minSpanDb, 1.0f), db)
    };

    std::vector<ParamPopover::ButtonSpec> buttons {
        { "RESET", [this] { setView (Limits {}); }, false },
        { "DONE",  [this] { closePopover(); }, true }
    };

    openIndex = viewIndex;
    popover.setContent ("VIEW", Theme::accent(), std::move (rows), std::move (buttons));
    showPanel();
    refreshChrome();
}

void LoudnessEditor::closePopover()
{
    openIndex = -1;
    popover.setVisible (false);

    if (popover.isOnDesktop())
        popover.removeFromDesktop();
    else if (popover.getParentComponent() == this)
        removeChildComponent (&popover);

    refreshChrome();
}

void LoudnessEditor::showPanel()
{
    const bool haveDesktop = juce::Desktop::getInstance().getDisplays().getPrimaryDisplay() != nullptr;

    // The editor is usually drawn scaled -- in an LV2 host the desktop scale arrives as a
    // transform on the editor (ui:scaleFactor -> setScaleFactor), not as a platform scale
    // on this plugin's own windows -- so a new window would come up at 1x, half the size of
    // the card. Match the editor, exactly as JUCE's PopupMenu does for a scaled target.
    const auto scale = juce::Component::getApproximateScaleFactorForComponent (this);

    if (haveDesktop && (! popover.isOnDesktop() || ! juce::approximatelyEqual (scale, panelScale)))
    {
        if (popover.isOnDesktop())
            popover.removeFromDesktop();

        panelScale = scale;
        popover.setDesktopScale (scale);

        // A real, window-manager-managed window (undecorated: no windowHasTitleBar flag).
        // It was override-redirect at first, which places exactly and is never decorated or
        // maximised -- but such a window cannot hold keyboard focus: the compositor hands
        // focus straight back to the host, so typing into a row went to the host instead.
        // windowHasDropShadow is what makes JUCE type it _NET_WM_WINDOW_TYPE_NORMAL rather
        // than _COMBO (setWindowType in juce_XWindowSystem_linux.cpp), i.e. focusable.
        popover.addToDesktop (juce::ComponentPeer::windowHasDropShadow);
        hostWindow.markAsToolWindowFor (popover);
    }
    else if (! haveDesktop && popover.getParentComponent() != this)
    {
        addChildComponent (popover);    // headless: nothing to float on
    }

    // The flip side of override-redirect is that nothing stacks it: it would float over
    // other applications. Hide it while another window is active -- but only if activity can
    // be read at all, judged now, while the user has just clicked in this very window.
    hostWindow.reset();
    hideWhenInactive = haveDesktop && hostWindow.hostWindowIsActive();

    placePanel();
    popover.setVisible (true);
    popover.toFront (false);
}

void LoudnessEditor::placePanel()
{
    if (openIndex < 0)
        return;

    if (! popover.isOnDesktop())
    {
        // Headless fallback only: inside the card, above the chips.
        const auto body = getLocalBounds().withTrimmedTop (Layout::headerH).withTrimmedBottom (Layout::footerH);
        const int h = popover.preferredHeight();
        popover.setBounds (Layout::cardWidth - Layout::bodyPad - ParamPopover::width,
                           body.getBottom() - Layout::popBottomInset - h, ParamPopover::width, h);
        return;
    }

    // Everything below is in desktop units (what getScreenBounds() returns); the panel's
    // own bounds are those divided by its scale, as its window multiplies them back up.
    // getScreenBounds() can be stale for an embedded UI after the host window moved, hence
    // the correction from the X server (see HostWindowTracker).
    const auto error = hostWindow.screenPositionError();
    const auto card = getScreenBounds() + error;
    panelAnchor = card.getPosition();

    auto* anchor = openIndex == globalsIndex ? static_cast<juce::Component*> (&setupButton)
                 : openIndex == viewIndex    ? static_cast<juce::Component*> (&viewButton)
                                             : static_cast<juce::Component*> (chips[(size_t) openIndex].get());
    const int anchorX = anchor->getScreenBounds().getCentreX() + error.x;

    const auto screen = [&]
    {
        if (auto* display = juce::Desktop::getInstance().getDisplays().getDisplayForRect (card))
            return display->userArea;

        return card.expanded (4000);
    }();

    const int width  = juce::roundToInt ((float) ParamPopover::width * panelScale);
    const int height = juce::roundToInt ((float) popover.preferredHeight() * panelScale);
    const int gap    = juce::roundToInt (6.0f * panelScale);
    const int besideY = juce::jlimit (screen.getY(), juce::jmax (screen.getY(), screen.getBottom() - height),
                                      card.getCentreY() - height / 2);

    // Below the card, under the chip that opened it -- never over the plot. If the card sits
    // too low on the screen for that, beside it instead; only as a last resort overlapping.
    const juce::Rectangle<int> candidates[] {
        { juce::jlimit (card.getX(), card.getRight() - width, anchorX - width / 2), card.getBottom() + gap, width, height },
        { card.getRight() + gap,    besideY, width, height },
        { card.getX() - gap - width, besideY, width, height }
    };

    const auto chosen = [&]
    {
        for (const auto& candidate : candidates)
            if (screen.contains (candidate))
                return candidate;

        return candidates[0].constrainedWithin (screen);
    }();

    popover.setBounds (juce::roundToInt ((float) chosen.getX() / panelScale),
                       juce::roundToInt ((float) chosen.getY() / panelScale),
                       ParamPopover::width, popover.preferredHeight());
}

//==============================================================================
void LoudnessEditor::resized()
{
    auto area = getLocalBounds();

    //--- header ---------------------------------------------------------------
    auto header = area.removeFromTop (Layout::headerH).reduced (16, 11);

    bypassButton.setBounds (header.removeFromRight (bypassButton.preferredWidth())
                                  .withSizeKeepingCentre (bypassButton.preferredWidth(), Layout::chromeH));
    header.removeFromRight (8);
    setupButton.setBounds (header.removeFromRight (setupButton.preferredWidth())
                                 .withSizeKeepingCentre (setupButton.preferredWidth(), Layout::chromeH));
    header.removeFromRight (8);
    viewButton.setBounds (header.removeFromRight (viewButton.preferredWidth())
                                .withSizeKeepingCentre (viewButton.preferredWidth(), Layout::chromeH));

    //--- footer ---------------------------------------------------------------
    area.removeFromBottom (Layout::footerH);

    //--- body -----------------------------------------------------------------
    auto inner = area.reduced (Layout::bodyPad);
    auto meterColumn = inner.removeFromRight (Layout::meterW);
    inner.removeFromRight (Layout::colGap);

    pad.setBounds (inner.removeFromTop (Layout::padH));
    inner.removeFromTop (Layout::rowGap);

    auto chipRow = inner.removeFromTop (Layout::chipsH);
    const int n = LoudnessIDs::numSettings;

    for (int s = 0; s < n; ++s)
    {
        const int x0 = chipRow.getX() + juce::roundToInt ((float) (chipRow.getWidth() + Layout::chipGap) * (float) s / (float) n);
        const int x1 = chipRow.getX() + juce::roundToInt ((float) (chipRow.getWidth() + Layout::chipGap) * (float) (s + 1) / (float) n)
                         - Layout::chipGap;
        chips[(size_t) s]->setBounds (x0, chipRow.getY(), x1 - x0, chipRow.getHeight());
    }

    meter.setBounds (meterColumn);

    placePanel();
}

void LoudnessEditor::paint (juce::Graphics& g)
{
    g.fillAll (Theme::card);

    const auto bounds = getLocalBounds();

    //--- header ---------------------------------------------------------------
    const auto header = bounds.withHeight (Layout::headerH);

    g.setColour (Theme::headerBg);
    g.fillRect (header);
    g.setColour (Theme::headerLine);
    g.drawLine (0.0f, (float) Layout::headerH - 0.5f, (float) getWidth(), (float) Layout::headerH - 0.5f, 1.0f);

    const auto titleFont = Theme::sans (13.0f, true);
    const float baseline = 11.0f + ((float) Layout::chromeH - titleFont.getHeight()) * 0.5f + titleFont.getAscent();

    g.setColour (Theme::ink);
    g.setFont (titleFont);
    drawBaselineText (g, "Loudness Contour", 16.0f, baseline);

    const auto subtitleFont = Theme::mono (10.0f);
    g.setColour (Theme::inkFaint);
    g.setFont (subtitleFont);
    drawBaselineText (g, "morph pad",
                      16.0f + (float) juce::GlyphArrangement::getStringWidthInt (titleFont, "Loudness Contour") + 10.0f,
                      baseline);

    // Detector readout, sitting to the left of the SETUP / BYPASS chips.
    g.setColour (Theme::accent());
    g.setFont (Theme::mono (11.0f, true));
    g.drawText (juce::String (processor.getLevelDb(), 1) + " dBFS",
                header.withTrimmedRight (getWidth() - viewButton.getX() + 8),
                juce::Justification::centredRight);

    //--- footer ---------------------------------------------------------------
    const auto footer = bounds.withTop (getHeight() - Layout::footerH);

    g.setColour (Theme::footerBg);
    g.fillRect (footer);
    g.setColour (Theme::footerLine);
    g.drawLine ((float) footer.getX(), (float) footer.getY() + 0.5f,
                (float) footer.getRight(), (float) footer.getY() + 0.5f, 1.0f);

    const auto dot = " " + Theme::Glyph::middot() + " ";
    const auto hint = openIndex == viewIndex    ? "drag a row" + dot + "RESET returns to the full range"
                    : openIndex == globalsIndex ? "drag a row" + dot + "these apply to all four settings"
                    : openIndex >= 0            ? "drag a row" + dot + "COPY " + Theme::Glyph::arrow() + " writes into the next setting"
                                                : "click a setting to edit" + dot + "drag dots on the pad or ticks on the meter";

    g.setColour (Theme::inkFaint);
    g.setFont (Theme::mono (10.0f));
    g.drawText (hint, footer.reduced (16, 8), juce::Justification::centredLeft);
    g.drawText ("detector in zone " + juce::String (processor.getActiveSetting() + 1),
                footer.reduced (16, 8), juce::Justification::centredRight);

    //--- card frame -----------------------------------------------------------
    g.setColour (Theme::cardBorder);
    g.drawRect (bounds, 1);
}

//==============================================================================
void LoudnessEditor::timerCallback()
{
    // The readout shows one decimal, so finer moves would be invisible repaints.
    const auto db = (float) juce::roundToInt (processor.getLevelDb() * 10.0f) / 10.0f;

    if (! juce::approximatelyEqual (db, shownLevel))
    {
        shownLevel = db;
        meter.setLevel (db);
        repaint (0, 0, getWidth(), Layout::headerH);
    }

    // A session restored while the editor is open brings its own plot range.
    if (processor.getViewVersion() != seenViewVersion)
    {
        view = processor.getViewLimits();
        seenViewVersion = processor.getViewVersion();
        pad.setViewLimits (view);
        popover.repaint();
    }

    const int zone = processor.getActiveSetting();

    if (zone != shownZone)
    {
        shownZone = zone;
        refreshChrome();
    }

    // The panel is a separate window, so it has to be told when the card moves (the host
    // window was dragged), disappears (minimised, closed) or goes behind another application.
    if (popover.isOnDesktop())
    {
        if (! isShowing())
        {
            closePopover();
            return;
        }

        if (hideWhenInactive && ++activityCheck % 8 == 0)      // ~7 Hz is plenty for this
        {
            // Never while the panel is in use: hiding a component mid-drag drops its mouse
            // capture, which aborts the edit. And debounce, because a click on this
            // override-redirect window can make the active window flicker for one check.
            // "In use" is deliberately broad: a button down anywhere, or the pointer over the
            // card or the panel. Clicking this override-redirect window can leave the active
            // window reading as something else, and hiding the panel while it is being used
            // drops its mouse capture and aborts the edit.
            const bool inUse = juce::ModifierKeys::currentModifiers.isAnyMouseButtonDown()
                                 || popover.isMouseOver (true)
                                 || isMouseOver (true);
            const bool active = hostWindow.hostWindowIsActive (&popover);

            inactiveChecks = (inUse || active) ? 0 : inactiveChecks + 1;

            if (inactiveChecks == 0 && ! popover.isVisible())
            {
                placePanel();                   // it may have moved while hidden
                popover.setVisible (true);
            }
            else if (inactiveChecks >= 3 && popover.isVisible())
            {
                popover.setVisible (false);
            }
        }

        if (popover.isVisible() && getScreenPosition() + hostWindow.screenPositionError() != panelAnchor)
            placePanel();
    }
}
