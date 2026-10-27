#pragma once

#include <juce_gui_basics/juce_gui_basics.h>

#include <vector>

//==============================================================================
/** Answers two questions JUCE cannot, for a plugin UI embedded in a host's window on X11.
    Used to keep the floating settings panel attached to the card.

    - WHERE IS IT, REALLY? An embedded plugin UI is a reparented child window. JUCE caches
      the host window's screen position and refreshes it only on a ConfigureNotify for the
      embedded window (XWindowSystem::getWindowBounds), which never arrives when the *host*
      window is dragged -- the child does not move relative to its parent. So
      getScreenBounds() goes stale until something else happens to refresh it. This asks
      the X server directly.

    - IS THE HOST WINDOW THE ACTIVE ONE? The panel is an override-redirect window, so the
      window manager does not stack it: left alone it would float over other applications.

    It talks to JUCE's own X connection through JUCE_GUI_BASICS_INCLUDE_XHEADERS, the opt-in
    JUCE's LV2/VST wrappers and juce_opengl use -- libX11 stays dlopen'd, so the plugin gains
    no link-time dependency on it (which matters on a headless DSP box). The opt-in is
    confined to HostWindowTracker.cpp. Elsewhere, where child windows report their screen
    position correctly, both calls are no-ops.
*/
class HostWindowTracker
{
public:
    explicit HostWindowTracker (juce::Component& componentInsideHostWindow);

    /** What to add to the component's getScreenBounds() to get where it really is, in
        desktop units. One X round trip. */
    juce::Point<int> screenPositionError() const;

    /** False only when another top-level window is known to be the active one; true
        whenever that cannot be determined. One X round trip (plus a one-off walk up the
        window tree after reset()). */
    bool hostWindowIsActive (juce::Component* alsoOurs = nullptr);

    /** Give X keyboard focus to `window`'s own window, or hand it back to the host's.

        Needed because JUCE will not do it for a floating panel: its peer already believes it
        is focused, so ComponentPeer::grabFocus() short-circuits and never calls
        XSetInputFocus -- while the X server still has the focus on the host window, so every
        keystroke goes there. Verified: the server does accept focus on that window. */
    void takeKeyboardFocus (juce::Component& window);

    /** Marks `window` as a tool window belonging to the host's: WM_TRANSIENT_FOR so the window
        manager keeps it above the host and out of the window switcher, and the UTILITY type.
        Call after addToDesktop() and before the window is shown -- a window manager reads both
        when the window is mapped. */
    void markAsToolWindowFor (juce::Component& window);

    /** Forget the cached window ancestry; call when (re)opening something that uses it. */
    void reset()    { ancestors.clear(); }

private:
    juce::Component& component;
    std::vector<juce::pointer_sized_uint> ancestors;
};
