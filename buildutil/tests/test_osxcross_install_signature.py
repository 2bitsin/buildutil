"""The osxcross install step: the ad-hoc code signature, and the rpath.

ld64 signs every arm64 Mach-O it links, ad-hoc: a CodeDirectory of
SHA-256 page hashes over the file. cmake's install rewrites the RPATH of
the copy with install_name_tool, which changes covered bytes and leaves
the hashes stale -- and on Linux there is no codesign to recompute them,
so the installed binary is one dyld refuses to launch. The lane
therefore links with the INSTALL rpath, leaving the install step nothing
to edit.

That step also writes the rpath buildutil computed, checked here on the
real Mach-O: dyld expands @loader_path, never $ORIGIN.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from buildutil import deposit, engine

# The lane image this box carries; point the variable at another tag
# to run these against a differently named osxcross image.
IMAGE = os.environ.get("BUILDUTIL_OSXCROSS_IMAGE", "cefpackage/osxcross:26")


def _image_present() -> bool:
  docker = shutil.which("docker")
  return bool(docker) and subprocess.run(
    [docker, "image", "inspect", IMAGE],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


lane = pytest.mark.skipif(not _image_present(),
                          reason=f"{IMAGE} is not on this docker daemon")


@pytest.fixture
def osxcross(monkeypatch):
  monkeypatch.setattr(engine, "_osxcross_live", lambda: True)
  monkeypatch.setattr(engine, "_wine_msvc_live", lambda: False)
  monkeypatch.setattr(
    engine, "_osxcross_tool",
    lambda name: f"/opt/osxcross/target/bin/arm64-apple-darwin25-{name}")


def test_the_lane_links_with_the_install_rpath(osxcross):
  # The whole fix: with the install rpath already in the link, cmake's
  # install has no RPATH_CHANGE to make and copies the signed file
  # unmodified.
  assert "-DCMAKE_BUILD_WITH_INSTALL_RPATH=ON" \
         in engine._osxcross_configure_args()


def test_a_native_configure_keeps_its_build_rpath(monkeypatch):
  # Off the cross lane a binary is RUN from the build tree, and the
  # build rpath is what lets it find its sibling libraries there.
  monkeypatch.setattr(engine, "_osxcross_live", lambda: False)
  monkeypatch.setattr(engine, "_wine_msvc_live", lambda: False)
  assert engine._osxcross_configure_args() == []


# The Darwin binutils and linker the lane needs, named once for both
# scripts below; a real build gets them from
# engine._osxcross_configure_args().
BIN = "/opt/osxcross/target/bin"
LANE_FLAGS = (
  f"-DCMAKE_SYSTEM_NAME=Darwin -DCMAKE_INSTALL_PREFIX=/work/out "
  f"-DCMAKE_C_COMPILER=oa64-clang -DCMAKE_CXX_COMPILER=oa64-clang++ "
  f"-DCMAKE_AR={BIN}/arm64-apple-darwin25-ar "
  f"-DCMAKE_RANLIB={BIN}/arm64-apple-darwin25-ranlib "
  f"-DCMAKE_INSTALL_NAME_TOOL={BIN}/arm64-apple-darwin25-install_name_tool "
  f"-DCMAKE_EXE_LINKER_FLAGS=-fuse-ld=lld "
  f"-DCMAKE_SHARED_LINKER_FLAGS=-fuse-ld=lld")

PROJECT = """\
cmake_minimum_required(VERSION 3.25)
project(sig CXX)
add_library(greet SHARED greet.cpp)
set_target_properties(greet PROPERTIES
  INSTALL_RPATH "@loader_path" BUILD_RPATH "${CMAKE_CURRENT_BINARY_DIR}")
add_executable(app main.cpp)
target_link_libraries(app PRIVATE greet)
set_target_properties(app PROPERTIES INSTALL_RPATH "@loader_path")
install(TARGETS app greet RUNTIME DESTINATION bin LIBRARY DESTINATION bin)
"""

GREET = 'extern "C" int greet() { return 7; }\n'
MAIN = 'extern "C" int greet();\nint main() { return greet() - 7; }\n'

# cmake, ninja and the toolchain are all in the image; nothing here
# reaches the network or the conan cache.
SCRIPT = f"""
set -euo pipefail
export PATH={BIN}:$PATH
cd /work
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release {LANE_FLAGS} \
  $EXTRA > /dev/null
cmake --build build > /dev/null
cmake --install build
cmp build/app out/bin/app && echo INSTALLED-IS-THE-SIGNED-FILE
"""


def _in_the_lane(tmp_path: Path, script: str, extra: str = "") -> str:
  done = subprocess.run(
    ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}",
     "-v", f"{tmp_path}:/work", "-e", "BUILDUTIL=lane-test",
     "-e", f"EXTRA={extra}", "--entrypoint", "bash", IMAGE, "-c", script],
    capture_output=True, text=True)
  return done.stdout + done.stderr


def _install_in_the_lane(tmp_path: Path, extra: str) -> str:
  for name, text in (("CMakeLists.txt", PROJECT), ("greet.cpp", GREET),
                     ("main.cpp", MAIN)):
    (tmp_path / name).write_text(text)
  return _in_the_lane(tmp_path, SCRIPT, extra)


@lane
def test_the_install_step_invalidates_the_signature_without_the_flag(tmp_path):
  """The reported failure, in the image: the RPATH edit rewrites signed
  bytes of the copy and says so."""
  output = _install_in_the_lane(tmp_path, "")
  assert "will invalidate the code signature" in output
  assert "INSTALLED-IS-THE-SIGNED-FILE" not in output


@lane
def test_the_flag_installs_the_file_that_was_signed(tmp_path):
  output = _install_in_the_lane(tmp_path, "-DCMAKE_BUILD_WITH_INSTALL_RPATH=ON")
  assert "will invalidate the code signature" not in output
  assert "INSTALLED-IS-THE-SIGNED-FILE" in output


# ------------------------------------ the rpath the machinery computes --

CFG = {"cmake_option_prefix": "ACME", "module_define_prefix": "ACM"}

ROOT_CMAKE = """\
cmake_minimum_required(VERSION 3.25)
project(lane CXX)
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

# The lane's rpath policy from the signature fix above, plus the shared
# linkage that gives the app a dylib to find at all.
MACHINERY_SCRIPT = f"""
set -euo pipefail
export PATH={BIN}:$PATH
cd /work
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release {LANE_FLAGS} \
  -DBUILDUTIL_MODULE_LINKAGE=shared -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON \
  > /dev/null
cmake --build build > /dev/null
cmake --install build > /dev/null
{BIN}/arm64-apple-darwin25-otool -l out/tool | grep -A2 LC_RPATH | grep ' path '
"""


def _mirrored_app_in_the_lane(tmp_path: Path) -> str:
  """A buildutil tree, not a hand-written one: the rpath under test is
  the one the machinery computes."""
  deposit.ensure(tmp_path, CFG)
  (tmp_path / "CMakeLists.txt").write_text(ROOT_CMAKE)
  src = tmp_path / "sources"
  src.mkdir()
  (src / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  core = src / "ser" / "core"
  core.mkdir(parents=True)
  (core / "CMakeLists.txt").write_text("Init_submodule()\n")
  (core / "impl.cpp").write_text(CORE_IMPL)
  tool = src / "tool"
  tool.mkdir()
  (tool / "CMakeLists.txt").write_text(
    "Init_submodule()\nLink_dependencies(ser-core)\n")
  (tool / "main.cpp").write_text(TOOL_MAIN)
  return _in_the_lane(tmp_path, MACHINERY_SCRIPT)


@lane
def test_the_installed_executable_carries_a_dyld_rpath(tmp_path):
  """dyld never expands $ORIGIN, so an app carrying it finds neither the
  dylib beside it nor the sibling module's mirror."""
  output = _mirrored_app_in_the_lane(tmp_path)
  assert "@loader_path" in output, output
  assert "@loader_path/ser" in output, output
  assert "$ORIGIN" not in output, output
