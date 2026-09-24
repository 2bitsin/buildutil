"""buildutil commands: coverage."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import typer

from ..app import (app, _compiler_option, _option_option,
                   _no_parallel_option, _retired_parallel_option,
                   pytest_args_from_options)
from ..config import MODULE_DEFINE_PREFIX, PROJECT
from ..engine import *




@app.command()
def coverage(
  filter: str = typer.Option(
    "", "--filter", "-f",
    help="Regex passed to ctest -R, matched against test names. "
       "Coverage is computed only over what the matching tests exercise.",
  ),
  timeout: float = typer.Option(
    60.0, "--timeout",
    help="Per-test timeout (seconds).",
  ),
  no_parallel: bool = _no_parallel_option(),
  retired_parallel: bool = _retired_parallel_option(),
  jobs: int = typer.Option(
    0, "--jobs", "-j",
    help="Parallel ctest workers. 0 (default) = 0.7 x the core count, at "
         "least 1.",
  ),
  fail_under: float = typer.Option(
    0.0, "--fail-under",
    help="Exit non-zero if overall line coverage falls below this percentage. "
       "0 (default) disables the gate.",
  ),
  fail_per_file: float = typer.Option(
    0.0, "--fail-per-file",
    help="Exit non-zero if ANY non-skipped module's line coverage falls below "
       "this percentage. Each file in the report is checked individually. "
       "0 (default) disables the per-file gate.",
  ),
  skip: list[str] = typer.Option(
    [], "--skip",
    help="Path (relative to repo root, matched on suffix) excluded from "
       "the per-file gate. Repeatable. Typical use: opt out modules that "
       "don't have tests yet so they don't block the gate.",
  ),
  label: str = typer.Option(
    "", "--label", "-L",
    help="Run only tests carrying this ctest label (e.g. a module name). "
       "Coverage then reflects just that module's own tests — fast, and "
       "avoids the full corpus when verifying a single module.",
  ),
  no_pytest: bool = typer.Option(
    False, "--no-pytest",
    help="Skip the python pytest suites and report on what ctest alone "
       "covers. Instrumented code runs many times slower than release, so "
       "a python suite driving it is usually the slow leg by a wide "
       "margin — this is the switch for iterating on a C++ module's own "
       "coverage. NOT for the gate: a module covered only from python "
       "reads near-0% without it.",
  ),
  test_option: list[str] = typer.Option(
    [], "--test-option", "-O",
    help="Pass an option through to pytest, same form as `test -O`. "
       "E.g. `-O \"m:not slow\"` to drop a marker's tests from the "
       "pytest leg.",
  ),
  conan_home: str = typer.Option(
    None, "--conan-home",
    help="Override the CONAN_HOME path.",
  ),
  compiler: str = _compiler_option(),
  option: list[str] = _option_option(),
):
  """Build with --coverage instrumentation, run ctest, then emit a gcovr report.

  Shares the standard Debug build dir (`_build/<profile>/`) with the
  BUILDUTIL_COVERAGE option flipped on — so coverage and a plain Debug
  build are mutually exclusive, and switching between them is a
  re-configure cmake handles cleanly. Linux gcc / macOS clang only;
  the cmake config rejects MSVC.
  """
  _enter(conan_home)
  _select_compiler(compiler, "auto")

  settings = _detect_settings("Debug")
  profile = _ensure_profile(settings)
  build_dir = Path("_build") / _profile_name(settings)
  report_dir = (Path("_build") / "coverage-report").resolve()
  report_dir.mkdir(parents=True, exist_ok=True)
  # gcov writes `*##HASH.gcov` temps into its CWD, and gcovr runs
  # gcov from whatever directory it picks as the "working dir". By
  # default that's --root (REPO_ROOT), so the temps would transit
  # the source tree. Pointing --gcov-object-directory at this
  # dedicated dir makes gcovr run gcov from here instead — and
  # since -fprofile-abs-path bakes absolute paths into the .gcno,
  # gcov still resolves .gcda/.gcno/source fine from an unrelated
  # cwd. The temps therefore land here and nowhere else.
  artifacts_dir = (Path("_build") / "coverage-artifacts").resolve()
  artifacts_dir.mkdir(parents=True, exist_ok=True)

  _conan_install(profile, build_dir)
  _cmake_configure(build_dir, "Debug", tests=True, bench=False,
                   coverage=True)
  _cmake_build(build_dir)

  # Wipe any .gcda from a previous coverage run before re-running
  # ctest. gcc stamps .gcda with the .gcno's checksum, and if the
  # build recompiled even one TU since the last run, the surviving
  # .gcda is from a binary that doesn't match the new .gcno —
  # gcov bails with "stamp mismatch" / "no_working_dir_found" and
  # gcovr aborts. Cheaper than a full clean.
  stale_gcda = list(build_dir.rglob("*.gcda"))
  for gcda in stale_gcda:
    gcda.unlink()
  if stale_gcda:
    typer.echo(f"coverage: cleared {len(stale_gcda)} stale .gcda")

  ctest_cmd = [
    "ctest", "--test-dir", str(build_dir),
    "--output-on-failure", "--no-tests=error",
    "--timeout", str(timeout),
  ]
  if filter:
    ctest_cmd += ["-R", filter]
  if label:
    ctest_cmd += ["-L", label]
  ctest_cmd += parallel_args(no_parallel, jobs)
  subprocess.check_call(ctest_cmd)

  # A project's pytest suites may drive native code through a pybind
  # bridge whose import path prefers _install/lib/python -- the RELEASE
  # module, carrying no instrumentation. Left alone, such tests exercise
  # the bridge TUs thoroughly and contribute not one .gcda line, so they
  # report 0% no matter what the suite does. [coverage] bridge_dirs
  # in buildutil.toml names those build subdirs; the first one present
  # is exported as <module_define_prefix>_BRIDGE_DIR so the suite
  # imports the instrumented module for this run.
  bridge_dir = next(
    (d for rel in PROJECT["coverage_bridge_dirs"]
     if (d := (build_dir / rel).resolve()).is_dir()), None)
  if no_pytest:
    typer.echo("coverage: --no-pytest -- python-driven coverage is NOT "
               "collected; modules covered only from python read near-0%")
  else:
    pytest_env = dict(os.environ)
    if bridge_dir:
      pytest_env[f"{MODULE_DEFINE_PREFIX}_BRIDGE_DIR"] = str(bridge_dir)
    elif PROJECT["coverage_bridge_dirs"]:
      typer.echo("coverage: no instrumented bridge under "
                 f"{build_dir} ({PROJECT['coverage_bridge_dirs']}) -- "
                 "python-driven coverage will not be collected")
    _run_pytests(env=pytest_env,
                 extra_args=pytest_args_from_options(test_option))

  # gcovr collects .gcda data from the build tree and produces both a
  # text summary (stdout) and a navigable HTML report. Filter to our
  # own sources/ tree; skip *.test.cpp files since coverage of test
  # scaffolding isn't interesting.
  html_index = report_dir / "index.html"
  gcovr_cmd = [
    "gcovr",
    "--root", str(Path(".").resolve()),
    # Run gcov from the artifacts dir so its *.gcov temps land
    # there instead of in REPO_ROOT (see artifacts_dir comment).
    "--gcov-object-directory", str(artifacts_dir),
    # Orphaned .gcno from removed/renamed sources (a deleted module or
    # test) otherwise abort the whole run — skip notes whose source
    # gcov can't resolve instead of failing on them.
    "--gcov-ignore-errors", "no_working_dir_found",
    # gcc PR 68080: gcov's branch counters can wrap and come back NEGATIVE
    # ("branch 6 taken -96626"), and gcovr treats that as a fatal parse error
    # and writes no report at all. It is reachable here because the pytest
    # leg boots real guests through the instrumented bridge -- tens of
    # millions of guest instructions, and the hot branches in the executor
    # and decoder overflow. Warn once per file and carry on: the wrap is in
    # the BRANCH counters, and the per-file gate below measures LINES.
    "--gcov-ignore-parse-errors", "negative_hits.warn_once_per_file",
    "--filter", "sources/",
    # Exclusions: *.test.* is test scaffolding, not interesting on its
    # own; a project adds its vendored code via [coverage] exclude in
    # buildutil.toml (gcovr regexes). The Objective-C++ spellings are
    # here for the same reason the C++ ones are -- a macOS module's
    # platform.macos.test.mm is scaffolding too, and gcovr would
    # otherwise count it against the project.
    "--exclude", r".*\.test\.cpp$",
    "--exclude", r".*\.test\.hpp$",
    "--exclude", r".*\.test\.mm$",
    "--exclude", r".*\.test\.m$",
    *(arg for rx in PROJECT["coverage_exclude"] for arg in ("--exclude", rx)),
    "--print-summary",
    "--html-details", str(html_index),
    "--txt", str(report_dir / "summary.txt"),
    # Cobertura XML is what GitLab's coverage_report widget consumes;
    # JSON is handy for diffing across runs.
    "--cobertura", str(report_dir / "coverage.xml"),
    "--json", str(report_dir / "coverage.json"),
    # Per-file aggregate numbers (honors GCOVR_EXCL_LINE and friends).
    # The detailed coverage.json above does NOT — it's raw per-line data.
    "--json-summary", str(report_dir / "summary.json"),
  ]
  if fail_under > 0.0:
    gcovr_cmd += ["--fail-under-line", str(fail_under)]
  gcovr_cmd.append(str(build_dir.resolve()))
  try:
    subprocess.check_call(gcovr_cmd)
  finally:
    # gcovr unlinks each .gcov as it parses it; sweep any
    # abnormal-exit stragglers from the artifacts dir, plus the
    # historical leak spots in case gcovr ever runs without the
    # --gcov-object-directory pin above.
    for sweep in (artifacts_dir, REPO_ROOT, REPO_ROOT.parent, Path.home()):
      for leaked in sweep.glob("*.gcov"):
        leaked.unlink()
  print(f"\ncoverage report: {html_index}")

  # Per-file gate. gcovr's own --fail-under-line works on the global
  # rollup; we want each tested module to clear the bar individually,
  # so post-process the JSON summary (which honors GCOVR_EXCL_LINE).
  # Files in --skip are not counted against the gate (useful for
  # modules without tests yet — they sit at 0% and would otherwise
  # mask everything else).
  if fail_per_file > 0.0:
    import json
    summary_json = report_dir / "summary.json"
    with summary_json.open() as fp:
      data = json.load(fp)
    skip_suffixes = tuple(skip)
    disabled = _disabled_modules()
    failures: list[tuple[str, float]] = []
    print(f"\nper-file gate: each module ≥ {fail_per_file}% line coverage")
    for f in sorted(data.get("files", []), key=lambda e: e["filename"]):
      path = f["filename"]
      parts = path.split("/")
      if len(parts) > 2 and parts[0] == "sources" and parts[1] in disabled:
        print(f"  SKIP {path}  (module disabled in _bdudata/modules.ini)")
        continue
      if any(path.endswith(s) for s in skip_suffixes):
        print(f"  SKIP {path}")
        continue
      total = f.get("line_total", 0)
      hit   = f.get("line_covered", 0)
      if total == 0:
        continue
      pct = f.get("line_percent", 100.0 * hit / total)
      mark = "OK  " if pct >= fail_per_file else "FAIL"
      print(f"  {mark} {pct:6.2f}%  {path}  ({hit}/{total})")
      if pct < fail_per_file:
        failures.append((path, pct))
    if failures:
      print(f"\nper-file coverage gate failed ({len(failures)} module(s) below "
            f"{fail_per_file}%):")
      for path, pct in failures:
        print(f"  {pct:6.2f}%  {path}")
      raise typer.Exit(code=1)
