# speakerd: the headless plugin host that runs on each speaker. It runs a graph of headless
# plugin builds (JUCE's AudioProcessorGraph, LV2 hosting) inside the PipeWire cycle as one
# pw_filter node, loads its graph from the state layers at boot, and never touches the network.
# See README.md, and docs/remote-plugin-host.md at the repository root.
cmake_minimum_required(VERSION 3.22)

project(SPEAKERD VERSION 0.1.0)

# A cross build (tools/build.sh speakerd aarch64) links the target's libpipewire, never this
# machine's. Unless the caller says where the target's pkg-config files are, they are in its
# multiarch directory: Debian's layout, where libpipewire-0.3-dev:arm64 installs them (under
# CMAKE_SYSROOT if there is one).
if(CMAKE_CROSSCOMPILING AND CMAKE_LIBRARY_ARCHITECTURE AND NOT DEFINED ENV{PKG_CONFIG_LIBDIR})
    set(ENV{PKG_CONFIG_LIBDIR} "${CMAKE_SYSROOT}/usr/lib/${CMAKE_LIBRARY_ARCHITECTURE}/pkgconfig:${CMAKE_SYSROOT}/usr/share/pkgconfig")
    if(CMAKE_SYSROOT)
        set(ENV{PKG_CONFIG_SYSROOT_DIR} "${CMAKE_SYSROOT}")
    endif()
endif()

find_package(JUCE CONFIG REQUIRED)
find_package(PkgConfig REQUIRED)
# An imported target: its include directories are system includes, so the SPA inline headers
# do not trip the warning flags below.
pkg_check_modules(PIPEWIRE REQUIRED IMPORTED_TARGET libpipewire-0.3)

if(CMAKE_CROSSCOMPILING AND CMAKE_LIBRARY_ARCHITECTURE
   AND NOT PIPEWIRE_LINK_LIBRARIES MATCHES "/${CMAKE_LIBRARY_ARCHITECTURE}/")
    message(FATAL_ERROR "libpipewire-0.3 for ${CMAKE_LIBRARY_ARCHITECTURE} not found (found: ${PIPEWIRE_LINK_LIBRARIES}): "
                        "install libpipewire-0.3-dev:arm64, or set PKG_CONFIG_LIBDIR to its pkg-config directory")
endif()

juce_add_console_app(speakerd PRODUCT_NAME "speakerd")

target_sources(speakerd
    PRIVATE
        Main.cpp
        DeviceConfig.cpp
        FilterGraph.cpp
        GraphDocument.cpp
        GraphBuilder.cpp
        LV2State.cpp
        Engine.cpp
        PipeWireNode.cpp
        Render.cpp
        StateLayers.cpp)

target_compile_definitions(speakerd
    PRIVATE
        JUCE_PLUGINHOST_LV2=1
        JUCE_PLUGINHOST_VST3=0
        JUCE_USE_CURL=0
        JUCE_WEB_BROWSER=0
        JUCE_USE_FLAC=0                    # the render mode reads and writes WAV only
        JUCE_USE_OGGVORBIS=0
        SPEAKERD_VERSION="${PROJECT_VERSION}")

# No GUI module: nothing here links X11, so speakerd runs on a speaker with no display.
target_link_libraries(speakerd
    PRIVATE
        juce::juce_audio_processors_headless
        juce::juce_audio_formats
        PkgConfig::PIPEWIRE
    PUBLIC
        juce::juce_recommended_config_flags
        juce::juce_recommended_warning_flags)

# The generator of examples/loudness.filtergraph (examples/make-loudness-filtergraph.sh runs
# it): an AudioPluginHost session made the way AudioPluginHost makes one. Not part of speakerd,
# and built only on request:  cmake --build build/speakerd --target speakerd-make-filtergraph
juce_add_console_app(speakerd-make-filtergraph PRODUCT_NAME "speakerd-make-filtergraph")
set_target_properties(speakerd-make-filtergraph PROPERTIES EXCLUDE_FROM_ALL TRUE)

target_sources(speakerd-make-filtergraph
    PRIVATE
        examples/MakeFiltergraph.cpp
        GraphBuilder.cpp
        GraphDocument.cpp
        LV2State.cpp)

target_compile_definitions(speakerd-make-filtergraph
    PRIVATE
        JUCE_PLUGINHOST_LV2=1
        JUCE_PLUGINHOST_VST3=0
        JUCE_USE_CURL=0
        JUCE_WEB_BROWSER=0)

target_link_libraries(speakerd-make-filtergraph
    PRIVATE
        juce::juce_audio_processors_headless
    PUBLIC
        juce::juce_recommended_config_flags
        juce::juce_recommended_warning_flags)
