"""Require() in cmake: one file, once per NAME, SYSTEM never from conan."""
import os
import shutil
import subprocess

import pytest

from buildutil import deposit

CFG = {"cmake_option_prefix": "ACME", "module_define_prefix": "ACM"}

e2e = pytest.mark.skipif(
  shutil.which("cmake") is None or
  not (shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")),
  reason="needs cmake and a C++ compiler")

ROOT = """\
cmake_minimum_required(VERSION 3.25)
project(placement CXX)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
add_subdirectory(sources)
"""

FAKE_CONFIG = """\
add_library(Fake::fake SHARED IMPORTED)
set_target_properties(Fake::fake PROPERTIES IMPORTED_LOCATION "%s/libfake.so")
set(Fake_FOUND TRUE)
"""

FAKE_VERSION = """\
set(PACKAGE_VERSION 1.0)
set(PACKAGE_VERSION_COMPATIBLE TRUE)
"""


def _configure(tmp_path, sources, module="", env=None, extra=()):
  deposit.ensure(tmp_path, CFG)
  (tmp_path / "CMakeLists.txt").write_text(ROOT)
  (tmp_path / "sources").mkdir(exist_ok=True)
  (tmp_path / "sources" / "CMakeLists.txt").write_text(sources)
  if module:
    (tmp_path / "sources" / "widget").mkdir()
    (tmp_path / "sources" / "widget" / "CMakeLists.txt").write_text(module)
  return subprocess.run(
    ["cmake", "-S", str(tmp_path), "-B", str(tmp_path / "b"), *extra],
    capture_output=True, text=True, env={**os.environ, **(env or {})})


def _fake_package(prefix, location):
  config = prefix / "lib" / "cmake" / "Fake"
  config.mkdir(parents=True)
  (config / "FakeConfig.cmake").write_text(FAKE_CONFIG % location)
  (config / "FakeConfigVersion.cmake").write_text(FAKE_VERSION)
  return prefix


def _with_fake(tmp_path, sources, module=""):
  """Configure against a scaffolded Fake package, never a host package."""
  prefix = _fake_package(tmp_path / "prefix", tmp_path / "host-lib")
  return _configure(tmp_path, sources, module,
                    env={"CMAKE_PREFIX_PATH": str(prefix)})


@e2e
def test_a_Require_in_a_module_CMakeLists_is_refused_by_file(tmp_path):
  proc = _with_fake(tmp_path, "add_subdirectory(widget)\n",
                    'Require(Fake VERSION ">=0" SYSTEM)\n')
  assert proc.returncode != 0
  assert "sources/widget/CMakeLists.txt" in proc.stderr
  assert "Require belongs in" in proc.stderr


@e2e
def test_a_second_Require_of_one_NAME_is_refused(tmp_path):
  proc = _with_fake(tmp_path, 'Require(Fake VERSION ">=0" SYSTEM)\n'
                              'Require(Fake VERSION ">=0.5" SYSTEM)\n')
  assert proc.returncode != 0
  assert "Require(Fake) is declared twice" in proc.stderr


@e2e
def test_one_NAME_split_across_platforms_is_one_Require_per_platform(tmp_path):
  proc = _with_fake(
    tmp_path, 'Require(Fake VERSION ">=0" SYSTEM PLATFORM Linux)\n'
              'Require(Fake VERSION ">=0" CONAN fake PLATFORM Emscripten)\n')
  assert proc.returncode == 0, proc.stdout + proc.stderr


@e2e
def test_PUBLIC_is_valid_with_SYSTEM(tmp_path):
  proc = _with_fake(tmp_path, 'Require(Fake VERSION ">=0" SYSTEM PUBLIC)\n')
  assert proc.returncode == 0, proc.stdout + proc.stderr


@e2e
def test_FORCE_without_SYSTEM_is_refused_by_name(tmp_path):
  proc = _with_fake(tmp_path,
                    'Require(Fake VERSION ">=0" FORCE PLATFORM Nowhere)\n')
  assert proc.returncode != 0
  assert "FORCE without SYSTEM" in proc.stderr


@e2e
def test_a_SYSTEM_target_inside_the_conan_cache_is_refused(tmp_path):
  home = tmp_path / "conan-home"
  location = home / "p" / "fake1234" / "p" / "lib"
  location.mkdir(parents=True)
  prefix = _fake_package(tmp_path / "prefix", location)
  proc = _configure(tmp_path, 'Require(Fake VERSION ">=0" SYSTEM)\n',
                    env={"CONAN_HOME": str(home)},
                    extra=[f"-DCMAKE_PREFIX_PATH={prefix}"])
  assert proc.returncode != 0
  assert "imported Fake::fake from" in proc.stderr
  assert "inside the conan cache" in proc.stderr


@e2e
def test_a_SYSTEM_find_skips_the_conan_generators(tmp_path):
  """The generators serve conan dependants; the project finds the host's."""
  generators = tmp_path / "generators"
  (generators / "lib" / "cmake" / "Fake").mkdir(parents=True)
  (generators / "lib" / "cmake" / "Fake" / "FakeConfig.cmake").write_text(
    'message(FATAL_ERROR "found the generators copy")\n')
  (generators / "lib" / "cmake" / "Fake" / "FakeConfigVersion.cmake"
   ).write_text(FAKE_VERSION)
  toolchain = generators / "toolchain.cmake"
  toolchain.write_text(
    f'list(PREPEND CMAKE_PREFIX_PATH "{generators}")\n')
  prefix = _fake_package(tmp_path / "prefix", tmp_path / "host-lib")
  proc = _configure(tmp_path, 'Require(Fake VERSION ">=0" SYSTEM)\n',
                    env={"CMAKE_PREFIX_PATH": str(prefix)},
                    extra=[f"-DCMAKE_TOOLCHAIN_FILE={toolchain}"])
  assert proc.returncode == 0, proc.stdout + proc.stderr
