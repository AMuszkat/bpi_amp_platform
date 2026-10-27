# Bench scope for the MAX98397 amplifier's own voltage/current/supply measurements.
# Shared plugin setup (identity, formats, flags, LTO) lives in ../cmake/SpeakerPlugin.cmake;
# build with ../tools/build-podman.sh ampScope  (see ../README.md).
cmake_minimum_required(VERSION 3.22)

project(AMPSCOPE_PLUGIN VERSION 0.0.1)

find_package(JUCE CONFIG REQUIRED)
include(${CMAKE_CURRENT_LIST_DIR}/../cmake/SpeakerPlugin.cmake)

speaker_add_plugin(ampScope
    CODE    Ascp
    INPUTS  1
    OUTPUTS 1
    SOURCES
        PluginProcessor.cpp
        PluginEditor.cpp
        ScopeView.cpp
        AmpTelemetry.cpp
)

# The amplifier's measurements only exist behind ALSA -- the measurement ADC readbacks are
# mixer controls and the voltage/current sense arrives as a TDM capture stream, neither of
# which JUCE's device layer can reach. JUCE already pulls in libasound on Linux; this states
# the dependency explicitly because AmpTelemetry.cpp calls snd_ctl_* / snd_pcm_* directly.
if(UNIX AND NOT APPLE)
    find_library(ALSA_LIBRARY NAMES asound REQUIRED)
    target_link_libraries(ampScope PRIVATE ${ALSA_LIBRARY})
endif()
