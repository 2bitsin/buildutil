"""Generate .vscode/{c_cpp_properties,tasks,launch}.json from the LIVE
module inventory, so they never drift -- dead targets (removed apps,
renamed modules, stale paths) can't linger because nothing is
hand-written. Regenerated on every configure and on demand via
`buildutil vscode`.

The shape (0.7.0, user spec): every submodule gets tasks for what it
actually HAS -- build/run/test in debug, relwithdebinfo and release,
bench in release only, godbolt in all three flavors (0.9.0; also
whole-tree) -- and every executable target (app, tests, benches) gets
a launch config for debug and relwithdebinfo pointing at the
BUILD-TREE binary, pre-launched by the matching targeted build task.
Deps tasks pre-install the conan graph per configuration.
c_cpp_properties is UPSERTED, not rewritten: each build refreshes the
just-built profile's entry and moves it to the top (vscode's default),
pruning missing build databases while keeping hand-added entries.

These three files are GITIGNORED (regenerated); .vscode/settings.json
is hand-maintained and stays tracked.
"""
from __future__ import annotations

import json
import platform
import shlex
from pathlib import Path

from .config import (PROJECT_NAME, REPO_ROOT, VENV_PY, cmake_cache_entry,
                     cmake_path)

# ===========================================================================
# CONFIG -- what to generate and its general shape. Edit here, not the JSON.
# ===========================================================================

# buildutil flags every build/test/bench task carries. --i-am-willingly-circumventing-build-and-test-time-safeguards:
# the watchdog exists so an AGENT invoking buildutil can't hang forever
# on a wedged build; vscode tasks are driven by a human watching the
# terminal, who interrupts long-running work themselves (owner ruling).
COMMON_FLAGS = ["--i-am-willingly-circumventing-build-and-test-time-safeguards", "--max-errors=3", "--jump-to-error=1"]
JOBS: int | None = None                    # None = all cores; int caps -j

# flavor label -> the --release/--debug spelling that build/test/run all
# resolve identically (both flags = RelWithDebInfo).
FLAVORS = {
  "debug":          ["--debug"],
  "release":        ["--release"],
  "relwithdebinfo": ["--relwithdebinfo"],
}
# launch configs only for flavors that carry debug info
LAUNCH_FLAVORS = ("debug", "relwithdebinfo")

PROBLEM_MATCHER = ["$gcc"]
DEBUG_TYPE = "cppdbg"
GDB_SETUP = [{"description": "Enable pretty-printing for gdb",
              "text": "-enable-pretty-printing", "ignoreFailures": True}]


def _debugger() -> dict:
  """cppdbg drives lldb on macOS and gdb elsewhere; the files are per machine."""
  if platform.system() == "Darwin":
    return {"MIMode": "lldb"}
  return {"MIMode": "gdb", "miDebuggerPath": "gdb", "setupCommands": GDB_SETUP}

# ===========================================================================
# remembered prompt values
# ===========================================================================
# vscode's promptString cannot persist what the user typed (upstream
# vscode#26076), so buildutil remembers FOR it: run/test/bench record
# the value they were handed (run --args, test -f, bench --filter)
# here, and every regeneration bakes it back in as the prompt's
# default and as the launch configs' argv. Prompts allow empty; an
# empty answer is remembered too (= cleared).

_INPUTS_STORE = REPO_ROOT/"_bdudata"/"vscode-inputs.json"

def record_input(kind: str, key: str, value: str) -> None:
  """Remember a prompt value ('args'/'filter'/'benchfilter' per
  module) for the next generation."""
  try:
    data = json.loads(_INPUTS_STORE.read_text())
  except (OSError, ValueError):
    data = {}
  if data.get(kind, {}).get(key) == value:
    return
  data.setdefault(kind, {})[key] = value
  _INPUTS_STORE.parent.mkdir(parents=True, exist_ok=True)
  _INPUTS_STORE.write_text(json.dumps(data, indent=1) + "\n")

def recorded(kind: str, key: str) -> str:
  try:
    return json.loads(_INPUTS_STORE.read_text()).get(kind, {}).get(key, "")
  except (OSError, ValueError):
    return ""

# ===========================================================================
# discovery
# ===========================================================================

SOURCES = REPO_ROOT/"sources"

def _dormant() -> set:
  """Module names under [disabled] in _bdudata/modules.ini (gitignored)."""
  config = REPO_ROOT/"_bdudata"/"modules.ini"
  dormant, section = set(), ""
  if config.exists():
    for line in config.read_text().splitlines():
      line = line.strip()
      if not line or line[0] in "#;":
        continue
      if line.startswith("[") and line.endswith("]"):
        section = line[1:-1].lower()
      elif section == "disabled":
        dormant.add(line.split()[0])
  return dormant

def _module_name(module_dir: Path) -> str:
  # MUST match buildutil.cmake's target naming: '-' join with kind tags
  # stripped from every component (sources/a/b.exe -> a-b). This used
  # '_' before 0.7.0 and raw components before 0.42.1 — both times every
  # affected module's tasks named a target that didn't exist.
  from . import naming
  return naming.module_name(module_dir.relative_to(SOURCES).parts)

def discover() -> dict:
  """{ module_name: {rel, tests, benches, app, bundle, helper} } per live module
  (a dir with CMakeLists.txt; dot-dirs and dormant excluded)."""
  from .config import PROJECT
  bundle = PROJECT.get("bundle_macos") or {}
  bundle_module = bundle.get("module")
  helper_module = bundle.get("helper_module")
  dormant = _dormant()
  modules = {}
  for cmake in sorted(SOURCES.rglob("CMakeLists.txt")):
    module_dir = cmake.parent
    parts = module_dir.relative_to(SOURCES).parts
    if not parts or any(part.startswith(".") for part in parts):
      continue                             # skip the sources/ aggregator root
    try:
      name = _module_name(module_dir)
    except SystemExit:
      continue    # contradictory kind tags: cmake refuses the whole tree
    if name in dormant:
      continue
    modules[name] = {
      "rel": "/".join(parts),
      "tests": bool(next(module_dir.rglob("*.test.cpp"), None)
                    or next(module_dir.rglob("*.test/*.cpp"), None)),
      "benches": bool(next(module_dir.rglob("*.bench.cpp"), None)
                      or next(module_dir.rglob("*.bench/*.cpp"), None)),
      "app": (module_dir/"main.cpp").exists(),
      "bundle": name == bundle_module,
      "helper": name == helper_module,
    }
  return modules

def _has_pytests() -> bool:
  """Mirror of engine._run_pytests' discovery: tools/*/pytest.ini."""
  return bool(sorted((REPO_ROOT/"tools").glob("*/pytest.ini")))

def _build_dir(profile: str = "") -> Path:
  """_build/, or one profile's build tree under it."""
  return REPO_ROOT/"_build"/profile


def _profile_prefix() -> str | None:
  """'<arch>-<os>-<compiler>' of the host toolchain — the stem of every
  _build/<profile>/ this machine produces. Detection first (works
  before any build); an existing build dir as fallback."""
  try:
    from . import engine
    s = engine._detect_settings("Debug")
    return "-".join([s["arch"], s["os"], s["compiler"]]).lower()
  except Exception:
    builds = _build_dir()
    if builds.is_dir():
      for entry in sorted(builds.iterdir()):
        parts = entry.name.split("-")
        if len(parts) >= 4 and parts[1] in _VSCODE_OS:
          return "-".join(parts[:-1])
    return None

# ===========================================================================
# tasks.json
# ===========================================================================

def _buildutil_command() -> str:
  """What tasks invoke: the repo-root `./buildutil` launcher when the
  project carries one (0.42.0 — it routes through the project venv and
  a vendored copy wins over anything installed), else the plain name on
  PATH. The launcher check is 'executable FILE': in buildutil's own
  repo the name is the package directory, and on Windows the sh
  launcher cannot run."""
  import os as _os
  import platform as _platform
  launcher = REPO_ROOT / "buildutil"
  if (_platform.system() != "Windows" and launcher.is_file()
      and _os.access(launcher, _os.X_OK)):
    return "./buildutil"
  return "buildutil"


def _prompted(option: str, input_id: str) -> dict:
  """ONE shell token for a prompt-driven option: `--option=${input:id}`,
  strongly quoted (vscode wraps it in single quotes before the shell
  sees it).

  Both halves matter, because these are `"type": "shell"` tasks. Split
  over two tokens, an empty answer -- which every prompt advertises as
  "empty = all" -- makes the value token vanish and leaves a dangling
  `--option` that eats the next argument or aborts; joined with `=`,
  an empty answer arrives as an empty string, which is exactly what the
  receiving command reads as "no filter". Unquoted, a filter like `*`
  or `Foo.*` is a live glob the shell expands against the cwd before
  buildutil ever runs, and `--args` (a shell-style string buildutil
  itself shlex-splits) would be word-split and expanded a level too
  early. Strong quoting passes the answer through verbatim."""
  return {"value": f"{option}=${{input:{input_id}}}", "quoting": "strong"}


def _buildutil_task(label: str, verb_args: list, *, log: str,
                    matcher=True) -> dict:
  from . import options
  args = list(COMMON_FLAGS)
  if JOBS:
    args.append(f"--jobs={JOBS}")
  if log:
    args += ["--clear-logs", "--write-log-to", f"_build/{log}"]
  # the project options this run chose: the editor's buttons then build
  # what the command line that regenerated them built
  args += options.with_chosen(verb_args)
  task = {
    "label": label,
    "type": "shell",
    "command": _buildutil_command(),
    "args": args,
    "group": "build",
    "presentation": {"reveal": "always", "panel": "shared"},
    "options": {"cwd": "${workspaceFolder}"},
  }
  if matcher:
    task["problemMatcher"] = PROBLEM_MATCHER
  return task

def _module_tasks(name: str, m: dict) -> list:
  """The per-module task set — only what the module actually has:
  build ×3 always; app -> run ×3 + targeted app builds (launch
  pre-tasks); tests -> test ×3 + targeted test builds; benches ->
  bench (release only) + targeted bench builds."""
  tasks = []
  for flavor, flags in FLAVORS.items():
    tasks.append(_buildutil_task(
      f"{PROJECT_NAME}: build {name} ({flavor})",
      ["build", "--target", name, *flags],
      log=f"{name}-{flavor}-build.log"))
  if m["app"]:
    for flavor, flags in FLAVORS.items():
      tasks.append(_buildutil_task(
        f"{PROJECT_NAME}: run {name} ({flavor})",
        ["run", "--target", name, *flags,
         _prompted("--args", f"args-{name}")],
        log=f"{name}-{flavor}-run.log"))
    for flavor in LAUNCH_FLAVORS:
      tasks.append(_buildutil_task(
        f"{PROJECT_NAME}: build {name} ({flavor})",
        # the executable carries the BARE module name since 0.23.0; the
        # library is what took the decoration
        ["build", "--target", name, *FLAVORS[flavor]],
        log=f"{name}-app-{flavor}-build.log"))
  if m["tests"]:
    for flavor, flags in FLAVORS.items():
      tasks.append(_buildutil_task(
        f"{PROJECT_NAME}: test {name} ({flavor})",
        ["test", name, *flags, _prompted("--filter", f"filter-{name}")],
        log=f"{name}-{flavor}-test.log"))
    for flavor in LAUNCH_FLAVORS:
      tasks.append(_buildutil_task(
        f"{PROJECT_NAME}: build {name}-tests ({flavor})",
        ["build", "--target", f"{name}-tests", *FLAVORS[flavor]],
        log=f"{name}-tests-{flavor}-build.log"))
  if m["benches"]:
    tasks.append(_buildutil_task(
      f"{PROJECT_NAME}: bench {name}",     # benches: release only
      ["bench", name, _prompted("--filter", f"benchfilter-{name}")],
      log=f"{name}-bench.log"))
    for flavor in LAUNCH_FLAVORS:
      tasks.append(_buildutil_task(
        f"{PROJECT_NAME}: build {name}-benches ({flavor})",
        ["build", "--include-bench", "--target", f"{name}-benches",
         *FLAVORS[flavor]],
        log=f"{name}-benches-{flavor}-build.log"))
  # source↔assembly report; per flavor because the asm IS the flavor's.
  # The prompt scopes to functions (regex vs demangled names; empty =
  # every function) and is remembered like the others.
  for flavor, flags in FLAVORS.items():
    tasks.append(_buildutil_task(
      f"{PROJECT_NAME}: godbolt {name} ({flavor})",
      ["godbolt", "-m", name, *flags,
       _prompted("--function", f"godboltfilter-{name}")],
      log=f"{name}-{flavor}-godbolt.log"))
  return tasks

def _tasks_document(modules: dict, has_pytests: bool) -> dict:
  any_benches = any(m["benches"] for m in modules.values())
  any_tests = any(m["tests"] for m in modules.values())
  tasks = []
  # dependency pre-install, per configuration and all at once
  tasks.append(_buildutil_task(
    f"{PROJECT_NAME}: deps (all configurations)", ["deps"],
    log="deps.log", matcher=False))
  for flavor, flag in (("debug", "--debug"), ("release", "--release"),
                       ("relwithdebinfo", "--relwithdebinfo")):
    tasks.append(_buildutil_task(
      f"{PROJECT_NAME}: deps ({flavor})", ["deps", flag],
      log=f"deps-{flavor}.log", matcher=False))
  # whole-tree verbs
  for flavor, flags in FLAVORS.items():
    tasks.append(_buildutil_task(
      f"{PROJECT_NAME}: build ({flavor})", ["build", *flags],
      log=f"{flavor}-build.log"))
    if any_tests or has_pytests:
      tasks.append(_buildutil_task(
        f"{PROJECT_NAME}: test ({flavor})", ["test", *flags],
        log=f"{flavor}-test.log"))
  if has_pytests:
    tasks.append(_buildutil_task(
      f"{PROJECT_NAME}: test (native only)", ["test", "--no-pytest"],
      log="test-native.log"))
    tasks.append(_buildutil_task(
      f"{PROJECT_NAME}: test (python only)", ["test", "--no-native"],
      log="test-python.log"))
  if any_tests:
    tasks.append(_buildutil_task(
      f"{PROJECT_NAME}: test (filter)",
      ["test", "--debug", "--no-pytest", _prompted("--filter", "testFilter")],
      log="test-filter.log"))
    tasks.append(_buildutil_task(
      f"{PROJECT_NAME}: coverage", ["coverage"], log="coverage.log"))
  if any_benches:
    tasks.append(_buildutil_task(
      f"{PROJECT_NAME}: bench", ["bench"], log="bench.log"))
  for flavor, flags in FLAVORS.items():
    tasks.append(_buildutil_task(
      f"{PROJECT_NAME}: godbolt ({flavor})", ["godbolt", *flags],
      log=f"{flavor}-godbolt.log"))
  # per-module verbs
  for name in sorted(modules):
    tasks += _module_tasks(name, modules[name])
  tasks.append({
    "label": f"{PROJECT_NAME}: refresh .vscode", "type": "shell",
    "command": _buildutil_command(), "args": ["--i-am-willingly-circumventing-build-and-test-time-safeguards", "vscode"],
    "presentation": {"reveal": "silent", "panel": "shared"},
    "options": {"cwd": "${workspaceFolder}"},
  })
  document = {"version": "2.0.0", "tasks": tasks, "inputs": []}
  if any_tests:
    document["inputs"].append({
      "id": "testFilter", "type": "promptString",
      "description": "ctest -R regex (e.g. Foo, Foo\\.Bar, ^Foo\\.Baz$; "
                     "empty = all tests)",
      "default": "",
    })
  # per-module prompts, prefilled with the last value each verb
  # recorded (empty allowed and remembered — see record_input)
  for name in sorted(modules):
    m = modules[name]
    if m["app"]:
      # recorded under the APP (leaf) name — run.py keys it there, and
      # the launch configs read it back there; a joined-name key would
      # go stale the moment a nested module's task recorded a value
      from . import naming
      app = naming.app_name(m["rel"].split("/"))
      document["inputs"].append({
        "id": f"args-{name}", "type": "promptString",
        "description": f"{name} arguments (shell-style string; empty = none)",
        "default": recorded("args", app),
      })
    if m["tests"]:
      document["inputs"].append({
        "id": f"filter-{name}", "type": "promptString",
        "description": f"{name} ctest -R regex (empty = all tests)",
        "default": recorded("filter", name),
      })
    if m["benches"]:
      document["inputs"].append({
        "id": f"benchfilter-{name}", "type": "promptString",
        "description": f"{name} --benchmark_filter regex (empty = all)",
        "default": recorded("benchfilter", name),
      })
    document["inputs"].append({
      "id": f"godboltfilter-{name}", "type": "promptString",
      "description": f"{name} godbolt function regex, matched against "
                     "demangled names (empty = every function)",
      "default": recorded("godboltfilter", name),
    })
  return document

# ===========================================================================
# launch.json
# ===========================================================================

def _module_executables(name: str, m: dict) -> list[tuple[str, str, list]]:
  """(binary name, installed path, argv) per executable; argv is the remembered
  prompt value of the matching task, which is what `buildutil run` records."""
  import shlex as _shlex
  from . import naming
  parts = m["rel"].split("/")
  mirror = naming.mirror_parent(parts)
  execs = []
  # a [bundle.macos.helpers] executable only runs as a child of the app
  if m["app"] and not m.get("helper", False):
    app = naming.app_name(parts)
    execs.append((app, _installed(mirror, app, bundle=m.get("bundle", False)),
                  _shlex.split(recorded("args", app))))
  if m["tests"]:
    flt = recorded("filter", name)
    execs.append((f"{name}-tests", _installed(mirror, f"{name}-tests"),
                  [f"--gtest_filter={flt}"] if flt else []))
  if m["benches"]:
    flt = recorded("benchfilter", name)
    execs.append((f"{name}-benches", _installed(mirror, f"{name}-benches"),
                  [f"--benchmark_filter={flt}"] if flt else []))
  return execs


def _installed(mirror: str, binary: str, *, bundle: bool = False) -> str:
  """Where cmake --install puts it, relative to _install: buildutil.cmake mirrors
  the source tree; a [bundle.macos] app is a directory with the binary inside."""
  path = f"{mirror}/{binary}" if mirror else binary
  if bundle and platform.system() == "Darwin":
    return f"{path}.app/Contents/MacOS/{binary}"
  if platform.system() == "Windows":
    return f"{path}.exe"
  return path


def _venv_python() -> str:
  """The interpreter buildutil itself uses, as vscode reads it.
  $BUILDUTIL_VENV_DIR may put the venv outside the workspace."""
  try:
    inside = VENV_PY.relative_to(REPO_ROOT)
  except ValueError:
    return cmake_path(VENV_PY)
  return f"${{workspaceFolder}}/{cmake_path(inside)}"


def _launch_document(modules: dict, prefix: str | None) -> dict:
  configurations = []
  if prefix:
    for name in sorted(modules):
      m = modules[name]
      for flavor in LAUNCH_FLAVORS:
        for bin_name, installed, argv in _module_executables(name, m):
          # a targeted build never installs; the whole-tree build of the
          # flavour is what makes _install current
          configurations.append({
            "name": f"{bin_name} ({flavor})",
            "type": DEBUG_TYPE, "request": "launch",
            "program": f"${{workspaceFolder}}/_install/{installed}",
            "args": argv, "stopAtEntry": False, "cwd": "${workspaceFolder}",
            "environment": [], **_debugger(),
            "preLaunchTask": f"{PROJECT_NAME}: build ({flavor})",
          })
  configurations.append({
    "name": "debug: current python file", "type": "debugpy", "request": "launch",
    "program": "${file}", "python": _venv_python(),
    "console": "integratedTerminal", "cwd": "${workspaceFolder}", "justMyCode": True,
  })
  return {"version": "0.2.0", "configurations": configurations, "inputs": []}

# ===========================================================================
# c_cpp_properties.json  (IntelliSense; upserted, active profile on top)
# ===========================================================================

_VSCODE_OS = {"linux", "macos", "windows"}
_VSCODE_ARCH = {"x86_64": "x64", "armv8": "arm64", "aarch64": "arm64"}
_COMPILER_FAMILIES = {"apple-clang": "clang", "msvc": "msvc",
                      "gcc": "gcc", "clang": "clang"}

def _compiler_family(driver: str) -> str:
  base = Path(driver).name.lower().removesuffix(".exe")
  if base == "cl":
    base = "msvc"
  return next((family for name, family in _COMPILER_FAMILIES.items()
               if name in base), _COMPILER_FAMILIES["gcc"])

def _cpp_standard(profile_name: str) -> dict:
  """The standard the build resolved for the project's own targets."""
  cache = _build_dir(profile_name)/"CMakeCache.txt"
  if not cache.exists():
    return {}
  standard = cmake_cache_entry(cache, "BUILDUTIL_CXX_STANDARD")
  return {"cppStandard": f"c++{standard}"} if standard else {}

def _intellisense_mode(profile_name: str, driver: str | None = None) -> str | None:
  parts = profile_name.split("-")
  if len(parts) >= 3 and parts[1] in _VSCODE_OS:
    arch, os_name = parts[:2]
    compiler = "-".join(parts[2:])
  else:
    return None
  family = _compiler_family(driver) if driver else next(
    (family for name, family in _COMPILER_FAMILIES.items()
     if compiler == name or compiler.startswith(name + "-")), None)
  if family is None:
    return None
  return f"{os_name}-{family}-{_VSCODE_ARCH.get(arch, arch)}"

def _forced_include(profile_name: str) -> dict:
  """The options header the compiler is force-fed, so IntelliSense and the
  debugger read the same macros the build does."""
  from .config import PROJECT
  if not PROJECT["options"]:
    return {}
  return {"forcedInclude": [
    f"${{workspaceFolder}}/_build/{profile_name}/generated/"
    f"{PROJECT_NAME}/options.hpp"]}


def _cpp_configuration(profile_name: str) -> dict | None:
  database = _build_dir(profile_name)/"compile_commands.json"
  if not database.exists():
    return None
  base = {
    "name": profile_name,
    "compileCommands":
      f"${{workspaceFolder}}/_build/{profile_name}/compile_commands.json",
    "cStandard": "c17",
    **_forced_include(profile_name),
  }
  entries = json.loads(database.read_text())
  if not entries:
    return None
  first = entries[0]
  args = first.get("arguments") or shlex.split(
    first.get("command", ""), posix=platform.system() != "Windows")
  if not args:
    return None
  mode = _intellisense_mode(profile_name, args[0])
  return {**base, "compilerPath": args[0], **_cpp_standard(profile_name),
          **({"intelliSenseMode": mode} if mode else {})}

def _cpp_document(prefix: str | None, active: str | None) -> dict:
  """VS Code selects the first configuration, so the active build goes first."""
  path = REPO_ROOT/".vscode"/"c_cpp_properties.json"
  try:
    existing = json.loads(path.read_text()).get("configurations", [])
  except (OSError, ValueError):
    existing = []
  by_name = {}
  build_prefix = "${workspaceFolder}/_build/"
  for config in existing:
    if not isinstance(config, dict) or "name" not in config:
      continue
    database = config.get("compileCommands", "")
    if isinstance(database, str) and database.startswith(build_prefix):
      if not (_build_dir()/database[len(build_prefix):]).exists():
        continue
    by_name[config["name"]] = config
  names = set(by_name)
  builds = _build_dir()
  if builds.is_dir():
    names |= {e.name for e in builds.iterdir()
              if e.is_dir() and (e/"compile_commands.json").exists()}
  for name in sorted(names):
    database = by_name.get(name, {}).get("compileCommands")
    if database is not None and not (
        isinstance(database, str) and database.startswith(build_prefix)):
      continue
    refreshed = _cpp_configuration(name)
    if refreshed:
      by_name[name] = refreshed
  ordered = [by_name[n] for n in sorted(by_name)]
  if active and active in by_name:
    ordered.remove(by_name[active])
    ordered.insert(0, by_name[active])
  return {"version": 4, "configurations": ordered}

# ===========================================================================
# refresh
# ===========================================================================

def _write(name: str, document) -> None:
  path = REPO_ROOT/".vscode"/name
  path.parent.mkdir(exist_ok=True)
  path.write_text(json.dumps(document, indent=2) + "\n")

def refresh(active: str | None = None) -> list:
  """Regenerate the three derived .vscode files; `active` names the
  just-configured _build/<profile> so its c_cpp entry rises to the
  top. Returns the names written."""
  modules = discover()
  prefix = _profile_prefix()
  _write("c_cpp_properties.json", _cpp_document(prefix, active))
  _write("tasks.json", _tasks_document(modules, _has_pytests()))
  _write("launch.json", _launch_document(modules, prefix))
  return ["c_cpp_properties.json", "tasks.json", "launch.json"]
