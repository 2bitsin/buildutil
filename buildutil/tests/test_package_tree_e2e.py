"""What a packaged project's install tree holds -- which is what the
conan package ships, since `package()` is `cmake --install`.

Two kinds of file had no business in it: the module test and bench
executables, megabytes of GoogleTest apiece for programs ctest runs from
the BUILD tree, and the archive of a module with no sources of its own,
which holds no object and which `package_info()` then had to be taught to
ignore by name.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from buildutil import deposit

PYSUPPORT = Path(deposit.__file__).resolve().parent / "pysupport"

e2e = pytest.mark.skipif(
  shutil.which("cmake") is None or shutil.which("ninja") is None,
  reason="needs cmake and ninja")

CFG = {"name": "demo", "cmake_option_prefix": "DEMO",
       "module_define_prefix": "DEM", "package_kind": "library"}

ROOT_CMAKE = """\
cmake_minimum_required(VERSION 3.25)
project(demo CXX)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
add_subdirectory(sources)
"""

# The harnesses stand in as empty imported targets: what is under test is
# which artifacts are INSTALLED, so the suites carry their own main and
# link nothing.
SOURCES_CMAKE = """\
add_library(GTest::gtest_main INTERFACE IMPORTED)
add_library(benchmark::benchmark_main INTERFACE IMPORTED)
Scan_subdirectories()
"""


def _tree(root: Path) -> Path:
  """A library package of two modules: one compiling a source and
  carrying a suite and a bench, one with nothing of its own."""
  deposit.ensure(root, CFG)
  (root / "CMakeLists.txt").write_text(ROOT_CMAKE)
  src = root / "sources"
  (src / "widget").mkdir(parents=True)
  (src / "CMakeLists.txt").write_text(SOURCES_CMAKE)
  (src / "widget" / "CMakeLists.txt").write_text("Init_submodule()\n")
  (src / "widget" / "thing.cpp").write_text("int thing() { return 0; }\n")
  (src / "widget" / "thing.test.cpp").write_text("int main() { return 0; }\n")
  (src / "widget" / "thing.bench.cpp").write_text("int main() { return 0; }\n")
  (src / "wrapper").mkdir(parents=True)
  (src / "wrapper" / "CMakeLists.txt").write_text("Init_submodule()\n")
  (src / "wrapper" / "api.hpp").write_text("inline int api() { return 1; }\n")
  return root


def _run(*argv):
  done = subprocess.run(list(argv), capture_output=True, text=True)
  assert done.returncode == 0, done.stdout + done.stderr
  return done


def _listing(prefix: Path) -> list[str]:
  return sorted(str(p.relative_to(prefix)) for p in prefix.rglob("*")
                if p.is_file())


def _packaged(root: Path, *component):
  """The prefix the scaffolded package() fills, and what is in it."""
  build, prefix = root / "b", root / f"pkg{'-'.join(component)}"
  _run("cmake", "-S", str(root), "-B", str(build), "-G", "Ninja",
       "-DBUILD_TESTING=ON", "-DBUILD_BENCHMARKING=ON",
       f"-DBUILDUTIL_PY={sys.executable}",
       f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}")
  _run("cmake", "--build", str(build))
  _run("cmake", "--install", str(build), "--prefix", str(prefix),
       *([f"--component={component[0]}"] if component else []))
  return build, prefix, _listing(prefix)


@e2e
def test_the_package_does_not_ship_the_module_test_binaries(tmp_path):
  build, _, shipped = _packaged(_tree(tmp_path))
  assert not [f for f in shipped if "tests" in f or "benches" in f], shipped
  assert list(build.rglob("widget-tests")), (
    "the suite still has to be there to RUN -- it is a build-tree artifact")


@e2e
def test_a_module_with_no_sources_ships_no_empty_archive(tmp_path):
  """cmake needs a source per library, so a module with none gets a
  generated empty TU -- and the archive built from it holds no object.
  Nothing links it, and package_info() advertises it by name."""
  _, _, shipped = _packaged(_tree(tmp_path))
  assert "libwidget.a" in shipped, shipped
  assert "libwrapper.a" not in shipped, shipped
  assert "include/wrapper/api.hpp" in shipped, (
    "the header-only module still ships its headers")


@e2e
def test_the_suites_install_on_request(tmp_path):
  """Not gone, excluded: a project that wants the binaries somewhere asks
  for the component by name."""
  _, _, shipped = _packaged(_tree(tmp_path), "tests")
  assert shipped == ["widget-tests"], shipped


def test_the_local_install_tree_still_gets_the_suites(monkeypatch):
  """The package must not ship them; the developer's `_install/` must --
  the generated vscode launch configs debug the installed binary."""
  from buildutil import engine
  calls = []
  monkeypatch.setattr(engine.subprocess, "check_call", calls.append)
  engine._install_tree(Path("b"), tests=True, bench=True)
  assert [c[-1] for c in calls[1:]] == ["tests", "benches"]
  assert "--component" not in calls[0]


def test_a_build_without_suites_asks_for_no_components(monkeypatch):
  from buildutil import engine
  calls = []
  monkeypatch.setattr(engine.subprocess, "check_call", calls.append)
  engine._install_tree(Path("b"), tests=False, bench=False)
  assert len(calls) == 1
