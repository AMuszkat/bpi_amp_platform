# Linear-phase FIR crossover: mono in, low band + high band out.
# Shared plugin setup (identity, formats, flags, LTO) lives in ../cmake/SpeakerPlugin.cmake;
# build with ../tools/build-podman.sh crossover  (see ../README.md).
# Also builds crossover_control (editor, no DSP) and crossover_headless (DSP, no editor) for the
# remote plugin host; see TARGETS in ../cmake/SpeakerPlugin.cmake.
cmake_minimum_required(VERSION 3.22)

project(CROSSOVER_PLUGIN VERSION 0.0.4)

find_package(JUCE CONFIG REQUIRED)
include(${CMAKE_CURRENT_LIST_DIR}/../cmake/SpeakerPlugin.cmake)

speaker_add_plugin(crossover
    CODE    Cros
    INPUTS  1
    OUTPUTS 2
    SOURCES
        PluginProcessor.cpp
    DSP
        CrossoverDsp.cpp
    EDITOR
        PluginEditor.cpp
        PlotComponent.cpp
    MODULES
        juce_dsp
    SHARED
        blockfir
        bypass
    TARGETS complete control headless
)
