# Magnitude-correction filter chain (pre-LP FIR -> pre-HP biquads -> inverse FIR).
# Shared plugin setup (identity, formats, flags, LTO) lives in ../cmake/SpeakerPlugin.cmake;
# build with ../tools/build-podman.sh firConv  (see ../README.md).
# Also builds firConv_control (editor, no DSP) and firConv_headless (DSP, no editor) for the
# remote plugin host; see TARGETS in ../cmake/SpeakerPlugin.cmake.
cmake_minimum_required(VERSION 3.22)

project(FIR_CONV_PLUGIN VERSION 0.0.5)

find_package(JUCE CONFIG REQUIRED)
include(${CMAKE_CURRENT_LIST_DIR}/../cmake/SpeakerPlugin.cmake)

speaker_add_plugin(firConv
    CODE    Fcnv
    INPUTS  1
    OUTPUTS 1
    SOURCES
        PluginProcessor.cpp
    DSP
        FirConvDsp.cpp
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
