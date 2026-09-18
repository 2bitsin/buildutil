"""godbolt report: gas parsing/filtering, .loc mapping, label pruning,
demangling, function scoping, argv munging and the HTML panes."""
import subprocess

import pytest

from buildutil import godbolt as gb


ASM = """\
\t.file\t"hello.cpp"
\t.text
\t.globl\t_Z5greetv
\t.type\t_Z5greetv, @function
_Z5greetv:
.LFB0:
\t.cfi_startproc
\t.file 1 "sources/hello/hello.cpp"
\t.loc 1 4 1
\tpushq\t%rbp
\t.loc 1 5 10
\tmovl\t$42, %eax
\tjmp\t.L2
.L2:
\tpopq\t%rbp
\tret
\t.cfi_endproc
\t.size\t_Z5greetv, .-_Z5greetv
\t.section\t.rodata
.LC0:
\t.string\t"hello"
\t.text
\t.globl\tmain
\t.type\tmain, @function
main:
\t.file 2 "/usr/include/c++/13/iostream"
\t.loc 2 61 3
\tcall\t_Z5greetv
\t.loc 1 9 1
\txorl\t%eax, %eax
\tret
\t.size\tmain, .-main
\t.ident\t"GCC: 13.2"
"""

NAMES = {"_Z5greetv": "greet()", "main": "main"}


@pytest.fixture
def parsed():
  return gb.parse_asm(ASM)


def test_noise_is_filtered_and_state_directives_consumed(parsed):
  lines, files = parsed
  texts = [ln.text for ln in lines]
  assert not any(t.startswith(".cfi") for t in texts)
  assert not any(".ident" in t or ".size" in t or ".globl" in t for t in texts)
  assert files == {1: "sources/hello/hello.cpp",
                   2: "/usr/include/c++/13/iostream"}


def test_label_pruning_keeps_referenced_and_function_labels(parsed):
  lines, _ = parsed
  labels = [ln.text for ln in lines if ln.kind == "label"]
  assert "_Z5greetv:" in labels and "main:" in labels
  assert ".L2:" in labels          # jump target — referenced
  assert ".LFB0:" not in labels    # DWARF bookkeeping — unreferenced
  assert ".LC0:" not in labels     # rodata content never enters the report


def test_loc_mapping_and_function_ownership(parsed):
  lines, _ = parsed
  by_text = {ln.text: ln for ln in lines}
  assert (by_text["pushq\t%rbp"].file, by_text["pushq\t%rbp"].line) == (1, 4)
  assert (by_text["movl\t$42, %eax"].line) == 5
  assert by_text["pushq\t%rbp"].func == "_Z5greetv"
  assert by_text["call\t_Z5greetv"].func == "main"
  assert (by_text["call\t_Z5greetv"].file,
          by_text["call\t_Z5greetv"].line) == (2, 61)  # inlined other-file


def test_text_startup_section_is_text():
  # gcc -O2 places main in .text.startup, tab-separated — must stay "text"
  lines, _ = gb.parse_asm('\t.section\t.text.startup,"ax",@progbits\n'
                          '\t.type\tmain, @function\n'
                          'main:\n\tret\n'
                          '\t.section\t.rodata.str1.1,"aMS"\n'
                          '\t.string\t"x"\n')
  assert [ln.text for ln in lines] == ["main:", "ret"]


def test_dwarf5_two_string_file_directive():
  _, files = gb.parse_asm('\t.file 0 "/proj" "sources/main/main.cpp"\n'
                          '\t.file 3 "/proj" "/abs/gen.cpp"\n')
  assert files[0] == "/proj/sources/main/main.cpp"
  assert files[3] == "/abs/gen.cpp"    # absolute name wins over the dir


def test_mangled_tokens_and_identity_fallback(parsed, monkeypatch):
  lines, _ = parsed
  assert gb.mangled_tokens(lines) == {"_Z5greetv"}
  monkeypatch.setattr(gb.shutil, "which", lambda _: None)
  assert gb.demangle_map({"_Z5greetv"}) == {"_Z5greetv": "_Z5greetv"}


def test_demangle_map_batches_one_cxxfilt_run(monkeypatch):
  calls = []
  monkeypatch.setattr(gb.shutil, "which", lambda _: "/usr/bin/c++filt")
  def fake_run(cmd, input, **kw):
    calls.append(input)
    out = "\n".join("greet()" if t == "_Z5greetv" else t
                    for t in input.splitlines())
    return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
  monkeypatch.setattr(gb.subprocess, "run", fake_run)
  names = gb.demangle_map({"_Z5greetv", "main"})
  assert names == NAMES and len(calls) == 1


def test_scope_keeps_matching_functions_only(parsed):
  lines, _ = parsed
  scoped = gb.scope_functions(lines, ["greet"], NAMES)
  assert scoped and all(ln.func == "_Z5greetv" for ln in scoped)
  assert gb.scope_functions(lines, [], NAMES) == lines   # no patterns: all
  assert gb.scope_functions(lines, ["^main$"], NAMES)    # regex, demangled


def test_prepare_maps_primary_and_tips_other_files(parsed):
  lines, files = parsed
  rendered = gb.prepare(lines, files, NAMES, primary_ids={1},
                        display_paths={2: "/usr/include/c++/13/iostream"})
  by_text = {r.text: r for r in rendered}
  assert by_text["pushq\t%rbp"].src == 4 and by_text["pushq\t%rbp"].tip is None
  call = by_text["call\t_Z5greetv"]         # raw text; demangled at render
  assert call.src is None and call.dim
  assert call.tip == "/usr/include/c++/13/iostream:61"
  # name-derived anchors — stable cross-page link targets
  assert by_text["_Z5greetv:"].anchor == "f-_Z5greetv"
  assert by_text["main:"].anchor == "f-main"
  assert by_text[".L2:"].anchor == "lbl-_L2"       # jump target anchor
  # an insn with NO .loc explains itself instead of sitting blank
  no_loc = gb.prepare([gb.AsmLine("insn", "endbr64", None, None, "main")],
                      {}, NAMES, primary_ids=set())
  assert no_loc[0].tip == gb.NO_LOC_HINT and not no_loc[0].dim


def test_function_nav_orders_project_code_first(parsed):
  lines, _ = parsed
  assert gb.function_nav(lines, NAMES) == [
    ("f-_Z5greetv", "greet()", True), ("f-main", "main", True)]
  # with a classification, dependency functions sink below project ones
  assert gb.function_nav(lines, NAMES, project={"main"}) == [
    ("f-main", "main", True), ("f-_Z5greetv", "greet()", False)]


def test_page_symbols_maps_mangled_to_anchor(parsed):
  lines, _ = parsed
  assert gb.page_symbols(lines) == {"_Z5greetv": "f-_Z5greetv",
                                    "main": "f-main"}


def test_render_page_links_both_panes_with_shared_colors(parsed):
  lines, files = parsed
  rendered = gb.prepare(lines, files, NAMES, primary_ids={1})
  page = gb.render_page("proj", "x86_64-linux-gcc-debug (Debug)",
                        "sources/hello/hello.cpp",
                        "l1\nl2\nl3\nint greet() {\n  return 42;\n}\n",
                        rendered, gb.function_nav(lines, NAMES), NAMES)
  assert page.count('data-l="4"') == 2      # source line 4 + its one insn
  # line 5's .loc state covers movl/jmp/popq/ret — source line + 4 insns
  assert page.count('data-l="5"') == 5
  assert "greet():" in page                 # labels render demangled
  assert 'title="/usr/include/c++/13/iostream:61"' in page
  assert "prefers-color-scheme: dark" in page
  assert '<option value="f-_Z5greetv">greet()</option>' in page
  # every line explains itself: unmapped source lines and the legend
  assert gb.NO_ASM_HINT.split("(")[0].strip() in page
  assert 'class="legend"' in page


def test_render_page_links_symbol_references(parsed):
  lines, files = parsed
  rendered = gb.prepare(lines, files, NAMES, primary_ids={1})
  hrefs = {"_Z5greetv": "#f-_Z5greetv",              # in-page function
           "main": "other__page.cpp.html#f-main",    # cross-TU function
           ".L2": "#lbl-_L2"}                        # local jump target
  page = gb.render_page("proj", "cfg", "hello.cpp", "x\n",
                        rendered, [], NAMES, hrefs)
  assert '<a class="sym" href="#f-_Z5greetv">greet()</a>' in page
  assert 'id="f-_Z5greetv"' in page and 'id="lbl-_L2"' in page
  assert '<a class="sym" href="#lbl-_L2">.L2</a>' in page
  # jump/call operands are links; the label LINE itself is the target,
  # not a self-link
  assert '<a class="sym" href="#f-_Z5greetv">greet()</a>:' not in page


def test_render_page_escapes_html():
  rendered = [gb.RenderLine("label", "f<int>():", None, None, "f-x"),
              gb.RenderLine("insn", "cmp a<b", 1, None, None)]
  names = {"f<int>": "f<int>"}
  page = gb.render_page("p", "cfg", "a.cpp", "if (a<b) {}\n", rendered,
                        [("f-x", "f<int>()", True)], names)
  assert "f&lt;int&gt;" in page and "cmp a&lt;b" in page
  assert "<int>" not in page.split("</head>")[1]


def test_render_index_leads_with_project_code_and_folds_deps():
  page = gb.render_index("proj", "cfg", [
    ("sources/hello/hello.cpp", "sources__hello__hello.cpp.html",
     [("f-1", "greet()", True), ("f-2", "main", True),
      ("f-3", "std::vector<int>::push_back(int&&)", False)])])
  assert 'href="sources__hello__hello.cpp.html"' in page
  assert 'href="sources__hello__hello.cpp.html#f-1">greet()</a>' in page
  assert 'href="sources__hello__hello.cpp.html#f-2">main</a>' in page
  assert "2 function(s) + 1 dependency" in page
  # deps live behind a fold, never in the lead list
  assert "<details><summary>1 dependency function(s)</summary>" in page
  assert page.index(">main</a>") < page.index("push_back")
  assert "no translation units" in gb.render_index("proj", "cfg", [])


def test_asm_argv_swaps_output_and_strips_dep_and_lto_flags():
  from pathlib import Path
  argv = ["g++", "-O2", "-flto=auto", "-MD", "-MT", "obj/x.o", "-MF",
          "obj/x.o.d", "-o", "obj/x.o", "-c", "sources/x.cpp"]
  out = gb.asm_argv(argv, Path("/tmp/x.s"))
  assert out[:2] == ["g++", "-O2"]
  assert "sources/x.cpp" in out
  assert out[-5:] == ["-S", "-g", "-fno-lto", "-o", "/tmp/x.s"]
  for gone in ("-c", "-MD", "-MT", "-MF", "obj/x.o", "obj/x.o.d", "-flto=auto"):
    assert gone not in out


def test_page_name_flattens_path():
  assert gb.page_name("sources/x/y/z.cpp") == "sources__x__y__z.cpp.html"


PRUNE_ASM = """\
\t.text
\t.type\tproject_fn, @function
project_fn:
\t.file 1 "sources/x/x.cpp"
\t.loc 1 3 1
\tcall\tdep_used
\tret
\t.size\tproject_fn, .-project_fn
\t.type\tdep_used, @function
dep_used:
\t.file 2 "/usr/include/c++/17/vector"
\t.loc 2 100 1
\tcall\tdep_transitive
\tret
\t.size\tdep_used, .-dep_used
\t.type\tdep_transitive, @function
dep_transitive:
\t.loc 2 200 1
\tret
\t.size\tdep_transitive, .-dep_transitive
\t.type\tdep_orphan, @function
dep_orphan:
\t.loc 2 300 1
\tret
\t.size\tdep_orphan, .-dep_orphan
"""


def test_project_function_names_classifies_by_loc_file():
  lines, _ = gb.parse_asm(PRUNE_ASM)
  assert gb.project_function_names(lines, {1}) == {"project_fn"}


def test_prune_keeps_reachable_dependency_code_by_default():
  lines, _ = gb.parse_asm(PRUNE_ASM)
  kept, dropped = gb.prune_dependency_functions(lines, {1})
  funcs = {ln.func for ln in kept if ln.func}
  # dep_used (called by project) and dep_transitive (called by dep_used)
  # survive; dep_orphan — instantiation noise — is gone
  assert funcs == {"project_fn", "dep_used", "dep_transitive"}
  assert dropped == 1


def test_prune_project_only_drops_all_dependency_code():
  lines, _ = gb.parse_asm(PRUNE_ASM)
  kept, dropped = gb.prune_dependency_functions(
    lines, {1}, follow_references=False)
  assert {ln.func for ln in kept if ln.func} == {"project_fn"}
  assert dropped == 3


def test_tu_kind_classifies_scaffolding():
  assert gb.tu_kind("sources/hello/hello.cpp") == "code"
  assert gb.tu_kind("sources/hello/main.cpp") == "code"
  assert gb.tu_kind("sources/hello/hello.test.cpp") == "test"
  assert gb.tu_kind("sources/x/y/big.test/case1.cpp") == "test"
  assert gb.tu_kind("sources/hello/hello.bench.cpp") == "bench"
  assert gb.tu_kind("sources/x/y.bench/main.cpp") == "bench"
  # a directory NAMED test (no dot marker) is ordinary code
  assert gb.tu_kind("sources/attest/impl.cpp") == "code"
