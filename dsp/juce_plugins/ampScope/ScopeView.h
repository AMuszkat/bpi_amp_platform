#pragma once

#include "AmpTelemetry.h"
#include "Theme.h"

//==============================================================================
/** The scope screen: time across, level up, one trace per quantity.

    Laid out like a bench instrument -- ten time divisions across, eight level
    divisions up, each trace with its own units/division and a vertical offset in
    divisions -- so the familiar knobs mean the familiar things.

    The trace data is pulled straight from the acquisition ring on the message
    thread at the repaint rate; nothing is copied per sample and the acquisition
    thread is never blocked. When a window holds more samples than the screen has
    columns the drawing decimates by min/max per column, which is what keeps a
    fast transient visible instead of aliasing it away.
*/
class ScopeView  : public juce::Component,
                   private juce::Timer
{
public:
    enum class TriggerMode { freeRun, automatic, normal, single };
    enum class TriggerEdge { rising, falling };

    struct Trace
    {
        bool  visible      = true;
        float unitsPerDiv  = 1.0f;
        float offsetDivs   = 0.0f;       ///< positive moves the trace up
    };

    explicit ScopeView (amp::Telemetry&);
    ~ScopeView() override;

    //--- what to show ---------------------------------------------------------
    void setWindowSeconds (double);
    double getWindowSeconds() const noexcept         { return windowSeconds; }

    /** What the screen can actually show: the requested window, clipped to the
        record length at the source's current rate. A 2 s window off a 192 kHz
        capture would need more frames than the ring holds, and silently drawing
        a shorter span under a full-length time axis would be a lie. */
    double getEffectiveWindowSeconds() const;
    bool isWindowClipped() const;

    void setTrace (amp::Channel, const Trace&);
    Trace getTrace (amp::Channel c) const            { return traces[(size_t) c]; }

    void setTrigger (TriggerMode, amp::Channel source, TriggerEdge, float level);
    void armSingle();

    void setRunning (bool);
    bool isRunning() const noexcept                  { return running; }

    /** True once a single-shot capture has been caught and frozen. */
    bool isHolding() const noexcept                  { return holding; }

    /** Per-trace measurements over the displayed window, for the readout strip. */
    struct Measurement { bool valid = false; float min = 0, max = 0, mean = 0; };
    Measurement getMeasurement (amp::Channel c) const { return measurements[(size_t) c]; }

    //--- juce::Component ------------------------------------------------------
    void paint (juce::Graphics&) override;
    void resized() override;

private:
    void timerCallback() override;
    void rebuildBackground();
    juce::Rectangle<float> screenArea() const;

    /** Re-run the trigger search and the measurements. Deliberately NOT done in
        paint(): an editor with no peer is never painted, and the readouts still
        have to be right -- and doing it once per tick means the trigger search
        happens once per frame, not once per repaint. */
    void refreshWindow();
    bool resolveWindow (juce::uint64& firstFrame, int& frameCount);
    void updateMeasurements (juce::uint64 firstFrame, int frameCount);
    void paintTrace (juce::Graphics&, amp::Channel, juce::Rectangle<float>,
                     juce::uint64 firstFrame, int frameCount);

    float levelToY (float value, const Trace&, juce::Rectangle<float>) const;

    amp::Telemetry& telemetry;

    double windowSeconds = 0.02;
    bool   running = true;
    bool   holding = false;
    bool   singleArmed = false;

    TriggerMode  triggerMode   = TriggerMode::automatic;
    amp::Channel triggerSource = amp::Channel::vout;
    TriggerEdge  triggerEdge   = TriggerEdge::rising;
    float        triggerLevel  = 0.0f;

    juce::uint64 heldFirstFrame = 0;
    int          heldFrameCount = 0;
    juce::uint64 windowFirstFrame = 0;
    int          windowFrameCount = 0;
    bool         haveWindow = false;

    /** Why there is nothing to draw. "Waiting for samples" and "the trigger has
        not fired" look identical on a blank screen and mean completely
        different things, so the screen says which. */
    enum class Blank { filling, noTrigger };
    Blank        blankReason = Blank::filling;
    int          autoStarveCount = 0;

    std::array<Trace, (size_t) amp::numChannels> traces;
    std::array<Measurement, (size_t) amp::numChannels> measurements;

    juce::Image background;
    float       backgroundScale = 1.0f;
    double      backgroundWindow = 0.0;

    static constexpr int timeDivs = 10;
    static constexpr int levelDivs = 8;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (ScopeView)
};

//==============================================================================
/** Colour and formatting for one quantity, shared by the screen and the legend. */
namespace ScopeStyle
{
    juce::Colour colourFor (amp::Channel);

    /** A value with a sensible unit prefix, e.g. "1.25 V", "-340 mA", "24.6 C". */
    juce::String format (amp::Channel, float value, int significantDigits = 3);

    /** Engineering-friendly time label: "5 ms", "200 us", "1.0 s". */
    juce::String formatTime (double seconds);
}
