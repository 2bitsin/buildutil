"""[options] against real cmake: the force include, and a scaffolded build.

The compile line is the only place the injection shows, so these read the
real one out of compile_commands.json rather than asserting on cmake text,
and the scaffolded half runs the binary: a macro that reaches the header
and not the compiler would still pass every unit test.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from buildutil import deposit, initcmd

CFG = {"name": "demo", "cmake_option_prefix": "DEMO",
       "module_define_prefix": "DM", "options": {"contracts": True}}
PYSUPPORT = Path(deposit.__file__).resolve().parent / "pysupport"

pytestmark = pytest.mark.skipif(
  shutil.which("cmake") is None or shutil.which("ninja") is None or
  not (shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")),
  reason="needs cmake, ninja and a C++ compiler")

ROOT = """\
cmake_minimum_required(VERSION 3.25)
project(demo CXX)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
add_subdirectory(sources)
"""

# gtest and google-benchmark stand in as empty imported targets: the
# compile LINE is what is under test, and it exists after configure --
# these trees are never linked.
SOURCES = """\
add_library(GTest::gtest_main INTERFACE IMPORTED)
add_library(benchmark::benchmark_main INTERFACE IMPORTED)
Scan_subdirectories()
"""


def _tree(root: Path) -> Path:
  deposit.ensure(root, CFG)
  (root / "CMakeLists.txt").write_text(ROOT)
  module = root / "sources" / "hello"
  module.mkdir(parents=True)
  (root / "sources" / "CMakeLists.txt").write_text(SOURCES)
  (module / "CMakeLists.txt").write_text("Init_submodule()\n")
  (module / "hello.cpp").write_text("int hello() { return DEMO_CONTRACTS; }\n")
  (module / "main.cpp").write_text("int hello();\nint main() { return hello(); }\n")
  (module / "hello.test.cpp").write_text("int probe() { return DEMO_CONTRACTS; }\n")
  (module / "hello.bench.cpp").write_text("int gauge() { return DEMO_CONTRACTS; }\n")
  return root


def _configure(root: Path, *extra: str) -> Path:
  done = subprocess.run(
    ["cmake", "-S", str(root), "-B", str(root / "b"), "-G", "Ninja",
     "-DBUILD_TESTING=ON", "-DBUILD_BENCHMARKING=ON",
     f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}", *extra],
    capture_output=True, text=True)
  assert done.returncode == 0, done.stdout + done.stderr
  return root / "b"


def _compile_lines(build_dir: Path) -> dict[str, str]:
  entries = json.loads((build_dir / "compile_commands.json").read_text())
  return {Path(entry["file"]).name:
          entry.get("command") or " ".join(entry["arguments"])
          for entry in entries}


def test_every_target_of_the_project_is_handed_the_header(tmp_path):
  """The module library, its executable, its tests and its benches --
  one directory-scope block covers all four, with nothing declared."""
  build_dir = _configure(_tree(tmp_path))
  header = build_dir / "generated" / "demo" / "options.hpp"
  assert header.is_file()
  lines = _compile_lines(build_dir)
  assert set(lines) == {"hello.cpp", "main.cpp", "hello.test.cpp",
                        "hello.bench.cpp"}
  for name, command in lines.items():
    assert f"-include {header}" in command, f"{name}: {command}"


def test_the_msvc_lane_gets_the_spelling_cl_understands(tmp_path):
  """/FI is the same block's other arm; a Linux box cannot run it, so
  the rendered machinery is what says so."""
  machinery = (deposit.ensure(tmp_path, CFG) / "buildutil.cmake").read_text()
  assert '"$<$<AND:${preprocessed},${cl}>:/FI>"' in machinery


def _plain(root: Path) -> Path:
  """The same tree with nothing declared: no header, no force include."""
  tree = _tree(root)
  deposit.ensure(tree, {**CFG, "options": {}})
  module = tree / "sources" / "hello"
  for name in ("hello.cpp", "hello.test.cpp", "hello.bench.cpp"):
    (module / name).write_text("int probe() { return 0; }\n")
  return tree


def test_a_project_without_options_is_handed_nothing(tmp_path):
  build_dir = _configure(_plain(tmp_path))
  for command in _compile_lines(build_dir).values():
    assert "-include" not in command, command
  assert not (build_dir / "generated" / "demo").exists()


# ----------------------------------------------- the scaffolded project --

MAIN = """\
#include <cstdio>

#if DEMO_CONTRACTS
#  define CHECK(condition) ((condition) ? 0 : 1)
#else
#  define CHECK(condition) 0
#endif

int main()
{
  std::printf("contracts %d check %d\\n", DEMO_CONTRACTS, CHECK(false));
  return 0;
}
"""


def _scaffold(root: Path, monkeypatch) -> Path:
  monkeypatch.chdir(root)
  initcmd.main(["--name", "demo", "--cmake-prefix", "DEMO", "--no-package"])
  with (root / "buildutil.toml").open("a") as toml:
    toml.write("\n[options]\ncontracts = true\n")
  (root / "sources" / "demo" / "hello" / "main.cpp").write_text(MAIN)
  import buildutil.config as config
  monkeypatch.setattr(config, "REPO_ROOT", root)
  monkeypatch.setattr(config, "HAVE_PROJECT", True)
  deposit.ensure(root, config._load_project())
  return root


def _run(root: Path, *extra: str) -> str:
  done = subprocess.run(
    ["cmake", "-S", str(root), "-B", str(root / "b"), "-G", "Ninja",
     f"-DCMAKE_MODULE_PATH={deposit.cmake_dir(root)}",
     "-DBUILD_TESTING=OFF", "-DBUILD_BENCHMARKING=OFF",
     f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}", *extra],
    capture_output=True, text=True)
  assert done.returncode == 0, done.stdout + done.stderr
  built = subprocess.run(["cmake", "--build", str(root / "b")],
                         capture_output=True, text=True)
  assert built.returncode == 0, built.stdout + built.stderr
  ran = subprocess.run([str(root / "b" / "bin" / "hello")],
                       capture_output=True, text=True)
  assert ran.returncode == 0, ran.stdout + ran.stderr
  return ran.stdout.strip()


def test_a_scaffolded_project_reads_the_macro_and_includes_nothing_for_it(
    tmp_path, monkeypatch):
  """`buildutil init`, one [options] line, and a source that says `#if
  DEMO_CONTRACTS` -- no include, no cmake, no declaration anywhere."""
  root = _scaffold(tmp_path, monkeypatch)
  assert "options.hpp" not in (
    root / "sources" / "demo" / "hello" / "main.cpp").read_text()
  assert _run(root) == "contracts 1 check 1"


def test_the_other_value_reconfigures_the_same_build_tree(tmp_path,
                                                          monkeypatch):
  """One build dir, built twice: a changed option re-runs cmake there,
  rewrites the header, and the binary comes out the other way. A fresh
  tree per value would have proved nothing about the second build."""
  root = _scaffold(tmp_path, monkeypatch)
  header = root / "b" / "generated" / "demo" / "options.hpp"
  assert _run(root) == "contracts 1 check 1"
  assert "#define DEMO_CONTRACTS 1" in header.read_text()
  assert _run(root, "-DDEMO_OPTION_CONTRACTS=OFF") == "contracts 0 check 0"
  assert "#define DEMO_CONTRACTS 0" in header.read_text()
