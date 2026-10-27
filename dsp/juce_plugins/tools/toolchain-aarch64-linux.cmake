# CMake Toolchain File for Cross-Compiling to Banana Pi CM4 IO (Amlogic A311D)
#
# Shared by every plugin; tools/build-podman.sh and tools/build-aarch64.sh pass it as
#   -DCMAKE_TOOLCHAIN_FILE=<juce_plugins>/tools/toolchain-aarch64-linux.cmake
#
# Prerequisites (Linux host):
#   sudo apt install gcc-aarch64-linux-gnu g++-aarch64-linux-gnu

set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR aarch64)

# Cross-compiler
set(CMAKE_C_COMPILER aarch64-linux-gnu-gcc)
set(CMAKE_CXX_COMPILER aarch64-linux-gnu-g++)

# Search paths for cross-compilation
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)

# A311D has Cortex-A73 (4x) + Cortex-A53 (2x) big.LITTLE
# Using cortex-a53 tuning ensures compatibility with both core types
# NEON and hard-float are always enabled in aarch64
# set(CMAKE_C_FLAGS_INIT "-mcpu=cortex-a53")
# set(CMAKE_CXX_FLAGS_INIT "-mcpu=cortex-a53")
