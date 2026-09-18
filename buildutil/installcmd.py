"""`buildutil install` — vendor the package into a project so the tool
travels with it: a tracked copy at <root>/.buildutil (dot-prefixed, out
of reach of the project's `_*` gitignore rule) and a root launcher."""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent

VENDOR_PARENT = ".buildutil"

_EXCLUDE_DIRS = {"__pycache__", ".pytest_cache"}

# Order of preference mirrors how a project runs: a vendored copy IS the
# project's buildutil, the venv carries the pinned toolchain, python3 is
# the first-run fallback whose bootstrap creates that venv.
LAUNCHER = """\
#!/bin/sh
# buildutil launcher -- written by `buildutil install` / `buildutil init`.
# `./buildutil build ...` runs the project's buildutil through the venv
# the first run bootstraps (_pyvenv), so every call uses the pinned
# toolchain. A vendored copy (.buildutil/buildutil) takes precedence
# over anything installed on the machine. install/update regenerate
# this file -- edits here do not survive.
here="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
venv="${BUILDUTIL_VENV_DIR:-$here/_pyvenv}"
# keep bytecode caches out of the tracked tree (vendored copy included)
: "${PYTHONPYCACHEPREFIX:=$here/_bdudata/pycache}"
export PYTHONPYCACHEPREFIX
if [ -f "$here/.buildutil/buildutil/__main__.py" ]; then
  PYTHONPATH="$here/.buildutil${PYTHONPATH:+:$PYTHONPATH}"
  export PYTHONPATH
  if [ -x "$venv/bin/python" ]; then
    exec "$venv/bin/python" -m buildutil "$@"
  fi
  exec python3 -m buildutil "$@"
fi
if [ -x "$venv/bin/buildutil" ]; then
  exec "$venv/bin/buildutil" "$@"
fi
if command -v buildutil >/dev/null 2>&1; then
  exec buildutil "$@"
fi
exec python3 -m buildutil "$@"
"""


def write_launcher(root: Path) -> None:
  """The `./buildutil` shortcut: a directory there is the pre-0.42 layout
  (vendor_into migrates it), a foreign file is the project's own."""
  path = root / "buildutil"
  if path.is_dir():
    print(f"  ./buildutil is a directory — launcher not written "
          f"(a pre-0.42 vendored copy? `git rm -r buildutil`, then "
          f"re-run install)")
    return
  # the foreign thing on the name may be a binary: never decode it
  existing = path.read_bytes() if path.exists() else None
  if existing is not None and b"buildutil launcher" not in existing[:200]:
    print("  ./buildutil exists and is not our launcher — kept")
    return
  if existing != LAUNCHER.encode():
    path.write_text(LAUNCHER)
    path.chmod(0o755)
    print("  ./buildutil launcher written — `./buildutil build` works, "
          "fresh clone included")


def _source_version(source: Path) -> str:
  """The version OF THE TREE BEING VENDORED — `update` vendors the NEW
  version out of a scratch dir, and the old stamp would outlive it."""
  marker = source / "VENDORED"
  if marker.is_file():
    return marker.read_text(encoding="utf-8").strip()
  for info in source.parent.glob("buildutil-*.dist-info/METADATA"):
    for line in info.read_text(encoding="utf-8").splitlines():
      if line.startswith("Version:"):
        return line.split(":", 1)[1].strip()
  from .updatecmd import package_version
  return package_version()


def _source_origin(source: Path) -> str:
  """Where the tree being vendored CAME from, so the copy can still be
  updated once its own dist-info is gone."""
  from .updatecmd import ORIGIN_STAMP, origin_from_direct_url, recorded_origin
  stamp = source / ORIGIN_STAMP
  if stamp.is_file():
    return stamp.read_text(encoding="utf-8").strip()
  for record in source.parent.glob("buildutil-*.dist-info/direct_url.json"):
    return origin_from_direct_url(record.read_text(encoding="utf-8"))
  return recorded_origin()


def _copy_package(root: Path, source: Path) -> Path:
  """The copy itself, stamped with its version and its origin."""
  dst = root / VENDOR_PARENT / "buildutil"
  if dst.exists():
    shutil.rmtree(dst)
  dst.parent.mkdir(parents=True, exist_ok=True)
  shutil.copytree(
    source, dst,
    ignore=shutil.ignore_patterns(*_EXCLUDE_DIRS))
  from .updatecmd import ORIGIN_STAMP
  (dst / "VENDORED").write_text(
    _source_version(source) + "\n", encoding="utf-8")
  origin = _source_origin(source)
  if origin:
    (dst / ORIGIN_STAMP).write_text(origin + "\n", encoding="utf-8")
  return dst


def stage_copy(root: Path, source: Path = PKG_DIR) -> Path:
  """The copy ALONE, for `publish --bake-buildutil`: shipping a
  self-building package must not force the author to vendor their tree."""
  return _copy_package(root, source)


def discard_staged(dst: Path) -> None:
  """Undo stage_copy; a parent holding anything else existed before us."""
  if dst.exists():
    shutil.rmtree(dst)
  parent = dst.parent
  if parent.is_dir() and not any(parent.iterdir()):
    parent.rmdir()


def vendor_into(root: Path, source: Path = PKG_DIR) -> Path:
  """The tracked copy plus the ./buildutil launcher."""
  dst = root / VENDOR_PARENT / "buildutil"
  if dst.resolve() == source.resolve():
    print("already running the vendored copy at "
          f"{dst} — nothing to install")
    write_launcher(root)
    return dst
  dst = _copy_package(root, source)
  version = _source_version(source)
  # pre-0.42 the copy lived at <root>/buildutil, the launcher's path;
  # deleting it while running FROM it is the one thing not to do
  legacy = root / "buildutil"
  if (legacy / "VENDORED").is_file():
    if legacy.resolve() == source.resolve():
      print("the pre-0.42 vendored copy at buildutil/ is superseded — "
            "`git rm -r buildutil`, then re-run install so the "
            "./buildutil launcher can take its place")
    else:
      shutil.rmtree(legacy)
      print("pre-0.42 vendored copy at buildutil/ removed "
            f"(the copy now lives in {VENDOR_PARENT}/)")
  write_launcher(root)
  print(f"buildutil {version} vendored at "
        f"{dst.relative_to(root).as_posix()}/ "
        f"({sum(1 for p in dst.rglob('*') if p.is_file())} files)")
  print(f"commit {VENDOR_PARENT}/ and the ./buildutil launcher: the "
        "tool now travels with the project. A fresh clone runs "
        "`./buildutil build` — the first run bootstraps the venv, and "
        "every run after goes through it.")
  return dst


def main(argv: list[str]) -> None:
  ap = argparse.ArgumentParser(
    prog="buildutil install",
    description="vendor the running buildutil package into the project "
                "so it is tracked with it; in a directory with no trace "
                "of buildutil, also run init")
  ap.add_argument("--no-init", action="store_true",
                  help="only vendor; do not scaffold a project when no "
                       "buildutil.toml exists")
  a = ap.parse_args(argv)

  # vendoring belongs beside buildutil.toml, not wherever cwd is
  from .updatecmd import project_root
  root = project_root() or Path.cwd()
  had_project = (root / "buildutil.toml").is_file()
  vendor_into(root)
  if not had_project:
    if a.no_init:
      print("no buildutil.toml here and --no-init given — vendored only")
      return
    print("no buildutil.toml here — running init:")
    from . import initcmd
    initcmd.main([])
