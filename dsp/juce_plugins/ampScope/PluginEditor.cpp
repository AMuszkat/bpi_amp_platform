#include "PluginEditor.h"

namespace
{
    constexpr int headerHeight  = 46;
    constexpr int footerHeight  = 30;
    constexpr int controlWidth  = 216;
    constexpr int traceRowHeight = 26;
    constexpr int gutter        = 10;

    void styleChip (juce::TextButton& b)
    {
        b.setColour (juce::TextButton::buttonColourId,   Theme::chipBg);
        b.setColour (juce::TextButton::buttonOnColourId, Theme::chipBgOn);
        b.setColour (juce::TextButton::textColourOffId,  Theme::inkDim);
        b.setColour (juce::TextButton::textColourOnId,   Theme::accent());
    }
}

//==============================================================================
AmpScopeEditor::ScopeLookAndFeel::ScopeLookAndFeel()
{
    using juce::Colour;

    setColour (juce::ResizableWindow::backgroundColourId, Theme::card);

    setColour (juce::Slider::backgroundColourId,       Theme::track);
    setColour (juce::Slider::trackColourId,            Theme::accent().withAlpha (0.55f));
    setColour (juce::Slider::thumbColourId,            Theme::accent());
    setColour (juce::Slider::textBoxTextColourId,      Theme::ink);
    setColour (juce::Slider::textBoxBackgroundColourId, Theme::sunken);
    setColour (juce::Slider::textBoxOutlineColourId,   Theme::chipBorder);

    setColour (juce::ComboBox::backgroundColourId,     Theme::sunken);
    setColour (juce::ComboBox::textColourId,           Theme::ink);
    setColour (juce::ComboBox::outlineColourId,        Theme::chipBorder);
    setColour (juce::ComboBox::arrowColourId,          Theme::inkDim);

    setColour (juce::PopupMenu::backgroundColourId,    Theme::popBg);
    setColour (juce::PopupMenu::textColourId,          Theme::ink);
    setColour (juce::PopupMenu::highlightedBackgroundColourId, Theme::chipBgOn);
    setColour (juce::PopupMenu::highlightedTextColourId, Theme::accent());

    setColour (juce::Label::textColourId,              Theme::inkLabel);
    setColour (juce::ToggleButton::tickColourId,       Theme::accent());
    setColour (juce::ToggleButton::tickDisabledColourId, Theme::caretOff);
}

//==============================================================================
AmpScopeEditor::Field::Field (const juce::String& c, juce::Component& widget, int cw)
    : caption (c), control (widget), captionWidth (cw)
{
    addAndMakeVisible (control);
}

void AmpScopeEditor::Field::paint (juce::Graphics& g)
{
    g.setColour (Theme::inkFaint);
    g.setFont (Theme::mono (9.5f));
    g.drawText (caption, getLocalBounds().withWidth (captionWidth - 4),
                juce::Justification::centredLeft, false);
}

void AmpScopeEditor::Field::resized()
{
    control.setBounds (getLocalBounds().withTrimmedLeft (captionWidth));
}

//==============================================================================
AmpScopeEditor::TraceRow::TraceRow (AmpScopeProcessor& p, amp::Channel c)
    : processor (p), channel (c)
{
    addAndMakeVisible (enabled);
    enabled.setColour (juce::ToggleButton::tickColourId, ScopeStyle::colourFor (c));

    addAndMakeVisible (slot);
    slot.setTextWhenNothingSelected ("off");
    slot.onChange = [this]
    {
        // Item ids are 1 + the amp slot, with 1000 reserved for "off", so the
        // menu can carry the slot number without a parallel table.
        const int id = slot.getSelectedId();

        if (id != 0)
            processor.setSlotFor (channel, id == 1000 ? -1 : id - 1);
    };

    for (auto* s : { &perDiv, &offset })
    {
        addAndMakeVisible (*s);
        s->setTextBoxStyle (juce::Slider::TextBoxRight, false, 76, 18);
        s->setColour (juce::Slider::thumbColourId, ScopeStyle::colourFor (c));
        s->setColour (juce::Slider::trackColourId, ScopeStyle::colourFor (c).withAlpha (0.45f));
    }

    enabledAttachment = std::make_unique<juce::AudioProcessorValueTreeState::ButtonAttachment>
                            (p.apvts, AmpScopeProcessor::showId (c), enabled);
    perDivAttachment  = std::make_unique<juce::AudioProcessorValueTreeState::SliderAttachment>
                            (p.apvts, AmpScopeProcessor::scaleId (c), perDiv);
    offsetAttachment  = std::make_unique<juce::AudioProcessorValueTreeState::SliderAttachment>
                            (p.apvts, AmpScopeProcessor::offsetId (c), offset);
}

void AmpScopeEditor::TraceRow::refreshSlots (const amp::Device& mine,
                                             const juce::Array<amp::Device>& all)
{
    const int current = mine.slot (channel);
    const int wantedId = current < 0 ? 1000 : current + 1;

    // Which slots another amp on this DOUT line already drives. They arbitrate
    // by Hi-Z alone, so handing one out twice shorts two outputs together --
    // this menu is the only place that can prevent it, because the driver only
    // ever sees one amp at a time.
    juce::HashMap<int, juce::String> claimed;
    juce::String layout (current);
    layout << "@" << mine.captureChannels;

    for (const auto& other : all)
    {
        if (other.key() == mine.key() || other.cardIndex != mine.cardIndex)
            continue;

        for (int i = 0; i < amp::numChannels; ++i)
            if (const int s = other.slot ((amp::Channel) i); s >= 0)
            {
                claimed.set (s, other.prefix);
                layout << "/" << other.prefix << s;
            }
    }

    slot.setEnabled (mine.isValid());

    // Rebuilding every tick would fight the user's mouse, so only when this
    // amp's slot or somebody else's claim on one has actually moved.
    if (slot.getNumItems() > 0 && layout == slotLayout)
        return;

    slotLayout = layout;

    slot.clear (juce::dontSendNotification);
    slot.addItem ("off", 1000);

    for (int ch = 0; ch < 8; ++ch)
    {
        // A channel outside the capture stream is one the amp would transmit
        // and the SoC would never pick up -- offering it silently would just
        // produce a scope that refuses to start.
        const bool captured = mine.captureChannels <= 0 || ch < mine.captureChannels;

        for (int half = 0; half < 2; ++half)
        {
            const int s = amp::slots::fromChannelHalf (ch, half == 1);
            const bool taken = claimed.contains (s) && s != current;

            slot.addItem (amp::slots::name (s)
                            + (taken       ? "  (" + claimed[s] + ")"
                             : ! captured  ? juce::String ("  (not captured)")
                                           : juce::String()),
                          s + 1);

            if (taken || (! captured && s != current))
                slot.setItemEnabled (s + 1, false);
        }
    }

    slot.setSelectedId (wantedId, juce::dontSendNotification);
}

void AmpScopeEditor::TraceRow::update (const ScopeView::Measurement& m, bool sourceProvidesIt)
{
    juce::String text;

    if (! sourceProvidesIt)
        text = Theme::Glyph::emDash() + " not on this source";
    else if (m.valid)
        text = "min " + ScopeStyle::format (channel, m.min)
             + "   max " + ScopeStyle::format (channel, m.max)
             + "   avg " + ScopeStyle::format (channel, m.mean);
    else if (! enabled.getToggleState())
        text = Theme::Glyph::emDash() + " trace off";

    if (text != readout || sourceProvidesIt != available)
    {
        readout = text;
        available = sourceProvidesIt;

        perDiv.setEnabled (available);
        offset.setEnabled (available);
        repaint();
    }
}

void AmpScopeEditor::TraceRow::paint (juce::Graphics& g)
{
    const auto colour = ScopeStyle::colourFor (channel);

    auto swatch = juce::Rectangle<float> (26.0f, (float) getHeight() * 0.5f - 1.5f, 14.0f, 3.0f);
    g.setColour (available ? colour : colour.withAlpha (0.3f));
    g.fillRect (swatch);

    g.setColour (available ? Theme::inkLabel : Theme::inkFaint);
    g.setFont (Theme::mono (10.5f, true));
    g.drawText (amp::channelName (channel),
                juce::Rectangle<int> (46, 0, 42, getHeight()),
                juce::Justification::centredLeft, false);

    g.setColour (Theme::inkFaint);
    g.setFont (Theme::mono (9.5f));
    g.drawText (readout, getLocalBounds().withTrimmedLeft (getWidth() - 252).reduced (4, 0),
                juce::Justification::centredLeft, false);
}

void AmpScopeEditor::TraceRow::resized()
{
    auto r = getLocalBounds();

    enabled.setBounds (r.removeFromLeft (26));
    r.removeFromLeft (62);                       // swatch + name, painted
    slot.setBounds (r.removeFromLeft (104).reduced (2, 3));

    const int remaining = r.getWidth() - 252;
    perDiv.setBounds (r.removeFromLeft (juce::jmax (130, remaining / 2)).reduced (2, 3));
    offset.setBounds (r.removeFromLeft (juce::jmax (120, remaining / 2)).reduced (2, 3));
}

//==============================================================================
AmpScopeEditor::AmpScopeEditor (AmpScopeProcessor& p)
    : juce::AudioProcessorEditor (&p), processor (p), scope (p.telemetry)
{
    setLookAndFeel (&lookAndFeel);

    addAndMakeVisible (scope);

    //--- header ---------------------------------------------------------------
    addAndMakeVisible (deviceBox);
    deviceBox.setTextWhenNoChoicesAvailable ("no max98396/7 found");
    deviceBox.setTextWhenNothingSelected ("select an amplifier");
    deviceBox.onChange = [this]
    {
        const auto devices = processor.getKnownDevices();
        const int index = deviceBox.getSelectedId() - 1;

        if (juce::isPositiveAndBelow (index, devices.size()))
        {
            auto sel = processor.getSelection();
            sel.deviceKey = devices.getReference (index).key();
            processor.setSelection (sel);
        }
    };

    for (auto* b : { &runButton, &singleButton, &rescanButton })
    {
        addAndMakeVisible (*b);
        styleChip (*b);
    }

    runButton.setClickingTogglesState (true);
    runAttachment = std::make_unique<ButtonAttachment> (p.apvts, "run", runButton);

    singleButton.onClick = [this] { scope.armSingle(); };
    rescanButton.onClick = [this]
    {
        processor.refreshDevices();
        rebuildDeviceList();
    };

    //--- right-hand controls --------------------------------------------------
    auto addField = [this] (const juce::String& caption, juce::Component& widget)
    {
        auto* f = fields.add (new Field (caption, widget, 52));
        addAndMakeVisible (f);
        return f;
    };

    for (auto* s : { &timePerDiv, &trigLevel })
        s->setTextBoxStyle (juce::Slider::TextBoxRight, false, 62, 18);

    trigMode.addItemList ({ "Free run", "Auto", "Normal", "Single" }, 1);
    trigEdge.addItemList ({ "Rising", "Falling" }, 1);
    acqSource.addItemList ({ "Capture", "Demo" }, 1);
    captureRate.addItemList ({ "44100", "48000", "88200", "96000", "192000" }, 1);
    supplyAlign.addItemList ({ "MSB", "LSB" }, 1);

    for (int c = 0; c < amp::numChannels; ++c)
        trigSource.addItem (amp::channelName ((amp::Channel) c), c + 1);

    addField ("TIME", timePerDiv);
    addField ("MODE", trigMode);
    addField ("SRC",  trigSource);
    addField ("EDGE", trigEdge);
    addField ("LEVEL", trigLevel);
    addField ("ROUTE", acqSource);
    addField ("RATE", captureRate);
    addField ("ALIGN", supplyAlign);

    timePerDivAttachment  = std::make_unique<SliderAttachment> (p.apvts, "timePerDiv", timePerDiv);
    trigLevelAttachment   = std::make_unique<SliderAttachment> (p.apvts, "trigLevel", trigLevel);
    trigModeAttachment    = std::make_unique<ComboAttachment>  (p.apvts, "trigMode", trigMode);
    trigSourceAttachment  = std::make_unique<ComboAttachment>  (p.apvts, "trigSource", trigSource);
    trigEdgeAttachment    = std::make_unique<ComboAttachment>  (p.apvts, "trigEdge", trigEdge);
    acqSourceAttachment   = std::make_unique<ComboAttachment>  (p.apvts, "source", acqSource);
    captureRateAttachment = std::make_unique<ComboAttachment>  (p.apvts, "captureRate", captureRate);
    supplyAlignAttachment = std::make_unique<ComboAttachment>  (p.apvts, "supplyAlign", supplyAlign);

    //--- trace table ----------------------------------------------------------
    for (int c = 0; c < amp::numChannels; ++c)
        addAndMakeVisible (rows.add (new TraceRow (p, (amp::Channel) c)));

    rebuildDeviceList();
    pushParametersToScope();

    setResizeLimits (cardWidth, cardHeight, cardWidth, cardHeight);
    setSize (cardWidth, cardHeight);

    startTimerHz (20);
}

AmpScopeEditor::~AmpScopeEditor()
{
    setLookAndFeel (nullptr);
}

//==============================================================================
void AmpScopeEditor::rebuildDeviceList()
{
    const auto devices = processor.getKnownDevices();
    const auto selected = processor.getSelection().deviceKey;

    deviceBox.clear (juce::dontSendNotification);

    int idToSelect = 0;

    for (int i = 0; i < devices.size(); ++i)
    {
        const auto& d = devices.getReference (i);
        deviceBox.addItem (d.label(), i + 1);

        if (d.key() == selected)
            idToSelect = i + 1;
    }

    if (idToSelect > 0)
        deviceBox.setSelectedId (idToSelect, juce::dontSendNotification);

    lastSelectionVersion = processor.getSelectionVersion();
}

void AmpScopeEditor::pushParametersToScope()
{
    auto& apvts = processor.apvts;

    const float timeDiv = apvts.getRawParameterValue ("timePerDiv")->load();
    scope.setWindowSeconds ((double) timeDiv * 10.0);

    for (int c = 0; c < amp::numChannels; ++c)
    {
        const auto channel = (amp::Channel) c;

        ScopeView::Trace t;
        t.visible     = apvts.getRawParameterValue (AmpScopeProcessor::showId (channel))->load() > 0.5f;
        t.unitsPerDiv = apvts.getRawParameterValue (AmpScopeProcessor::scaleId (channel))->load();
        t.offsetDivs  = apvts.getRawParameterValue (AmpScopeProcessor::offsetId (channel))->load();

        scope.setTrace (channel, t);
    }

    const auto mode   = (int) apvts.getRawParameterValue ("trigMode")->load();
    const auto source = (amp::Channel) juce::jlimit (0, amp::numChannels - 1,
                                                     (int) apvts.getRawParameterValue ("trigSource")->load());
    const auto edge   = (int) apvts.getRawParameterValue ("trigEdge")->load();
    const auto levelDivs = apvts.getRawParameterValue ("trigLevel")->load();

    // The trigger knob is in divisions, like the front-panel control it copies;
    // turn that into the source trace's own units using that trace's scaling.
    const auto st = scope.getTrace (source);
    const float level = (levelDivs - st.offsetDivs) * st.unitsPerDiv;

    scope.setTrigger ((ScopeView::TriggerMode) juce::jlimit (0, 3, mode), source,
                      edge == 1 ? ScopeView::TriggerEdge::falling : ScopeView::TriggerEdge::rising,
                      level);

    const bool run = apvts.getRawParameterValue ("run")->load() > 0.5f;
    scope.setRunning (run);

    // Only on a change: stop() joins the acquisition thread, and doing that
    // twenty times a second because the timer happened to fire would be a
    // pointless blocking call on the message thread.
    if (run != processor.telemetry.getStatus().running)
    {
        if (run)
            processor.telemetry.start();
        else
            processor.telemetry.stop();
    }
}

void AmpScopeEditor::timerCallback()
{
    if (processor.getSelectionVersion() != lastSelectionVersion)
        rebuildDeviceList();

    processor.applyAcquisitionParameters();
    pushParametersToScope();

    const auto status = processor.telemetry.getStatus();

    const auto device = processor.telemetry.getDevice();
    const auto devices = processor.getKnownDevices();

    for (auto* row : rows)
    {
        row->update (scope.getMeasurement (row->channel),
                     status.available[(size_t) row->channel]);
        row->refreshSlots (device, devices);
    }

    // Free-running hosts never call repaint for us, and the status line is the
    // only place an acquisition or configuration failure is visible.
    juce::String text = status.message;

    if (const auto configError = processor.getConfigurationError(); configError.isNotEmpty())
    {
        text = configError + "   " + Theme::Glyph::middot() + "   " + text;
        statusIsError = true;
    }

    if (status.sampleRate > 0.0 && ! status.isError)
        text = juce::String (status.sampleRate >= 1000.0
                               ? juce::String (status.sampleRate / 1000.0, 1) + " kSa/s"
                               : juce::String ((int) status.sampleRate) + " Sa/s")
             + "   " + Theme::Glyph::middot() + "   " + text;

    // A 20 ms window off a 200 Hz poll is four samples. It draws -- it just is not
    // a picture of anything, and the reason is not visible on the screen itself.
    if (status.running && status.sampleRate > 0.0)
    {
        const double samples = scope.getEffectiveWindowSeconds() * status.sampleRate;

        if (samples < 20.0)
            text = "only " + juce::String ((int) samples) + " samples across the whole window at "
                     + juce::String ((int) status.sampleRate) + " Sa/s"
                     + "   " + Theme::Glyph::middot() + "   slow the timebase down"
                     + "   " + Theme::Glyph::middot() + "   " + text;
    }

    if (scope.isWindowClipped())
        text = "record length reached: showing "
                 + ScopeStyle::formatTime (scope.getEffectiveWindowSeconds())
                 + " of the requested "
                 + ScopeStyle::formatTime (scope.getWindowSeconds())
                 + "   " + Theme::Glyph::middot() + "   " + text;

    const bool isError = status.isError || processor.getConfigurationError().isNotEmpty();

    if (text != statusText || isError != statusIsError)
    {
        statusText = text;
        statusIsError = isError;
        repaint (0, getHeight() - footerHeight, getWidth(), footerHeight);
    }

    runButton.setButtonText (scope.isRunning() ? "RUN" : "STOP");
    singleButton.setEnabled ((int) processor.apvts.getRawParameterValue ("trigMode")->load() == 3);
}

//==============================================================================
void AmpScopeEditor::paint (juce::Graphics& g)
{
    auto bounds = getLocalBounds();

    g.fillAll (Theme::card);

    //--- header ---------------------------------------------------------------
    auto header = bounds.removeFromTop (headerHeight);
    g.setColour (Theme::headerBg);
    g.fillRect (header);
    g.setColour (Theme::headerLine);
    g.drawHorizontalLine (header.getBottom() - 1, 0.0f, (float) getWidth());

    g.setColour (Theme::ink);
    g.setFont (Theme::sans (15.0f, true));
    g.drawText ("ampScope", header.withTrimmedLeft (gutter + 2).withWidth (100),
                juce::Justification::centredLeft, false);

    g.setColour (Theme::inkFaint);
    g.setFont (Theme::mono (9.0f));
    g.drawText ("MAX98397", header.withTrimmedLeft (gutter + 2).withWidth (100).translated (0, 13),
                juce::Justification::centredLeft, false);

    //--- footer ---------------------------------------------------------------
    auto footer = bounds.removeFromBottom (footerHeight);
    g.setColour (Theme::footerBg);
    g.fillRect (footer);
    g.setColour (Theme::footerLine);
    g.drawHorizontalLine (footer.getY(), 0.0f, (float) getWidth());

    g.setColour (statusIsError ? Theme::setting (3) : Theme::inkFaint);
    g.setFont (Theme::mono (9.5f));
    g.drawText (statusText, footer.reduced (gutter, 0), juce::Justification::centredLeft, false);

    //--- control column -------------------------------------------------------
    auto controls = bounds.removeFromRight (controlWidth);
    controls.removeFromBottom (rows.size() * traceRowHeight + gutter);

    g.setColour (Theme::hairline);
    g.drawVerticalLine (controls.getX(), (float) controls.getY(), (float) controls.getBottom());

    // Group captions, positioned to match the field stack in resized().
    g.setColour (Theme::inkMuted);
    g.setFont (Theme::mono (9.0f, true));

    struct Group { const char* name; int fieldIndex; };
    static const Group groups[] { { "TIMEBASE", 0 }, { "TRIGGER", 1 }, { "ACQUISITION", 5 } };

    for (const auto& group : groups)
        if (auto* f = fields[group.fieldIndex])
            g.drawText (group.name,
                        juce::Rectangle<int> (controls.getX() + gutter, f->getY() - 16,
                                              controlWidth, 14),
                        juce::Justification::centredLeft, false);

    //--- trace table ----------------------------------------------------------
    if (auto* first = rows.getFirst())
    {
        g.setColour (Theme::hairline);
        g.drawHorizontalLine (first->getY() - 5, 0.0f, (float) getWidth());
    }
}

void AmpScopeEditor::resized()
{
    auto bounds = getLocalBounds();

    //--- header ---------------------------------------------------------------
    auto header = bounds.removeFromTop (headerHeight).reduced (gutter, 8);
    header.removeFromLeft (108);

    rescanButton.setBounds (header.removeFromRight (72));
    header.removeFromRight (6);
    singleButton.setBounds (header.removeFromRight (72));
    header.removeFromRight (6);
    runButton.setBounds (header.removeFromRight (60));
    header.removeFromRight (10);
    deviceBox.setBounds (header.removeFromLeft (juce::jmin (330, header.getWidth())));

    bounds.removeFromBottom (footerHeight);

    //--- trace table ----------------------------------------------------------
    auto table = bounds.removeFromBottom (rows.size() * traceRowHeight + gutter);
    table.removeFromTop (gutter);

    for (auto* row : rows)
        row->setBounds (table.removeFromTop (traceRowHeight).reduced (gutter, 0));

    //--- control column -------------------------------------------------------
    auto controls = bounds.removeFromRight (controlWidth).reduced (gutter, 0);

    // One stack, with a gap before each group caption.
    static const int gapBefore[] { 18, 24, 0, 0, 0, 24, 0, 0 };

    controls.removeFromTop (2);

    for (int i = 0; i < fields.size(); ++i)
    {
        controls.removeFromTop (gapBefore[i]);
        fields[i]->setBounds (controls.removeFromTop (22));
        controls.removeFromTop (4);
    }

    //--- screen ---------------------------------------------------------------
    scope.setBounds (bounds.reduced (gutter, gutter / 2));
}
