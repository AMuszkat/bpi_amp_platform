# The test of the shared block FIR (README.md). Built with the flags every plugin target gets
# (Release, juce::juce_recommended_config_flags, SpeakerPlugin.cmake's -flto=auto), so that it
# tests the kernel the way it ships; run.sh builds it for x86_64 and for aarch64 and runs both.
#
#     cmake -S tests/blockfir -B <dir> -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH=<JUCE>
cmake_minimum_required(VERSION 3.22)

project(BLOCKFIR_TEST VERSION 0.0.1)

find_package(JUCE CONFIG REQUIRED)
include(${CMAKE_CURRENT_LIST_DIR}/../../cmake/SpeakerPlugin.cmake)

juce_add_console_app(blockfir_test PRODUCT_NAME "blockfir_test")

target_sources(blockfir_test PRIVATE BlockFirTest.cpp Reference.cpp)

target_include_directories(blockfir_test PRIVATE
    "${SPEAKER_PLUGINS_DIR}/shared/blockfir"     # BlockFir.h
    "${SPEAKER_PLUGINS_DIR}/firConv"             # filters.hpp, the taps firConv compiles in
    "${SPEAKER_PLUGINS_DIR}/crossover")          # crossover_coefs.hpp, crossover's

target_compile_definitions(blockfir_test PRIVATE
    JUCE_WEB_BROWSER=0
    JUCE_USE_CURL=0)

target_link_libraries(blockfir_test
    PRIVATE
        juce::juce_dsp
    PUBLIC
        juce::juce_recommended_config_flags
        juce::juce_recommended_warning_flags)

speaker_add_lto_flags(blockfir_test)
set_target_properties(blockfir_test PROPERTIES POSITION_INDEPENDENT_CODE TRUE)   # as the plugins' code

# The reference does what it says and nothing else: a multiply and an add stay two roundings
# unless it calls std::fma, and nothing is reordered. Not in the LTO, so that no other
# translation unit's flags reach it.
set_source_files_properties(Reference.cpp PROPERTIES COMPILE_OPTIONS "-ffp-contract=off;-fno-lto")
