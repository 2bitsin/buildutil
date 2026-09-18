"""What a module's configure.py may do with the shadow tree, end to end.

The rule under test is the one buildutil actually owns: the generated
root is a MODULE DIRECTORY, so a hook that writes a file into
`generated/<m>/ui.embed/` has added an embedded resource -- no
declaration, no cmake, and the accessor and the ccache digest follow.
buildutil does not know or care what produced the bytes; the hook here
does with `emit()` and `emit_bytes()` what a real one does with a
transpiler and a compressor.

The helpers exist so the hook does not spell those paths by hand:
`data_dir('ui.embed')` is the one place that knows a platform tag goes
AFTER the suffix and that the vocabulary is closed. `inputs()` is what
makes an edit to a source the hook read re-run the hook.
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

REPORT = """\
#include "demo/resources.hpp"
#include <cstdio>
int main() {
  for (const auto& r : demo::resources::all()) {
    std::printf("[%.*s]%.*s\\n", (int)r.name.size(), r.name.data(),
                (int)r.size, reinterpret_cast<const char*>(r.data));
  }
  return 0;
}
"""

# A hook shaped exactly like the real one: read sources the module owns,
# transform them, put the result in the shadow data directory. The
# "transform" is upper-casing rather than a transpiler, because what is
# under test is the placement, not anybody's toolchain.
HOOK = """\
import buildutil_configure as cfg

found = cfg.inputs('*.src')
for source in found:
  cfg.emit('ui.embed/' + source.stem + '.txt', source.read_text().upper())
cfg.emit_bytes('ui.embed/packed.bin', bytes(range(8)))
print(f'{len(found)} file(s) for {cfg.module_name()} on {cfg.target_system()}')
"""


def _tree(root: Path, files: dict, hook: str = HOOK,
          main: str = REPORT) -> Path:
  root.mkdir(parents=True, exist_ok=True)
  deposit.ensure(root, {"name": "demo", "cmake_option_prefix": "DEMO",
                        "module_define_prefix": "DEM"})
  (root / "CMakeLists.txt").write_text(ROOT_CMAKE)
  src = root / "sources"
  (src / "demo").mkdir(parents=True)
  (src / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  (src / "demo" / "CMakeLists.txt").write_text("Init_submodule()\n")
  (src / "demo" / "main.cpp").write_text(main)
  (src / "demo" / "configure.py").write_text(hook)
  for rel, text in files.items():
    path = src / "demo" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
  return root


def _configure(root: Path, build: Path, *extra):
  return subprocess.run(
    ["cmake", "-S", str(root), "-B", str(build), "-G", "Ninja",
     f"-DBUILDUTIL_PY={sys.executable}",
     f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}",
     f"-DPython3_EXECUTABLE={sys.executable}", *extra],
    capture_output=True, text=True)


def _ok(proc):
  assert proc.returncode == 0, proc.stdout + proc.stderr
  return proc


def _build(build: Path):
  return subprocess.run(["cmake", "--build", str(build)],
                        capture_output=True, text=True)


def _run(build: Path):
  return _ok(subprocess.run([str(build / "bin" / "demo")],
                            capture_output=True, text=True)).stdout


@e2e
def test_what_the_hook_writes_into_the_shadow_embed_dir_is_a_resource(tmp_path):
  """The whole claim. Nothing declares `page.txt`; the hook wrote it into
  generated/demo/ui.embed/ and the binary holds it."""
  root = _tree(tmp_path, {"page.src": "hello\n", "ui.embed/hand.txt": "hand"})
  build = tmp_path / "b"
  _ok(_configure(root, build))
  _ok(_build(build))
  out = _run(build)
  assert "[page.txt]HELLO" in out
  assert "[hand.txt]hand" in out, "the hand-written half of the set went missing"
  assert "[packed.bin]" in out, "emit_bytes() output was not embedded"


@e2e
def test_editing_a_declared_input_re_runs_the_hook_and_the_binary_changes(
    tmp_path):
  """inputs() is what closes the loop: cmake re-runs configure when a
  declared input changes, the hook regenerates, and the TU that embeds
  the result recompiles -- with ccache in front of it, which is the
  reason the generator writes a content digest at all."""
  if shutil.which("ccache") is None:
    pytest.skip("needs ccache on PATH")
  root = _tree(tmp_path, {"page.src": "before\n"})
  build = tmp_path / "b"
  env = dict(os.environ, CCACHE_DIR=str(tmp_path / "ccache"))
  _ok(_configure(root, build, "-DCMAKE_CXX_COMPILER_LAUNCHER=ccache"))
  _ok(_build(build))
  assert "[page.txt]BEFORE" in _run(build)

  (root / "sources" / "demo" / "page.src").write_text("after\n")
  built = subprocess.run(["cmake", "--build", str(build)],
                         capture_output=True, text=True, env=env)
  assert built.returncode == 0, built.stdout + built.stderr
  assert "[page.txt]AFTER" in _run(build), (
    "the rebuilt binary still holds the old bytes -- either configure did "
    "not re-run, or ccache served a stale object for a resource it cannot "
    "see")


@e2e
def test_adding_a_file_the_hook_reads_re_runs_it(tmp_path):
  """The other half of inputs(), and the one a file list alone cannot do:
  a NEW file is in nobody's dependencies, so without the directories
  declared too cmake would not re-run, the hook would never see it, and
  the build would be green with a resource missing -- exactly what the
  *.embed/ globs use CONFIGURE_DEPENDS to prevent."""
  root = _tree(tmp_path, {"one.src": "first\n"})
  build = tmp_path / "b"
  _ok(_configure(root, build))
  _ok(_build(build))
  assert "[two.txt]" not in _run(build)

  (root / "sources" / "demo" / "two.src").write_text("second\n")
  _ok(_build(build))
  assert "[two.txt]SECOND" in _run(build), (
    "the hook never saw the new file -- configure did not re-run")


@e2e
def test_a_generated_name_colliding_with_a_hand_written_one_is_refused(tmp_path):
  """Both are base level, so nothing orders them, and a build where you
  cannot tell which of the two you are looking at is worse than one that
  will not configure."""
  root = _tree(tmp_path, {"page.src": "x\n", "ui.embed/page.txt": "by hand"})
  cfg = _configure(root, tmp_path / "b")
  assert cfg.returncode != 0
  assert "page.txt" in cfg.stderr
  assert "sources/demo/ui.embed" in cfg.stderr.replace("\\", "/")
  assert "generated/demo/ui.embed" in cfg.stderr.replace("\\", "/")


TAGGED_HOOK = """\
import buildutil_configure as cfg

(cfg.data_dir('ui.embed') / 'over.txt').write_text('base')
tag = {'Linux': 'linux', 'Darwin': 'macos', 'Windows': 'win32'}[
    cfg.target_system()]
(cfg.data_dir('ui.embed', tag=tag) / 'over.txt').write_text('exact')
(cfg.data_dir('ui.embed', tag='win32' if tag != 'win32' else 'linux')
 / 'dead.txt').write_text('dead')
"""


@e2e
def test_data_dir_places_a_platform_tagged_payload_where_the_glob_reads_it(
    tmp_path):
  """The shadow tree takes the tags too, with the same overlay levels:
  the hook's tagged copy wins over its own untagged one, and what it
  wrote for another platform contributes nothing here."""
  root = _tree(tmp_path, {}, hook=TAGGED_HOOK)
  build = tmp_path / "b"
  _ok(_configure(root, build))
  _ok(_build(build))
  out = _run(build)
  assert "[over.txt]exact" in out
  assert "dead.txt" not in out


BAD_TAG_HOOK = """\
import buildutil_configure as cfg
cfg.data_dir('ui.embed', tag='freebsd')
"""


@e2e
def test_a_tag_the_vocabulary_does_not_have_is_refused(tmp_path):
  """A typo'd tag would otherwise produce a directory nothing ever globs
  -- a hook that ran, wrote files and changed nothing about the build."""
  root = _tree(tmp_path, {}, hook=BAD_TAG_HOOK)
  cfg = _configure(root, tmp_path / "b")
  assert cfg.returncode != 0
  assert "freebsd" in cfg.stderr
  assert "win32" in cfg.stderr and "posix" in cfg.stderr


TOOL_HOOK = """\
import buildutil_configure as cfg
cfg.tool('definitely-not-a-real-program', install='apt install unicorn')
"""


@e2e
def test_a_missing_tool_is_refused_with_the_command_that_installs_it(tmp_path):
  """'tsc not found' with no next step is the failure this argument
  exists to prevent."""
  root = _tree(tmp_path, {}, hook=TOOL_HOOK)
  cfg = _configure(root, tmp_path / "b")
  assert cfg.returncode != 0
  assert "definitely-not-a-real-program" in cfg.stderr
  assert "apt install unicorn" in cfg.stderr
  # and it says which places it looked, because "not on PATH" is a lie
  # when the project declared the tool and conan did not resolve it
  assert "PATH" in cfg.stderr
  assert "TOOL" in cfg.stderr


DECLARED_TOOL_HOOK = """\
import buildutil_configure as cfg

found = cfg.tool('pretend-transpiler', install='never needed')
cfg.emit('ui.embed/which.txt', cfg.run([found], what='the pretend tool'))
print('dirs: ' + ';'.join(str(d) for d in cfg.tool_dirs()))
"""


def _fake_tool(directory: Path, name: str, says: str) -> Path:
  """A program that prints where it came from, so the test can tell the
  two copies apart by their OUTPUT rather than by a path it guessed."""
  directory.mkdir(parents=True, exist_ok=True)
  exe = directory / name
  exe.write_text(f"#!/bin/sh\nprintf %s {says}\n")
  exe.chmod(0o755)
  return exe


@e2e
@pytest.mark.skipif(os.name == "nt", reason="the fake tool is a shell script")
def test_a_declared_tool_is_found_where_conan_put_it(tmp_path):
  """A `Require(<x> ... TOOL)` package's bindir lands on
  CMAKE_PROGRAM_PATH -- a cmake VARIABLE that no subprocess inherits. The
  hook gets it anyway, which is what makes a pinned build-time executable
  reachable from the one place that runs build-time executables."""
  bindir = tmp_path / "conan" / "bin"
  _fake_tool(bindir, "pretend-transpiler", "from-the-declared-tool")
  root = _tree(tmp_path, {}, hook=DECLARED_TOOL_HOOK)
  build = tmp_path / "b"
  out = _ok(_configure(root, build, f"-DCMAKE_PROGRAM_PATH={bindir}"))
  assert str(bindir) in out.stdout, "tool_dirs() did not carry the bindir"
  _ok(_build(build))
  assert "[which.txt]from-the-declared-tool" in _run(build)


@e2e
@pytest.mark.skipif(os.name == "nt", reason="the fake tool is a shell script")
def test_the_declared_tool_beats_a_copy_on_path(tmp_path):
  """A project that pinned a version means the pinned one. A machine that
  happens to carry another copy must not quietly win -- that is the whole
  difference between a build input and a fact about somebody's laptop."""
  bindir = tmp_path / "conan" / "bin"
  _fake_tool(bindir, "pretend-transpiler", "from-the-declared-tool")
  on_path = tmp_path / "elsewhere"
  _fake_tool(on_path, "pretend-transpiler", "from-PATH")
  root = _tree(tmp_path, {}, hook=DECLARED_TOOL_HOOK)
  build = tmp_path / "b"
  env = dict(os.environ, PATH=f"{on_path}{os.pathsep}{os.environ['PATH']}")
  proc = subprocess.run(
    ["cmake", "-S", str(root), "-B", str(build), "-G", "Ninja",
     f"-DBUILDUTIL_PY={sys.executable}",
     f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}",
     f"-DPython3_EXECUTABLE={sys.executable}",
     f"-DCMAKE_PROGRAM_PATH={bindir}"],
    capture_output=True, text=True, env=env)
  _ok(proc)
  _ok(subprocess.run(["cmake", "--build", str(build)],
                     capture_output=True, text=True, env=env))
  assert "[which.txt]from-the-declared-tool" in _run(build), (
    "the copy on PATH won over the one the project declared")


@e2e
@pytest.mark.skipif(os.name == "nt", reason="the fake tool is a shell script")
def test_path_still_answers_when_nothing_was_declared(tmp_path):
  """The fallback is not a formality: most tools a hook runs (python,
  git, a system compiler) are not conan packages and never will be."""
  on_path = tmp_path / "elsewhere"
  _fake_tool(on_path, "pretend-transpiler", "from-PATH")
  root = _tree(tmp_path, {}, hook=DECLARED_TOOL_HOOK)
  build = tmp_path / "b"
  env = dict(os.environ, PATH=f"{on_path}{os.pathsep}{os.environ['PATH']}")
  _ok(subprocess.run(
    ["cmake", "-S", str(root), "-B", str(build), "-G", "Ninja",
     f"-DBUILDUTIL_PY={sys.executable}",
     f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}",
     f"-DPython3_EXECUTABLE={sys.executable}"],
    capture_output=True, text=True, env=env))
  _ok(subprocess.run(["cmake", "--build", str(build)],
                     capture_output=True, text=True, env=env))
  assert "[which.txt]from-PATH" in _run(build)


RUN_HOOK = """\
import buildutil_configure as cfg
import sys
cfg.run([sys.executable, '-c',
         'import sys; print("the tool said this"); sys.exit(3)'],
        what='the pretend transpile')
"""


@e2e
def test_a_failing_tool_shows_its_own_output(tmp_path):
  """A compiler's file:line:col is what an editor jumps to, so the tool's
  own output is what the build shows -- the wrapper only says which step
  it was."""
  root = _tree(tmp_path, {}, hook=RUN_HOOK)
  cfg = _configure(root, tmp_path / "b")
  assert cfg.returncode != 0
  assert "the tool said this" in cfg.stderr
  assert "the pretend transpile failed" in cfg.stderr
