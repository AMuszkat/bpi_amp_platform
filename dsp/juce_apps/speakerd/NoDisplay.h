#pragma once

#include <cstdlib>

namespace speakerd
{
    /** The DISPLAY speakerd runs with, whatever it was given: one that no X server can have. */
    inline constexpr const char* noDisplay = "speakerd-opens-no-display";

    /** Keeps speakerd, and the plugins it hosts, off every X server (README.md, "No X display").

        speakerd links no GUI module, but each JUCE LV2 plugin, the headless build too, links
        juce_gui_basics, and every instance of one starts JUCE's plugin message thread
        (juce_audio_plugin_client_LV2.cpp, detail::MessageThread), which creates the plugin's
        XWindowSystem: that calls XOpenDisplay on $DISPLAY, or on ":0.0" when DISPLAY is unset or
        empty (XWindowSystem::initialiseXDisplay). A plugin installs no X error handler (it is no
        standalone app), so when that server goes away Xlib's default handler ends speakerd,
        status 1, "X connection to :0.0 broken".

        A DISPLAY with no ':' and no leading '/' is one xcb_parse_display refuses, so XOpenDisplay
        returns null at once, before any socket, file or name lookup, and JUCE runs the plugin
        without X (isX11Available() false: no window, no event fd). Called first in main, before
        any thread exists, as setenv must be; nothing in speakerd reads DISPLAY afterwards. */
    inline void openNoDisplay()
    {
        ::setenv ("DISPLAY", noDisplay, 1);
    }
}
