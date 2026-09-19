"""PUBLISH_SYMBOLS has to reach the COMPILE line, not only the link. The
root here carries no visibility flag, like the scaffold: a project that
adds one masks the defect, since cmake then drops the machinery's
duplicate -fvisibility=hidden and the opt-out ends up last.
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
project(published CXX)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
add_subdirectory(sources)
"""

# nothing annotated: PUBLISH_SYMBOLS is the only thing that can carry
# core_answer across the shared boundary
CORE_IMPL = "int core_answer() { return 42; }\n"

TOOL_MAIN = """\
extern int core_answer();
int main() { return core_answer() == 42 ? 0 : 1; }
"""


def _tree(tmp_path, module_init):
  deposit.ensure(tmp_path, CFG)
  (tmp_path / "CMakeLists.txt").write_text(ROOT_CMAKE)
  src = tmp_path / "sources"
  src.mkdir()
  (src / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  core = src / "ser" / "core"
  core.mkdir(parents=True)
  (core / "CMakeLists.txt").write_text(module_init)
  (core / "impl.cpp").write_text(CORE_IMPL)
  tool = src / "tool"
  tool.mkdir()
  (tool / "CMakeLists.txt").write_text(
    "Init_submodule()\nLink_dependencies(ser-core)\n")
  (tool / "main.cpp").write_text(TOOL_MAIN)
  return tmp_path


def _steps(tmp_path):
  return (["cmake", "-S", str(tmp_path), "-B", str(tmp_path / "b"), "-G",
           "Ninja", "-DBUILDUTIL_MODULE_LINKAGE=shared"],
          ["cmake", "--build", str(tmp_path / "b")],
          ["cmake", "--install", str(tmp_path / "b"),
           "--prefix", str(tmp_path / "prefix")])


def _build(tmp_path) -> subprocess.CompletedProcess:
  for command in _steps(tmp_path):
    done = subprocess.run(command, capture_output=True, text=True)
    if done.returncode != 0:
      return done
  return done


def test_a_published_module_links_and_runs(tmp_path):
  """A flag assertion is what missed this, so the proof is a link and a
  run: the app resolves an unannotated symbol out of the shared module,
  and the installed binary does it again from the mirror."""
  built = _build(_tree(tmp_path, "Init_submodule(PUBLISH_SYMBOLS)\n"))
  assert built.returncode == 0, built.stdout + built.stderr
  ran = subprocess.run([str(tmp_path / "prefix" / "tool")],
                       capture_output=True)
  assert ran.returncode == 0, ran.stderr


def test_the_published_module_compiles_at_default_visibility(tmp_path):
  """The half nothing checked: -rdynamic on the link said published while
  the compile said hidden."""
  _tree(tmp_path, "Init_submodule(PUBLISH_SYMBOLS)\n")
  subprocess.run(_steps(tmp_path)[0], capture_output=True, text=True)
  flags = [line for line in (tmp_path / "b" / "build.ninja").read_text(
  ).splitlines() if "FLAGS = " in line and "-fvisibility=default" in line]
  assert flags, "the hatch does not lift the visibility default"
  assert not [line for line in flags if "-fvisibility=hidden" in line], (
    "-fvisibility=hidden reaches the published target:\n" + "\n".join(flags))


def test_an_unpublished_module_still_hides_its_symbols(tmp_path):
  """The hatch has to mean something: without it the same unannotated
  symbol must not cross the boundary."""
  built = _build(_tree(tmp_path, "Init_submodule()\n"))
  assert built.returncode != 0, "an unannotated symbol was exported anyway"
  assert "core_answer" in built.stdout + built.stderr
