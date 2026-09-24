#!/usr/bin/env python3
"""buildutil entry point.

Bootstraps the project venv (re-execing `python -m buildutil` under it
when needed; BUILDUTIL_SYSTEM=1, which a container image carrying the
toolchain sets, skips the venv and runs on the interpreter it was
installed into), then runs the CLI. Reached as the `buildutil` console
script or `python -m buildutil`.
"""
import atexit
import os
import sys


def _lift_watchdog_flags(argv: list[str]) -> tuple[list[str], list[str]]:
  """The watchdog switches (--i-am-willingly-circumventing-build-and-test-time-safeguards, --watchdog-budget N /
  --watchdog-budget=N) are legal ANYWHERE on the line — every subcommand
  accepts them. typer only parses globals before the subcommand, so pull
  them out and hand back (switches, everything else) for reassembly.
  Nothing after a literal `--` is touched: that is the target's own argv
  (run forwards it verbatim)."""
  flags: list[str] = []
  rest: list[str] = []
  i = 0
  while i < len(argv):
    arg = argv[i]
    if arg == "--":
      rest.extend(argv[i:])
      break
    if arg == "--no-watchdog":
      sys.stderr.write("buildutil: --no-watchdog is gone. The watchdog is the "
                       "measurement; a run it fails is a defect to fix. If you "
                       "must, say it: --i-am-willingly-circumventing-build-and-test-time-safeguards\n")
      sys.exit(2)
    if arg == "--i-am-willingly-circumventing-build-and-test-time-safeguards":
      flags.append(arg)
    elif arg == "--watchdog-budget" and i + 1 < len(argv):
      flags.extend(argv[i:i + 2])
      i += 1
    elif arg.startswith("--watchdog-budget="):
      flags.append(arg)
    else:
      rest.append(arg)
    i += 1
  return flags, rest


def _speak_utf8() -> None:
  """Say what we actually write, whatever the console thinks.

  The driver's own output is UTF-8 -- box-drawing rules around the status
  line, arrows, an em-dash in half the messages -- and on Windows python
  encodes stdout in the ANSI codepage the moment it is not a console: a
  PIPE (every CI job, every `buildutil ... > log`) gets cp1252, and the
  status line at the END of a successful run dies with

    UnicodeEncodeError: 'charmap' codec can't encode characters in
    position 2-13: character maps to <undefined>

  which is a green build reported as a failure, after the work was done
  (seen on a Windows CI job: MSVC compiled the project, export-pkg and
  test_package both passed, and this line took the job down). Nothing is
  reconfigured on POSIX, where the default is already UTF-8, and a stream
  that cannot be reconfigured (a replaced sys.stdout, a closed one) is
  left exactly as it is.
  """
  import sys
  for stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
      continue
    try:
      reconfigure(encoding="utf-8")
    except (ValueError, OSError):     # detached, closed, or not a TextIO
      pass


def main() -> None:
  import sys
  import platform

  if platform.system() == "Windows":
    _speak_utf8()

  watchdog_flags, argv = _lift_watchdog_flags(sys.argv[1:])
  sys.argv = [sys.argv[0], *watchdog_flags, *argv]

  # Four pre-project forms run where NO project (and no venv) exists,
  # stdlib-only, skipping the whole bootstrap below: `init` CREATES
  # buildutil.toml; `--version` and `update` concern the INSTALLED
  # package — update in particular must run on the interpreter that
  # owns it, which sys.executable stops being after the re-exec. They
  # never reach the typer root callback, so the watchdog is armed here
  # by the same rules (--version excepted: it cannot block).
  if argv[:1] == ["init"]:
    from . import initcmd, watchdog
    watchdog.arm_for("init", watchdog_flags)
    atexit.register(watchdog.report)
    initcmd.main(argv[1:])
    return
  if argv[:1] == ["install"]:
    from . import installcmd, watchdog
    watchdog.arm_for("install", watchdog_flags)
    atexit.register(watchdog.report)
    installcmd.main(argv[1:])
    return
  if argv[:1] == ["setup-skill"]:
    from . import skillcmd
    skillcmd.main(argv[1:])
    return
  if argv[:1] in (["--version"], ["-V"]):
    from . import updatecmd
    print(updatecmd.version_string())
    return
  if argv[:1] == ["update"]:
    from . import updatecmd, watchdog
    watchdog.arm_for("update", watchdog_flags)
    atexit.register(watchdog.report)
    updatecmd.update(argv[1:])
    return
  # cache-build runs INSIDE the conan cache (a published recipe's
  # build() invokes it): there IS a project there (the exported
  # sources), but bootstrapping a venv into the cache is exactly what
  # must not happen — so it stays on the stdlib lane with its kin.
  if argv[:1] == ["cache-build"]:
    from . import cachebuild, watchdog
    watchdog.arm_for("cache-build", watchdog_flags)
    atexit.register(watchdog.report)
    cachebuild.main(argv[1:])
    return

  from . import bootstrap, config, watchdog

  # Outside a project (no buildutil.toml anywhere above) every other
  # subcommand is meaningless AND bootstrap would plant a _pyvenv in
  # whatever cwd happens to be — refuse before either can happen.
  config.require_project()
  os.chdir(config.REPO_ROOT)
  bootstrap.ensure()

  # Imported only after the venv is live — these pull in typer +
  # conan-aware code. Importing each command module registers its
  # subcommands on `app`.
  from .app import app
  from .commands import (build, test, run, bench, coverage,  # noqa: F401
                         analyze, module, vscode, tool,      # noqa: F401
                         extend, godbolt, publish, configcmd)  # noqa: F401

  atexit.register(watchdog.report)
  app(prog_name="buildutil")


if __name__ == "__main__":
  main()
