cmake_minimum_required(VERSION 3.25)
project(package_smoke CXX)

find_package(@PKG_NAME@ REQUIRED CONFIG)

add_executable(smoke smoke.cpp)
target_link_libraries(smoke PRIVATE @PKG_NAME@::@PKG_NAME@)
