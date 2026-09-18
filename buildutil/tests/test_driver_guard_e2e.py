"""The direct-invocation guards (owner ruling): cmake, ninja, ctest and
conan invoked by hand in a buildutil project must FAIL WITH AN
EXPLANATION — the driver exports BUILDUTIL=<version> to every child it
runs, and the deposit's guards check for it. Each refusal is proven by
actually invoking the tool without the marker; each pass-through by
invoking it with the marker (which the whole test session carries — see
conftest.py)."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from buildutil import deposit

CFG = {"cmake_option_prefix": "ACME", "module_define_prefix": "ACM"}

pytestmark = pytest.mark.skipif(
  shutil.which("cmake") is None or shutil.which("ninja") is None or
  not (shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")),
  reason="needs cmake, ninja and a C++ compiler")

ROOT_CMAKE = """\
cmake_minimum_required(VERSION 3.25)
project(guarded CXX)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
include(CTest)
include(GoogleTest)
add_subdirectory(sources)
"""

EXPLANATION = "controlled by buildutil"


def _unmarked():
  env = dict(os.environ)
  env.pop("BUILDUTIL", None)
  return env


def _project(root):
  deposit.ensure(root, CFG)
  (root / "CMakeLists.txt").write_text(ROOT_CMAKE)
  src = root / "sources"
  src.mkdir()
  (src / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  mod = src / "hello"
  mod.mkdir()
  (mod / "CMakeLists.txt").write_text("Init_submodule()\n")
  (mod / "main.cpp").write_text("int main() { return 0; }\n")
  return root


def _configure(root, env=None):
  return subprocess.run(
    ["cmake", "-S", str(root), "-B", str(root / "b"), "-G", "Ninja"],
    capture_output=True, text=True, env=env)


def test_bare_cmake_configure_is_refused_with_the_explanation(tmp_path):
  _project(tmp_path)
  proc = _configure(tmp_path, env=_unmarked())
  assert proc.returncode != 0
  assert EXPLANATION in proc.stderr


def test_driven_configure_passes_via_flag_alone(tmp_path):
  """The driver's -DBUILDUTIL_PY is sufficient on its own — the guard
  must not additionally demand the env marker at configure time."""
  _project(tmp_path)
  proc = subprocess.run(
    ["cmake", "-S", str(tmp_path), "-B", str(tmp_path / "b"), "-G", "Ninja",
     f"-DBUILDUTIL_PY={sys.executable}"],
    capture_output=True, text=True, env=_unmarked())
  assert proc.returncode == 0, proc.stdout + proc.stderr


def test_bare_ninja_build_is_refused_but_driven_build_passes(tmp_path):
  _project(tmp_path)
  cfg = _configure(tmp_path)          # session env carries the marker
  assert cfg.returncode == 0, cfg.stdout + cfg.stderr
  bare = subprocess.run(["ninja", "-C", str(tmp_path / "b")],
                        capture_output=True, text=True, env=_unmarked())
  assert bare.returncode != 0
  assert EXPLANATION in bare.stdout + bare.stderr
  driven = subprocess.run(["ninja", "-C", str(tmp_path / "b")],
                          capture_output=True, text=True)
  assert driven.returncode == 0, driven.stdout + driven.stderr


def test_bare_ctest_is_refused_but_marked_ctest_passes(tmp_path):
  root = _project(tmp_path)
  gtest = root / "sources" / "hello" / "check.test.py"
  # a python suite registers through the same fixture wiring as gtest
  # without dragging GoogleTest into the fixture build
  gtest.write_text("def test_ok():\n  assert True\n")
  # the registered runner is the project venv's python — route it to
  # THIS interpreter via an exec script, not a symlink: a symlinked
  # venv python loses its pyvenv.cfg discovery and with it pytest
  venv_bin = root / "_pyvenv" / "bin"
  venv_bin.mkdir(parents=True)
  shim = venv_bin / "python"
  shim.write_text(f"#!/bin/sh\nexec {sys.executable} \"$@\"\n")
  shim.chmod(0o755)
  cfg = _configure(root)
  assert cfg.returncode == 0, cfg.stdout + cfg.stderr
  bare = subprocess.run(
    ["ctest", "--test-dir", str(root / "b"), "--output-on-failure"],
    capture_output=True, text=True, env=_unmarked())
  assert bare.returncode != 0
  assert EXPLANATION in bare.stdout + bare.stderr
  marked = subprocess.run(
    ["ctest", "--test-dir", str(root / "b"), "--output-on-failure"],
    capture_output=True, text=True)
  assert marked.returncode == 0, marked.stdout + marked.stderr
  assert "buildutil-driver-guard" in marked.stdout


def test_conanfile_validate_refuses_without_the_marker(tmp_path):
  """The scaffolded conanfile's validate() — executed, not grepped: a
  stub conan module stands in for the real one so the guard logic runs
  in any environment."""
  from buildutil import initcmd
  template = (initcmd.PROJECT_TEMPLATES / "conanfile.py").read_text()
  driver = tmp_path / "check.py"
  driver.write_text("""
import os, sys, types
conan = types.ModuleType("conan")
class ConanFile: pass
conan.ConanFile = ConanFile
tools = types.ModuleType("conan.tools.cmake")
for n in ("CMakeDeps", "CMakeToolchain", "cmake_layout"):
  setattr(tools, n, object)
errors = types.ModuleType("conan.errors")
class ConanInvalidConfiguration(Exception): pass
errors.ConanInvalidConfiguration = ConanInvalidConfiguration
sys.modules.update({"conan": conan, "conan.tools": types.ModuleType("conan.tools"),
                    "conan.tools.cmake": tools, "conan.errors": errors})
ns = {"__file__": sys.argv[1]}   # the template reads [package] beside itself
exec(open(sys.argv[1]).read(), ns)
recipe = ns[next(k for k, v in ns.items()
                 if isinstance(v, type) and issubclass(v, ConanFile)
                 and v is not ConanFile)]()
os.environ.pop("BUILDUTIL", None)
try:
  recipe.validate()
except ConanInvalidConfiguration as e:
  assert "controlled by buildutil" in str(e)
else:
  sys.exit("validate() let a bare conan through")
os.environ["BUILDUTIL"] = "1"
recipe.validate()
print("GUARD-OK")
""")
  recipe = tmp_path / "conanfile.py"
  recipe.write_text(template.replace("@NAME@", "t")
                    .replace("@CONAN_NAME@", "t"))
  proc = subprocess.run([sys.executable, str(driver), str(recipe)],
                        capture_output=True, text=True)
  assert proc.returncode == 0, proc.stdout + proc.stderr
  assert "GUARD-OK" in proc.stdout
