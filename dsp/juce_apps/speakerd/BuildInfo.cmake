# Writes BuildInfo.h: speakerd's version (SPEAKERD_VERSION, the project() version of
# CMakeLists.txt, passed as VERSION) and the commit it is built from (SPEAKERD_COMMIT), the
# commit's short hash (12 characters), with "-dirty" when the working tree has changes (tracked or
# untracked, not ignored), or "unknown" without git or a repository. CMakeLists.txt runs it at
# every build, not only at configure, so a new HEAD or an edit shows without a reconfigure; it
# rewrites the header only when the text changes, so an unchanged version and commit recompile
# nothing, and a new one recompiles only what includes BuildInfo.h (Main.cpp). Git finds the
# repository from the source directory, so it works in a git worktree too, where .git is a file.
#
#     cmake -DSOURCE_DIR=<dir> -DVERSION=<MAJOR.MINOR.PATCH> -DOUTPUT=<path of BuildInfo.h> -P BuildInfo.cmake

# project()'s own form (numbers and dots), which is also what the C string below can hold as is.
if(NOT VERSION MATCHES "^[0-9]+(\\.[0-9]+)*$")
    message(FATAL_ERROR "BuildInfo.cmake: VERSION is '${VERSION}', not speakerd's project() version (MAJOR.MINOR.PATCH)")
endif()

set(commit "unknown")
find_program(GIT_EXECUTABLE git)

if(GIT_EXECUTABLE)
    execute_process(COMMAND "${GIT_EXECUTABLE}" -C "${SOURCE_DIR}" rev-parse --short=12 HEAD
                    OUTPUT_VARIABLE hash RESULT_VARIABLE failed
                    OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)

    if(NOT failed AND hash)
        set(commit "${hash}")
        execute_process(COMMAND "${GIT_EXECUTABLE}" -C "${SOURCE_DIR}" status --porcelain
                        OUTPUT_VARIABLE changes RESULT_VARIABLE failed ERROR_QUIET)

        if(failed OR changes)
            set(commit "${commit}-dirty")
        endif()
    endif()
endif()

set(text "// Written by BuildInfo.cmake at every build: speakerd's version and the commit it is built from.\n#pragma once\n#define SPEAKERD_VERSION \"${VERSION}\"\n#define SPEAKERD_COMMIT \"${commit}\"\n")

set(old "")
if(EXISTS "${OUTPUT}")
    file(READ "${OUTPUT}" old)
endif()

if(NOT old STREQUAL text)
    file(WRITE "${OUTPUT}" "${text}")
endif()
