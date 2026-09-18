"""Package test for @PKG_NAME@ — scaffolded by buildutil, then
project-owned. Consumes the CACHED package exactly as a downstream
consumer would: CMakeDeps generates the find_package config from the
recipe's package_info, the smoke executable compiles against the
shipped headers and links the shipped libraries, and runs.

Extend smoke.cpp with real API calls — the scaffold only proves the
package resolves, compiles, links and executes.
"""
from conan import ConanFile
from conan.tools.build import can_run
from conan.tools.cmake import CMake, cmake_layout


class PackageTest(ConanFile):
  settings = "os", "compiler", "build_type", "arch"
  generators = "CMakeDeps", "CMakeToolchain", "VirtualRunEnv"

  def requirements(self):
    self.requires(self.tested_reference_str)

  def layout(self):
    cmake_layout(self)

  def build(self):
    cmake = CMake(self)
    cmake.configure()
    cmake.build()

  def test(self):
    if can_run(self):
      import os
      self.run(os.path.join(self.cpp.build.bindir, "smoke"), env="conanrun")
