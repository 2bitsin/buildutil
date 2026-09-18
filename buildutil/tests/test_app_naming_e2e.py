"""Where a module's application is built, and what it is called.

Two rules, both compiled rather than asserted, because both failed in ways
that only cmake can show:

  * an app is BUILT and INSTALLED under the module's own directory name --
    the LEAF of its path under sources/, not the joined target name, so a
    grouped mstools/rc ships `rc` while its cmake target stays mstools-rc;
  * an app lands in <build>/bin, never in the module's own binary dir.
    A module whose only source is main.cpp has no library to compile, so
    it gets an INTERFACE target -- and cmake's ninja generator gives that
    target a phony at <binary_dir>/<target>, the exact path an app named
    after its module would link to. ninja refused the whole tree with
    "multiple rules generate sources/wfl/wfl". A text assertion on
    buildutil.cmake cannot see that; only generate can.

Skipped where there is no cmake or no C++ compiler, like the rest of the
e2e tests.
"""
import shutil
import subprocess

import pytest

from buildutil import deposit

CFG = {"cmake_option_prefix": "ACME", "module_define_prefix": "ACM"}

pytestmark = pytest.mark.skipif(
  shutil.which("cmake") is None or shutil.which("ninja") is None or
  not (shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")),
  reason="needs cmake, ninja and a C++ compiler")

ROOT_CMAKE = """\
cmake_minimum_required(VERSION 3.25)
project(appnames CXX)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
add_subdirectory(sources)
"""

MAIN = "int main() { return 0; }\n"


def _tree(root, modules: dict):
  """A project of {sources-relative path: {file: text}} modules. A path with
  a '/' puts the module inside a GROUP dir, which carries no CMakeLists of
  its own -- Scan_subdirectories() recurses into it."""
  deposit.ensure(root, CFG)
  (root / "CMakeLists.txt").write_text(ROOT_CMAKE)
  src = root / "sources"
  src.mkdir(exist_ok=True)
  (src / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  for rel, files in modules.items():
    d = src / rel
    d.mkdir(parents=True)
    (d / "CMakeLists.txt").write_text("Init_submodule()\n")
    for name, text in files.items():
      (d / name).write_text(text)
  return root


def _configure(root) -> subprocess.CompletedProcess:
  """configure AND generate -- the step that used to fail. Ninja is not
  incidental here: it is the generator buildutil builds with everywhere
  (engine.py passes -G Ninja), and it is the only one that diagnoses the
  collision. cmake's default Unix Makefiles generator tolerates the
  duplicate output path silently, so a test left on the default would
  pass just as happily against the broken machinery."""
  return subprocess.run(
    ["cmake", "-S", str(root), "-B", str(root / "b"), "-G", "Ninja"],
    capture_output=True, text=True)


def _build(root) -> subprocess.CompletedProcess:
  cfg = _configure(root)
  assert cfg.returncode == 0, cfg.stdout + cfg.stderr
  return subprocess.run(["cmake", "--build", str(root / "b")],
                        capture_output=True, text=True)


def test_a_module_whose_only_source_is_main_generates(tmp_path):
  """The regression. Nothing to compile into a library, so the module
  target is INTERFACE and its phony collided with the app's output path."""
  _tree(tmp_path, {"wfl": {"main.cpp": MAIN}})
  cfg = _configure(tmp_path)
  assert cfg.returncode == 0, (
    "generate failed for a single-main.cpp module — the INTERFACE target's "
    "phony is colliding with the app again:\n" + cfg.stdout + cfg.stderr)
  assert "multiple rules generate" not in cfg.stdout + cfg.stderr
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr
  assert (tmp_path / "b" / "bin" / "wfl").is_file()


def test_a_grouped_app_is_named_for_its_leaf_directory(tmp_path):
  """Target name stays joined for uniqueness, the binary is the leaf."""
  _tree(tmp_path, {"mstools/rc": {"main.cpp": MAIN}})
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr
  assert (tmp_path / "b" / "bin" / "rc").is_file(), (
    "a grouped module's app should install as its leaf name")
  assert not (tmp_path / "b" / "bin" / "mstools-rc").exists(), (
    "the joined target name leaked into the binary name")
  # the cmake target keeps the joined form -- that is what makes it unique
  targets = subprocess.run(["cmake", "--build", str(tmp_path / "b"),
                            "--target", "help"], capture_output=True, text=True)
  # the EXECUTABLE carries the bare joined name now; the -app suffix is
  # gone from the real target (an ALIAS keeps old spellings working for
  # one release, and an alias has no build rule to list). This module is
  # single-main.cpp, so its library is INTERFACE and ninja lists no rule
  # for it -- the decoration is asserted on the static case below.
  assert "mstools-rc" in targets.stdout


def test_a_grouped_leaf_and_a_toplevel_module_coexist(tmp_path):
  """Grouping is how two modules get distinct target names while each keeps
  the binary name it is meant to ship -- mstools/rc ships rc, wrc ships wrc."""
  _tree(tmp_path, {"mstools/rc": {"main.cpp": MAIN},
                   "wrc": {"main.cpp": MAIN}})
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr
  assert (tmp_path / "b" / "bin" / "rc").is_file()
  assert (tmp_path / "b" / "bin" / "wrc").is_file()


def test_two_modules_shipping_the_same_app_name_are_refused(tmp_path):
  """install() flattens every app into one bin/, so a duplicate name would
  silently lose a binary. Fail at configure, naming both modules."""
  _tree(tmp_path, {"mstools/rc": {"main.cpp": MAIN},
                   "other/rc": {"main.cpp": MAIN}})
  cfg = _configure(tmp_path)
  assert cfg.returncode != 0, "duplicate app names configured cleanly"
  message = cfg.stdout + cfg.stderr
  assert "mstools-rc" in message and "other-rc" in message, message
  assert "'rc'" in message, message


def test_a_library_module_with_an_app_still_builds_its_library(tmp_path):
  """The ordinary case: main.cpp is kept out of the library, the rest
  compiles into it, and the app links it and lands in bin/ all the same."""
  _tree(tmp_path, {"hello": {
    "main.cpp": "int greet();\nint main() { return greet(); }\n",
    "greet.cpp": "int greet() { return 0; }\n"}})
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr
  assert (tmp_path / "b" / "bin" / "hello").is_file()
  # the library carries the decoration now, the executable the bare name
  assert list((tmp_path / "b").rglob("libhello-lib.a")), "library not built"


def test_the_executable_takes_the_bare_module_name(tmp_path):
  """The name a person types and the name every consumer
  spells is the module's own; the library is what gets decorated."""
  _tree(tmp_path, {"tool": {
    "main.cpp": "int lib();\nint main() { return lib(); }\n",
    "lib.cpp": "int lib() { return 0; }\n"}})
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr
  assert (tmp_path / "b" / "bin" / "tool").is_file()
  assert list((tmp_path / "b").rglob("libtool-lib.a")), "library not decorated"


def test_the_app_alias_keeps_old_spellings_working(tmp_path):
  """Migration shim, one release: consumer trees spell
  $<TARGET_FILE:<module>-app> in dozens of places and every box upgrades
  itself within minutes, so the break cannot be abrupt."""
  _tree(tmp_path, {"tool": {"main.cpp": MAIN}})
  (tmp_path / "CMakeLists.txt").write_text(
    ROOT_CMAKE + 'add_custom_target(shim COMMAND ${CMAKE_COMMAND} -E echo '
                 '"$<TARGET_FILE:tool-app>")\n')
  cfg = _configure(tmp_path)
  assert cfg.returncode == 0, (
    "the -app alias no longer resolves — consumers break on upgrade\n"
    + cfg.stdout + cfg.stderr)


def test_a_library_only_module_is_untouched(tmp_path):
  """No entry point, no second target, so nothing to disambiguate: the
  library keeps the bare name exactly as before."""
  _tree(tmp_path, {"plain": {"unit.cpp": "int unit() { return 0; }\n"}})
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr
  assert list((tmp_path / "b").rglob("libplain.a")), "a lone library was renamed"


def test_a_dependency_resolves_to_the_library_even_when_it_configures_later(tmp_path):
  """The ordering trap. The scan is alphabetical, so `alpha` links `zeta`
  before zeta's CMakeLists has run. A registry filled in as modules
  configure would answer 'no zeta-lib' and link the bare name — which is
  now zeta's EXECUTABLE. The answer comes from a pre-pass over the tree
  instead, so configure order cannot change it."""
  _tree(tmp_path, {
    "alpha": {"a.cpp": "int zeta();\nint a() { return zeta(); }\n",
              "main.cpp": "int a();\nint main() { return a(); }\n"},
    "zeta": {"z.cpp": "int zeta() { return 0; }\n",
             "main.cpp": "int zeta();\nint main() { return zeta(); }\n"}})
  (tmp_path / "sources" / "alpha" / "CMakeLists.txt").write_text(
    "Init_submodule()\nLink_dependencies(zeta)\n")
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr
  assert (tmp_path / "b" / "bin" / "alpha").is_file()
  assert (tmp_path / "b" / "bin" / "zeta").is_file()


GEN_SCRIPT = '''\
import argparse
ap = argparse.ArgumentParser()
ap.add_argument("--output", required=True)
a = ap.parse_args()
open(a.output, "w").write("static const int table[] = {1,2,3};\\n")
'''


def test_a_generated_source_attaches_to_the_library_not_the_executable(tmp_path):
  """A regression from the -app rename. The bare module name is the
  EXECUTABLE now, so attaching there left the library's own TUs with no
  build-order edge to the rule that writes the file — a clean build raced
  and an incremental one hid it — and moved the generated TU out of the
  library, where nothing linking the module could see it."""
  _tree(tmp_path, {"thing": {
    "main.cpp": "int reader();\nint main() { return reader(); }\n",
    "reader.cpp": '#include "thing/table.gh"\nint reader() { return table[0]; }\n'}})
  (tmp_path / "gen.py").write_text(GEN_SCRIPT)
  (tmp_path / "sources" / "thing" / "CMakeLists.txt").write_text(
    'Init_submodule()\nAdd_generated_source(OUTPUT "table.gh" SCRIPT "gen.py")\n')
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr
  # the generated object belongs to the LIBRARY's object dir
  lib_objs = list((tmp_path / "b").rglob("thing-lib.dir/**/*.o"))
  assert lib_objs, "the library compiled nothing"
  assert (tmp_path / "b" / "bin" / "thing").is_file()


def test_the_generated_header_reaches_a_library_TU_on_a_clean_build(tmp_path):
  """The failure the report describes is order-dependent, so build from an
  EMPTY tree: without the edge, the library TU can be compiled before the
  generator has run and the include simply is not there yet."""
  _tree(tmp_path, {"thing": {
    "main.cpp": "int reader();\nint main() { return reader(); }\n",
    "reader.cpp": '#include "thing/table.gh"\nint reader() { return table[0]; }\n'}})
  (tmp_path / "gen.py").write_text(GEN_SCRIPT)
  (tmp_path / "sources" / "thing" / "CMakeLists.txt").write_text(
    'Init_submodule()\nAdd_generated_source(OUTPUT "table.gh" SCRIPT "gen.py")\n')
  built = _build(tmp_path)
  assert built.returncode == 0, (
    "a library TU raced the generator\n" + built.stdout + built.stderr)


def test_publish_symbols_exports_them(tmp_path):
  """An executable that dlopen()s plugins calling back into its own
  symbols has to export them. Without this the plugin loads and fails to
  resolve — at dlopen on some platforms, at first call on others, never
  at build time."""
  _tree(tmp_path, {"host": {"main.cpp": MAIN}})
  (tmp_path / "sources" / "host" / "CMakeLists.txt").write_text(
    "Init_submodule(PUBLISH_SYMBOLS)\n")
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr
  ninja = (tmp_path / "b" / "build.ninja").read_text()
  link = [l for l in ninja.splitlines()
          if "LINK_FLAGS" in l and "rdynamic" in l]
  assert link, ("the plugin host does not export its symbols; no link "
                "flags carry -rdynamic")


def test_an_ordinary_app_does_not_export(tmp_path):
  """The declaration has to mean something — exporting everything by
  default would be a silent size and symbol-visibility change."""
  _tree(tmp_path, {"plain": {"main.cpp": MAIN}})
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr
  ninja = (tmp_path / "b" / "build.ninja").read_text()
  assert "rdynamic" not in ninja, (
    "an ordinary app is exporting its symbols")


def test_a_modules_install_tree_is_staged_as_a_build_root_overlay(tmp_path):
  """The overlay semantics: the path inside *.install/ IS the shipped
  path, staged over the build root so both trees agree about data."""
  _tree(tmp_path, {"tool": {"main.cpp": MAIN}})
  data = tmp_path / "sources" / "tool" / "runtime.install"
  (data / "helper").mkdir(parents=True)
  (data / "unicode.437").write_text("flat file\n")
  (data / "helper" / "en.nls").write_text("nested\n")
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr
  assert (tmp_path / "b" / "unicode.437").is_file()
  assert (tmp_path / "b" / "helper" / "en.nls").is_file(), (
    "the directory structure under .install/ was flattened")


def test_the_install_tree_installs_as_a_prefix_overlay(tmp_path):
  """data.install/x/y lands at <prefix>/x/y, and the app at its MIRRORED
  source path -- sources/tool being top-level, the file <prefix>/tool."""
  _tree(tmp_path, {"tool": {"main.cpp": MAIN}})
  data = tmp_path / "sources" / "tool" / "runtime.install"
  (data / "helper").mkdir(parents=True)
  (data / "unicode.437").write_text("flat\n")
  (data / "helper" / "en.nls").write_text("nested\n")
  assert _build(tmp_path).returncode == 0
  prefix = tmp_path / "inst"
  done = subprocess.run(["cmake", "--install", str(tmp_path / "b"),
                         "--prefix", str(prefix)],
                        capture_output=True, text=True)
  assert done.returncode == 0, done.stdout + done.stderr
  assert (prefix / "tool").is_file(), "the app is not at its mirrored path"
  assert not (prefix / "bin").exists(), "something still installs into bin/"
  assert (prefix / "unicode.437").is_file()
  assert (prefix / "helper" / "en.nls").is_file()


def test_sources_under_install_are_data_not_code(tmp_path):
  """The source glob is recursive, so without an exclusion a .cpp in the
  data tree would be compiled into the module — silently, and only for
  whoever put one there."""
  _tree(tmp_path, {"tool": {"main.cpp": MAIN}})
  data = tmp_path / "sources" / "tool" / "runtime.install"
  data.mkdir(parents=True)
  # would not compile if it were treated as a source
  (data / "sample.cpp").write_text("this is not valid C++ at all\n")
  built = _build(tmp_path)
  assert built.returncode == 0, (
    "a .cpp under .install/ was compiled as source\n"
    + built.stdout + built.stderr)
  assert (tmp_path / "b" / "sample.cpp").is_file()


def test_a_main_only_module_still_gets_a_real_library(tmp_path):
  """A module whose only TU is main.cpp used to become an INTERFACE
  library, which answers differently to every question a helper asks —
  refuses PRIVATE, compiles nothing. Every project touching 'the thing
  this module compiles into' had to test the type first."""
  _tree(tmp_path, {"solo": {"main.cpp": MAIN}})
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr
  assert list((tmp_path / "b").rglob("libsolo-lib.a")), (
    "a main.cpp-only module produced no real library")
  assert (tmp_path / "b" / "bin" / "solo").is_file()


def test_a_header_only_module_gets_one_too(tmp_path):
  """The other shape with nothing to compile. Same answer now."""
  _tree(tmp_path, {"headers": {}})
  (tmp_path / "sources" / "headers" / "api.h").write_text(
    "#pragma once\ninline int api() { return 0; }\n")
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr
  assert list((tmp_path / "b").rglob("libheaders.a"))


def test_a_consumer_links_a_main_only_modules_library(tmp_path):
  """The point of the uniformity: a consumer names the module and gets a
  library, without asking what kind it is."""
  _tree(tmp_path, {
    "solo": {"main.cpp": MAIN},
    "user": {"u.cpp": "int u() { return 0; }\n"}})
  (tmp_path / "sources" / "user" / "CMakeLists.txt").write_text(
    "Init_submodule()\nLink_dependencies(solo)\n")
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr


def test_an_ordinary_app_does_not_carry_unreferenced_objects(tmp_path):
  """The declaration has to mean something: pulling every object into
  every app would grow binaries for projects that never load a plugin."""
  _tree(tmp_path, {"plain": {
    "main.cpp": "int used();\nint main() { return used(); }\n",
    "used.cpp": "int used() { return 0; }\n",
    "spare.cpp": 'extern "C" int never_pulled() { return 7; }\n'}})
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr
  symbols = subprocess.run(["nm", "-C", str(tmp_path / "b" / "bin" / "plain")],
                           capture_output=True, text=True)
  if symbols.returncode != 0:
    pytest.skip("no nm to inspect the binary")
  assert "never_pulled" not in symbols.stdout


def test_an_ordinary_app_does_not_carry_unreferenced_objects(tmp_path):
  """The declaration has to stay scoped. Pulling every object into every
  app would grow binaries and lengthen links across whole projects that
  never load a plugin — which is the cost of doing this tree-wide, and
  the reason it is opt-in per module."""
  _tree(tmp_path, {"plain": {
    "main.cpp": "int used();\nint main() { return used(); }\n",
    "used.cpp": "int used() { return 0; }\n",
    "spare.cpp": 'extern "C" int never_pulled() { return 7; }\n'}})
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr
  symbols = subprocess.run(["nm", "-C", str(tmp_path / "b" / "bin" / "plain")],
                           capture_output=True, text=True)
  if symbols.returncode != 0:
    pytest.skip("no nm to inspect the binary")
  assert "never_pulled" not in symbols.stdout, (
    "an ordinary app is carrying objects nothing references")


def test_a_kind_tag_is_not_part_of_the_name(tmp_path):
  """`ls` says what a module builds; the tag never reaches the artifact.
  sources/wd.exe/ is the module `wd` and ships a binary called `wd` —
  otherwise Linux would install `wd.exe`."""
  _tree(tmp_path, {"wd.exe": {"main.cpp": MAIN}})
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr
  assert (tmp_path / "b" / "bin" / "wd").is_file()
  assert not (tmp_path / "b" / "bin" / "wd.exe").exists()


def test_an_obj_module_keeps_objects_nothing_references(tmp_path):
  """.obj: every object reaches whoever links it, including the ones
  nothing references — for code resolved against at runtime."""
  _tree(tmp_path, {
    "host.exe": {"main.cpp": "int used();\nint main() { return used(); }\n"},
    "plug.obj": {"used.cpp": "int used() { return 0; }\n",
                 "spare.cpp": 'extern "C" int only_at_runtime() { return 7; }\n'}})
  (tmp_path / "sources" / "host.exe" / "CMakeLists.txt").write_text(
    "Init_submodule()\nLink_dependencies(plug)\n")
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr
  syms = subprocess.run(["nm", "-C", str(tmp_path / "b" / "bin" / "host")],
                        capture_output=True, text=True)
  if syms.returncode != 0:
    pytest.skip("no nm")
  assert "only_at_runtime" in syms.stdout, (
    "an object nothing references was dropped from an .obj module")


def test_a_static_module_still_drops_what_nothing_references(tmp_path):
  """The default, unchanged — .obj has to mean something."""
  _tree(tmp_path, {
    "host.exe": {"main.cpp": "int used();\nint main() { return used(); }\n"},
    "plain": {"used.cpp": "int used() { return 0; }\n",
              "spare.cpp": 'extern "C" int never_pulled() { return 7; }\n'}})
  (tmp_path / "sources" / "host.exe" / "CMakeLists.txt").write_text(
    "Init_submodule()\nLink_dependencies(plain)\n")
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr
  syms = subprocess.run(["nm", "-C", str(tmp_path / "b" / "bin" / "host")],
                        capture_output=True, text=True)
  if syms.returncode != 0:
    pytest.skip("no nm")
  assert "never_pulled" not in syms.stdout


def test_a_shared_module_builds_a_shared_library(tmp_path):
  _tree(tmp_path, {"dip.so": {"api.cpp": 'extern "C" int api() { return 0; }\n'}})
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr
  assert list((tmp_path / "b").rglob("libdip.so")), "no shared library built"


def test_main_dll_cpp_selects_shared_by_presence(tmp_path):
  """Parallel to main.cpp selecting an executable: presence is the
  signal, the file is an ordinary TU."""
  _tree(tmp_path, {"plug": {
    "main.dll.cpp": 'extern "C" int entry() { return 0; }\n'}})
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr
  assert list((tmp_path / "b").rglob("libplug.so"))


def test_a_tag_that_contradicts_the_files_is_refused(tmp_path):
  """Silent precedence is how a directory listing starts lying."""
  _tree(tmp_path, {"thing.lib": {"main.cpp": MAIN}})
  cfg = _configure(tmp_path)
  assert cfg.returncode != 0
  assert "main.cpp" in cfg.stdout + cfg.stderr


def test_two_kind_tags_are_refused(tmp_path):
  _tree(tmp_path, {"thing.lib.so": {"a.cpp": "int a() { return 0; }\n"}})
  cfg = _configure(tmp_path)
  assert cfg.returncode != 0
  assert "more than one kind tag" in cfg.stdout + cfg.stderr

def _export_tree(tmp_path, consumer_cmake="Init_submodule()\n"):
  """A package-shaped project: sources/pkg/ is a GROUP (no CMakeLists of
  its own), so `pkg` is the package level every exported header is
  qualified by. util ships headers; user consumes them."""
  _tree(tmp_path, {
    "pkg/util": {"u.cpp": "int u() { return 0; }\n"},
    "pkg/user": {"user.cpp":
                 '#include <pkg/util/util_api.h>\nint use() { return api(); }\n'}})
  util = tmp_path / "sources" / "pkg" / "util"
  (util / "nested").mkdir(parents=True)
  (util / "_internal").mkdir(parents=True)
  (util / "unit.test").mkdir(parents=True)
  (util / "util_api.h").write_text("#pragma once\ninline int api() { return 0; }\n")
  (util / "nested" / "deep.h").write_text("#pragma once\n")
  (util / "_secret.hpp").write_text("#pragma once\n")
  (util / "_internal" / "hidden.hpp").write_text("#pragma once\n")
  (util / "unit.test" / "fixture.hpp").write_text("#pragma once\n")
  (tmp_path / "sources" / "pkg" / "user" / "CMakeLists.txt").write_text(consumer_cmake)
  return tmp_path


def _install(root):
  assert _build(root).returncode == 0
  prefix = root / "inst"
  done = subprocess.run(["cmake", "--install", str(root / "b"),
                         "--prefix", str(prefix)], capture_output=True, text=True)
  assert done.returncode == 0, done.stdout + done.stderr
  return prefix


def test_exported_headers_resolve_by_their_qualified_path(tmp_path):
  """The in-tree half. sources/ is a universal include root, so a header
  at sources/pkg/util/util_api.h is <pkg/util/util_api.h> from anywhere --
  the SAME string a consumer of the installed package writes."""
  built = _build(_export_tree(tmp_path))
  assert built.returncode == 0, built.stdout + built.stderr


def test_headers_install_at_their_sources_relative_path(tmp_path):
  """The other half, and the whole point: the shipped path equals the
  in-tree spelling, structure preserved, no tag directory in between."""
  prefix = _install(_export_tree(tmp_path))
  assert (prefix / "include" / "pkg" / "util" / "util_api.h").is_file()
  assert (prefix / "include" / "pkg" / "util" / "nested" / "deep.h").is_file()
  # the pre-0.48 flat destination must be gone, or the two spellings
  # diverge again for anyone who kept including by bare name
  assert not (prefix / "include" / "util_api.h").exists()


def test_underscore_marks_a_header_private(tmp_path):
  """Opt-OUT is the whole declaration: a leading underscore on the file
  or on any directory component keeps it out of the package."""
  prefix = _install(_export_tree(tmp_path))
  assert not (prefix / "include" / "pkg" / "util" / "_secret.hpp").exists()
  assert not (prefix / "include" / "pkg" / "util" / "_internal").exists()


def test_test_subtree_headers_do_not_ship(tmp_path):
  """*.test/ is test scaffolding, not API -- oxbox keeps .cli.hpp fixtures
  in unit.test/ and none of them belong in a consumer's include path."""
  prefix = _install(_export_tree(tmp_path))
  assert not (prefix / "include" / "pkg" / "util" / "unit.test").exists()


def test_a_grouped_module_is_a_dependency_by_its_leaf_name(tmp_path):
  """sources/pkg/util carries the path-joined target name pkg-util, but a
  dependency inside the same package spells it the way the source tree
  reads: Link_dependencies(util)."""
  _tree(tmp_path, {
    "pkg/util": {"u.cpp": "int u() { return 7; }\n",
                 "api.hpp": "#pragma once\nint u();\n"},
    "pkg/user": {"user.cpp":
                 '#include <pkg/util/api.hpp>\nint use() { return u(); }\n'}})
  (tmp_path / "sources" / "pkg" / "user" / "CMakeLists.txt").write_text(
    "Init_submodule()\nLink_dependencies(util)\n")
  built = _build(tmp_path)
  assert built.returncode == 0, (
    "a grouped module was not reachable by its leaf name:\n"
    + built.stdout + built.stderr)


def test_the_full_module_name_still_works(tmp_path):
  """The leaf is an ALIAS, not a replacement — the path-joined name is
  still the canonical spelling and must keep resolving."""
  _tree(tmp_path, {
    "pkg/util": {"u.cpp": "int u() { return 7; }\n",
                 "api.hpp": "#pragma once\nint u();\n"},
    "pkg/user": {"user.cpp":
                 '#include <pkg/util/api.hpp>\nint use() { return u(); }\n'}})
  (tmp_path / "sources" / "pkg" / "user" / "CMakeLists.txt").write_text(
    "Init_submodule()\nLink_dependencies(pkg-util)\n")
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr


def test_an_ambiguous_leaf_is_refused_by_name(tmp_path):
  """Two modules ending in the same leaf make the short spelling a coin
  flip. Fail at configure naming both, rather than linking whichever the
  scan reached first."""
  _tree(tmp_path, {
    "alpha/util": {"a.cpp": "int a() { return 1; }\n"},
    "beta/util":  {"b.cpp": "int b() { return 2; }\n"},
    "user":       {"user.cpp": "int use() { return 0; }\n"}})
  (tmp_path / "sources" / "user" / "CMakeLists.txt").write_text(
    "Init_submodule()\nLink_dependencies(util)\n")
  cfg = _configure(tmp_path)
  assert cfg.returncode != 0, "an ambiguous leaf linked silently"
  out = cfg.stdout + cfg.stderr
  assert "ambiguous" in out and "alpha-util" in out and "beta-util" in out, out


def test_the_component_manifest_records_the_module_graph(tmp_path):
  """A multi-module library package exposes one conan component per
  module, so a consumer can link oxbox::utilities instead of the whole
  package. The recipe cannot see Link_dependencies (that is cmake), so
  the build writes the graph down for package_info to read."""
  import json
  _tree(tmp_path, {
    "pkg/util": {"u.cpp": "int u() { return 7; }\n",
                 "api.hpp": "#pragma once\nint u();\n"},
    "pkg/user": {"user.cpp":
                 '#include <pkg/util/api.hpp>\nint use() { return u(); }\n'}})
  (tmp_path / "sources" / "pkg" / "user" / "CMakeLists.txt").write_text(
    "Init_submodule()\nLink_dependencies(util)\n")
  prefix = _install(tmp_path)
  man = prefix / "share" / "buildutil" / "buildutil-components.json"
  assert man.is_file(), "no component manifest was installed"
  comps = {c["path"]: c for c in json.loads(man.read_text())["components"]}
  assert set(comps) == {"pkg/util", "pkg/user"}, comps
  # paths stay sources/-relative and RAW — naming policy is the recipe's
  assert comps["pkg/user"]["needs"] == ["pkg/util"], comps["pkg/user"]
  assert comps["pkg/util"]["needs"] == [], comps["pkg/util"]
  assert comps["pkg/util"]["lib"] == "util", comps["pkg/util"]


def test_a_nested_module_installs_at_its_mirrored_path(tmp_path):
  """The ruling itself: sources/a/b/c ships <prefix>/a/b/c — the source
  tree is the install tree, the leaf being the file. Kind tags stripped
  from every component, since a tag is never part of a name."""
  _tree(tmp_path, {"plusplus/wpp386.exe": {"main.cpp": MAIN}})
  assert _build(tmp_path).returncode == 0
  prefix = tmp_path / "inst"
  done = subprocess.run(["cmake", "--install", str(tmp_path / "b"),
                         "--prefix", str(prefix)], capture_output=True, text=True)
  assert done.returncode == 0, done.stdout + done.stderr
  assert (prefix / "plusplus" / "wpp386").is_file(), (
    "the binary is not at its mirrored source path")
  assert not (prefix / "bin").exists()


def test_a_shared_module_installs_at_its_mirrored_path(tmp_path):
  _tree(tmp_path, {"wd/dipdwarf.so": {"api.cpp": 'extern "C" int api() { return 0; }\n'}})
  assert _build(tmp_path).returncode == 0
  prefix = tmp_path / "inst"
  assert subprocess.run(["cmake", "--install", str(tmp_path / "b"),
                         "--prefix", str(prefix)],
                        capture_output=True, text=True).returncode == 0
  assert (prefix / "wd" / "libdipdwarf.so").is_file(), (
    "the shared library is not in its mirrored parent")
  assert not (prefix / "lib").exists(), "something still installs into lib/"


def test_an_exe_module_may_take_its_entry_from_the_closure(tmp_path):
  """wd's shape — the entry point lives in a linked library
  (libgui's guixmain), an archive member nothing references, which a
  normal link drops. An .exe module with no main.cpp links every object
  of its whole closure, so the entry arrives with them."""
  _tree(tmp_path, {
    "gui": {"guimain.cpp": "int run();\nint main() { return run(); }\n",
            "gu.cpp": "int gu() { return 0; }\n"},
    "wd.exe": {"run.cpp": "int run() { return 0; }\n"}})
  (tmp_path / "sources" / "wd.exe" / "CMakeLists.txt").write_text(
    "Init_submodule()\nLink_dependencies(gui)\n")
  # gui must not itself become an app: main.cpp presence would make it
  # one, so bury the entry in a non-main TU name — which is the real
  # shape: guixmain.cpp, not main.cpp
  (tmp_path / "sources" / "gui" / "guimain.cpp").rename(
    tmp_path / "sources" / "gui" / "guixmain.cpp")
  built = _build(tmp_path)
  assert built.returncode == 0, (
    "the closure's entry point did not reach the app's link\n"
    + built.stdout + built.stderr)
  assert (tmp_path / "b" / "bin" / "wd").is_file()


def test_an_ordinary_app_still_takes_only_object_libraries(tmp_path):
  """The whole-closure treatment is scoped to entry-from-closure apps —
  an ordinary app pulling every static member would grow every binary."""
  _tree(tmp_path, {
    "tool": {"main.cpp": "int used();\nint main() { return used(); }\n"},
    "dep": {"used.cpp": "int used() { return 0; }\n",
            "spare.cpp": 'extern "C" int never_pulled() { return 7; }\n'}})
  (tmp_path / "sources" / "tool" / "CMakeLists.txt").write_text(
    "Init_submodule()\nLink_dependencies(dep)\n")
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr
  syms = subprocess.run(["nm", "-C", str(tmp_path / "b" / "bin" / "tool")],
                        capture_output=True, text=True)
  if syms.returncode != 0:
    pytest.skip("no nm")
  assert "never_pulled" not in syms.stdout

