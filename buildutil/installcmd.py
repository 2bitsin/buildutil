"""`buildutil install` — vendor the whole package into a project
(stdlib-only, pre-venv, like init).

The copy is TRACKED: it lands at <root>/.buildutil/buildutil/ (dot-
prefixed, so the project's `_*` gitignore rule does not touch it), and
a `./buildutil` launcher script lands at the repo root — the tool
travels with the project, and a fresh clone on a system with nothing
installed runs `./buildutil build` once: the launcher puts .buildutil/
on PYTHONPATH, the bootstrap brings up the project venv, and every
invocation after runs through it.

A VENDORED marker file inside the copy carries the version;
`buildutil --version` and the deposit stamp read it, and `buildutil
update` sees it and upgrades the COPY in place (same private-index rule
as a pip update) instead of pip.

Install into a directory with no trace of buildutil also runs init —
one command from empty directory to buildable project — unless
--no-init says the caller only wants the vendoring.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent

VENDOR_PARENT = ".buildutil"

_EXCLUDE_DIRS = {"__pycache__", ".pytest_cache"}

# The repo-root shortcut: `./buildutil build --whatever` from a clone,
# venv-backed. Order of preference mirrors how a project actually runs:
# a vendored copy IS the project's buildutil, so it wins over anything
# installed on the machine; the project venv's interpreter/console
# script carries the pinned toolchain, so it wins over PATH; python3 is
# the first-run fallback whose bootstrap creates that venv. sh, not
# bash — a fresh system promises no more.
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
  """Put the `./buildutil` shortcut at the project root. Never clobbers
  what isn't ours: a directory there is the pre-0.42 vendored layout
  (vendor_into migrates it), a file without our marker is the
  project's own. Our own launcher is content-compared and rewritten,
  so an old shape heals on the next install/update."""
  path = root / "buildutil"
  if path.is_dir():
    print(f"  ./buildutil is a directory — launcher not written "
          f"(a pre-0.42 vendored copy? `git rm -r buildutil`, then "
          f"re-run install)")
    return
  # bytes, not text: the foreign thing squatting on the name may well
  # be a compiled binary, and identifying it must not crash on decode
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
  """The version OF THE TREE BEING VENDORED -- never the running copy's.
  The two differ exactly when it matters: `update` fetches the NEW
  version into a scratch dir and vendors from there, and stamping it
  with the running (old) version would leave a copy lying about itself
  forever after. A scratch copy carries pip's dist-info beside it; a
  re-vendored copy carries its own VENDORED marker; only the running
  package falls back to its own metadata."""
  marker = source / "VENDORED"
  if marker.is_file():
    return marker.read_text(encoding="utf-8").strip()
  for info in source.parent.glob("buildutil-*.dist-info/METADATA"):
    for line in info.read_text(encoding="utf-8").splitlines():
      if line.startswith("Version:"):
        return line.split(":", 1)[1].strip()
  from .updatecmd import package_version
  return package_version()


def _copy_package(root: Path, source: Path) -> Path:
  """The copy itself: package at `source` to <root>/.buildutil/buildutil
  with the VENDORED version marker. Shared by the two callers with very
  different intents — vendor_into (committed, with launcher) and
  stage_copy (temporary, export-only)."""
  dst = root / VENDOR_PARENT / "buildutil"
  if dst.exists():
    shutil.rmtree(dst)
  dst.parent.mkdir(parents=True, exist_ok=True)
  shutil.copytree(
    source, dst,
    ignore=shutil.ignore_patterns(*_EXCLUDE_DIRS))
  (dst / "VENDORED").write_text(
    _source_version(source) + "\n", encoding="utf-8")
  return dst


def stage_copy(root: Path, source: Path = PKG_DIR) -> Path:
  """The vendored copy ALONE — no launcher, no commit guidance, no
  legacy migration. `publish --bake-buildutil` stages it around the
  conan export and discards it after: shipping a self-building package
  must never force the author to vendor their working tree. Returns
  the copy's path."""
  return _copy_package(root, source)


def discard_staged(dst: Path) -> None:
  """Undo stage_copy: the copy goes, and an emptied .buildutil/ parent
  goes with it. A parent holding anything else is left alone — it
  existed before us."""
  if dst.exists():
    shutil.rmtree(dst)
  parent = dst.parent
  if parent.is_dir() and not any(parent.iterdir()):
    parent.rmdir()


def vendor_into(root: Path, source: Path = PKG_DIR) -> Path:
  """Copy the package at `source` to <root>/.buildutil/buildutil,
  stamping the version, and (re)write the ./buildutil launcher.
  Returns the destination. Refuses only the degenerate case: vendoring
  a copy over itself."""
  dst = root / VENDOR_PARENT / "buildutil"
  if dst.resolve() == source.resolve():
    print("already running the vendored copy at "
          f"{dst} — nothing to install")
    write_launcher(root)
    return dst
  dst = _copy_package(root, source)
  version = _source_version(source)
  # pre-0.42 the copy lived at <root>/buildutil — the exact path the
  # launcher needs. A marker-carrying dir there is ours to retire; when
  # it IS the source we are running from it, so deleting it under a
  # live interpreter (whose lazy imports still resolve there) is the
  # one thing this must not do — hand that step back.
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

  # nearest project root wins -- vendoring belongs beside buildutil.toml,
  # not wherever cwd happens to be inside the tree
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
