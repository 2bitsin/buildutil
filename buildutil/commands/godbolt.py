"""buildutil commands: godbolt — source↔assembly HTML report."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import typer

from ..app import app, _compiler_option, _option_option
from ..config import PROJECT
from ..engine import *
from .. import godbolt as gb



@app.command()
def godbolt(
  function: list[str] = typer.Option(
    [], "--function", "-f",
    help="Limit the report to functions whose DEMANGLED name matches this "
       "regex (re.search — a plain substring works). Repeatable; any "
       "match keeps the function. Default: every function.",
  ),
  module: list[str] = typer.Option(
    [], "--module", "-m",
    help="Limit to this module's translation units (dash-joined name, as "
       "`buildutil module list` prints them). Repeatable. Default: every "
       "TU under sources/.",
  ),
  output: Path = typer.Option(
    None, "--output", "-o",
    help="Report directory (created, parents included, if missing). "
       "Default: _build/<profile>/godbolt-report.",
  ),
  include_tests: bool = typer.Option(
    False, "--include-tests",
    help="Also report the *.test.cpp / *.test/ TUs. Excluded by default: "
       "test scaffolding assembly is not what the report is for.",
  ),
  include_benches: bool = typer.Option(
    False, "--include-benches",
    help="Also report the *.bench.cpp / *.bench/ TUs (excluded by "
       "default, like tests).",
  ),
  project_only: bool = typer.Option(
    False, "--project-only",
    help="Drop dependency/stdlib functions COMPLETELY, links to them "
       "included — only the project's own code remains. Default keeps "
       "dependency functions the project's code (transitively) "
       "references, folded behind the project's own listing.",
  ),
  all_functions: bool = typer.Option(
    False, "--all-functions",
    help="Keep every function the TU emitted, including dependency/std "
       "template bodies nothing in the project references (hidden by "
       "default — they are instantiation noise).",
  ),
  release: bool = typer.Option(False, "--release", help="Optimized build (default)."),
  debug: bool = typer.Option(False, "--debug", help="Debug build."),
  relwithdebinfo: bool = typer.Option(
    False, "--relwithdebinfo", help="Optimized build with debug info."),
  no_build: bool = typer.Option(
    False, "--no-build",
    help="Skip conan/configure/build; reuse the existing build dir's "
       "compile_commands.json (the tree must already be configured).",
  ),
  conan_home: str = typer.Option(
    None, "--conan-home",
    help="Override the CONAN_HOME path.",
  ),
  compiler: str = _compiler_option(),
  option: list[str] = _option_option(),
):
  """Godbolt-style report: every source file side-by-side with its assembly.

  Named in tribute to Matt Godbolt's Compiler Explorer, and built the same
  way: each TU is recompiled with `-S -g` under its EXACT flags from
  compile_commands.json (so the asm IS the chosen configuration's), the
  `.loc` directives map instructions back to source lines, directive noise
  and unreferenced labels are filtered out, and all C++ symbols are
  demangled. Matching source/asm lines share a color; hovering either side
  highlights its counterpart, clicking scrolls the other pane there. Asm
  that comes from inlined code in OTHER files is dimmed and carries a
  `path:line` tooltip.
  """
  build_type = _resolve_build_type(release, debug, relwithdebinfo)
  _enter(conan_home)
  # the vscode godbolt tasks route their function prompt through -f and
  # rely on the value being REMEMBERED (next prompt default). An empty
  # answer means "every function" — record it (= cleared), then drop it.
  if len(module) == 1 and len(function) <= 1:
    from .. import vscode as _vscode
    _vscode.record_input("godboltfilter", module[0],
                         function[0] if function else "")
  function = [f for f in function if f]
  _select_compiler(compiler, "auto")
  settings = _detect_settings(build_type)
  if settings.get("compiler") == "msvc":
    typer.echo("godbolt: msvc is not supported — the report is built from "
               "gas `.loc` directives (gcc/clang only)", err=True)
    raise typer.Exit(code=2)
  profile = _ensure_profile(settings)
  build_dir = Path("_build") / _profile_name(settings)

  if not no_build:
    _conan_install(profile, build_dir)
    _cmake_configure(build_dir, build_type, tests=True, bench=False)
    _cmake_build(build_dir)

  ccj = build_dir / "compile_commands.json"
  if not ccj.exists():
    typer.echo(f"godbolt: {ccj} not found — run without --no-build first",
               err=True)
    raise typer.Exit(code=1)

  out_dir = (output if output is not None
             else build_dir / "godbolt-report").resolve()
  out_dir.mkdir(parents=True, exist_ok=True)

  # select the TUs: project sources only, optionally narrowed to modules;
  # test/bench scaffolding is out unless explicitly asked back in
  module_dirs = [Path("sources") / m.replace("-", "/") for m in module]
  entries = []
  scaffolding = {"test": 0, "bench": 0}
  for entry in json.loads(ccj.read_text()):
    src = Path(entry["file"])
    if not src.is_absolute():
      src = (Path(entry["directory"]) / src).resolve()
    try:
      rel = src.resolve().relative_to(REPO_ROOT)
    except ValueError:
      continue
    if rel.parts[:1] != ("sources",):
      continue
    if module_dirs and not any(rel.is_relative_to(d) for d in module_dirs):
      continue
    kind = gb.tu_kind(rel.as_posix())
    if ((kind == "test" and not include_tests)
        or (kind == "bench" and not include_benches)):
      scaffolding[kind] += 1
      continue
    entries.append((entry, src.resolve(), rel))
  if scaffolding["test"] or scaffolding["bench"]:
    typer.echo("godbolt: skipped "
               + ", ".join(f"{n} {kind} TU(s)"
                           for kind, n in scaffolding.items() if n)
               + " (scaffolding; --include-tests / --include-benches "
                 "brings them back)")
  if not entries:
    typer.echo("godbolt: no translation units matched"
               + (f" modules {', '.join(module)}" if module else ""),
               err=True)
    raise typer.Exit(code=1)

  config_label = f"{_profile_name(settings)} ({build_type})"
  project = PROJECT["name"]
  typer.echo(f"godbolt: {len(entries)} translation unit(s), "
             f"config {config_label}")

  def compile_one(item):
    entry, src_abs, rel = item
    argv = (list(entry["arguments"]) if "arguments" in entry
            else shlex.split(entry["command"]))
    with tempfile.NamedTemporaryFile(suffix=".s", delete=False) as tmp:
      out_s = Path(tmp.name)
    try:
      proc = subprocess.run(gb.asm_argv(argv, out_s),
                            cwd=entry["directory"],
                            capture_output=True, text=True)
      if proc.returncode != 0:
        return rel, None, proc.stderr.strip()
      return rel, out_s.read_text(errors="replace"), None
    finally:
      out_s.unlink(missing_ok=True)

  with ThreadPoolExecutor(max_workers=max(1, os.cpu_count() or 2)) as pool:
    compiled = list(pool.map(compile_one, entries))

  if project_only and all_functions:
    typer.echo("godbolt: --project-only and --all-functions are opposites — "
               "pick one", err=True)
    raise typer.Exit(code=2)

  # phase 1: parse + scope every TU, classify project vs dependency code,
  # prune per the chosen level, and index every surviving function
  # (mangled name -> page#anchor) so calls/jumps LINK across pages
  pages = []
  global_syms: dict[str, str] = {}
  failures = 0
  dropped_total = 0
  for (entry, src_abs, rel), (rel2, asm_text, err) in zip(entries, compiled):
    if asm_text is None:
      failures += 1
      typer.echo(f"godbolt: SKIP {rel} — compile failed:\n{err}", err=True)
      continue
    lines, files = gb.parse_asm(asm_text)
    names = gb.demangle_map(gb.mangled_tokens(lines)
                            | {ln.func for ln in lines if ln.func})
    lines = gb.scope_functions(lines, function, names)
    if function and not lines:
      continue                       # TU has none of the chosen functions

    # resolve the .file table once: which ids are the page's own source,
    # which are PROJECT files at all (repo-relative), how to caption others
    directory = Path(entry["directory"])
    primary_ids, project_ids, display = set(), set(), {}
    for idx, path in files.items():
      resolved = (Path(path) if Path(path).is_absolute()
                  else directory / path).resolve()
      if resolved == src_abs:
        primary_ids.add(idx)
      try:
        display[idx] = str(resolved.relative_to(REPO_ROOT))
        project_ids.add(idx)         # any repo file = project code
      except ValueError:
        display[idx] = path

    if not all_functions:
      lines, dropped = gb.prune_dependency_functions(
        lines, project_ids, follow_references=not project_only)
      dropped_total += dropped
    page_file = gb.page_name(str(rel))
    local_syms = gb.page_symbols(lines)
    for sym, anchor in local_syms.items():
      # comdat bodies (inline/template code) land in EVERY TU; the first
      # page keeps the cross-TU link — any instance IS the code
      global_syms.setdefault(sym, f"{page_file}#{anchor}")
    pages.append((src_abs, rel, page_file, lines, files, names,
                  local_syms, primary_ids, project_ids, display))

  # phase 2: render, in-page targets (own functions, local labels)
  # overriding the cross-TU index
  index_entries = []
  for (src_abs, rel, page_file, lines, files, names,
       local_syms, primary_ids, project_ids, display) in pages:
    rendered = gb.prepare(lines, files, names, primary_ids, display)
    hrefs = dict(global_syms)
    hrefs.update({sym: f"#{anchor}" for sym, anchor in local_syms.items()})
    hrefs.update({r.text[:-1]: f"#{r.anchor}" for r in rendered
                  if r.kind == "label" and r.anchor
                  and r.anchor.startswith("lbl-")})
    nav = gb.function_nav(lines, names,
                          gb.project_function_names(lines, project_ids))
    page = out_dir / page_file
    page.write_text(gb.render_page(
      project, config_label, str(rel),
      src_abs.read_text(errors="replace"), rendered, nav, names, hrefs))
    index_entries.append((str(rel), page.name, nav))
    n_deps = sum(1 for _, _, is_project in nav if not is_project)
    typer.echo(f"godbolt: {rel} — {len(nav) - n_deps} function(s)"
               + (f" + {n_deps} dependency" if n_deps else "") + ", "
               f"{sum(1 for r in rendered if r.kind == 'insn')} instruction(s)")

  index_entries.sort()
  (out_dir / "index.html").write_text(
    gb.render_index(project, config_label, index_entries))
  if dropped_total:
    typer.echo(f"godbolt: {dropped_total} dependency function(s) hidden "
               + ("(--project-only: including referenced ones; "
                  if project_only else "(unreferenced by project code; ")
               + "--all-functions shows everything)")
  print(f"\ngodbolt report: {out_dir / 'index.html'}")
  if failures:
    typer.echo(f"godbolt: {failures} TU(s) failed to compile (skipped)",
               err=True)
    raise typer.Exit(code=1)
