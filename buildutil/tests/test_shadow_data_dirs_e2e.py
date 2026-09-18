"""The shadow tree is a source tree: generated/<module>/ obeys the same
layout conventions as sources/<module>/.

`${CMAKE_BINARY_DIR}/generated/<module>/` was already where configure.py,
Add_generated_source and the resource generator emit, and already an
include root. What is asserted here is the rule that makes it a MODULE
DIRECTORY rather than a bag of outputs: `generated/<m>/ui.embed/` is a
resource set, `generated/<m>/data.install/` is runtime data, the platform
tags work there, the overlay levels are the same ones, and a name that a
hand-written file and a generated one both claim is a configure error
naming both.

No generator runs here on purpose. A file written into the shadow tree
before configure is exactly what a generator leaves behind, and testing
the CONVENTION rather than one producer of it is the point -- TypeScript
(test_typescript_e2e.py) is only the first thing that fills the tree.
"""
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from buildutil import deposit

PYSUPPORT = Path(deposit.__file__).resolve().parent / "pysupport"

e2e = pytest.mark.skipif(
  shutil.which("cmake") is None or shutil.which("ninja") is None,
  reason="needs cmake and ninja")

if platform.system() == "Windows":
  EXACT, DEAD = "win32", "linux"
elif platform.system() == "Darwin":
  EXACT, DEAD = "macos", "win32"
else:
  EXACT, DEAD = "linux", "win32"

ROOT_CMAKE = """\
cmake_minimum_required(VERSION 3.25)
project(demo CXX)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
add_subdirectory(sources)
"""

CFG = {"name": "demo", "cmake_option_prefix": "DEMO",
       "module_define_prefix": "DEM"}


def _tree(root: Path, source_files: dict, shadow_files: dict,
          main: str, build: str = "b", shared_files: dict | None = None) -> Path:
  """A module `demo`, plus files planted in its shadow tree exactly
  where a generator would have left them -- per profile, and in the
  build-invariant shared root that `output_dir(shared=True)` hands out."""
  root.mkdir(parents=True, exist_ok=True)
  deposit.ensure(root, CFG)
  (root / "CMakeLists.txt").write_text(ROOT_CMAKE)
  src = root / "sources"
  (src / "demo").mkdir(parents=True)
  (src / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  (src / "demo" / "CMakeLists.txt").write_text("Init_submodule()\n")
  (src / "demo" / "main.cpp").write_text(main)
  for rel, text in source_files.items():
    path = src / "demo" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
  for rel, text in shadow_files.items():
    path = root / build / "generated" / "demo" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
  for rel, text in (shared_files or {}).items():
    path = root / "_build" / "generated" / "demo" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
  return root


def _configure(root: Path, build: Path, *extra):
  return subprocess.run(
    ["cmake", "-S", str(root), "-B", str(build), "-G", "Ninja",
     f"-DBUILDUTIL_PY={sys.executable}",
     f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}", *extra],
    capture_output=True, text=True)


def _ok(proc):
  assert proc.returncode == 0, proc.stdout + proc.stderr
  return proc


def _manifest(build: Path) -> dict[str, str]:
  text = (build / "generated" / "demo" / "resources.manifest").read_text()
  out = {}
  for line in text.splitlines():
    if line:
      name, path = line.split("\t", 1)
      out[name] = path
  return out


REPORT = """\
#include "demo/resources.hpp"
#include <cstdio>
int main() {
  for (const auto& r : demo::resources::all()) {
    std::printf("%.*s=%.*s\\n", (int)r.name.size(), r.name.data(),
                (int)r.size, reinterpret_cast<const char*>(r.data));
  }
  return 0;
}
"""


@e2e
def test_a_generated_embed_directory_is_a_resource_set(tmp_path):
  """The whole claim, at its simplest: nothing declares the shadow
  directory, nothing lists the file, and the built binary holds it."""
  root = _tree(tmp_path,
               {"ui.embed/hand.txt": "hand"},
               {"ui.embed/gen.txt": "generated"}, REPORT)
  build = tmp_path / "b"
  _ok(_configure(root, build))
  _ok(subprocess.run(["cmake", "--build", str(build)],
                     capture_output=True, text=True))
  ran = _ok(subprocess.run([str(build / "bin" / "demo")],
                           capture_output=True, text=True))
  assert "gen.txt=generated" in ran.stdout
  assert "hand.txt=hand" in ran.stdout


@e2e
def test_the_platform_tags_work_in_the_shadow_tree_too(tmp_path):
  """Same overlay, same levels, across the two trees: a generated
  `ui.embed.<exact>/` beats a hand-written `ui.embed/`, and a generated
  directory tagged for another platform contributes nothing."""
  root = _tree(tmp_path,
               {"ui.embed/over.txt": "hand"},
               {f"ui.embed.{EXACT}/over.txt": "generated",
                f"ui.embed.{DEAD}/dead.txt": "dead"}, REPORT)
  build = tmp_path / "b"
  _ok(_configure(root, build))
  manifest = _manifest(build)
  assert f"ui.embed.{EXACT}" in manifest["over.txt"], manifest
  assert "dead.txt" not in manifest
  globs = (build / "CMakeFiles" / "VerifyGlobs.cmake").read_text()
  assert f"ui.embed.{DEAD}" not in globs


@e2e
def test_a_generated_name_colliding_with_a_hand_written_one_is_refused(tmp_path):
  """The refusal the issue asked for by name. Both are base level, so
  nothing orders them -- and a build where you cannot tell which of the
  two you are looking at is worse than one that will not configure."""
  root = _tree(tmp_path,
               {"ui.embed/app.js": "hand"},
               {"ui.embed/app.js": "generated"}, REPORT)
  cfg = _configure(root, tmp_path / "b")
  assert cfg.returncode != 0
  assert "app.js" in cfg.stderr
  assert "sources/demo/ui.embed" in cfg.stderr.replace("\\", "/")
  assert "generated/demo/ui.embed" in cfg.stderr.replace("\\", "/")


@e2e
def test_a_generated_install_directory_ships_like_any_other(tmp_path):
  """*.install/ is the other half of the same rule, and it has to reach
  BOTH trees -- the staged build root and the install prefix."""
  root = _tree(tmp_path,
               {"data.install/hand.txt": "hand"},
               {"data.install/gen.txt": "generated",
                f"data.install.{EXACT}/hand.txt": "generated-wins"},
               "int main() { return 0; }\n")
  build = tmp_path / "b"
  _ok(_configure(root, build))
  _ok(subprocess.run(["cmake", "--build", str(build)],
                     capture_output=True, text=True))
  prefix = tmp_path / "inst"
  _ok(subprocess.run(["cmake", "--install", str(build),
                      "--prefix", str(prefix)],
                     capture_output=True, text=True))
  for tree in (build, prefix):
    assert (tree / "gen.txt").read_text() == "generated", tree
    assert (tree / "hand.txt").read_text() == "generated-wins", tree


@e2e
def test_the_shared_root_is_a_module_directory_too(tmp_path):
  """`output_dir(shared=True)` is the build-invariant root, for output
  that cannot vary by build type -- and it was an INCLUDE root only. A
  hook that put a payload there got a green build with nothing embedded
  and nothing said, so the one thing a shared payload is for (transpile
  once, not once per profile) could not be done."""
  root = _tree(tmp_path, {}, {}, REPORT,
               shared_files={"ui.embed/shared.txt": "shared"})
  build = tmp_path / "b"
  _ok(_configure(root, build))
  _ok(subprocess.run(["cmake", "--build", str(build)],
                     capture_output=True, text=True))
  ran = _ok(subprocess.run([str(build / "bin" / "demo")],
                           capture_output=True, text=True))
  assert "shared.txt=shared" in ran.stdout


@e2e
def test_a_shared_install_directory_ships_like_any_other(tmp_path):
  root = _tree(tmp_path, {}, {}, "int main() { return 0; }\n",
               shared_files={"data.install/shared.txt": "shared"})
  build = tmp_path / "b"
  _ok(_configure(root, build))
  _ok(subprocess.run(["cmake", "--build", str(build)],
                     capture_output=True, text=True))
  prefix = tmp_path / "inst"
  _ok(subprocess.run(["cmake", "--install", str(build), "--prefix", str(prefix)],
                     capture_output=True, text=True))
  for tree in (build, prefix):
    assert (tree / "shared.txt").read_text() == "shared", tree


@e2e
def test_the_two_generated_roots_may_not_claim_one_name(tmp_path):
  """Same refusal as source-versus-generated: nothing orders two base
  level sets, so the build that cannot say which file it holds is the
  build that does not configure."""
  root = _tree(tmp_path, {}, {"ui.embed/app.js": "per-profile"}, REPORT,
               shared_files={"ui.embed/app.js": "shared"})
  cfg = _configure(root, tmp_path / "b")
  assert cfg.returncode != 0
  assert "app.js" in cfg.stderr
  assert "_build/generated/demo/ui.embed" in cfg.stderr.replace("\\", "/")
