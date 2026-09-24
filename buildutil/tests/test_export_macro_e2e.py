"""_Public_(n) on a definition exports it at version n from its shared module, nothing else (#157, #158)."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from buildutil import deposit, exports, initcmd

PYSUPPORT = Path(deposit.__file__).resolve().parent / "pysupport"
CFG = {"cmake_option_prefix": "ACME", "module_define_prefix": "ACM"}

pytestmark = pytest.mark.skipif(
  not sys.platform.startswith("linux") or shutil.which("readelf") is None
  or shutil.which("cmake") is None or shutil.which("ninja") is None
  or not (shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")),
  reason="needs Linux, readelf, cmake, ninja and a C++ compiler")

ROOT_CMAKE = """\
cmake_minimum_required(VERSION 3.25)
project(exported C CXX)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
add_subdirectory(sources)
"""

API = """\
#pragma once
#define PLUGIN_ABI_VERSION 3
#ifdef __cplusplus
extern "C" {
#endif
int plugin_answer(void);
int plugin_old(void);
#ifdef __cplusplus
}
#endif
"""

PLUGIN = """\
#include <plug-in/api.h>
#include <memory>
#include <vector>
extern "C" int helper_value();
extern "C" int internal_value() { return 1; }
static auto parts() -> int {
  return static_cast<int>(std::vector<int>{1, 2}.size()) + *std::make_shared<int>(38);
}
auto _Public_() plugin_answer() -> int { return helper_value() + internal_value() + parts(); }
auto _Public_(PLUGIN_ABI_VERSION) plugin_old() -> int { return 41; }
"""

LEGACY = """\
#include <plug-in/api.h>
_Public_() int legacy_answer(void) { return plugin_answer(); }
"""

HELPER = 'extern "C" auto helper_value() -> int { return 1; }\n'
MARKED_HELPER = 'extern "C" auto _Public_() helper_value() -> int { return 1; }\n'

TOOL = """\
#include <plug-in/api.h>
extern "C" int legacy_answer();
int main() { return plugin_answer() == 42 && legacy_answer() == 42 ? 0 : 1; }
"""

HOOK = "import buildutil_configure as bc\nbc.soversion({})\n"


def _tree(root, helper="helper", plugin=PLUGIN, package="7.1.4"):
  return _project(root, package, {
    "plug-in": (f"Init_submodule()\nLink_dependencies({helper.split('.')[0]})\n",
                {"main.so.cpp": plugin, "api.h": API}),
    "legacy.so": ("Init_submodule()\nLink_dependencies(plug-in)\n", {"legacy.c": LEGACY}),
    helper: ("Init_submodule()\n", {"helper.cpp": MARKED_HELPER if helper.endswith(".obj") else HELPER}),
    "tool": ("Init_submodule()\nLink_dependencies(plug-in legacy)\n", {"main.cpp": TOOL}),
  })


def _project(root, package, modules):
  deposit.ensure(root, CFG)
  (root / "_bdudata" / "buildinfo.json").write_text(json.dumps({"package": package}))
  (root / "CMakeLists.txt").write_text(ROOT_CMAKE)
  sources = root / "sources"
  sources.mkdir()
  (sources / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  for directory, (cmake, files) in modules.items():
    module = sources / directory
    module.mkdir()
    (module / "CMakeLists.txt").write_text(cmake)
    for name, text in files.items():
      (module / name).write_text(text)
  return root


def _run(*command, **kwargs):
  return subprocess.run(command, capture_output=True, text=True, **kwargs)


def _build(root, *configure):
  done = _run("cmake", "-S", str(root), "-B", str(root / "b"), "-G", "Ninja",
              f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}", *configure)
  if done.returncode == 0:
    built = _run("cmake", "--build", str(root / "b"))
    built.stdout = done.stdout + built.stdout
    return built
  return done


def _library(root, module):
  return next((root / "b" / "sources").glob(f"{module}*/lib{module}.so"))


def _exported(root, module):
  return sorted(exports.Elf.load(_library(root, module)).exported())


def _dynamic(library):
  return _run("readelf", "-dW", "--dyn-syms", str(library), check=True).stdout


def test_only_the_marked_definitions_are_exported(tmp_path):
  """The archive's helper and the std::vector and make_shared instantiations stay local."""
  built = _build(_tree(tmp_path))
  assert built.returncode == 0, built.stdout + built.stderr
  assert _exported(tmp_path, "plug-in") == ["plugin_answer", "plugin_old"]


def test_each_version_is_a_node_named_for_the_output_and_the_major_names_the_soname(tmp_path):
  built = _build(_tree(tmp_path))
  assert built.returncode == 0, built.stdout + built.stderr
  table = _dynamic(_library(tmp_path, "plug-in"))
  assert "plugin_answer@@PLUG_IN_7" in table
  assert "plugin_old@@PLUG_IN_3" in table
  assert "Library soname: [libplug-in.so.7]" in table
  assert _run(str(tmp_path / "b" / "bin" / "tool")).returncode == 0


def test_a_node_above_the_major_leaves_the_soname_at_the_major(tmp_path):
  """An added function at a newer node is additive: the soname stays, as GLIBC_2.34 sits in libc.so.6."""
  built = _build(_tree(tmp_path, plugin=PLUGIN.replace("_Public_(PLUGIN_ABI_VERSION)", "_Public_(9)")))
  assert built.returncode == 0, built.stdout + built.stderr
  table = _dynamic(_library(tmp_path, "plug-in"))
  assert "plugin_old@@PLUG_IN_9" in table
  assert "Library soname: [libplug-in.so.7]" in table


def test_the_marks_are_read_from_their_sections(tmp_path):
  _build(_tree(tmp_path))
  objects = (tmp_path / "b" / "generated" / "_buildutil" / "exports" / "plug-in" / "objects.txt")
  marks = [(mark.symbol, mark.text) for line in objects.read_text().split()
           for mark in exports.Elf.load(Path(line)).marks()]
  assert sorted(marks) == [("plugin_answer", ""), ("plugin_old", "3")]


@pytest.mark.parametrize("tree", ["b", "p"])
def test_the_soname_file_is_real_and_the_development_name_links_to_it(tmp_path, tree):
  _build(_tree(tmp_path))
  _run("cmake", "--install", str(tmp_path / "b"), "--prefix", str(tmp_path / "p"), check=True)
  directory = _library(tmp_path, "plug-in").parent if tree == "b" else tmp_path / "p"
  assert not (directory / "libplug-in.so.7").is_symlink()
  assert os.readlink(directory / "libplug-in.so") == "libplug-in.so.7"


def test_a_consumer_of_the_header_exports_nothing_of_it(tmp_path):
  """A C module including the plain ABI header gains only its own mark."""
  built = _build(_tree(tmp_path))
  assert built.returncode == 0, built.stdout + built.stderr
  assert _exported(tmp_path, "legacy") == ["legacy_answer"]


def test_an_obj_dependency_is_objects_and_keeps_its_marks(tmp_path):
  """Re-exporting a dependency is a declaration: it is an .obj module, not an archive."""
  built = _build(_tree(tmp_path, "helper.obj"))
  assert built.returncode == 0, built.stdout + built.stderr
  assert _exported(tmp_path, "plug-in") == ["helper_value", "plugin_answer", "plugin_old"]


def test_the_mark_and_the_project_options_are_both_force_included(tmp_path):
  """Two `-include`s on one line: cmake de-duplicates bare options, so each travels as one group."""
  _tree(tmp_path)
  deposit.ensure(tmp_path, {**CFG, "name": "demo", "options": {"contracts": True}})
  plugin = tmp_path / "sources" / "plug-in" / "main.so.cpp"
  plugin.write_text(PLUGIN + "static_assert(ACME_CONTRACTS == 1);\n")
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr
  assert _exported(tmp_path, "plug-in") == ["plugin_answer", "plugin_old"]


def test_the_package_version_is_in_buildinfo_and_not_in_the_forced_header(tmp_path):
  """The publish number moves every publish; in a force include it would rebuild every unit."""
  _build(_tree(tmp_path, package="7.1.4.9"))
  header = next((tmp_path / "b" / "generated").glob("*/buildinfo.hpp")).read_text()
  assert '#define ACM_PACKAGE_VERSION "7.1.4.9"' in header
  assert "#define ACM_PACKAGE_VERSION_MAJOR 7" in header
  assert "#define ACM_PACKAGE_VERSION_PATCH 4" in header
  assert "PACKAGE" not in (tmp_path / "b" / "generated" / "_buildutil" / "public.h").read_text()


def test_an_untagged_package_is_0_0_0_and_says_so(tmp_path):
  built = _build(_tree(tmp_path, package=""))
  assert built.returncode == 0, built.stdout + built.stderr
  assert "the package is 0.0.0 and _Public_() exports at major 0" in built.stdout
  assert "plugin_answer@@PLUG_IN_0" in _dynamic(_library(tmp_path, "plug-in"))


def test_a_version_that_is_not_semver_says_so_and_exports_at_0(tmp_path):
  """What `publish --version dev` stamps."""
  built = _build(_tree(tmp_path, package="dev"))
  assert built.returncode == 0, built.stdout + built.stderr
  assert "version 'dev' is not semver, major 0" in built.stdout
  assert "no semver tag" not in built.stdout
  assert "Library soname: [libplug-in.so.0]" in _dynamic(_library(tmp_path, "plug-in"))


@pytest.mark.parametrize("mark, text", [("_Public_(1.5)", "1.5"), ("_Public_(x)", "x")])
def test_a_version_that_is_not_a_non_negative_integer_fails_the_link(tmp_path, mark, text):
  built = _build(_tree(tmp_path, plugin=PLUGIN.replace("_Public_(PLUGIN_ABI_VERSION)", mark)))
  assert built.returncode != 0
  assert (f"`_Public_({text})` on `plugin_old` in " in built.stdout
          and "n must be a non-negative integer or empty" in built.stdout), built.stdout


def test_a_checked_in_map_that_leaves_a_mark_out_fails_naming_both(tmp_path):
  tree = _tree(tmp_path)
  script = tree / "sources" / "plug-in" / "exports.map"
  script.write_text("PLUG { global: plugin_answer; local: *; };\n")
  built = _build(tree)
  assert built.returncode != 0
  assert f"{script} does not export what _Public_ marks: plugin_old" in built.stdout


def test_the_hook_soversion_names_the_file_whatever_the_marks(tmp_path):
  tree = _tree(tmp_path)
  (tree / "sources" / "plug-in" / "configure.py").write_text(HOOK.format(2))
  built = _build(tree)
  assert built.returncode == 0, built.stdout + built.stderr
  table = _dynamic(next((tmp_path / "b" / "sources").glob("plug-in/libplug-in.so.2")))
  assert "Library soname: [libplug-in.so.2]" in table
  assert "plugin_answer@@PLUG_IN_7" in table


def test_a_marked_variable_fails_the_link_naming_symbol_object_and_type(tmp_path):
  built = _build(_tree(tmp_path, plugin=PLUGIN + "_Public_(9) int counter = 5;\n"))
  assert built.returncode != 0
  assert "`counter` in " in built.stdout and "main.so.cpp.o is OBJECT in a data section" in built.stdout, \
    built.stdout


def test_a_mark_on_a_static_function_fails_naming_the_local_symbol(tmp_path):
  source = (PLUGIN + "static auto _Public_() hidden() -> int { return 2; }\n"
            "auto touch() -> int { return hidden(); }\n")
  built = _build(_tree(tmp_path, plugin=source))
  assert built.returncode != 0
  assert "`_ZL6hiddenv` in " in built.stdout and "is marked but local" in built.stdout, built.stdout


@pytest.mark.parametrize("linkage, helper", [("static", None), ("shared", ["helper_value"])])
def test_a_mark_exports_from_the_shared_module_that_compiles_it(tmp_path, linkage, helper):
  """A library module folded in by static linkage exports nothing; module_linkage=shared exports its own."""
  tree = _tree(tmp_path)
  (tree / "sources" / "helper" / "helper.cpp").write_text(MARKED_HELPER)
  built = _build(tree, f"-DBUILDUTIL_MODULE_LINKAGE={linkage}")
  assert built.returncode == 0, built.stdout + built.stderr
  assert _exported(tmp_path, "plug-in") == ["plugin_answer", "plugin_old"]
  shared = list((tmp_path / "b" / "sources").glob("helper*/libhelper.so"))
  assert (sorted(exports.Elf.load(shared[0]).exported() & {"helper_value"}) if shared else None) == helper


IPO = "-DCMAKE_INTERPROCEDURAL_OPTIMIZATION=ON"
COMPILERS = {"gcc": ("gcc", "g++", ()), "clang": ("clang", "clang++", ("-DCMAKE_LINKER_TYPE=LLD",))}


def _compilers(family):
  """clang's LTO links through lld (ld.bfd would need LLVMgold, which few boxes carry)."""
  c, cxx, linker = COMPILERS[family]
  if shutil.which(c) is None or shutil.which(cxx) is None:
    pytest.skip(f"needs {family}")
  if linker and not Path(_run(c, "-print-prog-name=ld.lld").stdout.strip()).is_file():
    pytest.skip("needs lld")
  return (f"-DCMAKE_C_COMPILER={c}", f"-DCMAKE_CXX_COMPILER={cxx}", *linker)


@pytest.mark.parametrize("family", sorted(COMPILERS))
def test_an_lto_module_with_no_marks_links_a_static_helper(tmp_path, family):
  """cmake's IPO asks gcc for slim objects; buildutil's -ffat-lto-objects after it keeps the code."""
  _project(tmp_path, "7.1.4", {
    "plain": ("Init_submodule()\nLink_dependencies(helper)\n",
              {"main.so.cpp": 'extern "C" int h(); extern "C" int f() { return h(); }\n'}),
    "helper": ("Init_submodule()\n", {"helper.cpp": 'extern "C" int h() { return 1; }\n'}),
  })
  built = _build(tmp_path, IPO, *_compilers(family))
  assert built.returncode == 0, built.stdout + built.stderr


@pytest.mark.parametrize("family", sorted(COMPILERS))
def test_an_lto_module_exports_its_marks_at_their_nodes(tmp_path, family):
  built = _build(_tree(tmp_path), IPO, *_compilers(family))
  assert built.returncode == 0, built.stdout + built.stderr
  table = _dynamic(_library(tmp_path, "plug-in"))
  assert "plugin_answer@@PLUG_IN_7" in table
  assert "plugin_old@@PLUG_IN_3" in table
  assert _run(str(tmp_path / "b" / "bin" / "tool")).returncode == 0


def test_a_cmake_that_cannot_import_the_scan_fails_at_configure_naming_pysupport(tmp_path):
  bare = shutil.which("python3", path="/usr/bin")
  if bare is None or _run(bare, "-c", "import buildutil", cwd=tmp_path).returncode == 0:
    pytest.skip("needs a python3 that does not carry buildutil")
  done = _run("cmake", "-S", str(_tree(tmp_path)), "-B", str(tmp_path / "b"), "-G", "Ninja",
              f"-DBUILDUTIL_PY={bare}", cwd=tmp_path)
  assert done.returncode != 0
  assert "cannot run buildutil.exports" in done.stderr, done.stderr
  assert "-DBUILDUTIL_PYSUPPORT=" in done.stderr


@pytest.mark.parametrize("compiler, flags", [
  ("gcc", ["-std=c89", "-pedantic-errors"]), ("clang", ["-std=c89", "-pedantic-errors"]),
  ("clang", ["-Weverything", "-Werror"])])
def test_the_header_is_quiet_under_strict_c(tmp_path, compiler, flags):
  if shutil.which(compiler) is None:
    pytest.skip(f"needs {compiler}")
  done = _run("cmake", "-S", str(_tree(tmp_path)), "-B", str(tmp_path / "b"), "-G", "Ninja",
              f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}")
  assert done.returncode == 0, done.stderr
  source = tmp_path / "strict.c"
  source.write_text("int strict_answer(void);\nint _Public_() strict_answer(void) { return 42; }\n")
  header = tmp_path / "b" / "generated" / "_buildutil" / "public.h"
  compiled = _run(compiler, *flags, "-include", str(header), "-c", str(source),
                  "-o", str(tmp_path / "strict.o"))
  assert compiled.returncode == 0, compiled.stderr


def test_the_cache_build_exports_at_the_major_conan_builds(tmp_path):
  """The published recipe's build() passes the package version; the mark follows it."""
  tree = _tree(tmp_path / "src")
  (tree / "buildutil.toml").write_text(
    '[project]\nname = "exported"\ncmake_option_prefix = "ACME"\nmodule_define_prefix = "ACM"\n')
  toolchain = tmp_path / "toolchain.cmake"
  toolchain.write_text("")
  env = dict(os.environ, PYTHONPATH=str(Path(initcmd.__file__).resolve().parents[1]))
  done = _run(sys.executable, "-m", "buildutil", "cache-build", "--build-dir", str(tree / "b"),
              "--toolchain", str(toolchain), "--build-type", "Debug",
              "--package-version", "5.0.2.1", cwd=tree, env=env)
  assert done.returncode == 0, done.stdout + done.stderr
  assert "plugin_answer@@PLUG_IN_5" in _dynamic(_library(tree, "plug-in"))
