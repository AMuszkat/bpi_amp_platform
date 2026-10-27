#include "ScopeView.h"

#include <cmath>

//==============================================================================
namespace ScopeStyle
{

juce::Colour colourFor (amp::Channel c)
{
    switch (c)
    {
        case amp::Channel::vout: return Theme::setting (0);   // blue
        case amp::Channel::iout: return Theme::setting (2);   // amber
        case amp::Channel::pvdd: return Theme::setting (1);   // green
        case amp::Channel::vbat: return juce::Colour (0xff9a86d8);
        case amp::Channel::temp: return Theme::setting (3);   // red
        case amp::Channel::count: break;
    }

    return Theme::inkDim;
}

juce::String format (amp::Channel c, float value, int significantDigits)
{
    const juce::String unit (amp::channelUnit (c));

    // Temperature has no useful prefixes and is never small.
    if (c == amp::Channel::temp)
        return juce::String (value, 1) + " " + unit;

    const float mag = std::abs (value);
    float scaled = value;
    juce::String prefix;

    // The thresholds sit a hair below the decade so a value that will PRINT as
    // 1.00 does not get dressed up as "1000 m".
    if (mag >= 0.9995f || juce::exactlyEqual (mag, 0.0f)) { }
    else if (mag >= 0.9995e-3f)            { scaled = value * 1.0e3f;  prefix = "m"; }
    else                                   { scaled = value * 1.0e6f;  prefix = juce::String ("u"); }

    const float absScaled = std::abs (scaled);
    const int decimals = absScaled >= 100.0f ? juce::jmax (0, significantDigits - 3)
                       : absScaled >= 10.0f  ? juce::jmax (0, significantDigits - 2)
                                             : juce::jmax (0, significantDigits - 1);

    return juce::String (scaled, decimals) + " " + prefix + unit;
}

juce::String formatTime (double seconds)
{
    const double mag = std::abs (seconds);

    if (mag >= 1.0)     return juce::String (seconds, mag >= 10.0 ? 1 : 2) + " s";
    if (mag >= 1.0e-3)  return juce::String (seconds * 1.0e3, mag >= 1.0e-2 ? 1 : 2) + " ms";

    return juce::String (seconds * 1.0e6, mag >= 1.0e-5 ? 0 : 1) + " " + juce::String ("us");
}

} // namespace ScopeStyle

//==============================================================================
ScopeView::ScopeView (amp::Telemetry& t)  : telemetry (t)
{
    setOpaque (true);

    traces[(size_t) amp::Channel::vout] = { true,  5.0f,   0.0f };
    traces[(size_t) amp::Channel::iout] = { true,  1.0f,   0.0f };
    traces[(size_t) amp::Channel::pvdd] = { true,  4.0f,  -3.0f };   // sits low: it is a rail, not a swing
    traces[(size_t) amp::Channel::vbat] = { false, 1.0f,  -3.0f };
    traces[(size_t) amp::Channel::temp] = { false, 20.0f, -3.0f };

    startTimerHz (30);
}

ScopeView::~ScopeView() = default;

void ScopeView::setWindowSeconds (double s)
{
    const auto clamped = juce::jlimit (100.0e-6, 30.0, s);

    if (! juce::approximatelyEqual (clamped, windowSeconds))
    {
        windowSeconds = clamped;
        rebuildBackground();
        repaint();
    }
}

double ScopeView::getEffectiveWindowSeconds() const
{
    const auto rate = telemetry.getStatus().sampleRate;

    if (rate <= 0.0)
        return windowSeconds;

    const double holdable = (double) (telemetry.getRing().getCapacity() - 4) / rate;
    return juce::jmin (windowSeconds, holdable);
}

bool ScopeView::isWindowClipped() const
{
    return getEffectiveWindowSeconds() < windowSeconds * 0.999;
}

void ScopeView::setTrace (amp::Channel c, const Trace& t)
{
    traces[(size_t) c] = t;
    repaint();
}

void ScopeView::setTrigger (TriggerMode mode, amp::Channel source, TriggerEdge edge, float level)
{
    const bool modeChanged = (mode != triggerMode);

    triggerMode = mode;
    triggerSource = source;
    triggerEdge = edge;
    triggerLevel = level;

    if (modeChanged)
    {
        holding = false;
        singleArmed = (mode == TriggerMode::single);
    }

    repaint();
}

void ScopeView::armSingle()
{
    singleArmed = true;
    holding = false;
    repaint();
}

void ScopeView::setRunning (bool shouldRun)
{
    if (running == shouldRun)
        return;

    running = shouldRun;

    if (running)
        holding = false;

    repaint();
}

void ScopeView::resized()
{
    rebuildBackground();
}

void ScopeView::refreshWindow()
{
    juce::uint64 first = 0;
    int count = 0;

    blankReason = Blank::filling;
    haveWindow = resolveWindow (first, count);

    if (haveWindow)
    {
        windowFirstFrame = first;
        windowFrameCount = count;
        updateMeasurements (first, count);
    }
    else
    {
        for (auto& m : measurements)
            m = {};
    }
}

void ScopeView::timerCallback()
{
    if (running && ! holding)
    {
        refreshWindow();
        repaint();
    }
}

juce::Rectangle<float> ScopeView::screenArea() const
{
    // Room on the left for the level scale and under for the time scale.
    return getLocalBounds().toFloat().withTrimmedLeft (52.0f)
                                     .withTrimmedRight (8.0f)
                                     .withTrimmedTop (8.0f)
                                     .withTrimmedBottom (20.0f);
}

//==============================================================================
void ScopeView::rebuildBackground()
{
    background = {};
    repaint();
}

void ScopeView::paint (juce::Graphics& g)
{
    const auto bounds = getLocalBounds().toFloat();
    const auto screen = screenArea();

    if (screen.getWidth() < 8.0f || screen.getHeight() < 8.0f)
        return;

    //--- the static half: background, graticule, scales -----------------------
    // Cached in an opaque image for the same reason the loudness Morph Pad does
    // it: the software renderer redraws every grid line otherwise, and that is
    // the bulk of a frame at 30 Hz.
    const float scale = (float) g.getInternalContext().getPhysicalPixelScaleFactor();

    const double effectiveNow = getEffectiveWindowSeconds();

    if (background.isNull() || ! juce::approximatelyEqual (scale, backgroundScale)
         || ! juce::approximatelyEqual (effectiveNow, backgroundWindow))
    {
        backgroundScale = juce::jmax (1.0f, scale);
        backgroundWindow = effectiveNow;

        const int w = juce::roundToInt (bounds.getWidth()  * backgroundScale);
        const int h = juce::roundToInt (bounds.getHeight() * backgroundScale);

        if (w <= 0 || h <= 0)
            return;

        background = juce::Image (juce::Image::RGB, w, h, false);
        juce::Graphics ig (background);
        ig.addTransform (juce::AffineTransform::scale (backgroundScale));

        ig.fillAll (Theme::card);
        ig.setColour (Theme::padBg);
        ig.fillRect (screen);

        // graticule
        for (int i = 0; i <= timeDivs; ++i)
        {
            const float x = screen.getX() + screen.getWidth() * (float) i / (float) timeDivs;
            ig.setColour (i == timeDivs / 2 ? Theme::zeroLine : Theme::gridLine);
            ig.drawVerticalLine (juce::roundToInt (x), screen.getY(), screen.getBottom());
        }

        for (int i = 0; i <= levelDivs; ++i)
        {
            const float y = screen.getY() + screen.getHeight() * (float) i / (float) levelDivs;
            ig.setColour (i == levelDivs / 2 ? Theme::zeroLine : Theme::gridLine);
            ig.drawHorizontalLine (juce::roundToInt (y), screen.getX(), screen.getRight());
        }

        ig.setColour (Theme::padBorder);
        ig.drawRect (screen, 1.0f);

        // level scale: divisions from the centre, unlabelled -- each trace has
        // its own units/div, so the only honest shared label is "divisions".
        ig.setFont (Theme::mono (9.5f));
        ig.setColour (Theme::inkFaint);

        for (int i = 0; i <= levelDivs; ++i)
        {
            const float y = screen.getY() + screen.getHeight() * (float) i / (float) levelDivs;
            const int   div = levelDivs / 2 - i;

            ig.drawText (div == 0 ? juce::String ("0") : juce::String (div),
                         juce::Rectangle<float> (0.0f, y - 7.0f, screen.getX() - 6.0f, 14.0f),
                         juce::Justification::centredRight, false);
        }

        ig.setColour (Theme::inkFaint);
        ig.setFont (Theme::mono (9.5f));

        const double effective = getEffectiveWindowSeconds();

        for (int i = 0; i <= timeDivs; i += 2)
        {
            const float x = screen.getX() + screen.getWidth() * (float) i / (float) timeDivs;
            const double t = ((double) i / (double) timeDivs - 1.0) * effective;

            ig.drawText (i == timeDivs ? juce::String ("0")
                                       : ScopeStyle::formatTime (t),
                         juce::Rectangle<float> (x - 32.0f, screen.getBottom() + 3.0f, 64.0f, 14.0f),
                         juce::Justification::centred, false);
        }
    }

    {
        juce::Graphics::ScopedSaveState unscaled (g);
        g.addTransform (juce::AffineTransform::scale (1.0f / backgroundScale));
        g.drawImageAt (background, 0, 0);
    }

    //--- the live half --------------------------------------------------------
    if (! haveWindow)
        refreshWindow();

    if (! haveWindow)
    {
        // "stopped", "nothing is arriving" and "the trigger has not fired" look
        // identical on a blank screen and mean completely different things.
        const bool acquiring = telemetry.getStatus().running;

        juce::String why;

        if (! running)                          why = "stopped";
        else if (! acquiring)                   why = "no acquisition   " + Theme::Glyph::middot()
                                                        + "   see the status line";
        else if (blankReason == Blank::noTrigger) why = "no trigger   " + Theme::Glyph::middot()
                                                        + "   nothing crosses the level on "
                                                        + amp::channelName (triggerSource);
        else                                    why = "filling the record...";

        g.setColour (blankReason == Blank::noTrigger && running && acquiring ? Theme::setting (2)
                   : running && ! acquiring                                  ? Theme::setting (3)
                                                                            : Theme::inkFaint);
        g.setFont (Theme::sans (12.0f));
        g.drawText (why, screen, juce::Justification::centred, false);
        return;
    }

    const auto first = windowFirstFrame;
    const auto count = windowFrameCount;
    const auto status = telemetry.getStatus();

    g.reduceClipRegion (screen.toNearestInt());

    for (int c = 0; c < amp::numChannels; ++c)
    {
        const auto channel = (amp::Channel) c;

        if (traces[(size_t) c].visible && status.available[(size_t) c])
            paintTrace (g, channel, screen, first, count);
    }

    //--- trigger marker -------------------------------------------------------
    if (triggerMode != TriggerMode::freeRun && status.available[(size_t) triggerSource])
    {
        const auto& t = traces[(size_t) triggerSource];
        const float y = levelToY (triggerLevel, t, screen);

        if (screen.getY() <= y && y <= screen.getBottom())
        {
            g.setColour (ScopeStyle::colourFor (triggerSource).withAlpha (0.55f));

            const float dashes[] { 4.0f, 4.0f };
            g.drawDashedLine ({ screen.getX(), y, screen.getRight(), y }, dashes, 2, 1.0f);

            juce::Path arrow;
            arrow.addTriangle (screen.getX(), y - 4.0f,
                               screen.getX(), y + 4.0f,
                               screen.getX() + 6.0f, y);
            g.setColour (ScopeStyle::colourFor (triggerSource));
            g.fillPath (arrow);
        }

        // where the trigger sits in time
        const float x = screen.getCentreX();
        g.setColour (Theme::hairlineLit.withAlpha (0.7f));
        g.fillRect (x - 0.5f, screen.getY(), 1.0f, 6.0f);
    }

    if (holding)
    {
        g.setColour (Theme::setting (3));
        g.setFont (Theme::mono (10.0f, true));
        g.drawText ("HOLD", screen.reduced (6.0f), juce::Justification::topRight, false);
    }
}

//==============================================================================
bool ScopeView::resolveWindow (juce::uint64& firstFrame, int& frameCount)
{
    const auto status = telemetry.getStatus();
    const auto& ring = telemetry.getRing();

    if (status.sampleRate <= 0.0)
        return false;

    if (holding)
    {
        firstFrame = heldFirstFrame;
        frameCount = heldFrameCount;
        return frameCount > 1;
    }

    const auto written = ring.getWritePos();
    const int wanted = juce::jmax (2, (int) std::lround (getEffectiveWindowSeconds() * status.sampleRate));

    if (written < (juce::uint64) wanted)
        return false;

    const auto oldest = written - (juce::uint64) juce::jmin ((int) written, ring.getCapacity() - 4);

    // Free-running: the window simply ends at the newest sample.
    auto endFrame = written;

    if (triggerMode != TriggerMode::freeRun && status.available[(size_t) triggerSource])
    {
        // Look back for the most recent crossing that still leaves half a window
        // of samples on each side of it, so the event can sit mid-screen.
        const juce::uint64 half = (juce::uint64) (wanted / 2);
        bool found = false;

        if (written > half + 1 && oldest + half + 2 < written - half)
        {
            for (auto p = written - half - 1; p > oldest + half + 1; --p)
            {
                const float a = ring.read (triggerSource, p - 1);
                const float b = ring.read (triggerSource, p);

                const bool crossed = (triggerEdge == TriggerEdge::rising)
                                        ? (a < triggerLevel && b >= triggerLevel)
                                        : (a > triggerLevel && b <= triggerLevel);

                if (crossed)
                {
                    endFrame = p + half;
                    found = true;
                    break;
                }
            }
        }

        if (found)
        {
            autoStarveCount = 0;

            if (triggerMode == TriggerMode::single && singleArmed)
            {
                singleArmed = false;
                holding = true;
                heldFirstFrame = endFrame - (juce::uint64) wanted;
                heldFrameCount = wanted;
            }
        }
        else
        {
            switch (triggerMode)
            {
                case TriggerMode::automatic:
                    // Free-run after a moment without an edge -- the usual "auto"
                    // behaviour, so a dead input still shows its DC level.
                    if (++autoStarveCount < 15)
                        return false;
                    break;

                case TriggerMode::normal:
                case TriggerMode::single:
                    blankReason = Blank::noTrigger;
                    return false;           // say nothing rather than invent an edge

                case TriggerMode::freeRun:
                    break;
            }
        }
    }

    if (endFrame < (juce::uint64) wanted)
        return false;

    firstFrame = endFrame - (juce::uint64) wanted;
    frameCount = wanted;

    if (firstFrame < oldest)
    {
        firstFrame = oldest;
        frameCount = (int) juce::jmin ((juce::uint64) wanted, endFrame - oldest);
    }

    return frameCount > 1;
}

void ScopeView::updateMeasurements (juce::uint64 firstFrame, int frameCount)
{
    const auto& ring = telemetry.getRing();
    const auto status = telemetry.getStatus();

    for (int c = 0; c < amp::numChannels; ++c)
    {
        auto& m = measurements[(size_t) c];

        if (! status.available[(size_t) c] || ! traces[(size_t) c].visible)
        {
            m = {};
            continue;
        }

        float lo = std::numeric_limits<float>::max();
        float hi = -std::numeric_limits<float>::max();
        double sum = 0.0;

        for (int i = 0; i < frameCount; ++i)
        {
            const float v = ring.read ((amp::Channel) c, firstFrame + (juce::uint64) i);
            lo = juce::jmin (lo, v);
            hi = juce::jmax (hi, v);
            sum += v;
        }

        m.valid = true;
        m.min = lo;
        m.max = hi;
        m.mean = (float) (sum / (double) frameCount);
    }
}

float ScopeView::levelToY (float value, const Trace& t, juce::Rectangle<float> screen) const
{
    const float divs = (t.unitsPerDiv > 0.0f ? value / t.unitsPerDiv : 0.0f) + t.offsetDivs;
    const float pxPerDiv = screen.getHeight() / (float) levelDivs;

    return screen.getCentreY() - divs * pxPerDiv;
}

void ScopeView::paintTrace (juce::Graphics& g, amp::Channel channel, juce::Rectangle<float> screen,
                            juce::uint64 firstFrame, int frameCount)
{
    const auto& ring = telemetry.getRing();
    const auto& trace = traces[(size_t) channel];

    const int columns = juce::jmax (2, (int) screen.getWidth());
    const float dx = screen.getWidth() / (float) (columns - 1);

    const auto colour = ScopeStyle::colourFor (channel);
    g.setColour (colour);

    if (frameCount <= columns)
    {
        // Fewer samples than pixels: join them up.
        juce::Path path;
        const float step = screen.getWidth() / (float) (frameCount - 1);

        for (int i = 0; i < frameCount; ++i)
        {
            const float v = ring.read (channel, firstFrame + (juce::uint64) i);
            const float x = screen.getX() + step * (float) i;
            const float y = levelToY (v, trace, screen);

            if (i == 0)
                path.startNewSubPath (x, y);
            else
                path.lineTo (x, y);
        }

        g.strokePath (path, juce::PathStrokeType (1.4f));
        return;
    }

    // More samples than pixels: one vertical bar per column spanning the
    // min..max of the samples that fall in it, which is what keeps a narrow
    // spike on screen instead of aliasing it into nothing.
    juce::RectangleList<float> bars;
    float previousMin = 0.0f, previousMax = 0.0f;
    bool  havePrevious = false;

    for (int col = 0; col < columns; ++col)
    {
        const auto begin = firstFrame + (juce::uint64) ((juce::int64) frameCount * col / columns);
        const auto end   = firstFrame + (juce::uint64) ((juce::int64) frameCount * (col + 1) / columns);

        float lo = std::numeric_limits<float>::max();
        float hi = -std::numeric_limits<float>::max();

        for (auto p = begin; p < juce::jmax (end, begin + 1); ++p)
        {
            const float v = ring.read (channel, p);
            lo = juce::jmin (lo, v);
            hi = juce::jmax (hi, v);
        }

        float top = levelToY (hi, trace, screen);
        float bottom = levelToY (lo, trace, screen);

        // Bridge to the previous column so the trace reads as one line.
        if (havePrevious)
        {
            top = juce::jmin (top, levelToY (previousMax, trace, screen));
            bottom = juce::jmax (bottom, levelToY (previousMin, trace, screen));
        }

        previousMin = lo;
        previousMax = hi;
        havePrevious = true;

        bars.addWithoutMerging ({ screen.getX() + dx * (float) col,
                                  top,
                                  juce::jmax (1.0f, dx),
                                  juce::jmax (1.2f, bottom - top) });
    }

    // Slightly transparent: at a slow timebase several traces fill the same band
    // of the screen, and an opaque fill would simply hide whichever drew first.
    g.setColour (colour.withAlpha (0.8f));
    g.fillRectList (bars);
}
