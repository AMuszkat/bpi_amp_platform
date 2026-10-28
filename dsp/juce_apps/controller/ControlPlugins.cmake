# How the controller compiles in the control builds of the plugins (README.md, "Adding a
# plugin"). Included by CMakeLists.txt, which lists the plugins in one call:
#
#     controller_add_control_plugins(controller loudness)
#
# Each plugin's own CMakeLists.txt is added (EXCLUDE_FROM_ALL, so none of its LV2 or VST3
# bundles is built), and its control target, `<plugin>_control`, the shared-code target
# juce_add_plugin() makes, is read as the description of the build: its sources (SOURCES +
# EDITOR of speaker_add_plugin, so no DSP), its plugin definitions (JucePlugin_Name,
# SPEAKER_CONTROL=1, ...) and the JUCE modules it links.
#
# The sources are then compiled into the controller, with the controller's own JUCE
# configuration. The archive itself is not linked: it carries a second copy of every JUCE
# module, compiled as a plugin (JUCE_STANDALONE_APPLICATION=0), its public definitions would
# redefine the application's, and every plugin defines createPluginFilter(). Here each
# plugin's createPluginFilter is renamed bpiControl_create_<plugin> by a definition on its own
# sources, so any number of plugins link side by side.
#
# What it writes for the C++ side: generated/ControlPluginList.h, one entry per plugin with its
# factory and the LV2 URIs of its builds (Source/Plugins/ControlPlugins.cpp reads it).

include_guard(GLOBAL)

set(CONTROLLER_PLUGINS_DIR "${CMAKE_CURRENT_LIST_DIR}/../../juce_plugins")

# Each plugin's CMakeLists.txt starts with find_package(JUCE CONFIG REQUIRED). Run a second
# time in one build, JUCE 8.0.12's JUCEConfig.cmake fails ("Some targets in this export set
# were already defined"): its check of the targets it expects appends the whole module list
# (`juce::${_juce_modules}`) where it means one module, so it never sees that they are all
# there already. So once JUCE is found, a later find_package(JUCE) is a no-op here.
macro(find_package)
    if("${ARGV0}" STREQUAL "JUCE" AND TARGET juce::juce_core)
        set(JUCE_FOUND TRUE)
    else()
        _find_package(${ARGV})
    endif()
endmacro()

function(controller_add_control_plugins app)
    set(entries "")
    set(declarations "")

    foreach(plugin IN LISTS ARGN)
        if(NOT EXISTS "${CONTROLLER_PLUGINS_DIR}/${plugin}/CMakeLists.txt")
            message(FATAL_ERROR "controller: no plugin '${plugin}' in ${CONTROLLER_PLUGINS_DIR}")
        endif()

        if(NOT TARGET ${plugin}_control)
            add_subdirectory("${CONTROLLER_PLUGINS_DIR}/${plugin}" "${CMAKE_BINARY_DIR}/plugins/${plugin}" EXCLUDE_FROM_ALL)
        endif()

        foreach(kind IN ITEMS control headless)
            if(NOT TARGET ${plugin}_${kind})
                message(FATAL_ERROR "controller: plugin '${plugin}' has no ${kind} target: "
                                    "its speaker_add_plugin() needs TARGETS complete control headless")
            endif()
        endforeach()

        get_target_property(sourceDir ${plugin}_control SOURCE_DIR)
        get_target_property(sources ${plugin}_control SOURCES)
        get_target_property(interfaceDefs ${plugin}_control INTERFACE_COMPILE_DEFINITIONS)
        get_target_property(links ${plugin}_control LINK_LIBRARIES)

        # The plugin's own sources only (JUCE adds none of its own to a shared-code target
        # today; this keeps it that way if it ever does).
        set(pluginSources "")
        foreach(source IN LISTS sources)
            if(NOT IS_ABSOLUTE "${source}")
                set(source "${sourceDir}/${source}")
            endif()
            cmake_path(IS_PREFIX sourceDir "${source}" NORMALIZE isOwn)
            if(isOwn)
                list(APPEND pluginSources "${source}")
            endif()
        endforeach()

        # The plugin's identity and build kind; the JUCE configuration is the controller's.
        set(pluginDefs "")
        foreach(def IN LISTS interfaceDefs)
            if(NOT def MATCHES "^(JUCE_STANDALONE_APPLICATION|JUCE_MODULE_AVAILABLE_|JUCE_WEB_BROWSER|JUCE_USE_CURL|JUCE_VST3_CAN_REPLACE_VST2)")
                list(APPEND pluginDefs "${def}")
            endif()
        endforeach()

        # The JUCE modules the plugin's code uses, linked into the controller once. Not the
        # audio devices (juce_audio_utils brings them): the controller has no audio device, and
        # an editor that needs one does not belong in a control build.
        set(modules "")
        foreach(lib IN LISTS links)
            if(lib MATCHES "^juce::juce_" AND NOT lib MATCHES "^juce::juce_(audio_plugin_client|recommended_|audio_utils$|audio_devices$)")
                list(APPEND modules "${lib}")
            endif()
        endforeach()

        set(objects controller_control_${plugin})
        add_library(${objects} OBJECT ${pluginSources})
        target_compile_definitions(${objects} PRIVATE
            $<TARGET_PROPERTY:${app},COMPILE_DEFINITIONS>
            ${pluginDefs}
            createPluginFilter=bpiControl_create_${plugin})
        target_include_directories(${objects} PRIVATE
            "${sourceDir}"
            $<TARGET_PROPERTY:${app},INCLUDE_DIRECTORIES>)
        target_compile_options(${objects} PRIVATE $<TARGET_PROPERTY:${app},COMPILE_OPTIONS>)
        set_target_properties(${objects} PROPERTIES CXX_STANDARD 17 CXX_STANDARD_REQUIRED ON)
        target_link_libraries(${app} PRIVATE ${objects} ${modules})

        get_target_property(headlessUri ${plugin}_headless JUCE_LV2URI)
        get_target_property(controlUri  ${plugin}_control  JUCE_LV2URI)
        get_target_property(completeUri ${plugin}          JUCE_LV2URI)

        string(APPEND declarations "juce::AudioProcessor* JUCE_CALLTYPE bpiControl_create_${plugin}();\n")
        string(APPEND entries "    { \"${plugin}\", \"${headlessUri}\", \"${completeUri}\", \"${controlUri}\", bpiControl_create_${plugin} },\n")
    endforeach()

    set(header "${CMAKE_BINARY_DIR}/generated/ControlPluginList.h")
    file(CONFIGURE OUTPUT "${header}" CONTENT
"// Written by ControlPlugins.cmake: the control builds compiled into the controller.
#pragma once

${declarations}
namespace bpi::generated
{
    struct ControlPluginEntry
    {
        const char* name;           // the plugin's folder in dsp/juce_plugins
        const char* headlessUri;    // what a document names for a speaker
        const char* completeUri;    // the complete build, as a document may also name it
        const char* controlUri;     // the control build's own LV2 URI
        juce::AudioProcessor* (JUCE_CALLTYPE * create)();
    };

    inline const ControlPluginEntry controlPlugins[] =
    {
${entries}    };
}
" @ONLY)
    target_include_directories(${app} PRIVATE "${CMAKE_BINARY_DIR}/generated")
endfunction()
