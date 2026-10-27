#include "MorphPad.h"

namespace
{
    constexpr float dotSizeFocused = 15.0f;
    constexpr float dotSizeGhost   = 13.0f;
    constexpr float dotHitRadius   = 11.0f;
}

//==============================================================================
MorphPad::MorphPad (LoudnessProcessor& p)
    : processor (p)
{
    setOpaque (true);
    curveDirty.fill (true);

    auto& state = processor.getState();

    for (int s = 0; s < LoudnessIDs::numSettings; ++s)
    {
        for (int b = 0; b < LoudnessIDs::numBands; ++b)
        {
            const auto gainID = LoudnessIDs::band (s, b, "gain");
            const auto freqID = LoudnessIDs::band (s, b, "freq");
            const auto qID    = LoudnessIDs::band (s, b, "q");

            auto& slot = bands[(size_t) s][(size_t) b];
            slot.gainValue = state.getRawParameterValue (gainID);
            slot.freqValue = state.getRawParameterValue (freqID);
            slot.qValue    = state.getRawParameterValue (qID);
            slot.gain      = state.getParameter (gainID);
            slot.freq      = state.getParameter (freqID);

            state.addParameterListener (gainID, this);
            state.addParameterListener (freqID, this);
            state.addParameterListener (qID, this);
        }
    }
}

MorphPad::~MorphPad()
{
    cancelPendingUpdate();

    auto& state = processor.getState();

    for (int s = 0; s < LoudnessIDs::numSettings; ++s)
    {
        for (int b = 0; b < LoudnessIDs::numBands; ++b)
        {
            state.removeParameterListener (LoudnessIDs::band (s, b, "gain"), this);
            state.removeParameterListener (LoudnessIDs::band (s, b, "freq"), this);
            state.removeParameterListener (LoudnessIDs::band (s, b, "q"), this);
        }
    }
}

//==============================================================================
void MorphPad::setDisplayedSetting (int index)
{
    index = juce::jlimit (0, LoudnessIDs::numSettings - 1, index);

    if (displayed.exchange (index, std::memory_order_relaxed) != index)
    {
        fillDirty = true;       // the fill follows whichever setting is focused
        background = {};        // and which settings are ghosts has changed
        repaint();
    }
}

void MorphPad::parameterChanged (const juce::String& parameterID, float)
{
    // Synchronous, on whichever thread moved the parameter -- the message thread while
    // dragging, the audio thread for host automation. Atomics only; the repaint happens
    // in handleAsyncUpdate(). IDs are "s<setting>_e<band>_<what>", so char 1 is the set.
    const int setting = (int) parameterID[1] - '0';

    if (juce::isPositiveAndBelow (setting, LoudnessIDs::numSettings))
        markSettingDirty (setting);
}

void MorphPad::markSettingDirty (int setting)
{
    pendingDirty.fetch_or (1 << setting, std::memory_order_relaxed);
    triggerAsyncUpdate();
}

void MorphPad::handleAsyncUpdate()
{
    const auto mask = pendingDirty.exchange (0, std::memory_order_relaxed);

    const int focus = displayed.load (std::memory_order_relaxed);

    for (int s = 0; s < LoudnessIDs::numSettings; ++s)
    {
        if ((mask & (1 << s)) != 0)
        {
            curveDirty[(size_t) s] = true;

            if (s == focus)
                fillDirty = true;
            else
                background = {};        // a ghost moved, and ghosts live in the cache
        }
    }

    repaint();
}

//==============================================================================
void MorphPad::setViewLimits (const LoudnessProcessor::ViewLimits& newView)
{
    const auto v = newView.sanitised();

    if (juce::approximatelyEqual (v.minHz, view.minHz) && juce::approximatelyEqual (v.maxHz, view.maxHz)
         && juce::approximatelyEqual (v.bottomDb, view.bottomDb) && juce::approximatelyEqual (v.topDb, view.topDb))
        return;

    view = v;
    curveDirty.fill (true);         // the cached paths are in pixels of the old view
    fillDirty = true;
    background = {};                // the grid and the captions describe the old view
    repaint();
}

juce::String MorphPad::formatFrequency (float hz)
{
    if (hz < 1000.0f)
        return juce::String (juce::roundToInt (hz)) + " Hz";

    // One decimal below 10 kHz where it matters ("1.5 kHz"), none when it is ".0".
    const auto khz = hz / 1000.0f;
    const auto text = juce::String (khz, khz < 10.0f ? 1 : 0);
    return (text.endsWith (".0") ? text.dropLastCharacters (2) : text) + " kHz";
}

juce::String MorphPad::formatGain (float db)
{
    const auto whole = juce::roundToInt (db);
    return (whole > 0 ? "+" : "") + juce::String (whole) + " dB";
}

juce::String MorphPad::rangeCaption() const
{
    return formatFrequency (view.minHz) + " " + Theme::Glyph::emDash() + " " + formatFrequency (view.maxHz);
}

juce::String MorphPad::gainCaption() const
{
    if (juce::approximatelyEqual (view.topDb, -view.bottomDb))
        return "gain " + Theme::Glyph::plusMinus() + juce::String (juce::roundToInt (view.topDb)) + " dB";

    return "gain " + formatGain (view.topDb).upToFirstOccurrenceOf (" ", false, false) + " / "
                   + formatGain (view.bottomDb);
}

//==============================================================================
float MorphPad::xForFrequency (double hz) const
{
    const auto u = std::log (juce::jmax (1.0, hz) / (double) view.minHz) / std::log ((double) view.maxHz / (double) view.minHz);
    return padArea.getX() + (float) u * padArea.getWidth();
}

float MorphPad::yForDecibels (double db) const
{
    // Out-of-range gains are pinned to the edge, as the design does, rather than leaving
    // the curve.
    const auto clamped = juce::jlimit ((double) view.bottomDb, (double) view.topDb, db);
    const auto u = ((double) view.topDb - clamped) / (double) (view.topDb - view.bottomDb);
    return padArea.getY() + (float) u * padArea.getHeight();
}

double MorphPad::frequencyForX (float x) const
{
    const auto u = juce::jlimit (0.0, 1.0, (double) ((x - padArea.getX()) / padArea.getWidth()));
    return (double) view.minHz * std::pow ((double) view.maxHz / (double) view.minHz, u);
}

double MorphPad::decibelsForY (float y) const
{
    const auto u = juce::jlimit (0.0, 1.0, (double) ((y - padArea.getY()) / padArea.getHeight()));
    return (double) view.topDb - u * (double) (view.topDb - view.bottomDb);
}

juce::Point<float> MorphPad::dotCentre (int setting, int band) const
{
    const auto& slot = bands[(size_t) setting][(size_t) band];
    return { xForFrequency (slot.freqValue->load()), yForDecibels (slot.gainValue->load()) };
}

std::pair<int, int> MorphPad::dotAt (juce::Point<float> pos) const
{
    const int focus = displayed.load (std::memory_order_relaxed);

    // Two passes so the focused setting's dots, which are the draggable ones, win a tie.
    for (int pass = 0; pass < 2; ++pass)
    {
        for (int s = 0; s < LoudnessIDs::numSettings; ++s)
        {
            if ((pass == 0) != (s == focus))
                continue;

            for (int b = 0; b < LoudnessIDs::numBands; ++b)
                if (dotCentre (s, b).getDistanceFrom (pos) <= dotHitRadius)
                    return { s, b };
        }
    }

    return { -1, -1 };
}

//==============================================================================
double MorphPad::settingMagnitudeDb (int setting, double hz) const
{
    auto sr = processor.getSampleRate();
    if (sr <= 0.0)
        sr = 48000.0;

    double db = 0.0;

    // The two shelves run in series, so their dB responses add.
    for (int b = 0; b < LoudnessIDs::numBands; ++b)
    {
        const auto& slot = bands[(size_t) setting][(size_t) b];
        const auto gainDb = slot.gainValue->load();

        if (juce::approximatelyEqual (gainDb, 0.0f))
            continue;

        const auto coeffs = juce::dsp::IIR::Coefficients<float>::makeLowShelf (
            sr,
            juce::jlimit (10.0f, (float) (sr * 0.45), slot.freqValue->load()),
            juce::jmax (0.05f, slot.qValue->load()),
            juce::Decibels::decibelsToGain (gainDb));

        db += juce::Decibels::gainToDecibels (coeffs->getMagnitudeForFrequency (hz, sr), -120.0);
    }

    return db;
}

void MorphPad::buildCurve (int setting, juce::Path& stroke, juce::Path* fill) const
{
    stroke.clear();
    stroke.preallocateSpace (numCurvePoints * 3 + 8);

    // Sample the visible range only, so zooming in also sharpens the curve.
    for (int i = 0; i < numCurvePoints; ++i)
    {
        const auto u  = (double) i / (double) (numCurvePoints - 1);
        const auto hz = (double) view.minHz * std::pow ((double) view.maxHz / (double) view.minHz, u);
        const auto x  = padArea.getX() + (float) u * padArea.getWidth();
        const auto y  = yForDecibels (settingMagnitudeDb (setting, hz));

        if (i == 0)
            stroke.startNewSubPath (x, y);
        else
            stroke.lineTo (x, y);
    }

    if (fill != nullptr)
    {
        *fill = stroke;
        fill->lineTo (padArea.getRight(), yForDecibels (0.0));
        fill->lineTo (padArea.getX(),     yForDecibels (0.0));
        fill->closeSubPath();
    }
}

void MorphPad::rebuildDirtyCurves()
{
    const int focus = displayed.load (std::memory_order_relaxed);

    for (int s = 0; s < LoudnessIDs::numSettings; ++s)
    {
        const bool needFill = (s == focus && fillDirty);

        if (! curveDirty[(size_t) s] && ! needFill)
            continue;

        buildCurve (s, curves[(size_t) s], needFill ? &focusFill : nullptr);
        curveDirty[(size_t) s] = false;

        if (needFill)
            fillDirty = false;
    }
}

//==============================================================================
void MorphPad::updatePadArea()
{
    // 1px inset so the curve and the border do not fight over the same pixel.
    const auto area = getLocalBounds().toFloat().reduced (1.0f);

    if (area != padArea)
    {
        padArea = area;
        curveDirty.fill (true);     // the cached paths are in pixel space
        fillDirty = true;
        background = {};
    }
}

void MorphPad::resized()
{
    updatePadArea();
}

void MorphPad::rebuildBackground (float scale)
{
    const auto w = juce::jmax (1, juce::roundToInt ((float) getWidth() * scale));
    const auto h = juce::jmax (1, juce::roundToInt ((float) getHeight() * scale));

    // RGB, not ARGB: an opaque source is blitted, while an ARGB one is alpha-blended pixel
    // by pixel -- measured at 5.1 ms vs 10-14 ms a frame for this image. padBg covers it, so
    // there is nothing to clear first.
    background = juce::Image (juce::Image::RGB, w, h, false);
    backgroundScale = scale;

    juce::Graphics ig (background);
    ig.addTransform (juce::AffineTransform::scale (scale));     // draw in logical coordinates

    ig.fillAll (Theme::padBg);

    {
        juce::Graphics::ScopedSaveState clipToPlot (ig);
        ig.reduceClipRegion (padArea.toNearestInt());

        drawGrid (ig);

        // The other three settings belong here too: they do not move while the focused one
        // is being edited, which is exactly when repaints come thick and fast.
        const int focus = displayed.load (std::memory_order_relaxed);

        for (int s = 0; s < LoudnessIDs::numSettings; ++s)
        {
            if (s == focus)
                continue;

            ig.setColour (Theme::setting (s).withAlpha (0.32f));
            ig.strokePath (curves[(size_t) s], juce::PathStrokeType (1.25f));

            for (int b = 0; b < LoudnessIDs::numBands; ++b)
                drawDot (ig, s, b, false);
        }
    }

    ig.setColour (Theme::padBorder);
    ig.drawRect (getLocalBounds().toFloat(), 1.0f);
}

void MorphPad::drawDot (juce::Graphics& g, int setting, int band, bool isFocus) const
{
    const auto colour = Theme::setting (setting);
    const auto centre = dotCentre (setting, band);
    const auto circle = juce::Rectangle<float> (isFocus ? dotSizeFocused : dotSizeGhost,
                                                isFocus ? dotSizeFocused : dotSizeGhost).withCentre (centre);
    const bool hovered = (setting == hoverSetting && band == hoverBand);

    if (isFocus)
    {
        // The design's box-shadow: 0 0 0 4px rgba(255,255,255,.05)
        g.setColour (juce::Colours::white.withAlpha (0.05f));
        g.fillEllipse (circle.expanded (4.0f));
    }

    g.setColour (hovered ? Theme::dotHover : Theme::dotFill);
    g.fillEllipse (circle);

    g.setColour (isFocus ? colour : colour.withAlpha (0.62f));
    g.drawEllipse (circle.reduced (1.0f), 2.0f);

    if (! isFocus)
        return;

    // Up and to the right, as the design has it -- but flipped below and/or to the left when
    // the dot sits so close to an edge of the (zoomable) plot that it would be clipped.
    auto label = juce::Rectangle<float> (centre.x + 11.0f, centre.y - 32.0f, 40.0f, 12.0f);
    auto justification = juce::Justification::centredLeft;

    if (label.getY() < padArea.getY())
        label.setY (centre.y + 14.0f);

    if (label.getRight() > padArea.getRight())
    {
        label.setX (centre.x - 11.0f - label.getWidth());
        justification = juce::Justification::centredRight;
    }

    g.setColour (colour);
    g.setFont (Theme::mono (9.0f, true));
    g.drawText ("EQ" + juce::String (band + 1), label, justification);
}

void MorphPad::drawGrid (juce::Graphics& g) const
{
    // Axis-aligned single-pixel fills rather than drawLine: no antialiasing to compute, and
    // the grid is the most line-heavy part of the pad.
    const auto plot = padArea.toNearestInt();

    auto vertical = [&] (double hz)
    {
        g.fillRect (juce::roundToInt (xForFrequency (hz)), plot.getY(), 1, plot.getHeight());
    };

    auto horizontal = [&] (double db)
    {
        g.fillRect (plot.getX(), juce::roundToInt (yForDecibels (db)), plot.getWidth(), 1);
    };

    //--- frequency: n x 10^k inside the view; the decades are major ---------------
    std::vector<double> majorHz, minorHz;
    const auto firstDecade = (int) std::floor (std::log10 ((double) view.minHz));
    const auto lastDecade  = (int) std::ceil  (std::log10 ((double) view.maxHz));

    for (int k = firstDecade; k <= lastDecade; ++k)
    {
        const auto decade = std::pow (10.0, (double) k);

        for (int n = 1; n <= 9; ++n)
        {
            const auto hz = decade * n;

            // Strictly inside: a line on the edge would just thicken the frame.
            if (hz > (double) view.minHz * 1.001 && hz < (double) view.maxHz * 0.999)
                (n == 1 ? majorHz : minorHz).push_back (hz);
        }
    }

    //--- gain: the smallest nice step giving at most six intervals; soft half-steps -
    const auto span = (double) (view.topDb - view.bottomDb);
    double majorStep = 24.0;

    for (auto step : { 1.0, 2.0, 3.0, 6.0, 12.0, 24.0 })
    {
        if (span / step <= 6.0)
        {
            majorStep = step;
            break;
        }
    }

    const auto minorStep = juce::approximatelyEqual (majorStep, 3.0) ? 1.0 : majorStep * 0.5;
    std::vector<double> majorDb, minorDb;

    for (auto i = (int) std::ceil ((double) view.bottomDb / minorStep); (double) i * minorStep < (double) view.topDb; ++i)
    {
        const auto db = (double) i * minorStep;
        const auto onMajor = std::abs (std::remainder (db, majorStep)) < 1.0e-6;

        if (db <= (double) view.bottomDb + 1.0e-6 || std::abs (db) < 1.0e-6)
            continue;                                   // edge, or 0 dB (drawn on its own)

        (onMajor ? majorDb : minorDb).push_back (db);
    }

    //--- soft minors under the majors, 0 dB on top -----------------------------------
    g.setColour (Theme::gridMinor);
    for (auto hz : minorHz) vertical (hz);
    for (auto db : minorDb) horizontal (db);

    g.setColour (Theme::gridLine);
    for (auto hz : majorHz) vertical (hz);
    for (auto db : majorDb) horizontal (db);

    if (view.bottomDb < 0.0f && view.topDb > 0.0f)
    {
        g.setColour (Theme::zeroLine);
        horizontal (0.0);
    }
}

void MorphPad::paint (juce::Graphics& g)
{
    if (padArea.isEmpty())
        updatePadArea();

    rebuildDirtyCurves();

    const int focus = displayed.load (std::memory_order_relaxed);
    const auto focusColour = Theme::setting (focus);

    // Background, grid, frame and the three ghost settings come from the cache; only the
    // focused setting is redrawn per edit.
    const auto scale = (float) g.getInternalContext().getPhysicalPixelScaleFactor();

    if (background.isNull() || ! juce::approximatelyEqual (scale, backgroundScale))
        rebuildBackground (scale);

    {
        // Cancel the context's scale so the image lands 1:1 on the pixels it was rendered
        // for: that leaves a translation-only transform, which the software renderer blits
        // instead of resampling (resampling it cost ~6 ms a frame).
        juce::Graphics::ScopedSaveState unscaled (g);
        g.addTransform (juce::AffineTransform::scale (1.0f / scale));
        g.drawImageAt (background, 0, 0);
    }

    {
        // Everything plotted stays inside the plot area: with a zoomed view, curves and dots
        // can fall outside it.
        juce::Graphics::ScopedSaveState clipToPlot (g);
        g.reduceClipRegion (padArea.toNearestInt());

        g.setColour (focusColour.withAlpha (0.09f));
        g.fillPath (focusFill);

        g.setColour (focusColour);
        g.strokePath (curves[(size_t) focus], juce::PathStrokeType (2.5f));

        for (int b = 0; b < LoudnessIDs::numBands; ++b)
            drawDot (g, focus, b, true);
    }

    //--- corner captions: the visible range -----------------------------------
    g.setColour (Theme::inkFaint);
    g.setFont (Theme::mono (9.0f));

    g.drawText (rangeCaption(),
                padArea.withTrimmedLeft (8.0f).withTrimmedBottom (5.0f).removeFromBottom (12.0f),
                juce::Justification::bottomLeft);

    g.drawText (gainCaption(),
                padArea.withTrimmedRight (8.0f).withTrimmedTop (6.0f).removeFromTop (12.0f),
                juce::Justification::topRight);

}

//==============================================================================
void MorphPad::mouseDown (const juce::MouseEvent& e)
{
    const auto [s, b] = dotAt (e.position);

    if (s < 0)
        return;

    if (s != displayed.load (std::memory_order_relaxed))
    {
        // A faint dot is a shortcut to that setting, not a drag handle.
        if (onSelectSetting != nullptr)
            onSelectSetting (s);

        return;
    }

    dragSetting = s;
    dragBand    = b;

    auto& slot = bands[(size_t) s][(size_t) b];
    slot.gain->beginChangeGesture();
    slot.freq->beginChangeGesture();

    mouseDrag (e);
}

void MorphPad::mouseDrag (const juce::MouseEvent& e)
{
    if (dragSetting < 0)
        return;

    auto& slot = bands[(size_t) dragSetting][(size_t) dragBand];

    // Let each parameter's own NormalisableRange do the clamping, so the dot stops
    // exactly where the parameter does rather than where the pad's axis ends.
    const auto hz = slot.freq->getNormalisableRange().snapToLegalValue ((float) frequencyForX (e.position.x));
    const auto db = slot.gain->getNormalisableRange().snapToLegalValue ((float) decibelsForY (e.position.y));

    slot.freq->setValueNotifyingHost (slot.freq->convertTo0to1 (hz));
    slot.gain->setValueNotifyingHost (slot.gain->convertTo0to1 (db));
}

void MorphPad::mouseUp (const juce::MouseEvent&)
{
    if (dragSetting < 0)
        return;

    auto& slot = bands[(size_t) dragSetting][(size_t) dragBand];
    slot.gain->endChangeGesture();
    slot.freq->endChangeGesture();

    dragSetting = dragBand = -1;
}

void MorphPad::mouseMove (const juce::MouseEvent& e)
{
    const auto [s, b] = dotAt (e.position);

    if (s != hoverSetting || b != hoverBand)
    {
        const int focus = displayed.load (std::memory_order_relaxed);

        if (hoverSetting != focus || s != focus)
            background = {};    // a ghost dot's hover state is baked into the cache

        hoverSetting = s;
        hoverBand    = b;
        repaint();
    }

    if (s < 0)
        setMouseCursor (juce::MouseCursor::NormalCursor);
    else if (s == displayed.load (std::memory_order_relaxed))
        setMouseCursor (juce::MouseCursor::DraggingHandCursor);
    else
        setMouseCursor (juce::MouseCursor::PointingHandCursor);
}

void MorphPad::mouseExit (const juce::MouseEvent&)
{
    if (hoverSetting >= 0)
    {
        hoverSetting = hoverBand = -1;
        repaint();
    }

    setMouseCursor (juce::MouseCursor::NormalCursor);
}
