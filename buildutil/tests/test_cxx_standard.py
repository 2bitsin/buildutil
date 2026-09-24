"""The C++ standard: [project] cxx_standard and Init_submodule(STANDARD n) (#144)."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from buildutil import deposit, engine, reflect
from buildutil.config import cmake_cache_entry

PKG_PARENT = str(Path(__file__).resolve().parents[2])
PYSUPPORT = Path(deposit.__file__).resolve().parent / "pysupport"
CFG = {"name": "demo", "cmake_option_prefix": "DEMO", "module_define_prefix": "DM"}

e2e = pytest.mark.skipif(
  shutil.which("cmake") is None or shutil.which("ninja") is None or
  not (shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")),
  reason="needs cmake, ninja and a C++ compiler")

# gtest, google-benchmark and pybind11 stand in as empty targets: what is
# under test exists after configure, and these trees are never linked.
SOURCES = """\
add_library(GTest::gtest INTERFACE IMPORTED)
add_library(GTest::gtest_main INTERFACE IMPORTED)
add_library(benchmark::benchmark_main INTERFACE IMPORTED)
add_library(pybind11::module INTERFACE IMPORTED)
function(pybind11_add_module name)
  add_library(${name} MODULE ${ARGN})
endfunction()
Scan_subdirectories()
"""

# The standard each built target chose, dumped once the whole tree is read.
DUMP = """\
function(dump_standards dir)
  get_property(targets DIRECTORY "${dir}" PROPERTY BUILDSYSTEM_TARGETS)
  foreach(target IN LISTS targets)
    get_target_property(type ${target} TYPE)
    if(NOT type MATCHES "UTILITY|INTERFACE")
      get_target_property(standard ${target} CXX_STANDARD)
      get_target_property(options ${target} COMPILE_OPTIONS)
      file(APPEND "${CMAKE_BINARY_DIR}/standards.txt"
           "${target}|${standard}|${options}\\n")
    endif()
  endforeach()
  get_property(children DIRECTORY "${dir}" PROPERTY SUBDIRECTORIES)
  foreach(child IN LISTS children)
    dump_standards("${child}")
  endforeach()
endfunction()
file(WRITE "${CMAKE_BINARY_DIR}/standards.txt" "")
cmake_language(DEFER DIRECTORY "${CMAKE_SOURCE_DIR}" CALL dump_standards "${CMAKE_SOURCE_DIR}")
"""

# cmake listing no cxx_std_26 for the compiler is exactly a lane capped at 23.
NO_26 = "list(REMOVE_ITEM CMAKE_CXX_COMPILE_FEATURES cxx_std_26)\n"

# cl cannot run here: the machinery reads the compiler id once, at include,
# so the tree claims cl for that line alone and cmake keeps the real one.
AS_CL = ('set(_real_id "${CMAKE_CXX_COMPILER_ID}")\nset(CMAKE_CXX_COMPILER_ID MSVC)\n',
         'set(CMAKE_CXX_COMPILER_ID "${_real_id}")\n')


def _probe(root: Path) -> subprocess.CompletedProcess:
  code = "import buildutil.config as c; print(c.PROJECT['cxx_standard'])"
  env = {**os.environ, "PYTHONPATH": PKG_PARENT}
  env.pop("BUILDUTIL_ROOT", None)
  return subprocess.run([sys.executable, "-c", code], cwd=root, env=env,
                        text=True, capture_output=True)


@pytest.mark.parametrize("toml,expected", [
  ("", "None"), ("[project]\n", "None"),
  ("[project]\ncxx_standard = 20\n", "20"),
  ("[project]\ncxx_standard = 23\n", "23"),
  ("[project]\ncxx_standard = 26\n", "26"),
])
def test_the_manifest_accepts_the_three_standards(tmp_path, toml, expected):
  (tmp_path / "buildutil.toml").write_text(toml)
  done = _probe(tmp_path)
  assert done.returncode == 0, done.stderr
  assert done.stdout.strip() == expected


@pytest.mark.parametrize("value", ["17", "29", "23.0", '"23"', "true", "[23]"])
def test_the_manifest_refuses_anything_else_naming_the_three(tmp_path, value):
  (tmp_path / "buildutil.toml").write_text(f"[project]\ncxx_standard = {value}\n")
  done = _probe(tmp_path)
  assert done.returncode != 0
  assert "buildutil.toml: [project] cxx_standard" in done.stderr
  assert "(accepted: 20, 23, 26)" in done.stderr


def test_the_rendered_machinery_carries_the_declaration(tmp_path):
  text = (deposit.ensure(tmp_path, {**CFG, "cxx_standard": 23}) / "buildutil.cmake").read_text()
  assert 'set(_buildutil_project_cxx_standard "23")' in text
  assert 'set(_buildutil_cxx_standards "20;23;26")' in text


def test_an_undeclared_project_renders_no_standard(tmp_path):
  text = (deposit.ensure(tmp_path, CFG) / "buildutil.cmake").read_text()
  assert 'set(_buildutil_project_cxx_standard "")' in text


@pytest.mark.parametrize("compiler,version,cppstd", [
  ("gcc", "13", "23"), ("gcc", "14", "26"), ("clang", "16", "23"),
  ("clang", "17", "26"), ("apple-clang", "15", "23"), ("apple-clang", "16", "26"),
  ("msvc", "194", "23"),
])
def test_the_lane_cppstd_follows_conans_table(monkeypatch, compiler, version, cppstd):
  _no_cross_lane(monkeypatch)
  monkeypatch.setattr(engine, "_detect_compiler", lambda: (compiler, version))
  assert engine._detect_settings("Release")["compiler.cppstd"] == cppstd


def _no_cross_lane(monkeypatch, live: str = ""):
  for lane in ("_emscripten_live", "_wine_msvc_live", "_osxcross_live"):
    monkeypatch.setattr(engine, lane, lambda lane=lane: lane == live)


@pytest.mark.parametrize("lane,version_of,version,cppstd", [
  ("_emscripten_live", "_emscripten_clang_version", "16", "23"),
  ("_emscripten_live", "_emscripten_clang_version", "20", "26"),
  ("_wine_msvc_live", "_wine_msvc_version", "194", "23"),
  ("_osxcross_live", "_osxcross_clang_version", "15", "23"),
  ("_osxcross_live", "_osxcross_clang_version", "16", "26"),
])
def test_every_cross_lane_takes_the_same_table(monkeypatch, lane, version_of, version, cppstd):
  _no_cross_lane(monkeypatch, lane)
  monkeypatch.setattr(engine, version_of, lambda: version)
  assert engine._detect_settings("Release")["compiler.cppstd"] == cppstd


def test_a_compiler_outside_the_table_is_refused():
  with pytest.raises(SystemExit, match="no compiler.cppstd rule for compiler 'icx'"):
    engine._lane_cppstd("icx", "2025")


# name -> (CMakeLists, files): one module of every kind the machinery builds
KINDS = {
  "hello": ("Init_submodule()\n", ["hello.cpp", "main.cpp", "hello.test.cpp",
                                   "hello.bench.cpp", "hello.pybind.cpp"]),
  "plug": ("Init_submodule()\n", ["main.so.cpp"]),
  "objs.obj": ("Init_submodule()\n", ["objs.cpp"]),
  "support.test": ("Init_submodule()\n", ["support.cpp"]),
  "snake": ("Init_python_module()\n", ["snake.cpp", "snake.test.cpp"]),
}

# the targets those kinds make, as the dump names them
EVERY_TARGET = {"hello", "hello-lib", "hello-tests", "hello-benches",
                "hello-pybind", "plug", "objs", "support", "snake",
                "snake-tests"}


def _tree(root: Path, cfg: dict, modules: dict | None = None,
          before: str = "", after: str = "") -> Path:
  deposit.ensure(root, cfg)
  (root / "CMakeLists.txt").write_text(
    "cmake_minimum_required(VERSION 3.25)\nproject(demo CXX)\n" + before +
    'list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")\n'
    "include(buildutil)\n" + after + DUMP + "add_subdirectory(sources)\n")
  (root / "sources").mkdir()
  (root / "sources" / "CMakeLists.txt").write_text(SOURCES)
  for name, (init, files) in (modules or KINDS).items():
    module = root / "sources" / name
    module.mkdir()
    (module / "CMakeLists.txt").write_text(init)
    for index, file in enumerate(files):
      body = "int main() { return 0; }\n" if file == "main.cpp" else f"int f{index}() {{ return 0; }}\n"
      (module / file).write_text(body)
  return root


def _configure(root: Path, *extra: str) -> subprocess.CompletedProcess:
  return subprocess.run(
    ["cmake", "-S", str(root), "-B", str(root / "b"), "-G", "Ninja",
     "-DBUILD_TESTING=ON", "-DBUILD_BENCHMARKING=ON", "-DDEMO_PYBIND_DIR=/stub",
     f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}", *extra],
    capture_output=True, text=True)


def _configured(root: Path, *extra: str) -> Path:
  done = _configure(root, *extra)
  assert done.returncode == 0, done.stdout + done.stderr
  return root


def _refused(root: Path, *extra: str) -> str:
  done = _configure(root, *extra)
  assert done.returncode != 0, done.stdout
  return " ".join(done.stderr.split())


def _standards(root: Path) -> dict[str, tuple[str, str]]:
  """target -> (CXX_STANDARD, COMPILE_OPTIONS) as the configure chose them."""
  rows = (root / "b" / "standards.txt").read_text().splitlines()
  return {target: (standard, options) for target, standard, options
          in (row.split("|") for row in rows) if target in EVERY_TARGET}


def _compile_lines(root: Path) -> dict[str, str]:
  """Each compiled source, as module/file, with its compile line."""
  entries = json.loads((root / "b" / "compile_commands.json").read_text())
  return {Path(entry["file"]).relative_to(root / "sources").as_posix():
          entry.get("command") or " ".join(entry["arguments"])
          for entry in entries if "/sources/" in entry["file"]}


def _cached_standard(root: Path) -> str | None:
  return cmake_cache_entry(root / "b" / "CMakeCache.txt", "BUILDUTIL_CXX_STANDARD")


@e2e
def test_the_project_value_reaches_every_kind_of_target_over_the_lane(tmp_path):
  root = _configured(_tree(tmp_path, {**CFG, "cxx_standard": 23}), "-DCMAKE_CXX_STANDARD=26")
  standards = _standards(root)
  assert set(standards) == EVERY_TARGET
  assert {standard for standard, _ in standards.values()} == {"23"}
  lines = _compile_lines(root)
  assert len(lines) == sum(len(files) for _, files in KINDS.values())
  for name, command in lines.items():
    assert "-std=c++23" in command, f"{name}: {command}"
    assert "c++26" not in command and "gnu++" not in command, f"{name}: {command}"
  assert _cached_standard(root) == "23"


@e2e
def test_a_module_override_wins_for_its_own_targets_only(tmp_path):
  modules = {**KINDS, "hello": ("Init_submodule(STANDARD 20)\n", KINDS["hello"][1])}
  root = _configured(_tree(tmp_path, {**CFG, "cxx_standard": 23}, modules),
                     "-DCMAKE_CXX_STANDARD=26")
  for target, (standard, _) in _standards(root).items():
    assert standard == ("20" if target.startswith("hello") else "23"), target
  for name, command in _compile_lines(root).items():
    expected = "-std=c++20" if name.startswith("hello/") else "-std=c++23"
    assert expected in command, f"{name}: {command}"


@e2e
def test_an_undeclared_project_keeps_the_lane_standard(tmp_path):
  root = _configured(_tree(tmp_path, CFG), "-DCMAKE_CXX_STANDARD=26")
  assert {standard for standard, _ in _standards(root).values()} == {"26"}
  assert _cached_standard(root) == "26"


@e2e
def test_an_unknown_module_standard_is_refused_naming_the_three(tmp_path):
  modules = {"newer": ("Init_submodule(STANDARD 17)\n", ["newer.cpp"])}
  said = _refused(_tree(tmp_path, CFG, modules))
  assert "module 'newer': Init_submodule(STANDARD 17) is not a standard" in said
  assert "(accepted: 20, 23, 26)" in said


@e2e
def test_a_valueless_standard_is_refused_as_such(tmp_path):
  modules = {"newer": ("Init_submodule(STANDARD)\n", ["newer.cpp"])}
  said = _refused(_tree(tmp_path, CFG, modules))
  assert "module 'newer': Init_submodule(STANDARD) needs a value (accepted: 20, 23, 26)" in said


@e2e
@pytest.mark.parametrize("cfg,init,source", [
  ({**CFG, "cxx_standard": 26}, "Init_submodule()\n", "[project] cxx_standard = 26"),
  (CFG, "Init_submodule(STANDARD 26)\n", "module 'newer': Init_submodule(STANDARD 26)"),
])
def test_a_standard_past_the_lane_ceiling_fails_naming_its_source(tmp_path, cfg, init, source):
  said = _refused(_tree(tmp_path, cfg, {"newer": (init, ["newer.cpp"])}, before=NO_26))
  assert f"{source} asks for C++26, but this lane's compiler" in said
  assert "tops out at C++23" in said
  assert "buildutil never downgrades it" in said


@e2e
def test_the_lanes_own_standard_past_the_ceiling_names_the_lane(tmp_path):
  said = _refused(_tree(tmp_path, CFG, {"newer": ("Init_submodule()\n", ["newer.cpp"])},
                        before=NO_26), "-DCMAKE_CXX_STANDARD=26")
  assert "the lane's compiler.cppstd=26 asks for C++26" in said


def _directory_standard(root: Path, standard: int) -> Path:
  sources = root / "sources" / "CMakeLists.txt"
  sources.write_text(f"set(CMAKE_CXX_STANDARD {standard})\n" + sources.read_text())
  return root


@e2e
def test_a_directory_standard_still_counts_for_one_release_with_a_warning(tmp_path):
  root = _directory_standard(_tree(tmp_path, CFG), 23)
  done = _configure(root, "-DCMAKE_CXX_STANDARD=26")
  assert done.returncode == 0, done.stdout + done.stderr
  said = " ".join(done.stderr.split())
  assert said.count("set(CMAKE_CXX_STANDARD 23) in sources/") == 1
  assert "(use [project] cxx_standard): a directory's own C++ standard" in said
  assert {standard for standard, _ in _standards(root).values()} == {"23"}


@e2e
def test_a_directory_standard_past_the_ceiling_names_the_directory(tmp_path):
  root = _directory_standard(_tree(tmp_path, CFG, {"newer": ("Init_submodule()\n", ["newer.cpp"])},
                                   before=NO_26), 26)
  said = _refused(root, "-DCMAKE_CXX_STANDARD=23")
  assert ("set(CMAKE_CXX_STANDARD 26) in sources/newer (use [project] cxx_standard) "
          "asks for C++26") in said


@e2e
def test_a_declared_project_ignores_a_directory_standard(tmp_path):
  root = _directory_standard(_tree(tmp_path, {**CFG, "cxx_standard": 20}), 23)
  done = _configure(root, "-DCMAKE_CXX_STANDARD=26")
  assert done.returncode == 0, done.stdout + done.stderr
  assert "set(CMAKE_CXX_STANDARD" not in done.stderr
  assert {standard for standard, _ in _standards(root).values()} == {"20"}


@e2e
def test_cl_reaches_26_through_std_latest_past_its_cap_of_23(tmp_path):
  root = _configured(_tree(tmp_path, {**CFG, "cxx_standard": 26},
                           before=AS_CL[0] + NO_26, after=AS_CL[1]))
  for target, (standard, options) in _standards(root).items():
    assert standard == "23", target
    assert options.split(";").count("/std:c++latest") == 1, target


@e2e
def test_cl_at_23_gets_no_std_latest_from_buildutil(tmp_path):
  root = _configured(_tree(tmp_path, {**CFG, "cxx_standard": 23},
                           before=AS_CL[0], after=AS_CL[1]))
  for target, (standard, options) in _standards(root).items():
    assert standard == "23", target
    assert "/std:c++latest" not in options, target


@e2e
def test_the_cl_lane_default_is_26(tmp_path):
  root = _configured(_tree(tmp_path, CFG, before=AS_CL[0], after=AS_CL[1]),
                     "-DCMAKE_CXX_STANDARD=23")
  assert _cached_standard(root) == "26"
  for target, (standard, options) in _standards(root).items():
    assert (standard, "/std:c++latest" in options) == ("23", True), target


@pytest.mark.parametrize("cache,expected", [
  (None, reflect.FALLBACK_STD),
  ("BUILDUTIL_CXX_STANDARD:INTERNAL=\n", reflect.FALLBACK_STD),
  ("BUILDUTIL_CXX_STANDARD:INTERNAL=26\n", "c++26"),
  ("BUILDUTIL_CXX_STANDARD:INTERNAL=20\n", "c++20"),
])
def test_reflect_parses_at_the_standard_of_its_outputs_build_tree(
    tmp_path, monkeypatch, cache, expected):
  from buildutil import config
  monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
  output = tmp_path / "b" / "generated" / "demo" / "thing.reflect.hpp"
  output.parent.mkdir(parents=True)
  if cache is not None:
    (tmp_path / "b" / "CMakeCache.txt").write_text(cache)
  assert reflect.build_tree_std(output) == expected


def test_reflect_skips_a_cache_without_the_entry_and_stops_at_the_repo(tmp_path, monkeypatch):
  from buildutil import config
  repo = tmp_path / "repo"
  output = repo / "b" / "nested" / "generated" / "thing.reflect.hpp"
  output.parent.mkdir(parents=True)
  (tmp_path / "CMakeCache.txt").write_text("BUILDUTIL_CXX_STANDARD:INTERNAL=20\n")
  (repo / "b" / "nested" / "CMakeCache.txt").write_text("CMAKE_CXX_STANDARD:STRING=26\n")
  monkeypatch.setattr(config, "REPO_ROOT", repo)
  assert reflect.build_tree_std(output) == reflect.FALLBACK_STD
  (repo / "b" / "CMakeCache.txt").write_text("BUILDUTIL_CXX_STANDARD:INTERNAL=26\n")
  assert reflect.build_tree_std(output) == "c++26"


def _reflect_commands(root: Path, reflect_tree) -> list[str]:
  done = reflect_tree._configure(root)
  assert done.returncode == 0, done.stdout + done.stderr
  ninja = (root / "b" / "build.ninja").read_text()
  return [line for line in ninja.splitlines() if "buildutil.reflect generate" in line]


@e2e
def test_the_reflect_command_carries_the_standard_so_a_change_reruns_it(tmp_path):
  """The scan needs no clang, so the rule exists without the bindings."""
  from buildutil.tests import test_reflect_e2e as reflect_tree
  root = reflect_tree._tree(tmp_path, reflect_cfg={"cxx_standard": 20})
  before = _reflect_commands(root, reflect_tree)
  deposit.ensure(root, {**reflect_tree.CFG, "cxx_standard": 26}, ["reflect"])
  after = _reflect_commands(root, reflect_tree)
  assert before and all("--std c++20" in line for line in before)
  assert after and all("--std c++26" in line for line in after)
