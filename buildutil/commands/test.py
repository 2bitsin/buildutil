"""buildutil commands: test, cache-clean."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import typer

from ..app import (app, _compiler_option, _option_option,
                   _renamed_no_upload_option,
                   pytest_args_from_options)
from ..engine import *
from .. import options as project_options



# Where buildutil_pytest.py leaves each suite's case counts, relative to
# the build directory.
PYTEST_REPORTS = Path("Testing") / "buildutil-pytest"


def pytest_suite_lines(build_dir: Path) -> list[str]:
  """One line per python suite that ran, as pytest itself counts cases.

  A suite is ONE ctest entry, so `Passed` is all the ctest summary can
  say about it -- a suite whose every case skipped for want of a corpus
  reads exactly like a suite that proved something."""
  lines = []
  for report in sorted((build_dir / PYTEST_REPORTS).glob("*.json")):
    counts = json.loads(report.read_text())
    tally = ", ".join(f"{n} {outcome}" for outcome, n in sorted(counts.items()))
    lines.append(f"{report.stem}: {tally or 'no cases'}")
  return lines


@app.command()
def test(
  targets: list[str] = typer.Argument(
    None,
    help="Modules to test (e.g. 'utilities'). If omitted, every module.",
  ),
  release: bool = typer.Option(False, "--release", help="Optimized build (default)."),
  debug: bool = typer.Option(False, "--debug", help="Debug build."),
  filter: str = typer.Option(
    "", "--filter", "-f",
    help="Regex passed to ctest -R, matched against test names.",
  ),
  quiet: bool = typer.Option(
    False, "--quiet", "-q",
    help="Suppress per-failure ctest detail; print only the pass/fail "
         "summary + the failed-test roster + the result banner. Useful "
         "when many known failures would otherwise bury the summary.",
  ),
  timeout: float = typer.Option(
    60.0, "--timeout",
    help="Per-test timeout (seconds). A test exceeding this is killed and "
       "reported as a failure rather than hanging the whole run. "
       "Tune up if a test is genuinely slow; default catches "
       "deadlocks early.",
  ),
  parallel: bool = typer.Option(
    False, "--parallel",
    help="Run ctest across multiple cores. OFF by default — serial is the "
       "safety floor so a runaway test can't storm the box; the per-test "
       "--timeout still bounds a single hang.",
  ),
  jobs: int = typer.Option(
    0, "--jobs", "-j",
    help="Parallel job count when --parallel is set. 0 (default) = all cores.",
  ),
  no_upload: bool = _renamed_no_upload_option(),
  skip_dependency_upload: bool = typer.Option(
    False, "--skip-dependency-upload-so-everyone-rebuilds-from-source",
    help="Skip caching the built dependencies in the conan remote. Only for a "
         "real reason: every later clean build then recompiles them.",
  ),
  no_pytest: bool = typer.Option(
    False, "--no-pytest",
    help="Skip the python pytest run under tools/. Implied when "
         "explicit `targets` are given (those filter ctest only).",
  ),
  no_native: bool = typer.Option(
    False, "--no-native",
    help="Skip the C++ build and ctest entirely — run only the python "
         "pytest under tools/. Symmetric with --no-pytest.",
  ),
  no_build: bool = typer.Option(
    False, "--no-build",
    help="Skip the incremental build/install step; just run the tests "
         "already in the build dir (which must exist). The conan "
         "package test is skipped with it — it builds.",
  ),
  test_option: list[str] = typer.Option(
    [], "--test-option", "-O",
    help="Pass an option through to pytest. Form: 'name[:value]'. "
         "Bool flags drop the value ('name' → '--name'); valued options "
         "use 'name:value' → '--name=value'. Repeatable. Examples: "
         "-O update-goldens, -O k:two-groups (= pytest -k two-groups).",
  ),
  conan_home: str = typer.Option(
    None, "--conan-home",
    help="Override the CONAN_HOME path. Defaults to $CONAN_HOME if set, "
       "else <repo>/_conanhome.",
  ),
  compiler: str = _compiler_option(),
  option: list[str] = _option_option(),
):
  """Build (incrementally if possible) and run ctest, then pytest for
  every `tools/*` directory that ships a `pytest.ini`. The pytest step
  is skipped when ctest `targets` are given or `--no-pytest` is set;
  `--no-native` flips it around — run only pytest, skip the C++ side.
  `--no-build` keeps both test steps and drops the build, running them
  against the tree already in the build dir."""
  pytest_extra = pytest_args_from_options(test_option)

  if no_native:
    _run_pytests(extra_args=pytest_extra)
    return
  _enter(conan_home)
  _select_compiler(compiler, "auto")
  # tests default to RELEASE (ruling 2026-07-17): the release tree is
  # usually the warm one, and heavy suites run ~5x faster; --debug still
  # opts into the instrumented flavor
  # Follow whatever `build` last acted on when neither flag is given.
  # These two commands are used as a pair and defaulted differently --
  # build to Debug, test to Release -- so a fix could land in one tree
  # while the suite ran the other, with nothing in either command's
  # output saying which. Release stays the default on a tree that has
  # never been built: heavy suites run ~5x faster there, and there is no
  # recent build to disagree with.
  if release or debug:
    build_type = _resolve_build_type(release, debug)
  else:
    build_type = last_build_type() or "Release"
  settings = _detect_settings(build_type)
  typer.echo(f"profile: {_profile_name(settings)}"
             f"{project_options.profile_note()}")
  build_dir = Path("_build") / _profile_name(settings)
  targets = targets or []
  if len(targets) == 1:
    # remember the filter for this module's vscode prompt + launch args
    from .. import vscode as _vscode
    _vscode.record_input("filter", targets[0], filter)

  profile = None
  if no_build:
    # CTestTestfile.cmake is what ctest itself opens: present means the
    # tree was configured WITH tests, which a bare build dir is not.
    if not (build_dir / "CTestTestfile.cmake").exists():
      typer.echo(
        f"buildutil test --no-build: no built test tree at {build_dir}"
        " — `buildutil build` makes it", err=True)
      raise typer.Exit(1)
  else:
    profile = _ensure_profile(settings)
    _warn_dependency_upload_skipped(skip_dependency_upload)
    _conan_install(profile)
    _cmake_configure(build_dir, build_type, tests=True, bench=False)
    try:
      _cmake_build(build_dir, [f"{t}-tests" for t in targets])
    except subprocess.CalledProcessError:
      _status("BUILD FAILED")                # compile/link error — no tests ran
      raise typer.Exit(1)
    if not skip_dependency_upload:
      _upload_to_remote()

  ctest_cmd = [
    "ctest", "--test-dir", str(build_dir),
    *([] if quiet else ["--output-on-failure"]),
    # Fail when the filter matches zero tests or gtest discovery
    # found nothing — otherwise ctest exits 0 silently and we
    # mistake "broken discovery" for "no tests run because empty".
    "--no-tests=error",
    # Per-test wall-clock cap. A hung test exits as a failure
    # instead of stalling the whole run. CTest's --timeout flag
    # applies to every test individually.
    "--timeout", str(timeout),
  ]
  if targets:
    # ctest -L is a regex on test labels; build an anchored alternation.
    alt = "|".join(re.escape(t) for t in targets)
    ctest_cmd += ["-L", f"^({alt})$"]
  if filter:
    ctest_cmd += ["-R", filter]
  ctest_cmd += parallel_args(parallel, jobs)
  # Nothing older than this run may be counted as part of it.
  shutil.rmtree(build_dir / PYTEST_REPORTS, ignore_errors=True)
  # Capture ctest so the result banner can quote its pass/fail summary —
  # one invocation then reports its own count, no piping/grepping.
  ctest = subprocess.run(ctest_cmd, capture_output=True, text=True)
  if quiet:                                            # only the summary + failed roster
    for ln in ctest.stdout.splitlines():
      if "tests passed" in ln or "(Failed)" in ln:
        print(ln)
  else:
    sys.stdout.write(ctest.stdout)
  sys.stderr.write(ctest.stderr)
  for line in pytest_suite_lines(build_dir):
    typer.echo(f"pytest {line}")
  summary = next((ln.strip() for ln in ctest.stdout.splitlines() if "tests passed" in ln), "")
  if ctest.returncode != 0:
    _status(f"TESTS FAILED — {summary}" if summary else "TESTS FAILED")
    raise typer.Exit(1)

  # Python tests — skipped when ctest `targets` narrow the C++ run or
  # when --no-pytest is set.
  if not (no_pytest or targets):
    try:
      _run_pytests(extra_args=pytest_extra)
    except subprocess.CalledProcessError:
      _status("PYTEST FAILED")
      raise typer.Exit(1)

  # The conan package test — part of the unqualified run (owner req):
  # a full `buildutil test` on a packaged project also proves the
  # PACKAGE, by export-pkg of the tree just built + test_package/
  # consuming it from the cache. Targeted/filtered runs skip it, same
  # as they skip pytest; --no-build skips it because it builds.
  from .. import packaging
  if (not no_build and not targets and not filter and packaging.configured()
      and packaging.has_package_test()):
    host, build_prof = _host_build_profiles(profile)
    try:
      # no bump, non-interactive: a TEST run consumes no build number
      # and never prompts — with no version inferrable (no semver tag
      # yet) the package test is skipped with the reason, not an error
      pkg_version, _ = packaging.resolve_version(bump=False, isatty=False)
    except SystemExit as e:
      typer.echo(f"package test skipped: {e}")
      pkg_version = None
    if pkg_version:
      shared = packaging.shared_requested()
      try:
        reference = packaging.export_pkg(pkg_version, host, build_prof,
                                         shared=shared)
        typer.echo(f"package test: {reference}")
        packaging.run_package_test(pkg_version, host, build_prof,
                                   shared=shared)
      except subprocess.CalledProcessError:
        _status("PACKAGE TEST FAILED")
        raise typer.Exit(1)

  _status(f"OK — {summary}" if summary else "OK")



@app.command(name="cache-clean")
def cache_clean(
  conan_home: str = typer.Option(
    None, "--conan-home",
    help="Override the CONAN_HOME path."),
):
  """Prune the conan cache's non-critical folders.

  `conan cache clean` drops the build / source / download-tgz temp
  directories that accumulate every pipeline run, while keeping the
  installed package binaries. Reclaims the bulk of the cache growth
  without forcing dependency re-downloads. Run as the pipeline's
  final always-on stage so the shared runner cache doesn't leak.
  """
  _enter(conan_home)
  subprocess.check_call(["conan", "cache", "clean"])
