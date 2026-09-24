"""Optional helpers for a module's configure.py -- buildutil's
per-module configure step. Liftable; nothing here is project-specific.

configure.py is the entry point: the build runs it at CONFIGURE time
(presence-driven, like *.test.cpp / *.patch) with this module
importable. Its FIRST use is code generation, but it is a general
configure hook -- a script may do anything and need not import this at
all. The actual contract is three environment variables:

  CONFIGURE_OUTPUT_DIR    per-profile root (_build/<profile>/generated/<module>)
  CONFIGURE_SHARED_DIR    profile-agnostic (_build/generated/<module>)
  CONFIGURE_MANIFEST      where to report back
  CONFIGURE_SOURCE_DIR    the module's own directory
  CONFIGURE_MODULE        the module name
  CONFIGURE_TARGET_SYSTEM the TARGET platform (Linux/Darwin/Windows)
  CONFIGURE_PLATFORM_TAGS the platform tag vocabulary, comma-separated
  CONFIGURE_PROGRAM_PATH  bindirs of the declared TOOL requirements, '|'-sep

A script that ignores this module just reads those and writes the
manifest itself (lines: 'G <abspath>' generated file, 'D <abspath>'
input dependency, 'O <abspath>\t<option>' and 'M <abspath>\t<define>'
one compile option or definition of a declared source, 'S <number>' and
'V <version>' the shared library's soversion and full version). These
helpers are the convenient way to do the same: pick a root with
output_dir(), emit() files (content-diffed so an unchanged input never
churns a rebuild), and depends() to declare inputs whose change re-runs
configure. CMake adds both roots to the
module include path, compiles/links generated sources, and re-runs on
a changed script or declared input.

THE OUTPUT ROOT IS A MODULE DIRECTORY. It obeys exactly the same layout
conventions as sources/<module>/ -- `*.embed/` is a resource set,
`*.install/` is runtime data, and both take the platform tags
(`*.embed.linux/` overlays `*.embed.posix/` overlays `*.embed/`). So a
hook that writes a file into data_dir('ui.embed') has thereby added an
embedded resource: no declaration, no cmake, and the accessor, the
ccache content digest and the re-glob all follow. A name that a
generated file and a hand-written one both claim is a configure error
naming both. Both roots read that way, the per-profile one and the
shared one, and a data directory is SWEPT when the hook ends: it holds
what this run emitted or wrote and nothing else, so a renamed payload
ships once rather than twice.

buildutil owns no toolchain here on purpose. What a project transpiles,
bundles or compresses is the project's business; this module is the
PLACEMENT api it does that with -- where output goes (data_dir, emit,
emit_bytes), what re-runs the step (depends, inputs), and how an
external tool fails readably (tool, run)."""

import atexit
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

_PER_PROFILE = Path(os.environ['CONFIGURE_OUTPUT_DIR'])
_SHARED = Path(os.environ['CONFIGURE_SHARED_DIR'])
_MANIFEST = Path(os.environ['CONFIGURE_MANIFEST'])
_SOURCE = Path(os.environ.get('CONFIGURE_SOURCE_DIR', '.'))
_MODULE = os.environ.get('CONFIGURE_MODULE', '')
_TARGET = os.environ.get('CONFIGURE_TARGET_SYSTEM', 'Linux')
_TAGS = tuple(t for t in os.environ.get('CONFIGURE_PLATFORM_TAGS', '').split(',')
              if t)
_PROGRAMS = tuple(Path(p) for p in
                  os.environ.get('CONFIGURE_PROGRAM_PATH', '').split('|') if p)

# What a program is called on this machine. `tsc` is `tsc.cmd` on Windows
# (npm writes a shim, not an executable), and cmake's own find_program
# knows the same list.
_EXE_SUFFIXES = ('', '.exe', '.cmd', '.bat') if os.name == 'nt' else ('',)
_generated: list[Path] = []
_options: dict[Path, list[str]] = {}
_defines: dict[Path, list[str]] = {}
_soversion: list[str] = []
_inputs: list[Path] = []
# data directory -> what was in it before this run touched it
_swept: dict[Path, dict[Path, tuple[int, int]]] = {}
_kept: set[Path] = set()            # data_dir(keep=True): swept by nobody

# The directory suffixes whose CONTENTS are the declaration: what is in
# one is embedded or shipped, so what an earlier run left in one ships too.
_DATA_SUFFIXES = ('embed', 'install')


def source_dir() -> Path:
  """The module's own directory -- where configure.py sits, and the root
  every relative path in this module is relative to."""
  return _SOURCE


def module_name() -> str:
  """The cmake target name of the module being configured."""
  return _MODULE


def target_system() -> str:
  """The TARGET platform, never the host: 'Linux', 'Darwin' or
  'Windows'. A cross build gets the platform it is building FOR, which
  is the only one a generated payload can be right for."""
  return _TARGET


def output_dir(shared:bool = False) -> Path:
  """Emit root: per-profile by default, or the build-invariant shared
  root when shared=True (use for output that can't vary by build type,
  like id-stable enums)."""
  root = _SHARED if shared else _PER_PROFILE
  root.mkdir(parents=True, exist_ok=True)
  return root

def data_dir(name:str, *, tag:str|None = None, shared:bool = False,
             keep:bool = False) -> Path:
  """The shadow copy of one of the module's DATA directories, created.

      data_dir('ui.embed')             -> <root>/ui.embed/
      data_dir('ui.embed', tag='linux')-> <root>/ui.embed.linux/

  The point of the helper over building the path by hand is that it is
  the one place that knows the tag goes AFTER the suffix and that the
  vocabulary is closed -- `ui.linux.embed/` reads as an untagged set
  called `ui.linux` and would ship everywhere, and a typo'd tag would
  silently produce a directory nothing ever globs.

  The tree holds what this run put there: a file an earlier run wrote and
  this one did not is removed, because the directory's contents ARE the
  declaration and renaming a payload would otherwise ship both names.
  `keep=True` for a tree something else co-writes."""
  if '/' in name or name.startswith('.'):
    raise SystemExit(
      f"buildutil configure: data_dir({name!r}) takes a single directory "
      "name like 'ui.embed', not a path")
  if tag is not None:
    if tag not in _TAGS:
      raise SystemExit(
        f"buildutil configure: data_dir({name!r}, tag={tag!r}) -- not a "
        f"platform tag. Known: {', '.join(_TAGS)}")
    name = f"{name}.{tag}"
  path = output_dir(shared)/name
  path.mkdir(parents=True, exist_ok=True)
  if keep:
    _kept.add(path)
    _swept.pop(path, None)
  else:
    _watch(path)
  return path


def _watch(directory:Path) -> None:
  """The tree's before-picture, taken once and before anything writes:
  a file still exactly as it was then, and not declared, is one this run
  did not produce."""
  if directory not in _swept and directory not in _kept:
    _swept[directory] = {path: _fingerprint(path)
                         for path in directory.rglob('*') if path.is_file()}


def _fingerprint(path:Path) -> tuple[int, int]:
  info = path.stat()
  return (info.st_mtime_ns, info.st_size)


def _watch_data_dir(relative_path, shared:bool) -> None:
  """emit('ui.embed/app.js') writes into a data directory as surely as
  data_dir('ui.embed') does, and sweeps it on the same terms."""
  head, *rest = Path(relative_path).parts
  if rest and _is_data_dir(head):
    _watch(output_dir(shared)/head)


def _is_data_dir(name:str) -> bool:
  parts = name.split('.')
  if len(parts) > 2 and parts[-1] in _TAGS:
    parts = parts[:-1]
  return len(parts) > 1 and parts[-1] in _DATA_SUFFIXES


def inputs(*patterns:str, root=None) -> list[Path]:
  """Every file matching these glob patterns, recursively, DECLARED as an
  input: editing one re-runs configure, which re-runs whatever this hook
  does with them. Sorted, so the step's own inputs are in a stable order.
  Relative to the module's source directory unless `root` says otherwise.

  ADDING a file is covered too, and it takes more than the file list to
  do it: a new file is in nobody's dependencies, so cmake would not
  re-run and the hook would never see it -- a green build silently
  missing a resource, the exact failure the *.embed/ globs use
  CONFIGURE_DEPENDS to avoid. So the DIRECTORIES are declared as well.
  cmake watches a directory by its mtime, which is what changes when an
  entry appears or disappears."""
  base = Path(root) if root is not None else _SOURCE
  found: list[Path] = []
  for pattern in patterns:
    found.extend(p for p in base.rglob(pattern) if p.is_file())
  found = sorted(set(found))
  depends(*found)
  if base.is_dir():
    depends(base, *(d for d in base.rglob('*') if d.is_dir()))
  return found


def tool_dirs() -> tuple[Path, ...]:
  """The bindirs of this build's DECLARED tools -- one per
  `Require(<x> ... TOOL)` package conan resolved.

  These are what makes a build-time executable a build INPUT rather than
  a fact about whoever's machine it ran on: pinned in
  sources/CMakeLists.txt, resolved by conan, identical on the Linux
  boxes and the macOS and Windows runners."""
  return _PROGRAMS


def find_tool(name:str) -> str | None:
  """An external program, or None. DECLARED tools first, PATH second: a
  project that pinned a version means the pinned one, and a machine that
  happens to have another copy must not quietly win."""
  for directory in _PROGRAMS:
    for suffix in _EXE_SUFFIXES:
      candidate = directory/(name + suffix)
      if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
  return shutil.which(name)


def tool(name:str, *, install:str) -> str:
  """find_tool(), or a refusal that says where it looked and how to get
  it. `install` is the command a human should run -- 'tsc not found' with
  no next step is the failure this argument exists to prevent.

  The refusal names BOTH places on purpose. "not on PATH" is a lie when
  the project declared the tool and conan simply did not resolve it, and
  it sends whoever reads it to install something globally that the build
  was never going to look at."""
  found = find_tool(name)
  if found:
    return found
  declared = ("\n  declared TOOL requirements searched:\n    "
              + "\n    ".join(str(d) for d in _PROGRAMS)
              if _PROGRAMS else
              "\n  this build declares no TOOL requirements -- a "
              "`Require(<pkg> VERSION \"x.y.z\" TOOL)` in "
              "sources/CMakeLists.txt is what would pin it")
  raise SystemExit(
    f"buildutil configure ({_MODULE}): {name!r} was not found, and "
    f"{Path(_SOURCE).name}/configure.py needs it.{declared}\n"
    f"  ... nor on PATH.\n  install it with: {install}")


def run(argv, *, what:str, cwd=None) -> str:
  """Run an external tool. On failure the tool's OWN output is what the
  build shows -- a compiler's file:line:col is what an editor jumps to,
  and wrapping it in a summary would hide the only useful part."""
  done = subprocess.run([str(a) for a in argv], capture_output=True,
                        text=True, cwd=cwd)
  if done.returncode != 0:
    sys.stdout.write(done.stdout)
    sys.stderr.write(done.stderr)
    raise SystemExit(
      f"buildutil configure ({_MODULE}): {what} failed "
      f"(exit {done.returncode}): {' '.join(str(a) for a in argv)}")
  return done.stdout


def emit_bytes(relative_path, content:bytes, *, shared:bool = False) -> Path:
  """emit() for binary output -- a compressed payload, an image, anything
  that is not text. Same content-diff: rewriting identical bytes would
  move an mtime and rebuild whatever embeds them."""
  _watch_data_dir(relative_path, shared)
  path = output_dir(shared)/relative_path
  path.parent.mkdir(parents=True, exist_ok=True)
  if not (path.exists() and path.read_bytes() == content):
    path.write_bytes(content)
  declare(path)
  return path


def emit(relative_path, content:str, *, shared:bool = False,
         encoding:str = "utf-8") -> Path:
  """Write content under the chosen root (only if changed) and register
  it. relative_path is what consumers include -- emit('x86/enums.hpp')
  -> #include "x86/enums.hpp".

  UTF-8 unless told otherwise, NOT the locale's encoding: a generated
  table is a build input, and one whose bytes depend on the LANG of
  whoever ran the build is a table that is silently mojibake under
  LANG=C instead of an error. The compare-before-write below reads back
  the same way -- a file written under the old locale-dependent default
  may not decode as utf-8 at all, which counts as changed, so the first
  build after this lands rewrites it correctly."""
  _watch_data_dir(relative_path, shared)
  path = output_dir(shared)/relative_path
  path.parent.mkdir(parents=True, exist_ok=True)
  try:
    unchanged = path.exists() and path.read_text(encoding=encoding) == content
  except (UnicodeDecodeError, ValueError):
    unchanged = False
  if not unchanged:
    path.write_text(content, encoding=encoding)
  declare(path)
  return path

def declare(path, *, options=None, defines=None) -> None:
  """Register a file as generated, and a source's own compile options and definitions."""
  resolved = Path(path).resolve()
  if resolved not in _generated:
    _generated.append(resolved)
  if options is not None:
    _options[resolved] = [_option(path, o) for o in options]
  if defines is not None:
    _defines[resolved] = [_define(path, d) for d in defines]


def _manifest_value(path, what:str, value) -> str:
  text = str(value)
  if not text or any(c in text for c in ';\t\n\r'):
    raise SystemExit(
      f"buildutil configure ({_MODULE}): declare({str(path)!r}) {what} "
      f"{text!r} is empty or holds ';', a tab or a newline, which cmake "
      "would split into several")
  return text


def _option(path, value) -> str:
  text = _manifest_value(path, 'option', value)
  if ' ' in text and not text.startswith('SHELL:'):
    raise SystemExit(
      f"buildutil configure ({_MODULE}): declare({str(path)!r}) option "
      f"{text!r} holds a space and would reach the compiler as one "
      f"argument: pass each argument as its own option, or spell the "
      f"group 'SHELL:{text}'")
  return text


def _define(path, value) -> str:
  text = _manifest_value(path, 'define', value)
  name = text.partition('=')[0]
  if not (name.isidentifier() and name.isascii()):
    raise SystemExit(
      f"buildutil configure ({_MODULE}): declare({str(path)!r}) define "
      f"{text!r} is not NAME or NAME=value")
  return text


def soversion(number:int, *, version:str|None = None) -> None:
  """The module's shared library ABI number (its soname) and, optionally, its full version."""
  if isinstance(number, bool) or not isinstance(number, int) or number < 0:
    raise SystemExit(
      f"buildutil configure ({_MODULE}): soversion({number!r}) takes a "
      "non-negative integer")
  if version is not None and not re.fullmatch(r'\d+(\.\d+){0,2}', str(version)):
    raise SystemExit(
      f"buildutil configure ({_MODULE}): soversion({number}, "
      f"version={version!r}) takes one to three dotted numbers like '3.4.8'")
  _soversion[:] = [f'S {number}'] + ([f'V {version}'] if version is not None else [])


def depends(*paths) -> None:
  """Declare inputs whose change must re-run configure."""
  for path in paths:
    resolved = Path(path).resolve()
    if resolved not in _inputs:
      _inputs.append(resolved)

@atexit.register
def _finish() -> None:
  _write_manifest()
  _sweep()


def _sweep() -> None:
  """Every watched data directory, left holding exactly what this run
  emitted or wrote into it. Deepest entries first, so a directory is
  considered after the files it held."""
  for directory, before in _swept.items():
    for path in sorted(directory.rglob('*'), reverse=True):
      if path.is_dir():
        if not any(path.iterdir()):
          path.rmdir()
      elif (path.resolve() not in _generated
            and before.get(path) == _fingerprint(path)):
        path.unlink()


def _write_manifest() -> None:
  lines = [f'G {path}' for path in _generated] + [f'D {path}' for path in _inputs]
  lines += [f'O {path}\t{option}' for path, options in _options.items()
            for option in options]
  lines += [f'M {path}\t{define}' for path, defines in _defines.items()
            for define in defines]
  lines += _soversion
  _MANIFEST.parent.mkdir(parents=True, exist_ok=True)
  _MANIFEST.write_text('\n'.join(lines) + ('\n' if lines else ''))
