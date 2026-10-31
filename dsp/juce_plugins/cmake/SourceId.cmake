# The version and the source identity of a plugin (dsp/juce_plugins/README.md, "Versions and
# source identity"). The one implementation of both, used by the build and by
# tools/plugin-versions.sh:
#
#   include()d by SpeakerPlugin.cmake at configure time, for speaker_read_declaration();
#   run with `cmake -DMODE=<mode> ... -P SourceId.cmake`:
#
#     header       at every build (a custom target): writes OUTPUT, the plugin's
#                  SpeakerBuildInfo.h, when its text changes
#                    -DPLUGINS_DIR=<dsp/juce_plugins> -DPLUGIN=<folder> -DSHARED=<a,b,...>
#                    -DVERSION=<x.y.z> -DOUTPUT=<path>
#     bundle       after juce_lv2_helper wrote a bundle's ttl files: writes buildinfo.ttl next
#                  to them, and leaves manifest.ttl as the helper wrote it
#                    -DHEADER=<SpeakerBuildInfo.h> -DBUNDLE=<dir> -DURI=<plugin URI>
#                    -DBASE=<SPEAKER_LV2_URI_BASE>
#     identity     prints "<version> <source-id>" for one plugin       -DPLUGIN_DIR=<folder>
#     declaration  prints "<version>[ <shared>...]" read from a plugin's CMakeLists.txt, which
#                  need not be in a plugin folder (plugin-versions.sh check gives it the one
#                  of the base commit)                                    -DFILE=<path>
#
# The source identity of a plugin is 12 lowercase hex characters over the inputs it is
# compiled from, in this order: its own folder, each shared folder it declares (SHARED, in the
# declared order), dsp/juce_plugins/cmake, and the pinned JUCE commit (the gitlink
# dsp/juce_plugins/JUCE). From the repository root, for firConv:
#
#     git rev-parse HEAD:dsp/juce_plugins/firConv HEAD:dsp/juce_plugins/shared/blockfir \
#                   HEAD:dsp/juce_plugins/cmake HEAD:dsp/juce_plugins/JUCE \
#         | git hash-object --stdin | cut -c1-12
#
# with "-dirty" appended when `git status --porcelain -- <the same paths>` prints anything
# (tracked changes, staged or not, and untracked files that are not ignored), and "unknown"
# without git, outside a repository, or in one without a commit. The trees are HEAD's, so a
# clean tree's identity is reproducible from git alone; the gitlink is read from the tree too,
# so an uninitialised JUCE submodule (a git worktree) changes nothing. A folder that is not in
# HEAD yet (a new plugin or shared folder) counts as the empty tree,
# `git hash-object -t tree /dev/null`, and its untracked files make the identity -dirty.

include_guard(GLOBAL)

# Run with -P, a script has no policies of its own: CMake would take IN_LIST for an error and
# dereference quoted words in if() (CMP0057, CMP0054). Set them before the functions below,
# which keep the policies of their definition. Included, the includer's apply.
if(CMAKE_SCRIPT_MODE_FILE)
    cmake_minimum_required(VERSION 3.22)
endif()

# Reads what the identity needs from a plugin's CMakeLists.txt as text: the VERSION of its
# project() call (exactly MAJOR.MINOR.PATCH) and the SHARED folders of its
# speaker_add_plugin() call. Sets <out_error> to a message, or to "" when both were read.
# SpeakerPlugin.cmake checks at configure time that CMake's own reading agrees.
function(speaker_read_declaration file out_version out_shared out_error)
    set(${out_version} "" PARENT_SCOPE)
    set(${out_shared} "" PARENT_SCOPE)

    if(NOT EXISTS "${file}")
        set(${out_error} "no ${file}" PARENT_SCOPE)
        return()
    endif()

    file(READ "${file}" text)
    # Comments out: from a # to the end of its line (no plugin's CMakeLists.txt has a # in a
    # string). A newline first, so that a call on the first line starts after one too.
    string(REGEX REPLACE "#[^\n]*" "" text "${text}")
    string(PREPEND text "\n")

    if(NOT text MATCHES "[^A-Za-z0-9_][pP][rR][oO][jJ][eE][cC][tT][ \t\r\n]*\\(([^)]*)\\)")
        set(${out_error} "${file}: no project() call" PARENT_SCOPE)
        return()
    endif()
    set(projectArgs " ${CMAKE_MATCH_1} ")
    if(NOT projectArgs MATCHES "[ \t\r\n]VERSION[ \t\r\n]+([0-9]+\\.[0-9]+\\.[0-9]+)[ \t\r\n]")
        set(${out_error} "${file}: project() needs VERSION MAJOR.MINOR.PATCH, three numbers "
                         "written out (tools/plugin-versions.sh reads it from the text)" PARENT_SCOPE)
        return()
    endif()
    set(version "${CMAKE_MATCH_1}")

    if(NOT text MATCHES "\n[ \t]*speaker_add_plugin[ \t]*\\(([^)]*)\\)")
        set(${out_error} "${file}: no speaker_add_plugin() call at the start of a line" PARENT_SCOPE)
        return()
    endif()
    string(STRIP "${CMAKE_MATCH_1}" callArgs)
    string(REGEX REPLACE "[ \t\r\n]+" ";" tokens "${callArgs}")

    set(keywords CODE INPUTS OUTPUTS SOURCES DSP EDITOR MODULES TARGETS SHARED)
    set(shared "")
    set(inShared FALSE)
    foreach(token IN LISTS tokens)
        if(token IN_LIST keywords)
            set(inShared FALSE)
            if(token STREQUAL "SHARED")
                set(inShared TRUE)
            endif()
        elseif(inShared)
            list(APPEND shared "${token}")
        endif()
    endforeach()

    set(${out_version} "${version}" PARENT_SCOPE)
    set(${out_shared} "${shared}" PARENT_SCOPE)
    set(${out_error} "" PARENT_SCOPE)
endfunction()

# The source identity (above) of the plugin in <plugins_dir>/<plugin> with the shared folders
# <shared> (a list), into <out>.
function(speaker_source_id plugins_dir plugin shared out)
    set(${out} "unknown" PARENT_SCOPE)
    find_program(GIT_EXECUTABLE git)
    if(NOT GIT_EXECUTABLE)
        return()
    endif()

    # Paths relative to dsp/juce_plugins (`HEAD:./<path>` is relative to git's working
    # directory, -C): the same objects as the repository-root paths of the command above.
    set(inputs "./${plugin}")
    foreach(name IN LISTS shared)
        list(APPEND inputs "./shared/${name}")
    endforeach()
    list(APPEND inputs "./cmake" "./JUCE")

    execute_process(COMMAND "${GIT_EXECUTABLE}" -C "${plugins_dir}" rev-parse --verify --quiet "HEAD^{commit}"
                    RESULT_VARIABLE failed OUTPUT_QUIET ERROR_QUIET)
    if(failed)
        return()
    endif()

    set(revisions "")
    foreach(input IN LISTS inputs)
        execute_process(COMMAND "${GIT_EXECUTABLE}" -C "${plugins_dir}" rev-parse --verify --quiet "HEAD:${input}"
                        RESULT_VARIABLE failed OUTPUT_QUIET ERROR_QUIET)
        if(NOT failed)
            list(APPEND revisions "HEAD:${input}")
        else()
            # Not in HEAD yet: the empty tree, whose id rev-parse prints back as it is.
            execute_process(COMMAND "${GIT_EXECUTABLE}" -C "${plugins_dir}" hash-object -t tree --stdin
                            INPUT_FILE /dev/null OUTPUT_VARIABLE emptyTree RESULT_VARIABLE failed
                            OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)
            if(failed)
                return()
            endif()
            list(APPEND revisions "${emptyTree}")
        endif()
    endforeach()

    execute_process(COMMAND "${GIT_EXECUTABLE}" -C "${plugins_dir}" rev-parse ${revisions}
                    COMMAND "${GIT_EXECUTABLE}" -C "${plugins_dir}" hash-object --stdin
                    OUTPUT_VARIABLE hash RESULTS_VARIABLE results
                    OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)
    if(NOT results STREQUAL "0;0" OR NOT hash MATCHES "^[0-9a-f]+$")
        return()
    endif()
    string(SUBSTRING "${hash}" 0 12 id)

    # --no-optional-locks: a build must never hold the index lock that a git command run at
    # the same time needs.
    execute_process(COMMAND "${GIT_EXECUTABLE}" --no-optional-locks -C "${plugins_dir}"
                            status --porcelain -- ${inputs}
                    OUTPUT_VARIABLE changes RESULT_VARIABLE failed ERROR_QUIET)
    if(failed OR NOT changes STREQUAL "")
        string(APPEND id "-dirty")
    endif()

    set(${out} "${id}" PARENT_SCOPE)
endfunction()

# Prints one line on stdout (message() would go to stderr).
function(_speaker_print line)
    execute_process(COMMAND "${CMAKE_COMMAND}" -E echo "${line}")
endfunction()

# Writes <file> only when <text> differs from what it holds, so that nothing that depends on
# it is rebuilt for an unchanged identity.
function(_speaker_write_if_changed file text)
    set(old "")
    if(EXISTS "${file}")
        file(READ "${file}" old)
    endif()
    if(NOT old STREQUAL text)
        file(WRITE "${file}" "${text}")
    endif()
endfunction()

# What follows runs only as `cmake -P SourceId.cmake`, not when the file is include()d.
if(NOT CMAKE_SCRIPT_MODE_FILE)
    return()
endif()
get_filename_component(_speakerScript "${CMAKE_SCRIPT_MODE_FILE}" REALPATH)
get_filename_component(_speakerThisFile "${CMAKE_CURRENT_LIST_FILE}" REALPATH)
if(NOT _speakerScript STREQUAL _speakerThisFile)
    return()
endif()

if(MODE STREQUAL "header")
    string(REPLACE "," ";" shared "${SHARED}")
    speaker_source_id("${PLUGINS_DIR}" "${PLUGIN}" "${shared}" id)
    _speaker_write_if_changed("${OUTPUT}"
"// Written by dsp/juce_plugins/cmake/SourceId.cmake at every build: the version and the source
// identity of the plugin ${PLUGIN}, shared by all its targets (dsp/juce_plugins/README.md).
#pragma once
#define SPEAKER_PLUGIN_NAME \"${PLUGIN}\"
#define SPEAKER_PLUGIN_VERSION \"${VERSION}\"
#define SPEAKER_PLUGIN_SOURCE_ID \"${id}\"
")

elseif(MODE STREQUAL "bundle")
    file(READ "${HEADER}" header)
    if(NOT header MATCHES "#define SPEAKER_PLUGIN_VERSION \"([^\"]+)\"")
        message(FATAL_ERROR "${HEADER}: no SPEAKER_PLUGIN_VERSION")
    endif()
    set(version "${CMAKE_MATCH_1}")
    if(NOT header MATCHES "#define SPEAKER_PLUGIN_SOURCE_ID \"([^\"]+)\"")
        message(FATAL_ERROR "${HEADER}: no SPEAKER_PLUGIN_SOURCE_ID")
    endif()
    set(id "${CMAKE_MATCH_1}")

    _speaker_write_if_changed("${BUNDLE}/buildinfo.ttl"
"@prefix bpi: <${BASE}#> .

<${URI}>
    bpi:version \"${version}\" ;
    bpi:sourceId \"${id}\" .
")

    # manifest.ttl stays exactly as juce_lv2_helper wrote it: nothing names buildinfo.ttl. An
    # rdfs:seeAlso to it would make lilv parse it as plugin data, and a damaged file would then
    # stop the plugin instantiating (lilv's plugin.c and instance.c, parse_errors). No LV2 host
    # needs it; speakerd reads it directly.

elseif(MODE STREQUAL "identity")
    get_filename_component(pluginDir "${PLUGIN_DIR}" ABSOLUTE)
    get_filename_component(plugin "${pluginDir}" NAME)
    get_filename_component(pluginsDir "${pluginDir}" DIRECTORY)
    speaker_read_declaration("${pluginDir}/CMakeLists.txt" version shared error)
    if(error)
        message(FATAL_ERROR "${error}")
    endif()
    speaker_source_id("${pluginsDir}" "${plugin}" "${shared}" id)
    _speaker_print("${version} ${id}")

elseif(MODE STREQUAL "declaration")
    speaker_read_declaration("${FILE}" version shared error)
    if(error)
        message(FATAL_ERROR "${error}")
    endif()
    list(JOIN shared " " sharedText)
    string(STRIP "${version} ${sharedText}" line)
    _speaker_print("${line}")

else()
    message(FATAL_ERROR "SourceId.cmake: MODE must be header, bundle, identity or declaration "
                        "(got '${MODE}')")
endif()
