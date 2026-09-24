"""The host's @CMAKE_NAME@ as the conan package <conan>/system@host."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from conan import ConanFile
from conan.errors import ConanException, ConanInvalidConfiguration
from conan.tools.build import cross_building

CMAKE_NAME = "@CMAKE_NAME@"

_PROBE_LISTS = """\
cmake_minimum_required(VERSION 3.20)
project(probe C)
set_property(GLOBAL PROPERTY probe_depth 0)
macro(find_package)
  get_property(_probe_depth GLOBAL PROPERTY probe_depth)
  math(EXPR _probe_depth "${_probe_depth} + 1")
  set_property(GLOBAL PROPERTY probe_depth ${_probe_depth})
  get_property(_probe_before DIRECTORY PROPERTY IMPORTED_TARGETS)
  set_property(GLOBAL PROPERTY probe_before_${_probe_depth}
               "${_probe_before}")
  _find_package(${ARGV})
  get_property(_probe_depth GLOBAL PROPERTY probe_depth)
  get_property(_probe_before GLOBAL PROPERTY probe_before_${_probe_depth})
  get_property(_probe_after DIRECTORY PROPERTY IMPORTED_TARGETS)
  foreach(_probe_target IN LISTS _probe_after)
    get_property(_probe_owned GLOBAL PROPERTY probe_owner_${_probe_target} SET)
    if(NOT _probe_target IN_LIST _probe_before AND NOT _probe_owned)
      set_property(GLOBAL PROPERTY probe_owner_${_probe_target} "${ARGV0}")
    endif()
  endforeach()
  math(EXPR _probe_depth "${_probe_depth} - 1")
  set_property(GLOBAL PROPERTY probe_depth ${_probe_depth})
endmacro()
function(probe_string out value)
  string(REPLACE "\\\\" "\\\\\\\\" value "${value}")
  string(REPLACE "\\"" "\\\\\\"" value "${value}")
  set(${out} "\\"${value}\\"" PARENT_SCOPE)
endfunction()
function(probe_list out)
  set(json "[]")
  set(index 0)
  foreach(item IN LISTS ARGN)
    probe_string(item "${item}")
    string(JSON json SET "${json}" ${index} "${item}")
    math(EXPR index "${index} + 1")
  endforeach()
  set(${out} "${json}" PARENT_SCOPE)
endfunction()
get_property(before DIRECTORY PROPERTY IMPORTED_TARGETS)
find_package(${PROBE_NAME} REQUIRED ${PROBE_COMPONENTS})
get_property(after DIRECTORY PROPERTY IMPORTED_TARGETS)
probe_string(version "${${PROBE_NAME}_VERSION}")
set(json "{}")
string(JSON json SET "${json}" version "${version}")
string(JSON json SET "${json}" targets "{}")
foreach(target IN LISTS after)
  if(target IN_LIST before)
    continue()
  endif()
  get_property(package GLOBAL PROPERTY probe_owner_${target})
  get_target_property(type ${target} TYPE)
  get_target_property(configurations ${target} IMPORTED_CONFIGURATIONS)
  set(locations "")
  foreach(property IN ITEMS IMPORTED_LOCATION_${PROBE_BUILD_TYPE}
          IMPORTED_LOCATION)
    get_target_property(location ${target} ${property})
    if(location)
      list(APPEND locations "${location}")
    endif()
  endforeach()
  foreach(configuration IN LISTS configurations)
    get_target_property(location ${target} IMPORTED_LOCATION_${configuration})
    if(location)
      list(APPEND locations "${location}")
    endif()
  endforeach()
  probe_string(package "${package}")
  probe_string(type "${type}")
  probe_list(locations ${locations})
  set(fact "{}")
  string(JSON fact SET "${fact}" package "${package}")
  string(JSON fact SET "${fact}" type "${type}")
  string(JSON fact SET "${fact}" locations "${locations}")
  foreach(property IN ITEMS INTERFACE_INCLUDE_DIRECTORIES
          INTERFACE_LINK_LIBRARIES INTERFACE_COMPILE_DEFINITIONS)
    get_target_property(value ${target} ${property})
    if(NOT value)
      set(value "")
    endif()
    probe_list(value ${value})
    string(JSON fact SET "${fact}" ${property} "${value}")
  endforeach()
  string(JSON json SET "${json}" targets ${target} "${fact}")
endforeach()
file(WRITE "${PROBE_OUT}" "${json}")
"""

_LIBRARY_TYPES = {"SHARED_LIBRARY", "STATIC_LIBRARY", "UNKNOWN_LIBRARY",
                  "INTERFACE_LIBRARY"}

# link items naming the C runtime's own libraries: no package, no version
_RUNTIME_LIBRARIES = {
  "Threads::Threads": "pthread", "-pthread": "pthread", "pthread": "pthread",
  "-lpthread": "pthread", "dl": "dl", "-ldl": "dl", "m": "m", "-lm": "m",
  "rt": "rt", "-lrt": "rt"}

_PKG_CONFIG_CALLS = {"pkg_check_modules", "pkg_search_module"}
_PKG_CONFIG_KEYWORDS = {"REQUIRED", "QUIET", "NO_CMAKE_PATH", "GLOBAL",
                        "NO_CMAKE_ENVIRONMENT_PATH", "IMPORTED_TARGET"}
_CONFIG_ROOTS = ("lib/cmake", "lib64/cmake", "lib/*/cmake", "share/cmake")

HOST_TARGETS = "buildutil_host_targets"


def _component_name(target: str) -> str:
  """OpenSSL::SSL is ssl; a target without a namespace is its own name."""
  return target.rpartition("::")[2].lower()


def _facts_from_probe(probe: dict, name: str) -> dict:
  """The version, the own library targets, and the foreign targets they link."""
  if not probe["version"]:
    raise ConanException(
      f"find_package({name}) set no {name}_VERSION, so the host's "
      f"{name} has no version to check the floor and the pins against")
  libraries = {target: fact for target, fact in probe["targets"].items()
               if fact["type"] in _LIBRARY_TYPES}
  own = {target: {"location": next(iter(fact["locations"]), ""),
                  "include_dirs": fact["INTERFACE_INCLUDE_DIRECTORIES"],
                  "link": fact["INTERFACE_LINK_LIBRARIES"],
                  "defines": fact["INTERFACE_COMPILE_DEFINITIONS"]}
         for target, fact in libraries.items() if fact["package"] == name}
  linked = {_link_item(item) for fact in own.values() for item in fact["link"]}
  foreign = {target: fact["package"] for target, fact in libraries.items()
             if fact["package"] != name and target in linked
             and target not in _RUNTIME_LIBRARIES}
  return {"version": probe["version"], "targets": own, "foreign": foreign}


def _trace_events(trace: str) -> list[dict]:
  return [json.loads(line) for line in trace.splitlines() if line]


def _read_files(events: list[dict], scratch: str) -> list[str]:
  """Every file the find executed or read, from cmake's json trace."""
  files = set()
  for event in events:
    files.add(event.get("file", ""))
    arguments = event.get("args", [])
    if event.get("cmd", "").lower() == "file" and len(arguments) > 1 \
       and arguments[0] in ("STRINGS", "READ"):
      files.add(arguments[1])
  return sorted(path for path in files
                if path and not path.startswith(scratch)
                and os.path.isfile(path))


def _pkg_config_modules(events: list[dict]) -> set[str]:
  """The pkg-config modules the find asked for, version constraints dropped."""
  modules = set()
  for event in events:
    if event.get("cmd", "").lower() in _PKG_CONFIG_CALLS:
      modules.update(argument.split("<")[0].split(">")[0].split("=")[0]
                     for argument in event["args"][1:]
                     if argument not in _PKG_CONFIG_KEYWORDS)
  return modules


def _pkg_config_files(modules: set[str]) -> list[str]:
  """Each module's .pc file, where pkg-config finds it now."""
  pkg_config = shutil.which("pkg-config")
  if pkg_config is None:
    return []
  files = []
  for module in sorted(modules):
    run = subprocess.run([pkg_config, "--variable=pcfiledir", module],
                         capture_output=True, text=True)
    if run.returncode == 0 and run.stdout.strip():
      files.append(os.path.join(run.stdout.strip(), f"{module}.pc"))
  return files


def _search_prefixes() -> list[str]:
  """The prefixes find_package searches for a config, in its own order."""
  variables = (f"{CMAKE_NAME}_ROOT", f"{CMAKE_NAME.upper()}_ROOT",
               "CMAKE_PREFIX_PATH")
  prefixes = [prefix for variable in variables
              for prefix in os.environ.get(variable, "").split(os.pathsep)
              if prefix]
  prefixes += [os.path.dirname(entry.rstrip("/"))
               for entry in os.environ.get("PATH", "").split(os.pathsep)
               if entry.rstrip("/").endswith(("/bin", "/sbin"))]
  return prefixes + ["/usr/local", "/usr", "/"]


def _config_dirs() -> list[str]:
  """Every <prefix>/<config root>/<Name>* directory a find could pick."""
  wanted = CMAKE_NAME.lower()
  found = []
  for prefix in _search_prefixes():
    for root in _CONFIG_ROOTS:
      for folder in sorted(Path(prefix).glob(root)):
        found += [str(entry) for entry in sorted(folder.iterdir())
                  if entry.is_dir() and entry.name.lower().startswith(wanted)]
  return found


def _stamp(path: str) -> list[int] | None:
  try:
    status = os.stat(path)
  except OSError:
    return None
  return [status.st_size, status.st_mtime_ns]


def _fingerprint(paths: list[str]) -> dict:
  return {path: _stamp(path) for path in paths}


def _target_info(cpp_info, conan_name: str, target: str, alone: bool):
  """The root for a package's only target, else the target's component."""
  if alone:
    cpp_info.set_property("cmake_target_name", target)
    return cpp_info
  root = f"{conan_name}::{conan_name}"
  if target == root:
    cpp_info.set_property("cmake_target_name", f"{root}-system")
  component = cpp_info.components[_component_name(target)]
  component.set_property("cmake_target_name", target)
  return component


def _link_item(item: str) -> str:
  if item.startswith("$<LINK_ONLY:") and item.endswith(">"):
    return item[len("$<LINK_ONLY:"):-1]
  if item.startswith("$<"):
    raise ConanException(
      f"{CMAKE_NAME} links {item!r}, a generator expression the host "
      "wrapper cannot evaluate outside cmake")
  return item


def _add_link_item(info, item: str, siblings: dict) -> None:
  """One INTERFACE_LINK_LIBRARIES entry as conan's cpp_info spells it."""
  if item in _RUNTIME_LIBRARIES:
    info.system_libs.append(_RUNTIME_LIBRARIES[item])
  elif item in siblings:
    info.requires.append(siblings[item])
  elif os.path.isabs(item):
    info.libdirs.append(os.path.dirname(item))
    info.libs.append(os.path.basename(item))
  elif item.startswith("-l"):
    info.system_libs.append(item[2:])
  elif item.startswith("-"):
    info.sharedlinkflags.append(item)
    info.exelinkflags.append(item)
  elif "::" in item:
    raise ConanException(
      f"{CMAKE_NAME} links {item}, a target find_package({CMAKE_NAME}) "
      "did not import; the host wrapper cannot describe it")
  else:
    info.system_libs.append(item)


def _spelling(conan_name: str, target: str, alone: bool) -> str:
  """How a conan consumer's cpp_info.requires names one host target."""
  return f"{conan_name}::{conan_name if alone else _component_name(target)}"


def _cpp_info_from_facts(facts: dict, cpp_info, conan_name: str,
                         foreign: dict[str, str]) -> None:
  """One component per host target, at the host's absolute paths; foreign
  maps a target another wrapper declares to its <conan>::<component>."""
  targets = facts["targets"]
  alone = len(targets) == 1
  siblings = {target: _component_name(target) for target in targets}
  siblings.update(foreign)
  cpp_info.includedirs, cpp_info.libdirs, cpp_info.bindirs = [], [], []
  for target, fact in targets.items():
    info = _target_info(cpp_info, conan_name, target, alone)
    info.includedirs = list(fact["include_dirs"])
    info.libdirs, info.bindirs, info.libs = [], [], []
    info.defines = list(fact["defines"])
    if fact["location"]:
      info.libdirs.append(os.path.dirname(fact["location"]))
      info.libs.append(os.path.basename(fact["location"]))
    for item in fact["link"]:
      _add_link_item(info, _link_item(item), siblings)
  cpp_info.set_property("cmake_file_name", CMAKE_NAME)
  cpp_info.set_property("system_package_version", facts["version"])
  cpp_info.set_property(HOST_TARGETS, {
    target: _spelling(conan_name, target, alone) for target in targets})


def host_targets(dependencies) -> dict[str, str]:
  """Every host target the wrappers among dependencies declare, spelled."""
  targets = {}
  for dependency in dependencies.values():
    targets.update(dependency.cpp_info.get_property(HOST_TARGETS) or {})
  return targets


def _packages(option: str) -> dict[str, tuple[str, str]]:
  """The packages option, "CMakeName=conan[:component,...] ...", as
  {CMakeName: (conan, "component ...")}."""
  packages = {}
  for entry in option.split():
    name, _, rest = entry.partition("=")
    conan, _, components = rest.partition(":")
    packages[name] = (conan, components.replace(",", " "))
  return packages


def _cache_folder() -> Path:
  home = os.environ.get("CONAN_HOME") or str(Path.home() / ".conan2")
  return Path(home) / "buildutil-host"


def _write_atomically(path: Path, text: str) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                   delete=False) as scratch:
    scratch.write(text)
  os.replace(scratch.name, path)


def _missing_owner(ref, owner: str, imported: list[str]) -> ConanException:
  return ConanException(
    f"{ref}: find_package({CMAKE_NAME}) imports {imported} through "
    f"find_package({owner}); add Require({owner} VERSION \"<floor>\" SYSTEM) "
    "to the sources/CMakeLists.txt that requires this wrapper, so the "
    f"host's {owner} is a package of its own.")


class HostPackage(ConanFile):
  settings = "os", "arch", "compiler", "build_type"
  options = {"host_version": [None, "ANY"], "components": ["ANY"],
             "packages": ["ANY"]}
  default_options = {"host_version": None, "components": "", "packages": ""}
  # not shared-library: conan would put the host's library dirs on
  # LD_LIBRARY_PATH, where they shadow every package's own RUNPATH copies
  package_type = "unknown"
  build_policy = "missing"
  # a dependant's id follows this package's id (host version, components),
  # never the recipe revision whoever published last happened to render
  package_id_embed_mode = "full_package_mode"
  package_id_non_embed_mode = "full_package_mode"
  package_id_unknown_mode = "full_package_mode"

  def configure(self):
    if not cross_building(self):
      self.options.host_version = self._facts()["version"]

  def requirements(self):
    if cross_building(self):
      return
    packages = _packages(str(self.options.packages))
    foreign = self._facts()["foreign"]
    for owner in sorted(set(foreign.values())):
      if owner not in packages:
        raise _missing_owner(self.ref, owner, sorted(
          target for target, package in foreign.items() if package == owner))
      conan, components = packages[owner]
      self.requires(f"{conan}/system@host", options={
        "components": components, "packages": str(self.options.packages)})

  def validate(self):
    if cross_building(self):
      raise ConanInvalidConfiguration(
        f"{self.ref}: a cross build cannot take {CMAKE_NAME} from the "
        "host; the probe would describe the build machine.")

  def package_id(self):
    self.info.settings.rm_safe("compiler")
    self.info.settings.rm_safe("build_type")
    self.info.options.rm_safe("packages")

  def package(self):
    pass

  def package_info(self):
    spellings = host_targets(self.dependencies.direct_host)
    _cpp_info_from_facts(self._facts(), self.cpp_info, self.name, {
      target: spellings[target] for target in self._facts()["foreign"]})

  def _facts(self) -> dict:
    """The probe's facts, probed again only when what it read changed."""
    if not hasattr(self, "_probed"):
      self._probed = self._cached_facts()
    return self._probed

  def _cache_key(self) -> str:
    key = json.dumps([
      Path(__file__).read_text(encoding="utf-8"), CMAKE_NAME,
      str(self.options.components),
      *(self.settings.get_safe(name) for name in ("os", "arch", "build_type")),
      *(os.environ.get(name, "") for name in (
        "CMAKE_PREFIX_PATH", f"{CMAKE_NAME}_ROOT",
        f"{CMAKE_NAME.upper()}_ROOT", "PATH", "PKG_CONFIG_PATH"))])
    return hashlib.sha256(key.encode()).hexdigest()[:16]

  def _cached_facts(self) -> dict:
    cache = _cache_folder() / f"{self.name}-{self._cache_key()}.json"
    if cache.is_file():
      cached = json.loads(cache.read_text(encoding="utf-8"))
      if (cached["files"] == _fingerprint(sorted(cached["files"]))
          and cached["config_dirs"] == _config_dirs()):
        return cached["facts"]
    facts, files = self._probe()
    _write_atomically(cache, json.dumps(
      {"facts": facts, "files": _fingerprint(files),
       "config_dirs": _config_dirs()}, indent=1))
    return facts

  def _probe(self) -> tuple[dict, list[str]]:
    """find_package the host's package in a project outside any toolchain."""
    with tempfile.TemporaryDirectory(prefix="buildutil-host-") as scratch:
      folder = Path(scratch)
      (folder / "CMakeLists.txt").write_text(_PROBE_LISTS, encoding="utf-8")
      out, trace = folder / "facts.json", folder / "trace.json"
      run = subprocess.run(self._probe_command(folder, out, trace),
                           capture_output=True, text=True,
                           env={key: value for key, value in os.environ.items()
                                if key != "CMAKE_TOOLCHAIN_FILE"})
      if run.returncode != 0:
        raise ConanException(
          f"{self.ref}: the host has no usable {CMAKE_NAME}; "
          f"find_package({CMAKE_NAME}) said:\n"
          + (run.stdout + run.stderr)[-2000:])
      facts = _facts_from_probe(json.loads(out.read_text(encoding="utf-8")),
                                CMAKE_NAME)
      events = _trace_events(trace.read_text(encoding="utf-8"))
      read = _read_files(events, str(folder))
    pc_files = _pkg_config_files(_pkg_config_modules(events))
    locations = [target["location"] for target in facts["targets"].values()]
    return facts, sorted({*read, *pc_files, *filter(None, locations)})

  def _probe_command(self, folder: Path, out: Path,
                     trace: Path) -> list[str]:
    cmake = (self.conf.get("tools.cmake:cmake_program")
             or shutil.which("cmake") or "cmake")
    generator = ["-G", "Ninja"] if shutil.which("ninja") else []
    components = str(self.options.components).split()
    wanted = ";".join(["COMPONENTS", *components]) if components else ""
    return [cmake, "-S", str(folder), "-B", str(folder / "build"), *generator,
            "--trace-expand", "--trace-format=json-v1",
            f"--trace-redirect={trace}",
            f"-DPROBE_NAME={CMAKE_NAME}",
            f"-DPROBE_COMPONENTS={wanted}",
            f"-DPROBE_BUILD_TYPE={str(self.settings.build_type).upper()}",
            f"-DPROBE_OUT={out.as_posix()}"]
