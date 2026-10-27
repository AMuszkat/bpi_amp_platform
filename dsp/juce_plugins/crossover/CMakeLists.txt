# Linear-phase FIR crossover: mono in, low band + high band out.
# Shared plugin setup (identity, formats, flags, LTO) lives in ../cmake/SpeakerPlugin.cmake;
# build with ../tools/build-podman.sh crossover  (see ../README.md).
cmake_minimum_required(VERSION 3.22)

project(CROSSOVER_PLUGIN VERSION 0.0.1)

find_package(JUCE CONFIG REQUIRED)
include(${CMAKE_CURRENT_LIST_DIR}/../cmake/SpeakerPlugin.cmake)

speaker_add_plugin(crossover
    CODE    Cros
    INPUTS  1
    OUTPUTS 2
    SOURCES
        PluginProcessor.cpp
        PluginEditor.cpp
        PlotComponent.cpp
    MODULES
        juce_dsp
)
