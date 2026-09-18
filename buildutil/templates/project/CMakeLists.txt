cmake_minimum_required(VERSION 3.25)
project(@NAME@ CXX)

# the machinery buildutil renders at build time; universal policy
# (compile DB, CTest/GoogleTest, coverage/gc-sections/benchmark
# options) lives there, not here
include(buildutil)

add_subdirectory(sources)
