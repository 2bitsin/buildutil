"""Package test for @PKG_NAME@ — scaffolded by buildutil, then
project-owned. Verifies the cached APPLICATION package ships runnable
executables; extend test() to invoke your program with real arguments
(self.run("yourtool --version", env="conanrun")).
"""
import os

from conan import ConanFile
from conan.tools.build import can_run


class PackageTest(ConanFile):
  settings = "os", "compiler", "build_type", "arch"
  generators = "VirtualRunEnv"

  def requirements(self):
    self.requires(self.tested_reference_str)

  def test(self):
    if not can_run(self):
      return
    dep = self.dependencies["@PKG_NAME@"]
    root = dep.package_folder
    executables = [
      os.path.join(base, f)
      for base, _, files in os.walk(root) for f in files
      if os.access(os.path.join(base, f), os.X_OK)]
    if not executables:
      raise AssertionError(
        f"@PKG_NAME@ packaged no executables under {root}")
    self.output.info(
      f"@PKG_NAME@ ships {len(executables)} executable(s), e.g. "
      + os.path.relpath(executables[0], root))
