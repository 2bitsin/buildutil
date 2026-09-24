"""Link_dependencies(RUNTIME <module>): a module that dlopens a sibling finds it, built and installed."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from buildutil import deposit

PYSUPPORT = Path(deposit.__file__).resolve().parent / "pysupport"
CFG = {"cmake_option_prefix": "ACME", "module_define_prefix": "ACM"}

pytestmark = pytest.mark.skipif(
  not sys.platform.startswith("linux") or shutil.which("readelf") is None
  or shutil.which("cmake") is None or shutil.which("ninja") is None
  or not (shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")),
  reason="needs Linux, readelf, cmake, ninja and a C++ compiler")

ROOT_CMAKE = """\
cmake_minimum_required(VERSION 3.25)
project(loads CXX)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
add_subdirectory(sources)
"""

LOADER = """\
#include <dlfcn.h>
extern "C" _Public_() int loaded_answer() {
  void* plugin = dlopen("libplugin.so", RTLD_NOW);
  if (plugin == nullptr) { return -1; }
  auto answer = reinterpret_cast<int (*)()>(dlsym(plugin, "plugin_answer"));
  return answer == nullptr ? -2 : answer();
}
"""

PLUGIN = 'extern "C" _Public_() int plugin_answer() { return 42; }\n'
LOADER_SIGNATURE = 'extern "C" _Public_() int loaded_answer()'

TOOL = """\
extern "C" int loaded_answer();
int main() { return loaded_answer() == 42 ? 0 : 1; }
"""


def _tree(root, loader_links="Link_dependencies(dl RUNTIME plugin)\n", plugin="plugins/plugin"):
  deposit.ensure(root, CFG)
  (root / "CMakeLists.txt").write_text(ROOT_CMAKE)
  sources = root / "sources"
  sources.mkdir()
  (sources / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  modules = {"host": ("Init_submodule()\n" + loader_links, "main.so.cpp", LOADER),
             plugin: ("Init_submodule()\n", "main.so.cpp", PLUGIN),
             "tool": ("Init_submodule()\nLink_dependencies(host)\n", "main.cpp", TOOL)}
  for directory, (cmake, name, text) in modules.items():
    module = sources / directory
    module.mkdir(parents=True)
    (module / "CMakeLists.txt").write_text(cmake)
    (module / name).write_text(text)
  return root


def _configure(root):
  return subprocess.run(["cmake", "-S", str(root), "-B", str(root / "b"), "-G", "Ninja",
                         f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}"], capture_output=True, text=True)


def _install(root):
  configured = _configure(root)
  assert configured.returncode == 0, configured.stdout + configured.stderr
  for command in (["cmake", "--build", str(root / "b")],
                  ["cmake", "--install", str(root / "b"), "--prefix", str(root / "prefix")]):
    done = subprocess.run(command, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
  return root


def _runpath(library):
  dynamic = subprocess.run(["readelf", "-d", str(library)], capture_output=True, text=True,
                           check=True).stdout
  return [line for line in dynamic.splitlines() if "RUNPATH" in line or "RPATH" in line]


def _run(binary):
  return subprocess.run([str(binary)], capture_output=True, text=True,
                        env={"PATH": "/usr/bin:/bin"})


def test_the_installed_loader_finds_its_sibling(tmp_path):
  _install(_tree(tmp_path))
  assert "$ORIGIN/plugins" in "".join(_runpath(tmp_path / "prefix" / "libhost.so"))
  ran = _run(tmp_path / "prefix" / "tool")
  assert ran.returncode == 0, ran.stderr


def test_the_built_loader_finds_its_sibling(tmp_path):
  _install(_tree(tmp_path))
  runpath = "".join(_runpath(tmp_path / "b" / "sources" / "host" / "libhost.so"))
  assert str(tmp_path / "b" / "sources" / "plugins" / "plugin") in runpath, runpath
  ran = _run(tmp_path / "b" / "bin" / "tool")
  assert ran.returncode == 0, ran.stderr


def test_without_the_declaration_the_installed_loader_does_not(tmp_path):
  _install(_tree(tmp_path, "Link_dependencies(dl)\n"))
  assert _run(tmp_path / "prefix" / "tool").returncode != 0


@pytest.mark.parametrize("links,message", [
  ("Link_dependencies(RUNTIME dl)\n", "names no module of this project"),
  ("Link_dependencies(RUNTIME tool)\n", "Only a shared library can be loaded"),
])
def test_a_runtime_load_that_cannot_be_one_is_refused(tmp_path, links, message):
  configured = _configure(_tree(tmp_path, links))
  assert configured.returncode != 0
  assert message in configured.stderr, configured.stderr


def test_whatever_runs_the_loader_builds_the_loaded_module_first(tmp_path):
  """The app dlopens the plugin and links nothing of it; building the app alone builds both."""
  tree = _tree(tmp_path)
  app = tree / "sources" / "app"
  app.mkdir()
  (app / "CMakeLists.txt").write_text("Init_submodule()\nLink_dependencies(dl RUNTIME plugin)\n")
  (app / "main.cpp").write_text(LOADER.replace(LOADER_SIGNATURE, "int main_answer()")
                                + "int main() { return main_answer() == 42 ? 0 : 1; }\n")
  assert _configure(tree).returncode == 0
  built = subprocess.run(["cmake", "--build", str(tree / "b"), "--target", "app"],
                         capture_output=True, text=True)
  assert built.returncode == 0, built.stdout + built.stderr
  assert _run(tree / "b" / "bin" / "app").returncode == 0


def test_a_loaded_module_that_does_not_build_here_is_refused(tmp_path):
  configured = _configure(_tree(tmp_path, plugin="plugins/plugin.macos"))
  assert configured.returncode != 0
  assert "which does not build for" in " ".join(configured.stderr.split()), configured.stderr


def test_an_application_s_tests_find_the_module_it_loads(tmp_path):
  """The dlopen is in the app's own code, which its test executable links: the test's rpath is searched."""
  tree = _tree(tmp_path)
  (tree / "CMakeLists.txt").write_text(ROOT_CMAKE.replace(
    "include(buildutil)", "add_library(GTest::gtest_main INTERFACE IMPORTED)\n"
                          "add_library(GTest::gtest INTERFACE IMPORTED)\ninclude(buildutil)"))
  app = tree / "sources" / "app"
  app.mkdir()
  (app / "CMakeLists.txt").write_text("Init_submodule()\nLink_dependencies(dl RUNTIME plugin)\n")
  (app / "load.cpp").write_text(LOADER.replace(LOADER_SIGNATURE, "int main_answer()"))
  checks = "int main_answer();\nint main() { return main_answer() == 42 ? 0 : 1; }\n"
  (app / "main.cpp").write_text(checks)
  (app / "load.test.cpp").write_text(checks)
  configured = _configure(tree)
  assert configured.returncode == 0, configured.stdout + configured.stderr
  built = subprocess.run(["cmake", "--build", str(tree / "b"), "--target", "app-tests"],
                         capture_output=True, text=True)
  assert built.returncode == 0, built.stdout + built.stderr
  tests = next((tree / "b" / "sources" / "app").glob("app-tests"))
  assert str(tree / "b" / "sources" / "plugins" / "plugin") in "".join(_runpath(tests))
  ran = subprocess.run(["ctest", "--test-dir", str(tree / "b"), "--output-on-failure"],
                       capture_output=True, text=True)
  assert ran.returncode == 0 and "100% tests passed" in ran.stdout, ran.stdout + ran.stderr
