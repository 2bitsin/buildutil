"""Require(... OPTIONS key=value ...): conan package options at the one
source of truth.

The failure this ends: a package whose conan defaults are wrong for the
project (spdlog without use_std_fmt, sqlite3 without FTS5) forced a
hand-edit of conanfile.py's default_options — a second file that had to
stay consistent with the Require() call it shadowed. OPTIONS rides the
Require call; the conanfile passes it on the requires itself, and the
cmake side validates the tokens so both parsers refuse the same lies.
"""
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"


def _conanfile_module(tmp_path):
  import importlib.util
  import types
  for name, attrs in (("conan", {"ConanFile": object}),
                      ("conan.tools", {}),
                      ("conan.errors", {
                        "ConanException": type("ConanException", (Exception,), {}),
                        "ConanInvalidConfiguration":
                          type("ConanInvalidConfiguration", (Exception,), {})}),
                      ("conan.tools.build", {"cross_building": lambda conanfile: False}),
                      ("conan.tools.scm", {"Version": object}),
                      ("conan.tools.cmake", {"CMakeDeps": object,
                                             "CMakeToolchain": object,
                                             "cmake_layout": lambda *a: None})):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
      setattr(mod, k, v)
    sys.modules.setdefault(name, mod)
  src = (TEMPLATES / "project" / "conanfile.py").read_text().replace(
    "@CONAN_NAME@", "x").replace("@CMAKE_OPTION_PREFIX@", "X")
  path = tmp_path / "cf.py"
  path.write_text(src)
  spec = importlib.util.spec_from_file_location("cf_options_test", path)
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  mod.cross_building = lambda conanfile: False
  return mod


def _requires(tmp_path, call: str, target_os: str = "Linux"):
  cf = _conanfile_module(tmp_path)
  root = tmp_path / "proj" / "sources"
  root.mkdir(parents=True)
  (root / "CMakeLists.txt").write_text(call)
  return cf._parse_requires(tmp_path / "proj", target_os)


def test_the_reported_call_parses_its_option(tmp_path):
  got = _requires(
    tmp_path, 'Require(spdlog VERSION ">=1.15" CONAN spdlog '
              'OPTIONS use_std_fmt=True)\n')
  assert got[0]["conan_name"] == "spdlog"
  assert got[0]["options"] == {"use_std_fmt": True}


def test_values_coerce_to_their_natural_types(tmp_path):
  got = _requires(
    tmp_path, 'Require(sqlite3 VERSION "3.45" OPTIONS enable_fts5=True '
              'shared=False max_column=2000 threadsafe=serialized)\n')
  assert got[0]["options"] == {
    "enable_fts5": True, "shared": False,
    "max_column": 2000, "threadsafe": "serialized"}


def test_options_compose_with_the_other_keywords(tmp_path):
  got = _requires(
    tmp_path, 'Require(Boost VERSION "1.84" CONAN boost '
              'OPTIONS header_only=True PLATFORM Linux Windows '
              'COMPONENTS system)\n')
  assert got[0]["options"] == {"header_only": True}


def test_a_lowercase_component_named_like_a_keyword_stays_a_value(tmp_path):
  """Flushed out by the compose test: keyword matching was
  case-insensitive, so `COMPONENTS system` (Boost.System!) read as the
  SYSTEM flag and silently dropped the package from the conan graph —
  while cmake's case-sensitive cmake_parse_arguments kept it as a
  component. The parsers must agree with cmake's semantics."""
  got = _requires(
    tmp_path, 'Require(Boost VERSION "1.84" CONAN boost '
              'COMPONENTS system test options)\n')
  assert len(got) == 1, "a lowercase component was read as a keyword"
  assert got[0]["conan_name"] == "boost"
  assert not got[0]["test"]


def test_a_token_without_equals_is_refused(tmp_path):
  with pytest.raises(ValueError, match="not key=value"):
    _requires(tmp_path, 'Require(fmt VERSION "10" OPTIONS shared)\n')


def test_no_options_key_stays_empty(tmp_path):
  got = _requires(tmp_path, 'Require(fmt VERSION "10.2.1")\n')
  assert got[0]["options"] == {}


# ---------------------------- COMPONENTS +/- as option shorthand --
# A boost-shaped recipe exposes one with_*/without_* option per library,
# and naming each of them key=value is a line of noise per library. The
# tokens ride the COMPONENTS list, where the library names already are.

def test_a_plus_token_is_a_with_option(tmp_path):
  got = _requires(
    tmp_path, 'Require(Boost VERSION "1.84" CONAN boost COMPONENTS +asio)\n')
  assert got[0]["options"] == {"with_asio": True}


def test_a_minus_token_is_a_without_option(tmp_path):
  got = _requires(
    tmp_path, 'Require(Boost VERSION "1.84" CONAN boost COMPONENTS -json)\n')
  assert got[0]["options"] == {"without_json": True}


def _extra(tmp_path, extra: str) -> dict:
  """The shared parser's reading of the tokens after VERSION."""
  import buildutil_requires
  return buildutil_requires.parse_extra(extra)


def test_bare_components_keep_their_meaning_beside_the_sugar(tmp_path):
  got = _extra(tmp_path, 'CONAN boost COMPONENTS system +asio '
                         'filesystem -charconv')
  assert got["components"] == ["system", "filesystem"]
  assert got["options"] == {"with_asio": True, "without_charconv": True}


def test_the_sugar_composes_with_explicit_options(tmp_path):
  got = _extra(tmp_path,
               'CONAN boost COMPONENTS system +asio OPTIONS header_only=False')
  assert got["options"] == {"with_asio": True, "header_only": False}
  assert got["components"] == ["system"]


def test_options_written_before_components_conflict_just_the_same(tmp_path):
  # the sugar is resolved after the whole call is read, so which keyword
  # the author wrote first cannot change the answer
  with pytest.raises(ValueError, match="both set an option"):
    _extra(tmp_path, 'CONAN boost OPTIONS with_asio=False COMPONENTS +asio')


def test_the_sugar_reaches_conan_on_the_requires_call(tmp_path):
  r, calls = _recorded_recipe(
    tmp_path, 'Require(Boost VERSION "1.84" CONAN boost '
              'COMPONENTS system +asio -json)\n')
  r.requirements()
  assert calls == [
    ("requires", "boost/1.84",
     {"options": {"with_asio": True, "without_json": True}}),
  ]


def test_a_bare_sign_names_no_option(tmp_path):
  with pytest.raises(ValueError, match="names no option"):
    _requires(tmp_path,
              'Require(Boost VERSION "1.84" CONAN boost COMPONENTS +)\n')


def test_both_signs_on_one_library_are_refused(tmp_path):
  with pytest.raises(ValueError, match=r"both '\+asio' and '-asio'"):
    _requires(tmp_path, 'Require(Boost VERSION "1.84" CONAN boost '
                        'COMPONENTS +asio -asio)\n')


@pytest.mark.parametrize("option", ["with_asio=False", "without_asio=True"])
def test_the_sugar_and_its_long_spelling_together_are_refused(
    tmp_path, option):
  # silent last-wins between two spellings of one option is the exact
  # failure OPTIONS exists to end
  with pytest.raises(ValueError, match="both set an option"):
    _extra(tmp_path, f'CONAN boost COMPONENTS +asio OPTIONS {option}')


def test_the_sugar_on_a_system_dep_is_refused(tmp_path):
  with pytest.raises(ValueError, match="SYSTEM is meaningless"):
    _requires(tmp_path,
              'Require(Boost VERSION "1.84" SYSTEM COMPONENTS +asio)\n')


# --------------------------------------------- PUBLIC (oxbox) --

def test_public_parses_and_defaults_off(tmp_path):
  got = _requires(
    tmp_path, 'Require(nlohmann_json VERSION "3.12.0" PUBLIC)\n'
              'Require(fmt VERSION "10.2.1")\n')
  assert got[0]["public"] and not got[1]["public"]


def test_a_lowercase_public_component_stays_a_value(tmp_path):
  """Same case-sensitivity contract as SYSTEM: `COMPONENTS public` is a
  component name, not the PUBLIC keyword."""
  got = _requires(
    tmp_path, 'Require(Boost VERSION "1.84" CONAN boost '
              'COMPONENTS public system)\n')
  assert not got[0]["public"]


def test_public_composes_with_options(tmp_path):
  got = _requires(
    tmp_path, 'Require(yaml-cpp VERSION "0.9.0" CONAN yaml-cpp '
              'PUBLIC OPTIONS shared=False)\n')
  assert got[0]["public"]
  assert got[0]["options"] == {"shared": False}


def _recorded_recipe(tmp_path, call: str):
  """The template recipe with recording requires methods — proves the
  options actually reach the conan calls, not just the parse."""
  cf = _conanfile_module(tmp_path)
  root = tmp_path / "proj" / "sources"
  root.mkdir(parents=True)
  (root / "CMakeLists.txt").write_text(call)
  r = cf.ProjectRecipe()
  r.settings = SimpleNamespace(os="Linux")
  r.recipe_folder = str(tmp_path / "proj")
  calls = []
  r.requires = lambda ref, **kw: calls.append(("requires", ref, kw))
  r.test_requires = lambda ref, **kw: calls.append(("test_requires", ref, kw))
  r.tool_requires = lambda ref, **kw: calls.append(("tool_requires", ref, kw))
  return r, calls


def test_requirements_pass_options_on_the_requires_call(tmp_path):
  r, calls = _recorded_recipe(
    tmp_path, 'Require(spdlog VERSION "1.15.0" OPTIONS use_std_fmt=True)\n'
              'Require(fmt VERSION "10.2.1")\n')
  r.requirements()
  assert calls == [
    ("requires", "spdlog/1.15.0", {"options": {"use_std_fmt": True}}),
    ("requires", "fmt/10.2.1", {}),
  ]


def test_tool_requires_carry_options_too(tmp_path):
  r, calls = _recorded_recipe(
    tmp_path, 'Require(cmake VERSION "3.29" TOOL OPTIONS bootstrap=False)\n')
  r.build_requirements()
  assert calls == [
    ("tool_requires", "cmake/3.29", {"options": {"bootstrap": False}}),
  ]


def test_public_reaches_conan_as_transitive_headers(tmp_path):
  """The oxbox failure: a dep whose headers appear in the
  package's own exported headers never reached consumers — conan does
  not propagate a static-lib requirement's headers by default. PUBLIC
  on the Require call is the declaration; transitive_headers=True on
  the requires call is what it must become."""
  r, calls = _recorded_recipe(
    tmp_path, 'Require(nlohmann_json VERSION "3.12.0" PUBLIC '
              'OPTIONS shared=False)\n'
              'Require(fmt VERSION "10.2.1")\n')
  r.requirements()
  assert calls == [
    ("requires", "nlohmann_json/3.12.0",
     {"options": {"shared": False}, "transitive_headers": True}),
    ("requires", "fmt/10.2.1", {}),
  ]


# ------------------------------------------------ cmake side (e2e) --

from buildutil import deposit

CFG = {"cmake_option_prefix": "ACME", "module_define_prefix": "ACM"}

# helpers probes the CXX compiler at include time, so the tiny tree
# needs a real one — same gate as the other e2e files
e2e = pytest.mark.skipif(
  shutil.which("cmake") is None or
  not (shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")),
  reason="needs cmake and a C++ compiler")

ROOT = """\
cmake_minimum_required(VERSION 3.25)
project(reqopt CXX)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
add_subdirectory(sources)
"""


def _sources(tmp_path, require_line):
  deposit.ensure(tmp_path, CFG)
  (tmp_path / "CMakeLists.txt").write_text(ROOT)
  (tmp_path / "sources").mkdir()
  (tmp_path / "sources" / "CMakeLists.txt").write_text(require_line + "\n")


def _configure_with(tmp_path, require_line):
  _sources(tmp_path, require_line)
  return subprocess.run(
    ["cmake", "-S", str(tmp_path), "-B", str(tmp_path / "b")],
    capture_output=True, text=True)


@e2e
def test_cmake_accepts_options_without_choking(tmp_path):
  # TOOL returns before find_package, so no real package is needed —
  # what matters is that the OPTIONS tokens parse
  proc = _configure_with(
    tmp_path, 'Require(ninja VERSION "1.11" TOOL OPTIONS with_tests=False)')
  assert proc.returncode == 0, proc.stdout + proc.stderr


@e2e
def test_cmake_refuses_a_malformed_option_token(tmp_path):
  proc = _configure_with(
    tmp_path, 'Require(ninja VERSION "1.11" TOOL OPTIONS shared)')
  assert proc.returncode != 0
  assert "not key=value" in proc.stderr


@e2e
def test_cmake_refuses_options_on_a_system_dep(tmp_path):
  proc = _configure_with(
    tmp_path, 'Require(ZLIB VERSION "1.3" SYSTEM OPTIONS shared=True)')
  assert proc.returncode != 0
  assert "OPTIONS with SYSTEM is meaningless" in proc.stderr


@e2e
def test_cmake_accepts_public_without_choking(tmp_path):
  # PLATFORM-gated to an OS this test never runs on, so find_package
  # is skipped and no real package is needed — what matters is that
  # the PUBLIC token parses as a flag, not an unknown argument
  proc = _configure_with(
    tmp_path, 'Require(foo VERSION "1.0" PUBLIC PLATFORM SomewhereElse)')
  assert proc.returncode == 0, proc.stdout + proc.stderr


@e2e
def test_cmake_accepts_the_option_shorthand(tmp_path):
  proc = _configure_with(
    tmp_path, 'Require(ninja VERSION "1.11" TOOL '
              'COMPONENTS one +asio -json two)')
  assert proc.returncode == 0, proc.stdout + proc.stderr


@e2e
def test_the_shorthand_never_reaches_find_package(tmp_path):
  # A stub find module records what it was asked for. Asserted rather than
  # inferred from an error message: the whole point of the sugar is that
  # find_package is never told about it, and only the callee can say.
  _sources(tmp_path,
           'Require(Stub VERSION "1.0" COMPONENTS realone +asio -json two)')
  (tmp_path / "_bdudata" / "cmake" / "FindStub.cmake").write_text(
    'file(WRITE "${CMAKE_BINARY_DIR}/asked.txt" "${Stub_FIND_COMPONENTS}")\n'
    "set(Stub_FOUND TRUE)\n")
  proc = subprocess.run(
    ["cmake", "-S", str(tmp_path), "-B", str(tmp_path / "b")],
    capture_output=True, text=True)
  assert proc.returncode == 0, proc.stdout + proc.stderr
  assert (tmp_path / "b" / "asked.txt").read_text() == "realone;two"


@e2e
def test_cmake_refuses_a_bare_sign(tmp_path):
  proc = _configure_with(
    tmp_path, 'Require(ninja VERSION "1.11" TOOL COMPONENTS -)')
  assert proc.returncode != 0
  assert "names no option" in proc.stderr


@e2e
def test_cmake_refuses_both_signs_on_one_library(tmp_path):
  proc = _configure_with(
    tmp_path, 'Require(ninja VERSION "1.11" TOOL COMPONENTS +asio -asio)')
  assert proc.returncode != 0
  assert "both '+asio' and '-asio'" in proc.stderr


@e2e
def test_cmake_refuses_the_shorthand_beside_its_long_spelling(tmp_path):
  proc = _configure_with(
    tmp_path, 'Require(ninja VERSION "1.11" TOOL COMPONENTS +asio '
              'OPTIONS with_asio=False)')
  assert proc.returncode != 0
  assert "COMPONENTS '+asio' and OPTIONS 'with_asio=False'" in proc.stderr


@e2e
def test_cmake_refuses_the_shorthand_on_a_system_dep(tmp_path):
  proc = _configure_with(
    tmp_path, 'Require(ZLIB VERSION "1.3" SYSTEM COMPONENTS +asio)')
  assert proc.returncode != 0
  assert "OPTIONS with SYSTEM is meaningless" in proc.stderr


@e2e
def test_cmake_refuses_public_off_the_runtime_graph(tmp_path):
  proc = _configure_with(
    tmp_path, 'Require(GTest VERSION "1.17" TEST PUBLIC)')
  assert proc.returncode != 0
  assert "PUBLIC only makes sense" in proc.stderr
