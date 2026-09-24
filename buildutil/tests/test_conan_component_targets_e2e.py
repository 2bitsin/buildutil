"""#153: a module links a conan dependency's own cmake target names."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from buildutil import installcmd

PKG_PARENT = Path(installcmd.__file__).resolve().parents[1]
CXX = shutil.which("c++") or shutil.which("g++")

e2e = pytest.mark.skipif(
  not all(map(shutil.which, ("conan", "cmake", "ninja"))) or CXX is None,
  reason="needs conan, cmake, ninja and a C++ compiler")

FAKE_RECIPE = """\
from conan import ConanFile
from conan.tools.cmake import CMake, cmake_layout


class Fake(ConanFile):
  name = "fake"
  version = "1.0.0"
  settings = "os", "arch", "compiler", "build_type"
  package_type = "static-library"
  generators = "CMakeToolchain"
  exports_sources = "CMakeLists.txt", "*.cpp", "*.hpp"

  def layout(self):
    cmake_layout(self)

  def build(self):
    cmake = CMake(self)
    cmake.configure()
    cmake.build()

  def package(self):
    CMake(self).install()

  def package_info(self):
    self.cpp_info.set_property("cmake_file_name", "Fake")
    self.cpp_info.set_property("cmake_target_name", "Fake::Fake")
    for component in ("a", "b"):
      self.cpp_info.components[component].libs = [f"fake_{component}"]
      self.cpp_info.components[component].set_property(
        "cmake_target_name", f"Fake::{component.upper()}")
"""

FAKE_LISTS = """\
cmake_minimum_required(VERSION 3.20)
project(fake CXX)
add_library(fake_a STATIC a.cpp)
add_library(fake_b STATIC b.cpp)
install(TARGETS fake_a fake_b)
install(FILES a.hpp b.hpp DESTINATION include)
"""

USER_CORE = """\
#include <fakeuser/core/core.hpp>
#include <a.hpp>
auto fakeuser_core() -> char const* { return fake_a(); }
"""

USER_EXTRA = """\
#include <fakeuser/core/core.hpp>
#include <fakeuser/extra/extra.hpp>
#include <b.hpp>
#include <string>
auto fakeuser_extra() -> std::string {
  return std::string(fakeuser_core()) + fake_b();
}
"""

SMOKE = """\
#include <fakeuser/extra/extra.hpp>
auto main() -> int { return fakeuser_extra() == "ab" ? 0 : 1; }
"""

THIRD_MAIN = """\
#include <fakeuser/extra/extra.hpp>
#include <cstdio>
auto main() -> int {
  std::printf("%s\\n", fakeuser_extra().c_str());
  return 0;
}
"""


def _write(path: Path, text: str) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(text)


def _environment(world: Path) -> dict:
  env = {key: value for key, value in os.environ.items()
         if not key.startswith(("CONAN_REMOTE_", "CI_ARTIFACTORY_"))
         and key not in ("BUILDUTIL_ROOT", "CC", "CXX")}
  env.update(PYTHONPATH=str(PKG_PARENT), BUILDUTIL_SYSTEM="1", BUILDUTIL="1",
             CONAN_HOME=str(world / "home"), BUILDUTIL_NO_CONAN_UPDATE="1",
             CCACHE_DISABLE="1",
             PATH=os.pathsep.join([str(Path(sys.executable).parent),
                                   os.environ.get("PATH", "")]))
  return env


def _run(argv, cwd: Path, env: dict) -> subprocess.CompletedProcess:
  return subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                        capture_output=True, text=True)


def _buildutil(project: Path, env: dict, *argv) -> subprocess.CompletedProcess:
  return _run([sys.executable, "-m", "buildutil", *argv], project, env)


def _export_fake(world: Path, env: dict) -> None:
  recipe = world / "fake"
  _write(recipe / "conanfile.py", FAKE_RECIPE)
  _write(recipe / "CMakeLists.txt", FAKE_LISTS)
  for component in ("a", "b"):
    _write(recipe / f"{component}.hpp",
           f"#pragma once\nauto fake_{component}() -> char const*;\n")
    _write(recipe / f"{component}.cpp",
           f'#include "{component}.hpp"\n'
           f'auto fake_{component}() -> char const* {{ return "{component}"; }}\n')
  _run(["conan", "export", str(recipe)], world, env).check_returncode()


def _fakeuser(world: Path, env: dict) -> Path:
  """A two-module library: core links Fake::A, extra links core and Fake::B."""
  project = world / "fakeuser"
  project.mkdir()
  _buildutil(project, env, "init", "--name", "fakeuser", "--no-agents",
             "--package", "library").check_returncode()
  shutil.rmtree(project / "sources" / "fakeuser")
  modules = project / "sources" / "fakeuser"
  _write(project / "sources" / "CMakeLists.txt",
         'Require(Fake VERSION "1.0.0" CONAN fake)\nScan_subdirectories()\n')
  _write(modules / "core" / "CMakeLists.txt",
         "Init_submodule()\nLink_dependencies(Fake::A)\n")
  _write(modules / "core" / "core.hpp",
         "#pragma once\nauto fakeuser_core() -> char const*;\n")
  _write(modules / "core" / "core.cpp", USER_CORE)
  _write(modules / "extra" / "CMakeLists.txt",
         "Init_submodule()\nLink_dependencies(core Fake::B)\n")
  _write(modules / "extra" / "extra.hpp",
         "#pragma once\n#include <string>\nauto fakeuser_extra() -> std::string;\n")
  _write(modules / "extra" / "extra.cpp", USER_EXTRA)
  _write(project / "test_package" / "smoke.cpp", SMOKE)
  return project


@pytest.fixture(scope="module")
def world(tmp_path_factory):
  world = tmp_path_factory.mktemp("conan-targets")
  env = _environment(world)
  _run(["conan", "remote", "remove", "*"], world, env)
  _export_fake(world, env)
  return world


@e2e
def test_a_modules_conan_cmake_targets_pass_the_package_test(world):
  """publish runs the package test; a consumer then links the package."""
  env = _environment(world)
  project = _fakeuser(world, env)
  published = _buildutil(project, env, "publish", "--no-upload", "--release",
                         "--version", "1.0.0")
  output = published.stdout + published.stderr
  assert published.returncode == 0, output
  assert "fakeuser/1.0.0 packaged and tested in the local cache" in output
  third = world / "third"
  third.mkdir()
  _buildutil(third, env, "init", "--name", "third", "--no-agents",
             "--no-package").check_returncode()
  shutil.rmtree(third / "sources" / "third")
  _write(third / "sources" / "CMakeLists.txt",
         'Require(fakeuser VERSION "1.0.0" CONAN fakeuser)\n'
         "Scan_subdirectories()\n")
  _write(third / "sources" / "app" / "CMakeLists.txt",
         "Init_submodule()\nLink_dependencies(fakeuser::extra)\n")
  _write(third / "sources" / "app" / "main.cpp", THIRD_MAIN)
  built = _buildutil(third, env, "build", "--release", "--no-tests")
  assert built.returncode == 0, built.stdout + built.stderr
  app = next(path for path in (third / "_install").rglob("app")
             if path.is_file())
  assert _run([str(app)], third, env).stdout == "ab\n"
