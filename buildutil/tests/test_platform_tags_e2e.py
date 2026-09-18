"""Platform tags on DIRECTORIES, compiled rather than asserted.

A file tag (foo.linux.cpp) picks per-OS translation units at scan time.
The same tags now work on a directory — host.linux/, nt.win32/ — the way
*.test/ already tags a whole subtree, so a ported tree that keeps entire
per-host source directories renames the directory instead of suffixing
every file in it.

Proving this needs a compiler, not a regex: the claim is "these TUs are
not built", and the cheapest honest way to show a file was not compiled
is to make it uncompilable and watch the build succeed anyway.

Skipped where there is no cmake or no C++ compiler.
"""
import platform
import shutil
import subprocess

import pytest

from buildutil import deposit

CFG = {"cmake_option_prefix": "ACME", "module_define_prefix": "ACM"}

pytestmark = pytest.mark.skipif(
  shutil.which("cmake") is None or
  not (shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")),
  reason="needs cmake and a C++ compiler")

# tags as this host sees them, so the suite means the same thing everywhere
if platform.system() == "Windows":
  LIVE, DEAD, POSIX_LIVE = "win32", "linux", False
elif platform.system() == "Darwin":
  LIVE, DEAD, POSIX_LIVE = "macos", "win32", True
else:
  LIVE, DEAD, POSIX_LIVE = "linux", "win32", True

ROOT_CMAKE = """\
cmake_minimum_required(VERSION 3.25)
project(plattags CXX)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
add_subdirectory(sources)
"""

POISON = '#error "this TU must never be compiled on this host"\n'


def _tree(root, files: dict):
  """One module, `plat`, holding {module-relative path: text}."""
  root.mkdir(parents=True, exist_ok=True)
  deposit.ensure(root, CFG)
  (root / "CMakeLists.txt").write_text(ROOT_CMAKE)
  src = root / "sources"
  (src / "plat").mkdir(parents=True)
  (src / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  (src / "plat" / "CMakeLists.txt").write_text("Init_submodule()\n")
  for rel, text in files.items():
    path = src / "plat" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
  return root


def _build(root) -> subprocess.CompletedProcess:
  cfg = subprocess.run(
    ["cmake", "-S", str(root), "-B", str(root / "b"), "-G", "Ninja"]
    if shutil.which("ninja") else
    ["cmake", "-S", str(root), "-B", str(root / "b")],
    capture_output=True, text=True)
  assert cfg.returncode == 0, cfg.stdout + cfg.stderr
  return subprocess.run(["cmake", "--build", str(root / "b")],
                        capture_output=True, text=True)


def test_a_dead_platform_directory_is_not_compiled(tmp_path):
  """The poisoned TU is the proof: it cannot compile, and the build is
  green, so it was never handed to the compiler."""
  _tree(tmp_path, {
    "main.cpp": "int from_host();\nint main() { return from_host(); }\n",
    f"host.{LIVE}/impl.cpp": "int from_host() { return 0; }\n",
    f"host.{DEAD}/impl.cpp": POISON + "int from_host() { return 1; }\n"})
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr


def test_a_live_platform_directory_is_compiled(tmp_path):
  """The other half, and the one a 'drop everything' bug would pass: the
  app does not link unless host.<live>/impl.cpp was built."""
  _tree(tmp_path, {
    "main.cpp": "int from_host();\nint main() { return from_host(); }\n",
    f"host.{LIVE}/impl.cpp": "int from_host() { return 0; }\n"})
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr
  assert (tmp_path / "b" / "bin" / "plat").is_file()


@pytest.mark.skipif(not POSIX_LIVE, reason="posix is not live on Windows")
def test_a_posix_directory_is_live_on_linux_and_macos(tmp_path):
  _tree(tmp_path, {
    "main.cpp": ("int from_host();\nint from_posix();\n"
                 "int main() { return from_host() + from_posix(); }\n"),
    f"host.{LIVE}/impl.cpp": "int from_host() { return 0; }\n",
    "shared.posix/impl.cpp": "int from_posix() { return 0; }\n"})
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr


def test_nested_tags_all_have_to_hold(tmp_path):
  """A dead subtree inside a live one is still dead — and a live subtree
  inside a dead one does not come back to life."""
  _tree(tmp_path, {
    "main.cpp": "int from_host();\nint main() { return from_host(); }\n",
    f"host.{LIVE}/impl.cpp": "int from_host() { return 0; }\n",
    f"host.{LIVE}/inner.{DEAD}/bad.cpp": POISON,
    f"host.{DEAD}/inner.{LIVE}/bad.cpp": POISON})
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr


def test_a_directory_tag_and_a_file_tag_both_have_to_hold(tmp_path):
  """The two tag kinds compose the way nested directory tags do: whichever
  says 'not this host' wins. A live directory does not resurrect a dead
  file tag, and a live file tag does not escape a dead directory."""
  _tree(tmp_path, {
    "main.cpp": "int from_host();\nint main() { return from_host(); }\n",
    f"host.{LIVE}/impl.cpp": "int from_host() { return 0; }\n",
    f"host.{LIVE}/bad.{DEAD}.cpp": POISON,
    f"host.{DEAD}/bad.{LIVE}.cpp": POISON})
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr


def test_the_checkouts_own_path_is_not_read_as_a_tag(tmp_path):
  """Classification is project-RELATIVE. These rules match directory
  names, so an absolute path would let the directory someone happens to
  clone into decide the build: a checkout under scratch.win32/ dropped
  every source in the project, silently and everywhere."""
  root = _tree(tmp_path / f"scratch.{DEAD}", {
    "main.cpp": "int from_host();\nint main() { return from_host(); }\n",
    f"host.{LIVE}/impl.cpp": "int from_host() { return 0; }\n"})
  built = _build(root)
  assert built.returncode == 0, built.stdout + built.stderr
  assert (root / "b" / "bin" / "plat").is_file(), (
    "the project built nothing — an ancestor directory of the checkout "
    "was read as a platform tag")
