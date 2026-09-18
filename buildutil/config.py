"""Shared paths + constants for the buildutil package.

Stdlib-only on purpose: bootstrap.py imports this on the host interpreter,
before the venv (and any third-party dep) exists.

The project seam (extracted from bossdeux): buildutil is generic, the
project it drives is not. Everything project-specific lives in ONE file —
`buildutil.toml` at the repo root — and the repo root IS "the nearest
ancestor holding buildutil.toml". No key in that file is required; an
empty file marks the root and takes every default.

    [project]
    name = "bossdeux"               # vscode task labels, messages
    cmake_option_prefix = "BOSSDEUX"  # -D<PREFIX>_MAX_ERRORS etc.
    module_define_prefix = "BDX"    # <PREFIX>_<NAME>_ENABLED=1 defines

    [options]
    contracts = true           # project options: the value is the DEFAULT,
    max_depth = 32             # `--option name=value` chooses another, and
    greeting = "hello"         # buildutil injects <PREFIX>_<NAME> as a
                               # compile definition on every target

    [venv]
    extra_deps = ["pybind11==3.0.4", "capstone==5.0.7"]

    [coverage]
    bridge_dirs = ["sources/bdx86emu"]  # pybind bridges: objects count,
                                        # .gcda only appears via pytest
    exclude = ["sources/image/contrib/"]  # gcovr --exclude regexes for
                                          # vendored code

    [analyze]
    exclude = ["image/contrib"]  # path substrings clang-tidy skips

    [conan]
    # extra lines for every generated conan profile: `options` go in
    # [options] (per-OS variants win over the plain key on that OS),
    # `conf` lines in [conf] — for dep option policy and per-package
    # workarounds that are the project's, not the driver's
    options_linux = ["sdl/*:x11=False"]
    conf = ["somepkg/*:tools.build:cxxflags+=['-Wno-error']"]

    [test]
    python = ["tools"]         # directories that are NOT modules and hold
                               # a python suite: one ctest entry each,
                               # <dir>-pytest, exactly what a module's
                               # *.test.py gets

    [bench]
    suite = "inspector.bench"  # python bench suite (run as `python -m
                               # <suite>` with tools/ on PYTHONPATH);
                               # unset = run the *-benches executables

    [run]
    default = "bdxmcp"           # `buildutil run` launches this binary
    default_windows = "bdxgui"   # per-OS override (also _linux, _macos);
    default_macos = "bdxgui"     # nothing configured = --target required

    [modules.<name>]         # per-module facts, all optional:
    objc_arc = false         # compile this module's Objective-C/C++
                             # WITHOUT ARC (the default is with).
    platforms = ["posix"]   # targets this module builds on; dormant elsewhere
    frameworks = ["Cocoa"]   # Apple system frameworks the module links
                             # (macOS only; ignored elsewhere).

    [modules]
    dormant = ["as", "re2c"]  # modules that do NOT build by default --
                             # a PROJECT fact (not ported yet, needs a
                             # toolchain nobody has). A local
                             # _bdudata/modules.ini still overrides, in
                             # both directions.

    [reflect]                # only read when "reflect" is a declared
    namespace = "reflect"    # cmake extension. The namespace is baked into
    include = "detour"       # user source, so changing it later breaks
    scan = "direct"          # consumers.
    annotation = "macro"     #
    macros = "auto"          # annotation: what the shipped _Label/_Meta
                             # macros EXPAND to. "macro" (the default) is
                             # nothing at all -- the generator reads them
                             # out of the raw token stream, exactly as it
                             # always has. "attribute" expands them to real
                             # [[buildutil::label(...)]] attributes, and
                             # buildutil then adds the per-compiler
                             # suppression flags to this project's compiles.
                             # Opt in only once every site uses a position
                             # an attribute is legal in: `_Label(x) SYSTEM`
                             # becomes a LEADING enumerator attribute, which
                             # is ill-formed, while `SYSTEM _Label(x)` is
                             # read the same by both.
                             # macros: where the macro spellings come from.
                             # "auto" writes _buildutil/reflect-macros.hpp
                             # and the support header includes it; "none"
                             # writes neither, leaving the names free for a
                             # project that already means something else by
                             # them; a PATH names the project's own macro
                             # file, whose spellings the generator reads
                             # back out of it.
                             #
                             # include: how a tagged header's schemes reach
                             # a TU. "detour" (the default) generates a
                             # wrapper header that the include path puts in
                             # front of the real one, so the schemes travel
                             # WITH the class and a transitively-reached
                             # type cannot be missing them.
                             # "source"|"module" are the DEPRECATED
                             # force-include modes, kept one release as an
                             # escape hatch: they push the reflect header
                             # into each .cpp / each target instead, which
                             # is what let a transitively-reached scheme go
                             # silently absent.
                             # scan: direct|preprocess, how a .cpp is
                             # matched to the reflect headers it needs.
                             # Read only by the force-include modes.

    [resources]              # files compiled INTO the binary, reachable
    dir = "resources"        # through one generated accessor -- see the
    files = ["*.html", "*.js"]  # "Resources" section of the README.
    module = "xoctet"    # which module carries them (default: the
                             # [project] name); `namespace` overrides the
                             # generated <ns>::resources, `prefix` is
                             # prepended to every name, and `mime` adds
                             # content types for exotic extensions.
                             # [[resources]] (double brackets) declares
                             # several independent sets.

    [runtime]                # dependencies that ship files which must sit
    from = ["cef"]           # NEXT TO the executable at run time -- copied
    targets = ["xoctet"] # into the build tree and installed flat.
    dirs = ["contrib/blobs"] # `targets` limits it to named apps (default:
                             # every app); `dirs` ships repo-relative
                             # directories with no package behind them.

    [bundle.macos]           # the module's executable ships as an .app
    identifier = "com.example.app"    # bundle instead of a bare binary
    version = "0.1.0"
    plist = { LSUIElement = true, LSMinimumSystemVersion = "12.0" }
    [bundle.macos.helpers]   # sub-process bundles in Contents/Frameworks,
    module = "xoctet-helper"          # all running ONE executable
    variants = ["", "Alerts", "GPU", "Plugin", "Renderer"]

    [cmake]
    extensions = ["watcom"]  # opt-in machinery beyond the base;
                             # rendered into _bdudata/cmake at build time
    export_module_headers = true  # PORTED TREES ONLY: put every module's
                             # own dir on its consumers' include path, so
                             # cross-module #includes resolve unqualified.
                             # Off by default; see Init_submodule.
"""
from __future__ import annotations

import os
import platform
import sys
from pathlib import Path, PurePath


def _discover_root() -> Path | None:
  """BUILDUTIL_ROOT wins; else walk up from cwd to the nearest
  buildutil.toml. None means 'not inside a project' — commands that
  need a repo refuse with a clear message instead of guessing (the
  pre-extraction driver hardcoded parents[2] of its own file, which
  stopped meaning anything the day the package left the repo)."""
  env = os.environ.get("BUILDUTIL_ROOT")
  if env:
    return Path(env).resolve()
  d = Path.cwd().resolve()
  for candidate in (d, *d.parents):
    if (candidate / "buildutil.toml").is_file():
      return candidate
  return None


_root = _discover_root()
HAVE_PROJECT = _root is not None
REPO_ROOT = _root if _root is not None else Path.cwd().resolve()


def require_project() -> Path:
  """The repo root, or a refusal every command shares."""
  if not HAVE_PROJECT:
    sys.exit("buildutil: no buildutil.toml found in this or any parent "
             "directory (set BUILDUTIL_ROOT to override)")
  return REPO_ROOT


# Keys a project may still be carrying that no longer mean anything. Refused
# rather than ignored: the modes they configured warned for a release that
# they were going away, and a clone that silently built the other way is the
# failure [reflect] is committed to the repo to prevent.
REMOVED_REFLECT_KEYS = {
  "include": "the detour is the only delivery; the force-include modes "
             "could not deliver a scheme reached transitively. "
             "Drop the key.",
  "scan": "it only chose how the force-include modes mapped a .cpp to its "
          "headers, and the detour needs no mapping. Drop the key.",
}


def _load_project() -> dict:
  """buildutil.toml, flattened over the defaults. tomllib is the price
  of the seam: stdlib since 3.11, and the guard here beats a stack
  trace on the 3.10 shell runner someone will eventually try."""
  cfg = {
    "name": "project",
    "cmake_option_prefix": "BUILDUTIL",
    "module_define_prefix": "MOD",
    "options": {},               # [options]: name -> default value
    "venv_extra_deps": [],
    "coverage_bridge_dirs": [],
    "cmake_extensions": [],
    "export_module_headers": False,
    "modules_dormant": [],
    "modules_platforms": {},
    "modules_no_arc": [],        # [modules.<name>] objc_arc = false
    "modules_frameworks": {},    # [modules.<name>] frameworks = [...]
    "test_python_suites": [],    # [test] python: non-module suite dirs
    "bench_suite": "",
    "run_default": "",
    "run_default_os": {},
    "coverage_exclude": [],
    "analyze_exclude": [],
    "conan_options": [],
    "conan_options_os": {},
    "conan_conf": [],
    "buildutil_version": "",     # [buildutil] version: pin for `update`
    "update_source": "",         # [update] source: index or git URL
    "package_kind": "",          # [package] kind: "library"|"application"
    "package_name": "",          # [package] name (conan package name)
    "resources": [],             # [resources] / [[resources]] sets
    "runtime_from": [],          # [runtime] from: deps with a payload
    "runtime_targets": [],       # [runtime] targets: which apps
    "runtime_dirs": [],          # [runtime] dirs: repo-relative payload
    "bundle_macos": {},          # [bundle.macos]
    "reflect_namespace": "reflect",  # [reflect] namespace
    "reflect_annotation": "macro",   # [reflect] annotation: macro|attribute
    "reflect_macros": "auto",        # [reflect] macros: auto|none|<path>
  }
  path = REPO_ROOT / "buildutil.toml"
  if not (HAVE_PROJECT and path.is_file()):
    return cfg
  if sys.version_info < (3, 11):
    sys.exit("buildutil needs python >= 3.11 (tomllib); this is "
             + platform.python_version())
  import tomllib
  with open(path, "rb") as f:
    raw = tomllib.load(f)
  proj = raw.get("project", {})
  for key in ("name", "cmake_option_prefix", "module_define_prefix"):
    if key in proj:
      cfg[key] = proj[key]
  cfg["options"] = _project_options(raw)
  cfg["venv_extra_deps"] = list(raw.get("venv", {}).get("extra_deps", []))
  cfg["coverage_bridge_dirs"] = list(
    raw.get("coverage", {}).get("bridge_dirs", []))
  cfg["cmake_extensions"] = list(
    raw.get("cmake", {}).get("extensions", []))
  cfg["export_module_headers"] = bool(
    raw.get("cmake", {}).get("export_module_headers", False))
  # [reflect] is project POLICY, not a local preference: a fresh clone that
  # quietly built something else is the failure this table prevents.
  reflect = raw.get("reflect", {})
  for gone, why in REMOVED_REFLECT_KEYS.items():
    if gone in reflect:
      sys.exit("buildutil.toml: [reflect] {} was removed -- {}".format(
        gone, why))
  cfg["reflect_namespace"] = str(reflect.get("namespace", "reflect"))
  cfg["reflect_annotation"] = str(reflect.get("annotation", "macro"))
  cfg["reflect_macros"] = str(reflect.get("macros", "auto"))
  cfg["modules_dormant"] = list(raw.get("modules", {}).get("dormant", []))
  cfg["modules_platforms"] = _module_platforms(raw)
  cfg["modules_no_arc"] = _modules_without_arc(raw)
  cfg["modules_frameworks"] = _module_frameworks(raw)
  cfg["test_python_suites"] = _python_suites(raw)
  cfg["bench_suite"] = str(raw.get("bench", {}).get("suite", ""))
  run = raw.get("run", {})
  cfg["run_default"] = str(run.get("default", ""))
  cfg["run_default_os"] = {
    key: str(run[f"default_{key}"])
    for key in ("linux", "windows", "macos") if f"default_{key}" in run}
  cfg["coverage_exclude"] = list(
    raw.get("coverage", {}).get("exclude", []))
  cfg["analyze_exclude"] = list(
    raw.get("analyze", {}).get("exclude", []))
  conan = raw.get("conan", {})
  cfg["conan_options"] = list(conan.get("options", []))
  cfg["conan_options_os"] = {
    key: list(conan[f"options_{key}"])
    for key in ("linux", "windows", "macos", "emscripten")
    if f"options_{key}" in conan}
  cfg["conan_conf"] = list(conan.get("conf", []))
  # [buildutil] version = "X.Y.Z" pins what `buildutil update` installs
  # inside this project; "latest" (or absent) means newest available
  cfg["buildutil_version"] = str(raw.get("buildutil", {}).get("version", ""))
  cfg["update_source"] = str(raw.get("update", {}).get("source", ""))
  # [package]: this project ships as a conan package. COMMITTED on
  # purpose — what a project ships as is a fact about the project, and
  # the conanfile reads the same section, so a fresh clone packages
  # identically. Absent = consumer-only recipe, exactly as before.
  # Deliberately NO version key: the version never lives in the package
  # source (owner ruling) — it is derived from git tags at publish
  # time, or given with `publish --version`.
  pkg = raw.get("package", {})
  cfg["package_kind"] = str(pkg.get("kind", ""))
  cfg["package_name"] = str(pkg.get("name", ""))
  cfg["resources"] = _resource_sets(raw, cfg["name"])
  runtime = raw.get("runtime", {})
  if not isinstance(runtime, dict):
    sys.exit("buildutil.toml: [runtime] must be a table")
  unknown = sorted(set(runtime) - {"from", "targets", "dirs"})
  if unknown:
    sys.exit(f"buildutil.toml: [runtime] has unknown key(s) "
             f"{', '.join(unknown)} (known: dirs, from, targets)")
  cfg["runtime_from"] = [str(one) for one in runtime.get("from", [])]
  cfg["runtime_targets"] = [str(one) for one in runtime.get("targets", [])]
  cfg["runtime_dirs"] = [str(one) for one in runtime.get("dirs", [])]
  for directory in cfg["runtime_dirs"]:
    if directory.startswith("/") or ".." in Path(directory).parts:
      sys.exit(f"buildutil.toml: [runtime] dirs entry {directory!r} must be "
               "a path inside the repo, relative to its root")
  cfg["bundle_macos"] = _bundle_macos(raw, cfg["name"])
  return cfg


def _is_option_name(name: str) -> bool:
  """Lower snake case, starting with a letter: `contracts`, `max_depth`."""
  return (name.isascii() and name.isidentifier() and name.islower()
          and name[0].isalpha())


def _project_options(raw: dict) -> dict:
  """[options] name = default -- what a build may choose and buildutil
  injects as <PREFIX>_<NAME>. Validated here so a typo names itself at
  `buildutil build` rather than as a missing macro three layers down."""
  from . import options            # stdlib-only, like everything here
  declared = raw.get("options", {})
  if not isinstance(declared, dict):
    sys.exit("buildutil.toml: [options] must be a table of "
             "name = default (contracts = true)")
  out = {}
  for name, value in declared.items():
    if not _is_option_name(name):
      sys.exit(f"buildutil.toml: [options] key {name!r} is not an option "
               "name (lower snake case, starting with a letter: "
               "contracts, max_depth)")
    kind = options.kind_of(value)
    if kind is None:
      sys.exit(f"buildutil.toml: [options] {name} must default to a "
               "boolean, an integer or a string, not a "
               f"{type(value).__name__}")
    if kind == "string" and options.refused_characters(value):
      sys.exit(f"buildutil.toml: [options] {name} may not hold any of "
               f"{options.TEXT_REFUSED.strip()} -- the value travels "
               "through a cmake string on its way to the compiler")
    out[name] = value
  return out


_BUNDLE_KEYS = {"module", "identifier", "name", "version", "plist", "helpers"}
_HELPER_KEYS = {"module", "suffix", "variants"}


def _plist_pairs(plist: dict, where: str) -> list[str]:
  """[bundle.macos] plist as `Key=type:value` tokens. The toml types have
  to survive the trip through a rendered cmake call and out the other
  side into plistlib, where a bool is <true/> and a string is <string> --
  a text template could not tell them apart, which is why the plist is
  generated by python rather than configure_file'd from a file the
  project would have to carry."""
  pairs = []
  for key, value in plist.items():
    if isinstance(value, bool):
      pairs.append(f"{key}=bool:{'true' if value else 'false'}")
    elif isinstance(value, int):
      pairs.append(f"{key}=int:{value}")
    elif isinstance(value, str):
      pairs.append(f"{key}=string:{value}")
    elif isinstance(value, list) and all(isinstance(v, str) for v in value):
      pairs.append(f"{key}=array:" + "\x1f".join(value))
    else:
      sys.exit(f"buildutil.toml: {where} plist key {key!r} is a "
               f"{type(value).__name__}; Info.plist values buildutil writes "
               "are strings, booleans, integers or lists of strings")
    if "," in pairs[-1] or "|" in pairs[-1] or '"' in pairs[-1]:
      sys.exit(f"buildutil.toml: {where} plist key {key!r} value may not "
               "contain ',', '|' or '\"'")
  return pairs


def _bundle_macos(raw: dict, project_name: str) -> dict:
  """[bundle.macos]: the module's executable ships as an .app instead of
  a bare binary. Everything a bundle needs that is NOT the project's
  business -- the plist boilerplate, the Frameworks layout, the helper
  naming rule, the install destination cmake refuses to guess for a
  bundle target -- is buildutil's; the identifier, the version and any
  extra plist keys are the project's, and they are all that is here."""
  declared = raw.get("bundle", {}).get("macos")
  if declared is None:
    return {}
  if not isinstance(declared, dict):
    sys.exit("buildutil.toml: [bundle.macos] must be a table")
  unknown = sorted(set(declared) - _BUNDLE_KEYS)
  if unknown:
    sys.exit(f"buildutil.toml: [bundle.macos] has unknown key(s) "
             f"{', '.join(unknown)} (known: {', '.join(sorted(_BUNDLE_KEYS))})")
  module = str(declared.get("module", "") or project_name)
  identifier = str(declared.get("identifier", "")).strip()
  if not identifier:
    sys.exit("buildutil.toml: [bundle.macos] needs `identifier` -- macOS "
             "keys the bundle on it, and two apps sharing one is a class "
             "of bug nobody enjoys")
  name = str(declared.get("name", "") or module)
  version = str(declared.get("version", "") or "0.0.0")
  plist = declared.get("plist", {}) or {}
  if not isinstance(plist, dict):
    sys.exit("buildutil.toml: [bundle.macos] plist must be a table")
  helpers = declared.get("helpers", {}) or {}
  if not isinstance(helpers, dict):
    sys.exit("buildutil.toml: [bundle.macos.helpers] must be a table")
  unknown = sorted(set(helpers) - _HELPER_KEYS)
  if unknown:
    sys.exit(f"buildutil.toml: [bundle.macos.helpers] has unknown key(s) "
             f"{', '.join(unknown)} (known: {', '.join(sorted(_HELPER_KEYS))})")
  helper_module = str(helpers.get("module", ""))
  suffix = str(helpers.get("suffix", " Helper"))
  variants = [str(one) for one in helpers.get("variants", [])]
  if variants and not helper_module:
    sys.exit("buildutil.toml: [bundle.macos.helpers] lists variants but no "
             "`module` -- every helper bundle runs one named module's "
             "executable")
  # The helper bundle names and identifiers, computed HERE rather than in
  # cmake: an empty variant (the unsuffixed helper) cannot survive a
  # cmake list, and the naming rule is a rule, not a loop.
  bundles = []
  for variant in variants:
    label = f"{name}{suffix}" + (f" ({variant})" if variant else "")
    ident = f"{identifier}.helper" + (f".{variant.lower()}" if variant else "")
    if any(bad in label + ident for bad in (",", "|", '"')):
      sys.exit(f"buildutil.toml: [bundle.macos.helpers] variant "
               f"{variant!r} produces {label!r}, which may not contain "
               "',', '|' or '\"'")
    bundles.append(f"{label}|{ident}")
  return {"module": module, "identifier": identifier, "name": name,
          "version": version, "plist": _plist_pairs(plist, "[bundle.macos]"),
          "helper_module": helper_module, "helpers": bundles}


_MODULE_KEYS = {"objc_arc", "frameworks", "platforms"}


def _module_tables(raw: dict):
  """The [modules.<name>] sub-tables, validated once. `[modules]` also
  carries the scalar `dormant` list, which is skipped rather than
  refused -- the two live in one TOML section on purpose."""
  modules = raw.get("modules", {})
  if not isinstance(modules, dict):
    sys.exit("buildutil.toml: [modules] must be a table")
  for name, entry in modules.items():
    if not isinstance(entry, dict):
      continue
    unknown = sorted(set(entry) - _MODULE_KEYS)
    if unknown:
      sys.exit(f"buildutil.toml: [modules.{name}] has unknown key(s) "
               f"{', '.join(unknown)} (known: "
               f"{', '.join(sorted(_MODULE_KEYS))})")
    yield name, entry


def _module_platforms(raw: dict) -> dict:
  """The target tags a module accepts; an empty list accepts no target.
  Windows also accepts its OS spelling beside the existing win32 tag."""
  from .naming import PLATFORM_TAGS
  known = (*PLATFORM_TAGS, "windows")
  out = {}
  for name, entry in _module_tables(raw):
    if "platforms" not in entry:
      continue
    names = entry["platforms"]
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
      sys.exit(f"buildutil.toml: [modules.{name}] platforms must be a list of names")
    for tag in names:
      if tag not in known:
        sys.exit(f"buildutil.toml: [modules.{name}] unknown platform {tag!r} "
                 f"(known: {', '.join(sorted(known))})")
    out[name] = ["win32" if tag == "windows" else tag for tag in names]
  return out


def _module_frameworks(raw: dict) -> dict:
  """[modules.<name>] frameworks = ["Cocoa", "Foundation"] -- the Apple
  system frameworks a module links. An Objective-C module almost always
  needs at least one, and the alternative is a project writing
  `target_link_libraries(... "-framework Cocoa")` by hand, which is the
  cmake logic these declarations exist to replace. Inert off macOS, so a
  cross-platform module declares it once and every other platform
  ignores it."""
  out = {}
  for name, entry in _module_tables(raw):
    names = entry.get("frameworks")
    if names is None:
      continue
    if isinstance(names, str):
      names = [names]
    names = [str(one) for one in names]
    for one in names:
      if not one or not one.replace("_", "").isalnum():
        sys.exit(f"buildutil.toml: [modules.{name}] frameworks entry "
                 f"{one!r} is not a framework name (letters, digits and "
                 "underscores -- 'Cocoa', 'CoreFoundation')")
    if names:
      out[name] = names
  return out


def _modules_without_arc(raw: dict) -> list[str]:
  """[modules.<name>] objc_arc = false -- the modules whose Objective-C
  and Objective-C++ sources are compiled WITHOUT automatic reference
  counting. ARC is the default everywhere else, so this list is normally
  empty and a project never mentions the subject.

  `[modules]` already carries `dormant`, a list; a per-module sub-table
  sits beside it in the same TOML section without colliding, which is
  why the shape is [modules.<name>] rather than a second top-level
  table nobody would connect to the first."""
  out = []
  for name, entry in _module_tables(raw):
    if "objc_arc" in entry:
      if not isinstance(entry["objc_arc"], bool):
        sys.exit(f"buildutil.toml: [modules.{name}] objc_arc must be true "
                 "or false")
      if not entry["objc_arc"]:
        out.append(name)
  return out


_TEST_KEYS = {"python"}


def _python_suites(raw: dict) -> list[str]:
  """[test] python: suite directories that are not modules."""
  test = raw.get("test", {})
  if not isinstance(test, dict):
    sys.exit("buildutil.toml: [test] must be a table")
  unknown = sorted(set(test) - _TEST_KEYS)
  if unknown:
    sys.exit(f"buildutil.toml: [test] has unknown key(s) "
             f"{', '.join(unknown)} (known: {', '.join(sorted(_TEST_KEYS))})")
  suites = [str(one) for one in test.get("python", [])]
  for suite in suites:
    if suite.startswith("/") or ".." in Path(suite).parts:
      sys.exit(f"buildutil.toml: [test] python entry {suite!r} must be a "
               "directory inside the repo, relative to its root")
  return suites


def _identifier(text: str) -> str:
  """A project name as a C++ namespace component: everything an
  identifier cannot hold folded to '_'. `bossdeux-main` is a fine project
  name and `namespace bossdeux-main` is not a namespace at all."""
  out = "".join(char if (char.isalnum() or char == "_") else "_"
                for char in text)
  return out if (out and not out[0].isdigit()) else f"_{out}"


_RESOURCE_KEYS = {"dir", "files", "module", "namespace", "prefix", "mime"}


def _resource_sets(raw: dict, project_name: str) -> list[dict]:
  """[resources] (one set) or [[resources]] (several), validated here so
  the rendered cmake never has to argue about shape and a typo names
  itself at `buildutil build` rather than three layers down.

  Only the DECLARATION lives in the toml: which directory, which files,
  which module carries them. Everything else -- how the bytes get into
  the binary, which back end the compiler can take, how the accessor
  looks -- is buildutil's, and no project writes a line of it."""
  declared = raw.get("resources")
  if declared is None:
    return []
  if isinstance(declared, dict):
    declared = [declared]
  if not isinstance(declared, list):
    sys.exit("buildutil.toml: [resources] must be a table, or [[resources]] "
             "repeated for several sets")
  sets = []
  for index, entry in enumerate(declared):
    where = f"[[resources]] #{index + 1}" if len(declared) > 1 else "[resources]"
    if not isinstance(entry, dict):
      sys.exit(f"buildutil.toml: {where} is not a table")
    unknown = sorted(set(entry) - _RESOURCE_KEYS)
    if unknown:
      sys.exit(f"buildutil.toml: {where} has unknown key(s) "
               f"{', '.join(unknown)} (known: "
               f"{', '.join(sorted(_RESOURCE_KEYS))})")
    directory = str(entry.get("dir", "")).strip()
    if not directory:
      sys.exit(f"buildutil.toml: {where} needs `dir` -- the directory whose "
               "files are embedded, relative to the repo root")
    if directory.startswith("/") or ".." in Path(directory).parts:
      sys.exit(f"buildutil.toml: {where} dir={directory!r} must be a path "
               "inside the repo, relative to its root")
    files = entry.get("files", [])
    if isinstance(files, str):
      files = [files]
    files = [str(one) for one in files]
    module = str(entry.get("module", "") or project_name)
    namespace = str(entry.get("namespace", "") or _identifier(project_name))
    prefix = str(entry.get("prefix", ""))
    mime = entry.get("mime", {}) or {}
    if not isinstance(mime, dict):
      sys.exit(f"buildutil.toml: {where} mime must be a table of "
               "extension = \"content/type\"")
    mime_pairs = []
    for ext, value in mime.items():
      if not str(ext).startswith("."):
        sys.exit(f"buildutil.toml: {where} mime key {ext!r} must start with "
                 "a dot (.svg, .wasm)")
      mime_pairs.append(f"{ext}={value}")
    # The rendered call carries these as `,`/`|`-joined fields, so a
    # separator inside a value would silently split it.
    for label, value in (("dir", directory), ("prefix", prefix),
                         ("namespace", namespace), ("module", module)):
      if "|" in value or '"' in value:
        sys.exit(f"buildutil.toml: {where} {label}={value!r} may not "
                 "contain '|' or '\"'")
    for glob in files:
      if "," in glob or "|" in glob or '"' in glob:
        sys.exit(f"buildutil.toml: {where} files entry {glob!r} may not "
                 "contain ',', '|' or '\"'")
    sets.append({"module": module, "dir": directory, "files": files,
                 "namespace": namespace, "prefix": prefix,
                 "mime": mime_pairs})
  return sets


def default_run_target(system: str, project: dict | None = None) -> str:
  """The `buildutil run` binary for a platform.system() value, from the
  [run] table: the per-OS override wins, then `default`, else ""
  (meaning: the caller must ask for --target). The pre-extraction
  driver hardcoded bossdeux's binaries here."""
  project = PROJECT if project is None else project
  key = {"Linux": "linux", "Windows": "windows",
         "Darwin": "macos"}.get(system, "linux")
  return project["run_default_os"].get(key) or project["run_default"]


PROJECT = _load_project()
PROJECT_NAME = PROJECT["name"]
CMAKE_PREFIX = PROJECT["cmake_option_prefix"]
MODULE_DEFINE_PREFIX = PROJECT["module_define_prefix"]

# Python venv layout differs by platform: POSIX puts binaries under bin/,
# Windows under Scripts/, and Windows binaries get the .exe suffix. Drive
# both off the same VENV_BIN_DIR so the rest of the code stays neutral.
# BUILDUTIL_VENV_DIR: a cross-compile container (msvc-wine, for one)
# mounts the repo but must keep its venv OFF the mount -- the host's
# _pyvenv shebangs point at paths that do not exist inside, and rebuilding
# in place would break the host side right back
VENV_DIR = Path(os.environ.get("BUILDUTIL_VENV_DIR",
                               REPO_ROOT / "_pyvenv"))
if platform.system() == "Windows":
  VENV_BIN_DIR = VENV_DIR / "Scripts"
  VENV_EXE_SUFFIX = ".exe"
else:
  VENV_BIN_DIR = VENV_DIR / "bin"
  VENV_EXE_SUFFIX = ""
VENV_PY = VENV_BIN_DIR / f"python{VENV_EXE_SUFFIX}"
# BUILDUTIL_SYSTEM=1 means the running interpreter IS the toolchain and no
# project venv is ever created -- so everything that points cmake or a
# subprocess at "the venv python" must point at THIS python instead.
# Discovered on oxbox CI: the reflect scan execs ${Python3_EXECUTABLE},
# which named a _pyvenv that did not exist, and `cmake -E env` reported
# only "no such file or directory".
if os.environ.get("BUILDUTIL_SYSTEM") == "1":
  VENV_PY = Path(sys.executable)
DEFAULT_CONAN_HOME = REPO_ROOT / "_conanhome"
INSTALL_PREFIX = REPO_ROOT / "_install"
BUILD_PID_FILE = REPO_ROOT / "_build" / ".build.pid"


def cmake_path(path) -> str:
  """A path as CMake wants to READ it: forward slashes, on every platform.

  Windows renders a Path with backslashes, and CMake re-emits some of the
  values we hand it as cmake CODE -- try_compile copies the inherited
  CMAKE_MODULE_PATH into its scratch project as a quoted set(), where a
  backslash starts an escape sequence and a drive letter after one is not a
  valid escape. The compiler-ABI probe then fails and NO Windows build gets
  past configure -- which is how the first run of `publish` on Windows
  ended. Every path-valued -D goes through here: the driver's configure
  and the cache-build one alike, because the baked consumer lane hits the
  identical wall. On Linux and macOS this returns
  exactly what str() did.

  A PurePath argument keeps its OWN flavour, which is what lets a test on
  Linux hand this a PureWindowsPath and see the rendering a Windows runner
  would get; a plain string is read as a path of the running platform.
  """
  return (path if isinstance(path, PurePath) else Path(path)).as_posix()

# Local, gitignored working data (the `_*` rule). The build counter and the
# module build toggles live here so they never reach history.
BDUDATA_DIR = REPO_ROOT / "_bdudata"
MODULES_INI = BDUDATA_DIR / "modules.ini"

# The driver's own deps; a project adds its own via [venv] extra_deps
# (e.g. bossdeux: pybind11 for its python bridge, capstone for a
# disassembly view).
# The reflect extension parses C++ with libclang. It lands here, in the
# PROJECT venv and only when the extension is declared, rather than in
# pyproject.toml -- the driver stays dependency-free, so a machine that only
# carries buildutil does not pull 35MB of clang for a feature it never uses.
# The package supplies both the bindings and a fallback libclang.so; a system
# clang is preferred at runtime because it matches the compiler in use and
# because the wheel ships no resource headers.
REFLECT_DEPS = ["libclang==18.1.1"]

VENV_DEPS = [
  "typer==0.24.2",
  "conan==2.28.1",
  "gcovr==7.2",
  "pytest==8.3.4",
  *(REFLECT_DEPS if "reflect" in PROJECT["cmake_extensions"] else []),
  *PROJECT["venv_extra_deps"],
]


def load_dotenv() -> None:
  """Pull KEY=VALUE pairs from <repo>/.env into os.environ for local dev
  convenience (e.g. CONAN_REMOTE_* JFrog creds). Existing env vars are NOT
  overridden, so anything the pipeline injects on a CI runner — or anything
  the user already exported — wins over what's on disk. Comments (#) and
  blank lines are ignored; quoted values are stripped of one matching pair
  of surrounding quotes."""
  path = REPO_ROOT / ".env"
  if not path.exists():
    return
  for line in path.read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
      continue
    key, _, value = line.partition("=")
    key = key.strip()
    value = value.strip()
    if (len(value) >= 2
        and value[0] == value[-1]
        and value[0] in ('"', "'")):
      value = value[1:-1]
    if key and key not in os.environ:
      os.environ[key] = value
