# Plugin editor windows on Linux/X11

Editor-window behaviour found while developing the `loudness` and `ampScope` editors in the
JUCE AudioPluginHost (the `-debug-linux` flow; setting that up is in
[`../hosting/audiopluginhost-linux.md`](../hosting/audiopluginhost-linux.md)). All of this was
verified against JUCE 8.0.12 sources and measured on a GNOME/X11 desktop at 2x scale.

## 1. A fixed-size plugin window can still be maximised — and then the editor stretches

Symptom: a plugin whose editor is deliberately small (the loudness "Morph Pad", 620x474)
shows up **full screen**, its layout smeared across the whole display. Two independent
faults stack up, and both have to be fixed.

**Measured**, with `xwininfo`/`xprop` on the plugin window at 2x desktop scale:

    as opened              1244x1004 +640+504   WM_NORMAL_HINTS: no min/max at all
    after a maximise       3840x2096 +0+64      _NET_WM_STATE_MAXIMIZED_{HORZ,VERT}
    plugin editor surface  1240x948  ->  3836x2040   (stretched with it)

3840x2096+0+64 is exactly GNOME's maximised geometry under a 64 px top bar. Super+Up, a
drag to the top edge or a title double-click all produce it.

**1. JUCE drops the fixed-size hints (host side).** `XWindowSystem::setBounds()` calls
`updateConstraints()`, which correctly sets `PMinSize|PMaxSize` (min = max) for a
non-resizable window — and then *immediately* calls `XSetWMNormalHints` again with only
`USSize|USPosition`. That call replaces the whole property, so the limits never survive
and the WM treats **every** fixed-size JUCE window as resizable. The LV2 side is not the
problem: the plugin's `ui.ttl` declares `ui:noUserResize`, and JUCE's LV2 host honours it
(`isResizable()` in `juce_LV2PluginFormat.cpp` -> `setResizable (false, ...)`); the hint is
lost one layer further down, in X11.

Fix: `juce_plugins/patches/juce-x11-keep-fixed-size-hints.patch` carries min/max into that
second write. `tools/build-podman.sh -setup-host` applies every patch in `juce_plugins/patches/`
to a `git archive` export of the pinned commit (`pluginHost/juce-src/`) and builds the host
from that, so **the submodule is never modified**. The export happens on the host, not in
the container: the submodule's `.git` file points outside the `/work` bind mount, so git
cannot read it from inside. A fingerprint of commit + patches in
`pluginHost/.juce-source-fingerprint` makes `-setup-host` rebuild a host that predates a
patch, and makes `-debug-linux` warn about one. The patch covers the non-resizable case
only; a *resizable* window with a constrainer loses its limits the same way.

**2. An editor with no constrainer follows any size it is handed (plugin side).** When the
host resizes the UI, JUCE's LV2 client does `editor->setBoundsConstrained (whole area)`
(`juce_audio_plugin_client_LV2.cpp`, `resized()`). Without a constrainer that is a plain
`setBounds`, so a fixed layout gets stretched. Give a fixed-size editor a real one:

```cpp
setResizeLimits (w, h, w, h);   // min == max: also keeps resizableByHost false,
setSize (w, h);                 // so the generated ui.ttl still says ui:noUserResize
```

Either fix alone is not enough: without (1) the window is still maximised, just black
around a correctly-sized editor; without (2) any host that does resize the UI stretches it.

**Verified after both fixes**, same maximise request: the window now publishes
`program specified minimum/maximum size: 1244 by 1004`, GNOME refuses the request, and it
stays 1244x1004 with the editor surface at 1240x948 — no `MAXIMIZED` state.

**Reproducing it** needs no mouse: send `_NET_WM_STATE` add `MAXIMIZED_VERT|HORZ` to the
window (`XSendEvent` to the root with `SubstructureRedirectMask|SubstructureNotifyMask`),
then read `xwininfo -tree` and `xprop WM_NORMAL_HINTS _NET_WM_STATE`.

Two traps met while measuring this:
- AudioPluginHost allows **multiple instances** (`moreThanOneInstanceAllowed()` is true), so
  `pkill -x AudioPluginHost` also kills a host the user has open. Kill the PID you started.
- `xwininfo -id ""` (an empty id, e.g. the window lookup failed) does not error — it waits
  for you to **click** a window, and hangs a script.

## 2. A floating window from an embedded plugin UI (X11)

The loudness plugin's settings panel is its own window, floating just below the plugin card
so it never covers the plot. Four things had to be solved, each verified in the real host
with `xwininfo` (driving it with synthetic X events, see the end).

**Which kind of window: it depends on whether you need to type into it.**

`addToDesktop (ComponentPeer::windowIsTemporary)` sets `override_redirect`
(`juce_XWindowSystem_linux.cpp`), the mechanism JUCE's popup menus use: the WM neither
decorates, moves nor maximises it, so it lands exactly where you put it. That is ideal —
until the panel needs a text field. **An override-redirect window cannot hold keyboard
focus here.** Measured: the plugin's `XSetInputFocus` on it succeeds (a bare Xlib test
confirms the server accepts it), and JUCE reports `hasKeyboardFocus() == true`, yet within
the same second the compositor has handed focus back to the host window, so every keystroke
goes to the host. Symptom: the editor opens, typing does nothing, and Return still commits —
the keysym path survives while `XmbLookupString` (which needs a focused input context)
produces no characters.

So a panel that takes typed input must be a **real, managed window**. Two JUCE details make
that work:
- Pass `ComponentPeer::windowHasDropShadow`. It reads like cosmetics, but `setWindowType()`
  types a window `_NET_WM_WINDOW_TYPE_COMBO` when it has no drop-shadow flag and
  `Desktop::canUseSemiTransparentWindows()` — a menu-like type the WM will not focus.
  With the flag it becomes `_NET_WM_WINDOW_TYPE_NORMAL`. Leave out `windowHasTitleBar` and
  it is still undecorated (verified: the window stays a direct child of root, unreparented).
- JUCE will not set the focus for you. `ComponentPeer::grabFocus()` is a no-op when the peer
  already believes it is focused — which it did here — so `XSetInputFocus` was never called.
  Do it explicitly (`HostWindowTracker::takeKeyboardFocus`).

Make it behave as a tool window rather than a second application window by setting
`WM_TRANSIENT_FOR` to the host's top-level and `_NET_WM_WINDOW_TYPE_UTILITY` — both **after**
`addToDesktop` and **before** it is shown, since the WM reads them when the window is mapped.
And remember the panel can then be the active window itself, so whatever decides "is one of
our windows active" has to count it as ours.

Rounded corners need an ARGB visual, which `addToDesktop` derives from `isOpaque()` — just
leave the component non-opaque.

**Match the editor's scale, or it comes up at half size.** In an LV2 host the desktop
scale reaches the plugin as `ui:scaleFactor` -> `AudioProcessorEditor::setScaleFactor`,
i.e. a *transform on the editor*; the plugin's own new windows are at 1x. Measured at 2x:
panel 282x263 physical next to a 1240x948 editor. Do what `PopupMenu` does for a scaled
target: take `Component::getApproximateScaleFactorForComponent (editor)`, return it from
the floating component's `getDesktopScaleFactor()` override (set it *before*
`addToDesktop`), and divide the screen coordinates you place it at by it. After: 564x526.

**JUCE's screen position for an embedded UI goes stale when the host window moves.** The
plugin UI is a reparented child window. `XWindowSystem::getWindowBounds()` refreshes the
cached `parentScreenPosition` only on a `ConfigureNotify`/`GravityNotify` for the *embedded*
window, and dragging the host window sends it neither: the child does not move relative to
its parent. So `getScreenBounds()` stays wrong until something unrelated (a click that
restacks the window) happens to refresh it, and the panel is left behind. There is no public
live query. Ask the X server: `XTranslateCoordinates (embeddedWindow -> root)`, compare with
the peer's `localToGlobal ({0,0})`, and correct by the difference (`HostWindowTracker`).

To reach JUCE's own X connection, define `JUCE_GUI_BASICS_INCLUDE_XHEADERS 1` before the
first JUCE include of one `.cpp`: the opt-in JUCE's LV2/VST wrappers and `juce_opengl` use.
It exposes `XWindowSystem::getDisplay()`, `X11Symbols` (libX11 stays **dlopen'd**, so no
link-time libX11 dependency, which matters on a headless box) and
`XWindowSystemUtilities::{ScopedXLock, GetXProperty, Atoms}`. Two traps:
- Define it **unconditionally**. `#if JUCE_LINUX` around it is silently false — `JUCE_LINUX`
  is only defined by the JUCE headers you are about to include — and you get "'Display' in
  namespace '::' does not name a type". `juce_gui_basics.h` reads the macro only inside its
  own Linux/BSD guard, so it is harmless elsewhere.
- `GetXProperty` does not take the display lock; hold a `ScopedXLock` around it.

**Nothing stacks a floating panel for you** — left alone it can sit over other apps. The
panel hides while `_NET_ACTIVE_WINDOW` (root) is a window outside the plugin's own window
ancestry (walked once with `XQueryTree`) and is not the panel itself. Treat an active window of 0 as "unknown", not
"someone else": focus can land on no managed window at all. And only enable hiding if the
host window reads as active when the panel opens, so a desktop where this cannot be read
never hides it by mistake.

**Driving this headlessly.** JUCE handles `XSendEvent` button events like real ones, so a
tiny Xlib tool can click a chip (press + release, `event_mask 0` so it reaches the window's
own client), `XMoveWindow` on the host window stands in for dragging it, and synthetic
`KeyPress`/`KeyRelease` really do type into a focused JUCE `TextEditor` — that is how the
text-entry fix above was verified end to end. Two things do **not** work from a script on
GNOME: synthetic *motion* does not drive a drag (JUCE grabs the pointer on mouse-down and
follows the real one), and the active window cannot be changed — mutter's focus-stealing
prevention ignores `_NET_ACTIVE_WINDOW` requests, and even a freshly opened `xmessage` does
not get focus. So hiding-when-inactive can only be checked by hand.

## 3. Repainting a plugin editor is expensive in a Debug build at HiDPI

A Debug build at 4K with a 2x scale paints into 1048x640 physical pixels with an unoptimised
rasteriser. The loudness pad measured ~20 ms for a full repaint, and every parameter edit
repaints -- so dragging a control at pointer rate asks for more than the frame budget and the
UI stutters. Release, and the Pi at 1x, are far cheaper, but the Debug build is what
`-debug-linux` runs, i.e. what you develop against.

**Measure paired, inside one run.** Absolute timings on this machine swing by 2.5x with CPU
load: the same code measured 5.1, 9.2 and 12.8 ms a frame across three consecutive runs. Any
"it gone faster" conclusion from separate runs is noise. Sample both paths alternately in one
run (here: force the expensive path every 30th frame and attribute each paint to the path it
took) and compare those.

**Split the drawing into what changes and what does not,** and cache the rest in an image:
background, grid, frame and the settings that are not being edited. Measured 2.1-2.2x off the
per-edit repaint. Two traps, both measured, both counter-intuitive:
- Draw the cache with the context's scale cancelled -- `addTransform (scale (1 / scale))`, then
  `drawImageAt` -- so the net transform is translation-only. JUCE's software renderer blits
  that; anything else resamples the image, which cost ~6 ms a frame here.
- Keep the cache `Image::RGB`. `Image::ARGB` matches the window's format but is alpha-blended
  per pixel (10-14 ms vs 5 ms); an opaque source takes the copy path.

Grid lines are axis-aligned, so `fillRect` on integer coordinates beats `drawLine`: no
antialiasing to compute for the most line-heavy part of the plot.

**Where the time was NOT.** Probing first stopped two plausible-sounding theories: the X round
trips added for window tracking (section 2) cost 0.04-0.07 ms each, and the 60 Hz editor timer
0.1-0.2 ms a tick. Both are noise next to one repaint.

(Driving a UI with synthetic X events, and what does not work that way: see the end of §2.)
