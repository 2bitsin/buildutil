"""Local module build toggles in _bdudata/modules.ini.

An ini with [enabled] / [disabled] sections, one module per line. A module is
dormant iff it appears under [disabled] — this matches the project cmake's
dormant-modules parser, the code that actually drops it from the build.
Anything not under [disabled] builds. The set of *available* modules is the
sources/ subdirs that carry a CMakeLists.txt — derived, never hardcoded.
"""
from __future__ import annotations

import re
from pathlib import Path

from .config import MODULE_DEFINE_PREFIX

# Anything a C identifier cannot hold. A directory name is free-form;
# the macro built from it is not.
_NOT_IDENT = re.compile(r"[^0-9A-Za-z_]")

_HEADER = (
  "# Local module build toggles (gitignored). Modules under [disabled] are\n"
  "# skipped at configure time. Edit here or via `buildutil module`.\n"
)


def available(sources_dir) -> list[str]:
  """Module names that exist to toggle: sources/* dirs with a CMakeLists.txt."""
  root = Path(sources_dir)
  if not root.is_dir():
    return []
  return sorted(
    entry.name for entry in root.iterdir()
    if entry.is_dir() and (entry / "CMakeLists.txt").exists())


def load(path) -> tuple[list[str], list[str]]:
  """Parse the ini into (enabled, disabled), each in file order."""
  enabled: list[str] = []
  disabled: list[str] = []
  target = Path(path)
  if not target.exists():
    return enabled, disabled
  section = None
  buckets = {"enabled": enabled, "disabled": disabled}
  for raw in target.read_text().splitlines():
    line = raw.split("#", 1)[0].split(";", 1)[0].strip()
    if not line:
      continue
    if line.startswith("[") and line.endswith("]"):
      section = line[1:-1].strip().lower()
    elif section in buckets:
      buckets[section].append(line)
  return enabled, disabled


def save(path, enabled: list[str], disabled: list[str]) -> None:
  lines = [_HEADER, "[enabled]", *_dedup(enabled), "", "[disabled]", *_dedup(disabled)]
  Path(path).parent.mkdir(parents=True, exist_ok=True)
  Path(path).write_text("\n".join(lines) + "\n")


def _dedup(names: list[str]) -> list[str]:
  seen: set[str] = set()
  return [name for name in names if not (name in seen or seen.add(name))]


def set_dormant(path, name: str, dormant: bool) -> None:
  """Move a module into [disabled] (dormant) or [enabled], leaving order intact
  for the rest."""
  enabled, disabled = load(path)
  enabled = [module for module in enabled if module != name]
  disabled = [module for module in disabled if module != name]
  (disabled if dormant else enabled).append(name)
  save(path, enabled, disabled)


def dormant_set(path, defaults=None, *, system=None, platforms=None) -> set[str]:
  """Which modules do NOT build, from the two layers that decide it.

  The PROJECT default is committed, in buildutil.toml ([modules] dormant):
  "these modules are not buildable yet" is a fact about the project, not
  about your checkout, and it belongs where a clean clone can see it. It
  lived only in the gitignored _bdudata/modules.ini, so a fresh clone of a
  tree with partly-ported modules built them and died -- for a new box, a
  new contributor, or a CI runner, with nothing committed to explain why.

  The LOCAL ini overrides it, in both directions: [disabled] adds to the
  set, [enabled] takes away. That is what makes the toml a default rather
  than a lock -- someone porting `as` puts it under [enabled] and builds
  it, without touching a committed file or fighting the project.

  `system` is the selected target, never the host inferred here. Modules
  whose platform list does not match join the committed dormant defaults
  before the local overrides are applied. None leaves platforms undecided.

  `defaults=None` reads the project config, and that default is the point:
  passing the key explicitly at every call site is exactly how [cmake]
  export_module_headers shipped inert in 0.17.1. Tests pass it directly."""
  if defaults is None:
    from .config import PROJECT
    defaults = PROJECT["modules_dormant"]
  if platforms is None:
    from .config import PROJECT
    platforms = PROJECT.get("modules_platforms", {})
  defaults = set(defaults)
  if system is not None:
    from .naming import live_platform_tags
    live = set(live_platform_tags(system))
    defaults.update(name for name, tags in platforms.items()
                    if not live.intersection(tags))
  enabled, disabled = load(path)
  return (set(defaults) | set(disabled)) - set(enabled)


def status(sources_dir, path, defaults=None) -> list[tuple[str, bool]]:
  """(name, dormant) for every available module, sorted by name."""
  dormant = dormant_set(path, defaults)
  return [(name, name in dormant) for name in available(sources_dir)]


def define_stem(name: str) -> str:
  """A module directory name as the macro half of its ENABLED define:
  upper-cased, with everything a C identifier cannot hold folded to '_'.

  A bare .upper() was not enough. `cc -D` stops reading the macro name at
  the first character that cannot appear in an identifier, so a module
  directory `mstools-cl` emitted -DOW_MSTOOLS-CL_ENABLED=1, which defines
  OW_MSTOOLS with the value "-CL_ENABLED=1". OW_MSTOOLS_CL_ENABLED was
  never defined at all, so the #ifdef a project would reach for was
  silently always false, and the module that legitimately owns the name
  OW_MSTOOLS had its guard redefined to nonsense -- once per hyphenated
  sibling, on every translation unit in the tree."""
  return _NOT_IDENT.sub("_", name).upper()


def enabled_definitions(sources_dir, path, defaults=None) -> list[str]:
  """The preprocessor defs announcing which modules build:
  <PREFIX>_<NAME>_ENABLED=1 for every available, non-dormant module
  (name through define_stem, prefix from buildutil.toml). A translation
  unit anywhere can `#if` on it to know whether a sibling module is
  present; a dormant one is simply absent from this set, so its seams
  fold away. cmake applies the list verbatim (buildutil.cmake
  Scan_subdirectories)."""
  live = [name for name, dormant in status(sources_dir, path, defaults)
          if not dormant]
  # Folding to '_' can make two directory names collide (foo-bar and
  # foo_bar both become FOO_BAR). Silently, one guard would then stand
  # for two modules -- refuse instead, the way a duplicate app name is
  # refused, and say which two.
  owner: dict[str, str] = {}
  for name in live:
    stem = define_stem(name)
    if stem in owner:
      raise SystemExit(
        f"buildutil: module directories {owner[stem]!r} and {name!r} both "
        f"announce themselves as {MODULE_DEFINE_PREFIX}_{stem}_ENABLED -- "
        f"the define is the directory name with non-identifier characters "
        f"folded to '_'. Rename one of them.")
    owner[stem] = name
  return [f"{MODULE_DEFINE_PREFIX}_{define_stem(name)}_ENABLED=1"
          for name in live]
