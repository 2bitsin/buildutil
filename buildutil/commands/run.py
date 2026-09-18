"""buildutil commands: run."""
from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

import typer

from ..app import (app, _compiler_option, _option_option,
                   _renamed_no_upload_option)
from ..engine import *




@app.command(
  context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
def run(
  ctx: typer.Context,
  target: str = typer.Option(
    None, "--target",
    help="App to launch: its binary name (the module's leaf directory) "
       "or the joined module name — resolved to the module's mirrored "
       "spot in the install tree. Default: the [run] table in "
       "buildutil.toml (`default`, with per-OS `default_linux`/"
       "`default_windows`/`default_macos` overrides).",
  ),
  release: bool = typer.Option(False, "--release", help="Optimized build."),
  debug: bool = typer.Option(False, "--debug", help="Debug build (default)."),
  no_build: bool = typer.Option(
    False, "--no-build",
    help="Skip the incremental build/install step; just exec the existing binary.",
  ),
  no_upload: bool = _renamed_no_upload_option(),
  skip_dependency_upload: bool = typer.Option(
    False, "--skip-dependency-upload-so-everyone-rebuilds-from-source",
    help="Skip caching the built dependencies in the conan remote. Only for a "
         "real reason: every later clean build then recompiles them.",
  ),
  args: str = typer.Option(
    None, "--args",
    help="Extra arguments for the target as ONE shell-style string "
       "(shlex-split; '' = none). The vscode run tasks route their "
       "prompt through this, and the value is REMEMBERED — it becomes "
       "the prompt's next default and the launch configs' argv.",
  ),
  conan_home: str = typer.Option(
    None, "--conan-home",
    help="Override the CONAN_HOME path. Defaults to $CONAN_HOME if set, "
       "else <repo>/_conanhome.",
  ),
  compiler: str = _compiler_option(),
  option: list[str] = _option_option(),
  psexec: bool = typer.Option(
    False, "--psexec",
    help="Windows only. Launch via 'psexec -i' so the target lands "
       "in the user's interactive desktop session (not Session 0). "
       "Useful when buildutil itself is invoked from a Windows "
       "service (gitlab-runner) and the target opens a GUI window. "
       "Requires psexec.exe on PATH.",
  ),
):
  """Build (incrementally) and exec an installed binary.

  Everything passed after `--` is forwarded verbatim to the target. Example:

    buildutil run --target hello
  """
  from ..config import default_run_target
  _enter(conan_home)
  if target is None:
    target = default_run_target(platform.system())
    if not target:
      typer.echo(
        "buildutil run: no --target given and no [run] default in "
        "buildutil.toml", err=True)
      raise typer.Exit(code=2)

  # --target may be the joined module name (vscode tasks pass it, cmake
  # calls the target that) or the bare binary name; the FILE is always
  # named after the module's leaf directory and ships at the module's
  # mirrored source path (buildutil.cmake: the source tree is the install
  # tree). Resolve through the one shared derivation.
  from .. import naming
  resolved = naming.resolve_app(target, Path("sources"))
  app, mirror = resolved if resolved else (target, None)

  extra_args = list(ctx.args)
  if args is not None:
    import shlex
    from .. import vscode as _vscode
    # keyed by the APP name: the launch configs replay recorded args
    # under that name, and a joined-name key would never be read back
    _vscode.record_input("args", app, args)
    extra_args = [*shlex.split(args), *extra_args]

  # Compiler detection (cl /Bv et al) is only meaningful when we're
  # going to invoke the build. The --no-build path just exec's an
  # already-installed binary, so don't require cl on PATH for it —
  # CI's launch:gui job doesn't source vcvars and would crash on
  # _detect_settings otherwise.
  if not no_build:
    _select_compiler(compiler, "auto")
    build_type = _resolve_build_type(release, debug)
    settings = _detect_settings(build_type)
    profile = _ensure_profile(settings)
    build_dir = Path("_build") / _profile_name(settings)

    _warn_dependency_upload_skipped(skip_dependency_upload)
    _conan_install(profile)
    _cmake_configure(build_dir, build_type, tests=True, bench=False)
    _cmake_build(build_dir)
    subprocess.check_call([
      "cmake", "--install", str(build_dir), "--prefix", str(INSTALL_PREFIX),
    ])
    if not skip_dependency_upload:
      _upload_to_remote()

  # Resolve the binary. --no-build prefers the freshest build-tree
  # copy: `buildutil bench` (and build-time generator scripts that
  # shell out to `run --no-build`) build but never install, so
  # _install/bin would be stale. With a build the install step ran,
  # so _install/bin is current.
  binary = None
  if no_build:
    candidates = sorted(
      # <build>/bin, where buildutil.cmake puts every app — under its APP
      # name (the leaf), which is also why the old {target} glob missed
      # every nested module: the joined name names no file anywhere.
      (p for p in Path("_build").glob(f"*/bin/{app}")
       if p.is_file()),
      key=lambda p: p.stat().st_mtime, reverse=True)
    if candidates:
      binary = candidates[0]
  # Windows installs target binaries with a .exe suffix; cmake's
  # install respects that, so add it here when the bare path doesn't
  # exist. POSIX leaves the name unchanged.
  if binary is None:
    if mirror is not None:
      binary = INSTALL_PREFIX / mirror / app if mirror else INSTALL_PREFIX / app
    else:
      # no module answers to the name (a stale install, a generated
      # tool): fall back to finding the file — app names are unique
      # tree-wide, so a hit is unambiguous
      found = [p for p in INSTALL_PREFIX.rglob(app) if p.is_file()]
      binary = found[0] if found else INSTALL_PREFIX / app
    if platform.system() == "Windows" and not binary.exists():
      exe = binary.with_suffix(".exe")
      if exe.exists():
        binary = exe
  # [bundle.macos]: the artefact is a DIRECTORY, and exec'ing the binary
  # inside it is not the same thing as launching it -- the framework CEF
  # loads is found relative to the bundle, the Info.plist is what names
  # the sub-process bundles, and LSUIElement only applies to a launched
  # app. `open` is how macOS starts one.
  bundle = Path(f"{binary}.app")
  if platform.system() == "Darwin" and bundle.is_dir():
    cmd = ["open", "-W", "-n", str(bundle)]
    if extra_args:
      cmd += ["--args", *extra_args]
    typer.echo(f"launching {bundle.name}")
    raise SystemExit(subprocess.call(cmd))
  if not binary.exists():
    typer.echo(f"binary not found: {binary}", err=True)
    have = sorted(
      str(p.relative_to(INSTALL_PREFIX))
      for p in INSTALL_PREFIX.rglob("*")
      if p.is_file() and (os.access(p, os.X_OK) or p.suffix == ".exe")
    ) if INSTALL_PREFIX.is_dir() else []
    if have:
      shown = ", ".join(have[:20]) + (" …" if len(have) > 20 else "")
      typer.echo(f"installed binaries: {shown}", err=True)
    else:
      typer.echo(
        "nothing is installed yet — a plain `buildutil build` installs "
        "every module's app (a --target build skips install by design)",
        err=True)
    raise typer.Exit(code=1)

  if psexec:
    if platform.system() != "Windows":
      typer.echo("--psexec is Windows-only", err=True)
      raise typer.Exit(code=2)
    # Resolve the logged-in user's session via explorer.exe (the
    # Windows shell process). `psexec -i` without an id defaults to
    # the *console* session, which is not the user's session when
    # they're connected via RDP — the binary launches but lands on a
    # desktop nobody can see.
    session = _detect_user_session_id()
    if session is None:
      typer.echo(
        "--psexec: no interactive session found (no explorer.exe). "
        "Log in at the console or via RDP and re-run.",
        err=True,
      )
      raise typer.Exit(code=3)
    typer.echo(f"launching in session {session}")
    # psexec launches the target with a system32 working directory, so a
    # relative arg like `--floppy boot.img` wouldn't resolve. Pin the working
    # dir (-w) to ours and hand psexec an absolute binary path.
    cmd = [
      "psexec.exe", "-accepteula", "-nobanner",
      "-i", str(session), "-w", os.getcwd(),
      str(binary.resolve()), *extra_args,
    ]
    raise SystemExit(subprocess.call(cmd))

  # execv replaces the buildutil process; the target inherits stdout/stderr
  # / signals directly, no extra layer to interpose between Ctrl-C and the
  # target's signal handler.
  os.execv(str(binary), [str(binary), *extra_args])
