"""The install rpath, spelled for the target's loader: ld.so expands
$ORIGIN and ignores @loader_path, dyld does the opposite. Configure-time
only, so both answers are reachable from one Linux box.
"""
import shutil
import subprocess

import pytest

from buildutil import deposit

CFG = {"cmake_option_prefix": "ACME", "module_define_prefix": "ACM"}

pytestmark = pytest.mark.skipif(
  shutil.which("cmake") is None or shutil.which("ninja") is None or
  not (shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")),
  reason="needs cmake, ninja and a C++ compiler")

ROOT_CMAKE = """\
cmake_minimum_required(VERSION 3.25)
project(rpaths CXX)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
add_subdirectory(sources)
"""

CORE_IMPL = """\
__attribute__((visibility("default"))) int core_answer() { return 42; }
"""

TOOL_MAIN = """\
extern int core_answer();
int main() { return core_answer() == 42 ? 0 : 1; }
"""


def _install_script(tmp_path, *extra):
  """The app sits one mirror level from its shared dep, so both rpath
  forms appear: the binary's own directory and a hop out of it."""
  deposit.ensure(tmp_path, CFG)
  (tmp_path / "CMakeLists.txt").write_text(ROOT_CMAKE)
  src = tmp_path / "sources"
  src.mkdir()
  (src / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  core = src / "ser" / "core"
  core.mkdir(parents=True)
  (core / "CMakeLists.txt").write_text("Init_submodule()\n")
  (core / "impl.cpp").write_text(CORE_IMPL)
  tool = src / "tool"
  tool.mkdir()
  (tool / "CMakeLists.txt").write_text(
    "Init_submodule()\nLink_dependencies(ser-core)\n")
  (tool / "main.cpp").write_text(TOOL_MAIN)
  done = subprocess.run(
    ["cmake", "-S", str(tmp_path), "-B", str(tmp_path / "b"), "-G", "Ninja",
     "-DBUILDUTIL_MODULE_LINKAGE=shared", *extra],
    capture_output=True, text=True)
  assert done.returncode == 0, done.stdout + done.stderr
  return (tmp_path / "b" / "sources" / "tool" /
          "cmake_install.cmake").read_text()


def test_an_elf_target_keeps_origin(tmp_path):
  script = _install_script(tmp_path)
  assert "$ORIGIN:$ORIGIN/ser" in script, script
  assert "@loader_path" not in script, script


def test_a_darwin_target_gets_loader_path(tmp_path):
  """A Mach-O carrying $ORIGIN finds neither its own directory nor the
  sibling module's mirror, and only says so on a Mac."""
  script = _install_script(tmp_path, "-DCMAKE_SYSTEM_NAME=Darwin")
  assert '-add_rpath "@loader_path"' in script, script
  assert '-add_rpath "@loader_path/ser"' in script, script
  assert "$ORIGIN" not in script, script
