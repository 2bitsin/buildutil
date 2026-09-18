"""What an EMBEDDED interpreter needs and a bridge did not give.

A project carried all three as hand-written cmake in its own tree, which
is the layer a buildutil project holds nothing in."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from buildutil import deposit

CFG = {"cmake_option_prefix": "ACME", "module_define_prefix": "ACM"}
MACHINERY = (Path(deposit.__file__).resolve().parent / "templates" / "cmake" /
             "base" / "buildutil.cmake")

CXX = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")
e2e = pytest.mark.skipif(
  shutil.which("cmake") is None or shutil.which("ninja") is None or
  CXX is None, reason="needs cmake, ninja and a C++ compiler")

ROOT_CMAKE = """\
cmake_minimum_required(VERSION 3.25)
project(embedpy CXX)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
add_subdirectory(sources)
"""


# ------------------------------------------------- rendered machinery --

def _machinery():
  return MACHINERY.read_text()


def _at_file_scope(text, needle):
  """True when `needle` sits after the last endfunction(): root scope."""
  assert needle in text, needle
  return text.index(needle) > text.rindex("endfunction()")


def test_the_pybind11_prefix_joins_the_search_path_at_root_scope():
  text = _machinery()
  appended = 'list(APPEND CMAKE_PREFIX_PATH "${_buildutil_pybind_dir}")'
  assert _at_file_scope(text, appended), (
    "found inside a function, the prefix path dies with the call -- which "
    "only the bridge target could resolve pybind11")


def test_the_install_rpath_pass_is_scheduled_for_every_project():
  text = _machinery()
  assert _at_file_scope(text, "CALL _buildutil_apply_install_rpaths"), (
    "scheduled from a module branch, the pass only ran for projects with "
    "a shared sibling -- a static one gets no pass at all")


def test_the_extension_name_comes_off_the_entry_file():
  text = _machinery()
  assert 'string(REGEX REPLACE "\\\\.pybind\\\\.cpp$" "" _pb_module' in text
  assert "OUTPUT_NAME ${target})" not in text, (
    "the cmake target name is the directory's, not the module python "
    "imports")


def _scaffold(tmp_path):
  deposit.ensure(tmp_path, CFG)
  (tmp_path / "CMakeLists.txt").write_text(ROOT_CMAKE)
  src = tmp_path / "sources"
  src.mkdir()
  return src


def _cmake(tmp_path, build, *extra):
  return subprocess.run(
    ["cmake", "-S", str(tmp_path), "-B", str(build), "-G", "Ninja",
     f"-DCMAKE_INSTALL_PREFIX={tmp_path / 'inst'}", *extra],
    capture_output=True, text=True)


def _build(build):
  return subprocess.run(["cmake", "--build", str(build)],
                        capture_output=True, text=True)


@pytest.fixture(scope="module")
def pybind_venv(tmp_path_factory):
  """One real venv with pybind11 — the bridge probes the interpreter the
  driver runs under, and that is what a driver build hands over."""
  venv = tmp_path_factory.mktemp("pyb") / "venv"
  if subprocess.run([sys.executable, "-m", "venv", str(venv)],
                    capture_output=True).returncode != 0:
    pytest.skip("cannot create a venv")
  if subprocess.run([str(venv / "bin" / "pip"), "install", "pybind11"],
                    capture_output=True).returncode != 0:
    pytest.skip("cannot install pybind11 into the venv")
  return venv


EMBED_MAIN = """\
#include <pybind11/embed.h>
int main() {
  pybind11::scoped_interpreter guard{};
  pybind11::exec("print('embedded')");
  return 0;
}
"""

BRIDGE = """\
#include <pybind11/pybind11.h>
int thing();
PYBIND11_MODULE(widget, m) { m.def("thing", &thing); }
"""


def _embedding_project(tmp_path, entries=("widget.pybind.cpp",)):
  src = _scaffold(tmp_path)
  (src / "CMakeLists.txt").write_text(
    'Require(Python3 VERSION ">=3.9" SYSTEM'
    ' COMPONENTS Interpreter Development.Embed)\n'
    'Require(pybind11 VERSION ">=2.10" SYSTEM)\n'
    "Scan_subdirectories()\n")
  module = src / "acme" / "shell"
  module.mkdir(parents=True)
  (module / "CMakeLists.txt").write_text(
    "Init_submodule()\nLink_dependencies(pybind11::embed Python3::Python)\n")
  (module / "thing.cpp").write_text("int thing() { return 7; }\n")
  (module / "main.cpp").write_text(EMBED_MAIN)
  for entry in entries:
    (module / entry).write_text(BRIDGE)
  return tmp_path


@e2e
def test_a_module_that_embeds_python_resolves_pybind11(tmp_path, pybind_venv):
  """The module links pybind11::embed and declares it by Require, in
  a tree where the bridge's own find_package never ran for it."""
  root = _embedding_project(tmp_path, entries=())
  build = tmp_path / "b"
  configured = _cmake(root, build,
                      f"-DBUILDUTIL_PY={pybind_venv / 'bin' / 'python'}")
  assert configured.returncode == 0, configured.stdout + configured.stderr
  built = _build(build)
  assert built.returncode == 0, built.stdout + built.stderr
  assert (build / "bin" / "shell").is_file()


@e2e
def test_the_extension_is_importable_under_its_own_name(tmp_path, pybind_venv):
  """widget.pybind.cpp in sources/acme/shell shipped acme-shell.so,
  which no import statement can reach."""
  root = _embedding_project(tmp_path)
  build = tmp_path / "b"
  assert _cmake(root, build,
                f"-DBUILDUTIL_PY={pybind_venv / 'bin' / 'python'}"
                ).returncode == 0
  built = _build(build)
  assert built.returncode == 0, built.stdout + built.stderr
  extensions = [p.name for p in build.rglob("*.so")]
  assert any(name.startswith("widget.cpython-") for name in extensions), (
    f"no SOABI-tagged widget extension among {extensions}")
  imported = subprocess.run(
    [str(pybind_venv / "bin" / "python"), "-c",
     "import widget; print(widget.thing())"],
    capture_output=True, text=True,
    env=dict(os.environ, PYTHONPATH=str(build / "sources" / "acme" / "shell")))
  assert imported.returncode == 0, imported.stderr
  assert imported.stdout.strip() == "7"


@e2e
def test_two_entries_in_one_module_are_refused(tmp_path, pybind_venv):
  root = _embedding_project(tmp_path,
                            entries=("widget.pybind.cpp", "gadget.pybind.cpp"))
  configured = _cmake(root, tmp_path / "b",
                      f"-DBUILDUTIL_PY={pybind_venv / 'bin' / 'python'}")
  assert configured.returncode != 0
  assert "at most one *.pybind.cpp" in configured.stderr


@e2e
def test_the_embedded_interpreter_starts_from_a_bare_environment(
    tmp_path, pybind_venv):
  """libpython is outside the loader's path, and the installed binary
  used to keep $ORIGIN and nothing else."""
  root = _embedding_project(tmp_path, entries=())
  build = tmp_path / "b"
  assert _cmake(root, build,
                f"-DBUILDUTIL_PY={pybind_venv / 'bin' / 'python'}"
                ).returncode == 0
  assert _build(build).returncode == 0
  installed = subprocess.run(["cmake", "--install", str(build)],
                             capture_output=True, text=True)
  assert installed.returncode == 0, installed.stdout + installed.stderr
  app = tmp_path / "inst" / "acme" / "shell"
  ran = subprocess.run([str(app)], capture_output=True, text=True, env={})
  assert ran.returncode == 0, ran.stderr
  assert "embedded" in ran.stdout


HOST_SOURCE = "int host_answer() { return 42; }\n"
HOST_CONFIG = """\
add_library(HostThing::HostThing SHARED IMPORTED)
set_target_properties(HostThing::HostThing PROPERTIES
  IMPORTED_LOCATION "{library}")
set(HostThing_FOUND TRUE)
"""
HOST_MAIN = """\
int host_answer();
int main() { return host_answer() == 42 ? 0 : 1; }
"""


@pytest.fixture(scope="module")
def host_package(tmp_path_factory):
  """A shared library with a cmake config, outside the loader's path."""
  root = tmp_path_factory.mktemp("hostpkg")
  source = root / "host.cpp"
  source.write_text(HOST_SOURCE)
  library = root / "lib" / "libhostthing.so"
  library.parent.mkdir()
  # a SONAME, or the linker records the absolute path it was handed as
  # DT_NEEDED and the loader never consults an rpath at all
  built = subprocess.run(
    [CXX, "-shared", "-fPIC", "-Wl,-soname,libhostthing.so",
     "-o", str(library), str(source)], capture_output=True, text=True)
  assert built.returncode == 0, built.stderr
  config = root / "lib" / "cmake" / "HostThing"
  config.mkdir(parents=True)
  (config / "HostThingConfig.cmake").write_text(
    HOST_CONFIG.format(library=library))
  return root


def _host_project(tmp_path, require):
  src = _scaffold(tmp_path)
  (src / "CMakeLists.txt").write_text(f"{require}\nScan_subdirectories()\n")
  module = src / "acme" / "user"
  module.mkdir(parents=True)
  (module / "CMakeLists.txt").write_text(
    "Init_submodule()\nLink_dependencies(HostThing::HostThing)\n")
  (module / "main.cpp").write_text(HOST_MAIN)
  return tmp_path


def _install_and_run(tmp_path, host_package):
  build = tmp_path / "b"
  configured = _cmake(tmp_path, build, f"-DCMAKE_PREFIX_PATH={host_package}",
                      f"-DBUILDUTIL_PY={sys.executable}")
  assert configured.returncode == 0, configured.stdout + configured.stderr
  assert _build(build).returncode == 0
  assert subprocess.run(["cmake", "--install", str(build)],
                        capture_output=True).returncode == 0
  app = tmp_path / "inst" / "acme" / "user"
  return subprocess.run([str(app)], capture_output=True, text=True, env={})


@e2e
def test_a_declared_host_library_is_found_by_the_installed_binary(
    tmp_path, host_package):
  ran = _install_and_run(_host_project(tmp_path, "Require(HostThing SYSTEM)"),
                         host_package)
  assert ran.returncode == 0, ran.stderr


@e2e
def test_a_conan_managed_library_keeps_its_cache_path_out(
    tmp_path, host_package):
  """Without SYSTEM the package is conan's: it travels as [runtime] payload
  beside the binary, and a cache path in the rpath would let an artifact
  that ships none still start on the machine that built it."""
  ran = _install_and_run(_host_project(tmp_path, "Require(HostThing)"),
                         host_package)
  assert ran.returncode != 0
  assert "libhostthing.so" in ran.stderr
