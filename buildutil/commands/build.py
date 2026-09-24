"""buildutil commands: setup, clean, build, kill."""
from __future__ import annotations

import os
import shutil
import signal
from pathlib import Path

import typer

from .. import bootstrap
from ..app import (app, _compiler_option, _option_option,
                   _renamed_no_upload_option)
from ..engine import *




@app.command()
def setup(
  no_agents: bool = typer.Option(
    False, "--no-agents",
    help="Skip installing the buildutil skill for coding agents "
         "detected at the project root."),
) -> None:
  """Rewrite the venv console script, re-register the conan remote,
  and render the cmake machinery into the runtime dir (the same render
  every build runs on entry — nothing is ever committed).

  Auto-runs once on first invocation when _pyvenv/ doesn't exist. A
  CONAN_REMOTE_* change (env or .env) is picked up automatically by the
  next command anyway — the registration is stamp-guarded on the env's
  fingerprint; this verb FORCES a fresh registration + login regardless.
  """
  from .. import deposit
  from ..config import PROJECT, REPO_ROOT
  # PROJECT whole — see the note at engine.py's ensure() call
  out = deposit.ensure(REPO_ROOT, PROJECT, PROJECT["cmake_extensions"])
  typer.echo(f"cmake machinery rendered at {out}")
  bootstrap.install_venv_symlinks()
  bootstrap.ensure_conan_remote(force=True)
  if not no_agents:
    from .. import skillcmd
    skillcmd.auto_install(REPO_ROOT)



@app.command()
def deps(
  release: bool = typer.Option(
    False, "--release", help="Only the Release configuration."),
  debug: bool = typer.Option(
    False, "--debug", help="Only the Debug configuration."),
  relwithdebinfo: bool = typer.Option(
    False, "--relwithdebinfo", help="Optimized build with debug info."),
  conan_home: str = typer.Option(
    None, "--conan-home",
    help="Override the CONAN_HOME path."),
  compiler: str = _compiler_option(),
) -> None:
  """Pre-install (conan) the dependency graph — no build.

  With no flags every configuration is warmed (Debug, Release,
  RelWithDebInfo), so a fresh checkout downloads/builds all deps in
  one sitting instead of stalling the first build of each flavor;
  flags narrow it to specific configurations.
  """
  build_type = _resolve_build_type(release, debug, relwithdebinfo)
  _enter(conan_home)
  _select_compiler(compiler, "auto")
  chosen = ([build_type]
            if release or debug or relwithdebinfo else [])
  for build_type in chosen or ["Debug", "Release", "RelWithDebInfo"]:
    settings = _detect_settings(build_type)
    profile = _ensure_profile(settings)
    build_dir = Path("_build") / _profile_name(settings)
    typer.echo(f"deps: conan install for {_profile_name(settings)}")
    _conan_install(profile, build_dir)


@app.command()
def clean(
  all: bool = typer.Option(
    False, "--all",
    help="Delete the whole _build/ tree (preserves _conanhome/)."),
  nuke: bool = typer.Option(
    False, "--nuke",
    help="Equivalent to `rm -rf _*` at the project root — wipes every "
         "underscore-prefixed entry (build, install, conanhome, "
         "pyvenv). Brings the tree back to a fresh-clone state."),
  yes: bool = typer.Option(
    False, "--yes", "-y",
    help="Skip the --nuke confirmation prompt."),
) -> None:
  """Wipe transient build artifacts.

  With no flags: clear stale `.gcda` runtime-coverage files and the
  rendered `_build/coverage-report/` — useful after a `./buildutil run`
  of an instrumented target left timestamp-mismatch data behind that
  confuses the next `coverage` run.
  """

  if nuke:
    # `_*` is gitignored — wipe every underscore-prefixed entry in the
    # project root the way `rm -rf _*` would, leaving only tracked
    # state. Matches _build, _install, _conanhome, _pyvenv, any
    # leftover _probe scripts, etc.
    victims = sorted(Path(".").glob("_*"))
    if not victims:
      typer.echo("buildutil clean: nothing to nuke")
      return
    typer.echo("buildutil clean --nuke will remove:")
    for path in victims:
      typer.echo(f"  {path}/")
    if not yes and not typer.confirm("proceed?", default=False):
      typer.echo("buildutil clean: aborted")
      raise typer.Exit(code=1)
    for path in victims:
      if path.is_symlink():
        path.unlink()
      elif path.is_dir():
        shutil.rmtree(path)
      else:
        path.unlink()
      typer.echo(f"buildutil clean: removed {path}")
    return

  build_root = Path("_build")
  if not build_root.exists():
    typer.echo("buildutil clean: nothing to do (_build/ does not exist)")
    return

  if all:
    shutil.rmtree(build_root)
    typer.echo(f"buildutil clean: removed {build_root}/")
    install_dir = Path("_install")
    if install_dir.exists():
      shutil.rmtree(install_dir)
      typer.echo(f"buildutil clean: removed {install_dir}/")
    return

  report_dir = build_root / "coverage-report"
  if report_dir.exists():
    shutil.rmtree(report_dir)
    typer.echo(f"buildutil clean: removed {report_dir}")
  gcda_files = list(build_root.rglob("*.gcda"))
  for path in gcda_files:
    path.unlink()
  typer.echo(f"buildutil clean: removed {len(gcda_files)} .gcda file(s)")



@app.command()
def build(
  release: bool = typer.Option(False, "--release", help="Optimized build (default)."),
  debug: bool = typer.Option(False, "--debug", help="Debug build."),
  relwithdebinfo: bool = typer.Option(
    False, "--relwithdebinfo", help="Optimized build with debug info."),
  no_tests: bool = typer.Option(
    False, "--no-tests", help="Skip building test targets."
  ),
  include_bench: bool = typer.Option(
    False, "--include-bench",
    help="Also build *-benches targets. Off by default — benches pull in "
         "google-benchmark and add real compile time (#embed corpora etc.).",
  ),
  no_upload: bool = _renamed_no_upload_option(),
  skip_dependency_upload: bool = typer.Option(
    False, "--skip-dependency-upload-so-everyone-rebuilds-from-source",
    help="Skip caching the built dependencies in the conan remote. Only for a "
         "real reason: every later clean build then recompiles them.",
  ),
  conan_home: str = typer.Option(
    None, "--conan-home",
    help="Override the CONAN_HOME path. Defaults to $CONAN_HOME if set, "
       "else <repo>/_conanhome.",
  ),
  target: str = typer.Option(
    None, "--target",
    help="Build only this CMake target (and its dependencies), skipping "
         "install and upload. For fast iteration on one module, e.g. "
         "--target hello. Repeatable via comma: --target hello,utilities.",
  ),
  gc_sections: bool = typer.Option(
    False, "--gc-sections",
    help="Build with the linker's --gc-sections/--print-gc-sections and write "
         "the dropped (unreferenced, i.e. dead-code) sections to "
         "_build/<profile>/gc-sections.log. Linux gcc/clang only; pair with "
         "--no-tests for the cleanest product-only signal.",
  ),
  compiler: str = _compiler_option(),
  option: list[str] = _option_option(),
):
  """Run conan install, cmake configure, and cmake build."""
  build_type = _resolve_build_type(release, debug, relwithdebinfo)
  _enter(conan_home)
  _select_compiler(compiler, "auto")
  _warn_dependency_upload_skipped(skip_dependency_upload)
  _full_build(
    build_type,
    _profile_origin(release, debug, relwithdebinfo),
    tests=not no_tests,
    upload=not skip_dependency_upload,
    bench=include_bench,
    targets=target.split(",") if target else None,
    gc_sections=gc_sections,
  )



@app.command()
def kill() -> None:
  """Kill a running buildutil build — the whole ninja/compiler group.

  For when a build runs away (e.g. a template-error explosion spewing
  a multi-gigabyte log). No-op if nothing is building.
  """
  if not BUILD_PID_FILE.exists():
    typer.echo("buildutil kill: no build in progress")
    return
  pid = int(BUILD_PID_FILE.read_text().strip())
  try:
    os.killpg(pid, signal.SIGKILL)
    typer.echo(f"buildutil kill: killed build process group {pid}")
  except ProcessLookupError:
    typer.echo("buildutil kill: build already finished")
  BUILD_PID_FILE.unlink(missing_ok=True)
