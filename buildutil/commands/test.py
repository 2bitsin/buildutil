"""buildutil commands: test, cache-clean."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import typer

from ..app import (app, _compiler_option, _option_option,
                   _no_parallel_option, _retired_parallel_option,
                   _renamed_no_upload_option,
                   pytest_args_from_options)
from ..engine import *
from .. import config



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
    help="Modules/labels to test (e.g. 'utilities'); overrides configured exclude_labels. "
         "If omitted, every module subject to configured label exclusions.",
  ),
  release: bool = typer.Option(
    False, "--release", help="Optimized build (default)."),
  debug: bool = typer.Option(False, "--debug", help="Debug build."),
  relwithdebinfo: bool = typer.Option(
    False, "--relwithdebinfo", help="Optimized build with debug info."),
  filter: str = typer.Option(
    "", "--filter", "-f",
    help="Regex passed to ctest -R, matched against test names.",
  ),
  label_exclude: list[str] = typer.Option(
    [], "--label-exclude",
    help="Regex passed to ctest -LE to exclude labels. Repeatable; replaces "
         "configured exclude_labels defaults, including with explicit targets.",
  ),
  quiet: bool = typer.Option(
    False, "--quiet", "-q",
    help="Suppress per-failure ctest detail; print only the pass/fail "
         "summary + the failed-test roster + the result banner. Useful "
         "when many known failures would otherwise bury the summary.",
  ),
  timeout: float = typer.Option(
    60.0, "--timeout",
    help="Per-test timeout (seconds) for every entry that declares none of "
       "its own. A test exceeding it is killed and reported as a failure "
       "rather than hanging the whole run; a python suite that needs "
       "longer declares it in buildutil.toml's [test.timeout] and keeps "
       "that.",
  ),
  no_parallel: bool = _no_parallel_option(),
  retired_parallel: bool = _retired_parallel_option(),
  jobs: int = typer.Option(
    0, "--jobs", "-j",
    help="Parallel ctest workers. 0 (default) = 0.7 x the core count, at "
         "least 1.",
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
         "pytest under tools/, without build directory/profile environment "
         "variables. Symmetric with --no-pytest.",
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
  against the tree already in the build dir. Python lanes get its bin
  directory on PATH, BUILDUTIL_BUILD_DIR and BUILDUTIL_PROFILE."""
  build_type = _resolve_build_type(release, debug, relwithdebinfo)
  pytest_extra = pytest_args_from_options(test_option)

  if no_native:
    pytest_env = os.environ.copy()
    pytest_env.pop("BUILDUTIL_BUILD_DIR", None)
    pytest_env.pop("BUILDUTIL_PROFILE", None)
    _run_pytests(env=pytest_env, extra_args=pytest_extra)
    return
  _enter(conan_home)
  _select_compiler(compiler, "auto")
  settings = _detect_settings(build_type)
  build_dir = Path("_build") / _profile_name(settings)
  typer.echo(_profile_line(build_dir.name,
                           _profile_origin(release, debug, relwithdebinfo)))
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
    _conan_install(profile, build_dir)
    _cmake_configure(build_dir, build_type, tests=True, bench=False)
    try:
      _cmake_build(build_dir, [f"{t}-tests" for t in targets])
    except subprocess.CalledProcessError:
      _status("BUILD FAILED")                # compile/link error — no tests ran
      raise typer.Exit(1)
    if not skip_dependency_upload:
      _upload_to_remote([build_dir / "conan-graph.json"])

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
  ctest_cmd += _label_exclude_args(targets, label_exclude)
  ctest_cmd += parallel_args(no_parallel, jobs)
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
    pytest_env = os.environ.copy()
    pytest_env.update(
      PATH=os.pathsep.join([str(build_dir.resolve() / "bin"),
                            *([pytest_env["PATH"]] if pytest_env.get("PATH") else [])]),
      BUILDUTIL_BUILD_DIR=str(build_dir.resolve()),
      BUILDUTIL_PROFILE=_profile_name(settings),
    )
    try:
      _run_pytests(env=pytest_env, extra_args=pytest_extra)
    except subprocess.CalledProcessError:
      _status("PYTEST FAILED")
      raise typer.Exit(1)

  if not no_build and not targets and not filter:
    _run_package_smoke(profile)
  _status(f"OK — {summary}" if summary else "OK")


def _label_exclude_args(targets: list[str], patterns: list[str]) -> list[str]:
  labels = config.PROJECT["test_exclude_labels"]
  if not targets and not patterns and labels:
    alt = "|".join(re.escape(label) for label in labels)
    patterns = [f"^({alt})$"]
  return [arg for pattern in patterns for arg in ("-LE", pattern)]


def _run_package_smoke(profile: Path) -> None:
  # Owner requirement: an unqualified test run must prove the package too,
  # exporting the built tree and consuming it through test_package/.
  # Targeted/filtered runs skip this, as does --no-build because it builds.
  from .. import packaging
  if not packaging.configured() or not packaging.has_package_test():
    return
  try:
    pkg_version, _ = packaging.resolve_version(bump=False, isatty=False)
  except SystemExit as e:
    typer.echo(f"package test skipped: {e}")
    return
  host, build_prof = _host_build_profiles(profile)
  shared = packaging.shared_requested()
  reference = packaging.ref(pkg_version, user="buildutil", channel="smoke")
  try:
    packaging.export_pkg(pkg_version, host, build_prof, shared=shared,
                         user="buildutil", channel="smoke")
    typer.echo(f"package test: {reference}")
    packaging.run_package_test(pkg_version, host, build_prof, shared=shared,
                               user="buildutil", channel="smoke")
  except subprocess.CalledProcessError:
    _status("PACKAGE TEST FAILED")
    raise typer.Exit(1)
  finally:
    subprocess.check_call(["conan", "remove", reference, "-c"])



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
