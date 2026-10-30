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
# Build targets (docs/remote-plugin-host.md at the repository root). A plugin can also be built
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
#
# Shared source folders. Code that several plugins compile lives in juce_plugins/shared/<name>/
# (header-only: it is compiled where a plugin's sources include it), and a plugin that uses it
# names the folder:
#
#     speaker_add_plugin(myPlugin
#         ...
#         SHARED  blockfir)                  # juce_plugins/shared/blockfir: BlockFir.h
#
# which puts the folder on the include path of every target, and into the plugin's source
# identity (below). Only the folders a plugin declares are on its include path.
#
# Version and source identity (juce_plugins/README.md, "Versions and source identity"). Every
# target embeds the plugin's version (its project() VERSION, MAJOR.MINOR.PATCH) and its source
# identity (SourceId.cmake), recomputed at every build without a reconfigure:
#   - SpeakerBuildInfo.h, generated, on every target's include path: SPEAKER_PLUGIN_NAME,
#     SPEAKER_PLUGIN_VERSION, SPEAKER_PLUGIN_SOURCE_ID;
#   - the string "bpi-build-info: <LV2 URI> version <version> source <source-id>" in each
#     LV2 and VST3 binary (`strings <binary> | grep bpi-build-info`);
#   - buildinfo.ttl in each LV2 bundle, next to manifest.ttl, which names it (rdfs:seeAlso).
# tools/plugin-versions.sh reads the version and SHARED from this call's text, so both must be
# written out literally; the configure step checks that it reads what CMake does.

# Per directory, not global: each plugin's directory needs the variables below, also when one
# build adds several plugins (the controller), where a global guard would skip them for all
# but the first.
include_guard(DIRECTORY)

include(${CMAKE_CURRENT_LIST_DIR}/SourceId.cmake)

# Base of every plugin's LV2 URI. The URI is the plugin's global identity: hosts key their
# plugin database on it, so two plugins sharing one collide and only one survives a scan,
# and changing it orphans every saved session that references the old one. Hence one base
# for the whole repo, with the plugin name appended -- unique by construction. Change it
# deliberately, once (e.g. to the public repository URL), not per machine. It still names the
# bpi_amp_dsp_tuning repository the plugins came from: it is an identity, not a link, and
# changing it would orphan every saved session.
# tools/build-podman.sh reads this default back to write its debug sessions, so keep it on
# one line in this exact form.
# (A plain variable, not a cache entry: a cached value would silently survive an edit here in
# every existing build tree.)
set(SPEAKER_LV2_URI_BASE "https://github.com/AMuszkat/bpi_amp_dsp_tuning")

# juce_plugins/, which holds the plugin folders, shared/ and cmake/.
get_filename_component(SPEAKER_PLUGINS_DIR "${CMAKE_CURRENT_LIST_DIR}/.." ABSOLUTE)

# JUCE modules every plugin links. A plugin adds more through MODULES.
set(SPEAKER_COMMON_MODULES
    juce_audio_utils
    juce_audio_processors
    juce_gui_basics
    juce_gui_extra)

function(speaker_add_plugin target)
    cmake_parse_arguments(PARSE_ARGV 1 ARG "" "CODE;INPUTS;OUTPUTS" "SOURCES;DSP;EDITOR;MODULES;TARGETS;SHARED")

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

    # The plugin's folder is an input of its identity, and the scripts find plugins by
    # folder: it must be juce_plugins/<target>.
    get_filename_component(here "${CMAKE_CURRENT_SOURCE_DIR}" REALPATH)
    get_filename_component(expected "${SPEAKER_PLUGINS_DIR}/${target}" REALPATH)
    if(NOT here STREQUAL expected)
        message(FATAL_ERROR "speaker_add_plugin(${target}): must be called from "
                            "juce_plugins/${target}/CMakeLists.txt (called from ${CMAKE_CURRENT_SOURCE_DIR})")
    endif()

    set(sharedDirs "")
    foreach(name IN LISTS ARG_SHARED)
        if(NOT name MATCHES "^[A-Za-z0-9_-]+$" OR NOT IS_DIRECTORY "${SPEAKER_PLUGINS_DIR}/shared/${name}")
            message(FATAL_ERROR "speaker_add_plugin(${target}): SHARED names a folder of "
                                "juce_plugins/shared (got '${name}')")
        endif()
        list(APPEND sharedDirs "${SPEAKER_PLUGINS_DIR}/shared/${name}")
    endforeach()

    # tools/plugin-versions.sh reads the version and the shared folders from the text of this
    # file (SourceId.cmake): it must read what CMake does.
    speaker_read_declaration("${CMAKE_CURRENT_SOURCE_DIR}/CMakeLists.txt" textVersion textShared error)
    if(error)
        message(FATAL_ERROR "speaker_add_plugin(${target}): ${error}")
    endif()
    if(NOT textVersion STREQUAL PROJECT_VERSION)
        message(FATAL_ERROR "speaker_add_plugin(${target}): the project() VERSION reads as "
                            "'${textVersion}' from the text, CMake has '${PROJECT_VERSION}'")
    endif()
    if(NOT "${textShared}" STREQUAL "${ARG_SHARED}")
        message(FATAL_ERROR "speaker_add_plugin(${target}): SHARED reads as '${textShared}' "
                            "from the text, CMake has '${ARG_SHARED}': write it out literally")
    endif()

    # SpeakerBuildInfo.h, rewritten at every build when the identity changed (a new HEAD, an
    # edit), shared by the plugin's targets.
    set(generated "${CMAKE_CURRENT_BINARY_DIR}/speaker_generated")
    set(buildInfoHeader "${generated}/SpeakerBuildInfo.h")
    file(MAKE_DIRECTORY "${generated}")
    list(JOIN ARG_SHARED "," sharedArg)
    add_custom_target(${target}_build_info
        COMMAND "${CMAKE_COMMAND}" -DMODE=header "-DPLUGINS_DIR=${SPEAKER_PLUGINS_DIR}"
                "-DPLUGIN=${target}" "-DSHARED=${sharedArg}" "-DVERSION=${PROJECT_VERSION}"
                "-DOUTPUT=${buildInfoHeader}" -P "${SPEAKER_PLUGINS_DIR}/cmake/SourceId.cmake"
        BYPRODUCTS "${buildInfoHeader}"
        COMMENT "${target}: version ${PROJECT_VERSION} and the source identity"
        VERBATIM)

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

        _speaker_add_plugin_target("${name}" "${code}" "${kind}" "${sources}" "${modules}"
                                   "${sharedDirs}" "${target}_build_info" "${buildInfoHeader}")
    endforeach()
endfunction()

# One juce_add_plugin() target. `kind` is complete, control or headless.
function(_speaker_add_plugin_target name code kind sources modules sharedDirs buildInfoTarget buildInfoHeader)
    # The copy into ~/.lv2 and ~/.vst3 (or a cross build's copy directories) is enabled at the
    # end, by juce_enable_copy_plugin_step(), not by COPY_PLUGIN_AFTER_BUILD: POST_BUILD steps
    # run in the order they are added, and the copy must come after buildinfo.ttl is written.
    juce_add_plugin(${name}
        COPY_PLUGIN_AFTER_BUILD FALSE
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

    speaker_add_lto_flags(${name})

    get_filename_component(generated "${buildInfoHeader}" DIRECTORY)
    target_include_directories(${name} PRIVATE ${sharedDirs} "${generated}")
    add_dependencies(${name} ${buildInfoTarget})

    set(uri "${SPEAKER_LV2_URI_BASE}/${name}")
    get_target_property(wrappers ${name} JUCE_ACTIVE_PLUGIN_TARGETS)

    # The version and the identity in every binary. Compiled into each format's wrapper (an
    # object on the link line is always linked; one in the shared-code archive that nothing
    # references would be dropped), kept through LTO by `used`.
    set(buildInfoSource "${generated}/${name}_build_info.cpp")
    file(CONFIGURE OUTPUT "${buildInfoSource}" CONTENT
"// Written by SpeakerPlugin.cmake: the version and the source identity of @name@, as a string
// in its binary (`strings <binary> | grep bpi-build-info`).
#include \"SpeakerBuildInfo.h\"

#if defined (__GNUC__)
 #define SPEAKER_KEEP __attribute__ ((used))
#else
 #define SPEAKER_KEEP
#endif

extern \"C\" const char speaker_build_info[];
extern \"C\" SPEAKER_KEEP const char speaker_build_info[] =
    \"bpi-build-info: @uri@ version \" SPEAKER_PLUGIN_VERSION \" source \" SPEAKER_PLUGIN_SOURCE_ID;
" @ONLY)
    foreach(wrapper IN LISTS wrappers)
        target_sources(${wrapper} PRIVATE "${buildInfoSource}")
        add_dependencies(${wrapper} ${buildInfoTarget})
    endforeach()

    # buildinfo.ttl in the LV2 bundle, after juce_lv2_helper has written the bundle's ttl files
    # (its POST_BUILD step, added by juce_add_plugin) and before the copy (below).
    get_target_property(bundle ${name}_LV2 JUCE_PLUGIN_ARTEFACT_FILE)
    add_custom_command(TARGET ${name}_LV2 POST_BUILD
        COMMAND "${CMAKE_COMMAND}" -DMODE=bundle "-DHEADER=${buildInfoHeader}" "-DBUNDLE=${bundle}"
                "-DURI=${uri}" "-DBASE=${SPEAKER_LV2_URI_BASE}"
                -P "${SPEAKER_PLUGINS_DIR}/cmake/SourceId.cmake"
        VERBATIM)

    juce_enable_copy_plugin_step(${name})
endfunction()

# Link-time optimisation, parallelised, the way every plugin target gets it (also used by the
# tests that build plugin code, so that they test what ships). This replaces
# juce::juce_recommended_lto_flags: for GCC/Clang that passes a bare `-flto`, which runs the
# LTO link phase (LTRANS) single-threaded -- the slowest part of the build. `-flto=auto`
# produces the same binary with one LTRANS job per core. MSVC keeps the usual -GL/-LTCG.
function(speaker_add_lto_flags name)
    if(MSVC)
        target_compile_options(${name} PUBLIC $<$<CONFIG:Release>:-GL>)
        target_link_options(${name} PUBLIC $<$<CONFIG:Release>:-LTCG>)
    else()
        target_compile_options(${name} PUBLIC $<$<CONFIG:Release>:-flto=auto>)
        target_link_options(${name} PUBLIC $<$<CONFIG:Release>:-flto=auto>)
    endif()
endfunction()
