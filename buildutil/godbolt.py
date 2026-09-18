"""Godbolt-style source↔assembly HTML report — the name pays tribute to
Matt Godbolt's Compiler Explorer, whose technique this borrows: recompile
each TU with `-S -g` under its EXACT configured flags and map every kept
instruction back to a source line through the `.loc` debug directives.

This module is the pure half (stdlib only, no engine/typer imports):
parse gas output, filter the noise, prune unreferenced labels, demangle,
scope to selected functions, and render self-contained HTML. The verb in
commands/godbolt.py drives the compiler and feeds this.
"""
from __future__ import annotations

import html
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path



# ---------------------------------------------------------------- parsing

@dataclass
class AsmLine:
  kind: str          # 'label' | 'insn'
  text: str          # raw gas text (label text is 'name:'; insns keep operands)
  file: int | None   # .file index active at this line (None: no .loc seen)
  line: int | None   # source line from the active .loc
  func: str | None   # mangled name of the enclosing function (None: file scope)


_FILE_RE = re.compile(r'^\.file\s+(\d+)\s+"([^"]*)"(?:\s+"([^"]*)")?')
_LOC_RE = re.compile(r"^\.loc\s+(\d+)\s+(\d+)")
_TYPE_RE = re.compile(r"^\.type\s+([^,\s]+)\s*,\s*[@%]function")
_SIZE_RE = re.compile(r"^\.size\s+([^,\s]+)")
_LABEL_RE = re.compile(r"^([A-Za-z_.$][A-Za-z0-9_.$]*):(.*)$")
_LOCAL_LABEL_TOKEN = re.compile(r"\.L[A-Za-z0-9_.$]+")
MANGLED_TOKEN = re.compile(r"_Z[A-Za-z0-9_.$]+")


def parse_asm(text: str) -> tuple[list[AsmLine], dict[int, str]]:
  """One pass over `cc -S -g` output: keep labels and instructions from
  the .text sections, consume the directives that carry state (.file /
  .loc / .type / .size / .section), drop everything else (.cfi_*, .ident,
  .align, rodata content, ...). Then prune local labels (`.L*`) that no
  kept instruction references — the jump targets survive, the DWARF/EH
  bookkeeping labels go. Returns (lines, file_table)."""
  lines: list[AsmLine] = []
  files: dict[int, str] = {}
  loc: tuple[int, int] | None = None
  in_text = True                     # gas starts in .text
  pending_funcs: set[str] = set()
  current_func: str | None = None

  def emit(kind: str, raw: str) -> None:
    lines.append(AsmLine(kind, raw,
                         loc[0] if loc else None,
                         loc[1] if loc else None,
                         current_func))

  for raw in text.splitlines():
    stripped = raw.strip()
    if not stripped or stripped.startswith(("#", ";")):
      continue

    label = _LABEL_RE.match(stripped)
    if label:
      name, rest = label.group(1), label.group(2).strip()
      if name in pending_funcs:
        current_func = name          # function entry: the label OWNS itself
        loc = None                   # the PREVIOUS function's .loc must not
                                     # leak onto this one's entry — lines
                                     # before its first .loc are unmapped,
                                     # not wrongly colored (and a dependency
                                     # function must not inherit a project
                                     # file id and dodge the pruning)
      if in_text:
        emit("label", name + ":")
      if rest:                       # `name: insn` on one line — rare, split
        stripped = rest
      else:
        continue

    if stripped.startswith("."):
      if m := _FILE_RE.match(stripped):
        dir_part, name_part = m.group(2), m.group(3)
        files[int(m.group(1))] = (
          name_part if name_part and Path(name_part).is_absolute()
          else f"{dir_part}/{name_part}" if name_part else dir_part)
      elif m := _LOC_RE.match(stripped):
        loc = (int(m.group(1)), int(m.group(2)))
      elif m := _TYPE_RE.match(stripped):
        pending_funcs.add(m.group(1))
      elif m := _SIZE_RE.match(stripped):
        if m.group(1) == current_func:
          current_func = None
      elif stripped.startswith(".section"):
        parts = stripped.split(None, 1)
        arg = parts[1].split(",")[0].strip() if len(parts) > 1 else ""
        in_text = arg.startswith(".text")
      elif stripped == ".text" or stripped.startswith(".text."):
        in_text = True
      elif stripped.split(None, 1)[0] in (".data", ".bss", ".rodata"):
        in_text = False
      continue

    if in_text:
      emit("insn", stripped)

  # local-label pruning: keep .L* labels only when some kept instruction
  # names them (jump/branch targets, jump-table bases)
  referenced = {tok for ln in lines if ln.kind == "insn"
                for tok in _LOCAL_LABEL_TOKEN.findall(ln.text)}
  return [ln for ln in lines
          if not (ln.kind == "label" and ln.text.startswith(".L")
                  and ln.text[:-1] not in referenced)], files


# -------------------------------------------------------------- demangling

def mangled_tokens(lines: list[AsmLine]) -> set[str]:
  return {tok for ln in lines for tok in MANGLED_TOKEN.findall(ln.text)}


def demangle_map(tokens: set[str]) -> dict[str, str]:
  """Batch the whole set through one c++filt run. Missing c++filt or a
  bad round-trip degrades to identity — everything still works, just
  mangled."""
  ordered = sorted(tokens)
  identity = {t: t for t in ordered}
  filt = shutil.which("c++filt")
  if not filt or not ordered:
    return identity
  try:
    out = subprocess.run([filt], input="\n".join(ordered),
                         capture_output=True, text=True, check=True).stdout
  except (subprocess.CalledProcessError, OSError):
    return identity
  demangled = out.splitlines()
  if len(demangled) != len(ordered):
    return identity
  return dict(zip(ordered, demangled))


# ----------------------------------------------------------------- scoping

def scope_functions(lines: list[AsmLine], patterns: list[str],
                    names: dict[str, str]) -> list[AsmLine]:
  """Keep only functions whose DEMANGLED name matches any regex in
  `patterns` (re.search). No patterns: everything stays. File-scope lines
  (func None) are dropped when scoping — they belong to no chosen
  function."""
  if not patterns:
    return lines
  compiled = [re.compile(p) for p in patterns]
  def keep(func: str | None) -> bool:
    if func is None:
      return False
    pretty = names.get(func, func)
    return any(rx.search(pretty) for rx in compiled)
  return [ln for ln in lines if keep(ln.func)]


def _group_functions(lines: list[AsmLine]) -> dict[str, list[AsmLine]]:
  funcs: dict[str, list[AsmLine]] = {}
  for ln in lines:
    if ln.func:
      funcs.setdefault(ln.func, []).append(ln)
  return funcs


def project_function_names(lines: list[AsmLine],
                           project_ids: set[int]) -> set[str]:
  """Functions that ARE project code: any of their lines maps (.loc)
  into a file under the repo. Everything else is dependency/stdlib
  material a header instantiated into the TU."""
  return {name for name, body in _group_functions(lines).items()
          if any(ln.file in project_ids for ln in body)}


def prune_dependency_functions(
    lines: list[AsmLine], project_ids: set[int],
    follow_references: bool = True) -> tuple[list[AsmLine], int]:
  """Hide the dependency noise: a TU's asm carries comdat bodies for
  every std/dependency template its headers instantiated, most of which
  the project never calls. Project functions always stay. With
  follow_references (the default) everything reachable from them
  through call/jump/address references stays too — following the code
  never dead-ends; without it (--project-only) dependency code is
  dropped WHOLESALE — it is not the project's to influence, so calls
  into it simply render unlinked. Returns (kept, dropped count)."""
  funcs = _group_functions(lines)
  keep = set(project_function_names(lines, project_ids))
  if follow_references:
    refs: dict[str, set[str]] = {}
    for name, body in funcs.items():
      refs[name] = {tok for ln in body if ln.kind == "insn"
                    for tok in _TOKEN_RE.findall(ln.text)
                    if tok in funcs and tok != name}
    frontier = list(keep)
    while frontier:
      for ref in refs[frontier.pop()]:
        if ref not in keep:
          keep.add(ref)
          frontier.append(ref)
  kept = [ln for ln in lines if ln.func is None or ln.func in keep]
  return kept, len(funcs) - len(keep)


# --------------------------------------------------------------- rendering

@dataclass
class RenderLine:
  kind: str          # 'label' | 'insn'
  text: str          # raw gas text (tokens are linked/demangled at render)
  src: int | None    # primary-source line this asm belongs to (colored link)
  tip: str | None    # hover text: 'path:line' for other files, or a hint
  anchor: str | None # id: function entries (f-…) and local labels (lbl-…)
  dim: bool = False  # True: the line's code comes from ANOTHER file


_SLUG_RE = re.compile(r"[^A-Za-z0-9_-]")


def anchor_for(mangled: str) -> str:
  """A stable, name-derived anchor for a function entry — the same on
  every page, so cross-TU links need only the symbol."""
  return "f-" + _SLUG_RE.sub("_", mangled)


def _label_anchor(name: str) -> str:
  return "lbl-" + _SLUG_RE.sub("_", name)


NO_LOC_HINT = ("no line info from the compiler for this instruction "
               "(prologue/epilogue or generated glue)")
NO_ASM_HINT = ("no machine code maps to this line in this TU "
               "(declaration, comment, optimized out, or inlined elsewhere)")


def prepare(lines: list[AsmLine], files: dict[int, str],
            names: dict[str, str], primary_ids: set[int],
            display_paths: dict[int, str] | None = None) -> list[RenderLine]:
  """Resolve every AsmLine for display: attach the primary-source line
  number when the .loc points into the page's own source, a dimmed
  `path:line` tooltip when it points elsewhere (inlined code), and an
  explicit no-line-info hint otherwise — an unmapped line should say WHY
  it is unmapped. Function-entry labels get name-derived anchors (the
  cross-TU link targets); local labels get lbl- anchors (jump targets)."""
  out: list[RenderLine] = []
  display_paths = display_paths or files
  for ln in lines:
    src = tip = anchor = None
    dim = False
    if ln.kind == "label":
      name = ln.text[:-1]
      anchor = anchor_for(name) if name == ln.func else _label_anchor(name)
    elif ln.file is not None and ln.file in primary_ids:
      src = ln.line
    elif ln.file is not None and ln.file in files:
      tip = f"{display_paths.get(ln.file, files[ln.file])}:{ln.line}"
      dim = True
    else:
      tip = NO_LOC_HINT
    out.append(RenderLine(ln.kind, ln.text, src, tip, anchor, dim))
  return out


def function_nav(lines: list[AsmLine], names: dict[str, str],
                 project: set[str] | None = None) -> list[tuple[str, str, bool]]:
  """(anchor, demangled name, is_project) for every function entry —
  PROJECT functions first, dependency material after: the project's own
  code is what the report is about; deps are the segue, not the lead.
  project=None treats everything as project (no classification)."""
  nav: list[tuple[str, str, bool]] = []
  seen: set[str] = set()
  for ln in lines:
    if ln.kind == "label" and ln.text[:-1] == ln.func and ln.func not in seen:
      seen.add(ln.func)
      nav.append((anchor_for(ln.func), names.get(ln.func, ln.func),
                  project is None or ln.func in project))
  return ([entry for entry in nav if entry[2]]
          + [entry for entry in nav if not entry[2]])


def page_symbols(lines: list[AsmLine]) -> dict[str, str]:
  """{mangled function name: anchor} for every function this page defines."""
  return {ln.func: anchor_for(ln.func) for ln in lines
          if ln.kind == "label" and ln.text[:-1] == ln.func}


_TOKEN_RE = re.compile(r"[A-Za-z_.$][A-Za-z0-9_.$]*")


def _linked_html(text: str, names: dict[str, str],
                 hrefs: dict[str, str]) -> str:
  """Escape + demangle a gas line, wrapping every token that names a known
  function or label in a link — calls, jumps and address references become
  navigation, in-page (#…) or cross-TU (page.html#…)."""
  out: list[str] = []
  pos = 0
  for match in _TOKEN_RE.finditer(text):
    token = match.group(0)
    out.append(html.escape(text[pos:match.start()]))
    display = html.escape(names.get(token, token))
    href = hrefs.get(token)
    if href:
      out.append(f'<a class="sym" href="{html.escape(href, quote=True)}">'
                 f"{display}</a>")
    else:
      out.append(display)
    pos = match.end()
  out.append(html.escape(text[pos:]))
  return "".join(out)


_PALETTE = 20  # distinct hues, golden-angle spaced, cycled by source line

_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { margin:0; font:13px/1.5 ui-monospace,'Cascadia Code',Consolas,Menlo,monospace;
       background:Canvas; color:CanvasText; }
header { position:sticky; top:0; z-index:2; display:flex; gap:1em; align-items:baseline;
         padding:.5em .8em; background:Canvas; border-bottom:1px solid #8884; }
header h1 { font-size:1em; margin:0; font-weight:600; }
header .cfg { opacity:.65; }
header a { color:inherit; }
select { max-width:28em; font:inherit; }
.panes { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr);
         height:calc(100vh - 2.9em); }
.pane { overflow:auto; }
.pane+.pane { border-left:1px solid #8884; }
.line { white-space:pre; padding:0 .6em; min-height:1.5em; background:var(--bg,transparent); }
.line[data-l] { cursor:pointer; }
.hl { box-shadow:inset 0 0 0 999px rgba(128,128,128,.28); }
.lineno { display:inline-block; width:4ch; margin-right:1.2ch; text-align:right;
          opacity:.45; user-select:none; }
.label { font-weight:600; }
.other { opacity:.55; }
a.sym { color:inherit; text-decoration:underline dotted; }
a.sym:hover { text-decoration:underline solid; }
.legend { margin-left:auto; opacity:.6; font-size:.9em; white-space:nowrap; }
.pane-title { position:sticky; top:0; padding:.15em .6em; font-weight:600;
              background:Canvas; opacity:.9; border-bottom:1px solid #8884; z-index:1; }
[id] { scroll-margin-top:1.8em; }  /* anchor jumps clear the pane title */
"""

_JS = """
let cur=[];
document.addEventListener('mouseover',e=>{
  const el=e.target.closest('[data-l]');
  const key=el?el.dataset.l:null;
  cur.forEach(n=>n.classList.remove('hl'));cur=[];
  if(key){cur=[...document.querySelectorAll('[data-l="'+key+'"]')];
          cur.forEach(n=>n.classList.add('hl'));}
});
document.addEventListener('click',e=>{
  const el=e.target.closest('[data-l]');if(!el)return;
  const inSrc=!!el.closest('#src');
  const other=document.getElementById(inSrc?'asm':'src');
  const t=other.querySelector('[data-l="'+el.dataset.l+'"]');
  if(t)t.scrollIntoView({block:'center'});
});
const nav=document.getElementById('fnav');
if(nav)nav.addEventListener('change',()=>{if(nav.value)location.hash=nav.value;});
"""


def _palette_css() -> str:
  rules = []
  for i in range(_PALETTE):
    hue = (i * 137) % 360
    rules.append(f".c{i}{{--bg:hsl({hue} 70% 88%)}}")
  dark = "".join(f".c{i}{{--bg:hsl({(i * 137) % 360} 45% 27%)}}"
                 for i in range(_PALETTE))
  return "\n".join(rules) + \
         "\n@media (prefers-color-scheme: dark){" + dark + "}\n"


def render_page(project: str, config: str, source_rel: str,
                source_text: str, rendered: list[RenderLine],
                nav: list[tuple[str, str]],
                names: dict[str, str] | None = None,
                hrefs: dict[str, str] | None = None) -> str:
  """One self-contained HTML page: source pane left, filtered asm right,
  same color class on both sides of every mapping, hover highlights the
  counterpart lines, click scrolls the other pane there. Every symbol
  reference (call/jump target, local label, address) that `hrefs` knows
  is a link — in-page or across TU pages — so the code can be followed
  naturally. Every UNMAPPED line explains itself in a tooltip."""
  names = names or {}
  hrefs = hrefs or {}
  mapped = sorted({r.src for r in rendered if r.src is not None})
  color = {srcline: f"c{i % _PALETTE}" for i, srcline in enumerate(mapped)}

  src_rows = []
  for n, text in enumerate(source_text.splitlines(), start=1):
    cls, attr = "line", f' title="{html.escape(NO_ASM_HINT, quote=True)}"'
    if n in color:
      cls += " " + color[n]
      attr = f' data-l="{n}"'
    src_rows.append(f'<div class="{cls}"{attr}>'
                    f'<span class="lineno">{n}</span>'
                    f'{html.escape(text) or " "}</div>')

  asm_rows = []
  for r in rendered:
    cls = "line" + (" label" if r.kind == "label" else "")
    attr = ""
    if r.src is not None:
      cls += " " + color[r.src]
      attr = f' data-l="{r.src}"'
    elif r.tip:
      if r.dim:
        cls += " other"
      attr = f' title="{html.escape(r.tip, quote=True)}"'
    if r.anchor:
      attr += f' id="{r.anchor}"'
    if r.kind == "label":
      name = r.text[:-1]
      body = html.escape(names.get(name, name)) + ":"
    else:
      body = "        " + _linked_html(r.text, names, hrefs)
    asm_rows.append(f'<div class="{cls}"{attr}>{body}</div>')

  proj_opts = "".join(f'<option value="{a}">{html.escape(name)}</option>'
                      for a, name, is_project in nav if is_project)
  dep_opts = "".join(f'<option value="{a}">{html.escape(name)}</option>'
                     for a, name, is_project in nav if not is_project)
  if dep_opts:
    dep_opts = f'<optgroup label="dependency code">{dep_opts}</optgroup>'
  fnav = (f'<select id="fnav"><option value="">'
          f'jump to function ({len(nav)})</option>{proj_opts}{dep_opts}'
          "</select>" if nav else "")
  legend = ('<span class="legend">colors pair source ↔ asm · dimmed = '
            'inlined from another file · plain = no mapping (hover any '
            'line for why) · underlined symbols are links</span>')

  return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>{html.escape(source_rel)} — {html.escape(project)} godbolt</title>
<style>{_CSS}{_palette_css()}</style></head>
<body>
<header><h1><a href="index.html">{html.escape(project)}</a> /
{html.escape(source_rel)}</h1>
<span class="cfg">{html.escape(config)}</span>{fnav}{legend}</header>
<div class="panes">
<div class="pane" id="src"><div class="pane-title">source</div>
{chr(10).join(src_rows)}</div>
<div class="pane" id="asm"><div class="pane-title">assembly (filtered, demangled)</div>
{chr(10).join(asm_rows)}</div>
</div>
<script>{_JS}</script>
</body></html>
"""


def render_index(project: str, config: str,
                 entries: list[tuple[str, str, list[tuple[str, str, bool]]]]) -> str:
  """The report's front page: every TU (source rel-path, page href) with
  its function inventory — every function links straight to its assembly
  (page + anchor). PROJECT code leads; dependency functions (present
  only when --deps/--all-functions asked for them) sit folded behind a
  disclosure so they never crowd the page."""
  rows = []
  for source_rel, href, funcs in entries:
    page = html.escape(href, quote=True)
    def _items(pred):
      return "".join(
        f'<li><a href="{page}#{anchor}">{html.escape(f)}</a></li>'
        for anchor, f, is_project in funcs if pred(is_project))
    flist = f"<ul>{_items(lambda p: p)}</ul>"
    deps = _items(lambda p: not p)
    n_deps = sum(1 for _, _, is_project in funcs if not is_project)
    if deps:
      flist += (f"<details><summary>{n_deps} dependency function(s)"
                f"</summary><ul>{deps}</ul></details>")
    rows.append(
      f'<section><h2><a href="{page}">'
      f'{html.escape(source_rel)}</a> '
      f'<span class="n">{len(funcs) - n_deps} function(s)'
      f'{f" + {n_deps} dependency" if n_deps else ""}</span></h2>'
      f"{flist}</section>")
  body = "\n".join(rows) or "<p>no translation units matched.</p>"
  return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>{html.escape(project)} godbolt — {html.escape(config)}</title>
<style>{_CSS}
main{{padding:1em;max-width:75em}} section{{margin-bottom:1.2em}}
h2{{font-size:1em;margin:.2em 0}} .n{{opacity:.55;font-weight:400}}
ul{{margin:.2em 0 .2em 1.5em;padding:0;opacity:.85}}
</style></head>
<body><header><h1>{html.escape(project)} — source ↔ assembly</h1>
<span class="cfg">{html.escape(config)}</span></header>
<main>{body}</main></body></html>
"""


# --------------------------------------------------- compile-command munging

# flags that must not survive the -S re-run: the original object output,
# dep-file bookkeeping (rewriting the build's .d files would feed ninja a
# stale target name), and LTO (its -S output is bytecode, not assembly)
_DROP_WITH_ARG = {"-o", "-MF", "-MT", "-MQ"}
_DROP_ALONE = {"-c", "-MD", "-MMD", "-flto", "-flto=auto", "-flto=thin"}


def asm_argv(argv: list[str], out_s: Path) -> list[str]:
  """Transform a compile_commands.json argv into the `-S -g` variant that
  writes gas-with-.loc to `out_s`, preserving every configuration flag."""
  kept: list[str] = []
  skip = False
  for arg in argv:
    if skip:
      skip = False
      continue
    if arg in _DROP_WITH_ARG:
      skip = True
      continue
    if arg in _DROP_ALONE or arg.startswith("-flto"):
      continue
    kept.append(arg)
  return [*kept, "-S", "-g", "-fno-lto", "-o", str(out_s)]


def page_name(source_rel: str) -> str:
  """Flatten a repo-relative source path into one report filename."""
  return source_rel.replace("/", "__") + ".html"


def tu_kind(source_rel: str) -> str:
  """'test' / 'bench' / 'code' for a repo-relative TU path, by the module
  conventions (*.test.cpp / *.bench.cpp files, *.test/ / *.bench/ dirs).
  Test and bench TUs are scaffolding — excluded from the report by
  default; the report is about the code."""
  parts = source_rel.split("/")
  for marker, kind in ((".test", "test"), (".bench", "bench")):
    if parts[-1].endswith(f"{marker}.cpp"):
      return kind
    if any(part.endswith(marker) for part in parts[:-1]):
      return kind
  return "code"
