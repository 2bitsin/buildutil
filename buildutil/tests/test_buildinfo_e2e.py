"""The build identity against real cmake: the variables, the header, the
compile line that does not carry it.

Shaped like test_project_options_e2e.py, and for the same reason: a macro
that reaches the header and not the compiler would pass every unit test,
and the only place the force include would show is the real compile line.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from buildutil import deposit

CFG = {"name": "demo", "cmake_option_prefix": "DEMO",
       "module_define_prefix": "DM"}
PYSUPPORT = Path(deposit.__file__).resolve().parent / "pysupport"

pytestmark = pytest.mark.skipif(
  shutil.which("cmake") is None or shutil.which("ninja") is None or
  not (shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")),
  reason="needs cmake, ninja and a C++ compiler")

STAMP = {"number": 12, "commit": "abc1234", "dirty": True,
         "version": "v0.1.0-3-gabc1234-dirty", "tag": "",
         "time": "2026-09-20T12:34Z"}

ROOT = """\
cmake_minimum_required(VERSION 3.25)
project(demo CXX)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
message(STATUS "identity: ${DEMO_BUILD_VERSION}|${DEMO_BUILD_COMMIT}"
               "|${DEMO_BUILD_TAG}|${DEMO_BUILD_TIME}|${DEMO_BUILD_DIRTY}"
               "|${DEMO_BUILD_NUMBER}")
add_subdirectory(sources)
"""

SOURCES = """\
add_library(GTest::gtest_main INTERFACE IMPORTED)
add_library(benchmark::benchmark_main INTERFACE IMPORTED)
Scan_subdirectories()
"""

MAIN = """\
#include <cstdio>

#include "demo/buildinfo.hpp"

int main()
{
  std::printf("%s|%s|%s|%s|%d|%d\\n", DEMO_BUILD_VERSION, DEMO_BUILD_COMMIT,
              DEMO_BUILD_TAG, DEMO_BUILD_TIME, DEMO_BUILD_DIRTY,
              DEMO_BUILD_NUMBER);
  return 0;
}
"""


def _tree(root: Path, stamp: dict | None = STAMP) -> Path:
  deposit.ensure(root, CFG)
  if stamp is not None:
    (root / "_bdudata" / "buildinfo.json").write_text(json.dumps(stamp))
  (root / "CMakeLists.txt").write_text(ROOT)
  module = root / "sources" / "hello"
  module.mkdir(parents=True)
  (root / "sources" / "CMakeLists.txt").write_text(SOURCES)
  (module / "CMakeLists.txt").write_text("Init_submodule()\n")
  (module / "main.cpp").write_text(MAIN)
  return root


def _configure(root: Path, pysupport: Path = PYSUPPORT,
               cwd: Path | None = None) -> subprocess.CompletedProcess:
  # cwd matters: cmake's execute_process inherits it, and python puts it
  # first on sys.path -- run from this repo, the generator is importable
  # whatever BUILDUTIL_PYSUPPORT says.
  return subprocess.run(
    ["cmake", "-S", str(root), "-B", str(root / "b"), "-G", "Ninja",
     "-DBUILD_TESTING=OFF", "-DBUILD_BENCHMARKING=OFF",
     f"-DBUILDUTIL_PYSUPPORT={pysupport}"],
    capture_output=True, text=True, cwd=cwd)


def _built(root: Path) -> str:
  done = _configure(root)
  assert done.returncode == 0, done.stdout + done.stderr
  built = subprocess.run(["cmake", "--build", str(root / "b")],
                         capture_output=True, text=True)
  assert built.returncode == 0, built.stdout + built.stderr
  ran = subprocess.run([str(root / "b" / "bin" / "hello")],
                       capture_output=True, text=True)
  assert ran.returncode == 0, ran.stdout + ran.stderr
  return ran.stdout.strip()


def _line(stamp: dict, dirty: str) -> str:
  """The six values joined, as the binary prints them (dirty 1) and as
  cmake prints them (ON) -- the one reading that differs between them."""
  return "|".join([stamp["version"], stamp["commit"], stamp["tag"],
                   stamp["time"], dirty, str(stamp["number"])])


def test_the_stamped_values_reach_cmake_the_header_and_the_binary(tmp_path):
  root = _tree(tmp_path)
  assert _built(root) == _line(STAMP, "1")
  header = root / "b" / "generated" / "demo" / "buildinfo.hpp"
  assert "#define DEMO_BUILD_NUMBER 12" in header.read_text()
  assert f"identity: {_line(STAMP, 'ON')}" in _configure(root).stdout


def test_no_compile_line_carries_the_header(tmp_path):
  """The force include is exactly what this feature must not have: the
  time moves every build, and every TU would rebuild with it."""
  root = _tree(tmp_path)
  _built(root)
  entries = json.loads((root / "b" / "compile_commands.json").read_text())
  for entry in entries:
    command = entry.get("command") or " ".join(entry["arguments"])
    assert "buildinfo.hpp" not in command, command


def test_a_rebuild_on_a_new_stamp_carries_the_new_values(tmp_path):
  """One build tree, stamped twice: the json is a configure dependency,
  so the build that follows re-renders the header by itself."""
  root = _tree(tmp_path)
  assert _built(root) == _line(STAMP, "1")
  moved = dict(STAMP, number=13, time="2026-09-20T12:35Z", tag="v0.2.0",
               version="v0.2.0")
  (root / "_bdudata" / "buildinfo.json").write_text(json.dumps(moved))
  built = subprocess.run(["cmake", "--build", str(root / "b")],
                         capture_output=True, text=True)
  assert built.returncode == 0, built.stdout + built.stderr
  ran = subprocess.run([str(root / "b" / "bin" / "hello")],
                       capture_output=True, text=True)
  assert ran.stdout.strip() == _line(moved, "1")


def test_a_project_that_never_stamped_one_configures_anyway(tmp_path):
  """A hand-run cmake in a tree the driver has not built: the values
  stand in, the message says so, and nothing fails."""
  root = _tree(tmp_path, stamp=None)
  done = _configure(root)
  assert done.returncode == 0, done.stdout + done.stderr
  assert "build identity is unknown" in done.stdout
  assert "identity: unknown|unknown|||OFF|0" in done.stdout
  header = root / "b" / "generated" / "demo" / "buildinfo.hpp"
  assert '#define DEMO_BUILD_VERSION "unknown"' in header.read_text()


def test_a_cmake_that_cannot_run_the_generator_still_configures(tmp_path):
  """The deposit configured where the package is not importable -- a
  lane image with cmake and a toolchain and nothing else. Every project
  pays this call, so it warns and leaves the variables standing."""
  root = _tree(tmp_path)
  package = root / "poison" / "buildutil"
  pysupport = package / "pysupport"
  pysupport.mkdir(parents=True)
  (package / "__init__.py").write_text("raise ImportError('generator unavailable')\n")
  done = _configure(root, pysupport=pysupport, cwd=root)
  assert done.returncode == 0, done.stdout + done.stderr
  assert "cannot run buildutil.buildinfo" in done.stderr
  assert f"identity: {_line(STAMP, 'ON')}" in done.stdout
  assert not (root / "b" / "generated" / "demo").exists()
