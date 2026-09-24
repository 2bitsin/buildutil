"""The host wrapper maps a probe of the host's package onto a cpp_info."""
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from buildutil import deposit

LIB = "/usr/lib/x86_64-linux-gnu"

OPENSSL_PROBE = json.loads(f"""\
{{"version": "3.5.6", "targets": {{
  "OpenSSL::Crypto": {{"package": "OpenSSL", "type": "UNKNOWN_LIBRARY",
    "locations": ["{LIB}/libcrypto.so"],
    "INTERFACE_INCLUDE_DIRECTORIES": ["/usr/include"],
    "INTERFACE_LINK_LIBRARIES": [], "INTERFACE_COMPILE_DEFINITIONS": []}},
  "OpenSSL::SSL": {{"package": "OpenSSL", "type": "UNKNOWN_LIBRARY",
    "locations": ["{LIB}/libssl.so"],
    "INTERFACE_INCLUDE_DIRECTORIES": ["/usr/include"],
    "INTERFACE_LINK_LIBRARIES": ["OpenSSL::Crypto"],
    "INTERFACE_COMPILE_DEFINITIONS": []}},
  "OpenSSL::applink": {{"package": "OpenSSL", "type": "INTERFACE_LIBRARY",
    "locations": [], "INTERFACE_INCLUDE_DIRECTORIES": [],
    "INTERFACE_LINK_LIBRARIES": [], "INTERFACE_COMPILE_DEFINITIONS": []}}}}}}
""")

FREERDP_SERVER_PROBE = json.loads(f"""\
{{"version": "3.15.0", "targets": {{
  "freerdp": {{"package": "FreeRDP", "type": "SHARED_LIBRARY",
    "locations": ["{LIB}/libfreerdp3.so.3.15.0"],
    "INTERFACE_INCLUDE_DIRECTORIES": ["/usr/include"],
    "INTERFACE_LINK_LIBRARIES": ["winpr"], "INTERFACE_COMPILE_DEFINITIONS": []}},
  "freerdp-server": {{"package": "FreeRDP-Server", "type": "SHARED_LIBRARY",
    "locations": ["{LIB}/libfreerdp-server3.so.3.15.0"],
    "INTERFACE_INCLUDE_DIRECTORIES": ["/usr/include"],
    "INTERFACE_LINK_LIBRARIES": ["winpr", "freerdp"],
    "INTERFACE_COMPILE_DEFINITIONS": []}},
  "winpr": {{"package": "WinPR", "type": "SHARED_LIBRARY",
    "locations": ["{LIB}/libwinpr3.so.3.15.0"],
    "INTERFACE_INCLUDE_DIRECTORIES": ["/usr/include/winpr3"],
    "INTERFACE_LINK_LIBRARIES": [], "INTERFACE_COMPILE_DEFINITIONS": []}},
  "winpr-hash": {{"package": "WinPR", "type": "EXECUTABLE",
    "locations": ["/usr/bin/winpr-hash3"], "INTERFACE_INCLUDE_DIRECTORIES": [],
    "INTERFACE_LINK_LIBRARIES": [], "INTERFACE_COMPILE_DEFINITIONS": []}}}}}}
""")


@pytest.fixture
def wrapper(real_conan, tmp_path):
  """The wrapper rendered for Require(OpenSSL ... SYSTEM), as a module."""
  import types
  deposit.render_host_wrapper(tmp_path, "OpenSSL")
  path = tmp_path / "conanfile.py"
  module = types.ModuleType("host_wrapper_under_test")
  module.__file__ = str(path)
  exec(compile(path.read_text(), str(path), "exec"), module.__dict__)
  return module


def _cpp_info(wrapper, facts, name, foreign=None):
  from conan.internal.model.cpp_info import CppInfo
  cpp_info = CppInfo(set_defaults=True)
  wrapper._cpp_info_from_facts(facts, cpp_info, name, foreign or {})
  return cpp_info


@pytest.fixture
def openssl(wrapper):
  return _cpp_info(wrapper, wrapper._facts_from_probe(OPENSSL_PROBE,
                                                      "OpenSSL"), "openssl")


def _facts(version, target, location, link=()):
  return {"version": version, "foreign": {}, "targets": {target: {
    "location": location, "include_dirs": [], "link": list(link),
    "defines": []}}}


def test_the_probe_reads_as_the_packages_own_library_targets(wrapper):
  facts = wrapper._facts_from_probe(OPENSSL_PROBE, "OpenSSL")
  assert facts["version"] == "3.5.6"
  assert facts["foreign"] == {}
  assert facts["targets"]["OpenSSL::SSL"] == {
    "location": f"{LIB}/libssl.so", "include_dirs": ["/usr/include"],
    "link": ["OpenSSL::Crypto"], "defines": []}


def test_each_host_target_is_a_component_interface_targets_included(openssl):
  names = {name: component.get_property("cmake_target_name")
           for name, component in openssl.components.items()}
  assert names == {"crypto": "OpenSSL::Crypto", "ssl": "OpenSSL::SSL",
                   "applink": "OpenSSL::applink"}
  assert openssl.components["applink"].libs == []


def test_ssl_requires_its_sibling_crypto(openssl):
  assert openssl.components["ssl"].requires == ["crypto"]


def test_a_library_is_its_file_at_the_hosts_absolute_directory(openssl):
  ssl = openssl.components["ssl"]
  assert (ssl.libdirs, ssl.libs) == ([LIB], ["libssl.so"])
  assert ssl.includedirs == ["/usr/include"]


@pytest.mark.parametrize("file_name", ["libSDL2-2.0.so.0",
                                       "libpython3.13.so.1.0"])
def test_a_dotted_library_name_is_published_whole(wrapper, file_name):
  cpp_info = _cpp_info(wrapper, _facts("1.0", "x::x", f"{LIB}/{file_name}"),
                       "x")
  assert (cpp_info.libdirs, cpp_info.libs) == ([LIB], [file_name])


def test_the_package_carries_the_host_version_and_file_name(openssl):
  assert openssl.get_property("system_package_version") == "3.5.6"
  assert openssl.get_property("cmake_file_name") == "OpenSSL"
  assert openssl.get_property("cmake_target_name") is None


def test_a_package_of_one_target_is_that_target(wrapper):
  cpp_info = _cpp_info(wrapper, _facts("1.4.0", "hostlib::hostlib",
                                       "/opt/host/lib/libhostlib.so", ["m"]),
                       "hostlib")
  assert cpp_info.get_property("cmake_target_name") == "hostlib::hostlib"
  assert (cpp_info.libdirs, cpp_info.libs, cpp_info.system_libs) == (
    ["/opt/host/lib"], ["libhostlib.so"], ["m"])


def test_a_dependencys_targets_belong_to_its_own_wrapper(wrapper):
  facts = wrapper._facts_from_probe(FREERDP_SERVER_PROBE, "FreeRDP-Server")
  assert list(facts["targets"]) == ["freerdp-server"]
  assert facts["foreign"] == {"freerdp": "FreeRDP", "winpr": "WinPR"}
  cpp_info = _cpp_info(wrapper, facts, "freerdp-server", {
    "winpr": "winpr::winpr", "freerdp": "freerdp::freerdp"})
  assert cpp_info.get_property("cmake_target_name") == "freerdp-server"
  assert cpp_info.requires == ["winpr::winpr", "freerdp::freerdp"]


def test_a_dependency_only_found_on_the_way_is_not_required(wrapper):
  probe = json.loads(json.dumps(FREERDP_SERVER_PROBE))
  probe["targets"]["freerdp-server"]["INTERFACE_LINK_LIBRARIES"] = ["freerdp"]
  facts = wrapper._facts_from_probe(probe, "FreeRDP-Server")
  assert facts["foreign"] == {"freerdp": "FreeRDP"}


def _requirements(wrapper, monkeypatch, packages):
  monkeypatch.setitem(wrapper.__dict__, "cross_building", lambda conanfile: False)
  facts = wrapper._facts_from_probe(FREERDP_SERVER_PROBE, "FreeRDP-Server")
  required = []
  fake = SimpleNamespace(options=SimpleNamespace(packages=packages),
                         _facts=lambda: facts, ref="freerdp-server/system@host",
                         requires=lambda ref, options: required.append(
                           (ref, options)))
  wrapper.HostPackage.requirements(fake)
  return required


def test_a_dependency_with_its_own_SYSTEM_line_is_its_wrapper(wrapper,
                                                             monkeypatch):
  packages = "FreeRDP=freerdp WinPR=winpr:Tools,Core"
  assert _requirements(wrapper, monkeypatch, packages) == [
    ("freerdp/system@host", {"components": "", "packages": packages}),
    ("winpr/system@host", {"components": "Tools Core", "packages": packages})]


def test_a_dependency_without_a_SYSTEM_line_names_the_line_to_add(
    wrapper, monkeypatch):
  from conan.errors import ConanException
  with pytest.raises(ConanException,
                     match=r"\['winpr'\] through find_package\(WinPR\); add "
                           r'Require\(WinPR VERSION "<floor>" SYSTEM\)'):
    _requirements(wrapper, monkeypatch, "FreeRDP=freerdp")


@pytest.mark.parametrize("item, named", [
  ("$<$<CONFIG:Debug>:dbg>", "a generator expression"),
  ("Other::thing", "did not import")])
def test_a_link_item_the_wrapper_cannot_describe_is_refused(wrapper, item,
                                                            named):
  from conan.errors import ConanException
  with pytest.raises(ConanException, match=named):
    _cpp_info(wrapper, _facts("1.0", "x::x", "/opt/libx.so", [item]), "x")


def test_a_link_only_item_is_the_item(wrapper):
  cpp_info = _cpp_info(wrapper, _facts("1.0", "x::x", "/opt/libx.so",
                                       ["$<LINK_ONLY:m>"]), "x")
  assert cpp_info.system_libs == ["m"]


def test_a_host_package_without_a_version_is_a_probe_failure(wrapper):
  from conan.errors import ConanException
  with pytest.raises(ConanException, match="set no hostlib_VERSION"):
    wrapper._facts_from_probe({"version": "", "targets": {}}, "hostlib")


def test_the_trace_names_every_file_the_find_read(wrapper, tmp_path):
  config, header = tmp_path / "xConfig.cmake", tmp_path / "xv.h"
  config.write_text("")
  header.write_text("")
  trace = "\n".join(json.dumps(event) for event in [
    {"version": {"major": 1, "minor": 2}},
    {"file": str(config), "cmd": "set", "args": ["a", "b"]},
    {"file": str(config), "cmd": "file", "args": ["STRINGS", str(header), "v"]},
    {"file": "/scratch/CMakeLists.txt", "cmd": "project", "args": ["p"]}])
  assert wrapper._read_files(wrapper._trace_events(trace), "/scratch") == sorted(
    [str(config), str(header)])


def test_the_trace_names_the_pkg_config_modules_the_find_asked_for(wrapper):
  events = [{"cmd": "pkg_check_modules",
             "args": ["_OPENSSL", "QUIET", "openssl>=3", "libcrypto"]},
            {"cmd": "PKG_SEARCH_MODULE", "args": ["Z", "REQUIRED", "zlib"]}]
  assert wrapper._pkg_config_modules(events) == {"openssl", "libcrypto", "zlib"}


@pytest.mark.skipif(shutil.which("pkg-config") is None, reason="no pkg-config")
def test_a_modules_pc_file_is_where_pkg_config_finds_it(wrapper, tmp_path,
                                                        monkeypatch):
  (tmp_path / "hostlib.pc").write_text(
    "Name: hostlib\nDescription: d\nVersion: 1.4.0\n")
  monkeypatch.setenv("PKG_CONFIG_PATH", str(tmp_path))
  assert wrapper._pkg_config_files({"hostlib", "absent-module"}) == [
    str(tmp_path / "hostlib.pc")]


class _Probed:
  """Just enough of HostPackage for _cached_facts, counting probes."""

  def __init__(self, wrapper, components, files):
    self.options = SimpleNamespace(components=components)
    self.settings = SimpleNamespace(get_safe=lambda name: "x")
    self.name, self.files, self.probes = "hostlib", files, 0
    self._cached_facts = lambda: wrapper.HostPackage._cached_facts(self)
    self._cache_key = lambda: wrapper.HostPackage._cache_key(self)

  def _probe(self):
    self.probes += 1
    return {"version": "1.4.0", "targets": {}, "foreign": {}}, self.files


def test_the_cache_is_keyed_on_recipe_components_and_probe_environment(
    wrapper, tmp_path, monkeypatch):
  monkeypatch.setenv("CONAN_HOME", str(tmp_path / "home"))
  version = tmp_path / "hostlibConfigVersion.cmake"
  version.write_text("1.4.0")
  probed = _Probed(wrapper, "", [str(version)])
  probed._cached_facts()
  probed._cached_facts()
  assert probed.probes == 1
  probed.options.components = "Interpreter"
  probed._cached_facts()
  monkeypatch.setenv("CMAKE_PREFIX_PATH", "/opt/other")
  probed._cached_facts()
  monkeypatch.setenv("OpenSSL_ROOT", "/opt/openssl")
  probed._cached_facts()
  assert probed.probes == 4
  version.write_text("1.5.0 and longer")
  probed._cached_facts()
  assert probed.probes == 5
  recipe = Path(wrapper.__file__)
  recipe.write_text(recipe.read_text() + "\n")
  probed._cached_facts()
  assert probed.probes == 6
  for variable in ("PATH", "PKG_CONFIG_PATH"):
    monkeypatch.setenv(variable, f"/opt/{variable.lower()}")
    probed._cached_facts()
  assert probed.probes == 8
  written = sorted(os.listdir(tmp_path / "home" / "buildutil-host"))
  assert len(written) == 7 and all(name.endswith(".json") for name in written)


def test_a_config_newly_installed_at_a_searched_prefix_probes_again(
    wrapper, tmp_path, monkeypatch):
  monkeypatch.setenv("CONAN_HOME", str(tmp_path / "home"))
  monkeypatch.setenv("CMAKE_PREFIX_PATH", str(tmp_path / "prefix"))
  probed = _Probed(wrapper, "", [])
  probed._cached_facts()
  (tmp_path / "prefix" / "lib" / "cmake" / "OpenSSL-3").mkdir(parents=True)
  probed._cached_facts()
  probed._cached_facts()
  assert probed.probes == 2


CMAKE = shutil.which("cmake")

DEP_CONFIG = """\
set(dep_VERSION 2.0.0)
add_library(dep::dep SHARED IMPORTED)
set_target_properties(dep::dep PROPERTIES IMPORTED_LOCATION /opt/dep/libdep.so)
"""

TOP_CONFIG = """\
include(CMakeFindDependencyMacro)
find_dependency(dep)
add_library(top::top SHARED IMPORTED)
set_target_properties(top::top PROPERTIES
  IMPORTED_LOCATION /opt/top/libtop.so INTERFACE_LINK_LIBRARIES dep::dep)
"""

VERSION = """\
set(PACKAGE_VERSION {version})
set(PACKAGE_VERSION_COMPATIBLE TRUE)
"""


@pytest.mark.skipif(CMAKE is None, reason="needs cmake")
def test_the_probe_tells_a_packages_targets_from_its_dependencys(
    wrapper, tmp_path, monkeypatch):
  prefix = tmp_path / "prefix" / "lib" / "cmake"
  for name, config, version in (("dep", DEP_CONFIG, "2.0.0"),
                                ("top", TOP_CONFIG, "1.0.0")):
    (prefix / name).mkdir(parents=True)
    (prefix / name / f"{name}Config.cmake").write_text(config)
    (prefix / name / f"{name}ConfigVersion.cmake").write_text(
      VERSION.format(version=version))
  monkeypatch.setenv("CMAKE_PREFIX_PATH", str(tmp_path / "prefix"))
  monkeypatch.setitem(wrapper.__dict__, "CMAKE_NAME", "top")
  host = SimpleNamespace(
    conf=SimpleNamespace(get=lambda name: None), ref="top/system@host",
    options=SimpleNamespace(components=""),
    settings=SimpleNamespace(build_type="Release"))
  host._probe_command = (lambda *args:
                         wrapper.HostPackage._probe_command(host, *args))
  facts, files = wrapper.HostPackage._probe(host)
  assert facts["version"] == "1.0.0"
  assert list(facts["targets"]) == ["top::top"]
  assert facts["targets"]["top::top"]["link"] == ["dep::dep"]
  assert facts["foreign"] == {"dep::dep": "dep"}
  assert {str(prefix / name / f"{name}ConfigVersion.cmake")
          for name in ("dep", "top")} <= set(files)


THREADED_CONFIG = """\
include(CMakeFindDependencyMacro)
find_dependency(Threads)
add_library(threaded::threaded SHARED IMPORTED)
set_target_properties(threaded::threaded PROPERTIES
  IMPORTED_LOCATION /opt/threaded/libthreaded.so
  INTERFACE_LINK_LIBRARIES "Threads::Threads;${CMAKE_DL_LIBS};m")
"""


@pytest.mark.skipif(CMAKE is None, reason="needs cmake")
def test_threads_and_the_runtime_libraries_are_system_libs_not_packages(
    wrapper, tmp_path, monkeypatch):
  config = tmp_path / "prefix" / "lib" / "cmake" / "threaded"
  config.mkdir(parents=True)
  (config / "threadedConfig.cmake").write_text(THREADED_CONFIG)
  (config / "threadedConfigVersion.cmake").write_text(
    VERSION.format(version="1.0.0"))
  monkeypatch.setenv("CMAKE_PREFIX_PATH", str(tmp_path / "prefix"))
  monkeypatch.setitem(wrapper.__dict__, "CMAKE_NAME", "threaded")
  host = SimpleNamespace(
    conf=SimpleNamespace(get=lambda name: None), ref="threaded/system@host",
    options=SimpleNamespace(components=""),
    settings=SimpleNamespace(build_type="Release"))
  host._probe_command = (lambda *args:
                         wrapper.HostPackage._probe_command(host, *args))
  facts, _ = wrapper.HostPackage._probe(host)
  assert facts["foreign"] == {}
  cpp_info = _cpp_info(wrapper, facts, "threaded")
  assert cpp_info.system_libs == ["pthread", "dl", "m"]
  assert cpp_info.requires == []


def test_the_wrapper_publishes_its_targets_as_consumers_spell_them(openssl):
  assert openssl.get_property("buildutil_host_targets") == {
    "OpenSSL::SSL": "openssl::ssl", "OpenSSL::Crypto": "openssl::crypto",
    "OpenSSL::applink": "openssl::applink"}
