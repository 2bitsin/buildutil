"""Materialize the cmake machinery for a project (stdlib-only).

Since 0.3.0 NOTHING is committed into the project: the rendered cmake
lands in the gitignored runtime dir `_bdudata/cmake/` and the driver
points cmake at it (CMAKE_MODULE_PATH), so a project's own
`include(buildutil)` just resolves. The deposit is DERIVED DATA — it is
re-rendered on every ensure() so a package upgrade or a prefix change
in buildutil.toml takes effect on the next build, and there is nothing
to keep in sync or ever `git add`.

The deposit is cmake-only. The python halves (apply_patch,
orom_finalize, the configure-hook library) live next to their kin
inside the package and are invoked as modules by the rendered cmake —
`"${BUILDUTIL_PY}" -m buildutil.apply_patch` — with BUILDUTIL_PY /
BUILDUTIL_PYSUPPORT injected by the driver at configure time.

Extensions are declared in buildutil.toml ([cmake] extensions =
["watcom"]) — config, not filesystem state. `buildutil` is the base
every project gets; a firmware-less project never carries a watcom file.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import naming
from .config import CXX_STANDARDS

TEMPLATES = Path(__file__).resolve().parent / "templates" / "cmake"

BASE = ("buildutil.cmake", "bin2cpp.cmake", "driver_guard.cmake")

# Machine-readable provenance, first line of every rendered file. It is what
# `buildutil --version` reads back to report which buildutil a project's
# machinery was rendered by — so "is my deposit current?" is answerable
# without running a build, and a stale one names itself.
STAMP = "# buildutil-deposit-version:"
_STAMP_RE = re.compile(rf"(?m)^{re.escape(STAMP)}\s*(\S+)")

_BANNER = """\
{stamp} {version}
# ==========================================================================
#                        GENERATED FILE -- DO NOT EDIT
# ==========================================================================
# Rendered by buildutil {version} from templates/cmake/{origin}.
#
# This ENTIRE directory is derived data. Every build re-renders it from the
# installed buildutil package, so any edit made here is discarded silently
# the next time anything builds. There is nothing in here to commit and
# nothing to keep in sync.
#
#   To change the machinery ....... change the buildutil package, or upgrade
#                                   it (`buildutil update`).
#   To extend it for THIS project . drop a *.cmake file into the project's
#                                   own `cmake/` directory. It is included
#                                   automatically, it belongs to the project,
#                                   and buildutil never rewrites it.
# ==========================================================================
"""


def extensions() -> list[str]:
  return sorted(p.name for p in (TEMPLATES / "ext").iterdir() if p.is_dir())


def _banner(src: Path, version: str) -> str:
  return _BANNER.format(stamp=STAMP, version=version,
                        origin=src.relative_to(TEMPLATES).as_posix())


def _render(src: Path, cfg: dict, version: str,
            exts: list[str] = ()) -> str:
  # export_module_headers is absent from a minimal cfg (and from any
  # project that never opted in), and absent MUST mean off -- the
  # qualified-include discipline is the default everywhere.
  export = "ON" if cfg.get("export_module_headers") else "OFF"
  # a cmake list literal; empty renders to nothing, i.e. set(dormant)
  dormant = " ".join(f'"{name}"' for name in cfg.get("modules_dormant") or [])
  # [modules.<name>] objc_arc = false, as a cmake list literal
  no_arc = " ".join(f'"{name}"' for name in cfg.get("modules_no_arc") or [])
  # a LIBRARY-kind package must ship its static archives — everything
  # else keeps the pre-0.47 mirror (apps, shared libs, data, headers),
  # so no existing project's install output moves
  package_libs = "ON" if cfg.get("package_kind") == "library" else "OFF"
  # the declared extension list, as a cmake list literal, so buildutil.cmake
  # can include() them itself instead of each project remembering to
  extensions = " ".join(f'"{name}"' for name in exts or [])
  return _banner(src, version) + (
    src.read_text()
    .replace("@CMAKE_OPTION_PREFIX@", cfg["cmake_option_prefix"])
    .replace("@MODULE_DEFINE_PREFIX@", cfg["module_define_prefix"])
    .replace("@EXPORT_MODULE_HEADERS@", export)
    .replace("@PACKAGE_LIBS@", package_libs)
    .replace("@DEFAULT_DORMANT@", dormant)
    .replace("@MODULE_PLATFORMS@", "\n".join(
      f'  _buildutil_module_platforms(dormant "{name}" "{";".join(tags)}")'
      for name, tags in cfg.get("modules_platforms", {}).items()))
    .replace("@OPTIMIZE_ALWAYS@", " ".join(
      f'"{name}"' for name in cfg.get("optimize_always", [])))
    .replace("@MODULES_NO_ARC@", no_arc)
    .replace("@MODULE_FRAMEWORKS@", _module_frameworks(cfg))
    .replace("@PLATFORM_TABLE@", naming.platform_table_cmake())
    .replace("@PLATFORM_TAGS@", naming.platform_alternation())
    .replace("@KIND_TAGS@", ";".join(naming.KIND_TAGS))
    .replace("@EXTENSIONS@", extensions)
    .replace("@REFLECT_NAMESPACE@", cfg.get("reflect_namespace") or "reflect")
    .replace("@REFLECT_ANNOTATION@", cfg.get("reflect_annotation") or "macro")
    .replace("@REFLECT_MACROS@", cfg.get("reflect_macros") or "auto")
    .replace("@RESOURCE_NAMESPACE@", _resource_namespace(cfg))
    .replace("@RESOURCE_SETS@", _resource_sets(cfg))
    .replace("@PROJECT_OPTIONS@", _project_options(cfg))
    .replace("@BUILD_IDENTITY@", _build_identity(cfg))
    .replace("@RUNTIME_PAYLOAD@", _runtime_payload(cfg))
    .replace("@PYTHON_SUITES@", _python_suites(cfg))
    .replace("@DISCOVERY_TIMEOUT@", str(cfg.get("test_discovery_timeout", 30)))
    .replace("@MACOS_BUNDLE@", _macos_bundle(cfg))
    .replace("@CXX_STANDARD@", str(cfg.get("cxx_standard") or ""))
    .replace("@CXX_STANDARDS@", ";".join(map(str, CXX_STANDARDS)))
  )


def _module_frameworks(cfg: dict) -> str:
  """[modules.<name>] frameworks, as one registration call per module."""
  return "\n".join(
    f'_buildutil_module_frameworks("{name}" "{";".join(names)}")'
    for name, names in sorted((cfg.get("modules_frameworks") or {}).items()))


def _runtime_payload(cfg: dict) -> str:
  """buildutil.toml's [runtime] as the cmake call that registers it."""
  deps = cfg.get("runtime_from") or []
  dirs = cfg.get("runtime_dirs") or []
  if not deps and not dirs:
    return ""
  return '_buildutil_runtime_payload("{deps}" "{targets}" "{dirs}")'.format(
    deps=";".join(deps), targets=";".join(cfg.get("runtime_targets") or []),
    dirs=";".join(dirs))


def _project_options(cfg: dict) -> str:
  """buildutil.toml's [options] as the cmake call that declares them,
  renders the header and force-includes it."""
  from .options import declaration_list
  declared = cfg.get("options") or {}
  if not declared:
    return ""
  return '_buildutil_project_options("{name}" "{declarations}")'.format(
    name=_project_name(cfg), declarations=declaration_list(declared))


def _project_name(cfg: dict) -> str:
  """What the generated tree is keyed by; a minimal cfg names nothing."""
  return cfg.get("name") or "project"


def _build_identity(cfg: dict) -> str:
  """The call that reads _bdudata/buildinfo.json and renders its header."""
  return f'_buildutil_build_identity("{_project_name(cfg)}")'


def _python_suites(cfg: dict) -> str:
  """buildutil.toml's [test] python as the cmake call that registers it,
  carrying [test.timeout] as `<suite>=<seconds>` pairs."""
  suites = cfg.get("test_python_suites") or []
  if not suites:
    return ""
  timeouts = cfg.get("test_python_timeouts") or {}
  return '_buildutil_python_suites("{dirs}" "{timeouts}")'.format(
    dirs=";".join(suites),
    timeouts=";".join(f"{suite}={seconds}"
                      for suite, seconds in timeouts.items()))


def _macos_bundle(cfg: dict) -> str:
  """buildutil.toml's [bundle.macos] as the cmake call that registers it.
  The helper bundle NAMES were computed in config.py -- an empty variant
  cannot survive a cmake list, and the naming rule is a rule."""
  bundle = cfg.get("bundle_macos") or {}
  if not bundle:
    return ""
  return ('_buildutil_macos_bundle("{module}" "{identifier}" "{name}" '
          '"{version}" "{plist}" "{helper_module}" "{helpers}")').format(
    module=bundle["module"], identifier=bundle["identifier"],
    name=bundle["name"], version=bundle["version"],
    plist=",".join(bundle["plist"]),
    helper_module=bundle["helper_module"],
    helpers=",".join(bundle["helpers"]))


def _resource_namespace(cfg: dict) -> str:
  """The default outer namespace for generated resource accessors: the
  project name as an identifier. A set may name its own."""
  from .config import _identifier
  return _identifier(cfg.get("name") or "project")


def _resource_sets(cfg: dict) -> str:
  """buildutil.toml's [resources] declarations as the cmake calls that
  register them. Rendered rather than parsed in cmake: config.py already
  validated every field, and a project's CMakeLists stays declarations."""
  lines = []
  for entry in cfg.get("resources") or []:
    lines.append(
      '_buildutil_resource_set("{module}" "{dir}" "{files}" "{prefix}" '
      '"{namespace}" "{mime}")'.format(
        module=entry["module"], dir=entry["dir"],
        files=",".join(entry.get("files") or []),
        prefix=entry.get("prefix", ""),
        namespace=entry.get("namespace", ""),
        mime=",".join(entry.get("mime") or [])))
  return "\n".join(lines)


HOST_WRAPPER = TEMPLATES.parent / "host" / "conanfile.py"


def render_host_wrapper(folder: Path, cmake_name: str) -> None:
  """Render one SYSTEM Require's system@host recipe, rewriting only a change."""
  text = HOST_WRAPPER.read_text(encoding="utf-8").replace("@CMAKE_NAME@",
                                                          cmake_name)
  recipe = folder / "conanfile.py"
  if recipe.is_file() and recipe.read_text(encoding="utf-8") == text:
    return
  folder.mkdir(parents=True, exist_ok=True)
  recipe.write_text(text, encoding="utf-8")


def deposited_version(root: Path) -> str | None:
  """The buildutil version a project's deposit was rendered by, read off
  the stamp in buildutil.cmake — None when the deposit holds no such file
  (a fresh clone that has never built, or a deposit an older buildutil
  rendered under a different name) or when it predates stamping. Every
  None means the same thing: the next build renders it."""
  machinery = cmake_dir(root) / "buildutil.cmake"
  if not machinery.is_file():
    return None
  m = _STAMP_RE.search(machinery.read_text())
  return m.group(1) if m else None


def cmake_dir(root: Path) -> Path:
  return root / "_bdudata" / "cmake"


def ensure(root: Path, cfg: dict, exts: list[str] = ()) -> Path:
  """Render base + declared extensions into the runtime dir; returns
  it. Always re-renders — the output is derived, never project-owned,
  so there is no overwrite question and no staleness. The version stamp
  rides in the banner, which means a package upgrade alone changes the
  text and the whole deposit is rewritten on the next build."""
  from .updatecmd import package_version
  version = package_version()
  out = cmake_dir(root)
  out.mkdir(parents=True, exist_ok=True)
  srcs = [TEMPLATES / "base" / n for n in BASE]
  for e in exts:
    src = TEMPLATES / "ext" / e
    if not src.is_dir():
      raise SystemExit(
        f"buildutil: no cmake extension {e!r} in buildutil.toml "
        f"(have: {', '.join(extensions())})")
    srcs += [p for p in sorted(src.iterdir()) if p.is_file()]
  # a file that is no longer ours (an undeclared extension, a rename) must
  # not linger and be include()d -- the deposit is the rendered set, exactly
  wanted = {src.name for src in srcs}
  for stale in out.iterdir():
    if stale.is_file() and stale.name not in wanted:
      stale.unlink()
  for src in srcs:
    dst = out / src.name
    text = _render(src, cfg, version, list(exts))
    if not dst.exists() or dst.read_text() != text:
      dst.write_text(text)
  return out
