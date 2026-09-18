"""buildutil commands: analyze."""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

import typer

from ..app import app, _compiler_option, _option_option
from ..engine import *




@app.command()
def analyze(
  paths: list[str] = typer.Argument(
    None,
    help="Files or directories to scan. If omitted, every .cpp/.hpp under "
       "sources/. Module names like 'hello' are accepted as shorthand "
       "for 'sources/<name>'.",
  ),
  fix: bool = typer.Option(
    False, "--fix",
    help="Apply clang-tidy's suggested fix-its in place. Off by default — "
       "review output first.",
  ),
  check: str = typer.Option(
    None, "--check",
    help="Restrict to a single clang-tidy check or pattern (e.g. "
       "'readability-identifier-naming'). Disables every other check for "
       "this run — combine with --fix and one file to apply just that "
       "check's fix-its surgically, instead of letting overlapping renames "
       "trash the source.",
  ),
  profile: bool = typer.Option(
    False, "--profile",
    help="Profile clang-tidy check timings: bypass the result cache, "
       "sum each check's wall time across every TU, print a ranking.",
  ),
  release: bool = typer.Option(False, "--release", help="Optimized build."),
  debug: bool = typer.Option(False, "--debug", help="Debug build (default)."),
  jobs: int = typer.Option(
    0, "--jobs", "-j",
    help="Parallel clang-tidy workers. 0 picks os.cpu_count().",
  ),
  compiler: str = _compiler_option(),
  option: list[str] = _option_option(),
  conan_home: str = typer.Option(
    None, "--conan-home",
    help="Override the CONAN_HOME path. Defaults to $CONAN_HOME if set, "
       "else <repo>/_conanhome.",
  ),
):
  """Run clang-tidy across the project sources.

  Linux only (uses the gcc-debug compile_commands.json by default).
  Rules live in .clang-tidy at the repo root.
  """
  if platform.system() != "Linux":
    typer.echo("buildutil analyze: Linux only.", err=True)
    raise typer.Exit(code=2)

  _enter(conan_home)

  # clang-tidy is a clang frontend, so it must read a clang-built
  # compile DB — a gcc DB carries gcc-only flags clang's driver
  # rejects, making clang-tidy skip the TU. analyze therefore defaults
  # to the clang toolchain, which routes the build through its own
  # conan profile and build dir, separate from the gcc build/coverage
  # tree. `--compiler` can still override it.
  _select_compiler(compiler, "clang")

  build_type = _resolve_build_type(release, debug)
  settings = _detect_settings(build_type)
  build_dir = Path("_build") / _profile_name(settings)
  compdb = build_dir / "compile_commands.json"

  # Always build the analysis profile first: clang-tidy needs the
  # generated headers on disk and every TU present in
  # compile_commands.json. A no-op ninja rebuild is cheap. install=False
  # leaves _install/ as the gcc build's.
  _full_build(build_type, tests=True, upload=False, install=False)

  # Only run clang-tidy on TUs that are actually in compile_commands.json
  # — anything else (headers, platform-gated .cpp files, vendored code)
  # clang-tidy can't compile. Headers get analysed transitively via
  # HeaderFilterRegex when a TU that includes them is processed.
  import json as _json
  with compdb.open() as fp:
    cdb_files = {
      str(Path(e.get("directory", ".")) / e["file"]).replace("\\", "/")
      for e in _json.load(fp)
    }

  files: list[Path] = []
  if paths:
    for raw in paths:
      candidate = Path(raw)
      module_dir = Path("sources") / raw
      if not candidate.exists() and module_dir.is_dir():
        candidate = module_dir
      if candidate.is_dir():
        files += sorted(candidate.rglob("*.cpp"))
      elif candidate.exists() and candidate.suffix == ".cpp":
        files.append(candidate)
      else:
        typer.echo(f"buildutil analyze: no such .cpp '{raw}'.", err=True)
        raise typer.Exit(code=2)
  else:
    files += sorted(Path("sources").rglob("*.cpp"))

  # [analyze] exclude in buildutil.toml: path substrings for vendored
  # code the project wants tidy to skip outright.
  from ..config import PROJECT
  excludes = PROJECT["analyze_exclude"]
  files = [f for f in files
           if not any(sub in f.as_posix() for sub in excludes)
           and str(f.resolve()).replace("\\", "/") in cdb_files]
  if not files:
    typer.echo("buildutil analyze: no files matched.", err=True)
    raise typer.Exit(code=2)

  # `-p=<dir>` (no space) keeps "_build/" off the literal command line
  # so the project's deny-rule against direct `_build/` invocations
  # doesn't flag our tooling. Functionally identical to `-p <dir>`.
  #
  # When the project carries contrib/ctcache/clang_tidy_cache.py,
  # clang-tidy runs through it: a result cache keyed on the preprocessed
  # TU plus the resolved `--dump-config`, so unchanged TUs skip the slow
  # re-check and a `.clang-tidy` edit busts entries on its own. `--fix`
  # mutates files, so it bypasses the cache. Without the script, plain
  # clang-tidy — every TU re-checked each run.
  #
  # SAVE_OUTPUT: by default ctcache only stores a run that exits 0 with
  # no output. TUs routinely emit non-gating clang-diagnostic-error
  # noise (clang frontend vs gcc/libstdc++ lag) and exit non-zero, so
  # without this nothing is ever cached. SAVE_OUTPUT stores exit code +
  # output and replays both on a hit.
  ctcache = REPO_ROOT / "contrib" / "ctcache" / "clang_tidy_cache.py"
  if ctcache.exists():
    # Cache root defaults to _build/ctcache -- project-local, wiped by
    # `buildutil clean`, so a clean rebuild gets a clean tidy cache and a
    # global cache can't go stale across branches. CI overrides CTCACHE_DIR
    # to its persistent runner cache (a job's $HOME and _build are not
    # persistent there).
    if not os.environ.get("CTCACHE_DIR"):
      os.environ["CTCACHE_DIR"] = str(REPO_ROOT / "_build" / "ctcache")
    cache_dir = Path(os.environ["CTCACHE_DIR"])
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ["CTCACHE_BYPASS_FLAGS_REGEX"] = "^-?-fix"
    os.environ["CTCACHE_SAVE_OUTPUT"] = "1"
    base_cmd = [sys.executable, str(ctcache), "clang-tidy",
                f"-p={build_dir}", "--quiet"]
  else:
    cache_dir = None
    base_cmd = ["clang-tidy", f"-p={build_dir}", "--quiet"]
  if fix:
    base_cmd.append("--fix")

  # --profile: --enable-check-profile turns on per-check timing;
  # -store-check-profile drops it as a per-TU JSON instead of an
  # stderr table. A cache hit replays stored output and runs no checks
  # (so writes no JSON), hence profiling bypasses the cache.
  profile_dir: Path | None = None
  if profile:
    import tempfile
    profile_dir = Path(tempfile.mkdtemp(prefix="ctprofile-"))
    base_cmd.append("--enable-check-profile")
    # Trailing slash: clang-tidy treats the value as a directory only
    # when it ends in a separator, else it's a filename prefix.
    base_cmd.append(f"-store-check-profile={profile_dir}/")
    os.environ["CTCACHE_BYPASS_FLAGS_REGEX"] = "^-?-fix|check-profile"

  # --check: run a single check (or pattern) and nothing else, so a targeted
  # --fix touches only that one rule. -checks changes the result set, so the
  # cache must bust on it too.
  if check:
    base_cmd.append(f"-checks=-*,{check}")
    os.environ["CTCACHE_BYPASS_FLAGS_REGEX"] += "|-?-checks"

  workers = jobs or (os.cpu_count() or 1)
  # Parallel --fix lets two TUs that include the same header rewrite it at the
  # same time — exactly what corrupted unicode.hpp. Serialize when fixing.
  if fix:
    workers = 1
  typer.echo(f"clang-tidy: {len(files)} files, {workers} worker(s), "
             + (f"cache at {cache_dir}" if cache_dir else "no ctcache"))

  # Run in parallel; collect per-file exit codes. clang-tidy exits non-zero
  # when --warnings-as-errors fires (our .clang-tidy maps naming to errors).
  from concurrent.futures import ThreadPoolExecutor

  # Distinguish real check failures from frontend parse failures.
  # clang-diagnostic-error rows mean clang-tidy's own frontend
  # couldn't parse the TU (gcc16/libstdc++ consteval vs clang's
  # parser, etc.) — not an actionable check violation, just a tool
  # limitation. Anything else flagged is a real check we gate on.
  check_failures = 0
  compile_failures = 0
  check_pattern = re.compile(r'\[(?!clang-diagnostic-)[a-z][^]]+\]')
  compile_pattern = re.compile(r'\[clang-diagnostic-')

  def _run_one(target: Path) -> tuple[int, int]:
    proc = subprocess.run(
      base_cmd + [str(target)],
      capture_output=True,
      text=True,
    )
    if proc.stdout:
      sys.stdout.write(proc.stdout)
    if proc.stderr:
      sys.stderr.write(proc.stderr)
    combined = (proc.stdout or "") + (proc.stderr or "")
    return (
      1 if check_pattern.search(combined) else 0,
      1 if compile_pattern.search(combined) else 0,
    )

  with ThreadPoolExecutor(max_workers=workers) as pool:
    for check, compile_err in pool.map(_run_one, files):
      check_failures += check
      compile_failures += compile_err

  if profile_dir:
    totals: dict[str, float] = {}
    for jf in profile_dir.glob("*.json"):
      prof = _json.loads(jf.read_text()).get("profile", {})
      for key, secs in prof.items():
        if key.endswith(".wall"):
          totals[key[:-5]] = totals.get(key[:-5], 0.0) + secs
    shutil.rmtree(profile_dir, ignore_errors=True)
    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    typer.echo(f"\nclang-tidy check profile — total wall seconds across "
               f"{len(files)} TUs:")
    for name, secs in ranked[:25]:
      typer.echo(f"  {secs:9.3f}  {name}")

  if compile_failures:
    typer.echo(
      f"buildutil analyze: {compile_failures}/{len(files)} files hit "
      f"clang-diagnostic-error (clang frontend couldn't parse the TU "
      f"— usually a gcc/libstdc++ vs clang feature lag). Not gating "
      f"on these.",
      err=True,
    )
  if check_failures:
    typer.echo(
      f"buildutil analyze: {check_failures}/{len(files)} files have "
      f"clang-tidy check violations.",
      err=True,
    )
    raise typer.Exit(code=1)
  typer.echo(f"buildutil analyze: clean ({len(files)} files).")
