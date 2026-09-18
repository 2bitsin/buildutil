"""The reflect generator's python half, without a toolchain.

The e2e suite proves the whole thing compiles; these pin the decisions that
are easy to regress silently — which comments survive, which files are
selected, and the two properties the design exists for: no catch-all that
turns an unreflected type into an empty scheme, and no `noexcept` on the
emitted definition that would stop it matching the friend declaration.
"""
from __future__ import annotations

import re
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from buildutil import reflect


# --- the tag ----------------------------------------------------------------

def test_the_tag_is_recognised():
  assert reflect.is_tagged(
    "struct S { friend constexpr auto reflect_scheme(S*); };")


def test_the_template_form_of_the_tag_is_recognised():
  assert reflect.is_tagged(
    "template <class T> struct B {\n"
    "  template <class U> friend constexpr auto reflect_scheme(B<U>*);\n};")


def test_an_untagged_header_is_not_selected():
  assert not reflect.is_tagged("struct S { int a; };")


def test_a_mere_mention_is_not_a_tag():
  # the substring appears, so the cheap prefilter passes and the regex has
  # to be the thing that says no
  assert not reflect.is_tagged("// reflect_scheme is how you opt in\n")
  assert not reflect.is_tagged("auto x = reflect_scheme(p);")


# --- the tag, COUNTED --------------------------------------------------------
# The generator holds its own output against what the header promised, so
# "is there a tag" and "how many tags" have to be one question with one
# answer. These pin the counting rules; the shortfall tests below pin what
# happens when the count is not met.

def test_the_scan_and_the_count_are_the_same_call():
  # not "they agree" -- they cannot disagree: is_tagged IS the count being
  # non-empty, so a header the scan never selected can never be found short
  for text in ("struct S { friend constexpr auto reflect_scheme(S*); };",
               "// nothing here", "auto x = reflect_scheme(p);"):
    assert reflect.is_tagged(text) == bool(reflect.tag_declarations(text))


def test_a_tag_is_counted_once_and_named_by_its_type():
  assert reflect.tag_declarations(
    "struct A { friend constexpr auto reflect_scheme(A*); };\n"
    "struct B { friend constexpr auto reflect_scheme(B*); };") == ["A", "B"]


def test_every_opt_in_form_is_counted_and_named():
  # the enum's beside-itself form, and the template form whose parameter is
  # a template-id -- the name stops at the `<` on its own
  assert reflect.tag_declarations(
    "enum class C { A };\n"
    "constexpr auto reflect_scheme(C*);\n"
    "template <class U> friend constexpr auto reflect_scheme(demo::Boxed<U>*);"
  ) == ["C", "Boxed"]


def test_a_commented_out_tag_does_not_inflate_the_count():
  # a false positive here fails a build that is perfectly fine, so a
  # declaration that was only ever TALKED about is not a declaration
  assert reflect.tag_declarations(
    "// friend constexpr auto reflect_scheme(Ghost*);\n"
    "/* friend constexpr auto reflect_scheme(Spectre*); */\n"
    "struct S { friend constexpr auto reflect_scheme(S*); };") == ["S"]
  assert not reflect.is_tagged("// friend constexpr auto reflect_scheme(S*);")


def test_a_tag_inside_a_string_literal_does_not_inflate_the_count():
  assert reflect.tag_declarations(
    'char const* doc = "friend constexpr auto reflect_scheme(Ghost*);";') == []
  assert reflect.tag_declarations(
    'static_assert(x, R"help(constexpr auto reflect_scheme(Ghost*);)help");'
  ) == []


def test_a_digit_separator_does_not_open_a_character_literal():
  # 1'000 is a separator, not a literal; reading it as one would blank the
  # rest of the file and lose every tag written after it
  assert reflect.tag_declarations(
    "int x = 1'000'000;\n"
    "struct S { friend constexpr auto reflect_scheme(S*); };") == ["S"]


def test_blanking_keeps_the_text_the_same_length():
  # offsets have to keep lining up with the original -- everything else in
  # this file (labels, comments) is offset-addressed
  text = ("// a comment\n/* another */\nchar const* s = \"literal\";\n"
          "struct S { friend constexpr auto reflect_scheme(S*); };\n")
  blanked = reflect._code_only(text)
  assert len(blanked) == len(text)
  assert blanked.count("\n") == text.count("\n")


# --- a scheme the header writes itself ---------------------------------------
# A header that tags a type and then defines its scheme by hand is a shape the
# generator has to stay out of: it is how a test proves the framework reads the
# type and not a generated scheme. The definition is the type's own answer --
# nothing left to promise, and a second definition could only collide.

HAND_WRITTEN = ("struct Leaf { friend constexpr auto reflect_scheme(Leaf*); };\n"
                "constexpr auto reflect_scheme(Leaf*)\n"
                "{ return ::reflect::class_scheme<Leaf>{ }; }\n")


def test_a_scheme_defined_beside_its_type_is_not_a_promise():
  assert reflect.tag_declarations(HAND_WRITTEN) == []
  assert not reflect.is_tagged(HAND_WRITTEN)


def test_a_scheme_defined_inline_in_the_class_is_not_a_promise():
  assert reflect.tag_declarations(
    "struct S { friend constexpr auto reflect_scheme(S*) { return 0; } };") == []


def test_the_qualified_spelling_of_a_definition_still_answers_its_tag():
  # the friend declaration writes the type unqualified and the definition
  # writes it through its namespace: one type, and the name is its last part
  assert reflect.tag_declarations(
    "namespace demo { struct Leaf {\n"
    "  friend constexpr auto reflect_scheme(Leaf*); }; }\n"
    "constexpr auto reflect_scheme(demo::Leaf*) { return 0; }\n") == []


def test_a_trailing_return_type_does_not_hide_the_body():
  assert reflect.tag_declarations(
    "struct S { friend constexpr auto reflect_scheme(S*); };\n"
    "constexpr auto reflect_scheme(S*) noexcept -> Scheme(1) { return 0; }") == []


def test_a_declaration_with_a_trailing_return_type_is_still_a_promise():
  assert reflect.tag_declarations(
    "constexpr auto reflect_scheme(S*) noexcept -> Scheme(1);") == ["S"]


def test_a_tagged_neighbour_is_still_promised():
  assert reflect.tag_declarations(
    HAND_WRITTEN + "struct Gen { friend constexpr auto reflect_scheme(Gen*); };"
  ) == ["Gen"]


def test_hand_written_names_the_types_the_header_answers_for():
  assert reflect.hand_written(HAND_WRITTEN) == {"Leaf"}
  assert reflect.hand_written(
    "struct S { friend constexpr auto reflect_scheme(S*); };") == set()
  assert reflect.hand_written("int x = 0;") == set()


def test_a_definition_inside_a_comment_answers_nothing():
  tagged = "struct S { friend constexpr auto reflect_scheme(S*); };\n"
  assert reflect.hand_written(
    "// constexpr auto reflect_scheme(S*) { return 0; }\n" + tagged) == set()
  assert reflect.tag_declarations(
    "// constexpr auto reflect_scheme(S*) { return 0; }\n" + tagged) == ["S"]



# --- output paths -----------------------------------------------------------

def test_the_reflect_path_is_derivable_without_parsing(tmp_path):
  # this is what lets configure time know the output names
  sources = tmp_path / "sources"
  header = sources / "demo" / "thing" / "options.hpp"
  header.parent.mkdir(parents=True)
  header.write_text("")
  assert reflect.reflect_rel(header, sources) == \
    "demo/thing/options.reflect.hpp"


def test_the_detour_takes_the_headers_own_spelling(tmp_path):
  # not a derived name: the detour has to COLLIDE with the header under a
  # root that comes first, because that collision is the whole mechanism
  sources = tmp_path / "sources"
  header = sources / "demo" / "thing" / "options.hpp"
  header.parent.mkdir(parents=True)
  header.write_text("")
  assert reflect.detour_rel(header, sources) == "demo/thing/options.hpp"


# --- the detour -------------------------------------------------------------

def test_the_detour_is_the_real_header_then_its_schemes(tmp_path):
  sources, module, header = _tree(tmp_path)
  text = reflect.detour(header, sources)
  lines = [line for line in text.splitlines() if not line.startswith("//")]
  assert lines == ["#pragma once",
                   '#include "{}"'.format(header.as_posix()),
                   '#include "demo/thing/thing.reflect.hpp"']


def test_the_detour_names_the_real_header_by_absolute_path(tmp_path):
  # a relative spelling would have to be the one this file exists to
  # intercept -- it would include itself. #include_next is the
  # portable-looking alternative and MSVC has not got it.
  sources, module, header = _tree(tmp_path)
  text = reflect.detour(header, sources)
  assert '#include "{}"'.format(header.as_posix()) in text
  assert '#include "demo/thing/thing.hpp"' not in text


def test_the_detour_says_it_is_generated(tmp_path):
  sources, module, header = _tree(tmp_path)
  assert reflect.detour(header, sources).startswith(
    "// generated by buildutil — do not edit.")


def test_the_detour_is_byte_stable(tmp_path):
  # content derived from paths alone, which is what lets _write's content
  # diff keep its mtime still while the schemes beside it churn
  sources, module, header = _tree(tmp_path)
  assert reflect.detour(header, sources) == reflect.detour(header, sources)


# --- includes that would go around a detour ---------------------------------

def test_a_quoted_sibling_include_is_a_bypass(tmp_path):
  # the quoted form searches the INCLUDING file's own directory before any
  # -I path, so it finds the real header and the detour never runs
  sources, module, header = _tree(tmp_path)
  (module / "user.cpp").write_text('#include "thing.hpp"\n')
  found = reflect.bypasses(sources, [header])
  assert len(found) == 1
  path, line, spelling, quoted, correct = found[0]
  assert path == module / "user.cpp"
  assert (line, spelling, quoted) == (1, "thing.hpp", True)
  assert correct == "demo/thing/thing.hpp"


def test_a_bare_angle_include_from_inside_the_module_is_a_bypass(tmp_path):
  # a module's own directory is on its private include path, and it is not
  # the generated root
  sources, module, header = _tree(tmp_path)
  (module / "user.cpp").write_text("#include <thing.hpp>\n")
  found = reflect.bypasses(sources, [header])
  assert [(f[2], f[3]) for f in found] == [("thing.hpp", False)]


def test_the_sources_relative_angle_spelling_is_the_one_that_works(tmp_path):
  sources, module, header = _tree(tmp_path)
  (module / "user.cpp").write_text("#include <demo/thing/thing.hpp>\n")
  assert reflect.bypasses(sources, [header]) == []


def test_a_quoted_sources_relative_include_from_elsewhere_is_fine(tmp_path):
  # quoted, but the including file's own directory does not contain that
  # path, so the -I search runs and the generated root answers first
  sources, module, header = _tree(tmp_path)
  other = sources / "demo" / "other"
  other.mkdir(parents=True)
  (other / "user.cpp").write_text('#include "demo/thing/thing.hpp"\n')
  assert reflect.bypasses(sources, [header]) == []


def test_a_quoted_sibling_include_of_an_UNTAGGED_header_is_fine(tmp_path):
  # the real header is parsed IN PLACE, so its own neighbours still resolve
  # exactly as they did -- this is the whole advantage of wrapping over
  # copying, and refusing it would be a false alarm
  sources, module, header = _tree(tmp_path)
  (module / "shared.hpp").write_text("#pragma once\n")
  header.write_text('#include "shared.hpp"\n'
                    "struct S { friend constexpr auto reflect_scheme(S*); };")
  assert reflect.bypasses(sources, [header]) == []


def test_a_commented_out_include_is_not_a_bypass(tmp_path):
  # this refusal is a hard error; refusing a build over a spelling somebody
  # commented out years ago is not a trade anyone would take
  sources, module, header = _tree(tmp_path)
  (module / "user.cpp").write_text(
    '// #include "thing.hpp"\n/* #include "thing.hpp" */\n')
  assert reflect.bypasses(sources, [header]) == []


def test_the_refusal_names_the_site_and_the_spelling_to_use(tmp_path):
  sources, module, header = _tree(tmp_path)
  (module / "user.cpp").write_text('#include <string>\n#include "thing.hpp"\n')
  said = reflect.bypass_message(tmp_path, reflect.bypasses(sources, [header]))
  assert "sources/demo/thing/user.cpp:2" in said
  assert '#include "demo/thing/thing.hpp"' in said
  assert "silently not\n  reflected here" in said


def test_include_directives_keep_the_bracket_and_the_line(tmp_path):
  # quoted and angled resolve differently, and telling them apart is the
  # whole of the check above
  text = '#include <a/b.hpp>\n\n  #  include   "c.hpp"\n'
  assert reflect.include_directives(text) == [
    (1, "a/b.hpp", False), (3, "c.hpp", True)]


# --- which .cpp needs which -------------------------------------------------

def _tree(tmp_path):
  sources = tmp_path / "sources"
  module = sources / "demo" / "thing"
  module.mkdir(parents=True)
  header = module / "thing.hpp"
  header.write_text("struct S { friend constexpr auto reflect_scheme(S*); };")
  return sources, module, header


def test_a_qualified_include_maps_a_source_to_its_header(tmp_path):
  sources, module, header = _tree(tmp_path)
  user = module / "user.cpp"
  user.write_text('#include <demo/thing/thing.hpp>\n')
  mapping = reflect.sources_needing([user], [header], sources)
  assert mapping == {user: [header]}


def test_a_quoted_sibling_include_maps_too(tmp_path):
  sources, module, header = _tree(tmp_path)
  user = module / "user.cpp"
  user.write_text('#include "thing.hpp"\n')
  assert reflect.sources_needing([user], [header], sources) == {user: [header]}


def test_a_source_that_includes_nothing_reflected_is_left_alone(tmp_path):
  sources, module, header = _tree(tmp_path)
  bystander = module / "bystander.cpp"
  bystander.write_text("#include <string>\nint f() { return 0; }\n")
  assert reflect.sources_needing([bystander], [header], sources) == {}


# --- comments ---------------------------------------------------------------

def test_a_trailing_block_comment_loses_its_markers():
  assert reflect.normalize_comment("/* say more */") == "say more"


def test_a_multiline_trailing_run_keeps_its_lines():
  # the continuations are column-aligned under the member, so the marker has
  # to be matched after leading whitespace — getting this wrong leaves "//"
  # embedded in every line but the first
  raw = "// first\n                    // second\n                    // third"
  assert reflect.normalize_comment(raw) == "first\nsecond\nthird"


def test_relative_indentation_inside_a_comment_survives():
  raw = "// heading\n   //   indented\n   // flush"
  assert reflect.normalize_comment(raw) == "heading\n  indented\nflush"


def test_a_block_comments_leading_stars_go():
  assert reflect.normalize_comment("/* one\n * two\n */") == "one\ntwo"


def test_no_comment_is_the_empty_string():
  assert reflect.normalize_comment(None) == ""
  assert reflect.normalize_comment("/* */") == ""


def test_two_adjacent_blocks_lose_their_interior_markers():
  # clang's raw_comment concatenates adjacent blocks delimiters and all, so
  # stripping the outermost pair only left "…url */\n/* (can also…" in the
  # help text of every option written this way
  raw = ("/* llama server's base url */\n"
         "                            /* (can also be set via $URL) */")
  assert reflect.normalize_comment(raw) == ("llama server's base url\n"
                                            "(can also be set via $URL)")


def test_three_adjacent_blocks_become_three_lines():
  raw = "/* one */\n   /* two */\n   /* three */"
  assert reflect.normalize_comment(raw) == "one\ntwo\nthree"


def test_two_blocks_on_one_line_merge_too():
  assert reflect.normalize_comment("/* one */ /* two */") == "one\ntwo"


def test_a_block_followed_by_a_line_run_merges():
  # the token walk joins spellings itself, so a method's trailing comment can
  # mix the two forms even where raw_comment would refuse to
  raw = "/* a block first */\n// then a line\n// and another line"
  assert reflect.normalize_comment(raw) == ("a block first\n"
                                            "then a line\n"
                                            "and another line")


def test_a_line_run_stays_one_comment():
  # a `//` run is ONE comment: its lines share an indent that only means
  # anything read together, so splitting it would flatten the relative indent
  raw = "// heading\n   //   indented\n   // flush"
  assert reflect.normalize_comment(raw) == "heading\n  indented\nflush"


def test_a_single_block_is_untouched_by_the_split():
  assert reflect.normalize_comment("/* say more */") == "say more"
  assert reflect.normalize_comment("/* one\n * two\n */") == "one\ntwo"
  # a block whose body contains the other form's marker is still one block
  assert reflect.normalize_comment("/* see // there */") == "see // there"


def test_an_empty_block_between_two_others_is_a_blank_line():
  assert reflect.normalize_comment("/* a */\n/* */\n/* b */") == "a\n\nb"


# --- string literals --------------------------------------------------------

def test_comment_text_is_escaped_into_a_literal():
  assert reflect.cxx_string('a "b" \\ c') == '"a \\"b\\" \\\\ c"'
  assert reflect.cxx_string("one\ntwo") == '"one\\ntwo"'


# --- the support header -----------------------------------------------------

def test_the_support_header_takes_the_configured_namespace():
  assert "namespace acme {" in reflect.support_header("acme")


def test_the_tag_name_does_not_follow_the_namespace():
  # the tag is a fixed literal — it is what the user types and what the grep
  # looks for, and those two must not be able to drift
  text = reflect.support_header("acme")
  assert "reflect_scheme(static_cast<T*>(nullptr))" in text
  assert "acme_scheme(" not in text


def test_there_is_no_catch_all_that_fakes_an_empty_scheme():
  # the prototype had `_impl_scheme_of(...)` returning class_scheme<>, which
  # turns "codegen did not run" into "this command has no options", silently
  text = reflect.support_header()
  assert "(...)" not in text
  assert "static_assert(reflected<T>" in text


def test_the_support_header_itself_defines_no_macro():
  # The rule is still "no macros", and the two deliberate exceptions live in
  # a header of their own -- which is also what makes them replaceable.
  text = reflect.support_header()
  defines = [line.strip() for line in text.splitlines()
             if line.strip().startswith("#define")]
  assert defines == ["#define BUILDUTIL_REFLECT_V1_HPP"], defines
  assert text.count("struct call_traits<") == 13


def test_the_pack_indexing_fallback_is_gated_on_the_feature_test_macro():
  # not a hand-defined macro: MSVC has no C++26 core support, so a
  # hand-rolled switch would take the pack-indexing branch and fail
  assert "__cpp_pack_indexing" in reflect.support_header()


# --- emitted code -----------------------------------------------------------

def _entry():
  return reflect.Reflected(
    name="Options", namespaces=["demo"], template_params=[],
    members=[("verbose", "say more", False), ("attempts", "", True)],
    params=[("first", "the first one")])


def test_the_definition_carries_no_specifier_the_tag_lacks():
  # `constexpr auto reflect_scheme(T*)` and nothing else: a noexcept or a
  # consteval here makes it a DIFFERENT function from the friend
  # declaration, so the tag declares one that never gets defined
  text = reflect.emit([_entry()], "demo/thing/thing.hpp")
  assert "constexpr auto reflect_scheme(Options*)\n" in text
  assert "noexcept" not in text
  assert "consteval" not in text


def test_the_generated_header_is_self_sufficient():
  # -include puts it at the top of the TU, before the user's own include
  text = reflect.emit([_entry()], "demo/thing/thing.hpp")
  assert "#include <demo/thing/thing.hpp>" in text
  assert "#include <_buildutil/reflect.hpp>" in text


def test_behind_a_detour_it_does_not_name_its_header_again():
  # the detour included that header one line above this one, so the
  # back-include is a pragma-once no-op -- and a line that sends a reader
  # looking for a cycle that is not there
  text = reflect.emit([_entry()], "demo/thing/thing.hpp", back_include=False)
  assert "#include <demo/thing/thing.hpp>" not in text
  assert "#include <_buildutil/reflect.hpp>" in text
  assert "constexpr auto reflect_scheme(Options*)" in text


def test_private_members_are_marked_encapsulated():
  text = reflect.emit([_entry()], "demo/thing/thing.hpp")
  assert '"verbose", &Options::verbose, "say more", false' in text
  assert '"attempts", &Options::attempts, "", true' in text


def test_a_class_template_reapplies_its_parameters():
  entry = reflect.Reflected("Boxed", ["demo"], ["T"],
                            [("value", "", False)], None)
  text = reflect.emit([entry], "demo/thing/box.hpp")
  assert "template <typename T>" in text
  assert "reflect_scheme(Boxed<T>*)" in text
  assert "&Boxed<T>::value" in text


def test_generated_code_is_fully_qualified():
  # a `using namespace` in a header leaks into every TU downstream
  text = reflect.emit([_entry()], "demo/thing/thing.hpp")
  assert "using namespace" not in text
  assert "::reflect::class_scheme<" in text


def test_declarations_are_cmake_calls_not_data(tmp_path):
  sources, module, header = _tree(tmp_path)
  user = module / "user.cpp"
  user.write_text('#include "thing.hpp"\n')
  text = reflect.declarations([header], sources, tmp_path, {user: [header]},
                              detours=False)
  assert '_buildutil_reflect_declare("sources/demo/thing/thing.hpp" ' \
         '"demo/thing/thing.reflect.hpp" ' \
         '"" "sources/demo/thing/user.cpp")' in text


def test_the_declaration_names_the_detour_and_no_users(tmp_path):
  # The detour needs no .cpp -> header mapping, so the users field that the
  # force-include path lives on is simply empty -- there is nothing to
  # place, per TU or otherwise.
  sources, module, header = _tree(tmp_path)
  text = reflect.declarations([header], sources, tmp_path, {})
  assert '_buildutil_reflect_declare("sources/demo/thing/thing.hpp" ' \
         '"demo/thing/thing.reflect.hpp" ' \
         '"demo/thing/thing.hpp" "")' in text


# --- what the generated root stops owning -----------------------------------
# The generated root is ahead of sources/ on every include path, so a detour
# left behind by a header that was renamed, deleted or untagged keeps
# shadowing the spelling it was made for -- and the schemes beside it never
# change again. That is a green build that has stopped telling the truth.

def _generated(root: Path, *paths: str) -> Path:
  where = root / "generated"
  for rel in paths:
    path = where / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("// generated by buildutil — do not edit.\n")
  return where


def test_the_manifest_records_what_the_scan_owns(tmp_path):
  sources, module, header = _tree(tmp_path)
  where = tmp_path / "generated"
  reflect.prune(where, reflect.owned([header], sources, detours=True))
  assert (where / reflect.MANIFEST).read_text().split() == [
    "_buildutil/reflect-macros.hpp",
    "_buildutil/reflect.hpp",
    "demo/thing/thing.hpp",
    "demo/thing/thing.reflect.hpp",
    "demo/thing/thing.reflect.hpp.d"]


def test_a_file_the_scan_no_longer_owns_is_removed(tmp_path):
  sources, module, header = _tree(tmp_path)
  where = _generated(tmp_path, "demo/thing/thing.reflect.hpp",
                     "demo/thing/thing.reflect.hpp.d", "demo/thing/thing.hpp")
  reflect.prune(where, reflect.owned([header], sources, detours=True))

  # the header is renamed, deleted or untagged: it is out of the scan, and
  # so is everything that was generated from it
  gone = reflect.prune(where, reflect.owned([], sources, detours=True))
  assert sorted(gone) == ["demo/thing/thing.hpp",
                          "demo/thing/thing.reflect.hpp",
                          "demo/thing/thing.reflect.hpp.d"]
  assert not (where / "demo").exists(), "an emptied directory stayed behind"


def test_what_another_generator_wrote_is_left_alone(tmp_path):
  # Add_generated_source and the configure hooks write into the SAME root,
  # so ownership is read from the manifest and never inferred from the tree
  sources, module, header = _tree(tmp_path)
  where = _generated(tmp_path, "demo/thing/theirs.hpp")
  reflect.prune(where, reflect.owned([header], sources, detours=True))
  reflect.prune(where, reflect.owned([], sources, detours=True))
  assert (where / "demo" / "thing" / "theirs.hpp").is_file()


def test_before_there_is_a_manifest_the_two_known_shapes_still_go(tmp_path):
  # the first scan after an upgrade meets a root full of files it never
  # recorded: a `*.reflect.hpp` and its depfile are named by nothing else,
  # and the detour beside them says in its first line what it is
  sources, module, header = _tree(tmp_path)
  where = _generated(tmp_path, "demo/old/x.reflect.hpp",
                     "demo/old/x.reflect.hpp.d", "demo/other/theirs.hpp")
  (where / "demo" / "old" / "x.hpp").write_text(
    reflect.detour(sources / "demo" / "old" / "x.hpp", sources))

  gone = reflect.prune(where, reflect.owned([header], sources, detours=True))
  assert sorted(gone) == ["demo/old/x.hpp", "demo/old/x.reflect.hpp",
                          "demo/old/x.reflect.hpp.d"]
  assert (where / "demo" / "other" / "theirs.hpp").is_file()


# --- libclang selection -----------------------------------------------------

def test_a_system_libclang_older_than_the_bindings_is_refused(monkeypatch):
  # `apt install clang` on a debian base hands you clang 14, and 18.1.1
  # bindings on a 14 libclang.so abort mid-parse with "check that your
  # python bindings are compatible" -- a CI failure, not a graceful one
  monkeypatch.setattr(reflect, "_system_libclang", lambda: "/usr/lib/libclang.so")
  monkeypatch.setattr(reflect, "_clang_major", lambda: 14)
  assert reflect._usable_system_libclang(18) is None


def test_a_newer_system_libclang_is_used():
  # older bindings against a newer library are fine: the C API only grows
  if reflect._system_libclang() is None or reflect._clang_major() is None:
    return
  assert reflect._usable_system_libclang(18) is not None


def test_no_bindings_version_means_take_what_is_there(monkeypatch):
  monkeypatch.setattr(reflect, "_system_libclang", lambda: "/usr/lib/libclang.so")
  monkeypatch.setattr(reflect, "_clang_major", lambda: 14)
  monkeypatch.setattr(reflect, "_exports_c_api", lambda _: True)
  assert reflect._usable_system_libclang(0) == "/usr/lib/libclang.so"


def test_a_library_without_the_c_api_is_refused(monkeypatch):
  # the osxcross image: /usr/lib/llvm-18/lib holds libclang-cpp.so.18.1
  # (the C++ API) beside libclang-18.so.18 (the C API), and a filename
  # alone cannot be trusted -- the probe loads the library and asks
  monkeypatch.setattr(reflect, "_system_libclang",
                      lambda: "/usr/lib/llvm-18/lib/libclang-cpp.so.18.1")
  monkeypatch.setattr(reflect, "_clang_major", lambda: 18)
  monkeypatch.setattr(reflect, "_exports_c_api", lambda _: False)
  assert reflect._usable_system_libclang(18) is None


def test_the_c_api_probe_rejects_what_will_not_load():
  assert reflect._exports_c_api("/no/such/library.so") is False


def test_candidates_never_offer_the_cpp_api_library(tmp_path):
  # reverse-sorting for "newest first" would put libclang-cpp.so.18.1
  # ahead of libclang-18.so ('c' > '1'), which is exactly the trap
  lib = tmp_path / "lib"
  lib.mkdir()
  for name in ("libclang-cpp.so.18.1", "libclang-18.so.18", "libclang.so.1"):
    (lib / name).touch()
  (tmp_path / "bin").mkdir()
  exe = tmp_path / "bin" / "clang"
  exe.touch()
  offered = [Path(c).name for c in reflect._libclang_candidates(exe, None)]
  assert "libclang-cpp.so.18.1" not in offered
  assert "libclang-18.so.18" in offered


# --- enums ------------------------------------------------------------------

def test_an_enum_opts_in_without_friend():
  # an enum has no member-declaration list, so there is nothing to befriend;
  # it declares the entry point beside itself instead, and needs no
  # friendship because enumerators are public anyway
  assert reflect.is_tagged("enum class C { A };\n"
                           "constexpr auto reflect_scheme(C*);")


def test_the_diagnostic_names_both_opt_in_forms():
  text = reflect.support_header()
  assert "friend constexpr auto reflect_scheme(YourType*);" in text
  assert "constexpr auto reflect_scheme(YourEnum*);" in text


def test_an_enum_scheme_carries_names_values_and_comments():
  entry = reflect.ReflectedEnum(
    "Colour", ["app"], [("RED", "the warm one"), ("BLUE", "")])
  text = reflect.emit([], "app/colour.hpp", enums=[entry])
  assert '::reflect::enumerator<"RED", Colour::RED, "the warm one">' in text
  assert '::reflect::enumerator<"BLUE", Colour::BLUE, "">' in text
  assert "constexpr auto reflect_scheme(Colour*)" in text
  assert "::reflect::enum_scheme<" in text


def test_enums_are_emitted_before_classes():
  # a member of enum type wants its enum's scheme already visible
  enum = reflect.ReflectedEnum("Colour", ["app"], [("RED", "")])
  text = reflect.emit([_entry()], "app/thing.hpp", enums=[enum])
  assert text.index("enum_scheme") < text.index("class_scheme")


# --- methods ----------------------------------------------------------------

def test_a_method_scheme_carries_its_own_parameters():
  entry = reflect.Reflected(
    "Server", ["app"], [], [], None,
    [("perform_action", "a subcommand", [("times", "how many")]),
     ("stop", "", [])])
  text = reflect.emit([entry], "app/server.hpp")
  assert "constexpr auto reflect_interface_scheme(Server*)" in text
  assert '::reflect::method_scheme<"perform_action", &Server::perform_action,' in text
  assert '::reflect::param_scheme<"times", "how many">' in text
  assert "::reflect::call_scheme<>>" in text          # stop takes nothing


def test_a_type_with_no_methods_emits_no_interface_scheme():
  text = reflect.emit([_entry()], "demo/thing/thing.hpp")
  assert "reflect_interface_scheme" not in text


def test_call_traits_covers_every_qualifier_combination():
  # a subcommand-returning method may be spelled with any of them, and a
  # missing one surfaces as an incomplete type pointing at the support
  # header rather than at the user's class
  text = reflect.support_header()
  assert text.count("struct call_traits<") == 13   # 12 forms + the const passthrough
  assert "struct call_traits<F const> : call_traits<F>" in text


def test_a_zero_parameter_call_operator_still_gets_a_scheme():
  # "takes nothing" and "has no call operator" are different answers, and a
  # command declaring operator()() means the first must be enforceable
  with_none = reflect.Reflected("P", ["app"], [], [], [])
  without = reflect.Reflected("Q", ["app"], [], [], None)
  assert "reflect_call_scheme(P*)" in reflect.emit([with_none], "a.hpp")
  assert "takes no arguments" in reflect.emit([with_none], "a.hpp")
  assert "reflect_call_scheme" not in reflect.emit([without], "a.hpp")


# --- comments on methods ----------------------------------------------------

# The two predicates below ARE the attribution rule, and they are pure
# functions over the source bytes -- so they are tested here, where no
# libclang is needed, and the whole model is then tested against a real
# parse in test_reflect_e2e.py.

def _at(source: str, needle: str) -> int:
  return source.index(needle)


def test_a_comment_after_code_on_its_line_trails_it():
  source = b"int a { 0 };   // about a\n"
  assert reflect._follows_code(source, _at(source, b"// about a"), 0)


def test_a_comment_alone_on_its_line_does_not():
  source = b"int a { 0 };\n// about b\nint b { 0 };\n"
  assert not reflect._follows_code(source, _at(source, b"// about b"), 12)


def test_a_second_comment_beside_the_first_is_not_following_code():
  # two blocks side by side are one comment written twice, and the first
  # must not make the second look like a trailing comment on its own
  source = b"/* one */ /* two */\n"
  first = _at(source, b"/* one */")
  second = _at(source, b"/* two */")
  assert not reflect._follows_code(source, second, first + len(b"/* one */"))


def _block(start, column, trailing):
  return reflect._Block(start, 1, 1, column, trailing, [])


def test_an_aligned_line_continues_a_trailing_comment():
  # the continuation style this codebase writes: the lines under a trailing
  # comment are indented to its column, and that column is what tells them
  # from the next declaration's own flush-left introduction
  source = b"int a { 0 };   // one\n               // two\n"
  previous = _block(_at(source, b"// one"), 16, True)
  current = _block(_at(source, b"// two"), 16, False)
  assert reflect._joins(source, _at(source, b"// one") + 6, current, previous)


def test_a_flush_left_line_under_a_trailing_comment_does_not():
  source = b"int a { 0 };   // about a\n// about b\nint b { 0 };\n"
  previous = _block(_at(source, b"// about a"), 16, True)
  current = _block(_at(source, b"// about b"), 1, False)
  assert not reflect._joins(source, _at(source, b"// about a") + 10,
                            current, previous)


def test_a_blank_line_ends_a_block():
  source = b"// one\n\n// two\n"
  previous = _block(0, 1, False)
  current = _block(_at(source, b"// two"), 1, False)
  assert not reflect._joins(source, 6, current, previous)


def test_code_between_two_comments_ends_a_block():
  source = b"// one\nint a { 0 };\n// two\n"
  previous = _block(0, 1, False)
  current = _block(_at(source, b"// two"), 1, False)
  assert not reflect._joins(source, 6, current, previous)


# --- base classes -----------------------------------------------------------

def _base(written, canonical=None, access="PUBLIC"):
  """A stand-in for a CXX_BASE_SPECIFIER cursor.

  Only four things are asked of the real one, and a fake carries them
  without a toolchain -- which is what keeps the decisions below (which
  bases are captured, and how each is spelled) covered on a machine with no
  clang bindings. The spellings are VERBATIM libclang 18 output; see the
  cases in test_reflect_e2e.py for the same shapes through a real parse.
  """
  canonical = written if canonical is None else canonical
  a_type = SimpleNamespace(spelling=written)
  a_type.get_canonical = lambda: SimpleNamespace(spelling=canonical)
  return SimpleNamespace(type=a_type,
                         access_specifier=SimpleNamespace(name=access))


def _clang(*virtual):
  """cindex's module object, with the one C function _base_name reaches for
  when the bindings have no `Cursor.is_virtual_base` -- which is the case
  for the libclang 18.1.1 wheel buildutil pins."""
  lib = SimpleNamespace(
    clang_isVirtualBase=lambda base: any(base is v for v in virtual))
  return SimpleNamespace(conf=SimpleNamespace(lib=lib))


def test_a_base_is_named_by_its_fully_qualified_canonical_spelling():
  # libclang's `type.spelling` is the SOURCE spelling: a base written
  # `Local` from inside its own namespace comes back as `Local`, which
  # resolves to the wrong thing from the reflect header. The canonical
  # spelling is `demo::Local`, and the leading `::` stops a nested
  # namespace of the same name from winning the lookup.
  assert reflect._base_name(_clang(), _base("Local", "demo::Local"), []) \
    == "::demo::Local"
  assert reflect._base_name(_clang(), _base("acme::Base", "acme::Base"), []) \
    == "::acme::Base"


def test_an_alias_base_is_named_by_the_type_it_aliases():
  # `using Alias = acme::Base; struct D : Alias` -- canonical resolves the
  # alias, which is what a consumer asking `reflected<BASE>` needs
  assert reflect._base_name(_clang(), _base("Alias", "acme::Base"), []) \
    == "::acme::Base"


def test_a_template_id_base_keeps_its_arguments():
  assert reflect._base_name(_clang(), _base("Box<int>", "demo::Box<int>"), []) \
    == "::demo::Box<int>"


def test_a_protected_or_private_base_is_not_captured():
  # not part of the type's interface: no consumer may convert to it, so
  # handing back its name would only hand back something unusable
  assert reflect._base_name(
    _clang(), _base("Plain", access="PRIVATE"), []) is None
  assert reflect._base_name(
    _clang(), _base("Plain", access="PROTECTED"), []) is None


def test_a_virtual_base_is_not_captured():
  # a virtual base is SHARED, so a fold over the lattice would meet the same
  # subobject through several branches with no way to tell they are one
  base = _base("acme::Base", "acme::Base")
  assert reflect._base_name(_clang(base), base, []) is None


def test_the_bindings_own_virtual_query_is_preferred_when_it_exists():
  # newer bindings grow `Cursor.is_virtual_base()`; the ctypes route exists
  # only because 18.1.1's do not have it
  base = _base("acme::Base", "acme::Base")
  base.is_virtual_base = lambda: True
  assert reflect._base_name(_clang(), base, []) is None


def test_a_dependent_base_falls_back_to_what_the_source_said():
  # clang desugars `Box<T>` all the way to `Box<type-parameter-0-0>`, which
  # is printable and unwritable. The source spelling works because the list
  # is emitted at the class's own namespace scope under the same template
  # head.
  assert reflect._base_name(
    _clang(), _base("Box<T>", "Box<type-parameter-0-0>"), ["T"]) == "Box<T>"
  assert reflect._base_name(
    _clang(), _base("T", "type-parameter-0-0"), ["T"]) == "T"


def test_a_dependent_qualified_base_gets_its_typename():
  # a template argument is not one of the contexts where C++20 made
  # `typename` optional, so `T::inner` alone would not compile
  assert reflect._base_name(
    _clang(), _base("T::inner", "type-parameter-0-0::inner"), ["T"]) \
    == "typename T::inner"


def test_a_base_with_no_writable_canonical_name_falls_back_too():
  # `(anonymous namespace)::Hidden` is clang printing something nobody can
  # type; what was written still resolves in the file it was written in
  assert reflect._base_name(
    _clang(), _base("Hidden", "(anonymous namespace)::Hidden"), []) == "Hidden"


def test_a_derived_type_lists_its_bases_ahead_of_its_members():
  entry = reflect.Reflected(
    "Cmd", ["app"], [], [("verbose", "say more", False, False)], None,
    bases=["::app::Command", "::util::Loggable"])
  text = reflect.emit([entry], "app/cmd.hpp")
  assert "::reflect::derived_scheme<\n" \
         "    ::reflect::base_list<::app::Command, ::util::Loggable>,\n" \
         '    ::reflect::member_scheme<"verbose", &Cmd::verbose, ' \
         '"say more", false>\n' in text
  assert "class_scheme" not in text


def test_a_derived_type_with_no_members_of_its_own_still_says_so():
  entry = reflect.Reflected("Tag", ["app"], [], [], None,
                            bases=["::app::Command"])
  text = reflect.emit([entry], "app/tag.hpp")
  assert "::reflect::base_list<::app::Command>\n    /* no members */" in text


def test_a_type_without_bases_emits_what_it_emitted_before_bases_existed():
  # THE compatibility bar for this feature. Pinned as a literal rather than
  # as a property, because "unchanged" is the whole assertion: every header
  # already generated by 0.55 and every consumer written against
  # class_scheme has to stay correct without being touched.
  assert (
    "namespace demo {\n"
    "constexpr auto reflect_scheme(Options*)\n"
    "{\n"
    "  return ::reflect::class_scheme<\n"
    '    ::reflect::member_scheme<"verbose", &Options::verbose, '
    '"say more", false>,\n'
    '    ::reflect::member_scheme<"attempts", &Options::attempts, "", true>\n'
    "  >{ };\n"
    "}\n") in reflect.emit([_entry()], "demo/thing/thing.hpp")


def test_the_support_header_carries_the_base_vocabulary():
  text = reflect.support_header()
  assert "template <typename ... BASE> struct base_list { };" in text
  assert "struct derived_scheme { using bases = BASES; };" in text
  assert "consteval auto bases_of() noexcept" in text
  assert "concept has_reflected_bases" in text


def test_counting_and_indexing_step_over_the_base_list_in_both_branches():
  # derived_scheme matches the generic BOX<ITEM...> form too, with the base
  # list as its first ITEM -- so without a more specialized overload on BOTH
  # sides of the pack-indexing switch, every derived type would report one
  # member too many on one compiler and not the other
  text = reflect.support_header()
  assert text.count(
    "consteval auto scheme_size(derived_scheme<BASES, MEMBER...>)") == 1
  assert text.count(
    "consteval auto scheme_item(derived_scheme<BASES, MEMBER...>)") == 1
  assert text.count(
    "consteval auto scheme_item(derived_scheme<BASES, ITEM0, ITEMn...>)") == 1
  # the fallback recurses on a rebuilt box: dropping BASES there would hand
  # the next step to the generic overload, which counts differently
  assert "scheme_item<INDEX - 1u>(derived_scheme<BASES, ITEMn...>{ })" in text
  assert text.count("consteval auto base_item(base_list<BASE...>)") == 1
  assert text.count("consteval auto base_item(base_list<BASE0, BASEn...>)") == 1


def test_a_base_comes_back_as_a_type_and_never_as_a_value():
  # a base is routinely abstract or has no default constructor, and neither
  # is a reason for it to be unreachable through reflection
  text = reflect.support_header()
  assert "return std::type_identity<BASE...[INDEX]> { };" in text
  assert "return std::type_identity<BASE0> { };" in text


def test_the_captured_subset_of_bases_is_documented_where_it_bites():
  # the limits are not bugs, but a reader hitting one needs to find out why
  # from the header they are already looking at
  text = reflect.support_header()
  assert "DIRECT, PUBLIC, NON-VIRTUAL" in text
  assert "A listed base need NOT itself be reflected." in text


# --- the shortfall: a tagged type that produced no scheme -------------------
# Before this, the parse ran with KeepGoing, a type whose members would not
# resolve was skipped, and `generate` wrote the survivors and exited 0. The
# miss surfaced modules away as `static_assert: type is not reflected`,
# pointing at the use site and never at the parse. The header is the record
# of what should have been there, and it is checked against.

TWO_TAGS = ("struct A { friend constexpr auto reflect_scheme(A*); };\n"
            "struct B { friend constexpr auto reflect_scheme(B*); };\n")


def _promised(tmp_path, text=TWO_TAGS):
  header = tmp_path / "thing.hpp"
  header.write_text(text)
  return header


def _scheme(name):
  return SimpleNamespace(name=name)


def test_a_tagged_type_with_no_scheme_is_a_shortfall(tmp_path):
  short = reflect.shortfall(_promised(tmp_path), [_scheme("A")], [])
  assert short is not None, "the silent drop is back"
  promised, emitted, missing = short
  assert (len(promised), len(emitted)) == (2, 1)
  assert missing == ["B"]


def test_everything_promised_arriving_is_no_shortfall(tmp_path):
  assert reflect.shortfall(
    _promised(tmp_path), [_scheme("A"), _scheme("B")], []) is None


def test_an_enum_counts_towards_what_was_promised(tmp_path):
  header = _promised(
    tmp_path,
    "enum class C { X };\nconstexpr auto reflect_scheme(C*);\n" + TWO_TAGS)
  assert reflect.shortfall(
    header, [_scheme("A"), _scheme("B")], [_scheme("C")]) is None
  short = reflect.shortfall(header, [_scheme("A"), _scheme("B")], [])
  assert short is not None and short[2] == ["C"]


def test_a_name_read_differently_does_not_invent_a_shortfall(tmp_path):
  # COUNTS are the gate, never names. This side reads the name out of the
  # source text and clang reads it out of the AST; where the two spell it
  # differently the answer must be "nothing is missing", not a failed build.
  assert reflect.shortfall(
    _promised(tmp_path), [_scheme("A"), _scheme("Zzz")], []) is None


def test_a_hand_written_scheme_is_no_shortfall(tmp_path):
  # the header both tags Leaf and defines its scheme, and the
  # generator emitted nothing for it -- which is the agreement, not a miss
  assert reflect.shortfall(_promised(tmp_path, HAND_WRITTEN), [], []) is None


def test_a_hand_written_type_leaves_its_tagged_neighbour_promised(tmp_path):
  header = _promised(tmp_path, HAND_WRITTEN + TWO_TAGS)
  assert reflect.shortfall(header, [_scheme("A"), _scheme("B")], []) is None
  short = reflect.shortfall(header, [_scheme("A")], [])
  assert short is not None and short[2] == ["B"]


def test_only_the_types_the_header_leaves_open_are_emitted(tmp_path):
  # clang answers the friend declaration whether or not a definition follows,
  # so the hand-written one is dropped before anything is written
  header = _promised(tmp_path, HAND_WRITTEN + TWO_TAGS)
  entries, enums = reflect.without_hand_written(
    header, [_scheme("Leaf"), _scheme("A"), _scheme("B")], [_scheme("C")])
  assert [entry.name for entry in entries] == ["A", "B"]
  assert [entry.name for entry in enums] == ["C"]


def test_a_hand_written_enum_scheme_is_dropped_too(tmp_path):
  header = _promised(
    tmp_path,
    "enum class C { X };\nconstexpr auto reflect_scheme(C*) { return 0; }\n")
  entries, enums = reflect.without_hand_written(header, [], [_scheme("C")])
  assert (entries, enums) == ([], [])
  assert reflect.shortfall(header, [], []) is None


def test_the_message_names_the_shortfall_the_includes_and_what_clang_said():
  text = reflect.shortfall_message(
    Path("/x/sources/near/thing.hpp"),
    promised=["First", "Second"], emitted=["Second"], missing=["First"],
    includes=["/x/sources", "/x/b/generated"],
    said=["thing.hpp:2:10: error: \'vendor/marker.hpp\' file not found"])
  # (a) the shortfall, by name and by count
  assert "2 type(s) tagged, 1 scheme(s) emitted" in text
  assert "no scheme for: First" in text
  # (b) what clang said -- KeepGoing collected it and nothing ever printed it
  assert "vendor/marker.hpp\' file not found" in text
  # (c) the likely cause, and the include path it actually ran with
  assert "directory the generator was not given" in text
  assert "/x/sources" in text and "/x/b/generated" in text


def test_the_message_says_where_to_look_when_clang_said_nothing():
  text = reflect.shortfall_message(
    Path("/x/thing.hpp"), promised=["First"], emitted=[], missing=["First"],
    includes=[], said=[])
  assert "(none)" in text
  assert "another header" in text


# --- the generate step's include path ---------------------------------------

def _rendered(tmp_path) -> str:
  from buildutil import deposit
  deposit.ensure(tmp_path, {"cmake_option_prefix": "ACME",
                            "module_define_prefix": "ACM"}, ["reflect"])
  return (deposit.cmake_dir(tmp_path) / "reflect.cmake").read_text()


def test_the_generate_step_carries_the_consuming_modules_include_path(tmp_path):
  # Three pieces that are worthless apart, so they are pinned together:
  text = _rendered(tmp_path)
  # the include set is the module target\'s own, read as a GENERATOR
  # expression -- at generate time it also carries every linked dependency\'s
  # INTERFACE_INCLUDE_DIRECTORIES, which is the whole point
  assert "$<TARGET_PROPERTY:${lib},INCLUDE_DIRECTORIES>" in text
  # repeatable --include-dir, never a raw -I: the flag is the interface
  assert "--include-dir" in text
  assert "-I$<" not in text
  # ... and the joined form only becomes separate arguments with this
  assert "COMMAND_EXPAND_LISTS" in text


def test_an_empty_include_path_leaves_no_dangling_flag(tmp_path):
  # without the guard the argument survives as a bare `--include-dir` and
  # swallows whatever came after it
  text = _rendered(tmp_path)
  assert "$<$<BOOL:${dirs}>:--include-dir" in text
  assert 'set(dirs "$<TARGET_PROPERTY:${lib},INCLUDE_DIRECTORIES>")' in text


# ===========================================================================
# annotations: the attribute tier, and the macros that sugar it
# ===========================================================================
#
# The reading of real declarations happens against a real parse, in
# test_reflect_e2e.py. What is here is everything that is a pure function --
# the token walk, the positional binding rule, the two macro expansions, the
# emitted spellings -- because those are what regress silently.

# A lexer good enough for the shapes an annotation is written in: string
# literals whole, `::` as one token, everything else a word or a character.
# libclang lexes these the same way, which is what makes the tests below
# mean something (see the tokens printed in the e2e file's fixtures).
_TOKEN_RE = re.compile(r'"[^"]*"|::|\w+|\S')


def _lex(text: str) -> list:
  return [reflect._Token(m.group(), m.start(), m.end())
          for m in _TOKEN_RE.finditer(text)]


def _runs(text: str, macros=None):
  return reflect.annotation_runs(
    _lex(text), text.encode(),
    reflect.BUILTIN_MACROS if macros is None else macros, "x.hpp")


def _items(text: str, macros=None):
  return [item for run in _runs(text, macros) for item in run.items]


# --- reading the two tiers --------------------------------------------------

def test_an_attribute_is_read_with_its_argument_unquoted():
  assert _items('[[buildutil::label("system")]]') == [
    (reflect.Annotation.LABEL, ("system",))]


def test_the_macro_tier_reads_the_same_without_quotes():
  # `_Label(system)` and [[buildutil::label("system")]] are the same
  # annotation written two ways, and they have to arrive identical
  assert _items("_Label(system)") == _items('[[buildutil::label("system")]]')


def test_a_label_the_lexer_would_split_arrives_whole():
  # content-type is three tokens; only the original bytes know they were
  # written adjacent, which is why the argument is sliced and not joined
  assert _items("_Label(content-type)") == [
    (reflect.Annotation.LABEL, ("content-type",))]


def test_meta_keeps_every_argument_in_order():
  assert _items('[[buildutil::meta("wire", "hot")]]') == [
    (reflect.Annotation.META, ("wire", "hot"))]
  assert _items('_Meta("wire", "hot")') == _items(
    '[[buildutil::meta("wire", "hot")]]')


def test_two_annotations_in_one_specifier_are_both_read():
  assert _items('[[buildutil::label("a"), buildutil::meta("b")]]') == [
    (reflect.Annotation.LABEL, ("a",)), (reflect.Annotation.META, ("b",))]


def test_the_using_prefix_form_is_read_too():
  # [[using ns: a, b]] is a legal spelling of the same thing, and reading it
  # is also what keeps a typo inside it from escaping validation
  assert _items('[[using buildutil: label("a"), meta("b")]]') == [
    (reflect.Annotation.LABEL, ("a",)), (reflect.Annotation.META, ("b",))]


def test_somebody_elses_attribute_is_not_buildutils_business():
  assert _items("[[nodiscard]] [[gnu::pure]]") == []


def test_adjacent_annotations_are_one_run():
  # what binds is the RUN's outer edges, so a buildutil attribute written
  # beside a foreign one still touches the declaration next to it
  runs = _runs('[[nodiscard]] [[buildutil::label("a")]] _Meta(b)')
  assert len(runs) == 1
  assert runs[0].items == [(reflect.Annotation.LABEL, ("a",)),
                           (reflect.Annotation.META, ("b",))]


def test_anything_but_whitespace_ends_a_run():
  assert len(_runs('_Label(a) int x; _Label(b)')) == 2


def test_an_unknown_buildutil_attribute_is_refused_by_name():
  # THE recovery for clang's blunt suppression: in attribute mode nothing
  # else in the toolchain will ever mention this typo
  with pytest.raises(reflect.AnnotationError) as bad:
    _items('[[buildutil::lable("system")]]')
  said = str(bad.value)
  assert "buildutil::lable" in said
  assert "help, label, meta" in said, "the valid set is not named"
  assert "x.hpp:1" in said, "the site is not named"


def test_the_refusal_counts_lines_to_the_site():
  with pytest.raises(reflect.AnnotationError, match=r"x\.hpp:3"):
    _items('\n\nint x [[buildutil::labell("a")]];')


def test_a_macro_spelling_the_project_does_not_use_is_just_an_identifier():
  # `macros = "none"` frees the names entirely: with no spellings, _Label is
  # an ordinary token again and nothing reads it
  assert _items("_Label(system)", macros={}) == []


# --- which declaration a run belongs to -------------------------------------
# Positional, because clang's cursor extents are: a class swallows its own
# attribute, an enumerator's `= value` swallows a trailing one, a method
# swallows its trailing one and its parameters' both. The rule is the same
# one the comment model uses, and these are its three cases.

def _bound(text: str, *spans):
  data = text.encode()
  return [key for _run, key in
          reflect._bind(_runs(text), [(a, b, a) for a, b in spans], data)]


def _span(text: str, part: str):
  at = text.index(part)
  return at, at + len(part)


def test_a_run_the_declaration_after_it_touches_leads_it():
  text = '_Label(a) int x;'
  assert _bound(text, _span(text, "int x")) == [text.index("int x")]


def test_a_run_the_declaration_before_it_touches_trails_it():
  text = 'RED _Label(a), BLUE'
  assert _bound(text, _span(text, "RED"), _span(text, "BLUE")) == [0]


def test_a_run_the_declaration_swallowed_belongs_to_it():
  # `USER [[...]] = 5`: the initializer stretches the enumerator's extent
  # over its own trailing attribute, so neither edge touches anything
  text = 'USER [[buildutil::meta("a")]] = 5'
  assert _bound(text, (0, len(text))) == [0]


def test_a_comma_between_is_not_whitespace():
  # the enumerator case that must NOT bind: a leading attribute there is
  # ill-formed, and reading one would be inventing an annotation
  text = 'RED, [[buildutil::label("a")]] BLUE'
  assert _bound(text, _span(text, "RED")) == []


def test_a_comment_between_binds_to_nothing():
  # the rule `_Label` has always had: adjacency is the whole of it
  text = '_Label(a) /* note */ int x;'
  assert _bound(text, _span(text, "int x")) == []


def test_a_run_with_no_declaration_at_either_end_binds_to_nothing():
  assert _bound('_Label(a)') == []


# --- the macro header -------------------------------------------------------

def test_the_default_expansion_is_still_nothing_at_all():
  # PHASE ONE. Every site in the fleet writes `_Label(x) SYSTEM`, which
  # becomes a LEADING enumerator attribute the moment the macro expands to
  # one -- and that is ill-formed. So the default cannot move until the
  # sites do.
  text = reflect.macro_header(reflect.Expansion.MACRO)
  defines = [line for line in text.splitlines()
             if line.startswith("#define") and "_HPP" not in line]
  assert defines == ["#define _Label(x)", "#define _Meta(...)",
                     "#define _Help(x)"]


def test_attribute_expansion_stringizes_the_label_and_passes_meta_through():
  text = reflect.macro_header(reflect.Expansion.ATTRIBUTE)
  # `#x` is what lets the UNQUOTED spelling survive: _Label(content-type)
  # becomes label("content-type") and not label(content - type)
  assert "#define _Label(x)  [[buildutil::label(#x)]]" in text
  assert "#define _Meta(...) [[buildutil::meta(__VA_ARGS__)]]" in text
  assert "#define _Help(x)   [[buildutil::help(#x)]]" in text


def test_both_expansions_carry_the_same_include_guard():
  # one file, two contents: a project switching modes must not end up with
  # both definitions live in one translation unit
  for mode in reflect.Expansion:
    assert "#ifndef BUILDUTIL_REFLECT_MACROS_V1_HPP" in reflect.macro_header(mode)


def test_the_support_header_includes_the_macro_header():
  # the auto-include, and the whole of "no consumer breakage": a header that
  # says `#include <_buildutil/reflect.hpp>  // for _Label` still gets it
  assert "#include <_buildutil/reflect-macros.hpp>" in reflect.support_header()


def test_turning_the_macros_off_takes_the_include_with_them():
  text = reflect.support_header(
    "reflect", reflect.macro_source("none"))
  assert "reflect-macros.hpp" not in text
  assert "buildutil::label" in text, "the ground truth is still documented"


def test_a_projects_own_macro_file_is_reached_through_the_same_name(tmp_path):
  # one seam: the support header always includes the same path, and what
  # that path contains is the project's choice
  own = tmp_path / "mine.hpp"
  text = reflect.macro_header(reflect.Expansion.MACRO,
                              reflect.macro_source(str(own)))
  assert '#include "{}"'.format(own.as_posix()) in text
  assert "#define _Label" not in text


# --- [reflect] macros / annotation, read once -------------------------------

def test_the_two_words_are_words_and_anything_else_is_a_path():
  assert reflect.macro_source("auto").kind is reflect.Macros.BUILTIN
  assert reflect.macro_source("").kind is reflect.Macros.BUILTIN
  assert reflect.macro_source("none").kind is reflect.Macros.NONE
  found = reflect.macro_source("sources/mine.hpp")
  assert found.kind is reflect.Macros.CUSTOM
  assert found.path == Path("sources/mine.hpp")


def test_the_annotation_switch_names_its_valid_set_when_refused():
  assert reflect.expansion("macro") is reflect.Expansion.MACRO
  assert reflect.expansion("attribute") is reflect.Expansion.ATTRIBUTE
  assert reflect.expansion("") is reflect.Expansion.MACRO
  with pytest.raises(reflect.AnnotationError, match="attribute, macro"):
    reflect.expansion("attributes")


def test_the_builtin_spellings_are_the_three_buildutil_ships():
  assert reflect.macro_spellings(reflect.MACROS_BUILTIN) == {
    "_Label": reflect.Annotation.LABEL, "_Meta": reflect.Annotation.META,
    "_Help": reflect.Annotation.HELP}
  assert reflect.macro_spellings(reflect.macro_source("none")) == {}


def _own_macros(tmp_path, text: str) -> dict:
  path = tmp_path / "mine.hpp"
  path.write_text(text)
  return reflect.macro_spellings(reflect.macro_source(str(path)))


def test_a_projects_own_spellings_are_read_out_of_its_own_file(tmp_path):
  # the escape hatch has to be REAL: the generator reads raw tokens, so a
  # custom spelling it was never told about would expand correctly and
  # reflect nothing at all. The file that defines the sugar is what teaches
  # it -- there is no second list to keep in step.
  assert _own_macros(tmp_path,
                     "#define OX_NAME(x) [[buildutil::label(#x)]]\n"
                     "#define OX_TAG(...) [[buildutil::meta(__VA_ARGS__)]]\n"
                     "#define OX_UNRELATED(x) x\n") == {
    "OX_NAME": reflect.Annotation.LABEL, "OX_TAG": reflect.Annotation.META}


def test_a_continued_define_is_still_one_logical_line(tmp_path):
  assert _own_macros(tmp_path, "#define OX_NAME(x) \\\n"
                               "  [[buildutil::label(#x)]]\n") == {
    "OX_NAME": reflect.Annotation.LABEL}


def test_a_typo_in_the_projects_own_file_is_refused_by_name(tmp_path):
  with pytest.raises(reflect.AnnotationError, match="buildutil::lable"):
    _own_macros(tmp_path, "#define OX_NAME(x) [[buildutil::lable(#x)]]\n")


def test_a_file_that_defines_no_annotation_is_refused(tmp_path):
  # silently reading nothing is the failure this whole feature exists to
  # avoid, so pointing at the wrong file has to say so
  with pytest.raises(reflect.AnnotationError, match="no annotation macro"):
    _own_macros(tmp_path, "#define OX_NAME(x) x\n")


def test_an_unreadable_file_names_itself(tmp_path):
  with pytest.raises(reflect.AnnotationError, match="cannot be read"):
    reflect.macro_spellings(reflect.macro_source(str(tmp_path / "nope.hpp")))


def test_the_scan_owns_the_macro_header_and_only_when_it_writes_one(tmp_path):
  sources, module, header = _tree(tmp_path)
  assert "_buildutil/reflect-macros.hpp" in reflect.owned(
    [header], sources, detours=True)
  assert "_buildutil/reflect-macros.hpp" not in reflect.owned(
    [header], sources, detours=True, macros=reflect.macro_source("none"))


# --- the support types the tags land in -------------------------------------

def test_the_support_header_carries_the_tag_vocabulary():
  text = reflect.support_header()
  assert "template <FixedString ... TAG>\n  struct tag_list" in text
  assert "static constexpr std::size_t SIZE { sizeof...(TAG) };" in text
  assert "consteval auto type_scheme_of() noexcept" in text


def test_every_scheme_defaults_its_tags_so_nothing_already_written_moves():
  text = reflect.support_header()
  assert text.count("typename    TAGS         = tag_list<>") == 1  # member
  assert text.count("typename    TAGS    = tag_list<>") == 1       # param
  assert text.count("typename    TAGS = tag_list<>") == 1          # method
  assert text.count("typename TAGS = tag_list<>") == 2       # enumerator, type
  assert text.count("using tags = TAGS;") == 4
  assert "using tags       = TAGS;" in text                        # method


# --- what the emitter writes ------------------------------------------------

def _tagged_entry():
  return reflect.Reflected(
    name="Options", namespaces=["demo"], template_params=[],
    members=[("verbose", "say more", False, False, "", ("cli", "hot")),
             ("named", "", False, False, "the-name", ())],
    params=None)


def test_tags_are_emitted_verbatim_and_in_order():
  text = reflect.emit([_tagged_entry()], "demo/thing/thing.hpp")
  assert ('::reflect::member_scheme<"verbose", &Options::verbose, "say more", '
          'false, "", ::reflect::tag_list<"cli", "hot">>') in text


def test_a_defaulted_argument_in_the_middle_is_still_spelled():
  # template arguments are positional: a member with tags and no label has
  # to say `""` where the label goes, or the tags land on the label
  text = reflect.emit([_tagged_entry()], "demo/thing/thing.hpp")
  assert '"say more", false, "", ::reflect::tag_list<' in text
  # ... and one with a label and no tags stops at the label, as before
  assert '"named", &Options::named, "", false, "the-name">' in text


def test_a_declaration_with_neither_emits_exactly_what_it_always_did():
  # THE compatibility bar. Pinned as a literal, because "unchanged" is the
  # whole assertion: every header already generated and every consumer
  # already written has to stay correct without being touched.
  entry = reflect.Reflected(
    "Server", ["app"], [], [("verbose", "say more", False)],
    [("first", "the first one")],
    [("perform", "a subcommand", [("times", "how many")])])
  text = reflect.emit([entry], "app/server.hpp")
  assert '::reflect::member_scheme<"verbose", &Server::verbose, "say more", false>' in text
  assert '::reflect::param_scheme<"first", "the first one">' in text
  assert ('::reflect::method_scheme<"perform", &Server::perform, '
          '"a subcommand",\n'
          '      ::reflect::call_scheme<\n'
          '        ::reflect::param_scheme<"times", "how many">\n'
          '      >>') in text
  assert "tag_list" not in text and "type_scheme" not in text


def test_an_enumerators_tags_ride_after_its_label():
  entry = reflect.ReflectedEnum(
    "Colour", ["app"], [("RED", "the warm one", "red", ("wire",)),
                        ("BLUE", "", "", ())])
  text = reflect.emit([], "app/colour.hpp", enums=[entry])
  assert ('::reflect::enumerator<"RED", Colour::RED, "the warm one", "red", '
          '::reflect::tag_list<"wire">>') in text
  assert '::reflect::enumerator<"BLUE", Colour::BLUE, "">' in text


def test_a_methods_and_a_parameters_annotations_reach_their_schemes():
  entry = reflect.Reflected(
    "Server", ["app"], [], [], None,
    [("stop", "", [("how", "", "mode", ("enum",))], "halt", ("slow",))])
  text = reflect.emit([entry], "app/server.hpp")
  assert ('::reflect::param_scheme<"how", "", "mode", '
          '::reflect::tag_list<"enum">>') in text
  assert '::reflect::call_scheme<\n' in text
  assert '>, "halt", ::reflect::tag_list<"slow">>' in text


def test_a_type_that_says_nothing_about_itself_emits_no_entry_point():
  assert "reflect_type_scheme" not in reflect.emit(
    [_entry()], "demo/thing/thing.hpp")


def test_a_types_own_annotation_rides_its_own_entry_point():
  # class_scheme is a PACK of members, so there is no trailing parameter for
  # a class to grow -- and a second shape would have to be crossed with
  # derived_scheme. A separate entry point costs nothing when absent.
  entry = reflect.Reflected("Options", ["demo"], [], [], None,
                            label="opts", tags=("cli",))
  text = reflect.emit([entry], "demo/thing/thing.hpp")
  assert ("constexpr auto reflect_type_scheme(Options*)\n"
          "{\n"
          '  return ::reflect::type_scheme<"opts", '
          '::reflect::tag_list<"cli">>{ };\n'
          "}") in text


def test_a_class_templates_type_scheme_reapplies_its_head():
  entry = reflect.Reflected("Boxed", ["demo"], ["T"], [], None, tags=("box",))
  text = reflect.emit([entry], "demo/thing/box.hpp")
  assert ("template <typename T>\n"
          "constexpr auto reflect_type_scheme(Boxed<T>*)") in text


def test_an_enums_own_annotation_rides_the_same_entry_point():
  entry = reflect.ReflectedEnum("Colour", ["app"], [("RED", "")],
                                label="colour")
  text = reflect.emit([], "app/colour.hpp", enums=[entry])
  assert 'constexpr auto reflect_type_scheme(Colour*)' in text
  assert '::reflect::type_scheme<"colour">' in text


# --- the cmake half ---------------------------------------------------------

def test_the_scan_and_the_generate_step_both_learn_the_spellings(tmp_path):
  # the generator reads RAW tokens, so it has to be told which identifiers
  # are annotations -- at BOTH phases, or a custom spelling configures fine
  # and reflects nothing
  text = _rendered(tmp_path)
  assert text.count('--macros "${macros}"') == 2
  assert '--annotation "${_buildutil_reflect_annotation}"' in text


def test_the_suppression_flags_are_per_compiler_and_only_in_attribute_mode(
    tmp_path):
  text = _rendered(tmp_path)
  assert 'if(_buildutil_reflect_annotation STREQUAL "attribute")' in text
  # gcc takes the namespace (12+, which is why the version guard is there);
  # clang has no such granularity; MSVC spells it C5030
  assert "-Wno-attributes=buildutil::" in text
  assert "$<VERSION_GREATER_EQUAL:$<CXX_COMPILER_VERSION>,12>" in text
  assert "-Wno-unknown-attributes" in text
  assert "/wd5030" in text


def test_the_projects_own_macro_file_is_named_from_the_repo_root(tmp_path):
  text = _rendered(tmp_path)
  assert 'set(${out} "${CMAKE_SOURCE_DIR}/${spelling}" PARENT_SCOPE)' in text
  # and editing it restates what every header means, so it is a dependency
  assert 'DEPENDS "${header_abs}" ${macro_dep}' in text


def test_the_rendered_extension_carries_the_configured_values(tmp_path):
  from buildutil import deposit
  deposit.ensure(tmp_path, {"cmake_option_prefix": "ACME",
                            "module_define_prefix": "ACM",
                            "reflect_annotation": "attribute",
                            "reflect_macros": "sources/mine.hpp"}, ["reflect"])
  text = (deposit.cmake_dir(tmp_path) / "reflect.cmake").read_text()
  assert 'set(_buildutil_reflect_annotation "attribute")' in text
  assert 'set(_buildutil_reflect_macros "sources/mine.hpp")' in text


def test_the_support_surface_installs_as_a_directory(tmp_path):
  """Found while publishing a package: the headers the scan
  renders are PUBLIC API of any package whose installed headers say
  #include <_buildutil/reflect.hpp>, and nothing installed them -- every
  project that noticed carried its own rule. It is the DIRECTORY, since
  the surface is whatever the generator emitted (reflect.hpp today,
  reflect-macros.hpp beside it in macro mode), and a hand-listed file set
  is how a grown surface ships broken."""
  text = _rendered(tmp_path)
  assert 'install(DIRECTORY "${generated}/_buildutil/"' in text
  assert 'DESTINATION "include/_buildutil"' in text
  assert 'FILES_MATCHING PATTERN "*.hpp"' in text
  # before the no-tagged-headers early return: the support header renders
  # unconditionally, so a project whose schemes are all hand-written
  # still ships it
  pre_scan = text.split("function(_buildutil_ext_pre_scan)")[1]
  assert (pre_scan.index("install(DIRECTORY")
          < pre_scan.index("_buildutil_reflect_annotation STREQUAL"))


# ------------------------------------------------------- the macOS sysroot --
# libclang parses a TU directly and gets none of the favours the clang DRIVER
# does -- and on macOS one of those favours is where the standard library
# lives. Without -isysroot every tagged type collapses and the failure reads
# as a missing PROJECT include, which is what it cost a Mac run.

def test_the_sdk_is_passed_to_libclang_on_darwin(monkeypatch):
  monkeypatch.setattr(reflect.platform, "system", lambda: "Darwin")
  monkeypatch.setenv("SDKROOT", "/nonexistent-sdk")
  monkeypatch.setattr(reflect.Path, "is_dir", lambda self: True)
  assert reflect.sdk_path() == "/nonexistent-sdk"


def test_sdkroot_wins_over_xcrun(monkeypatch):
  """SDKROOT is the variable the whole Apple toolchain honours -- a conan
  profile with tools.apple:sdk_path exports it, and so does anyone
  cross-building by hand. xcrun is the fallback, not the authority."""
  called = []
  monkeypatch.setattr(reflect.platform, "system", lambda: "Darwin")
  monkeypatch.setenv("SDKROOT", "/from/env")
  monkeypatch.setattr(reflect.Path, "is_dir", lambda self: True)
  monkeypatch.setattr(reflect, "_run", lambda cmd: called.append(cmd) or "/from/xcrun")
  assert reflect.sdk_path() == "/from/env"
  assert not called, "xcrun was consulted even though SDKROOT was set"


def test_xcrun_supplies_the_sdk_when_the_environment_does_not(monkeypatch):
  monkeypatch.setattr(reflect.platform, "system", lambda: "Darwin")
  monkeypatch.delenv("SDKROOT", raising=False)
  monkeypatch.setattr(reflect.shutil, "which",
                      lambda name: "/usr/bin/xcrun" if name == "xcrun" else None)
  monkeypatch.setattr(reflect, "_run", lambda cmd: "/the/sdk")
  monkeypatch.setattr(reflect.Path, "is_dir", lambda self: True)
  assert reflect.sdk_path() == "/the/sdk"


def test_there_is_no_sysroot_anywhere_but_apple(monkeypatch):
  """clang's builtin search finds /usr/include/c++/NN by itself on Linux, so
  an -isysroot there would only narrow what it can see."""
  for system in ("Linux", "Windows"):
    monkeypatch.setattr(reflect.platform, "system", lambda s=system: s)
    monkeypatch.setenv("SDKROOT", "/should/be/ignored")
    assert reflect.sdk_path() is None, system


def test_parse_args_carries_the_sysroot_only_when_there_is_one():
  with_sdk = reflect.parse_args("c++20", ["/inc"], None, "/the/sdk")
  assert "-isysroot" in with_sdk
  assert with_sdk[with_sdk.index("-isysroot") + 1] == "/the/sdk"
  # and it precedes the project include dirs, as any sysroot must
  assert with_sdk.index("-isysroot") < with_sdk.index("-I/inc")

  without = reflect.parse_args("c++20", ["/inc"], None, None)
  assert "-isysroot" not in without
  # the default keeps every existing caller honest
  assert reflect.parse_args("c++20", ["/inc"], None) == without


@pytest.mark.parametrize("spelling", [
  '_Help(tip)', '_Help("tip")', '[[buildutil::help("tip")]]'])
def test_help_is_read_in_both_spellings(spelling):
  assert _items(spelling) == [(reflect.Annotation.HELP, ("tip",))]


@pytest.mark.parametrize("spelling", [
  '_Help(one) _Help(two)',
  '_Help(one) [[buildutil::help("two")]]',
  '[[buildutil::help("one"), buildutil::help("two")]]',
  '_Help() _Help()'])
def test_duplicate_help_is_an_annotation_error(spelling):
  with pytest.raises(reflect.AnnotationError, match="duplicate buildutil::help"):
    reflect._fold(_items(spelling))


def test_empty_help_is_distinct_from_absent_help():
  assert reflect._fold(_items('_Help("")')).help == ""
  assert reflect._fold([]).help is None


@pytest.mark.parametrize("lane", ["wine", "vcvars", "linux"])
def test_msvc_parse_arguments(monkeypatch, tmp_path, lane):
  monkeypatch.setattr(reflect.platform, "system",
                      lambda: "Windows" if lane == "vcvars" else "Linux")
  monkeypatch.delenv("EMSDK", raising=False)
  monkeypatch.setenv("INCLUDE", r"C:\Program Files\MSVC\include;C:\SDK\ucrt;;")
  wrapper = tmp_path / "msvc/bin/x64/cl"
  wrapper.parent.mkdir(parents=True)
  wrapper.touch()
  (wrapper.parent / "msvcenv.sh").write_text(
    'MSVCVER=14.51.36231\nSDKVER=10.0.26100.0\n')
  monkeypatch.setattr(reflect.shutil, "which",
                      lambda name: str(wrapper) if lane == "wine" and name == "cl"
                      else None)
  base = ["-xc++", "-std=c++23", "-fparse-all-comments",
          "-Wno-unknown-attributes", "-resource-dir", "/resource"]
  target = ["--target=x86_64-pc-windows-msvc",
            "-fms-compatibility", "-fms-extensions"]
  if lane == "wine":
    root = tmp_path / "msvc"
    paths = [root / "vc/tools/msvc/14.51.36231/include"]
    paths += [root / "kits/10/include/10.0.26100.0" / part
              for part in ("ucrt", "shared", "um")]
  elif lane == "vcvars":
    paths = [r"C:\Program Files\MSVC\include", r"C:\SDK\ucrt"]
  else:
    paths = []
    target = []
  for path in paths:
    target += ["-isystem", str(path)]
  assert reflect.parse_args("c++23", ["/project"], "/resource",
                            msvc=reflect.msvc_args()) == base + target + ["-I/project"]


def _fake_bindings(monkeypatch):
  package = types.ModuleType("clang")
  cindex = types.ModuleType("clang.cindex")
  cindex.Config = type("Config", (), {"set_library_file": staticmethod(print)})
  package.cindex = cindex
  monkeypatch.setitem(sys.modules, "clang", package)
  monkeypatch.setitem(sys.modules, "clang.cindex", cindex)


def test_a_machine_without_clang_is_told_so_before_the_parse(monkeypatch):
  # The pip libclang wheel carries no builtin headers, so a box with the
  # bindings and no clang parses every header without stddef.h and reports
  # the wreckage as a missing project include -- a fresh-machine trap.
  _fake_bindings(monkeypatch)
  monkeypatch.setattr(reflect, "_usable_system_libclang", lambda _: None)
  monkeypatch.setattr(reflect, "resource_dir", lambda: None)
  with pytest.raises(reflect.ClangUnavailable, match="clang on PATH"):
    reflect.load_clang()


def test_the_resource_directory_can_be_told_and_is_verified(monkeypatch, tmp_path):
  monkeypatch.setenv("BUILDUTIL_RESOURCE_DIR", str(tmp_path))
  assert reflect.resource_dir() == str(tmp_path)
  monkeypatch.setenv("BUILDUTIL_RESOURCE_DIR", str(tmp_path / "nowhere"))
  assert reflect.resource_dir() is None


def test_generate_reports_a_missing_clang_in_one_line(monkeypatch, tmp_path, capsys):
  def absent():
    raise reflect.ClangUnavailable(reflect.NO_RESOURCE_DIR)
  monkeypatch.setattr(reflect, "load_clang", absent)
  opts = SimpleNamespace(header=str(tmp_path / "x.hpp"),
                         output=str(tmp_path / "x.reflect.hpp"), include_dir=[])
  assert reflect._cmd_generate(opts) == 1
  assert "apt install clang" in capsys.readouterr().err
  assert not (tmp_path / "x.reflect.hpp").exists()
