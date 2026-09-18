"""buildutil commands: bench.

ONE verb, two shapes behind the toml seam. By default it builds the
`*.bench.cpp` google-benchmark targets (BUILD_BENCHMARKING on) and runs
every `${module}-benches` executable. A project may instead name a
python bench suite — `[bench] suite = "inspector.bench"` in
buildutil.toml (bossdeux's boot/workload suite) — and then bench builds
release and launches `python -m <suite>` with tools/ on PYTHONPATH,
exactly the pre-extraction behavior.

Before 0.4.0 BOTH shapes were registered as `bench` (this module's
bossdeux-specific one silently shadowed the generic runner in test.py),
so a fresh project's bench died on bossdeux's `inspector.bench` import.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import typer

from ..app import (app, _compiler_option, _option_option,
                   _renamed_no_upload_option)
from ..config import PROJECT
from ..engine import *


@app.command()
def bench(
  targets: list[str] = typer.Argument(
    None,
    help="Module names (e.g. hello) whose ${target}-benches "
         "executables get built and launched. Default: every module "
         "that has a *.bench.cpp file. Ignored in suite mode."),
  release: bool = typer.Option(
    True, "--release/--debug",
    help="Build flavor. Release is the default — running a Debug build "
         "through google-benchmark gives wildly misleading numbers."),
  filter: str = typer.Option(
    "", "--filter",
    help="Forwarded to each bench binary as --benchmark_filter=<re>."),
  perf: bool = typer.Option(
    False, "--perf",
    help="Profile each run under `perf record` and print a `perf "
         "report` breakdown. Forces a RelWithDebInfo build so the "
         "profile carries symbols and line info."),
  repeats: int = typer.Option(
    3, "--repeats",
    help="Suite mode only: runs per scenario; the fastest is kept."),
  only: str = typer.Option(
    None, "--only",
    help="Suite mode only: comma-separated scenario names to run."),
  json_out: str = typer.Option(
    None, "--json",
    help="Suite mode only: also write the JSON report to this path."),
  no_build: bool = typer.Option(
    False, "--no-build",
    help="Suite mode only: skip the build, benchmark what's there."),
  no_upload: bool = _renamed_no_upload_option(),
  skip_dependency_upload: bool = typer.Option(
    False, "--skip-dependency-upload-so-everyone-rebuilds-from-source",
    help="Suite mode only: skip caching the built dependencies in the conan "
         "remote. Only for a real reason: every later clean build then "
         "recompiles them."),
  conan_home: str = typer.Option(
    None, "--conan-home",
    help="Override the CONAN_HOME path."),
  compiler: str = _compiler_option(),
  option: list[str] = _option_option(),
):
  """Build the *.bench.cpp targets and run them (or the project's
  declared python bench suite).

  Bench targets pull google-benchmark in as a test-tier conan dep and
  produce one `${module}-benches` executable per module that has a
  `*.bench.cpp` file. The cmake side flips on BUILD_BENCHMARKING for
  the duration of the configure so `Require(... BENCH ...)` deps
  resolve. With `[bench] suite = "pkg.module"` in buildutil.toml the
  project's own python suite runs instead.
  """
  suite = PROJECT["bench_suite"]
  _enter(conan_home)
  if suite:
    if not no_build:
      _select_compiler(compiler, "auto")
      build_type = _resolve_build_type(release, not release)
      settings = _detect_settings(build_type)
      profile = _ensure_profile(settings)
      build_dir = Path("_build") / _profile_name(settings)
      _warn_dependency_upload_skipped(skip_dependency_upload)
      _conan_install(profile)
      _cmake_configure(build_dir, build_type, tests=True, bench=False)
      _cmake_build(build_dir)
      if not skip_dependency_upload:
        _upload_to_remote()
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
      p for p in ("tools", env.get("PYTHONPATH", "")) if p)
    command = [sys.executable, "-m", suite, "--repeats", str(repeats)]
    if only:
      command += ["--only", *only.split(",")]
    if json_out:
      command += ["--json", json_out]
    raise typer.Exit(code=subprocess.call(command, env=env))

  _select_compiler(compiler, "auto")
  if targets and len(targets) == 1:
    # remember the filter for this module's vscode prompt + launch args
    from .. import vscode as _vscode
    _vscode.record_input("benchfilter", targets[0], filter)
  build_type = "RelWithDebInfo" if perf else (
    "Release" if release else "Debug")
  settings = _detect_settings(build_type)
  profile = _ensure_profile(settings)
  build_dir = Path("_build") / _profile_name(settings)

  _conan_install(profile)
  _cmake_configure(build_dir, build_type, tests=False, bench=True)
  _cmake_build(build_dir)

  bin_dir = build_dir / "sources"
  bench_bins = sorted(bin_dir.rglob("*-benches"))
  bench_bins = [p for p in bench_bins if os.access(p, os.X_OK)]
  if targets:
    wanted = {f"{t}-benches" for t in targets}
    bench_bins = [p for p in bench_bins if p.name in wanted]
  if not bench_bins:
    typer.echo("buildutil bench: no *-benches executables found")
    raise typer.Exit(code=1)
  for bin in bench_bins:
    typer.echo(f"\n=== {bin.name} ===")
    cmd = [str(bin)]
    if filter:
      cmd.append(f"--benchmark_filter={filter}")
    if perf:
      data = build_dir / f"{bin.name}.perf.data"
      subprocess.check_call(
        ["perf", "record", "-o", str(data), "--", *cmd])
      subprocess.check_call(
        ["perf", "report", "-i", str(data), "--stdio",
         "--no-children", "--percent-limit", "1"])
    else:
      subprocess.check_call(cmd)
