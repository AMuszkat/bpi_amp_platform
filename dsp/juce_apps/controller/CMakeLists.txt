# The controller: JUCE's AudioPluginHost with the audio removed. It edits the speakers' graph
# and turns their knobs through the hub (docs/protocol.md), and processes no audio: the graph
# is never prepared or run, and no audio device type is compiled in. See README.md, and
# docs/remote-plugin-host.md at the repository root.
cmake_minimum_required(VERSION 3.22)

project(BPI_CONTROLLER VERSION 0.1.0)

find_package(JUCE CONFIG REQUIRED)
include(${CMAKE_CURRENT_LIST_DIR}/ControlPlugins.cmake)

juce_add_gui_app(controller
    PRODUCT_NAME "controller"
    COMPANY_NAME "BPI AMP Platform"
    BUNDLE_ID    org.bpi-amp.controller)

target_sources(controller
    PRIVATE
        Source/Main.cpp
        Source/Bridge/Auth.cpp
        Source/Bridge/Bridge.cpp
        Source/Bridge/GraphDocument.cpp
        Source/Bridge/JsonLineLink.cpp
        Source/Bridge/ScriptRunner.cpp
        Source/Plugins/ControlPlugins.cpp
        Source/Plugins/PluginGraph.cpp
        Source/UI/GraphEditorPanel.cpp
        Source/UI/MainHostWindow.cpp
        Source/UI/SpeakersPanel.cpp)

target_compile_definitions(controller
    PRIVATE
        # No audio: no device type exists in this binary, so none can be opened.
        JUCE_ALSA=0
        JUCE_JACK=0
        JUCE_BELA=0
        # No plugin is loaded from disk: the plugins are the control builds compiled in.
        JUCE_PLUGINHOST_LV2=0
        JUCE_PLUGINHOST_VST3=0
        JUCE_PLUGINHOST_VST=0
        JUCE_PLUGINHOST_LADSPA=0
        JUCE_PLUGINHOST_AU=0
        JUCE_WEB_BROWSER=0
        JUCE_USE_CURL=0
        JUCE_DISABLE_CAUTIOUS_PARAMETER_ID_CHECKING=1
        JUCE_APPLICATION_NAME_STRING="$<TARGET_PROPERTY:controller,JUCE_PRODUCT_NAME>"
        JUCE_APPLICATION_VERSION_STRING="$<TARGET_PROPERTY:controller,JUCE_VERSION>"
        CONTROLLER_VERSION="${PROJECT_VERSION}")

target_link_libraries(controller
    PRIVATE
        juce::juce_audio_processors
        juce::juce_cryptography
        juce::juce_gui_extra
        juce::juce_osc
    PUBLIC
        juce::juce_recommended_config_flags
        juce::juce_recommended_warning_flags)

# The control builds compiled in, one name per plugin of dsp/juce_plugins. Adding a plugin is
# adding its name here (README.md, "Adding a plugin").
controller_add_control_plugins(controller
    loudness)
