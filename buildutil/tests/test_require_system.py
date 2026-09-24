"""Require(... SYSTEM): the host's package, forced over every conan pin of it.

The two halves of Require are parsed twice — buildutil.cmake acts on the
call, and conanfile.py reads the SAME calls to derive requirements. A
keyword one side understands and the other does not is the two-parser
failure again, so both are asserted here together.
"""
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from buildutil import packaging

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
MACHINERY = TEMPLATES / "cmake" / "base" / "buildutil.cmake"


def _conanfile_module(tmp_path):
  """Load the rendered conanfile template as a module, stubbing the conan
  imports it does not need for parsing."""
  import sys, types
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
  spec = importlib.util.spec_from_file_location("cf_under_test", path)
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  mod.cross_building = lambda conanfile: False
  return mod


def _requires(tmp_path, call: str, target_os: str = "Linux",
              host_packages: bool = True):
  cf = _conanfile_module(tmp_path)
  root = tmp_path / "proj" / "sources"
  root.mkdir(parents=True)
  (root / "CMakeLists.txt").write_text(call)
  return cf._parse_requires(tmp_path / "proj", target_os, host_packages)


def test_a_plain_require_still_produces_a_conan_requirement(tmp_path):
  got = _requires(tmp_path, 'Require(fmt VERSION "10.2.1")\n')
  assert [e["conan_name"] for e in got] == ["fmt"]


def _recorded(tmp_path, call: str, skip_test_deps: bool = False,
              module=None):
  cf = module or _conanfile_module(tmp_path)
  root = tmp_path / "proj" / "sources"
  root.mkdir(parents=True)
  (root / "CMakeLists.txt").write_text(call)
  recipe = cf.ProjectRecipe()
  recipe.settings = SimpleNamespace(os="Linux")
  recipe.recipe_folder = str(tmp_path / "proj")
  calls = []
  recipe.requires = lambda ref, **kw: calls.append(("requires", ref, kw))
  recipe.test_requires = (
    lambda ref, **kw: calls.append(("test_requires", ref, kw)))
  if skip_test_deps:
    os.environ["X_SKIP_TEST_DEPS"] = "1"
  try:
    recipe.requirements()
  finally:
    os.environ.pop("X_SKIP_TEST_DEPS", None)
  return calls


def test_SYSTEM_forces_the_host_wrapper_over_every_pin(tmp_path):
  calls = _recorded(tmp_path, 'Require(Curses VERSION ">=6.0" SYSTEM)\n')
  assert calls == [("requires", "curses/system@host", {
    "force": True, "options": {"components": "", "packages": "Curses=curses"}})]


def test_every_wrapper_learns_the_SYSTEM_packages_and_its_components(
    tmp_path):
  calls = _recorded(
    tmp_path, 'Require(Python3 VERSION ">=3.12" SYSTEM COMPONENTS '
              'Interpreter Development)\n'
              'Require(pybind11 VERSION ">=2" SYSTEM)\n')
  packages = "Python3=python3:Interpreter,Development pybind11=pybind11"
  assert [kw["options"] for _, _, kw in calls] == [
    {"components": "Interpreter Development", "packages": packages},
    {"components": "", "packages": packages}]


def test_a_cross_build_forces_no_wrapper(tmp_path, monkeypatch):
  cf = _conanfile_module(tmp_path)
  monkeypatch.setitem(cf.ProjectRecipe.requirements.__globals__,
                      "cross_building", lambda conanfile: True)
  calls = _recorded(tmp_path, 'Require(SDL2 VERSION ">=2.28" SYSTEM)\n',
                    module=cf)
  assert calls == []


def test_a_test_only_SYSTEM_overrides_outside_the_runtime_graph(tmp_path):
  calls = _recorded(tmp_path, 'Require(Curses VERSION ">=6.0" SYSTEM TEST)\n')
  assert calls == [("requires", "curses/system@host", {"override": True})]


def test_a_test_only_SYSTEM_is_dropped_with_the_test_deps(tmp_path):
  calls = _recorded(tmp_path, 'Require(Curses VERSION ">=6.0" SYSTEM TEST)\n',
                    skip_test_deps=True)
  assert calls == []


def test_CONAN_names_the_package_SYSTEM_replaces(tmp_path):
  got = _requires(tmp_path, 'Require(OpenSSL VERSION ">=3" SYSTEM CONAN ssl)\n')
  assert [(e["ref"], e["system"]) for e in got] == [("ssl/system@host", True)]


def test_PUBLIC_with_SYSTEM_reaches_conan_as_transitive_headers(tmp_path):
  calls = _recorded(tmp_path, 'Require(OpenSSL VERSION ">=3" SYSTEM PUBLIC)\n')
  assert calls == [("requires", "openssl/system@host", {
    "force": True, "transitive_headers": True,
    "options": {"components": "", "packages": "OpenSSL=openssl"}})]


def test_FORCE_is_parsed_on_a_SYSTEM_entry(tmp_path):
  got = _requires(tmp_path, 'Require(OpenSSL VERSION ">=3" SYSTEM FORCE)\n')
  assert got[0]["force"]


def test_FORCE_without_SYSTEM_is_refused_by_name(tmp_path):
  with pytest.raises(ValueError, match="FORCE without SYSTEM"):
    _requires(tmp_path, 'Require(OpenSSL VERSION ">=3" FORCE)\n')


def test_SYSTEM_stays_out_of_a_cross_build(tmp_path):
  """A probe would describe the build machine; the toolchain's find rules."""
  got = _requires(tmp_path, 'Require(SDL2 VERSION ">=2.28" SYSTEM)\n',
                  host_packages=False)
  assert got == []


def test_SYSTEM_composes_with_the_other_keywords(tmp_path):
  got = _requires(
    tmp_path, 'Require(Curses VERSION ">=6.0" SYSTEM PLATFORM Windows)\n')
  assert got == []


def test_cmake_accepts_SYSTEM_as_a_keyword_not_a_stray_argument(tmp_path):
  """If buildutil.cmake does not declare it, cmake_parse_arguments leaves
  SYSTEM in UNPARSED_ARGUMENTS and it silently means nothing — the same
  blind-spot dependency this replaces."""
  text = MACHINERY.read_text()
  assert '"TEST;BENCH;TOOL;SYSTEM;PUBLIC;FORCE"' in text, (
    "buildutil.cmake does not declare SYSTEM; the two parsers disagree")


def test_both_parsers_know_the_same_keyword_set(tmp_path):
  """The invariant behind that: a keyword either side understands alone is
  a silent divergence."""
  machinery = MACHINERY.read_text()
  for keyword in packaging.requires_parser().KEYWORDS:
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
  guarded = 'if(REQ_SYSTEM)\n    string(REGEX REPLACE "^[><=~^]+" "" _ver'
  assert guarded in require and "${_ver}" not in require.split(guarded)[0]
  assert "find_package(${NAME} REQUIRED" in require, (
    "buildutil.cmake passes the Require VERSION to find_package for conan-"
    "managed deps; date-versioned packages can never match (re2 failure)")
