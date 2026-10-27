// JUCE's sanctioned route to its own X11 connection (see HostWindowTracker.h). It must be
// defined before the first JUCE header in this translation unit, and only here. Defined
// unconditionally on purpose: JUCE_LINUX is not known yet at this point (JUCE's own headers
// define it), and juce_gui_basics.h only reads this macro inside its Linux/BSD guard anyway.
#define JUCE_GUI_BASICS_INCLUDE_XHEADERS 1

#include "HostWindowTracker.h"

#include <algorithm>

HostWindowTracker::HostWindowTracker (juce::Component& c)
    : component (c)
{
}

#if JUCE_LINUX || JUCE_BSD
namespace
{
    ::Display* juceDisplay()
    {
        auto* windowSystem = juce::XWindowSystem::getInstanceWithoutCreating();
        return windowSystem != nullptr ? windowSystem->getDisplay() : nullptr;
    }

    ::Window nativeWindowOf (juce::ComponentPeer& peer)
    {
        return (::Window) reinterpret_cast<juce::pointer_sized_uint> (peer.getNativeHandle());
    }
}
#endif

juce::Point<int> HostWindowTracker::screenPositionError() const
{
   #if JUCE_LINUX || JUCE_BSD
    auto* peer = component.getPeer();
    auto* display = juceDisplay();

    if (peer == nullptr || display == nullptr)
        return {};

    int x = 0, y = 0;
    ::Window child = 0;

    {
        juce::XWindowSystemUtilities::ScopedXLock lock;
        auto* x11 = juce::X11Symbols::getInstance();

        if (! x11->xTranslateCoordinates (display, nativeWindowOf (*peer), x11->xDefaultRootWindow (display),
                                          0, 0, &x, &y, &child))
            return {};
    }

    // Physical pixels -> the peer's logical units -> the top-level component's desktop units.
    const auto actual   = juce::Point<float> ((float) x, (float) y) / (float) peer->getPlatformScaleFactor();
    const auto believed = peer->localToGlobal (juce::Point<float>());

    return ((actual - believed) / peer->getComponent().getDesktopScaleFactor()).roundToInt();
   #else
    return {};
   #endif
}

void HostWindowTracker::takeKeyboardFocus ([[maybe_unused]] juce::Component& window)
{
   #if JUCE_LINUX || JUCE_BSD
    auto* peer = window.getPeer();
    auto* display = juceDisplay();

    if (peer == nullptr || display == nullptr)
        return;

    juce::XWindowSystemUtilities::ScopedXLock lock;
    juce::X11Symbols::getInstance()->xSetInputFocus (display, nativeWindowOf (*peer),
                                                     RevertToParent, CurrentTime);
   #endif
}

void HostWindowTracker::markAsToolWindowFor ([[maybe_unused]] juce::Component& window)
{
   #if JUCE_LINUX || JUCE_BSD
    auto* peer = window.getPeer();
    auto* display = juceDisplay();

    if (peer == nullptr || display == nullptr)
        return;

    hostWindowIsActive();       // fills the ancestry if it is not cached yet

    if (ancestors.empty())
        return;

    juce::XWindowSystemUtilities::ScopedXLock lock;
    auto* x11 = juce::X11Symbols::getInstance();
    const auto target = nativeWindowOf (*peer);
    auto hostTopLevel = (::Window) ancestors.back();

    x11->xChangeProperty (display, target, XA_WM_TRANSIENT_FOR, XA_WINDOW, 32, PropModeReplace,
                          reinterpret_cast<unsigned char*> (&hostTopLevel), 1);

    const auto typeProperty = x11->xInternAtom (display, "_NET_WM_WINDOW_TYPE", False);
    auto utility = x11->xInternAtom (display, "_NET_WM_WINDOW_TYPE_UTILITY", False);

    if (typeProperty != None && utility != None)
        x11->xChangeProperty (display, target, typeProperty, XA_ATOM, 32, PropModeReplace,
                              reinterpret_cast<unsigned char*> (&utility), 1);
   #endif
}

bool HostWindowTracker::hostWindowIsActive ([[maybe_unused]] juce::Component* alsoOurs)
{
   #if JUCE_LINUX || JUCE_BSD
    auto* peer = component.getPeer();
    auto* display = juceDisplay();

    if (peer == nullptr || display == nullptr)
        return true;

    juce::XWindowSystemUtilities::ScopedXLock lock;
    auto* x11 = juce::X11Symbols::getInstance();
    const auto root = x11->xDefaultRootWindow (display);

    // The chain from the embedded window up to the root does not change while the panel is
    // open, so walk it once rather than on every check.
    if (ancestors.empty())
    {
        auto window = nativeWindowOf (*peer);

        for (int depth = 0; depth < 32 && window != 0 && window != root; ++depth)
        {
            ancestors.push_back ((juce::pointer_sized_uint) window);

            ::Window rootReturn = 0, parent = 0;
            ::Window* children = nullptr;
            unsigned int numChildren = 0;

            if (x11->xQueryTree (display, window, &rootReturn, &parent, &children, &numChildren) == 0)
                break;

            if (children != nullptr)
                x11->xFree (children);

            window = parent;
        }
    }

    const auto activeAtom = juce::XWindowSystemUtilities::Atoms::getIfExists (display, "_NET_ACTIVE_WINDOW");

    if (activeAtom == None)
        return true;

    juce::XWindowSystemUtilities::GetXProperty property (display, root, activeAtom, 0, 1, false, XA_WINDOW);

    if (! property.success || property.data == nullptr || property.numItems == 0)
        return true;

    // Format-32 properties come back as C longs, whatever the platform's word size.
    const auto active = (juce::pointer_sized_uint) *reinterpret_cast<const unsigned long*> (property.data);

    // A floating panel of ours can itself be the active window once it is a real, focusable
    // window -- that is still "us".
    if (alsoOurs != nullptr)
        if (auto* ownPeer = alsoOurs->getPeer())
            if (active == (juce::pointer_sized_uint) nativeWindowOf (*ownPeer))
                return true;

    // No active window is not "someone else is in front": focus can land nowhere, e.g. on an
    // unmanaged window such as the panel itself. Only hide for a real, different window.
    if (active == 0)
        return true;

    return std::find (ancestors.begin(), ancestors.end(), active) != ancestors.end();
   #else
    return true;
   #endif
}
