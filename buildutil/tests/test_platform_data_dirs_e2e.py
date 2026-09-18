"""Platform tags on DATA directories: ui.embed.linux/, locale.install.win32/.

The same vocabulary that picks a source (`decode.linux.cpp`) and a module
directory (`host.linux/`) now picks the contents of the two data trees a
module ships -- `*.embed/` (inside the binary) and `*.install/` (beside
it). The untagged directory is the base; a tagged one OVERLAYS it by
name, family before exact, and two applicable directories of the same
specificity claiming one name is a refusal rather than a coin toss.

Three kinds of proof here, because the claim has three halves:

  * WHICH BYTES WIN -- the built binary is asked, on this host, for the
    resource it holds; a merge that picked the base would hand back
    different bytes and the executable says so.
  * WHAT IS NOT THERE -- the dead platform's directory contributes no
    resource, no staged file, and (the part a plain "is it absent" check
    would miss) appears in NO glob: cmake writes every CONFIGURE_DEPENDS
    glob into CMakeFiles/VerifyGlobs.cmake, so a directory named there is
    a re-configure trigger, and editing a Windows asset on Linux must not
    re-run cmake.
  * EVERY OTHER PLATFORM -- the merge happens at CONFIGURE time, so a
    cross-configure with CMAKE_SYSTEM_NAME overridden answers for
    Windows and macOS from this box: the generated resource manifest is
    the merge, written out.

Skipped where there is no cmake or no ninja.
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

# The tags as THIS host sees them, so every assertion below means the
# same thing wherever the suite runs. Windows has no family tag at all --
# `posix` is not live there and `apple` never is -- so the family half of
# each fixture simply is not built there.
if platform.system() == "Windows":
  EXACT, FAMILY, DEAD = "win32", None, "linux"
elif platform.system() == "Darwin":
  EXACT, FAMILY, DEAD = "macos", "apple", "win32"
else:
  EXACT, FAMILY, DEAD = "linux", "posix", "win32"

ROOT_CMAKE = """\
cmake_minimum_required(VERSION 3.25)
project(demo CXX)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
add_subdirectory(sources)
"""

CFG = {"name": "demo", "cmake_option_prefix": "DEMO",
       "module_define_prefix": "DEM"}

# A cross-configure this box can actually reach: cmake is told the TARGET
# system and handed the host compiler with detection forced off, which is
# enough to get all the way through configure -- and the merge is a
# configure-time fact, so the manifest it writes is the whole answer.
# Nothing is compiled or linked from these trees.
FORCED = [
  "-DCMAKE_CXX_COMPILER_FORCED=TRUE",
  "-DCMAKE_CXX_COMPILE_FEATURES=cxx_std_98;cxx_std_11;cxx_std_14;"
  "cxx_std_17;cxx_std_20;cxx_std_23",
  "-DCMAKE_CXX20_STANDARD_COMPILE_OPTION=-std=c++20",
  "-DCMAKE_CXX_STANDARD_DEFAULT=17",
]


def _tree(root: Path, files: dict[str, str], main: str) -> Path:
  """One module, `demo`, holding {module-relative path: text}."""
  root.mkdir(parents=True, exist_ok=True)
  deposit.ensure(root, CFG)
  (root / "CMakeLists.txt").write_text(ROOT_CMAKE)
  src = root / "sources"
  (src / "demo").mkdir(parents=True)
  (src / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  (src / "demo" / "CMakeLists.txt").write_text("Init_submodule()\n")
  (src / "demo" / "main.cpp").write_text(main)
  for rel, text in files.items():
    path = src / "demo" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
  return root


def _configure(root: Path, build: Path, *extra) -> subprocess.CompletedProcess:
  return subprocess.run(
    ["cmake", "-S", str(root), "-B", str(build), "-G", "Ninja",
     f"-DBUILDUTIL_PY={sys.executable}",
     f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}", *extra],
    capture_output=True, text=True)


def _cross_configure(root: Path, build: Path, system: str):
  compiler = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")
  return _configure(root, build, f"-DCMAKE_SYSTEM_NAME={system}",
                    f"-DCMAKE_CXX_COMPILER={compiler}", *FORCED)


def _ok(proc):
  assert proc.returncode == 0, proc.stdout + proc.stderr
  return proc


def _manifest(build: Path) -> dict[str, str]:
  """{resource name: the directory it was taken from} -- the merge, as
  the generator will see it."""
  text = (build / "generated" / "demo" / "resources.manifest").read_text()
  out = {}
  for line in text.splitlines():
    if not line:
      continue
    name, path = line.split("\t", 1)
    out[name] = Path(path).parent.name
  return out


# --------------------------------------------------------------------------
# *.embed/ -- which bytes end up in the binary
# --------------------------------------------------------------------------

def _embed_fixture() -> tuple[dict[str, str], dict[str, str], list[str]]:
  """(files, expected resource->text, names that must be absent)."""
  files = {
    "ui.embed/shared.txt": "base",
    "ui.embed/over.txt": "base",
    "ui.embed/family.txt": "base",
    f"ui.embed.{EXACT}/over.txt": "exact",
    f"ui.embed.{EXACT}/exact-only.txt": "exact",
    f"ui.embed.{DEAD}/over.txt": "dead",
    f"ui.embed.{DEAD}/dead-only.txt": "dead",
  }
  expected = {"shared.txt": "base", "over.txt": "exact",
              "family.txt": "base", "exact-only.txt": "exact"}
  if FAMILY:
    files[f"ui.embed.{FAMILY}/over.txt"] = "family"
    files[f"ui.embed.{FAMILY}/family.txt"] = "family"
    files[f"ui.embed.{FAMILY}/family-only.txt"] = "family"
    expected["family.txt"] = "family"
    expected["family-only.txt"] = "family"
  return files, expected, ["dead-only.txt"]


def _main_for(expected: dict[str, str], absent: list[str]) -> str:
  """A main() that asks the BINARY what it holds. Every mismatch has its
  own exit code, so a failure says which resource lied."""
  body = []
  code = 10
  for name, text in sorted(expected.items()):
    body.append(f"""\
  {{
    const auto* r = demo::resources::find("{name}");
    if (!r) return {code};
    if (std::string_view(reinterpret_cast<const char*>(r->data), r->size)
        != "{text}") return {code + 1};
  }}""")
    code += 2
  for name in absent:
    body.append(f"""\
  if (demo::resources::find("{name}") != nullptr) return {code};""")
    code += 1
  body.append(f"  if (demo::resources::all().size() != {len(expected)}) "
              f"return {code};")
  return ("#include \"demo/resources.hpp\"\n#include <string_view>\n\n"
          "int main() {\n" + "\n".join(body) + "\n  return 0;\n}\n")


@e2e
def test_the_most_specific_embed_directory_wins(tmp_path):
  """base < family < exact, by resource NAME: a name only the base has
  survives, a name the family adds survives, and a name all three carry
  comes back with the exact platform's bytes."""
  files, expected, absent = _embed_fixture()
  root = _tree(tmp_path, files, _main_for(expected, absent))
  _ok(_configure(root, tmp_path / "b"))
  _ok(subprocess.run(["cmake", "--build", str(tmp_path / "b")],
                     capture_output=True, text=True))
  ran = subprocess.run([str(tmp_path / "b" / "bin" / "demo")],
                       capture_output=True, text=True)
  assert ran.returncode == 0, (
    f"the running binary rejected its own resources (code {ran.returncode})")


@e2e
def test_a_dead_platform_embed_directory_is_never_even_globbed(tmp_path):
  """Absent is not enough. cmake records every CONFIGURE_DEPENDS glob in
  VerifyGlobs.cmake and re-runs when one of them changes, so a win32
  directory NAMED there would make editing a Windows asset on Linux
  re-configure the build."""
  files, expected, absent = _embed_fixture()
  root = _tree(tmp_path, files, _main_for(expected, absent))
  _ok(_configure(root, tmp_path / "b"))
  assert f"ui.embed.{DEAD}" not in _manifest(tmp_path / "b").values()
  globs = (tmp_path / "b" / "CMakeFiles" / "VerifyGlobs.cmake").read_text()
  assert f"ui.embed.{DEAD}" not in globs, (
    "the dead platform's directory is in a CONFIGURE_DEPENDS glob -- "
    "touching a file in it would re-run cmake")
  # the other half, or "nothing is globbed" would pass this test: the
  # LIVE tagged directory must be watched, so adding a file to it is a
  # re-configure and not a silently missing resource
  assert f"ui.embed.{EXACT}" in globs, (
    "the live platform's directory is in no glob -- a file added to it "
    "would be embedded by nobody")


@e2e
def test_two_directories_of_the_same_specificity_are_a_configure_error(tmp_path):
  """Two BASE directories claiming one resource name: the refusal that
  was already there, still there, and now naming both directories."""
  root = _tree(tmp_path, {"ui.embed/clash.txt": "one",
                          "alt.embed/clash.txt": "two"},
               "int main() { return 0; }\n")
  cfg = _configure(root, tmp_path / "b")
  assert cfg.returncode != 0
  assert "clash.txt" in cfg.stderr and "same" in cfg.stderr
  assert "ui.embed" in cfg.stderr and "alt.embed" in cfg.stderr


@e2e
def test_a_tag_before_the_suffix_is_refused(tmp_path):
  """`ui.linux.embed/` reads as an untagged set named `ui.linux` and
  would ship everywhere -- the silent wrong answer, so it is loud."""
  root = _tree(tmp_path, {"ui.linux.embed/x.txt": "x"},
               "int main() { return 0; }\n")
  cfg = _configure(root, tmp_path / "b")
  assert cfg.returncode != 0
  assert "ui.embed.linux" in cfg.stderr, cfg.stderr


@e2e
def test_a_source_inside_a_tagged_data_directory_is_not_compiled(tmp_path):
  """*.embed/ and *.install/ are DATA. The source glob is recursive, and
  the untagged spelling was already excluded from it; the tagged one has
  to be too, or a .cpp dropped in there compiles into the module."""
  poison = '#error "a data directory is not a source directory"\n'
  root = _tree(tmp_path, {
    "ui.embed/a.txt": "a",
    f"ui.embed.{EXACT}/bad.cpp": poison,
    f"data.install.{EXACT}/bad.cpp": poison},
    "int main() { return 0; }\n")
  _ok(_configure(root, tmp_path / "b"))
  built = subprocess.run(["cmake", "--build", str(tmp_path / "b")],
                         capture_output=True, text=True)
  assert built.returncode == 0, built.stdout + built.stderr


# --------------------------------------------------------------------------
# *.install/ -- which bytes end up beside the binary
# --------------------------------------------------------------------------

@e2e
def test_the_most_specific_install_directory_wins_in_both_trees(tmp_path):
  """The staged BUILD tree and the INSTALL tree have to agree, and both
  have to hold the winner only -- a merge that emitted as it walked would
  copy the base over the exact one, or not, depending on order."""
  files = {
    "data.install/keep.txt": "base",
    "data.install/over.txt": "base",
    f"data.install.{EXACT}/over.txt": "exact",
    f"data.install.{EXACT}/sub/nested.txt": "exact",
    f"data.install.{DEAD}/over.txt": "dead",
    f"data.install.{DEAD}/dead-only.txt": "dead",
  }
  if FAMILY:
    files[f"data.install.{FAMILY}/over.txt"] = "family"
    files[f"data.install.{FAMILY}/family-only.txt"] = "family"
  root = _tree(tmp_path, files, "int main() { return 0; }\n")
  build = tmp_path / "b"
  _ok(_configure(root, build))
  _ok(subprocess.run(["cmake", "--build", str(build)],
                     capture_output=True, text=True))
  prefix = tmp_path / "inst"
  _ok(subprocess.run(["cmake", "--install", str(build),
                      "--prefix", str(prefix)],
                     capture_output=True, text=True))
  for tree in (build, prefix):
    assert (tree / "over.txt").read_text() == "exact", tree
    assert (tree / "keep.txt").read_text() == "base", tree
    assert (tree / "sub" / "nested.txt").read_text() == "exact", tree
    assert not (tree / "dead-only.txt").exists(), tree
    if FAMILY:
      assert (tree / "family-only.txt").read_text() == "family", tree


@e2e
def test_a_dead_platform_install_directory_is_never_globbed(tmp_path):
  root = _tree(tmp_path, {
    "data.install/keep.txt": "base",
    f"data.install.{EXACT}/over.txt": "exact",
    f"data.install.{DEAD}/over.txt": "dead"},
    "int main() { return 0; }\n")
  _ok(_configure(root, tmp_path / "b"))
  globs = (tmp_path / "b" / "CMakeFiles" / "VerifyGlobs.cmake").read_text()
  assert f"data.install.{DEAD}" not in globs
  assert f"data.install.{EXACT}" in globs


@e2e
def test_two_install_directories_of_the_same_specificity_are_an_error(tmp_path):
  root = _tree(tmp_path, {"one.install/clash.txt": "one",
                          "two.install/clash.txt": "two"},
               "int main() { return 0; }\n")
  cfg = _configure(root, tmp_path / "b")
  assert cfg.returncode != 0
  assert "clash.txt" in cfg.stderr
  assert "one.install" in cfg.stderr and "two.install" in cfg.stderr


# --------------------------------------------------------------------------
# every other platform, answered from this box at configure time
# --------------------------------------------------------------------------

CROSS = {
  "Windows": {"over.txt": "ui.embed.win32", "win-only.txt": "ui.embed.win32",
              "shared.txt": "ui.embed", "family.txt": "ui.embed"},
  "Darwin": {"over.txt": "ui.embed.macos", "family.txt": "ui.embed.posix",
             "mac-only.txt": "ui.embed.macos", "shared.txt": "ui.embed"},
  "Linux": {"over.txt": "ui.embed.linux", "family.txt": "ui.embed.posix",
            "shared.txt": "ui.embed"},
}

CROSS_FILES = {
  "ui.embed/shared.txt": "base",
  "ui.embed/over.txt": "base",
  "ui.embed/family.txt": "base",
  "ui.embed.posix/over.txt": "posix",
  "ui.embed.posix/family.txt": "posix",
  "ui.embed.linux/over.txt": "linux",
  "ui.embed.macos/over.txt": "macos",
  "ui.embed.macos/mac-only.txt": "macos",
  "ui.embed.win32/over.txt": "win32",
  "ui.embed.win32/win-only.txt": "win32",
}


@e2e
@pytest.mark.skipif(platform.system() != "Linux",
                    reason="the forced cross-configure is set up for this box")
@pytest.mark.parametrize("system", sorted(CROSS))
def test_the_target_platform_decides_not_the_host(tmp_path, system):
  """Selection is by TARGET platform, cross builds included. The merge is
  a configure-time fact, so the manifest written for a Windows or macOS
  target IS the answer -- no Windows compiler required to check it."""
  root = _tree(tmp_path, CROSS_FILES, "int main() { return 0; }\n")
  build = tmp_path / "b"
  _ok(_cross_configure(root, build, system))
  assert _manifest(build) == CROSS[system]


@e2e
@pytest.mark.skipif(platform.system() != "Linux",
                    reason="the forced cross-configure is set up for this box")
def test_posix_and_apple_together_on_macos_is_a_refusal(tmp_path):
  """Nothing orders `ui.embed.posix/` against `ui.embed.apple/` on a Mac:
  both are family tags, both are live, and buildutil refuses to invent a
  rule. The same tree configures fine for Linux, where only one of them
  is live -- which is what makes this a specificity rule and not a ban on
  the word `apple`."""
  files = {"ui.embed/x.txt": "base",
           "ui.embed.posix/x.txt": "posix",
           "ui.embed.apple/x.txt": "apple"}
  root = _tree(tmp_path, files, "int main() { return 0; }\n")
  mac = _cross_configure(root, tmp_path / "bmac", "Darwin")
  assert mac.returncode != 0
  assert "ui.embed.posix" in mac.stderr and "ui.embed.apple" in mac.stderr
  _ok(_cross_configure(root, tmp_path / "blin", "Linux"))
  assert _manifest(tmp_path / "blin") == {"x.txt": "ui.embed.posix"}
