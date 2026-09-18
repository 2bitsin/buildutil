# Project dependencies — the single source of truth. buildutil.cmake's
# Require() runs find_package at configure time, and conanfile.py parses
# the SAME calls to build the conan graph, so the two can never disagree.
# TEST/BENCH deps only materialize when tests/benches actually build;
# CONAN names the conan package when it differs from the cmake name.
Require(GTest VERSION ">=1.17.0" TEST CONAN gtest COMPONENTS gtest gtest_main gmock gmock_main)
Require(benchmark VERSION ">=1.9.0" BENCH CONAN benchmark)

# Every subdirectory holding a CMakeLists.txt becomes a module; a
# directory without one is a group and is scanned recursively.
Scan_subdirectories()
