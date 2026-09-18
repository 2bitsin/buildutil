"""Require(... SYSTEM): find_package in cmake, absent from the conan graph.

The two halves of Require are parsed twice — buildutil.cmake acts on the
call, and conanfile.py reads the SAME calls to derive requirements. A
keyword one side understands and the other does not is the two-parser
failure again, so both are asserted here together.
"""
import importlib.util
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
MACHINERY = TEMPLATES / "cmake" / "base" / "buildutil.cmake"


def _conanfile_module(tmp_path):
  """Load the rendered conanfile template as a module, stubbing the conan
  imports it does not need for parsing."""
  import sys, types
  for name, attrs in (("conan", {"ConanFile": object}),
                      ("conan.tools", {}),
                      ("conan.tools.cmake", {"CMakeDeps": object,
                                             "CMakeToolchain": object,
                                             "cmake_layout": lambda *a: None})):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
      setattr(mod, k, v)
    sys.modules.setdefault(name, mod)
  src = (TEMPLATES / "project" / "conanfile.py").read_text().replace("@CONAN_NAME@", "x")
  path = tmp_path / "cf.py"
  path.write_text(src)
  spec = importlib.util.spec_from_file_location("cf_under_test", path)
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod


def _requires(tmp_path, call: str, target_os: str = "Linux"):
  cf = _conanfile_module(tmp_path)
  root = tmp_path / "proj" / "sources"
  root.mkdir(parents=True)
  (root / "CMakeLists.txt").write_text(call)
  return cf._parse_requires(tmp_path / "proj", target_os)


def test_a_plain_require_still_produces_a_conan_requirement(tmp_path):
  got = _requires(tmp_path, 'Require(fmt VERSION "10.2.1")\n')
  assert [e["conan_name"] for e in got] == ["fmt"]


def test_SYSTEM_is_excluded_from_the_conan_graph(tmp_path):
  """The reported failure: dropping CONAN to use the host's copy made
  conan ask for a recipe that does not exist and fail the install."""
  got = _requires(tmp_path, 'Require(Curses VERSION ">=6.0" SYSTEM)\n')
  assert got == [], f"SYSTEM still reached the conan graph: {got}"


def test_SYSTEM_composes_with_the_other_keywords(tmp_path):
  got = _requires(
    tmp_path, 'Require(Curses VERSION ">=6.0" SYSTEM PLATFORM Linux)\n')
  assert got == []


def test_cmake_accepts_SYSTEM_as_a_keyword_not_a_stray_argument(tmp_path):
  """If buildutil.cmake does not declare it, cmake_parse_arguments leaves
  SYSTEM in UNPARSED_ARGUMENTS and it silently means nothing — the same
  blind-spot dependency this replaces."""
  text = MACHINERY.read_text()
  assert '"TEST;BENCH;TOOL;SYSTEM;PUBLIC"' in text, (
    "buildutil.cmake does not declare SYSTEM; the two parsers disagree")


def test_both_parsers_know_the_same_keyword_set(tmp_path):
  """The invariant behind that: a keyword either side understands alone is
  a silent divergence."""
  cf = _conanfile_module(tmp_path)
  machinery = MACHINERY.read_text()
  for keyword in cf._KEYWORDS:
    assert keyword in machinery, (
      f"conanfile.py parses {keyword} but buildutil.cmake never mentions it")


def test_find_package_version_check_is_SYSTEM_only():
  """For a conan-managed dep the requested range was enforced when conan
  resolved the graph, and the config find_package locates IS the package
  conan installed — re-checking can only produce false negatives. And it
  did: conan's config-version treats a dot-less version as its own major,
  so a date-versioned package (re2's "20251105") can never satisfy a
  lower bound like ">=20230301". The version argument must reach
  find_package only under REQ_SYSTEM, where the host provides the package
  and nobody else has verified anything."""
  text = MACHINERY.read_text()
  require = text.split("function(Require NAME)")[1].split("endfunction()")[0]
  guarded = 'set(_ver "")\n  if(REQ_SYSTEM)'
  assert guarded in require, (
    "buildutil.cmake passes the Require VERSION to find_package for conan-"
    "managed deps; date-versioned packages can never match (re2 failure)")
