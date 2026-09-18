"""Platform tags on a MODULE directory, and Objective-C++ sources.

Both close the same gap: the two places where buildutil's platform
convention ran out and a project had to write `if(APPLE)` in its own
cmake -- which is logic, in a file that is supposed to be declarations.

  sources/helper.macos/   the module exists on macOS and nowhere else
  platform.macos.mm       an Objective-C++ TU of a module, macOS only

The tag composes with the kind tags and is never part of a name: the
module, its binary and its install mirror are all `helper`.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from buildutil import deposit

PYSUPPORT = Path(deposit.__file__).resolve().parent / "pysupport"
CFG = {"cmake_option_prefix": "PLAT", "module_define_prefix": "PLT"}


def _rendered() -> str:
  """The machinery as a project actually sees it. The template carries
  @PLACEHOLDER@s -- the platform tag table among them, rendered from
  naming.py -- so asserting on the template would assert on the wrong
  text."""
  import tempfile
  from buildutil import deposit as _deposit
  out = _deposit.ensure(Path(tempfile.mkdtemp()),
                        {"cmake_option_prefix": "A",
                         "module_define_prefix": "A"})
  return (out / "buildutil.cmake").read_text()


e2e = pytest.mark.skipif(
  shutil.which("cmake") is None or shutil.which("ninja") is None,
  reason="needs cmake and ninja")

ROOT_CMAKE = """\
cmake_minimum_required(VERSION 3.25)
project(plat CXX)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
add_subdirectory(sources)
"""

# The live tag on every host this suite can run on. `posix` is true on
# Linux and macOS, `win32` on neither -- so the pair below is a real
# built/not-built contrast wherever the tests run.
LIVE = "posix"
DEAD = "win32"


def _scaffold(tmp_path):
  deposit.ensure(tmp_path, CFG)
  (tmp_path / "CMakeLists.txt").write_text(ROOT_CMAKE)
  src = tmp_path / "sources"
  src.mkdir()
  (src / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  return src


def _module(parent, name, main=True):
  module = parent / name
  module.mkdir(parents=True)
  (module / "CMakeLists.txt").write_text("Init_submodule()\n")
  if main:
    (module / "main.cpp").write_text("int main() { return 0; }\n")
  else:
    (module / "thing.cpp").write_text("int thing() { return 0; }\n")
  return module


def _configure(root, build, *extra):
  return subprocess.run(
    ["cmake", "-S", str(root), "-B", str(build), "-G", "Ninja",
     f"-DBUILDUTIL_PY={sys.executable}",
     f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}", *extra],
    capture_output=True, text=True)


def _targets(build):
  proc = subprocess.run(["cmake", "--build", str(build), "--target", "help"],
                        capture_output=True, text=True)
  return proc.stdout


@e2e
def test_a_live_tag_builds_and_the_tag_is_not_part_of_the_name(tmp_path):
  src = _scaffold(tmp_path)
  _module(src, f"tool.{LIVE}")
  build = tmp_path / "b"
  proc = _configure(tmp_path, build)
  assert proc.returncode == 0, proc.stdout + proc.stderr
  built = subprocess.run(["cmake", "--build", str(build)],
                         capture_output=True, text=True)
  assert built.returncode == 0, built.stdout + built.stderr
  # the module, the target and the binary are all `tool`
  assert (build / "bin" / "tool").is_file(), _targets(build)


@e2e
def test_a_dead_tag_means_the_module_is_not_in_the_build_at_all(tmp_path):
  """Not "built and skipped": never entered. Its CMakeLists does not run,
  so nothing it declares -- targets, tests, dependencies -- exists."""
  src = _scaffold(tmp_path)
  _module(src, f"helper.{DEAD}")
  # a syntax error inside proves the directory is never added
  (src / f"helper.{DEAD}" / "CMakeLists.txt").write_text(
    "Init_submodule()\nthis_command_does_not_exist()\n")
  build = tmp_path / "b"
  proc = _configure(tmp_path, build)
  assert proc.returncode == 0, proc.stdout + proc.stderr
  assert "helper" not in _targets(build)


@e2e
def test_the_tag_composes_with_a_kind_tag_in_either_order(tmp_path):
  src = _scaffold(tmp_path)
  _module(src, f"one.{LIVE}.exe")
  _module(src, f"two.exe.{LIVE}")
  build = tmp_path / "b"
  proc = _configure(tmp_path, build)
  assert proc.returncode == 0, proc.stdout + proc.stderr
  built = subprocess.run(["cmake", "--build", str(build)],
                         capture_output=True, text=True)
  assert built.returncode == 0, built.stdout + built.stderr
  # both tags off both names, whichever order they were written in
  assert (build / "bin" / "one").is_file()
  assert (build / "bin" / "two").is_file()


@e2e
def test_a_tagged_group_directory_takes_its_whole_subtree(tmp_path):
  src = _scaffold(tmp_path)
  group = src / f"host.{DEAD}"
  _module(group, "inner")
  build = tmp_path / "b"
  proc = _configure(tmp_path, build)
  assert proc.returncode == 0, proc.stdout + proc.stderr
  assert "host-inner" not in _targets(build)


@e2e
def test_a_dead_module_does_not_reserve_its_library_name(tmp_path):
  """The library pre-pass walks the tree independently of the scan; if it
  disagreed, a skipped module would still decorate a name and a live
  sibling would link the wrong target."""
  src = _scaffold(tmp_path)
  _module(src, f"thing.{DEAD}")
  _module(src, "thing", main=False)
  build = tmp_path / "b"
  proc = _configure(tmp_path, build)
  assert proc.returncode == 0, proc.stdout + proc.stderr
  built = subprocess.run(["cmake", "--build", str(build)],
                         capture_output=True, text=True)
  assert built.returncode == 0, built.stdout + built.stderr


@e2e
def test_an_objcxx_source_is_inert_off_macos(tmp_path):
  """On Linux the .mm must not reach the compiler at all -- it is globbed
  only on Darwin, and OBJCXX is never enabled. (That it COMPILES on macOS
  is a claim only a macOS runner can make; the machinery assertions below
  are what this box can check.)"""
  src = _scaffold(tmp_path)
  module = _module(src, "gui")
  (module / "platform.macos.mm").write_text("@this is not C++ at all\n")
  build = tmp_path / "b"
  proc = _configure(tmp_path, build)
  assert proc.returncode == 0, proc.stdout + proc.stderr
  built = subprocess.run(["cmake", "--build", str(build)],
                         capture_output=True, text=True)
  assert built.returncode == 0, built.stdout + built.stderr


def test_the_machinery_enables_objcxx_by_presence():
  """The trap this exists for: `.mm` is also in
  CMAKE_CXX_SOURCE_FILE_EXTENSIONS, so a .mm compiled without OBJCXX
  enabled is silently built as C++ -- which happens to work on clang and
  stops working on anything else. Enabling it is presence-driven, at ROOT
  scope, and keyed on the extension table rather than a hardcoded if."""
  from buildutil import naming
  machinery = _rendered()
  assert naming.EXTENSION_LANGUAGE["mm"] == "OBJCXX"
  assert naming.EXTENSION_LANGUAGE["m"] == "OBJC"
  assert "enable_language(" in machinery
  # globbed for a module only where the extension's platform set allows
  assert "_buildutil_ext_systems_${_ext}" in machinery
  # and classified by the same platform/test/bench tags as a .cpp
  alternation = naming.platform_alternation()
  assert (r'"\\.(' + alternation + r')\\.(test\\.|bench\\.)?[A-Za-z0-9]+$"') \
    in machinery


def test_platform_tags_are_stripped_from_module_names():
  """A tag is never part of a name -- of the target, the binary, or the
  install mirror. Same rule the kind tags follow. The vocabulary itself
  is rendered from naming.py, so there is one table, not four."""
  from buildutil import naming
  machinery = _rendered()
  assert ('set(_buildutil_platform_tags "'
          + ";".join(naming.PLATFORM_TAGS) + '")') in machinery
  assert "function(_buildutil_dir_platform_live leaf out)" in machinery
  assert machinery.count('  _buildutil_dir_platform_live("${sub}" _live)') == 2
