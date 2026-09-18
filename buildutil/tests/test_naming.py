"""Naming — the python mirror of buildutil.cmake's derivation, held to it.

The 0.42.0 field report: a vscode run task passed `--target
testing-supa-full` (the joined module name) and `buildutil run` looked
for a FILE of that name — but the file is named after the module's leaf
directory and ships at the module's mirrored source path, so every
NESTED module's run task died with "binary not found" (top-level
modules, where joined == leaf, hid the bug). The python side now asks
naming.py, and this file holds naming.py to what cmake actually does —
by compiling a tree and checking the artifacts land exactly where the
python functions predict, not by comparing two texts.
"""
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from buildutil import naming

# ---------------------------------------------------------------- unit --

def test_untagged_components_pass_through():
  assert naming.kind_of("hello") == ("hello", "")
  assert naming.kind_of("supa-full") == ("supa-full", "")


def test_tags_strip_and_synonyms_collapse():
  assert naming.kind_of("wd.exe") == ("wd", "exe")
  assert naming.kind_of("codec.a") == ("codec", "lib")
  assert naming.kind_of("plug.dll") == ("plug", "so")
  assert naming.kind_of("plug.dylib") == ("plug", "so")
  assert naming.kind_of("core.obj") == ("core", "obj")


def test_dots_that_are_not_tags_are_names():
  assert naming.kind_of("v2.1") == ("v2.1", "")
  assert naming.kind_of("libfoo.something") == ("libfoo.something", "")
  # cmake's ^(.+)\. — a bare ".exe" has nothing before the dot
  assert naming.kind_of(".exe") == (".exe", "")


def test_platform_tags_strip_first_and_are_not_kinds():
  """cmake names sources/helper.macos/ the module `helper`; the vscode
  tasks and `run` must agree, or they name targets that do not exist."""
  assert naming.kind_of("helper.macos") == ("helper", "")
  assert naming.kind_of("helper.macos.exe") == ("helper", "exe")
  assert naming.kind_of("helper.exe.macos") == ("helper", "exe")
  assert naming.kind_of("host.posix.linux") == ("host", "")
  assert naming.module_name(("xoctet-helper.macos",)) == "xoctet-helper"
  assert naming.app_name(("helper.macos",)) == "helper"


def test_multiple_tags_are_refused():
  with pytest.raises(SystemExit):
    naming.kind_of("thing.lib.so")


def test_names_of_a_nested_tagged_module():
  parts = ("tools", "wd.exe")
  assert naming.module_name(parts) == "tools-wd"
  assert naming.app_name(parts) == "wd"
  assert naming.mirror_parent(parts) == "tools"


def test_names_of_a_top_level_module():
  assert naming.module_name(("hello",)) == "hello"
  assert naming.app_name(("hello",)) == "hello"
  assert naming.mirror_parent(("hello",)) == ""


def _fake_sources(root, rels):
  src = root / "sources"
  for rel in rels:
    d = src / rel
    d.mkdir(parents=True)
    (d / "CMakeLists.txt").write_text("Init_submodule()\n")
  return src


def test_resolve_app_accepts_module_and_app_names(tmp_path):
  src = _fake_sources(tmp_path, ["testing/supa-full", "hello"])
  by_module = naming.resolve_app("testing-supa-full", src)
  by_app = naming.resolve_app("supa-full", src)
  assert by_module == by_app == ("supa-full", "testing")
  assert naming.resolve_app("hello", src) == ("hello", "")
  assert naming.resolve_app("no-such", src) is None


def test_resolve_app_skips_dot_dirs_and_mistagged(tmp_path):
  src = _fake_sources(tmp_path, [".archive/old", "bad.lib.so", "ok"])
  assert naming.resolve_app("old", src) is None
  assert naming.resolve_app("bad", src) is None
  assert naming.resolve_app("ok", src) == ("ok", "")


# ---------------------------------------------- cmake agreement (e2e) --

from buildutil import deposit

CFG = {"cmake_option_prefix": "ACME", "module_define_prefix": "ACM"}

e2e = pytest.mark.skipif(
  shutil.which("cmake") is None or shutil.which("ninja") is None or
  not (shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")),
  reason="needs cmake, ninja and a C++ compiler")

ROOT_CMAKE = """\
cmake_minimum_required(VERSION 3.25)
project(naming CXX)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
add_subdirectory(sources)
"""

MAIN = "int main() { return 0; }\n"


@e2e
def test_python_predictions_match_where_cmake_ships(tmp_path):
  """The one test that keeps the two parsers honest: build + install a
  tree holding a top-level app, a nested app (the field report's shape)
  and a nested TAGGED app, then assert an artifact exists at exactly
  the path naming.py predicts for each — build tree and install tree."""
  deposit.ensure(tmp_path, CFG)
  (tmp_path / "CMakeLists.txt").write_text(ROOT_CMAKE)
  src = tmp_path / "sources"
  src.mkdir()
  (src / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  layout = ["hello", "testing/supa-full", "tools/wd.exe"]
  for rel in layout:
    d = src / rel
    d.mkdir(parents=True)
    (d / "CMakeLists.txt").write_text("Init_submodule()\n")
    (d / "main.cpp").write_text(MAIN)
  build = tmp_path / "b"
  for cmd in (["cmake", "-S", str(tmp_path), "-B", str(build), "-G", "Ninja"],
              ["cmake", "--build", str(build)],
              ["cmake", "--install", str(build),
               "--prefix", str(tmp_path / "prefix")]):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
  for rel in layout:
    parts = tuple(rel.split("/"))
    app = naming.app_name(parts)
    built = build / "bin" / app
    assert built.is_file(), f"{rel}: no {built} in the build tree"
    mirror = naming.mirror_parent(parts)
    installed = (tmp_path / "prefix" / mirror / app if mirror
                 else tmp_path / "prefix" / app)
    assert installed.is_file(), (
      f"{rel}: python predicts {installed}, cmake shipped elsewhere:\n"
      + "\n".join(str(p) for p in (tmp_path / "prefix").rglob("*")))
    # and resolve_app agrees from either name the user might type
    assert naming.resolve_app(naming.module_name(parts), src) == (app, mirror)


# ------------------------------------------- the reported run failure --

@pytest.mark.skipif(
  os.name == "nt" or importlib.util.find_spec("click") is None,
  reason="needs execv and the CLI's typer/click (the plain-pytest CI "
         "job carries neither; pytest-cmake runs it)")
def test_run_finds_a_nested_modules_installed_binary(tmp_path):
  """`buildutil run --no-build --target testing-supa-full` — verbatim
  the failing vscode task shape: joined module name in, leaf-named
  binary at the mirrored install path out."""
  (tmp_path / "buildutil.toml").write_text(
    '[project]\nname = "t"\ncmake_option_prefix = "T"\n'
    'module_define_prefix = "T"\n')
  d = tmp_path / "sources" / "testing" / "supa-full"
  d.mkdir(parents=True)
  (d / "CMakeLists.txt").write_text("Init_submodule()\n")
  (d / "main.cpp").write_text(MAIN)
  installed = tmp_path / "_install" / "testing" / "supa-full"
  installed.parent.mkdir(parents=True)
  installed.write_text("#!/bin/sh\necho RAN-FROM-MIRROR\n")
  installed.chmod(0o755)
  pkg_parent = Path(naming.__file__).resolve().parents[1]
  proc = subprocess.run(
    [sys.executable, "-m", "buildutil", "--no-watchdog",
     "run", "--no-build", "--target", "testing-supa-full"],
    cwd=tmp_path, capture_output=True, text=True,
    env={**os.environ, "BUILDUTIL_SYSTEM": "1",
         "PYTHONPATH": str(pkg_parent), "HOME": str(tmp_path)})
  assert proc.returncode == 0, proc.stdout + proc.stderr
  assert "RAN-FROM-MIRROR" in proc.stdout
