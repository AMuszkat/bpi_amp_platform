# Level-dependent loudness contour built from two low-shelf EQs per setting.
# Shared plugin setup (identity, formats, flags, LTO) lives in ../cmake/SpeakerPlugin.cmake;
# build with ../tools/build-podman.sh loudness  (see ../README.md).
# Also builds loudness_control (editor, no DSP) and loudness_headless (DSP, no editor) for the
# remote plugin host; see TARGETS in ../cmake/SpeakerPlugin.cmake.
cmake_minimum_required(VERSION 3.22)

project(LOUDNESS_PLUGIN VERSION 0.0.1)

find_package(JUCE CONFIG REQUIRED)
include(${CMAKE_CURRENT_LIST_DIR}/../cmake/SpeakerPlugin.cmake)

speaker_add_plugin(loudness
    CODE    Loud
    INPUTS  1
    OUTPUTS 1
    SOURCES
        PluginProcessor.cpp
    DSP
        LoudnessDsp.cpp
    EDITOR
        PluginEditor.cpp
        MorphPad.cpp
        HostWindowTracker.cpp
    MODULES
        juce_dsp
    TARGETS complete control headless
)
