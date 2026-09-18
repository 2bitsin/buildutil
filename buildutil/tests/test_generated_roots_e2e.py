"""A generated root is reachable both ways, whichever mechanism made it.

There are two sanctioned homes for codegen — Add_generated_source, for a
generator the build itself produces, and a presence-driven configure.py —
and which one a module picks must not change what its output is reachable
AS. It did: both put their root on the include path, only one also set
`--embed-dir`, so an emitted data file was #include-able either way and
#embed-able from only one.

The compile line is the only place that shows it, so this reads the real
one out of compile_commands.json rather than asserting on cmake text.
"""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from buildutil import deposit

CFG = {"cmake_option_prefix": "ACME", "module_define_prefix": "ACM"}
PYSUPPORT = Path(deposit.__file__).resolve().parent / "pysupport"

pytestmark = pytest.mark.skipif(
  shutil.which("cmake") is None or
  not (shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")),
  reason="needs cmake and a C++ compiler")

ROOT_CMAKE = """\
cmake_minimum_required(VERSION 3.25)
project(genroots CXX)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
add_subdirectory(sources)
"""

CONFIGURE_PY = """\
import buildutil_configure as bc
bc.emit("gen/hdr.hpp", "#pragma once\\ninline int gen_v() { return 1; }\\n")
bc.emit("gen/table.inc", "0,1,2,3\\n")
"""


def _embed_works() -> bool:
  """Does this compiler actually implement #embed? The flag being on the
  command line and an #embed RESOLVING are different claims, and only the
  second one is what anybody wanted.

  Runs at IMPORT, because a skipif decorator is evaluated then -- including
  in the pure-python job where there is no compiler at all, and where the
  module-level skipif above has not spared this function. It therefore
  answers 'no' to everything it cannot determine rather than raising: the
  first version passed cxx=None straight to subprocess and took the whole
  collection down with a TypeError."""
  cxx = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")
  if cxx is None:
    return False
  probe = Path(tempfile.mkdtemp())
  (probe / "d").mkdir()
  (probe / "d" / "blob.inc").write_text("1,2,3")
  (probe / "t.cpp").write_text(
    'static const unsigned char b[] = {\n#embed "blob.inc"\n};\n'
    "int main() { return sizeof(b) ? 0 : 1; }\n")
  try:
    return subprocess.run(
      [cxx, "-std=c++26", f"--embed-dir={probe / 'd'}", "-c",
       str(probe / "t.cpp"), "-o", str(probe / "t.o")],
      capture_output=True).returncode == 0
  except OSError:
    return False



def _embed_dir_flag_accepted() -> bool:
  """Does the compiler accept --embed-dir at all? Weaker than _embed_works
  (no #embed needed), and it is what the machinery now probes for before
  emitting the flag — so the tests that assert the flag on a compile line
  have to ask the same question, or they fail on exactly the toolchains
  the probe exists to protect.

  Deliberately reports the same skip reason as the full #embed probe: a
  compiler that rejects the flag has no #embed either, and CI's skip
  allowlist stays a single entry rather than growing one per phrasing."""
  cxx = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")
  if cxx is None:
    return False
  probe_dir = Path(tempfile.mkdtemp())
  (probe_dir / "t.cpp").write_text("int main() { return 0; }\n")
  try:
    return subprocess.run(
      [cxx, f"--embed-dir={probe_dir}", "-c", str(probe_dir / "t.cpp"),
       "-o", str(probe_dir / "t.o")], capture_output=True).returncode == 0
  except OSError:
    return False

def _tree(root):
  deposit.ensure(root, CFG)
  (root / "CMakeLists.txt").write_text(ROOT_CMAKE)
  src = root / "sources"
  (src / "gen").mkdir(parents=True)
  (src / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  (src / "gen" / "CMakeLists.txt").write_text("Init_submodule()\n")
  (src / "gen" / "configure.py").write_text(CONFIGURE_PY)
  (src / "gen" / "unit.cpp").write_text(
    '#include "gen/hdr.hpp"\nint unit() { return gen_v(); }\n')
  return root


def _compile_commands(root) -> list[dict]:
  cfg = subprocess.run(
    ["cmake", "-S", str(root), "-B", str(root / "b"),
     "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
     f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}"],
    capture_output=True, text=True)
  assert cfg.returncode == 0, cfg.stdout + cfg.stderr
  db = root / "b" / "compile_commands.json"
  assert db.is_file(), "cmake exported no compile database"
  return json.loads(db.read_text())



def _build(root) -> subprocess.CompletedProcess:
  """configure + build, with the pysupport path configure.py needs."""
  cfg = subprocess.run(
    ["cmake", "-S", str(root), "-B", str(root / "b"),
     f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}"], capture_output=True, text=True)
  assert cfg.returncode == 0, cfg.stdout + cfg.stderr
  return subprocess.run(["cmake", "--build", str(root / "b")],
                        capture_output=True, text=True)

def _command_for(entries: list[dict], stem: str) -> str:
  for entry in entries:
    if stem in entry["file"]:
      return entry.get("command") or " ".join(entry["arguments"])
  raise AssertionError(f"no compile entry for {stem}")


@pytest.mark.skipif(not _embed_dir_flag_accepted(),
                    reason="compiler has no #embed")
def test_configure_py_roots_reach_the_compile_line_as_embed_dirs(tmp_path):
  """Both roots: the per-profile one under the build dir, and the shared
  one that outlives a profile."""
  root = _tree(tmp_path)
  command = _command_for(_compile_commands(root), "unit.cpp")
  per_profile = tmp_path / "b" / "generated" / "gen"
  shared = tmp_path / "_build" / "generated" / "gen"
  for expected in (per_profile, shared):
    assert f"--embed-dir={expected}" in command, (
      f"{expected} is on the include path but not the #embed search path "
      f"— a file emitted there can be #included and not #embedded\n"
      f"{command}")


def test_those_roots_are_still_include_dirs(tmp_path):
  """The half that already worked, so a fix cannot trade one for the other
  — unit.cpp includes the emitted header, so configure would fail outright,
  but assert the flag too since an -I can come from elsewhere."""
  root = _tree(tmp_path)
  command = _command_for(_compile_commands(root), "unit.cpp")
  per_profile = tmp_path / "b" / "generated" / "gen"
  assert f"-I{per_profile}" in command or f"-I {per_profile}" in command, command


@pytest.mark.skipif(not _embed_dir_flag_accepted(),
                    reason="compiler has no #embed")
def test_one_place_emits_embed_dir(tmp_path):
  """Anti-drift, which is the actual defect: two mechanisms each spelling
  the flag out is how they came to disagree. Both go through
  _buildutil_add_generated_roots now, and the rendered machinery says the
  flag exactly once."""
  machinery = (deposit.ensure(tmp_path, CFG) / "buildutil.cmake").read_text()
  # count EMISSIONS, not mentions: the capability probe names the flag
  # too, and conflating the two would have let this guard be "fixed" by
  # bumping a number the next time anything mentioned it
  assert machinery.count("--embed-dir=${root}") == 1, (
    "more than one place emits --embed-dir; they will drift again")
  assert "check_cxx_compiler_flag" in machinery, (
    "the flag is emitted without probing that the compiler accepts it — "
    "gcc before 15 rejects it outright")
  # both ROUTES go through it: the module path and Add_generated_source.
  # (Counts calls, not the definition — `function(name args)` has no
  # paren straight after the name.) It was 3 while Init_submodule had a
  # separate INTERFACE arm; that arm is gone, and the property being
  # guarded is "no route emits the flag by hand", not the arm count.
  assert machinery.count("_buildutil_add_generated_roots(") >= 2, (
    "a route to generated roots stopped going through the one function "
    "that keeps --embed-dir and -I in step")


EMBED_CONFIGURE_PY = """\
import buildutil_configure as bc
bc.emit("gen/blob.inc", "1,2,3,4,5")
"""

EMBED_ROOT_CMAKE = ROOT_CMAKE.replace(
  "include(buildutil)", "include(buildutil)\nadd_compile_options(-std=c++26)")


@pytest.mark.skipif(not _embed_works(), reason="compiler has no #embed")
def test_a_configure_py_file_can_actually_be_embedded(tmp_path):
  """The claim, end to end. Everything above checks that --embed-dir
  reaches the compile line; this compiles a real #embed of a file a
  configure.py emitted, which is the thing the flag exists for and the
  only version of it a consumer would notice. Without the fix the
  preprocessor cannot find the file and the build fails."""
  deposit.ensure(tmp_path, CFG)
  (tmp_path / "CMakeLists.txt").write_text(EMBED_ROOT_CMAKE)
  src = tmp_path / "sources"
  (src / "gen").mkdir(parents=True)
  (src / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  (src / "gen" / "CMakeLists.txt").write_text("Init_submodule()\n")
  (src / "gen" / "configure.py").write_text(EMBED_CONFIGURE_PY)
  (src / "gen" / "unit.cpp").write_text(
    "static const unsigned char blob[] = {\n"
    '#embed "gen/blob.inc"\n'
    "};\n"
    "int unit() { return sizeof(blob) == 5 ? 0 : 1; }\n")
  cfg = subprocess.run(
    ["cmake", "-S", str(tmp_path), "-B", str(tmp_path / "b"),
     f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}"], capture_output=True, text=True)
  assert cfg.returncode == 0, cfg.stdout + cfg.stderr
  built = subprocess.run(["cmake", "--build", str(tmp_path / "b")],
                         capture_output=True, text=True)
  assert built.returncode == 0, (
    "a #embed of a configure.py-emitted file did not resolve:\n"
    + built.stdout + built.stderr)


CROSS_CONFIGURE_PY = """\
import buildutil_configure as bc
bc.emit("tables.gh", "static const int table[] = {1,2,3};\\n")
"""


def _two_module_tree(root):
  """A producer with configure.py codegen, and a reader that CANNOT link
  it — the case the universal parent root exists for."""
  deposit.ensure(root, CFG)
  (root / "CMakeLists.txt").write_text(ROOT_CMAKE)
  src = root / "sources"
  (src / "producer").mkdir(parents=True)
  (src / "reader").mkdir(parents=True)
  (src / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  (src / "producer" / "CMakeLists.txt").write_text("Init_submodule()\n")
  (src / "producer" / "configure.py").write_text(CROSS_CONFIGURE_PY)
  (src / "producer" / "p.cpp").write_text("int p() { return 0; }\n")
  (src / "reader" / "CMakeLists.txt").write_text("Init_submodule()\n")
  return root


def test_a_generated_header_resolves_qualified_from_a_module_that_cannot_link(tmp_path):
  """#16's ruling: the parent generated root is universal, exactly as
  sources/ is. The reader does NOT link the producer — for a tool reading
  a sibling's generated tables, linking would be a dependency cycle — and
  must still resolve <module>/foo.gh with nothing declared."""
  root = _two_module_tree(tmp_path)
  (root / "sources" / "reader" / "r.cpp").write_text(
    '#include "producer/tables.gh"\nint r() { return table[0]; }\n')
  built = _build(root)
  assert built.returncode == 0, (
    "a qualified include of a sibling's generated header did not resolve "
    "without linking it\n" + built.stdout + built.stderr)


def test_the_producers_own_generated_dir_is_on_its_own_path(tmp_path):
  """The other half: unqualified from the module that generated it.

  A GUARD, not a regression test — the configure.py route already did
  this, and it is the Add_generated_source route that was missing its own
  dir (fixed alongside, but exercising it needs a generator tool target
  the harness has no cheap way to build). Kept so the configure.py side
  cannot quietly lose it."""
  root = _two_module_tree(tmp_path)
  (root / "sources" / "producer" / "use.cpp").write_text(
    '#include "tables.gh"\nint use() { return table[0]; }\n')
  built = _build(root)
  assert built.returncode == 0, built.stdout + built.stderr


GEN_FROM_INPUT = '''\
import argparse
ap = argparse.ArgumentParser()
ap.add_argument("--output", required=True)
ap.add_argument("rest", nargs="*")
a = ap.parse_args()
text = open(a.rest[0]).read().strip() if a.rest else "0"
open(a.output, "w").write("static const int table[] = {%s};\\n" % text)
'''


def _generated_tree(root, args_line):
  deposit.ensure(root, CFG)
  (root / "CMakeLists.txt").write_text(ROOT_CMAKE)
  src = root / "sources"
  (src / "gen").mkdir(parents=True)
  (src / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  (src / "gen" / "CMakeLists.txt").write_text(
    f'Init_submodule()\nAdd_generated_source(OUTPUT "table.gh" '
    f'SCRIPT "gen.py" {args_line})\n')
  (src / "gen" / "unit.cpp").write_text(
    '#include "gen/table.gh"\nint unit() { return table[0]; }\n')
  (root / "gen.py").write_text(GEN_FROM_INPUT)
  (root / "input.txt").write_text("1,2,3\n")
  return root


def test_an_args_path_becomes_a_dependency_without_repeating_it(tmp_path):
  """#31: 69 of 72 DEPENDS entries in the consumer tree restated a path
  ARGS already named. Worse than verbose — the two can disagree, and a
  DEPENDS missing a path ARGS still passes means the generator silently
  stops rerunning when that input changes."""
  root = _generated_tree(tmp_path, 'ARGS "input.txt"')
  cfg = subprocess.run(
    ["cmake", "-S", str(root), "-B", str(root / "b"), "-G", "Ninja",
     f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}"], capture_output=True, text=True)
  assert cfg.returncode == 0, cfg.stdout + cfg.stderr
  ninja = (root / "b" / "build.ninja").read_text()
  rule = [l for l in ninja.splitlines()
          if "table.gh" in l and l.startswith("build ")]
  assert rule, "no rule generates table.gh"
  assert any("input.txt" in l for l in rule), (
    "the input named in ARGS is not a dependency of the rule:\n"
    + "\n".join(rule))


def test_a_generator_expression_in_args_is_not_probed(tmp_path):
  """It has no value at configure time, and $<TARGET_FILE:> already
  carries its own build edge."""
  root = _generated_tree(tmp_path, 'ARGS "$<TARGET_FILE:gen>"')
  cfg = subprocess.run(
    ["cmake", "-S", str(root), "-B", str(root / "b"), "-G", "Ninja",
     f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}"], capture_output=True, text=True)
  assert cfg.returncode == 0, cfg.stdout + cfg.stderr


def test_an_args_value_that_is_not_a_file_adds_nothing(tmp_path):
  """An option value that happens to look like a path names nothing."""
  root = _generated_tree(tmp_path, 'ARGS "--flavour=fast"')
  cfg = subprocess.run(
    ["cmake", "-S", str(root), "-B", str(root / "b"), "-G", "Ninja",
     f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}"], capture_output=True, text=True)
  assert cfg.returncode == 0, cfg.stdout + cfg.stderr
  assert "flavour" not in (root / "b" / "build.ninja").read_text().replace(
    "--flavour=fast", "")


def test_link_dependencies_orders_a_reader_after_the_producers_generation(tmp_path):
  """The ruling that replaced a feature: a module reading another module's
  generated header declares it like any other dependency —
  Link_dependencies(producer) — and dependence IS build order. The include
  paths are universal; the link edge orders the reader's compiles after
  the producer's generation. This pins the contract so a cmake or
  machinery change that broke the implication fails here, not in a
  consumer's clean build."""
  root = _generated_tree(tmp_path, 'ARGS "input.txt"')
  src = root / "sources"
  (src / "reader").mkdir()
  (src / "reader" / "CMakeLists.txt").write_text(
    "Init_submodule()\nLink_dependencies(gen)\n")
  (src / "reader" / "r.cpp").write_text(
    '#include "gen/table.gh"\nint r() { return table[0]; }\n')
  cfg = subprocess.run(
    ["cmake", "-S", str(root), "-B", str(root / "b"), "-G", "Ninja",
     f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}"], capture_output=True, text=True)
  assert cfg.returncode == 0, cfg.stdout + cfg.stderr
  ninja = (root / "b" / "build.ninja").read_text()
  order = [l for l in ninja.splitlines()
           if "order_depends_target_reader" in l and l.startswith("build ")]
  assert order and any("gen" in l for l in order), (
    "the reader's compiles are not ordered behind its declared dependency:\n"
    + "\n".join(order))
  built = subprocess.run(["cmake", "--build", str(root / "b")],
                         capture_output=True, text=True)
  assert built.returncode == 0, (
    "a clean build raced the generator despite the declared dependency\n"
    + built.stdout + built.stderr)


def _entangled_tree(tmp_path):
  """A producer whose library LINKS another module — the shape that pools:
  cmake stamps the target's dependency closure onto every custom command
  attached to it, which is how the consumer's compiler<->generator pair
  produced a ninja cycle the file graph does not contain."""
  root = _generated_tree(tmp_path, 'ARGS "input.txt"')
  src = root / "sources"
  (src / "helper").mkdir()
  (src / "helper" / "CMakeLists.txt").write_text("Init_submodule()\n")
  (src / "helper" / "h.cpp").write_text("int h() { return 0; }\n")
  (src / "gen" / "CMakeLists.txt").write_text(
    'Init_submodule()\nLink_dependencies(helper)\n'
    'Add_generated_source(OUTPUT "table.gh" SCRIPT "gen.py" ARGS "input.txt")\n')
  return root


def test_each_generation_rule_owns_its_dependency_set(tmp_path):
  """Measured on cmake 4.4.2: cmake pools a target's dependency
  closure onto every custom command attached to it, so in an entangled
  compiler<->table-generator pair a tool edge belonging to ONE rule lands
  on its siblings and ninja reports a cycle the file graph does not have.
  Per-rule DEPENDS_EXPLICIT_ONLY (3.27+) stops the pooling; the tool edges
  a rule genuinely has are restated from its own $<TARGET_FILE:> refs."""
  root = _entangled_tree(tmp_path)
  cfg = subprocess.run(
    ["cmake", "-S", str(root), "-B", str(root / "b"), "-G", "Ninja",
     f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}"], capture_output=True, text=True)
  assert cfg.returncode == 0, cfg.stdout + cfg.stderr
  ninja = (root / "b" / "build.ninja").read_text()
  rule = [l for l in ninja.splitlines()
          if "table.gh" in l and "CUSTOM_COMMAND" in l]
  assert rule, "no rule generates table.gh"
  # DEPENDS_EXPLICIT_ONLY: the pooled closure (gen's link dep, helper) is
  # absent from the rule -- it carries its OWN inputs and nothing else
  assert not any("helper" in l for l in rule), (
    "the generation rule still carries pooled target-closure deps:\n"
    + "\n".join(rule))


def test_a_target_file_ref_in_args_is_still_a_dependency(tmp_path):
  """The global escape hatch (CMAKE_ADD_CUSTOM_COMMAND_DEPENDS_EXPLICIT_ONLY)
  strips $<TARGET_FILE:> tool edges outright — generators ran before being
  built. The per-rule form must NOT: the tool edge is restated explicitly,
  derived from the rule's own ARGS."""
  root = _generated_tree(tmp_path, 'ARGS "$<TARGET_FILE:gen>"')
  cfg = subprocess.run(
    ["cmake", "-S", str(root), "-B", str(root / "b"), "-G", "Ninja",
     f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}"], capture_output=True, text=True)
  assert cfg.returncode == 0, cfg.stdout + cfg.stderr
  ninja = (root / "b" / "build.ninja").read_text()
  rule = [l for l in ninja.splitlines()
          if "table.gh" in l and l.startswith("build ")]
  # the fixture's gen module is library-only, so $<TARGET_FILE:gen>
  # resolves to its archive — the edge, not the spelling, is the claim
  assert rule and any("libgen.a" in l for l in rule), (
    "the tool named in ARGS lost its build-order edge — the generator "
    "can run before it is built:\n" + "\n".join(rule))


DATA_GEN = '''\
import argparse
ap = argparse.ArgumentParser()
ap.add_argument("--output", required=True)
ap.add_argument("rest", nargs="*")
a = ap.parse_args()
open(a.output, "w").write("parsed grammar tables\\n")
'''


def _data_tree(tmp_path):
  """A module whose build-produced tool output is runtime DATA — the wd
  .prs shape: generated, staged like *.install content, never compiled."""
  root = tmp_path
  deposit.ensure(root, CFG)
  (root / "CMakeLists.txt").write_text(ROOT_CMAKE)
  src = root / "sources"
  (src / "wd").mkdir(parents=True)
  (src / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  (src / "wd" / "CMakeLists.txt").write_text(
    'Init_submodule()\n'
    'Add_generated_source(OUTPUT "wd/c.prs" SCRIPT "gen.py" STAGE DATA)\n')
  (src / "wd" / "w.cpp").write_text("int w() { return 0; }\n")
  (root / "gen.py").write_text(DATA_GEN)
  cfg = subprocess.run(
    ["cmake", "-S", str(root), "-B", str(root / "b"), "-G", "Ninja",
     f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}"], capture_output=True, text=True)
  return root, cfg


def test_generated_data_stages_as_a_build_root_overlay(tmp_path):
  """#36: STAGE DATA output is a generated *.install entry — OUTPUT is
  the prefix-rooted shipped path, staged at <build>/<path>. Building the
  module produces its data; nothing is compiled from it."""
  root, cfg = _data_tree(tmp_path)
  assert cfg.returncode == 0, cfg.stdout + cfg.stderr
  built = subprocess.run(["cmake", "--build", str(root / "b")],
                         capture_output=True, text=True)
  assert built.returncode == 0, built.stdout + built.stderr
  assert (root / "b" / "wd" / "c.prs").is_file(), (
    "the generated data file is not staged at its overlay path")


def test_generated_data_installs_at_its_shipped_path(tmp_path):
  root, cfg = _data_tree(tmp_path)
  assert cfg.returncode == 0
  assert subprocess.run(["cmake", "--build", str(root / "b")],
                        capture_output=True).returncode == 0
  prefix = root / "inst"
  done = subprocess.run(["cmake", "--install", str(root / "b"),
                         "--prefix", str(prefix)], capture_output=True, text=True)
  assert done.returncode == 0, done.stdout + done.stderr
  assert (prefix / "wd" / "c.prs").is_file(), (
    "generated data did not install at its overlay path")


def test_generated_data_is_never_compiled(tmp_path):
  """The output joins the data flow, not the target's sources."""
  root, cfg = _data_tree(tmp_path)
  assert cfg.returncode == 0
  ninja = (root / "b" / "build.ninja").read_text()
  assert "c.prs.o" not in ninja, "a DATA output was handed to the compiler"
