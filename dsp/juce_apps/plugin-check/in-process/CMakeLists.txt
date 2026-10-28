# The build tree run.sh makes for one plugin: the plugin's own targets (complete, control,
# headless, with their LV2 bundles, exactly as its CMakeLists.txt declares them), and the
# in-process checker linked against each build's shared code, the way JUCE links a plugin's
# format wrappers to it (_juce_link_plugin_wrapper in JUCEUtils.cmake). See ../README.md.
#
#     cmake -S in-process -B <dir> -DPLUGIN_DIR=<juce_plugins/plugin> -DCMAKE_PREFIX_PATH=<JUCE>
cmake_minimum_required(VERSION 3.22)

project(PLUGIN_CHECK_IN_PROCESS VERSION 0.0.1)

# No find_package(JUCE) here: the plugin's own CMakeLists.txt does it, and a second one in the
# same tree fails ("Some targets in this export set were already defined").

if(NOT PLUGIN_DIR OR NOT EXISTS "${PLUGIN_DIR}/CMakeLists.txt")
    message(FATAL_ERROR "set PLUGIN_DIR to a plugin folder in dsp/juce_plugins")
endif()
get_filename_component(PLUGIN "${PLUGIN_DIR}" NAME)

# The plugin's targets land in <dir>/plugin, so its bundles are
# <dir>/plugin/<target>_artefacts/Release/LV2/<target>.lv2.
add_subdirectory("${PLUGIN_DIR}" plugin)

foreach(build IN ITEMS ${PLUGIN} ${PLUGIN}_control ${PLUGIN}_headless)
    if(NOT TARGET ${build})
        continue()
    endif()

    add_executable(${build}_in_process "${CMAKE_CURRENT_LIST_DIR}/InProcess.cpp")
    target_include_directories(${build}_in_process PRIVATE
        $<TARGET_PROPERTY:${build},INCLUDE_DIRECTORIES>
        "${PLUGIN_DIR}")
    target_link_libraries(${build}_in_process PRIVATE ${build})
endforeach()
