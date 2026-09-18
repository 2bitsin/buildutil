"""[resources] end to end: real cmake, real compilers, a real binary.

The claim under test is the one a project actually depends on -- the
running executable hands back the exact bytes of every declared file,
with no file on disk to read -- proved on both back ends, on every
compiler this box has, and against the two traps that made the feature
worth building:

  * the SAME bytes from `#embed` and from the generated array (the
    fallback is not "close enough"); and
  * ccache. buildutil puts ccache in front of every compile and ccache's
    direct mode does not hash what `#embed` pulls in, so editing a
    resource produced a byte-identical binary (found in xoctet). The
    generator writes a content digest into the TU for exactly this, and
    test_a_resource_edit_survives_ccache is what keeps it there.
"""
import os
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

ROOT_CMAKE = """\
cmake_minimum_required(VERSION 3.25)
project(demo CXX)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
add_subdirectory(sources)
"""

# Deliberately small: three files, 268 bytes all told. A resource suite
# that takes a minute is a suite nobody runs.
TEXT = b"<h1>hi</h1>\n"
EVERY_BYTE = bytes(range(256))

MAIN = """\
#include "demo/resources.hpp"
#include <cstdio>

int main() {
  const auto* every = demo::resources::find("sub/all.bin");
  if (!every || every->size != 256) return 1;
  for (int i = 0; i < 256; ++i) {
    if (every->data[i] != static_cast<unsigned char>(i)) return 2;
  }
  const auto* text = demo::resources::find("index.html");
  if (!text || text->size != %d) return 3;
  if (text->mime != "text/html") return 4;
  const auto* nothing = demo::resources::find("empty.dat");
  if (!nothing || nothing->size != 0 || nothing->data != nullptr) return 5;
  // absent is not the same answer as empty
  if (demo::resources::find("no/such/file") != nullptr) return 6;
  if (!demo::resources::get("no/such/file").empty()) return 7;
  if (demo::resources::get("sub/all.bin").size() != 256) return 8;
  if (demo::resources::all().size() != 3) return 9;
  std::fwrite(text->data, 1, text->size, stdout);
  return 0;
}
""" % len(TEXT)


def _scaffold(tmp_path, toml_extra='[resources]\ndir = "assets"\n'):
  (tmp_path / "buildutil.toml").write_text(
    '[project]\nname = "demo"\n' + toml_extra)
  assets = tmp_path / "assets" / "sub"
  assets.mkdir(parents=True)
  (tmp_path / "assets" / "index.html").write_bytes(TEXT)
  (tmp_path / "assets" / "empty.dat").write_bytes(b"")
  (assets / "all.bin").write_bytes(EVERY_BYTE)
  (tmp_path / "CMakeLists.txt").write_text(ROOT_CMAKE)
  src = tmp_path / "sources"
  (src / "demo").mkdir(parents=True)
  (src / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  (src / "demo" / "CMakeLists.txt").write_text("Init_submodule()\n")
  (src / "demo" / "main.cpp").write_text(MAIN)
  _render(tmp_path)
  return tmp_path


def _render(root):
  """The deposit as the driver renders it, [resources] and all."""
  import tomllib
  from buildutil.config import _resource_sets
  raw = tomllib.loads((root / "buildutil.toml").read_text())
  deposit.ensure(root, {
    "name": "demo", "cmake_option_prefix": "DEMO",
    "module_define_prefix": "DEM",
    "resources": _resource_sets(raw, "demo")})


def _configure(root, build, *extra, compiler=None, launcher=None):
  cmd = ["cmake", "-S", str(root), "-B", str(build), "-G", "Ninja",
         f"-DBUILDUTIL_PY={sys.executable}",
         f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}", *extra]
  if compiler:
    cmd.append(f"-DCMAKE_CXX_COMPILER={compiler}")
  if launcher:
    cmd.append(f"-DCMAKE_CXX_COMPILER_LAUNCHER={launcher}")
  return subprocess.run(cmd, capture_output=True, text=True)


def _build(build, env=None):
  return subprocess.run(["cmake", "--build", str(build)],
                        capture_output=True, text=True, env=env)


def _ok(proc):
  assert proc.returncode == 0, proc.stdout + proc.stderr
  return proc


def _compilers():
  """Every C++ compiler on this box, so the matrix is what is actually
  installed rather than a list that goes stale. The box ships gcc 16 and
  clang 20; CI images may ship one. `None` means "whatever cmake picks" --
  never an EMPTY list, which would collect zero cases and read exactly
  like a pass."""
  found = [name for name in ("g++", "clang++") if shutil.which(name)]
  return found or [None]


@e2e
@pytest.mark.parametrize("compiler", _compilers())
@pytest.mark.parametrize("fallback", ["OFF", "ON"])
def test_the_binary_hands_back_the_exact_bytes(tmp_path, compiler, fallback):
  """Both back ends, every compiler. -DBUILDUTIL_EMBED_FALLBACK=ON is how
  a box with `#embed` still exercises the path MSVC will take."""
  root = _scaffold(tmp_path)
  build = tmp_path / "b"
  configure = _ok(_configure(root, build,
                             f"-DBUILDUTIL_EMBED_FALLBACK={fallback}",
                             compiler=compiler))
  built = _ok(_build(build))
  if fallback == "ON":
    assert "(array)" in built.stdout, built.stdout
  app = build / "bin" / "demo"
  run = subprocess.run([str(app)], capture_output=True)
  assert run.returncode == 0, (
    f"the embedded bytes did not survive to runtime (check {run.returncode} "
    f"in main.cpp)\n{configure.stdout}\n{built.stdout}")
  assert run.stdout == TEXT
  # nothing is read at run time: the resource directory can go entirely
  shutil.rmtree(root / "assets")
  assert subprocess.run([str(app)], capture_output=True).stdout == TEXT


@e2e
def test_the_two_back_ends_produce_identical_binaries_bytes(tmp_path):
  """The strongest form of 'both paths produce the same data': build the
  same tree twice, once per back end, and compare what each executable
  reports for every declared name."""
  root = _scaffold(tmp_path)
  outputs = []
  for fallback in ("OFF", "ON"):
    build = tmp_path / f"b{fallback}"
    _ok(_configure(root, build, f"-DBUILDUTIL_EMBED_FALLBACK={fallback}"))
    _ok(_build(build))
    run = subprocess.run([str(build / "bin" / "demo")], capture_output=True)
    assert run.returncode == 0, run.stderr
    outputs.append(run.stdout)
  assert outputs[0] == outputs[1] == TEXT


@e2e
def test_a_resource_edit_rebuilds_the_binary(tmp_path):
  root = _scaffold(tmp_path)
  build = tmp_path / "b"
  _ok(_configure(root, build))
  _ok(_build(build))
  # same LENGTH, different bytes: main.cpp checks the size too, so a
  # length change would pass this test for the wrong reason
  (root / "assets" / "index.html").write_bytes(b"<h1>ed</h1>\n")
  _ok(_build(build))
  assert subprocess.run([str(build / "bin" / "demo")],
                        capture_output=True).stdout == b"<h1>ed</h1>\n"


@e2e
@pytest.mark.skipif(shutil.which("ccache") is None, reason="needs ccache")
def test_a_resource_edit_survives_ccache(tmp_path):
  """buildutil configures ccache as the compiler launcher on every native
  build, and ccache's direct mode hashes a source and its #includes --
  NOT the files `#embed` pulls in. Without the generator's content
  digests this test sees the OLD bytes in a freshly linked binary, which
  is what xoctet hit: edit the UI, rebuild, nothing changes."""
  root = _scaffold(tmp_path)
  build = tmp_path / "b"
  env = dict(os.environ, CCACHE_DIR=str(tmp_path / "ccache"))
  _ok(_configure(root, build, launcher=shutil.which("ccache")))
  _ok(_build(build, env=env))
  assert subprocess.run([str(build / "bin" / "demo")],
                        capture_output=True).stdout == TEXT
  (root / "assets" / "index.html").write_bytes(b"<h1>af</h1>\n")
  _ok(_build(build, env=env))
  assert subprocess.run([str(build / "bin" / "demo")],
                        capture_output=True).stdout == b"<h1>af</h1>\n", \
    "ccache served a stale object for a #embed TU"


@e2e
def test_adding_a_file_reruns_the_generator(tmp_path):
  """The set is a configure-time glob; CONFIGURE_DEPENDS is what makes a
  new file appear without anyone re-running cmake by hand."""
  root = _scaffold(tmp_path)
  build = tmp_path / "b"
  _ok(_configure(root, build))
  _ok(_build(build))
  (root / "assets" / "late.txt").write_bytes(b"late\n")
  _ok(_build(build))
  header = (build / "generated" / "demo" / "resources.hpp").read_text()
  assert "late.txt" in header


@e2e
def test_a_presence_driven_embed_directory_needs_no_toml(tmp_path):
  """`<name>.embed/` inside a module is the declaration, exactly as
  `*.install/` is for the data that ships beside the binary."""
  root = _scaffold(tmp_path, toml_extra="")
  module = root / "sources" / "demo"
  ui = module / "ui.embed" / "sub"
  ui.mkdir(parents=True)
  (module / "ui.embed" / "index.html").write_bytes(TEXT)
  (module / "ui.embed" / "empty.dat").write_bytes(b"")
  (ui / "all.bin").write_bytes(EVERY_BYTE)
  build = tmp_path / "b"
  _ok(_configure(root, build))
  _ok(_build(build))
  assert subprocess.run([str(build / "bin" / "demo")],
                        capture_output=True).stdout == TEXT


@e2e
def test_a_cpp_inside_an_embed_directory_is_data_not_source(tmp_path):
  """The module's source glob is recursive; a .cpp sitting in a resource
  tree must be embedded, never compiled."""
  root = _scaffold(tmp_path, toml_extra="")
  module = root / "sources" / "demo"
  (module / "ui.embed" / "sub").mkdir(parents=True)
  (module / "ui.embed" / "index.html").write_bytes(TEXT)
  (module / "ui.embed" / "empty.dat").write_bytes(b"")
  (module / "ui.embed" / "sub" / "all.bin").write_bytes(EVERY_BYTE)
  # would be a redefinition of main() if it were compiled
  (module / "ui.embed" / "snippet.cpp").write_bytes(b"int main() { return 9; }")
  build = tmp_path / "b"
  _ok(_configure(root, build))
  _ok(_build(build))
  assert (build / "generated" / "demo" / "resources.hpp").read_text().count(
    "snippet.cpp") == 1


@e2e
def test_a_declaration_naming_no_module_is_an_error(tmp_path):
  """A [resources] set nothing claims embeds nothing, silently -- which
  is the failure every presence rule in the machinery exists to avoid."""
  root = _scaffold(tmp_path,
                   '[resources]\ndir = "assets"\nmodule = "typo"\n')
  proc = _configure(root, tmp_path / "b")
  assert proc.returncode != 0
  assert "'typo'" in proc.stdout + proc.stderr


@e2e
def test_two_modules_cannot_share_one_namespace(tmp_path):
  root = _scaffold(tmp_path, toml_extra="")
  for name in ("one", "two"):
    module = root / "sources" / name
    (module / f"{name}.embed").mkdir(parents=True)
    (module / f"{name}.embed" / "x.txt").write_bytes(b"x")
    (module / "CMakeLists.txt").write_text("Init_submodule()\n")
    (module / f"{name}.cpp").write_text("void " + name + "() {}\n")
  proc = _configure(root, tmp_path / "b")
  assert proc.returncode != 0
  assert "both embed resources into namespace" in proc.stdout + proc.stderr


@e2e
def test_the_module_tests_target_sees_the_generated_header(tmp_path):
  """A *.test.cpp includes the accessor like any other module TU, and
  compiles in parallel with the library -- the ordering has to be stated
  (the split-module lesson, restated for this route)."""
  root = _scaffold(tmp_path)
  (root / "sources" / "demo" / "probe.test.cpp").write_text(
    '#include "demo/resources.hpp"\n'
    "bool probe() { return demo::resources::find(\"index.html\") != nullptr; }\n")
  build = tmp_path / "b"
  _ok(_configure(root, build, "-DBUILD_TESTING=OFF"))
  _ok(_build(build))
