"""exports.map beside main.so.cpp, or emitted by the hook, is the shared module's version script (#131)."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from buildutil import deposit

CFG = {"cmake_option_prefix": "ACME", "module_define_prefix": "ACM"}
PYSUPPORT = Path(deposit.__file__).resolve().parent / "pysupport"

pytestmark = pytest.mark.skipif(
  not sys.platform.startswith("linux") or shutil.which("readelf") is None
  or shutil.which("nm") is None or shutil.which("cmake") is None
  or shutil.which("ninja") is None
  or not (shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")),
  reason="needs Linux, binutils, cmake, ninja and a C++ compiler")

ROOT_CMAKE = """\
cmake_minimum_required(VERSION 3.25)
project(versioned CXX)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
add_subdirectory(sources)
"""

CORE = """\
extern "C" int keep_me() { return 1; }
extern "C" int drop_me() { return 2; }
"""

CHECKED_IN = "CHECKED_1.0 { global: keep_me; local: *; };\n"

HOOK = """\
import buildutil_configure as bc
bc.emit("exports.map", "EMITTED_2.0 { global: keep_me; local: *; };\\n")
"""


def _tree(root, files, init="Init_submodule(PUBLISH_SYMBOLS)\n", directory="core"):
  deposit.ensure(root, CFG)
  (root / "CMakeLists.txt").write_text(ROOT_CMAKE)
  (root / "sources").mkdir()
  (root / "sources" / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  module = root / "sources" / directory
  module.mkdir()
  (module / "CMakeLists.txt").write_text(init)
  for name, text in files.items():
    (module / name).write_text(text)
  return root


def _configure(root, *extra):
  return subprocess.run(["cmake", "-S", str(root), "-B", str(root / "b"), "-G", "Ninja",
                         f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}", *extra],
                        capture_output=True, text=True)


def _build(root):
  configured = _configure(root)
  assert configured.returncode == 0, configured.stdout + configured.stderr
  built = subprocess.run(["cmake", "--build", str(root / "b")], capture_output=True, text=True)
  assert built.returncode == 0, built.stdout + built.stderr
  return root / "b" / "sources" / "core" / "libcore.so"


def _versions(library):
  return subprocess.run(["readelf", "-V", "-W", str(library)], capture_output=True,
                        text=True, check=True).stdout


def _dynamic_symbols(library):
  return subprocess.run(["nm", "-D", "--defined-only", str(library)], capture_output=True,
                        text=True, check=True).stdout


def test_a_checked_in_map_versions_the_library(tmp_path):
  """PUBLISH_SYMBOLS exports both functions; the script keeps one, under its node."""
  library = _build(_tree(tmp_path, {"main.so.cpp": CORE, "exports.map": CHECKED_IN}))
  assert "CHECKED_1.0" in _versions(library)
  symbols = _dynamic_symbols(library)
  assert "keep_me@@CHECKED_1.0" in symbols, symbols
  assert "drop_me" not in symbols, symbols


def test_without_a_map_publish_symbols_exports_everything(tmp_path):
  library = _build(_tree(tmp_path, {"main.so.cpp": CORE}))
  symbols = _dynamic_symbols(library)
  assert "drop_me" in symbols, symbols
  assert "CHECKED_1.0" not in _versions(library)


def test_a_hook_emitted_map_beside_a_checked_in_one_is_refused_naming_both(tmp_path):
  tree = _tree(tmp_path, {"main.so.cpp": CORE, "exports.map": CHECKED_IN, "configure.py": HOOK})
  configured = _configure(tree)
  assert configured.returncode != 0
  assert "has more than one exports.map, checked in or declared" in configured.stderr
  assert str(tree / "sources" / "core" / "exports.map") in configured.stderr
  assert str(tree / "b" / "generated" / "core" / "exports.map") in configured.stderr


def test_the_hook_emitted_map_versions_the_library(tmp_path):
  versions = _versions(_build(_tree(tmp_path, {"main.so.cpp": CORE, "configure.py": HOOK})))
  assert "EMITTED_2.0" in versions, versions


def test_editing_the_map_relinks(tmp_path):
  _build(_tree(tmp_path, {"main.so.cpp": CORE, "exports.map": CHECKED_IN}))
  (tmp_path / "sources" / "core" / "exports.map").write_text(
    CHECKED_IN.replace("CHECKED_1.0", "CHECKED_1.1"))
  library = _build(tmp_path)
  assert "CHECKED_1.1" in _versions(library)


@pytest.mark.parametrize("directory,files", [
  ("core", {"impl.cpp": CORE, "exports.map": CHECKED_IN}),
  ("core.obj", {"impl.cpp": CORE, "exports.map": CHECKED_IN}),
  ("core", {"impl.cpp": CORE, "configure.py": HOOK}),
])
def test_a_map_on_a_module_that_is_not_shared_is_refused(tmp_path, directory, files):
  configured = _configure(_tree(tmp_path, files, "Init_submodule()\n", directory))
  assert configured.returncode != 0
  assert "not a shared library module" in configured.stderr, configured.stderr
  assert "exports.map" in configured.stderr, configured.stderr


def test_a_darwin_target_is_told_the_map_is_not_applied(tmp_path):
  configured = _configure(_tree(tmp_path, {"main.so.cpp": CORE, "exports.map": CHECKED_IN}),
                          "-DCMAKE_SYSTEM_NAME=Darwin")
  assert configured.returncode == 0, configured.stdout + configured.stderr
  assert "applied on ELF targets only" in configured.stderr, configured.stderr
  assert "--version-script" not in (tmp_path / "b" / "build.ninja").read_text()
