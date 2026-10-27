#pragma once

#include <juce_gui_basics/juce_gui_basics.h>

//==============================================================================
/** Palette and type ramp for the "Morph Pad" GUI.

    Ported from the design project "Audio plugin GUI redesign" (Morph Pad.dc.html).
    Everything visual lives here so the look can be retuned without hunting through
    paint() methods.

    FONTS. The design asks for IBM Plex Sans / IBM Plex Mono. Neither is bundled --
    JUCE cannot pull a webfont, and nothing in this project embeds a .ttf -- so the
    nearest system families stand in: the default sans, and the platform's default
    monospaced family everywhere the design says Plex Mono. To use the real thing,
    drop the .ttf files in, add them with juce_add_binary_data, and point these two
    helpers at the typeface; nothing else has to change.
*/
namespace Theme
{
    //--- surfaces -------------------------------------------------------------
    inline const juce::Colour card         { 0xff0f1317 };
    inline const juce::Colour cardBorder   { 0xff000000 };
    inline const juce::Colour headerBg     { 0xff161b21 };
    inline const juce::Colour headerLine   { 0xff232a31 };
    inline const juce::Colour footerBg     { 0xff0b0f13 };
    inline const juce::Colour footerLine   { 0xff1f262d };
    inline const juce::Colour padBg        { 0xff080b0d };
    inline const juce::Colour padBorder    { 0xff1e252c };
    inline const juce::Colour popBg        { 0xff12181e };
    inline const juce::Colour popHeadBg    { 0xff0b0f13 };
    inline const juce::Colour sunken       { 0xff0a0d10 };
    inline const juce::Colour track        { 0xff1c232a };
    inline const juce::Colour chipBg       { 0xff0f1418 };
    inline const juce::Colour chipBgOn     { 0xff171e25 };
    inline const juce::Colour chipBorder   { 0xff1c232a };
    inline const juce::Colour chipBorderOn { 0xff2b343d };
    inline const juce::Colour hairline     { 0xff242b33 };
    inline const juce::Colour hairlineLit  { 0xff3a444e };

    //--- graph ----------------------------------------------------------------
    inline const juce::Colour gridLine     { 0xff181f25 };
    inline const juce::Colour gridMinor    { 0xff0f1419 };   // very soft: barely off padBg
    inline const juce::Colour zeroLine     { 0xff33404a };
    inline const juce::Colour dotFill      { 0xff0b0e11 };
    inline const juce::Colour dotHover     { 0xff151c22 };

    //--- ink ------------------------------------------------------------------
    inline const juce::Colour ink          { 0xffe6ebf0 };
    inline const juce::Colour inkLabel     { 0xffc3ced8 };
    inline const juce::Colour inkDim       { 0xff8b9aa6 };
    inline const juce::Colour inkMuted     { 0xff7c8996 };
    inline const juce::Colour inkFaint     { 0xff6b7884 };
    inline const juce::Colour caretOff     { 0xff4f5a64 };
    inline const juce::Colour onAccent     { 0xff0a0d10 };

    /** Per-setting accent. Index 0 doubles as the global accent (meter, detector
        readout), exactly as the design's colors(i) does. */
    inline juce::Colour setting (int index)
    {
        static const juce::Colour colours[] { juce::Colour (0xff4db8e8),
                                              juce::Colour (0xff5fbf8f),
                                              juce::Colour (0xffd59b4a),
                                              juce::Colour (0xffd1707a) };
        return colours[(size_t) juce::jlimit (0, 3, index)];
    }

    inline juce::Colour accent() { return setting (0); }

    //--- type -----------------------------------------------------------------
    inline juce::Font sans (float height, bool semiBold = false)
    {
        auto options = juce::FontOptions (height);
        return juce::Font (semiBold ? options.withStyle ("Bold") : options);
    }

    inline juce::Font mono (float height, bool semiBold = false)
    {
        auto options = juce::FontOptions (juce::Font::getDefaultMonospacedFontName(),
                                          height, juce::Font::plain);
        return juce::Font (semiBold ? options.withStyle ("Bold") : options);
    }

    /** Non-ASCII glyphs must go through CharPointer_UTF8: juce::String's const char*
        constructor assumes ASCII and asserts on any byte > 127 (juce_String.cpp:327),
        which in Release silently renders as mojibake instead. */
    inline juce::String utf8 (const char* literal)
    {
        return juce::String (juce::CharPointer_UTF8 (literal));
    }

    namespace Glyph
    {
        inline juce::String emDash()   { return utf8 ("\xe2\x80\x94"); }
        inline juce::String arrow()    { return utf8 ("\xe2\x86\x92"); }
        inline juce::String cross()    { return utf8 ("\xe2\x9c\x95"); }
        inline juce::String caretUp()  { return utf8 ("\xe2\x96\xb4"); }
        inline juce::String caretDown(){ return utf8 ("\xe2\x96\xbe"); }
        inline juce::String plusMinus(){ return utf8 ("\xc2\xb1"); }
        inline juce::String middot()   { return utf8 ("\xc2\xb7"); }
    }
}
