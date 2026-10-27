# plugin-check: loads the complete, control and headless builds of one plugin and checks that
# they fit together the way the remote plugin host needs (see README.md).
cmake_minimum_required(VERSION 3.22)

project(PLUGIN_CHECK VERSION 0.0.1)

find_package(JUCE CONFIG REQUIRED)

juce_add_console_app(plugin-check PRODUCT_NAME "plugin-check")

target_sources(plugin-check PRIVATE Main.cpp)

target_compile_definitions(plugin-check
    PRIVATE
        JUCE_PLUGINHOST_LV2=1
        JUCE_PLUGINHOST_VST3=1
        JUCE_MODAL_LOOPS_PERMITTED=1          # the editor check runs its own dispatch loop
        JUCE_WEB_BROWSER=0
        JUCE_USE_CURL=0)

target_link_libraries(plugin-check
    PRIVATE
        juce::juce_audio_processors
        juce::juce_audio_utils
        juce::juce_gui_basics
    PUBLIC
        juce::juce_recommended_config_flags
        juce::juce_recommended_warning_flags)
