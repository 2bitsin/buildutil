"""Require(... SYSTEM) end to end: the host's library beats a conan pin."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from buildutil import deposit, installcmd, packaging

PKG_PARENT = Path(installcmd.__file__).resolve().parents[1]
CXX = shutil.which("c++") or shutil.which("g++")

TOOLS = ("conan", "cmake", "ninja")
e2e = pytest.mark.skipif(
  not all(map(shutil.which, TOOLS)) or CXX is None,
  reason="needs conan, cmake, ninja and a C++ compiler")

HEADER = "#pragma once\nauto hostlib_which() -> char const*;\n"
SOURCE = ('#include "hostlib.hpp"\n'
          'auto hostlib_which() -> char const* { return "%s"; }\n')

HOST_CONFIG = """\
get_filename_component(_prefix "${{CMAKE_CURRENT_LIST_DIR}}/../../.." ABSOLUTE)
{dependencies}
if(NOT TARGET {name}::{name})
  add_library({name}::{name} SHARED IMPORTED)
  set_target_properties({name}::{name} PROPERTIES
    IMPORTED_LOCATION "${{_prefix}}/lib/lib{name}.so"
    INTERFACE_INCLUDE_DIRECTORIES "${{_prefix}}/include"{links})
endif()
"""

HOST_VERSION = """\
set(PACKAGE_VERSION {version})
if(PACKAGE_FIND_VERSION_MAJOR EQUAL {major} AND
   PACKAGE_FIND_VERSION VERSION_LESS_EQUAL PACKAGE_VERSION)
  set(PACKAGE_VERSION_COMPATIBLE TRUE)
endif()
"""

CONAN_RECIPE = """\
from conan import ConanFile
from conan.tools.cmake import CMake, cmake_layout


class Hostlib(ConanFile):
  name = "hostlib"
  settings = "os", "arch", "compiler", "build_type"
  package_type = "static-library"
  generators = "CMakeToolchain"
  exports_sources = "CMakeLists.txt", "hostlib.cpp", "hostlib.hpp"

  def layout(self):
    cmake_layout(self)

  def build(self):
    cmake = CMake(self)
    cmake.configure()
    cmake.build()

  def package(self):
    CMake(self).install()

  def package_info(self):
    self.cpp_info.libs = ["hostlib"]
    self.cpp_info.set_property("cmake_file_name", "hostlib")
    self.cpp_info.set_property("cmake_target_name", "hostlib::hostlib")
"""

CONAN_LISTS = """\
cmake_minimum_required(VERSION 3.20)
project(hostlib CXX)
add_library(hostlib STATIC hostlib.cpp)
install(TARGETS hostlib)
install(FILES hostlib.hpp DESTINATION include)
"""

PINNER_HEADER = "#pragma once\nauto pinner_which() -> char const*;\n"

PINNER_SOURCE = """\
#include <pinner/core/core.hpp>
#include <hostlib.hpp>
auto pinner_which() -> char const* { return hostlib_which(); }
"""

CONSUMER_MAIN = """\
#include <hostlib.hpp>
#include <pinner/core/core.hpp>
#include <cstdio>
auto main() -> int {
  std::printf("%s %s\\n", hostlib_which(), pinner_which());
  return 0;
}
"""


USER_CORE = """\
#include <{name}/core/core.hpp>
#include <{library}.hpp>
auto {name}_core() -> char const* {{ return {library}_which(); }}
"""

USER_EXTRA = """\
#include <{name}/core/core.hpp>
#include <{name}/extra/extra.hpp>
auto {name}_extra() -> char const* {{ return {name}_core(); }}
"""

THIRD_MAIN = """\
#include <{name}/extra/extra.hpp>
#include <cstdio>
auto main() -> int {{
  std::printf("%s\\n", {name}_extra());
  return 0;
}}
"""

SSLPINNER_RECIPE = """\
from conan import ConanFile


class SslPinner(ConanFile):
  name = "sslpinner"
  version = "1.0.0"
  package_type = "header-library"

  def requirements(self):
    self.requires("openssl/3.0.15")
"""

SSL_MAIN = """\
#include <openssl/ssl.h>
#include <cstdio>
auto main() -> int {
  std::printf("%s\\n", OpenSSL_version(OPENSSL_VERSION));
  return SSL_CTX_new(TLS_method()) == nullptr;
}
"""


def _write(path: Path, text: str) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(text)


def _environment(world: Path) -> dict:
  dropped = ("BUILDUTIL_ROOT", "CC", "CXX", "CMAKE_PREFIX_PATH")
  env = {key: value for key, value in os.environ.items()
         if not key.startswith(("CONAN_REMOTE_", "CI_ARTIFACTORY_"))
         and key not in dropped}
  env.update(PYTHONPATH=str(PKG_PARENT), BUILDUTIL_SYSTEM="1", BUILDUTIL="1",
             CONAN_HOME=str(world / "home"), BUILDUTIL_NO_CONAN_UPDATE="1",
             CMAKE_PREFIX_PATH=str(world / "host"), CCACHE_DISABLE="1",
             PATH=os.pathsep.join([str(Path(sys.executable).parent),
                                   os.environ.get("PATH", "")]))
  return env


def _run(argv, cwd: Path, env: dict) -> subprocess.CompletedProcess:
  return subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                        capture_output=True, text=True)


def _buildutil(project: Path, env: dict, *argv) -> subprocess.CompletedProcess:
  return _run([sys.executable, "-m", "buildutil", *argv], project, env)


def _host_library(host: Path, name: str, version: str, returns: str,
                  dependency: str = "") -> None:
  """lib<name>.so and its config, whose find_dependency finds dependency."""
  config = host / "lib" / "cmake" / name
  _write(host / "include" / f"{name}.hpp",
         f"#pragma once\nauto {name}_which() -> char const*;\n")
  _write(host / "src" / f"{name}.cpp",
         f'#include "{name}.hpp"\n'
         f'auto {name}_which() -> char const* {{ return "{returns}"; }}\n')
  _write(config / f"{name}Config.cmake", HOST_CONFIG.format(
    name=name,
    dependencies=(f"include(CMakeFindDependencyMacro)\n"
                  f"find_dependency({dependency})" if dependency else ""),
    links=(f"\n    INTERFACE_LINK_LIBRARIES {dependency}::{dependency}"
           if dependency else "")))
  _write(config / f"{name}ConfigVersion.cmake",
         HOST_VERSION.format(version=version, major=version.split(".")[0]))
  subprocess.run([CXX, "-shared", "-fPIC", f"-I{host / 'include'}",
                  str(host / "src" / f"{name}.cpp"),
                  "-o", str(host / "lib" / f"lib{name}.so")], check=True)


def _host_prefix(world: Path) -> None:
  host = world / "host"
  _host_library(host, "hostlib", "1.4.0", "host")
  _host_library(host, "link", "1.0.0", "link")
  _host_library(host, "chain", "1.0.0", "chain", dependency="link")
  _host_library(host, "top", "1.0.0", "top", dependency="chain")


def _conan_hostlib(world: Path, env: dict) -> None:
  recipe = world / "conan-hostlib"
  _write(recipe / "conanfile.py", CONAN_RECIPE)
  _write(recipe / "CMakeLists.txt", CONAN_LISTS)
  _write(recipe / "hostlib.hpp", HEADER)
  _write(recipe / "hostlib.cpp", SOURCE % "conan")
  for version in ("1.2.0", "1.5.0"):
    exported = _run(["conan", "export", str(recipe), "--version", version],
                    world, env)
    exported.check_returncode()


def _project(root: Path, env: dict, name: str, *init) -> Path:
  root.mkdir()
  _buildutil(root, env, "init", "--name", name, "--no-agents",
             *init).check_returncode()
  shutil.rmtree(root / "sources" / name)
  shutil.rmtree(root / "test_package", ignore_errors=True)
  return root


def _export_pinner(world: Path, env: dict, version: str, pin: str) -> None:
  """A baked library pinning conan's hostlib, so the cache can rebuild it."""
  pinner = _project(world / f"pinner-{version}", env, "pinner",
                    "--package", "library")
  core = pinner / "sources" / "pinner" / "core"
  _write(pinner / "sources" / "CMakeLists.txt",
         f'Require(hostlib VERSION "{pin}" CONAN hostlib)\n'
         "Scan_subdirectories()\n")
  _write(core / "CMakeLists.txt",
         "Init_submodule()\nLink_dependencies(hostlib::hostlib)\n")
  _write(core / "core.hpp", PINNER_HEADER)
  _write(core / "core.cpp", PINNER_SOURCE)
  installcmd.vendor_into(pinner)
  exported = _run(["conan", "export", ".", "--version", version], pinner, env)
  exported.check_returncode()


def _publish(world: Path, env: dict, name: str, requires: str,
             library: str, links: str) -> None:
  """A published two-module library whose core calls a host library."""
  published = _project(world / name, env, name, "--package", "library")
  modules = published / "sources" / name
  _write(published / "sources" / "CMakeLists.txt",
         requires + "Scan_subdirectories()\n")
  _write(modules / "core" / "CMakeLists.txt",
         f"Init_submodule()\nLink_dependencies({links})\n")
  _write(modules / "core" / "core.hpp",
         f"#pragma once\nauto {name}_core() -> char const*;\n")
  _write(modules / "core" / "core.cpp",
         USER_CORE.format(name=name, library=library))
  _write(modules / "extra" / "CMakeLists.txt",
         "Init_submodule()\nLink_dependencies(core)\n")
  _write(modules / "extra" / "extra.hpp",
         f"#pragma once\nauto {name}_extra() -> char const*;\n")
  _write(modules / "extra" / "extra.cpp", USER_EXTRA.format(name=name))
  installcmd.vendor_into(published)
  _run(["conan", "export", ".", "--version", "1.0.0"], published,
       env).check_returncode()


def _publish_wrapper(world: Path, env: dict, name: str) -> None:
  """The wrapper recipe publish uploads beside a package that forces it."""
  wrapper = world / f"{name}-wrapper"
  deposit.render_host_wrapper(wrapper, name)
  _run(["conan", "export", str(wrapper), "--name", name, "--version",
        "system", "--user", "host"], world, env).check_returncode()


CHAIN = 'Require(chain VERSION ">=1" SYSTEM)\n'
LINK = 'Require(link VERSION ">=1" SYSTEM)\n'
TOP = 'Require(top VERSION ">=1" SYSTEM)\n'


@pytest.fixture(scope="module")
def world(tmp_path_factory):
  """Host hostlib 1.4.0, conan's 1.2.0 and 1.5.0, a pinner of each, and a
  published package with a SYSTEM Require."""
  world = tmp_path_factory.mktemp("host-wins")
  env = _environment(world)
  _run(["conan", "remote", "remove", "*"], world, env)
  _host_prefix(world)
  _conan_hostlib(world, env)
  _export_pinner(world, env, "1.0.0", "1.2.0")
  _export_pinner(world, env, "1.1.0", "1.5.0")
  _publish(world, env, "hostuser",
           'Require(hostlib VERSION ">=1" SYSTEM)\n'
           'Require(pinner VERSION "1.0.0" CONAN pinner)\n',
           "hostlib", "hostlib::hostlib pinner::pinner")
  _publish(world, env, "chainfirst", CHAIN + LINK, "chain",
           "chain::chain link::link")
  _publish(world, env, "linkfirst", LINK + CHAIN, "chain",
           "chain::chain link::link")
  _publish(world, env, "topfirst", TOP + CHAIN + LINK, "top",
           "top::top chain::chain link::link")
  for name in ("hostlib", "chain", "link", "top"):
    _publish_wrapper(world, env, name)
  return world


def _consumer(world: Path, name: str, system: str,
              pinner: str) -> tuple[Path, dict]:
  env = _environment(world)
  consumer = _project(world / name, env, name, "--no-package")
  app = consumer / "sources" / "app"
  _write(consumer / "sources" / "CMakeLists.txt",
         f'Require(hostlib VERSION ">=1" {system})\n'
         f'Require(pinner VERSION "{pinner}" CONAN pinner)\n'
         "Scan_subdirectories()\n")
  _write(app / "CMakeLists.txt", "Init_submodule()\n"
         "Link_dependencies(hostlib::hostlib pinner::pinner)\n")
  _write(app / "main.cpp", CONSUMER_MAIN)
  return consumer, env


def _third(world: Path, name: str, package: str,
           own: str = "") -> tuple[Path, dict]:
  """A consumer of a published package with no SYSTEM line of its own."""
  env = _environment(world)
  third = _project(world / name, env, name, "--no-package")
  app = third / "sources" / "app"
  _write(third / "sources" / "CMakeLists.txt", own
         + f'Require({package} VERSION "1.0.0" CONAN {package})\n'
         "Scan_subdirectories()\n")
  _write(app / "CMakeLists.txt",
         f"Init_submodule()\nLink_dependencies({package}::extra)\n")
  _write(app / "main.cpp", THIRD_MAIN.format(name=package))
  return third, env


def _app(consumer: Path) -> Path:
  installed = (consumer / "_install").rglob("app")
  return next(path for path in installed if path.is_file())


def _graph_refs(consumer: Path) -> list[str]:
  graph_file = next((consumer / "_build").rglob("conan-graph.json"))
  nodes = json.loads(graph_file.read_text())["graph"]["nodes"].values()
  return [node["ref"].partition("#")[0] for node in nodes]


def _linked_hostlibs(consumer: Path, env: dict) -> list[str]:
  ldd = _run(["ldd", str(_app(consumer))], consumer, env).stdout
  return [line for line in ldd.splitlines() if "libhostlib" in line]


def _generated_lib_dirs(consumer: Path) -> list[str]:
  data = (consumer / "_build").rglob("hostlib-*-data.cmake")
  lines = "".join(path.read_text() for path in data).splitlines()
  return [line for line in lines if "_LIB_DIRS" in line]


@e2e
def test_the_host_library_replaces_a_dependencys_conan_pin(world):
  consumer, env = _consumer(world, "consumer-a", "SYSTEM", "1.0.0")
  built = _buildutil(consumer, env, "build", "--no-tests")
  assert built.returncode == 0, built.stdout + built.stderr
  ran = _run([str(_app(consumer))], consumer, env)
  assert ran.stdout == "host host\n", ran.stdout + ran.stderr
  linked = _linked_hostlibs(consumer, env)
  assert len(linked) == 1 and str(world / "host" / "lib") in linked[0]
  assert "hostlib/1.2.0" not in _graph_refs(consumer)
  assert "hostlib/system@host" in _graph_refs(consumer)
  lib_dirs = _generated_lib_dirs(consumer)
  assert lib_dirs, "the wrapper generated no hostlib config"
  assert all(str(world / "host" / "lib") in line for line in lib_dirs)


@e2e
def test_a_pin_above_the_host_is_refused_before_anything_builds(world):
  consumer, env = _consumer(world, "consumer-b", "SYSTEM", "1.1.0")
  built = _buildutil(consumer, env, "build", "--no-tests")
  output = built.stdout + built.stderr
  assert built.returncode != 0
  refusal = "pinner/1.1.0 pins hostlib/1.5.0; the host's hostlib is 1.4.0"
  assert refusal in output, output
  assert "Building from source" not in output


@e2e
def test_FORCE_takes_the_host_over_a_higher_pin_and_says_so(world):
  consumer, env = _consumer(world, "consumer-b2", "SYSTEM FORCE", "1.1.0")
  built = _buildutil(consumer, env, "build", "--no-tests")
  output = built.stdout + built.stderr
  assert built.returncode == 0, output
  line = ("hostlib: the host's 1.4.0 is forced over pinner/1.1.0's pin "
          "hostlib/1.5.0 (FORCE)")
  assert output.count(line) == 1, output
  assert f"WARN: {line}" in output, output
  assert _run([str(_app(consumer))], consumer, env).stdout == "host host\n"


def _run_environment(consumer: Path) -> str:
  scripts = (consumer / "_build").rglob("conanrunenv*.sh")
  return "".join(path.read_text() for path in scripts)


@e2e
def test_a_published_packages_SYSTEM_Require_reaches_its_consumer(world):
  third, env = _third(world, "third-c", "hostuser")
  built = _buildutil(third, env, "build", "--no-tests")
  assert built.returncode == 0, built.stdout + built.stderr
  ran = _run([str(_app(third))], third, env)
  assert ran.stdout == "host\n", ran.stdout + ran.stderr
  assert "hostlib/system@host" in _graph_refs(third)
  assert "hostlib/1.2.0" not in _graph_refs(third)
  linked = _linked_hostlibs(third, env)
  assert len(linked) == 1 and str(world / "host" / "lib") in linked[0]
  assert str(world / "host" / "lib") not in _run_environment(third)


@e2e
def test_a_consumers_own_pin_downstream_of_the_force_is_refused(world):
  third, env = _third(world, "third-d", "hostuser",
                      'Require(hostlib VERSION "1.2.0" CONAN hostlib)\n')
  built = _buildutil(third, env, "build", "--no-tests")
  output = built.stdout + built.stderr
  assert built.returncode != 0
  refusal = ("Version conflict: Conflict between hostlib/system@host and "
             "hostlib/1.2.0 in the graph.")
  assert refusal in output, output
  assert "Building from source" not in output


@e2e
@pytest.mark.parametrize("package, returns", [
  ("chainfirst", "chain"), ("linkfirst", "chain"), ("topfirst", "top")])
def test_a_dependencys_target_is_its_own_wrappers_in_either_order(
    world, package, returns):
  third, env = _third(world, f"third-{package}", package)
  built = _buildutil(third, env, "build", "--no-tests")
  assert built.returncode == 0, built.stdout + built.stderr
  assert _run([str(_app(third))], third, env).stdout == f"{returns}\n"
  refs = _graph_refs(third)
  assert "chain/system@host" in refs and "link/system@host" in refs


def _host_openssl() -> str | None:
  if not shutil.which("pkg-config"):
    return None
  version = subprocess.run(["pkg-config", "--modversion", "openssl"],
                           capture_output=True, text=True)
  return version.stdout.strip() if version.returncode == 0 else None


@e2e
@pytest.mark.skipif(_host_openssl() is None, reason="no host openssl.pc")
def test_a_conan_openssl_pin_yields_the_hosts_OpenSSL(world):
  env = _environment(world)
  recipe = world / "sslpinner"
  _write(recipe / "conanfile.py", SSLPINNER_RECIPE)
  _run(["conan", "export", str(recipe)], world, env).check_returncode()
  consumer = _project(world / "smoke", env, "smoke", "--no-package")
  _write(consumer / "sources" / "CMakeLists.txt",
         'Require(OpenSSL VERSION ">=3" SYSTEM)\n'
         'Require(sslpinner VERSION "1.0.0" CONAN sslpinner)\n'
         "Scan_subdirectories()\n")
  _write(consumer / "sources" / "app" / "CMakeLists.txt",
         "Init_submodule()\n"
         "Link_dependencies(OpenSSL::SSL OpenSSL::Crypto)\n")
  _write(consumer / "sources" / "app" / "main.cpp", SSL_MAIN)
  env["CMAKE_PREFIX_PATH"] = ""
  built = _buildutil(consumer, env, "build", "--no-tests")
  assert built.returncode == 0, built.stdout + built.stderr
  ran = _run([str(_app(consumer))], consumer, env)
  assert ran.stdout.startswith(f"OpenSSL {_host_openssl()}"), ran.stdout
  assert "openssl/system@host" in _graph_refs(consumer)
  assert "openssl/3.0.15" not in _graph_refs(consumer)


def _export_render(world: Path, env: dict, name: str, marker: str) -> str:
  """A wrapper render that differs in text only; its exported revision."""
  folder = world / f"{name}-render-{marker}"
  deposit.render_host_wrapper(folder, name)
  recipe = folder / "conanfile.py"
  recipe.write_text(recipe.read_text() + f"# {marker}\n")
  exported = _run(["conan", "export", str(folder), "--name", name,
                   "--version", "system", "--user", "host", "--format=json"],
                  world, env)
  exported.check_returncode()
  return json.loads(exported.stdout)["reference"].partition("#")[2]


def _package_ids(world: Path, env: dict) -> dict[str, str]:
  """ref#rrev to package id of hostuser's graph, as conan computes them."""
  _run(["conan", "profile", "detect", "--exist-ok"], world, env)
  info = _run(["conan", "graph", "info", "--requires=hostuser/1.0.0",
               "--format=json"], world, env)
  assert info.returncode == 0, info.stdout + info.stderr
  nodes = json.loads(info.stdout)["graph"]["nodes"].values()
  return {node["ref"]: node["package_id"] for node in nodes if node["ref"]}


def _node(ids: dict[str, str], name: str) -> tuple[str, str]:
  return next((ref, package) for ref, package in ids.items()
              if ref.startswith(f"{name}/"))


@e2e
def test_a_dependants_package_id_follows_the_host_never_the_revision(world):
  env = _environment(world)
  first = _export_render(world, env, "hostlib", "first")
  before = _package_ids(world, env)
  second = _export_render(world, env, "hostlib", "second")
  after = _package_ids(world, env)
  assert first != second
  assert _node(before, "hostlib")[0].endswith(first)
  assert _node(after, "hostlib")[0].endswith(second)
  assert _node(after, "hostuser") == _node(before, "hostuser")
  newer = world / "host-1.5"
  _host_library(newer, "hostlib", "1.5.0", "host")
  moved = _package_ids(world, {**env, "CMAKE_PREFIX_PATH": str(newer)})
  assert _node(moved, "hostuser")[1] != _node(before, "hostuser")[1]


@e2e
def test_update_keeps_the_revision_this_driver_rendered(world):
  env = {key: value for key, value in _environment(world).items()
         if key != "BUILDUTIL_NO_CONAN_UPDATE"}
  consumer, _ = _consumer(world, "consumer-update", "SYSTEM", "1.0.0")
  _export_render(world, env, "hostlib", "newest")
  built = _buildutil(consumer, env, "build", "--no-tests")
  assert built.returncode == 0, built.stdout + built.stderr
  rendered = consumer / "_bdudata" / "host" / "hostlib" / "conanfile.py"
  revision = packaging.requires_parser().recipe_revision(rendered.read_bytes())
  graph = next((consumer / "_build").rglob("conan-graph.json"))
  refs = [node["ref"] for node in
          json.loads(graph.read_text())["graph"]["nodes"].values()]
  assert f"hostlib/system@host#{revision}" in refs
