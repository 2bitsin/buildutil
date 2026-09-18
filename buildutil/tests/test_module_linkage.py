"""module_linkage — the first real configuration option (owner idea):
untagged modules, static by default, switch to shared as CONFIGURATION;
kind-tagged modules keep their word. Hidden visibility stays the hard
rule (owner: "I really hate export-every-symbol") — only annotated
symbols cross a shared boundary, PUBLISH_SYMBOLS being the explicit
per-module hatch. The value persists in _bdudata/config.ini, resolves
env > saved > default, carries a profile-dir axis (static and shared
never share a build tree), and is linked to conan's standard `shared`
option on packaged libraries."""
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from buildutil import configopts, packaging

# ------------------------------------------------ the config core --

def test_default_and_persistence(tmp_path):
  assert configopts.get("module_linkage", root=tmp_path) == "static"
  configopts.set_value("module_linkage", "shared", root=tmp_path)
  assert configopts.get("module_linkage", root=tmp_path) == "shared"
  assert (tmp_path / "_bdudata" / "config.ini").is_file()
  configopts.reset(root=tmp_path)
  assert configopts.get("module_linkage", root=tmp_path) == "static"


def test_env_wins_over_saved(tmp_path, monkeypatch):
  configopts.set_value("module_linkage", "shared", root=tmp_path)
  monkeypatch.setenv("BUILDUTIL_OPT_MODULE_LINKAGE", "static")
  assert configopts.get("module_linkage", root=tmp_path) == "static"


def test_values_are_validated(tmp_path):
  with pytest.raises(SystemExit, match="must be one of"):
    configopts.set_value("module_linkage", "sideways", root=tmp_path)
  with pytest.raises(SystemExit, match="unknown option"):
    configopts.set_value("no_such_option", "x", root=tmp_path)


def test_describe_reports_value_and_source(tmp_path, monkeypatch):
  monkeypatch.delenv("BUILDUTIL_OPT_MODULE_LINKAGE", raising=False)
  rows = {r[0]: r for r in configopts.describe(root=tmp_path)}
  assert rows["module_linkage"][1:3] == ("static", "default")
  configopts.set_value("module_linkage", "shared", root=tmp_path)
  rows = {r[0]: r for r in configopts.describe(root=tmp_path)}
  assert rows["module_linkage"][1:3] == ("shared", "saved")


# ------------------------------------------- the profile-dir axis --

SETTINGS = {"arch": "x86_64", "os": "Linux", "compiler": "gcc",
            "build_type": "Release"}


def test_profile_name_grows_the_axis_only_when_shared():
  from buildutil import engine
  assert engine._profile_name(SETTINGS, linkage="static") == \
    "x86_64-linux-gcc-release"
  assert engine._profile_name(SETTINGS, linkage="shared") == \
    "x86_64-linux-gcc-shared-release"


def test_recipe_layout_agrees_with_the_driver(tmp_path, monkeypatch):
  """The two-parsers gate: the scaffolded conanfile's _profile_name and
  the engine's must produce the same directory for both linkages —
  disagreement means export-pkg packages a tree nobody built."""
  import importlib.util
  import types
  for name, attrs in (("conan", {"ConanFile": object}),
                      ("conan.tools", {}),
                      ("conan.tools.cmake", {"CMakeDeps": object,
                                             "CMakeToolchain": object,
                                             "cmake_layout": lambda *a: None})):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
      setattr(mod, k, v)
    sys.modules.setdefault(name, mod)
  src = (Path(packaging.__file__).parent / "templates" / "project" /
         "conanfile.py").read_text().replace("@CONAN_NAME@", "x").replace(
           "@CMAKE_OPTION_PREFIX@", "X")
  cf = tmp_path / "conanfile.py"
  cf.write_text(src)
  spec = importlib.util.spec_from_file_location("cf_linkage", cf)
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  from buildutil import engine
  settings = SimpleNamespace(arch="x86_64", os="Linux", compiler="gcc",
                             build_type="Release")
  for linkage in ("static", "shared"):
    monkeypatch.setenv("BUILDUTIL_OPT_MODULE_LINKAGE", linkage)
    assert mod._profile_name(settings) == \
      engine._profile_name(SETTINGS, linkage=linkage), linkage


def test_recipe_reads_the_ini_when_no_env(tmp_path, monkeypatch):
  monkeypatch.delenv("BUILDUTIL_OPT_MODULE_LINKAGE", raising=False)
  configopts.save({"module_linkage": "shared"}, root=tmp_path)
  # the recipe helper parses the same file
  import importlib.util
  import types
  sys.modules.setdefault("conan", types.ModuleType("conan"))
  setattr(sys.modules["conan"], "ConanFile", object)
  src = (Path(packaging.__file__).parent / "templates" / "project" /
         "conanfile.py").read_text().replace("@CONAN_NAME@", "x").replace(
           "@CMAKE_OPTION_PREFIX@", "X")
  cf = tmp_path / "conanfile.py"
  cf.write_text(src)
  spec = importlib.util.spec_from_file_location("cf_ini", cf)
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  assert mod._module_linkage() == "shared"


# ------------------------------------- conan shared-option linkage --

def test_library_recipe_declares_the_standard_shared_option(tmp_path):
  import importlib.util
  import types
  sys.modules.setdefault("conan", types.ModuleType("conan"))
  setattr(sys.modules["conan"], "ConanFile", object)
  src = (Path(packaging.__file__).parent / "templates" / "project" /
         "conanfile.py").read_text().replace("@CONAN_NAME@", "x").replace(
           "@CMAKE_OPTION_PREFIX@", "X")
  (tmp_path / "buildutil.toml").write_text(
    '[package]\nkind = "library"\nname = "s"\n')
  cf = tmp_path / "conanfile.py"
  cf.write_text(src)
  spec = importlib.util.spec_from_file_location("cf_opt", cf)
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  assert mod.ProjectRecipe.options == {"shared": [True, False]}
  assert mod.ProjectRecipe.default_options == {"shared": False}
  assert mod.ProjectRecipe.package_type == "library"


def test_export_and_test_request_shared_when_asked(monkeypatch):
  from buildutil import config
  monkeypatch.setattr(config, "PROJECT",
                      {**config.PROJECT, "package_kind": "library",
                       "package_name": "ser", "name": "ser"})
  calls = []
  packaging.export_pkg("1.0.0.1", Path("/h"), Path("/b"), shared=True,
                       run=calls.append)
  packaging.run_package_test("1.0.0.1", Path("/h"), Path("/b"),
                             shared=True, run=calls.append)
  assert ["-o", "&:shared=True"] == [
    calls[0][calls[0].index("-o")], calls[0][calls[0].index("-o") + 1]]
  assert "-o" in calls[1] and "ser/*:shared=True" in calls[1]
  # and NOT when static
  calls.clear()
  packaging.export_pkg("1.0.0.1", Path("/h"), Path("/b"), shared=False,
                       run=calls.append)
  assert "-o" not in calls[0]


def test_shared_requested_needs_library_kind_and_shared_config(
    tmp_path, monkeypatch):
  from buildutil import config
  monkeypatch.setenv("BUILDUTIL_OPT_MODULE_LINKAGE", "shared")
  monkeypatch.setattr(config, "PROJECT",
                      {**config.PROJECT, "package_kind": "library"})
  assert packaging.shared_requested()
  monkeypatch.setattr(config, "PROJECT",
                      {**config.PROJECT, "package_kind": "application"})
  assert not packaging.shared_requested()
  monkeypatch.setenv("BUILDUTIL_OPT_MODULE_LINKAGE", "static")
  monkeypatch.setattr(config, "PROJECT",
                      {**config.PROJECT, "package_kind": "library"})
  assert not packaging.shared_requested()


# --------------------------------------------------- e2e (cmake) --

from buildutil import deposit

CFG = {"cmake_option_prefix": "ACME", "module_define_prefix": "ACM"}

e2e = pytest.mark.skipif(
  shutil.which("cmake") is None or shutil.which("ninja") is None or
  not (shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")),
  reason="needs cmake, ninja and a C++ compiler")

ROOT_CMAKE = """\
cmake_minimum_required(VERSION 3.25)
project(linkage CXX)
add_compile_options(-fvisibility=hidden)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
add_subdirectory(sources)
"""

CORE_IMPL = """\
__attribute__((visibility("default"))) int core_answer() { return 42; }
"""

TOOL_MAIN = """\
extern int core_answer();
int main() { return core_answer() == 42 ? 0 : 1; }
"""


def _tree(tmp_path, linkage):
  deposit.ensure(tmp_path, CFG)
  (tmp_path / "CMakeLists.txt").write_text(ROOT_CMAKE)
  src = tmp_path / "sources"
  src.mkdir()
  (src / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  core = src / "ser" / "core"
  core.mkdir(parents=True)
  (core / "CMakeLists.txt").write_text("Init_submodule()\n")
  (core / "impl.cpp").write_text(CORE_IMPL)
  pinned = src / "pinned.a"
  pinned.mkdir()
  (pinned / "CMakeLists.txt").write_text("Init_submodule()\n")
  (pinned / "impl.cpp").write_text("int pinned_fn() { return 1; }\n")
  tool = src / "tool"
  tool.mkdir()
  (tool / "CMakeLists.txt").write_text(
    "Init_submodule()\nLink_dependencies(ser-core)\n")
  (tool / "main.cpp").write_text(TOOL_MAIN)
  for cmd in (["cmake", "-S", str(tmp_path), "-B", str(tmp_path / "b"),
               "-G", "Ninja", f"-DBUILDUTIL_MODULE_LINKAGE={linkage}"],
              ["cmake", "--build", str(tmp_path / "b")],
              ["cmake", "--install", str(tmp_path / "b"),
               "--prefix", str(tmp_path / "prefix")]):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, " ".join(cmd) + "\n" + proc.stdout + proc.stderr
  return tmp_path / "prefix"


@e2e
def test_shared_linkage_builds_ships_and_runs(tmp_path):
  """The whole story compiled: untagged modules go shared (leaf-named,
  mirror-installed), a .a-TAGGED module keeps its word, the app links
  the shared sibling through its ANNOTATED symbol, and the INSTALLED
  app runs — which is the $ORIGIN rpath proof, since the mirror is the
  only place the library exists at that path."""
  prefix = _tree(tmp_path, "shared")
  assert (prefix / "ser" / "libcore.so").is_file(), \
    "\n".join(str(p) for p in prefix.rglob("*"))
  assert not list(prefix.rglob("libpinned.so")), \
    "a .a-tagged module was switched to shared"
  installed_app = prefix / "tool"
  assert installed_app.is_file()
  ran = subprocess.run([str(installed_app)], capture_output=True)
  assert ran.returncode == 0, \
    "the installed app could not run against its mirrored shared dep"


@e2e
def test_static_default_is_byte_for_byte_the_old_world(tmp_path):
  prefix = _tree(tmp_path, "static")
  assert not list(prefix.rglob("*.so")), list(prefix.rglob("*"))
  assert (prefix / "tool").is_file()
  ran = subprocess.run([str(prefix / "tool")], capture_output=True)
  assert ran.returncode == 0
