"""The universal policy moved OUT of every project's root CMakeLists and
into the rendered machinery (owner req: "minimize the root CMakeLists.txt
to the bare essentials and have any universal buildutil functionality
hidden, for all buildutil projects").

Every project used to paste the same block -- the compile DB,
include(CTest)/include(GoogleTest), option(BUILD_BENCHMARKING), and the
coverage/gc-sections options with their flag blocks -- differing only in
the option prefix, and never updated it again. These tests build a root
that carries NONE of it: cmake_minimum_required, project(),
include(buildutil), add_subdirectory(sources). Anything the machinery
failed to take over shows up here as a missing artifact, not as a
project's private regression six months later."""
import json
import shutil
import subprocess
import sys

import pytest

from buildutil import deposit

CFG = {"cmake_option_prefix": "ACME", "module_define_prefix": "ACM"}

pytestmark = pytest.mark.skipif(
  shutil.which("cmake") is None or shutil.which("ninja") is None or
  not (shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")),
  reason="needs cmake, ninja and a C++ compiler")

# THE minimal root, character for character what `buildutil init`
# scaffolds -- with the module-path line the driver would otherwise pass
# as -DCMAKE_MODULE_PATH.
MINIMAL_ROOT = """\
cmake_minimum_required(VERSION 3.25)
project(bare CXX)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
add_subdirectory(sources)
"""

# Read AFTER include(buildutil) and before any module: the machinery is
# what has to have supplied these, and a cmake error here names the one
# that went missing instead of failing somewhere downstream.
SOURCES = """\
if(NOT COMMAND gtest_discover_tests)
  message(FATAL_ERROR "the machinery did not include(GoogleTest)")
endif()
if(NOT DEFINED BUILD_TESTING)
  message(FATAL_ERROR "the machinery did not include(CTest)")
endif()
if(NOT DEFINED BUILD_BENCHMARKING)
  message(FATAL_ERROR "the machinery did not declare BUILD_BENCHMARKING")
endif()
Scan_subdirectories()
"""


def _tree(root):
  deposit.ensure(root, CFG)
  (root / "CMakeLists.txt").write_text(MINIMAL_ROOT)
  src = root / "sources"
  src.mkdir()
  (src / "CMakeLists.txt").write_text(SOURCES)
  mod = src / "hello"
  mod.mkdir()
  (mod / "CMakeLists.txt").write_text("Init_submodule()\n")
  (mod / "hello.cpp").write_text("int hello() { return 7; }\n")
  (mod / "main.cpp").write_text("int hello();\nint main() { return hello() - 7; }\n")
  # a python suite registers through the same ctest wiring as gtest
  # without dragging GoogleTest's runtime into the fixture build
  (mod / "check.test.py").write_text("def test_ok():\n  assert True\n")
  # the registered runner is the project venv's python -- route it to THIS
  # interpreter through an exec script, not a symlink (a symlinked venv
  # python loses its pyvenv.cfg discovery, and with it pytest)
  venv_bin = root / "_pyvenv" / "bin"
  venv_bin.mkdir(parents=True)
  shim = venv_bin / "python"
  shim.write_text(f"#!/bin/sh\nexec {sys.executable} \"$@\"\n")
  shim.chmod(0o755)
  return root


def _configure(root, *extra):
  proc = subprocess.run(
    ["cmake", "-S", str(root), "-B", str(root / "b"), "-G", "Ninja", *extra],
    capture_output=True, text=True)
  assert proc.returncode == 0, proc.stdout + proc.stderr
  return root / "b"


def _compile_flags(build):
  db = json.loads((build / "compile_commands.json").read_text())
  return " ".join(entry["command"] for entry in db)


def test_a_bare_root_still_gets_the_compile_database(tmp_path):
  """CMAKE_EXPORT_COMPILE_COMMANDS was the root's line; clangd, the
  vscode integration, `buildutil analyze` and the reflect scan all read
  the file it produces, and a project that dropped the line got a
  working build with silently dead tooling."""
  build = _configure(_tree(tmp_path))
  assert (build / "compile_commands.json").is_file()
  assert "hello.cpp" in _compile_flags(build)


def test_a_bare_root_still_registers_and_runs_tests(tmp_path):
  """include(CTest) -- enable_testing() and BUILD_TESTING -- came from
  the root too. The suite has to actually RUN: a tree that configures but
  registers nothing looks identical from the outside."""
  root = _tree(tmp_path)
  build = _configure(root)
  assert subprocess.run(["cmake", "--build", str(build)],
                        capture_output=True, text=True).returncode == 0
  proc = subprocess.run(
    ["ctest", "--test-dir", str(build), "--output-on-failure"],
    capture_output=True, text=True)
  assert proc.returncode == 0, proc.stdout + proc.stderr
  assert "hello-pytest" in proc.stdout, proc.stdout


def test_coverage_instruments_from_the_canonical_switch(tmp_path):
  """BUILDUTIL_COVERAGE, not <PREFIX>_COVERAGE: one name for every
  project, because the block acting on it is one file now."""
  build = _configure(_tree(tmp_path), "-DBUILDUTIL_COVERAGE=ON")
  flags = _compile_flags(build)
  assert "--coverage" in flags
  assert "-fprofile-abs-path" in flags       # gcovr must not spawn from $HOME
  assert "-O0" in flags                      # line numbers stay honest


def test_gc_sections_instruments_from_the_canonical_switch(tmp_path):
  build = _configure(_tree(tmp_path), "-DBUILDUTIL_GC_SECTIONS=ON")
  flags = _compile_flags(build)
  assert "-ffunction-sections" in flags and "-fdata-sections" in flags
  link = (build / "build.ninja").read_text()
  assert "--print-gc-sections" in link       # what the dead-code log reads


def test_the_prefixed_switch_is_no_longer_understood(tmp_path):
  """The old spelling is GONE, not quietly honoured: a root that kept its
  own block is migrated by the release sweep, and a -D nobody reads would
  make `buildutil coverage` report zero coverage with no error."""
  build = _configure(_tree(tmp_path), "-DACME_COVERAGE=ON")
  assert "--coverage" not in _compile_flags(build)
