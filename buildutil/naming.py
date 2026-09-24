"""Module and application naming — the python mirror of buildutil.cmake.

The cmake side derives every name from the directory tree: a module's
TARGET is its sources/-relative path with kind tags stripped from every
component, joined with '-' (_buildutil_name_for_dir); its APPLICATION
is the stripped LEAF alone (_buildutil_module_app_name); and the
install tree mirrors the source tree, so the binary ships at the
stripped parent path + the app name (_buildutil_mirror_parent).

Python needs the same answers — `buildutil run` must find the installed
binary, vscode tasks must name real targets — and deriving them twice
is the two-parsers trap this codebase keeps paying for (modules.ini,
Require, the 0.23 pre-pass). This module is the ONE python derivation;
test_naming.py holds it to the cmake behaviour by compiling a tree and
checking the artifacts land exactly where these functions predict.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Iterator, Sequence

KIND_TAGS = ("obj", "lib", "a", "exe", "so", "dll", "dylib", "test")

# THE PLATFORM VOCABULARY, in one place.
#
# One table, three uses, and that is the point: the same word tags a
# SOURCE (`decode.linux.cpp`), a DIRECTORY that is part of a module
# (`host.linux/`), a MODULE directory (`sources/helper.macos/`) and a
# DATA directory (`ui.embed.win32/`, `locale.install.posix/`). They were
# three regexes in the cmake before, and a fourth in this file waiting
# to disagree with them; now the cmake's copy is RENDERED from here, so
# there is nothing to keep in step.
#
# EXACT tags name one target platform. FAMILY tags name a group of them:
# `posix` is every unix buildutil targets, Emscripten included (musl
# libc, a POSIX-shaped file system: mmap, open and write are there;
# sockets, signals and processes are not), `apple` is macOS (and iOS if
# it is ever added), `native` is every target with an operating system
# underneath, which is every one but the browser. A family is LESS
# specific than an exact tag, which is what makes overlays orderable --
# see PLATFORM_LEVEL.
PLATFORM_EXACT = ("win32", "linux", "macos", "emscripten")
PLATFORM_FAMILIES = ("posix", "apple", "native")
PLATFORM_TAGS = PLATFORM_EXACT + PLATFORM_FAMILIES

# Which tags are LIVE for a TARGET platform (cmake's CMAKE_SYSTEM_NAME,
# never the host), ordered LEAST SPECIFIC FIRST so a later entry
# overlays an earlier one. Any other system reads as a generic unix.
PLATFORM_LIVE = {
  "Emscripten": ("posix", "emscripten"),
  "Windows": ("native", "win32"),
  "Darwin": ("native", "posix", "apple", "macos"),
  "Linux": ("native", "posix", "linux"),
}
PLATFORM_LIVE_DEFAULT = PLATFORM_LIVE["Linux"]

# Specificity: the untagged thing is level 0, a family overlays it, an
# exact platform overlays the family. Two applicable things at the SAME
# level claiming one name is a configure error -- there is no rule that
# would say which of `ui.embed.posix/` and `ui.embed.apple/` wins on
# macOS, so buildutil refuses to invent one.
PLATFORM_LEVEL = {tag: 1 for tag in PLATFORM_FAMILIES}
PLATFORM_LEVEL.update({tag: 2 for tag in PLATFORM_EXACT})


# Which concrete TARGET SYSTEMS (cmake's CMAKE_SYSTEM_NAME) each tag
# stands for. Everything below is set arithmetic over this, which is what
# makes "extension default INTERSECT explicit tag" a rule rather than a
# pile of cases.
TAG_SYSTEMS = {
  "emscripten": ("Emscripten",),
  "win32": ("Windows",),
  "linux": ("Linux",),
  "macos": ("Darwin",),
  "posix": ("Linux", "Darwin", "Emscripten"),
  "apple": ("Darwin",),
  "native": ("Windows", "Linux", "Darwin"),
}

# A source EXTENSION carries a platform default, so a file that can only
# exist on one platform does not have to say so twice. `window.mm` is
# Objective-C++: it is an Apple source by being one, and tagging it
# `.macos.mm` would be repeating the extension. An explicit tag then only
# NARROWS the default (`foo.macos.mm` is legal and means the same thing);
# a tag whose systems do not intersect the default (`foo.linux.mm`) is a
# configure error naming both.
#
# An empty tuple means every platform. Extensions NOT here are not module
# sources at all: `.asm` in particular belongs to the watcom extension's
# Init_firmware(), and claiming it in the base glob would silently
# compile a firmware image into a host module.
SOURCE_EXTENSIONS = {
  "cpp": (), "cc": (), "cxx": (), "c": (),
  "mm": ("Darwin",), "m": ("Darwin",),
  "rc": ("Windows",), "manifest": ("Windows",),
  "s": (), "S": (),
}

# Which cmake LANGUAGE an extension needs enabled, where cmake does not
# enable it from project(<name> CXX). Enabled by presence, on the target
# platform the extension is legal on.
EXTENSION_LANGUAGE = {"c": "C", "mm": "OBJCXX", "m": "OBJC", "rc": "RC",
                      "s": "ASM", "S": "ASM"}

# Headers are NOT filtered by tag and never will be: nobody chooses what
# `#include "foo.h"` opens -- the preprocessor does -- so a tagged header
# would resolve or not depending on a rule the include path knows nothing
# about. A per-platform header is reached the ordinary way, from the
# per-platform source that includes it.
HEADER_EXTENSIONS = ("h", "hpp", "hxx", "hh", "inl", "ipp")


def platform_alternation() -> str:
  """The vocabulary as a regex alternation, for the rendered cmake."""
  return "|".join(PLATFORM_TAGS)


def platform_table_cmake() -> str:
  """The table above as cmake, rendered into buildutil.cmake so the two
  sides cannot drift."""
  lines = [f'set(_buildutil_platform_tags "{";".join(PLATFORM_TAGS)}")']
  for system, live in sorted(PLATFORM_LIVE.items()):
    lines.append(f'set(_buildutil_platform_live_{system} "{";".join(live)}")')
  lines.append('set(_buildutil_platform_live_default "'
               + ";".join(PLATFORM_LIVE_DEFAULT) + '")')
  for tag, level in sorted(PLATFORM_LEVEL.items()):
    lines.append(f"set(_buildutil_platform_level_{tag} {level})")
  for tag, systems in sorted(TAG_SYSTEMS.items()):
    lines.append(f'set(_buildutil_tag_systems_{tag} "{";".join(systems)}")')
  lines.append('set(_buildutil_source_extensions "'
               + ";".join(SOURCE_EXTENSIONS) + '")')
  for ext, systems in sorted(SOURCE_EXTENSIONS.items()):
    lines.append(f'set(_buildutil_ext_systems_{ext} "{";".join(systems)}")')
  for ext, language in sorted(EXTENSION_LANGUAGE.items()):
    lines.append(f'set(_buildutil_ext_language_{ext} "{language}")')
  lines.append('set(_buildutil_header_extensions "'
               + ";".join(HEADER_EXTENSIONS) + '")')
  return "\n".join(lines)


def live_platform_tags(system: str) -> tuple[str, ...]:
  """The live tags for a platform.system()-style name, least specific
  first. The python mirror of _buildutil_platform_suffixes."""
  return PLATFORM_LIVE.get(system, PLATFORM_LIVE_DEFAULT)

_SYNONYMS = {"a": "lib", "dll": "so", "dylib": "so"}


def _without_platform_tags(name: str) -> str:
  stripping = True
  while stripping:
    stripping = False
    for tag in PLATFORM_TAGS:
      if name.endswith("." + tag) and len(name) > len(tag) + 1:
        name = name[: -(len(tag) + 1)]
        stripping = True
        break
  return name


def kind_of(component: str) -> tuple[str, str]:
  """(name, kind) for ONE path component — kind tags stripped, synonyms
  collapsed ('.a' means lib, '.dll'/'.dylib' mean so). Strips repeatedly
  so 'thing.lib.so' is seen as the contradiction it is and refused, the
  same refusal cmake makes."""
  # platform tags are not kinds and may sit on either side of one, so they
  # come off before and after, as in cmake's _buildutil_kind_of
  name = _without_platform_tags(component)
  found: list[str] = []
  stripping = True
  while stripping:
    stripping = False
    for tag in KIND_TAGS:
      # cmake's ^(.+)\.tag$ — at least one char must precede the dot
      if name.endswith("." + tag) and len(name) > len(tag) + 1:
        found.append(tag)
        name = name[: -(len(tag) + 1)]
        stripping = True
        break
  if len(found) > 1:
    pretty = ", .".join(found)
    raise SystemExit(
      f"buildutil: {component!r} carries more than one kind tag "
      f"(.{pretty}). A module builds one thing; a listing that says "
      "otherwise is lying about the tree.")
  kind = _SYNONYMS.get(found[0], found[0]) if found else ""
  return _without_platform_tags(name), kind


def module_name(rel_parts: Sequence[str]) -> str:
  """The cmake TARGET name for a module at sources/<parts...>: every
  component tag-stripped, joined with '-' (sources/a/b.exe -> a-b)."""
  return "-".join(kind_of(part)[0] for part in rel_parts)


def app_name(rel_parts: Sequence[str]) -> str:
  """The name a module's application is built and installed under: the
  tag-stripped LEAF (sources/mstools/rc -> rc), never the joined form."""
  return kind_of(rel_parts[-1])[0]


def mirror_parent(rel_parts: Sequence[str]) -> str:
  """Where the module's artifacts install, relative to the prefix: the
  tag-stripped parent path ('' for a top-level module). The leaf names
  the FILE, this names its directories."""
  return "/".join(kind_of(part)[0] for part in rel_parts[:-1])


def modules(sources_dir: Path) -> Iterator[tuple[str, ...]]:
  """sources/-relative path parts of every module (a dir carrying a
  CMakeLists.txt). Dot-prefixed components never build (cmake's scan
  skips them); a component with contradictory kind tags is skipped too —
  cmake refuses to configure such a tree at all, so no artifact of it
  can exist for a caller to resolve."""
  for cmake in sorted(Path(sources_dir).rglob("CMakeLists.txt")):
    parts = cmake.parent.relative_to(sources_dir).parts
    if not parts or any(part.startswith(".") for part in parts):
      continue
    try:
      module_name(parts)
    except SystemExit:
      continue
    yield parts


def resolve_app(target: str, sources_dir: Path) -> tuple[str, str] | None:
  """(app name, install-relative parent dir) for `target`, which may be
  either the joined module name (what vscode tasks pass, what cmake
  calls the target) or the bare app/binary name (unique tree-wide — the
  duplicate-app check enforces it). None when no module matches."""
  for parts in modules(sources_dir):
    if target in (module_name(parts), app_name(parts)):
      return app_name(parts), mirror_parent(parts)
  return None
