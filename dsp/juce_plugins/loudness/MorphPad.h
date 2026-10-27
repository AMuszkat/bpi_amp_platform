#pragma once

#include "PluginProcessor.h"
#include "Theme.h"

//==============================================================================
/** The morph pad: the four contours drawn on one log-frequency / gain plane, with
    the focused setting's two shelves as dots you drag to set frequency and gain.

    Replaces the old ResponsePlot. The curve maths and the responsiveness work carry
    over unchanged -- cached parameter pointers, one cached Path per setting rebuilt
    only when that setting's parameters change, and updates driven by APVTS listeners
    rather than a polling timer (APVTS dispatches parameterChanged synchronously, so a
    drag tracks the pointer with no added latency; the same callback arrives on the
    audio thread for host automation, hence the atomic dirty mask + AsyncUpdater).
    The design has no live/glide curve, so unlike its predecessor this component needs
    no timer at all: it repaints on change and is otherwise idle.

    AXES. The visible range is configurable (setViewLimits; default 20 Hz - 20 kHz and
    +/-28 dB, as the design specifies). The grid is generated from it: a line at every
    frequency n x 10^k -- the decades major, the rest very soft minors -- and gain lines at a
    step chosen so there are at most six intervals, with soft half-steps between them.
    Note the shelf frequency parameter itself only ranges to 2 kHz, so at the default view a
    dragged dot stops about two thirds across; that is the parameter's range, not the pad's.
*/
class MorphPad  : public juce::Component,
                  private juce::AsyncUpdater,
                  private juce::AudioProcessorValueTreeState::Listener
{
public:
    explicit MorphPad (LoudnessProcessor&);
    ~MorphPad() override;

    void paint (juce::Graphics&) override;
    void resized() override;

    void mouseDown (const juce::MouseEvent&) override;
    void mouseDrag (const juce::MouseEvent&) override;
    void mouseUp   (const juce::MouseEvent&) override;
    void mouseMove (const juce::MouseEvent&) override;
    void mouseExit (const juce::MouseEvent&) override;

    /** Which setting is drawn bold and gets draggable dots. The editor pins this to
        the open popover's setting, and otherwise to the detector's live zone -- the
        design's activeIndex(). */
    void setDisplayedSetting (int index);

    /** Fired when a faint setting's dot is clicked: the design opens that setting. */
    std::function<void (int)> onSelectSetting;

    /** The visible frequency / gain range. Rebuilds the curves (they are cached in pixels). */
    void setViewLimits (const LoudnessProcessor::ViewLimits&);

    static juce::String formatFrequency (float hz);   // "20 Hz", "1.5 kHz", "20 kHz"
    static juce::String formatGain (float db);        // "+28 dB", "-6 dB", "0 dB"

private:
    void handleAsyncUpdate() override;
    void parameterChanged (const juce::String& parameterID, float newValue) override;

    void markSettingDirty (int setting);        // any thread
    void rebuildDirtyCurves();

    void updatePadArea();

    float xForFrequency (double hz) const;
    float yForDecibels (double db) const;
    double frequencyForX (float x) const;
    double decibelsForY (float y) const;

    juce::Point<float> dotCentre (int setting, int band) const;
    /** Index of the dot under `pos`, or {-1,-1}. The focused setting wins ties. */
    std::pair<int, int> dotAt (juce::Point<float> pos) const;

    void buildCurve (int setting, juce::Path& stroke, juce::Path* fill) const;
    double settingMagnitudeDb (int setting, double hz) const;

    LoudnessProcessor& processor;

    static constexpr int numCurvePoints = 201;
    LoudnessProcessor::ViewLimits view;         // visible range, set by the editor

    void drawGrid (juce::Graphics&) const;
    void drawDot (juce::Graphics&, int setting, int band, bool isFocus) const;

    /** Background, grid and frame rendered once into an image. They only change with the
        size or the view, while curves and dots change on every parameter edit -- and a full
        repaint costs ~12 ms in a Debug build, which made dragging a settings row stutter. */
    void rebuildBackground (float scale);

    juce::Image background;
    float backgroundScale = 0.0f;

    juce::String rangeCaption() const;
    juce::String gainCaption() const;

    struct BandParams
    {
        std::atomic<float>*         gainValue = nullptr;   // reading (lock free)
        std::atomic<float>*         freqValue = nullptr;
        std::atomic<float>*         qValue    = nullptr;
        juce::RangedAudioParameter* gain      = nullptr;   // writing (gestures)
        juce::RangedAudioParameter* freq      = nullptr;
    };

    std::array<std::array<BandParams, LoudnessIDs::numBands>, LoudnessIDs::numSettings> bands;

    std::array<juce::Path, LoudnessIDs::numSettings> curves;
    std::array<bool, LoudnessIDs::numSettings> curveDirty;
    juce::Path focusFill;
    bool fillDirty = true;

    std::atomic<int> pendingDirty { 0 };
    std::atomic<int> displayed { 0 };

    juce::Rectangle<float> padArea;

    int dragSetting = -1, dragBand = -1;        // gesture in progress
    int hoverSetting = -1, hoverBand = -1;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (MorphPad)
};
