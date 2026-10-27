# Magnitude-correction filter chain (pre-LP FIR -> pre-HP biquads -> inverse FIR).
# Shared plugin setup (identity, formats, flags, LTO) lives in ../cmake/SpeakerPlugin.cmake;
# build with ../tools/build-podman.sh firConv  (see ../README.md).
cmake_minimum_required(VERSION 3.22)

project(FIR_CONV_PLUGIN VERSION 0.0.1)

find_package(JUCE CONFIG REQUIRED)
include(${CMAKE_CURRENT_LIST_DIR}/../cmake/SpeakerPlugin.cmake)

speaker_add_plugin(firConv
    CODE    Fcnv
    INPUTS  1
    OUTPUTS 1
    SOURCES
        PluginProcessor.cpp
        PluginEditor.cpp
        PlotComponent.cpp
    MODULES
        juce_dsp
)
