# Shared CMake setup for every plugin in juce_plugins/.
#
# Each plugin's CMakeLists.txt is just:
#
#     cmake_minimum_required(VERSION 3.22)
#     project(MY_PLUGIN VERSION 0.0.1)
#     find_package(JUCE CONFIG REQUIRED)
#     include(${CMAKE_CURRENT_LIST_DIR}/../cmake/SpeakerPlugin.cmake)
#
#     speaker_add_plugin(myPlugin
#         CODE    Mypl                  # unique 4-char plugin code
#         INPUTS  1                     # main-bus channel counts (see below)
#         OUTPUTS 1
#         SOURCES PluginProcessor.cpp PluginEditor.cpp
#         MODULES juce_dsp)             # JUCE modules beyond the common set, optional
#
# and anything genuinely plugin-specific (an extra library, a define) goes after the call,
# against the same target.
#
# The target name doubles as the product name, the LV2 binary name and the last segment of
# the LV2 URI, and tools/build-podman.sh relies on that: it builds `<name>_LV2` and expects
# `<name>.lv2`.
#
# Build targets (docs/remote-plugin-host.md in bpi_amp_platform). A plugin can also be built
# as a remote control and as a headless DSP engine, from the same sources:
#
#     speaker_add_plugin(myPlugin
#         CODE    Mypl
#         INPUTS  1
#         OUTPUTS 1
#         SOURCES PluginProcessor.cpp        # parameters, state: in every target
#         DSP     MyPluginDsp.cpp            # the audio processing: left out of `control`
#         EDITOR  PluginEditor.cpp           # the GUI: left out of `headless`
#         TARGETS complete control headless)
#
#   complete   -> target `myPlugin`           everything, as before (the default)
#   control    -> target `myPlugin_control`   parameters + editor, no DSP: the editor runs as a
#                                             remote control in the controller app
#   headless   -> target `myPlugin_headless`  parameters + DSP, no editor: runs on the speaker
#
# Each target compiles with SPEAKER_COMPLETE, SPEAKER_CONTROL and SPEAKER_HEADLESS defined to
# 0 or 1, so the sources can tell which build they are in. The control and headless targets get
# their own plugin code (the last character of CODE replaced by 1 or 2) and their own LV2 URI
# (`<base>/<name>_control`, `<base>/<name>_headless`), so a host never confuses them with the
# complete plugin. The parameter layout must be identical in all three; the controller and the
# speaker exchange parameter values and saved state between the control and headless builds.

include_guard(GLOBAL)

# Base of every plugin's LV2 URI. The URI is the plugin's global identity: hosts key their
# plugin database on it, so two plugins sharing one collide and only one survives a scan,
# and changing it orphans every saved session that references the old one. Hence one base
# for the whole repo, with the plugin name appended -- unique by construction. Change it
# deliberately, once (e.g. to the public repository URL), not per machine.
# tools/build-podman.sh reads this default back to write its debug sessions, so keep it on
# one line in this exact form.
# (A plain variable, not a cache entry: a cached value would silently survive an edit here in
# every existing build tree.)
set(SPEAKER_LV2_URI_BASE "https://github.com/AMuszkat/bpi_amp_dsp_tuning")

# JUCE modules every plugin links. A plugin adds more through MODULES.
set(SPEAKER_COMMON_MODULES
    juce_audio_utils
    juce_audio_processors
    juce_gui_basics
    juce_gui_extra)

function(speaker_add_plugin target)
    cmake_parse_arguments(PARSE_ARGV 1 ARG "" "CODE;INPUTS;OUTPUTS" "SOURCES;DSP;EDITOR;MODULES;TARGETS")

    if(ARG_UNPARSED_ARGUMENTS)
        message(FATAL_ERROR "speaker_add_plugin(${target}): unknown arguments ${ARG_UNPARSED_ARGUMENTS}")
    endif()
    if(NOT ARG_CODE MATCHES "^[A-Za-z0-9][a-z0-9][a-z0-9][a-z0-9]$")
        # JUCE wants exactly four characters; GarageBand additionally wants only the first
        # upper-case. VST3/AU hosts key on the code, so it MUST differ between plugins.
        message(FATAL_ERROR "speaker_add_plugin(${target}): CODE must be 4 characters, "
                            "only the first one upper-case (got '${ARG_CODE}')")
    endif()
    # INPUTS / OUTPUTS are the main-bus channel counts. They must match the processor's
    # BusesProperties; CMake does not use them itself -- tools/build-podman.sh reads them
    # from this call to wire the plugin into the debug plugin-host session.
    foreach(n IN ITEMS INPUTS OUTPUTS)
        if(NOT ARG_${n} MATCHES "^[0-9]+$")
            message(FATAL_ERROR "speaker_add_plugin(${target}): ${n} must be a channel count")
        endif()
    endforeach()
    if(NOT ARG_SOURCES)
        message(FATAL_ERROR "speaker_add_plugin(${target}): SOURCES is required")
    endif()

    if(NOT ARG_TARGETS)
        set(ARG_TARGETS complete)
    endif()
    foreach(kind IN LISTS ARG_TARGETS)
        if(NOT kind MATCHES "^(complete|control|headless)$")
            message(FATAL_ERROR "speaker_add_plugin(${target}): TARGETS takes complete, control "
                                "and headless (got '${kind}')")
        endif()
    endforeach()
    if(NOT complete IN_LIST ARG_TARGETS)
        # The complete build is what the desktop host, the tuning workflow and
        # tools/build-podman.sh use; every plugin keeps one.
        message(FATAL_ERROR "speaker_add_plugin(${target}): TARGETS must include complete")
    endif()
    if(control IN_LIST ARG_TARGETS AND NOT ARG_EDITOR)
        message(FATAL_ERROR "speaker_add_plugin(${target}): a control target needs its editor "
                            "sources listed under EDITOR")
    endif()
    if(headless IN_LIST ARG_TARGETS AND NOT ARG_DSP)
        message(FATAL_ERROR "speaker_add_plugin(${target}): a headless target needs its DSP "
                            "sources listed under DSP")
    endif()

    set(modules ${SPEAKER_COMMON_MODULES} ${ARG_MODULES})
    list(REMOVE_DUPLICATES modules)
    list(TRANSFORM modules PREPEND "juce::")

    string(SUBSTRING "${ARG_CODE}" 0 3 codeStem)

    foreach(kind IN LISTS ARG_TARGETS)
        if(kind STREQUAL "complete")
            set(name "${target}")
            set(code "${ARG_CODE}")
            set(sources ${ARG_SOURCES} ${ARG_DSP} ${ARG_EDITOR})
        elseif(kind STREQUAL "control")
            set(name "${target}_control")
            set(code "${codeStem}1")
            set(sources ${ARG_SOURCES} ${ARG_EDITOR})
        else()
            set(name "${target}_headless")
            set(code "${codeStem}2")
            set(sources ${ARG_SOURCES} ${ARG_DSP})
        endif()

        _speaker_add_plugin_target("${name}" "${code}" "${kind}" "${sources}" "${modules}")
    endforeach()
endfunction()

# One juce_add_plugin() target. `kind` is complete, control or headless.
function(_speaker_add_plugin_target name code kind sources modules)
    juce_add_plugin(${name}
        COPY_PLUGIN_AFTER_BUILD TRUE
        PLUGIN_MANUFACTURER_CODE Juce          # may be shared by all plugins
        PLUGIN_CODE ${code}
        FORMATS LV2 VST3
        PRODUCT_NAME "${name}"
        LV2URI "${SPEAKER_LV2_URI_BASE}/${name}"
        LV2_SHARED_LIBRARY_NAME ${name})

    juce_generate_juce_header(${name})

    target_sources(${name} PRIVATE ${sources})

    set(isComplete 0)
    set(isControl 0)
    set(isHeadless 0)
    if(kind STREQUAL "complete")
        set(isComplete 1)
    elseif(kind STREQUAL "control")
        set(isControl 1)
    else()
        set(isHeadless 1)
    endif()

    target_compile_definitions(${name}
        PUBLIC
            JUCE_WEB_BROWSER=0                 # re-enabling needs NEEDS_WEB_BROWSER TRUE above
            JUCE_USE_CURL=0                    # re-enabling needs NEEDS_CURL TRUE above
            JUCE_VST3_CAN_REPLACE_VST2=0
            SPEAKER_COMPLETE=${isComplete}
            SPEAKER_CONTROL=${isControl}
            SPEAKER_HEADLESS=${isHeadless})

    target_link_libraries(${name}
        PRIVATE
            ${modules}
        PUBLIC
            juce::juce_recommended_config_flags
            juce::juce_recommended_warning_flags)

    # Link-time optimisation, parallelised. This replaces juce::juce_recommended_lto_flags:
    # for GCC/Clang that passes a bare `-flto`, which runs the LTO link phase (LTRANS)
    # single-threaded -- the slowest part of the build. `-flto=auto` produces the same
    # binary with one LTRANS job per core. MSVC keeps the usual -GL/-LTCG.
    if(MSVC)
        target_compile_options(${name} PUBLIC $<$<CONFIG:Release>:-GL>)
        target_link_options(${name} PUBLIC $<$<CONFIG:Release>:-LTCG>)
    else()
        target_compile_options(${name} PUBLIC $<$<CONFIG:Release>:-flto=auto>)
        target_link_options(${name} PUBLIC $<$<CONFIG:Release>:-flto=auto>)
    endif()
endfunction()
