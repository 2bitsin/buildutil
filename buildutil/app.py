"""The shared typer application plus CLI-surface helpers: the root callback
(output tee), the --compiler option factory, and the result banner."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import typer



app = typer.Typer(add_completion=False, no_args_is_help=True)



def _clear_logs_beside(path: Path) -> None:
  """Delete every *.log next to `path`, so the directory holds one run's log."""
  for stale in path.parent.glob("*.log"):
    stale.unlink(missing_ok=True)


def _enable_log_tee(path: Path, clear_logs: bool = False) -> None:
  """Tee everything written to stdout/stderr — by this process and by
  every subprocess it spawns — to `path`, while keeping the original
  terminal output intact.

  Works by duping the real terminal fds, redirecting fd 1 and fd 2 to
  a pipe, and pumping that pipe to (terminal, file) in a background
  thread. Because subprocesses inherit fd 1 and fd 2, their output
  flows through the same pipe with no per-Popen interception.
  """
  import atexit
  import threading

  if getattr(_enable_log_tee, "_active", False):
    return
  _enable_log_tee._active = True

  path = Path(path).resolve()
  path.parent.mkdir(parents=True, exist_ok=True)
  if clear_logs:
    _clear_logs_beside(path)
  log = open(path, "ab", buffering=0)

  saved_out_fd = os.dup(1)
  saved_err_fd = os.dup(2)
  read_fd, write_fd = os.pipe()
  os.dup2(write_fd, 1)
  os.dup2(write_fd, 2)
  os.close(write_fd)

  def _pump() -> None:
    while True:
      try:
        chunk = os.read(read_fd, 4096)
      except OSError:
        break
      if not chunk:
        break
      os.write(saved_out_fd, chunk)
      log.write(chunk)
    try:
      os.close(read_fd)
    except OSError:
      pass
    log.close()

  pump = threading.Thread(target=_pump, daemon=True)
  pump.start()

  def _shutdown() -> None:
    try: sys.stdout.flush()
    except Exception: pass
    try: sys.stderr.flush()
    except Exception: pass
    # Restore originals so the pipe's write end has no holders left; the
    # reader thread then drains the pipe, sees EOF and exits. Only close
    # the saved fds AFTER the join -- the pump writes chunks to
    # saved_out_fd until EOF, so closing first races it into EBADF. If
    # the join times out, leak the two fds: the process is exiting.
    os.dup2(saved_out_fd, 1)
    os.dup2(saved_err_fd, 2)
    pump.join(timeout=2.0)
    if not pump.is_alive():
      os.close(saved_out_fd)
      os.close(saved_err_fd)

  atexit.register(_shutdown)



def _effective_max_errors(max_errors: int, fail_fast: bool) -> int:
  """Fold the two error-cap switches into a single -fmax-errors count for
  BUILDUTIL_MAX_ERRORS. An explicit --max-errors N (N>0) wins; --fail-fast
  alone means 1 (stop at the first error); neither set means 0 (off)."""
  if max_errors > 0:
    return max_errors
  return 1 if fail_fast else 0



# The budget policy (per-verb budgets, the arm-or-skip decision) lives in
# watchdog.py — stdlib-only, so the pre-project verbs (init, update) that
# run before this module CAN import share the exact same rules.
from .watchdog import watchdog_budget_for  # noqa: F401  (re-export: CLI + tests)


def pytest_args_from_options(options: list[str]) -> list[str]:
  """Translate `-O` forms into pytest arguments: 'name' is a bool flag
  (`--name`), 'name:value' a valued one (`--name=value`). Shared by `test`
  and `coverage` so `-O "m:not guest"` means the same thing in both.

  A ONE-CHARACTER name becomes a short option (`-k expr`, `-m expr`), which
  is not cosmetic: pytest registers `-k` and `-m` with no long form at all,
  so `--k=expr` is not an unknown flag -- argparse hands it to pytest as a
  PATH, and the run dies with "file or directory not found: --k=expr".
  """
  args: list[str] = []
  for option in options:
    if ":" in option:
      name, _, value = option.partition(":")
      args += [f"-{name}", value] if len(name) == 1 else [f"--{name}={value}"]
    else:
      args.append(f"-{option}" if len(option) == 1 else f"--{option}")
  return args


def _print_version(value: bool) -> None:
  if value:
    from . import updatecmd
    typer.echo(updatecmd.version_string())
    raise typer.Exit()


@app.callback()
def _root(
  ctx: typer.Context,
  version: bool = typer.Option(
    False, "--version", "-V", callback=_print_version, is_eager=True,
    help="Print the installed buildutil version + package path, exit."),
  write_log_to: Path = typer.Option(
    None, "--write-log-to",
    help="Tee all output — this process and every subprocess it "
         "spawns — to this file. Available on every subcommand; pass "
         "it BEFORE the subcommand: `./buildutil --write-log-to=PATH "
         "<subcommand>`."),
  clear_logs: bool = typer.Option(
    False, "--clear-logs",
    help="With --write-log-to: first delete every *.log beside it, so the "
         "directory holds only this run's log. Nothing to delete is fine."),
  max_errors: int = typer.Option(
    0, "--max-errors",
    help="Stop each compile after N errors (gcc -fmax-errors / clang "
         "-ferror-limit); 1 aborts on the first error. 0 = off (full "
         "output). Honoured by every build-running subcommand (build, "
         "test, bench, coverage, run); pass it BEFORE the subcommand: "
         "`./buildutil --max-errors 1 build`."),
  fail_fast: bool = typer.Option(
    False, "--fail-fast",
    help="Stop each compile at the FIRST error — sugar for --max-errors 1 "
         "(an explicit --max-errors N still wins). For the one-error-fix-"
         "rebuild loop during a rewrite. gcc -fmax-errors=1 / clang "
         "-ferror-limit=1; no-op on MSVC. Honoured by every build-running "
         "subcommand; pass it BEFORE the subcommand: "
         "`./buildutil --fail-fast build`."),
  jobs: int = typer.Option(
    80, "--jobs", "-j",
    help="Build parallelism as a PERCENT of the cores (cmake --build "
         "--parallel). Default 80. A tiny value gives a near-serial build "
         "(the count floors at 1 core), so the build halts at the FIRST "
         "failing file instead of letting parallel jobs already in flight "
         "spew more errors. Pair with --max-errors to stop dead on the "
         "first error with a minimal log. Pass it BEFORE the subcommand."),
  jump_to_error: int = typer.Option(
    0, "--jump-to-error",
    help="On a failed build, parse the gcc/clang output for error locations "
         "(file:line:col inside the project) and open the first N in the editor "
         "via `code -r -g`. N is this value; 0 = off. Anchored on the error "
         "itself, so it skips the include/instantiation chain and jumps to "
         "where the error is reported. Pass it BEFORE the subcommand."),
  no_watchdog: bool = typer.Option(
    False, "--no-watchdog",
    help="Escape hatch: run this one command without the wall-clock "
         "budget (warn 1min / error 2min / FAIL 3min). For known-long "
         "commands like coverage. Off by default in CI already (the job "
         "timeout is the bound). Legal ANYWHERE on the line — every "
         "subcommand accepts it: `buildutil coverage --no-watchdog`."),
  watchdog_budget: float = typer.Option(
    180.0, "--watchdog-budget",
    help="Move the watchdog's FAIL line to N seconds for this command "
         "(warn/error scale to 1/3 and 2/3 of it). An explicit budget arms "
         "the watchdog even in CI. Legal ANYWHERE on the line — every "
         "subcommand accepts it."),
  no_timing: bool = typer.Option(
    False, "--no-timing",
    help="Skip the wall-time statistics line every command prints on "
         "exit. Pass it BEFORE the subcommand."),
  no_conan_update: bool = typer.Option(
    False, "--no-conan-update",
    help="Resolve version ranges against the local conan cache only — "
         "skip the `--update` every install passes by default. The "
         "escape hatch for offline work or a deliberately frozen "
         "cache. Pass it BEFORE the subcommand."),
) -> None:
  """build driver — project named by buildutil.toml at the repo root.

  Also available with no project: `buildutil init` (scaffold one),
  `buildutil update` (self-upgrade with pip),
  `buildutil --version`.
  """
  from . import watchdog
  if no_timing:
    os.environ["BUILDUTIL_NO_TIMING"] = "1"
  if no_conan_update:
    os.environ["BUILDUTIL_NO_CONAN_UPDATE"] = "1"
  # The watchdog catches a build-system slowness bug during an interactive /
  # agent run. CI jobs have their own GitLab timeout as the real bound, so a
  # second budget there only false-fails legitimately-long jobs (the wine cross
  # build compiles every dep from source) -- off in CI unless asked for.
  # ParameterSource by NAME, not identity: newer typer vendors click, so
  # `import click` may not resolve at all (a slim `pip install typer` env),
  # and an enum imported from a different click than the one typer runs on
  # would compare unequal anyway.
  source = ctx.get_parameter_source("watchdog_budget")
  budget = watchdog_budget_for(
    ctx.invoked_subcommand, watchdog_budget,
    explicit=(getattr(source, "name", "") == "COMMANDLINE"),
    no_watchdog=no_watchdog, in_ci=bool(os.environ.get("CI")))
  if budget is not None:
    watchdog.arm(budget)
  if write_log_to is not None:
    _enable_log_tee(write_log_to, clear_logs=clear_logs)
  errors_cap = _effective_max_errors(max_errors, fail_fast)
  if errors_cap > 0:
    os.environ["BUILDUTIL_MAX_ERRORS"] = str(errors_cap)
  os.environ["BUILDUTIL_JOBS_PERCENT"] = str(max(1, min(jobs, 100)))
  if jump_to_error > 0:
    os.environ["BUILDUTIL_JUMP_TO_ERROR"] = str(jump_to_error)



def _compiler_option():
  """The shared --compiler typer Option; per-command default policy
  is passed to _select_compiler, not encoded here."""
  return typer.Option(
    None, "--compiler",
    help="Toolchain to build with: gcc, clang, apple-clang, msvc, "
         "wine-msvc, osxcross or emscripten. "
         "Default: autodetect the host compiler (analyze uses clang).")


def _apply_options(ctx: typer.Context, value: list[str]) -> list[str]:
  if ctx.resilient_parsing or not value:
    return value
  from . import options
  try:
    options.apply(value)
  except ValueError as error:
    typer.echo(f"buildutil {ctx.command.name}: {error}", err=True)
    raise typer.Exit(code=2)
  return value


def _option_option():
  """The shared --option flag: it records the choice for the whole run,
  so every verb that configures reads it without carrying it through."""
  return typer.Option(
    [], "--option", callback=_apply_options, metavar="NAME=VALUE",
    help="Choose one of the project options buildutil.toml declares, for "
         "this build: `--option contracts=off`. Booleans take "
         "on/off/true/false. Repeatable. buildutil injects every option "
         "as the compile definition PREFIX_NAME on every target of the "
         "project.")


def _refuse_renamed_no_upload(ctx: typer.Context, value: bool) -> bool:
  if value and not ctx.resilient_parsing:
    typer.echo(
      f"buildutil {ctx.command.name}: --no-upload was renamed to "
      "--skip-dependency-upload-so-everyone-rebuilds-from-source. Pass that "
      "if you really mean it; publish --no-upload is unchanged.", err=True)
    raise typer.Exit(code=2)
  return value


def _renamed_no_upload_option():
  """Hidden on the build verbs: typing --no-upload explains the rename."""
  return typer.Option(
    False, "--no-upload", hidden=True, is_eager=True,
    callback=_refuse_renamed_no_upload)
