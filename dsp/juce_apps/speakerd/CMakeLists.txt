# speakerd: the headless plugin host that runs on each speaker. It runs a graph of headless
# plugin builds (JUCE's AudioProcessorGraph, LV2 hosting) inside the PipeWire cycle as one
# pw_filter node, loads its graph from the state layers at boot, and never touches the network.
# See README.md, and docs/remote-plugin-host.md at the repository root.
cmake_minimum_required(VERSION 3.22)

project(SPEAKERD VERSION 0.1.0)

find_package(JUCE CONFIG REQUIRED)
find_package(PkgConfig REQUIRED)
# An imported target: its include directories are system includes, so the SPA inline headers
# do not trip the warning flags below.
pkg_check_modules(PIPEWIRE REQUIRED IMPORTED_TARGET libpipewire-0.3)

juce_add_console_app(speakerd PRODUCT_NAME "speakerd")

target_sources(speakerd
    PRIVATE
        Main.cpp
        DeviceConfig.cpp
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
