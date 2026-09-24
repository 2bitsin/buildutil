"""Test-lane modules (#132): support code the suites share, never shipped."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from buildutil import deposit
from buildutil.tests.test_packaging import _CppInfo, _recipe_module

PYSUPPORT = Path(deposit.__file__).resolve().parent / "pysupport"

pytestmark = pytest.mark.skipif(
  shutil.which("cmake") is None or shutil.which("ninja") is None or
  not (shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")),
  reason="needs cmake, ninja and a C++ compiler")

CFG = {"name": "demo", "cmake_option_prefix": "DEMO",
       "module_define_prefix": "DEM", "package_kind": "library"}

ROOT_CMAKE = """\
cmake_minimum_required(VERSION 3.25)
project(demo CXX)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
add_subdirectory(sources)
"""

# GoogleTest and google-benchmark stand in as one registry, present when a Require(... TEST) would be.
SOURCES_CMAKE = """\
if(BUILD_TESTING OR BUILD_BENCHMARKING)
  add_library(fake-gtest STATIC "${CMAKE_SOURCE_DIR}/fake/gtest.cpp")
  target_include_directories(fake-gtest PUBLIC "${CMAKE_SOURCE_DIR}/fake")
  add_library(GTest::gtest ALIAS fake-gtest)
  add_library(fake-gtest-main STATIC "${CMAKE_SOURCE_DIR}/fake/main.cpp")
  target_link_libraries(fake-gtest-main PUBLIC fake-gtest)
  add_library(GTest::gtest_main ALIAS fake-gtest-main)
  add_library(benchmark::benchmark_main ALIAS fake-gtest-main)
endif()
Scan_subdirectories()
"""

GTEST_H = """\
#pragma once
namespace testing {
struct Test { virtual ~Test() = default; virtual void TestBody() = 0; };
auto Register(char const* suite, char const* name) -> int;
auto RunAll(int argc, char** argv) -> int;
}
#define TEST_F(fixture, name) \\
  struct fixture##_##name : fixture { void TestBody() override; }; \\
  static int const fixture##_##name##_registered = \\
    ::testing::Register(#fixture, #name); \\
  void fixture##_##name::TestBody()
"""

GTEST_CPP = """\
#include <gtest/gtest.h>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
namespace testing {
static auto Names() -> std::vector<std::string>& {
  static std::vector<std::string> names;
  return names;
}
auto Register(char const* suite, char const* name) -> int {
  Names().push_back(std::string(suite) + "." + name);
  return 0;
}
auto RunAll(int argc, char** argv) -> int {
  for (int i = 1; i < argc; ++i) {
    if (std::strcmp(argv[i], "--gtest_list_tests") == 0) {
      for (auto const& full : Names()) {
        auto dot = full.find('.');
        std::printf("%s.\\n  %s\\n", full.substr(0, dot).c_str(),
                    full.substr(dot + 1).c_str());
      }
      return 0;
    }
    if (std::strncmp(argv[i], "--gtest_filter=", 15) == 0) {
      for (auto const& full : Names()) {
        if (full == argv[i] + 15) return 0;
      }
      return 1;
    }
  }
  return 0;
}
}
"""

GTEST_MAIN = """\
#include <gtest/gtest.h>
auto main(int argc, char** argv) -> int { return testing::RunAll(argc, argv); }
"""

RIG_HPP = """\
#pragma once
#include <gtest/gtest.h>
struct Rig : testing::Test { auto Answer() const -> int; };
"""


def _module(path: Path, cmake: str, files: dict) -> None:
  path.mkdir(parents=True)
  (path / "CMakeLists.txt").write_text(cmake)
  for name, text in files.items():
    (path / name).write_text(text)


def _suite(name: str) -> dict:
  return {f"{name}.cpp": f"auto {name}() -> int {{ return 1; }}\n",
          f"{name}.test.cpp": "#include <demo/support.test/rig.hpp>\n"
                              f"TEST_F(Rig, From_{name}) {{}}\n"}


def _tree(root: Path) -> Path:
  """Suites a and b link demo/support.test directly, c through a second .test module."""
  deposit.ensure(root, CFG)
  (root / "CMakeLists.txt").write_text(ROOT_CMAKE)
  fake = root / "fake" / "gtest"
  fake.mkdir(parents=True)
  (fake / "gtest.h").write_text(GTEST_H)
  (root / "fake" / "gtest.cpp").write_text(GTEST_CPP)
  (root / "fake" / "main.cpp").write_text(GTEST_MAIN)
  src = root / "sources"
  src.mkdir()
  (src / "CMakeLists.txt").write_text(SOURCES_CMAKE)
  _module(src / "demo" / "support.test", "Init_submodule()\n", {
    "rig.hpp": RIG_HPP,
    "rig.cpp": '#include "rig.hpp"\nauto Rig::Answer() const -> int { return 42; }\n',
    "shared.cpp": '#include "rig.hpp"\nTEST_F(Rig, Shared) {}\n'})
  _module(src / "demo" / "chain.test", "Init_submodule()\nLink_dependencies(support)\n",
          {"chained.cpp": "#include <demo/support.test/rig.hpp>\nTEST_F(Rig, Chained) {}\n"})
  _module(src / "a", "Init_submodule()\nLink_dependencies(TEST support BENCH support)\n",
          {**_suite("a"), "a.bench.cpp": "#include <demo/support.test/rig.hpp>\n"
                                         "TEST_F(Rig, Bench_a) {}\n"})
  _module(src / "b", "Init_submodule()\nLink_dependencies(TEST demo::support.test)\n",
          _suite("b"))
  _module(src / "c", "Init_submodule()\nLink_dependencies(TEST chain)\n",
          _suite("c"))
  return root


def _cmake(*argv):
  return subprocess.run(["cmake", *argv], capture_output=True, text=True)


def _switch(on: bool) -> str:
  return "ON" if on else "OFF"


def _configure(root: Path, build: str = "b", testing: bool = True, benches: bool = False):
  return _cmake("-S", str(root), "-B", str(root / build), "-G", "Ninja",
                f"-DBUILD_TESTING={_switch(testing)}", f"-DBUILD_BENCHMARKING={_switch(benches)}",
                f"-DBUILDUTIL_PY={sys.executable}", f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}")


def _built(root: Path, build: str = "b", testing: bool = True, benches: bool = False) -> Path:
  for done in (_configure(root, build, testing, benches),
               _cmake("--build", str(root / build))):
    assert done.returncode == 0, done.stdout + done.stderr
  return root / build


def _said(done) -> str:
  """cmake's error text with its word wrapping undone."""
  return " ".join(done.stderr.split())


def _listed(build: Path, label: str) -> str:
  done = subprocess.run(["ctest", "--test-dir", str(build), "-N", "-L", f"^{label}$"],
                        capture_output=True, text=True)
  assert done.returncode == 0, done.stdout + done.stderr
  return done.stdout


def test_every_suite_linking_it_runs_its_registrations(tmp_path):
  """a links it by leaf, b by its `demo::support.test` alias, c through chain.test."""
  build = _built(_tree(tmp_path))
  for suite in "ab":
    listed = _listed(build, suite)
    assert "Rig.Shared" in listed and f"Rig.From_{suite}" in listed, listed
  chained = _listed(build, "c")
  assert "Rig.Shared" in chained and "Rig.Chained" in chained, chained
  ran = subprocess.run(["ctest", "--test-dir", str(build), "--output-on-failure"],
                       capture_output=True, text=True)
  assert ran.returncode == 0, ran.stdout + ran.stderr


def test_an_obj_module_one_hop_away_reaches_the_suite(tmp_path):
  root = _tree(tmp_path)
  _module(root / "sources" / "plug.obj", "Init_submodule()\nLink_dependencies(GTest::gtest)\n",
          {"loaded.cpp": "#include <gtest/gtest.h>\nstruct Plug : testing::Test {};\n"
                         "TEST_F(Plug, Loaded) {}\n"})
  _module(root / "sources" / "d", "Init_submodule()\nLink_dependencies(plug)\n",
          {"d.cpp": "auto d() -> int { return 1; }\n",
           "d.test.cpp": "#include <gtest/gtest.h>\nstruct Own : testing::Test {};\n"
                         "TEST_F(Own, Case) {}\n"})
  assert "Plug.Loaded" in _listed(_built(root), "d")


def test_a_bench_reaching_it_runs_its_registrations_without_tests(tmp_path):
  build = _built(_tree(tmp_path), "benches", testing=False, benches=True)
  [bench] = build.rglob("a-benches")
  listed = subprocess.run([str(bench), "--gtest_list_tests"], capture_output=True,
                          text=True).stdout
  assert "Shared" in listed and "Bench_a" in listed, listed


def test_a_build_without_tests_or_benches_does_not_build_it(tmp_path):
  build = _built(_tree(tmp_path), "notests", testing=False)
  targets = _cmake("--build", str(build), "--target", "help").stdout
  assert "demo-support" not in targets and "demo-chain" not in targets, targets
  assert list(build.rglob("liba.a")), "the suites' modules still build"


@pytest.mark.parametrize("spelling", ["support", "demo-support", "demo::support.test"])
def test_a_positional_link_to_it_is_refused_naming_both(tmp_path, spelling):
  root = _tree(tmp_path)
  (root / "sources" / "a" / "CMakeLists.txt").write_text(
    f"Init_submodule()\nLink_dependencies({spelling})\n")
  for testing in (True, False):
    done = _configure(root, f"b{testing}", testing)
    assert done.returncode != 0
    assert "a links the .test module demo-support" in _said(done), done.stderr


def test_a_library_package_publishes_without_it(tmp_path):
  root = _tree(tmp_path)
  build = _built(root)
  prefix = tmp_path / "prefix"
  done = _cmake("--install", str(build), "--prefix", str(prefix))
  assert done.returncode == 0, done.stdout + done.stderr
  shipped = [str(p.relative_to(prefix)) for p in prefix.rglob("*") if p.is_file()]
  assert "liba.a" in shipped, shipped
  assert not [f for f in shipped if "support" in f or "chain" in f], shipped
  manifest = json.loads((prefix / "share/buildutil/buildutil-components.json").read_text())
  assert sorted(c["path"] for c in manifest["components"]) == ["a", "b", "c"]
  recipe_dir = tmp_path / "recipe"
  recipe_dir.mkdir()
  recipe = _recipe_module(recipe_dir, '[package]\nkind = "library"\nname = "demo"\n')
  instance = recipe.ProjectRecipe()
  instance.package_folder = str(prefix)
  instance.cpp_info = _CppInfo()
  instance.package_info()
  described = repr(instance.cpp_info)
  assert "support" not in described and "GTest" not in described, described


def test_a_test_case_inside_it_is_refused_by_name(tmp_path):
  root = _tree(tmp_path)
  (root / "sources/demo/support.test/oops.test.cpp").write_text("int x;\n")
  for testing in (True, False):
    done = _configure(root, f"b{testing}", testing)
    assert done.returncode != 0
    assert "oops.test.cpp" in _said(done) and "has no suite" in _said(done), done.stderr


def test_a_python_bridge_inside_it_is_refused_by_name(tmp_path):
  root = _tree(tmp_path)
  (root / "sources/demo/support.test/bridge.pybind.cpp").write_text("#error never compiled\n")
  done = _configure(root)
  assert done.returncode != 0
  assert "bridge.pybind.cpp" in _said(done) and "has no suite" in _said(done), done.stderr


def test_publish_symbols_on_it_is_refused(tmp_path):
  root = _tree(tmp_path)
  (root / "sources/demo/support.test/CMakeLists.txt").write_text(
    "Init_submodule(PUBLISH_SYMBOLS)\n")
  done = _configure(root)
  assert done.returncode != 0
  assert "support.test is a .test module and calls Init_submodule(PUBLISH_SYMBOLS)" in _said(done), \
    done.stderr


def test_a_test_directory_with_a_cmakelists_inside_a_module_is_refused(tmp_path):
  root = _tree(tmp_path)
  _module(root / "sources" / "a" / "inner.test", "Init_submodule()\n", {})
  done = _configure(root)
  assert done.returncode != 0
  assert "inner.test holds a CMakeLists.txt" in _said(done), done.stderr


def test_a_test_tagged_group_is_refused(tmp_path):
  root = _tree(tmp_path)
  _module(root / "sources" / "kit.test" / "d", "Init_submodule()\n",
          {"d.cpp": "int d() { return 0; }\n"})
  done = _configure(root)
  assert done.returncode != 0
  assert "kit.test is tagged .test but holds no CMakeLists.txt" in _said(done), done.stderr
