"""Field fixes from oxbox/BossDeux packaging — the two
buildutil.cmake gaps that only surface in SPLIT modules (main.cpp present,
so the module is <mod>-lib + exe):

The first — the generated-resources include root and ordering attached to
${target}, which in a split module is the EXECUTABLE; every lib TU
including the generated header failed to compile.

The second — the pybind bridge linked the executable (illegal without
ENABLE_EXPORTS: configure died with "may not be linked into another
target") and got no module usage requirements (the exe's pools are
PRIVATE), so a bridge TU with a qualified cross-module include failed.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from buildutil import deposit

CFG = {"cmake_option_prefix": "ACME", "module_define_prefix": "ACM"}

e2e = pytest.mark.skipif(
  shutil.which("cmake") is None or shutil.which("ninja") is None or
  not (shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")),
  reason="needs cmake, ninja and a C++ compiler")

ROOT_CMAKE = """\
cmake_minimum_required(VERSION 3.25)
project(fieldfix CXX)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
add_subdirectory(sources)
"""


def _scaffold(tmp_path):
  deposit.ensure(tmp_path, CFG)
  (tmp_path / "CMakeLists.txt").write_text(ROOT_CMAKE)
  src = tmp_path / "sources"
  src.mkdir()
  (src / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  return src


def _cmake(tmp_path, *steps):
  procs = []
  for cmd in steps:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    procs.append(proc)
    if proc.returncode != 0:
      break
  return procs


# ----------------------------------------------------- resources --

FONT_HPP = """\
#pragma once
#include "resources/vga.rom.hpp"
inline unsigned char first_glyph_byte() {
  return static_cast<unsigned char>(VGA_ROM[0]);
}
"""


@e2e
def test_resources_reach_the_module_library(tmp_path):
  """The reported shape: the blob's generated header is included from a
  TU compiled into <mod>-lib, NOT from main.cpp. Before the fix the -I
  and the generation ordering lived on the exe alone, so this exact
  tree failed with 'resources/vga.rom.hpp: No such file or directory'."""
  src = _scaffold(tmp_path)
  gui = src / "gui"
  gui.mkdir()
  (gui / "CMakeLists.txt").write_text("Init_submodule()\n")
  (gui / "vga.rom.bin").write_bytes(bytes(range(16)))
  (gui / "font.hpp").write_text(FONT_HPP)
  (gui / "font.cpp").write_text(
    '#include "font.hpp"\n'
    'unsigned char glyph() { return first_glyph_byte(); }\n')
  (gui / "main.cpp").write_text(
    "unsigned char glyph();\n"
    "int main() { return glyph() == 0 ? 0 : 1; }\n")
  build = tmp_path / "b"
  procs = _cmake(tmp_path,
    ["cmake", "-S", str(tmp_path), "-B", str(build), "-G", "Ninja"],
    ["cmake", "--build", str(build)])
  for proc in procs:
    assert proc.returncode == 0, proc.stdout + proc.stderr
  app = build / "bin" / "gui"
  assert app.is_file()
  assert subprocess.run([str(app)]).returncode == 0, \
    "the embedded resource byte did not survive to runtime"


# ------------------------------------------------- pybind bridge --

@pytest.fixture(scope="module")
def pybind_venv(tmp_path_factory):
  """One real venv with pybind11, shared by the module's tests. The
  bridge probes the interpreter the DRIVER runs under (BUILDUTIL_PY,
  which under the driver IS this venv's python), so each test hands it
  over the way the driver does -- and symlinks the venv in as well,
  because the module's own build still lives beside it."""
  venv = tmp_path_factory.mktemp("pyb") / "venv"
  if subprocess.run([sys.executable, "-m", "venv", str(venv)],
                    capture_output=True).returncode != 0:
    pytest.skip("cannot create a venv")
  pip = venv / "bin" / "pip"
  if subprocess.run([str(pip), "install", "pybind11"],
                    capture_output=True).returncode != 0:
    pytest.skip("cannot install pybind11 into the venv")
  return venv


BRIDGE_CPP = """\
#include <pybind11/pybind11.h>
#include "util/helper.hpp"   // qualified cross-module include
int engine_state();          // and a symbol from emu-lib
PYBIND11_MODULE(emu, m) {
  m.def("state", [] { return engine_state() + util_answer(); });
}
"""


@e2e
def test_pybind_bridge_links_a_split_module(tmp_path, pybind_venv):
  """The reported shape: a module WITH main.cpp grows a *.pybind.cpp
  whose TU has a qualified cross-module include. Before the fix,
  configure failed outright ('Target "emu" of type EXECUTABLE may not
  be linked into another target') and — with that half patched by hand
  — the include could not resolve, since the exe propagates nothing."""
  src = _scaffold(tmp_path)
  util = src / "util"
  util.mkdir()
  (util / "CMakeLists.txt").write_text("Init_submodule()\n")
  (util / "helper.hpp").write_text(
    "#pragma once\ninline int util_answer() { return 41; }\n")
  emu = src / "emu"
  emu.mkdir()
  (emu / "CMakeLists.txt").write_text("Init_submodule()\n")
  (emu / "engine.cpp").write_text("int engine_state() { return 1; }\n")
  (emu / "main.cpp").write_text(
    "int engine_state();\nint main() { return engine_state(); }\n")
  (emu / "emu.pybind.cpp").write_text(BRIDGE_CPP)
  (tmp_path / "_pyvenv").symlink_to(pybind_venv)
  build = tmp_path / "b"
  procs = _cmake(tmp_path,
    ["cmake", "-S", str(tmp_path), "-B", str(build), "-G", "Ninja",
     f"-DBUILDUTIL_PY={pybind_venv / 'bin' / 'python'}"],
    ["cmake", "--build", str(build)])
  for proc in procs:
    assert proc.returncode == 0, proc.stdout + proc.stderr
  bridged = list(build.rglob("emu*.so"))
  assert bridged, "no python extension was built:\n" + "\n".join(
    str(p) for p in build.rglob("*"))
