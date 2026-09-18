"""End-to-end for the reflect extension: real cmake, ninja and compiler.

The fixture deliberately mirrors the shapes that broke during development --
a trailing `/* */` comment, a multi-line `//` run whose continuations are
column-aligned, a private member, a class template, an untagged neighbour,
and per-parameter comments inside operator()'s argument list.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from buildutil import deposit, reflect

def _bindings() -> bool:
  # the generator runs under THIS interpreter (the test hands cmake
  # sys.executable), so this is the interpreter that must have them
  try:
    import clang.cindex  # noqa: F401
    return True
  except ImportError:
    return False


pytestmark = pytest.mark.skipif(
  shutil.which("cmake") is None or shutil.which("ninja") is None or
  not (shutil.which("c++") or shutil.which("g++") or shutil.which("clang++"))
  or not _bindings(),
  reason="needs cmake, ninja, a C++ compiler and the clang python bindings")

PYSUPPORT = Path(deposit.__file__).resolve().parent / "pysupport"
PYPATH = Path(deposit.__file__).resolve().parent.parent

CFG = {"cmake_option_prefix": "ACME", "module_define_prefix": "ACM"}

ROOT_CMAKE = """\
cmake_minimum_required(VERSION 3.25)
project(reflectdemo CXX)
set(CMAKE_CXX_STANDARD 23)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
add_subdirectory(sources)
"""

HEADER = """\
#pragma once
#include <string>

#include <_buildutil/reflect.hpp>   // for _Label

namespace demo {

  enum class Colour : int { RED = 1,          /* the warm one */
                            _Label(azure) BLUE };  // labelled, and on a SHARED line
  constexpr auto reflect_scheme(Colour*);   // an enum opts in from BESIDE itself

  struct Options
  {
    friend constexpr auto reflect_scheme(Options*);

    bool  verbose { false };   /* say more about what is happening */
    int   retries { 3 };       // how many times to try again
                               // before giving up entirely, which
                               // is a multi-line trailing run
    _Label(display-name) std::string name { "x" };
    Colour tint { Colour::RED };

  private:
    int attempts { 0 };        /* not a cli option, but still reflected */

  public:
    auto operator() (int  first,    /* the first one */
                     bool second    /* the second one */
                    ) const -> int;
  };

  struct Untagged { int nothing { 0 }; };

  // A trailing comment written as a run of adjacent blocks: one comment to
  // the reader, several to the lexer, and the interior `*/ /*` used to ride
  // straight into the help text.
  struct Multi
  {
    friend constexpr auto reflect_scheme(Multi*);

    int url { 0 };             /* the base url, such as https://x.example/ */
                               /* (also settable from the environment) */
    int three { 0 };           /* one */
                               /* two */
                               /* three */
    int single { 0 };          /* just the one */

    auto method() const -> int { return 0; }  /* the method, first block */
                                              /* the method, second block */

    auto operator() (int  first,   /* the first one */
                                   /* first, continued */
                     bool second   /* the second one */
                    ) const -> int;
  };

  template <typename T>
  struct Boxed
  {
    template <typename U> friend constexpr auto reflect_scheme(Boxed<U>*);
    T value { };
  };

  // Bases. Tag is the shape a cli `Command` marker has -- untagged, and
  // listed all the same; Secret and Shared are the two kinds that are not
  // captured; deep::Far is a base that has to survive being named from
  // another namespace's reflect header.
  struct Tag    { };
  struct Secret { int s { 0 }; };
  struct Shared { int v { 0 }; };

  namespace deep {
    struct Far
    {
      friend constexpr auto reflect_scheme(Far*);
      int f { 0 };               /* a base one namespace over */
    };
  }

  struct Widget : Tag, deep::Far, private Secret, virtual Shared
  {
    friend constexpr auto reflect_scheme(Widget*);
    int own { 1 };               /* the only member Widget itself has */
  };

  struct Crate : Boxed<int>      // a template-id base
  {
    friend constexpr auto reflect_scheme(Crate*);
    int count { 0 };
  };

  template <typename T>
  struct Crated : Boxed<T>       // a DEPENDENT base: no canonical spelling
  {
    template <typename U> friend constexpr auto reflect_scheme(Crated<U>*);
    int count { 0 };
  };
}
"""

USER_CPP = """\
#include <demo/thing/thing.hpp>
#include <cstddef>
#include <type_traits>
#include <utility>

constexpr auto S = reflect::scheme_of<demo::Options>();
static_assert(reflect::scheme_size(S) == 5);

static_assert(decltype(reflect::scheme_item<0>(S))::NAME_STRING == "verbose");
static_assert(decltype(reflect::scheme_item<0>(S))::COMMENT ==
              "say more about what is happening");
static_assert(!decltype(reflect::scheme_item<0>(S))::ENCAPSULATED);

// the multi-line trailing run keeps its lines and loses its markers
static_assert(decltype(reflect::scheme_item<1>(S))::COMMENT ==
              "how many times to try again\\n"
              "before giving up entirely, which\\n"
              "is a multi-line trailing run");

// a private member is reachable: the tag befriended the entry point
static_assert(decltype(reflect::scheme_item<4>(S))::NAME_STRING == "attempts");
static_assert(decltype(reflect::scheme_item<4>(S))::ENCAPSULATED);
static_assert(std::is_same_v<
  decltype(decltype(reflect::scheme_item<4>(S))::REFERENCE),
  int demo::Options::* const>);

// an enum reflects its enumerator names, values and comments
constexpr auto E = reflect::scheme_of<demo::Colour>();
static_assert(reflect::scheme_size(E) == 2);
static_assert(decltype(reflect::scheme_item<0>(E))::NAME_STRING == "RED");
static_assert(decltype(reflect::scheme_item<0>(E))::COMMENT == "the warm one");
static_assert(decltype(reflect::scheme_item<0>(E))::VALUE == demo::Colour::RED);

// --- _Label: an external name the identifier could not carry ---------------
// The marker expands to NOTHING, so the compiler never saw it; the generator
// read it from the raw token stream and put it here.
static_assert(decltype(reflect::scheme_item<2>(S))::NAME_STRING == "name");
static_assert(decltype(reflect::scheme_item<2>(S))::LABEL == "display-name");
// a label the LEXER would split (three tokens) survives, because it is
// sliced from the source rather than joined from tokens
static_assert(decltype(reflect::scheme_item<2>(S))::LABEL.find('-') == 7);
// no _Label written means EMPTY -- not the member's own name, which is what
// leaves a consumer free to derive an external name by its own convention
static_assert(decltype(reflect::scheme_item<0>(S))::LABEL == "");
// enumerators too, and the association is by OFFSET: RED and BLUE share a
// line, so only BLUE may come back labelled
static_assert(decltype(reflect::scheme_item<0>(E))::LABEL == "");
static_assert(decltype(reflect::scheme_item<1>(E))::LABEL == "azure");
static_assert(static_cast<int>(decltype(reflect::scheme_item<1>(E))::VALUE) == 2);

// operator() parameter names and comments, which raw_comment cannot supply
constexpr auto C = reflect::call_scheme_of<demo::Options>();
static_assert(reflect::scheme_size(C) == 2);
static_assert(decltype(reflect::scheme_item<1>(C))::NAME_STRING == "second");
static_assert(decltype(reflect::scheme_item<1>(C))::COMMENT == "the second one");
// names come from the generator, arity from the language: they must agree
static_assert(reflect::scheme_size(C) ==
              reflect::call_traits_of<demo::Options>::ARITY);

// class templates reflect too
constexpr auto B = reflect::scheme_of<demo::Boxed<double>>();
static_assert(reflect::scheme_size(B) == 1);
static_assert(decltype(reflect::scheme_item<0>(B))::NAME_STRING == "value");

// detection is honest about a type that was never tagged
static_assert(!reflect::reflected<demo::Untagged>);

// --- a comment written as a run of adjacent blocks -------------------------

// One comment to the reader, several to the lexer. Each block's own
// delimiters go; the interior `*/` and `/*` used to reach the help text.
constexpr auto M = reflect::scheme_of<demo::Multi>();
static_assert(decltype(reflect::scheme_item<0>(M))::COMMENT ==
              "the base url, such as https://x.example/\\n"
              "(also settable from the environment)");
static_assert(decltype(reflect::scheme_item<1>(M))::COMMENT ==
              "one\\ntwo\\nthree");
// a lone block is what it always was
static_assert(decltype(reflect::scheme_item<2>(M))::COMMENT == "just the one");

// the method path is a token walk rather than raw_comment, and merges the
// same way
constexpr auto MI = reflect::interface_scheme_of<demo::Multi>();
static_assert(decltype(reflect::scheme_item<0>(MI))::COMMENT ==
              "the method, first block\\nthe method, second block");

// and so does a parameter's, which is a third walk again
constexpr auto MC = reflect::call_scheme_of<demo::Multi>();
static_assert(decltype(reflect::scheme_item<0>(MC))::COMMENT ==
              "the first one\\nfirst, continued");
static_assert(decltype(reflect::scheme_item<1>(MC))::COMMENT ==
              "the second one");

// --- bases -----------------------------------------------------------------

// counting and indexing see the MEMBERS of a derived type and nothing else,
// which is what lets everything written against class_scheme keep working
constexpr auto W = reflect::scheme_of<demo::Widget>();
static_assert(reflect::scheme_size(W) == 1);
static_assert(decltype(reflect::scheme_item<0>(W))::NAME_STRING == "own");
static_assert(decltype(reflect::scheme_item<0>(W))::COMMENT ==
              "the only member Widget itself has");

// direct, public, non-virtual, in declaration order: Secret is private and
// Shared is virtual, so neither is here
constexpr auto WB = reflect::bases_of<demo::Widget>();
static_assert(reflect::bases_size(WB) == 2);
static_assert(std::is_same_v<reflect::base_at<0, decltype(WB)>, demo::Tag>);
static_assert(std::is_same_v<reflect::base_at<1, decltype(WB)>,
                             demo::deep::Far>);
static_assert(std::is_same_v<decltype(reflect::base_item<1>(WB)),
                             std::type_identity<demo::deep::Far>>);
static_assert(reflect::has_reflected_bases<demo::Widget>);

// an untagged base is listed anyway -- whether it is worth descending into
// is reflected<BASE>, which is the consumer's question and not the
// generator's
static_assert(!reflect::reflected<demo::Tag>);
static_assert(reflect::reflected<demo::deep::Far>);

// "no bases" is an answer, not a failure to compile
static_assert(!reflect::has_reflected_bases<demo::Options>);
static_assert(reflect::bases_size(reflect::bases_of<demo::Options>()) == 0);

// a template-id base, and a dependent one that has no canonical spelling
constexpr auto CB = reflect::bases_of<demo::Crate>();
static_assert(std::is_same_v<reflect::base_at<0, decltype(CB)>,
                             demo::Boxed<int>>);
constexpr auto TB = reflect::bases_of<demo::Crated<char>>();
static_assert(std::is_same_v<reflect::base_at<0, decltype(TB)>,
                             demo::Boxed<char>>);

// the fold a consumer actually writes: visit each base that reflects
template <typename T>
consteval auto reflected_bases_of() -> std::size_t
{
  constexpr auto bases = reflect::bases_of<T>();
  return [&]<std::size_t ... I>(std::index_sequence<I...>) {
    return (std::size_t{ 0 } + ...
            + (reflect::reflected<reflect::base_at<I, decltype(bases)>>
                 ? 1u : 0u));
  }(std::make_index_sequence<reflect::bases_size(bases)>{ });
}
static_assert(reflected_bases_of<demo::Widget>() == 1);   // Far yes, Tag no

int demo::Options::operator()(int first, bool second) const
{ return second ? first : 0; }
"""

BYSTANDER_CPP = """\
// includes no reflected header: must not be forced to parse one
int bystander() { return 7; }
"""


def _tree(root: Path, *, extension: bool = True, reflect_cfg: dict = None):
  cfg = dict(CFG)
  cfg.update(reflect_cfg or {})
  deposit.ensure(root, cfg, ["reflect"] if extension else [])
  (root / "CMakeLists.txt").write_text(ROOT_CMAKE)
  src = root / "sources"
  src.mkdir(exist_ok=True)
  (src / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  module = src / "demo" / "thing"
  module.mkdir(parents=True)
  (module / "CMakeLists.txt").write_text("Init_submodule()\n")
  (module / "thing.hpp").write_text(HEADER)
  (module / "thing.cpp").write_text(USER_CPP)
  (module / "bystander.cpp").write_text(BYSTANDER_CPP)
  return root


def _configure(root: Path) -> subprocess.CompletedProcess:
  import sys
  return subprocess.run(
    ["cmake", "-S", str(root), "-B", str(root / "b"), "-G", "Ninja",
     "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
     f"-DPython3_EXECUTABLE={sys.executable}",
     f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}",
     f"-DBUILDUTIL_PYPATH={PYPATH}"],
    capture_output=True, text=True)


def _build(root: Path) -> subprocess.CompletedProcess:
  done = _configure(root)
  assert done.returncode == 0, done.stdout + done.stderr
  return subprocess.run(["cmake", "--build", str(root / "b")],
                        capture_output=True, text=True)


def _compile_commands(root: Path) -> list[dict]:
  done = _configure(root)
  assert done.returncode == 0, done.stdout + done.stderr
  db = root / "b" / "compile_commands.json"
  assert db.is_file(), "cmake exported no compile database"
  return json.loads(db.read_text())


def _command_for(entries: list[dict], stem: str) -> str:
  for entry in entries:
    if stem in entry["file"]:
      return entry.get("command") or " ".join(entry["arguments"])
  raise AssertionError(f"no compile entry for {stem}")


# --- the whole point: a tagged type becomes a usable scheme -----------------

def test_a_tagged_header_produces_a_reflect_header(tmp_path):
  root = _tree(tmp_path)
  done = _build(root)
  assert done.returncode == 0, done.stdout + done.stderr
  assert (root / "b" / "generated" / "demo" / "thing"
          / "thing.reflect.hpp").is_file()
  assert (root / "b" / "generated" / "_buildutil" / "reflect.hpp").is_file()


def test_names_comments_privates_and_parameters_reach_the_compiler(tmp_path):
  # every assertion lives in thing.cpp as a static_assert, so a green build
  # IS the assertion -- there is no way for this to pass vacuously
  root = _tree(tmp_path)
  done = _build(root)
  assert done.returncode == 0, done.stdout + done.stderr


def test_bases_reach_the_compiler_as_types(tmp_path):
  # Same trick, for the base list: thing.cpp asserts the captured subset,
  # the declaration order, that counting still sees members only, and that
  # a base comes back as a TYPE -- through a real parse, a real generator
  # run and a real compiler. What the unit tests fake, this one does not.
  root = _tree(tmp_path)
  done = _build(root)
  assert done.returncode == 0, done.stdout + done.stderr

  produced = (root / "b" / "generated" / "demo" / "thing"
              / "thing.reflect.hpp").read_text()
  # the emitted names are fully qualified from the root: the reflect header
  # names them from a scope the class never saw
  assert "::reflect::base_list<::demo::Tag, ::demo::deep::Far>" in produced
  assert "::demo::Secret" not in produced and "::demo::Shared" not in produced
  # a dependent base has no canonical spelling, so it is re-applied as the
  # source wrote it -- inside the same template head, at the same scope
  assert "::reflect::base_list<Boxed<T>>" in produced
  # and a type with no bases is untouched by any of this
  assert "constexpr auto reflect_scheme(Options*)\n{\n" \
         "  return ::reflect::class_scheme<" in produced


# --- delivery ---------------------------------------------------------------

def test_the_forced_include_lands_only_on_the_translation_units_that_ask(
    tmp_path):
  # the DEPRECATED path, pinned by config: what it does is not in dispute,
  # only whether it is enough (it is not -- see the transitive test below)
  root = _tree(tmp_path, reflect_cfg={"reflect_include": "source"})
  entries = _compile_commands(root)
  assert "thing.reflect.hpp" in _command_for(entries, "thing.cpp")
  assert "reflect.hpp" not in _command_for(entries, "bystander.cpp")


def test_module_mode_forces_every_translation_unit(tmp_path):
  root = _tree(tmp_path, reflect_cfg={"reflect_include": "module"})
  entries = _compile_commands(root)
  # module mode needs no mapping, so the bystander gets it too
  assert "thing.reflect.hpp" in _command_for(entries, "bystander.cpp")


# --- the failure mode this design exists to avoid ---------------------------

def test_an_untagged_type_is_an_error_not_an_empty_scheme(tmp_path):
  root = _tree(tmp_path)
  module = root / "sources" / "demo" / "thing"
  (module / "thing.cpp").write_text(
    "#include <demo/thing/thing.hpp>\n"
    "constexpr auto S = reflect::scheme_of<demo::Untagged>();\n"
    "int demo::Options::operator()(int a, bool b) const { return b ? a : 0; }\n")
  done = _build(root)
  assert done.returncode != 0
  assert "not reflected" in done.stdout + done.stderr


# --- invalidation -----------------------------------------------------------

def test_editing_an_included_header_regenerates(tmp_path):
  # the parse resolves THROUGH the include closure, so a reflect file is
  # stale when anything in that closure changes -- not just its own header.
  # That edge exists only because the generator writes a depfile.
  #
  # Asserted with real builds, not `ninja -n`: under a dry run the glob
  # re-check never executes, so ninja cannot know the set is unchanged and
  # conservatively schedules a CMake re-run into the output.
  root = _tree(tmp_path)
  module = root / "sources" / "demo" / "thing"
  (module / "shared.hpp").write_text("#pragma once\n")
  (module / "thing.hpp").write_text(
    HEADER.replace('#include <string>',
                   '#include <string>\n#include "shared.hpp"'))
  assert _build(root).returncode == 0

  produced = (root / "b" / "generated" / "demo" / "thing"
              / "thing.reflect.hpp")
  depfile = Path(str(produced) + ".d")
  assert depfile.is_file(), "generator wrote no depfile"
  assert "shared.hpp" in depfile.read_text(), \
    "the include closure is not a dependency"

  def rebuild() -> str:
    done = subprocess.run(["ninja", "-C", str(root / "b")],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout

  assert "reflect demo" not in rebuild(), "rebuilt with nothing changed"
  (module / "shared.hpp").write_text("#pragma once\n// touched\n")
  assert "reflect demo" in rebuild(), \
    "editing a header in the closure did not invalidate the reflect file"


# --- opting out costs nothing ----------------------------------------------

def test_a_renamed_header_leaves_nothing_behind(tmp_path):
  # The generated root sits AHEAD of sources/, so a detour left behind by a
  # header that moved keeps shadowing the spelling it was made for, and the
  # schemes beside it are frozen at whatever the last parse said. The build
  # stays green and stops telling the truth -- which is what "editing the
  # source of a reflection does not regenerate it" turns out to be.
  root = _tree(tmp_path)
  assert _build(root).returncode == 0
  generated = root / "b" / "generated" / "demo" / "thing"
  assert (generated / "thing.reflect.hpp").is_file()

  module = root / "sources" / "demo" / "thing"
  (module / "other.hpp").write_text(HEADER)
  (module / "thing.hpp").unlink()
  for name in ("thing.cpp", "bystander.cpp"):
    (module / name).write_text(
      (module / name).read_text().replace("thing/thing.hpp",
                                          "thing/other.hpp"))
  assert _build(root).returncode == 0

  assert (generated / "other.reflect.hpp").is_file()
  for name in ("thing.reflect.hpp", "thing.reflect.hpp.d", "thing.hpp"):
    assert not (generated / name).exists(), \
      "{} outlived the header it was generated from".format(name)


def test_an_untagged_header_stops_being_reflected(tmp_path):
  # the silent half of the same defect: the header is still there and still
  # edited, it has simply left the scan -- and the frozen schemes beside it
  # went on answering for it
  root = _tree(tmp_path)
  module = root / "sources" / "demo" / "thing"
  tagged = ("#pragma once\nnamespace demo {\n  struct Extra\n  {\n"
            "    friend constexpr auto reflect_scheme(Extra*);\n"
            "    int e { 0 };   /* an extra */\n  };\n}\n")
  (module / "extra.hpp").write_text(tagged)
  assert _build(root).returncode == 0

  generated = root / "b" / "generated" / "demo" / "thing"
  assert (generated / "extra.reflect.hpp").is_file()

  (module / "extra.hpp").write_text(
    tagged.replace("    friend constexpr auto reflect_scheme(Extra*);\n", ""))
  assert _build(root).returncode == 0

  assert not (generated / "extra.reflect.hpp").exists()
  assert not (generated / "extra.hpp").exists(), \
    "the detour went on shadowing the header it no longer describes"


def test_a_project_without_the_extension_is_untouched(tmp_path):
  root = _tree(tmp_path, extension=False)
  module = root / "sources" / "demo" / "thing"
  (module / "thing.cpp").write_text("int thing() { return 1; }\n")
  done = _build(root)
  assert done.returncode == 0, done.stdout + done.stderr
  assert not (root / "b" / "generated" / "demo").exists()
  entries = _compile_commands(root)
  assert "reflect" not in _command_for(entries, "thing.cpp")


# --- the consuming module's include path reaches the generator -------------
# The parse runs with KeepGoing, so an include the generator cannot resolve
# used to cost only the types that needed it -- quietly, exit 0, and the miss
# surfaced as `static_assert: type is not reflected` modules away. Two halves
# are pinned here: the include path now travels, and a shortfall is loud.

VENDOR_HPP = """\
#pragma once
namespace vendor { struct Handle { int slot { 0 }; }; }
"""

BASE_HPP = """\
#pragma once
#include <vendor/handle.hpp>
namespace demo::base { using vendor::Handle; }
"""

THING_HPP = """\
#pragma once
#include <demo/base/base.hpp>

namespace demo {

  struct Held
  {
    friend constexpr auto reflect_scheme(Held*);
    vendor::Handle handle { };   /* the member that needs the dependency */
    int            spare  { 1 }; /* and one that never would */
  };

  struct Plain
  {
    friend constexpr auto reflect_scheme(Plain*);
    int only { 2 };              /* nothing exotic: this one always survives */
  };
}
"""

THING_CPP = """\
#include <demo/thing/thing.hpp>

constexpr auto H = reflect::scheme_of<demo::Held>();
static_assert(reflect::scheme_size(H) == 2);
constexpr auto P = reflect::scheme_of<demo::Plain>();
static_assert(reflect::scheme_size(P) == 1);
int held() { return 0; }
"""

# The same shortfall without cmake in the way: one header, one include
# directory, and the flag is the only thing that differs between the runs.
NEAR_HPP = """\
#pragma once
#include <vendor/handle.hpp>

namespace near {
  struct Held
  {
    friend constexpr auto reflect_scheme(Held*);
    vendor::Handle handle { };
    int            spare  { 1 };
  };
  struct Plain
  {
    friend constexpr auto reflect_scheme(Plain*);
    int only { 2 };
  };
}
"""


def _vendor(root: Path) -> Path:
  """An include root that is nobody's sources/ -- a stand-in for the conan
  package whose headers a tagged type cannot be parsed without."""
  vendor = root / "vendor"
  (vendor / "vendor").mkdir(parents=True)
  (vendor / "vendor" / "handle.hpp").write_text(VENDOR_HPP)
  return vendor


def _two_modules(root: Path, *, linked: bool = True) -> Path:
  """Module `base` publishes the vendor include root; module `thing` has a
  tagged type that cannot be parsed without it, and reaches it ONLY by
  linking base. That edge is stated by Link_dependencies(), which runs after
  Init_submodule() -- and therefore after the extension's per-module hook --
  so this is exactly the shape that proves the include path is read late
  enough to see it."""
  deposit.ensure(root, dict(CFG), ["reflect"])
  (root / "CMakeLists.txt").write_text(ROOT_CMAKE)
  src = root / "sources"
  src.mkdir(exist_ok=True)
  (src / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  vendor = _vendor(root)

  base = src / "demo" / "base"
  base.mkdir(parents=True)
  (base / "CMakeLists.txt").write_text(
    "Init_submodule()\n"
    'target_include_directories(demo-base PUBLIC "{}")\n'.format(
      vendor.as_posix()))
  (base / "base.hpp").write_text(BASE_HPP)
  (base / "base.cpp").write_text(
    '#include "base.hpp"\nint base_unit() { return 0; }\n')

  thing = src / "demo" / "thing"
  thing.mkdir(parents=True)
  (thing / "CMakeLists.txt").write_text(
    "Init_submodule()\n" + ("Link_dependencies(base)\n" if linked else ""))
  (thing / "thing.hpp").write_text(THING_HPP)
  (thing / "thing.cpp").write_text(THING_CPP)
  return root


def _reflect_rule(root: Path, header_name: str) -> str:
  for line in (root / "b" / "build.ninja").read_text().splitlines():
    if "buildutil.reflect generate" in line and header_name in line:
      return line
  raise AssertionError("no reflect rule mentioning " + header_name)


def test_a_linked_modules_include_path_reaches_the_generate_step(tmp_path):
  # thing.cpp static_asserts both schemes, so a green build IS the assertion
  root = _two_modules(tmp_path, linked=True)
  done = _build(root)
  assert done.returncode == 0, done.stdout + done.stderr
  produced = (root / "b" / "generated" / "demo" / "thing"
              / "thing.reflect.hpp").read_text()
  assert "reflect_scheme(Held*)" in produced
  assert "reflect_scheme(Plain*)" in produced


def test_the_generate_rule_carries_the_dependencys_include_directory(tmp_path):
  # against the RENDERED build, because the flag has to survive $<JOIN>,
  # COMMAND_EXPAND_LISTS and ninja's quoting to be worth anything at all
  root = _two_modules(tmp_path, linked=True)
  done = _configure(root)
  assert done.returncode == 0, done.stdout + done.stderr
  rule = _reflect_rule(root, "thing.hpp")
  assert "--include-dir" in rule
  assert str(root / "sources") in rule
  assert str(root / "vendor") in rule, \
    "the dependency's include directory never reached the generate step"
  # the module's own private root arrives the same way, from the same property
  assert str(root / "sources" / "demo" / "thing") in rule


def test_without_that_edge_the_generator_fails_loudly(tmp_path):
  # the bug, exactly: two types tagged, one scheme emitted. It used to exit 0
  root = _two_modules(tmp_path, linked=False)
  done = _build(root)
  assert done.returncode != 0, "the silent drop is back"
  said = done.stdout + done.stderr
  assert "2 type(s) tagged, 1 scheme(s) emitted" in said
  assert "no scheme for: Held" in said
  assert "handle.hpp" in said, "clang's own diagnostics were not surfaced"
  assert not (root / "b" / "generated" / "demo" / "thing"
              / "thing.reflect.hpp").exists(), \
    "a half-populated reflect header was left on disk"


def _generate(header: Path, output: Path, *include_dirs: Path) -> int:
  argv = ["generate", "--header", str(header), "--output", str(output)]
  for directory in include_dirs:
    argv += ["--include-dir", str(directory)]
  return reflect.main(argv)


def test_one_include_dir_is_the_whole_difference(tmp_path, capsys):
  # no cmake, no compiler: the same generate invocation twice, differing by
  # one flag. This is the reduction the field report arrived with.
  sources = tmp_path / "sources" / "near"
  sources.mkdir(parents=True)
  vendor = _vendor(tmp_path)
  header = sources / "thing.hpp"
  header.write_text(NEAR_HPP)
  output = tmp_path / "thing.reflect.hpp"

  assert _generate(header, output, sources.parent) != 0
  said = capsys.readouterr().err
  assert "no scheme for: Held" in said
  assert not output.exists(), "a partial scheme was written anyway"

  assert _generate(header, output, sources.parent, vendor) == 0
  text = output.read_text()
  assert "reflect_scheme(Held*)" in text
  assert "reflect_scheme(Plain*)" in text


# --- a header that writes its own schemes ------------------------------------
# Tagging a type and defining its scheme by hand is how a test proves the
# framework reads the TYPE and not a generated scheme. The generator used to
# count the definition as a second promise, refuse the header for a shortfall
# that was not there, and -- past that gate -- emit a colliding definition.

WRITTEN_HPP = """\
#pragma once
#include <_buildutil/reflect.hpp>

namespace demo {

  struct Own
  {
    friend constexpr auto reflect_scheme(Own*);
    int only { 0 };              /* the one member, named by hand below */
  };

  constexpr auto reflect_scheme(Own*)
  {
    return ::reflect::class_scheme<
      ::reflect::member_scheme<"only", &Own::only>
    >{ };
  }

  struct Gen
  {
    friend constexpr auto reflect_scheme(Gen*);
    int first  { 1 };
    int second { 2 };
  };
}
"""

WRITTEN_CPP = """\
#include <demo/thing/own.hpp>

// the hand-written scheme is the one that answers, and it names one member
constexpr auto O = reflect::scheme_of<demo::Own>();
static_assert(reflect::scheme_size(O) == 1);
constexpr auto G = reflect::scheme_of<demo::Gen>();
static_assert(reflect::scheme_size(G) == 2);
int own_unit() { return 0; }
"""


def test_a_hand_written_scheme_is_left_alone_and_its_neighbour_is_not(tmp_path):
  root = _tree(tmp_path)
  module = root / "sources" / "demo" / "thing"
  (module / "own.hpp").write_text(WRITTEN_HPP)
  (module / "own.cpp").write_text(WRITTEN_CPP)
  done = _build(root)
  assert done.returncode == 0, done.stdout + done.stderr
  produced = (root / "b" / "generated" / "demo" / "thing"
              / "own.reflect.hpp").read_text()
  assert "reflect_scheme(Gen*)" in produced
  assert "reflect_scheme(Own*)" not in produced, \
    "the generator wrote a second definition of a hand-written scheme"


def test_a_header_of_only_hand_written_schemes_is_not_claimed(tmp_path):
  root = _tree(tmp_path)
  module = root / "sources" / "demo" / "thing"
  (module / "own.hpp").write_text(WRITTEN_HPP.replace(
    "  struct Gen\n"
    "  {\n"
    "    friend constexpr auto reflect_scheme(Gen*);\n"
    "    int first  { 1 };\n"
    "    int second { 2 };\n"
    "  };\n", ""))
  (module / "own.cpp").write_text(
    WRITTEN_CPP.replace("constexpr auto G = reflect::scheme_of<demo::Gen>();\n"
                    "static_assert(reflect::scheme_size(G) == 2);\n", ""))
  done = _build(root)
  assert done.returncode == 0, done.stdout + done.stderr
  generated = root / "b" / "generated" / "demo" / "thing"
  assert not (generated / "own.reflect.hpp").exists()
  assert not (generated / "own.hpp").exists(), \
    "a detour was laid over a header the generator has nothing to say about"


def test_the_generator_reports_no_shortfall_for_a_written_scheme(tmp_path,
                                                                 capsys):
  # the reduction from the field report: seven types, each tagged and each
  # defined, refused as "7 type(s) tagged, 0 scheme(s) emitted"
  sources = tmp_path / "sources" / "demo" / "thing"
  sources.mkdir(parents=True)
  header = sources / "own.hpp"
  header.write_text(WRITTEN_HPP)
  support = tmp_path / "generated" / "_buildutil"
  support.mkdir(parents=True)
  (support / "reflect.hpp").write_text(reflect.support_header())
  output = tmp_path / "own.reflect.hpp"
  assert _generate(header, output, tmp_path / "sources",
                   tmp_path / "generated") == 0, capsys.readouterr().err
  text = output.read_text()
  assert "reflect_scheme(Gen*)" in text
  assert "reflect_scheme(Own*)" not in text


# --- the scheme a transitive include used to lose --------------------------
# The field report: 165 tests green, and the shipped binary rejecting its own
# inherited options. The base class's scheme was force-included into the test
# TU, which named the base header directly, and into nothing else -- so in
# main.cpp's TU `reflected<Options>` answered NO, the consumer's templates
# instantiated a second way, and the linker kept whichever it met first.
#
# The detour makes the question unaskable: the schemes arrive with the class.

OPTIONS_HPP = """\
#pragma once
namespace demo {
  struct Options
  {
    friend constexpr auto reflect_scheme(Options*);
    bool verbose { false };      /* say more about what is happening */
    int  retries { 3 };          /* how many times to try again */
  };
}
"""

COMMAND_HPP = """\
#pragma once
#include <demo/base/options.hpp>

namespace demo {
  struct Command : Options
  {
    friend constexpr auto reflect_scheme(Command*);
    int count { 0 };             /* the one member Command itself has */
  };
}
"""

# The TU at the heart of the incident: it names the DERIVED header and
# nothing else, and the base's scheme has to be here anyway.
COMMAND_CPP = """\
#include <demo/cmd/command.hpp>
#include <type_traits>

constexpr auto C = reflect::scheme_of<demo::Command>();
static_assert(reflect::scheme_size(C) == 1);

constexpr auto B = reflect::bases_of<demo::Command>();
static_assert(reflect::bases_size(B) == 1);
using BASE = reflect::base_at<0, decltype(B)>;
static_assert(std::is_same_v<BASE, demo::Options>);

// this is the assertion the field report reduces to
static_assert(reflect::reflected<BASE>);
constexpr auto O = reflect::scheme_of<BASE>();
static_assert(reflect::scheme_size(O) == @MEMBERS@);
static_assert(decltype(reflect::scheme_item<0>(O))::NAME_STRING == "verbose");

int command_unit() { return 0; }
"""


def _inherited(root: Path, *, members: int = 2, reflect_cfg: dict = None):
  """Options in one module, Command deriving from it in another, and a .cpp
  that includes ONLY the derived header."""
  cfg = dict(CFG)
  cfg.update(reflect_cfg or {})
  deposit.ensure(root, cfg, ["reflect"])
  (root / "CMakeLists.txt").write_text(ROOT_CMAKE)
  src = root / "sources"
  src.mkdir(exist_ok=True)
  (src / "CMakeLists.txt").write_text("Scan_subdirectories()\n")

  base = src / "demo" / "base"
  base.mkdir(parents=True)
  (base / "CMakeLists.txt").write_text("Init_submodule()\n")
  (base / "options.hpp").write_text(OPTIONS_HPP)
  (base / "options.cpp").write_text(
    "#include <demo/base/options.hpp>\nint options_unit() { return 0; }\n")

  cmd = src / "demo" / "cmd"
  cmd.mkdir(parents=True)
  (cmd / "CMakeLists.txt").write_text("Init_submodule()\n")
  (cmd / "command.hpp").write_text(COMMAND_HPP)
  (cmd / "command.cpp").write_text(
    COMMAND_CPP.replace("@MEMBERS@", str(members)))
  return root


def test_a_transitively_reached_scheme_is_present_in_that_tu(tmp_path):
  # every assertion is a static_assert in command.cpp, so a green build IS
  # the assertion and there is no way for this to pass vacuously
  root = _inherited(tmp_path)
  done = _build(root)
  assert done.returncode == 0, done.stdout + done.stderr


def test_the_force_include_path_still_loses_it(tmp_path):
  # the same tree, one config key different. This is the bug, reproduced --
  # and what makes the test above mean something.
  root = _inherited(tmp_path, reflect_cfg={"reflect_include": "source"})
  done = _build(root)
  assert done.returncode != 0, "the transitive miss did not reproduce"
  assert "not reflected" in done.stdout + done.stderr


def test_the_detour_wraps_the_real_header_and_takes_its_spelling(tmp_path):
  root = _inherited(tmp_path)
  assert _build(root).returncode == 0
  detour = root / "b" / "generated" / "demo" / "base" / "options.hpp"
  assert detour.is_file(), "no detour was written"
  text = detour.read_text()
  # the real header by absolute path, then its schemes -- and no class text
  # of its own, so an error or a goto-definition lands in the real file
  assert '#include "{}"'.format(
    (root / "sources" / "demo" / "base" / "options.hpp").as_posix()) in text
  assert '#include "demo/base/options.reflect.hpp"' in text
  assert "struct Options" not in text
  # and the schemes no longer name the header that included them
  schemes = (root / "b" / "generated" / "demo" / "base"
             / "options.reflect.hpp").read_text()
  assert "#include <demo/base/options.hpp>" not in schemes


def test_the_generated_root_comes_first_and_nothing_is_force_included(
    tmp_path):
  root = _inherited(tmp_path)
  command = _command_for(_compile_commands(root), "command.cpp")
  generated = str(root / "b" / "generated")
  sources = str(root / "sources")
  assert command.index("-I" + generated) < command.index("-I" + sources), \
    "sources/ answers before the generated root, so the detour never runs"
  assert "-include" not in command and "/FI" not in command


def test_a_sibling_relative_include_is_refused_at_configure(tmp_path):
  # the one spelling that goes around a detour: quoted, from next door
  root = _inherited(tmp_path)
  (root / "sources" / "demo" / "base" / "peek.cpp").write_text(
    '#include <string>\n#include "options.hpp"\nint peek() { return 0; }\n')
  done = _configure(root)
  assert done.returncode != 0, "the bypass was allowed"
  said = done.stdout + done.stderr
  assert "sources/demo/base/peek.cpp:2" in said
  assert '#include "demo/base/options.hpp"' in said


def test_editing_a_member_reaches_the_translation_units(tmp_path):
  root = _inherited(tmp_path)
  assert _build(root).returncode == 0

  base = root / "sources" / "demo" / "base"
  (base / "options.hpp").write_text(
    OPTIONS_HPP.replace("  };",
                        "    int  width { 80 };         /* how wide */\n  };"))
  cmd = root / "sources" / "demo" / "cmd"
  (cmd / "command.cpp").write_text(COMMAND_CPP.replace("@MEMBERS@", "3"))
  done = _build(root)
  assert done.returncode == 0, done.stdout + done.stderr
  assert "width" in (root / "b" / "generated" / "demo" / "base"
                     / "options.reflect.hpp").read_text()


def test_the_detour_is_not_restamped_by_a_rebuild(tmp_path):
  # the detour is derived from PATHS, so it must sit still while the schemes
  # beside it churn -- otherwise every consumer of the header rebuilds every
  # time anything in that header's include closure moves
  import os
  root = _inherited(tmp_path)
  assert _build(root).returncode == 0
  detour = root / "b" / "generated" / "demo" / "base" / "options.hpp"
  before = os.stat(detour).st_mtime_ns

  assert _build(root).returncode == 0
  assert os.stat(detour).st_mtime_ns == before, "a no-change build restamped it"

  # and the sharper case: the header really did change, the schemes really
  # are regenerated, and the detour still must not move
  base = root / "sources" / "demo" / "base"
  (base / "options.hpp").write_text(
    OPTIONS_HPP.replace("say more about what is happening", "say more"))
  assert _build(root).returncode == 0
  schemes = (root / "b" / "generated" / "demo" / "base"
             / "options.reflect.hpp").read_text()
  assert '"verbose", &Options::verbose, "say more"' in schemes
  assert os.stat(detour).st_mtime_ns == before, \
    "regenerating the schemes restamped the detour"


# --- which comment a method takes ------------------------------------------
# The comment beside a declaration IS that option's help text, so there is
# nowhere beside a declaration to explain WHY it is written the way it is --
# a design note left there is printed to whoever runs --help. The rule is
# "trailing preferred", and it was only ever half true: a member rides
# clang's raw_comment, which associates a trailing comment with a FIELD_DECL,
# while a method has no such association -- so raw_comment returned the
# PRECEDING block and the trailing one was never looked for.

METHOD_COMMENTS_HPP = """\
#pragma once
namespace demo {
  struct S
  {
    friend constexpr auto reflect_scheme(S*);

    // a design rationale that explains WHY this is written the way it is,
    // and that must never be shown to anyone running --help
    int member { 0 };            /* the member help text */

    // the same shape above a method, which used to win
    // and which spans two lines
    auto both() -> int;          /* the method help text */

    // nothing trailing here, so this block IS the help text
    auto onlypre() -> int;

    auto onlytrail() -> int;     /* only a trailing comment */
  };
}
"""


def _emitted(tmp_path: Path, text: str) -> str:
  header = tmp_path / "m.hpp"
  header.write_text(text)
  output = tmp_path / "m.reflect.hpp"
  assert _generate(header, output, tmp_path) == 0
  return output.read_text()


def test_a_method_takes_its_trailing_comment_exactly_as_a_member_does(tmp_path):
  text = _emitted(tmp_path, METHOD_COMMENTS_HPP)
  # the member has always done this...
  assert '"member", &S::member, "the member help text"' in text
  # ...and now the method does too, rather than shipping the rationale
  assert '"both", &S::both, "the method help text"' in text
  assert "design rationale" not in text
  assert "used to win" not in text


def test_a_preceding_block_still_answers_when_nothing_trails(tmp_path):
  # the fallback, which must not regress: preferring the trailing comment is
  # not the same as refusing every other kind
  text = _emitted(tmp_path, METHOD_COMMENTS_HPP)
  assert ('"onlypre", &S::onlypre, '
          '"nothing trailing here, so this block IS the help text"') in text
  assert '"onlytrail", &S::onlytrail, "only a trailing comment"' in text


# --- a comment INSIDE a declaration is never the declaration's own ----------
# The field report: a subcommand's help screen described its first argument
# instead of the subcommand. A parameter's comment sits inside the parens
# and therefore on the line the declaration ENDS on, and "trailing" was read
# as "shares the end line" -- so the parameter's comment was taken as the
# method's, and trailing-wins then suppressed the leading description that
# was the real answer.

INSIDE_HPP = """\
#pragma once
#include <string>
namespace demo {
  struct Agent
  {
    friend constexpr auto reflect_scheme(Agent*);

    // Send a message to the model and print the response
    auto chat(std::string what /* message to send to the model */) -> int;

    // a leading block that must still LOSE to a trailing one
    auto both(int n /* the n */) -> int;   /* the real method comment */

    // a declaration spanning lines, parameter comment on the LAST one
    auto spread(int a,
                int b /* the b */) -> int;

    int member /* a note among the tokens */ { 0 };  /* the member help */

    auto operator() (int first /* the first */) const -> int;
  };
}
"""


def test_a_parameters_comment_is_not_its_methods(tmp_path):
  text = _emitted(tmp_path, INSIDE_HPP)
  assert ('"chat", &Agent::chat, '
          '"Send a message to the model and print the response"') in text
  # and it is still the parameter's
  assert '::reflect::param_scheme<"what", "message to send to the model">' in text


def test_trailing_still_wins_over_leading(tmp_path):
  text = _emitted(tmp_path, INSIDE_HPP)
  assert '"both", &Agent::both, "the real method comment"' in text
  assert "must still LOSE" not in text


def test_a_parameter_comment_on_the_last_line_of_a_declaration(tmp_path):
  text = _emitted(tmp_path, INSIDE_HPP)
  assert ('"spread", &Agent::spread, '
          '"a declaration spanning lines, parameter comment on the LAST one"') \
    in text
  assert '::reflect::param_scheme<"b", "the b">' in text


def test_a_members_inline_comment_is_not_its_help_text(tmp_path):
  # the same failure one declaration kind over: clang's raw_comment handed
  # back the comment written among the member's own tokens and never looked
  # at the one beside it
  text = _emitted(tmp_path, INSIDE_HPP)
  assert '"member", &Agent::member, "the member help"' in text
  assert "among the tokens" not in text


def test_an_operators_parameter_comment_is_still_the_parameters(tmp_path):
  text = _emitted(tmp_path, INSIDE_HPP)
  assert '::reflect::param_scheme<"first", "the first">' in text


# --- which line continues a comment and which starts the next one ----------

COLUMNS_HPP = """\
#pragma once
namespace demo {
  struct S
  {
    friend constexpr auto reflect_scheme(S*);

    int aligned { 0 };     // the first line
                           // aligned under it, so the same comment
    int flush { 0 };       // this one's own
    // written flush left, so it introduces the NEXT member
    int next { 0 };
  };
}
"""


def test_an_aligned_run_is_one_comment_and_a_flush_line_is_the_next_ones(
    tmp_path):
  text = _emitted(tmp_path, COLUMNS_HPP)
  assert ('"aligned", &S::aligned, '
          '"the first line\\naligned under it, so the same comment"') in text
  assert '"flush", &S::flush, "this one\'s own"' in text
  assert ('"next", &S::next, '
          '"written flush left, so it introduces the NEXT member"') in text


# --- the same header twice is the same file --------------------------------

ENUMS_HPP = """\
#pragma once
namespace demo {
  enum class First  { A };
  constexpr auto reflect_scheme(First*);
  enum class Second { B };
  constexpr auto reflect_scheme(Second*);
  enum class Third  { C };
  constexpr auto reflect_scheme(Third*);
}
"""


def test_enums_are_emitted_in_declaration_order(tmp_path):
  # nothing in the source ordered them: they came out of a set of cursor
  # hashes, so the same header emitted a differently ordered file run to
  # run -- which restamps it and rebuilds every translation unit that
  # includes it, for no change at all
  text = _emitted(tmp_path, ENUMS_HPP)
  assert (text.index("reflect_scheme(First*)")
          < text.index("reflect_scheme(Second*)")
          < text.index("reflect_scheme(Third*)"))


def test_the_same_header_generates_the_same_file(tmp_path):
  # a second parse of the same source must produce the same bytes, or
  # _write's content diff cannot keep an unchanged output from restamping
  assert _emitted(tmp_path, ENUMS_HPP) == _emitted(tmp_path, ENUMS_HPP)


# ===========================================================================
# the attribute tier (and _Label's transition onto it)
# ===========================================================================
#
# Ground truth is a real C++ attribute in buildutil's own namespace, which
# every compiler parses and ignores; `_Label` / `_Meta` are sugar over it.
# The generator reads BOTH out of the raw token stream, which is what makes
# the transition free -- and which is why these are read through a REAL
# parse: libclang DROPS an unknown attribute from the AST entirely, so
# everything below rests on where clang puts each cursor's extent.

def _support(root: Path, mode=reflect.Expansion.MACRO) -> Path:
  """The two shipped headers, where a parse with `-I root` finds them."""
  (root / "_buildutil").mkdir(exist_ok=True)
  (root / "_buildutil" / "reflect.hpp").write_text(reflect.support_header())
  (root / "_buildutil" / "reflect-macros.hpp").write_text(
    reflect.macro_header(mode))
  return root


ANNOTATED_HPP = """\
#pragma once
#include <_buildutil/reflect.hpp>

namespace demo {

  enum class [[buildutil::label("colour")]] Colour : int
  {
    RED [[buildutil::label("red")]],            /* the warm one */
    USER [[buildutil::meta("wire", "hot")]] = 5,
    _Label(azure) BLUE,
    PLAIN
  };
  constexpr auto reflect_scheme(Colour*);

  struct [[buildutil::label("opts"), buildutil::meta("cli")]] Options
  {
    friend constexpr auto reflect_scheme(Options*);

    [[buildutil::label("lead")]] int leading { 0 };
    int trailing [[buildutil::label("trail"), buildutil::meta("a", "b")]] { 1 };
    _Label(display-name) int legacy { 2 };
    int plain { 3 };             /* nothing said about this one */

    [[buildutil::label("m1")]] auto lead_method() const -> int;
    auto trail_method() const [[buildutil::meta("slow")]] -> int;
    auto with_params([[buildutil::label("p1")]] int first,
                     int second [[buildutil::meta("opt")]]) const -> int;
    auto operator() (int only [[buildutil::label("solo")]]) const -> int;
  };
}
"""


def test_an_attribute_is_read_on_every_kind_in_every_legal_position(tmp_path):
  text = _emitted(_support(tmp_path), ANNOTATED_HPP)
  # a class and an enum: after the class-key, the only position there is
  assert '::reflect::type_scheme<"colour">' in text
  assert ('::reflect::type_scheme<"opts", ::reflect::tag_list<"cli">>') in text
  # an enumerator: trailing, the only position there is
  assert '"RED", Colour::RED, "the warm one", "red">' in text
  # a member: leading and trailing both
  assert '"leading", &Options::leading, "", false, "lead">' in text
  assert ('"trailing", &Options::trailing, "", false, "trail", '
          '::reflect::tag_list<"a", "b">>') in text
  # a method: leading and trailing both
  assert '::reflect::call_scheme<>, "m1">' in text
  assert '::reflect::call_scheme<>, "", ::reflect::tag_list<"slow">>' in text
  # a parameter: leading and trailing both, and inside operator() as well
  assert '::reflect::param_scheme<"first", "", "p1">' in text
  assert ('::reflect::param_scheme<"second", "", "", '
          '::reflect::tag_list<"opt">>') in text
  assert '::reflect::param_scheme<"only", "", "solo">' in text


def test_the_legacy_prefix_spelling_still_reads_beside_the_attributes(tmp_path):
  # PHASE ONE's whole point: every site in the fleet writes the prefix form,
  # and one header may now carry both spellings at once
  text = _emitted(_support(tmp_path), ANNOTATED_HPP)
  assert '"BLUE", Colour::BLUE, "", "azure">' in text
  assert '"legacy", &Options::legacy, "", false, "display-name">' in text


def test_an_enumerator_keeps_its_trailing_attribute_across_an_initializer(
    tmp_path):
  # `USER [[...]] = 5`: the initializer stretches the enumerator's extent
  # over its own attribute, so neither edge of the run touches anything and
  # only the "swallowed" rule finds it
  text = _emitted(_support(tmp_path), ANNOTATED_HPP)
  assert ('"USER", Colour::USER, "", "", '
          '::reflect::tag_list<"wire", "hot">>') in text


def test_a_declaration_that_says_nothing_says_exactly_what_it_always_did(
    tmp_path):
  text = _emitted(_support(tmp_path), ANNOTATED_HPP)
  assert '"plain", &Options::plain, "nothing said about this one", false>' \
    in text


def test_a_header_with_no_annotations_produces_not_one_extra_byte(tmp_path):
  # BYTE IDENTITY. Every reflect header already on disk in the fleet was
  # generated from a header shaped like this one, and none of them may move.
  text = _emitted(tmp_path, METHOD_COMMENTS_HPP)
  assert "tag_list" not in text
  assert "type_scheme" not in text


TYPO_HPP = """\
#pragma once
namespace demo {
  struct Options
  {
    friend constexpr auto reflect_scheme(Options*);
    int retries [[buildutil::lable("tries")]] { 3 };
  };
}
"""


def test_a_typo_in_the_buildutil_namespace_fails_the_generation(
    tmp_path, capsys):
  # This is what recovers the typo safety that clang's blunt
  # -Wno-unknown-attributes gives up: nothing else in the toolchain will
  # ever mention this attribute again.
  output = tmp_path / "typo.reflect.hpp"
  header = tmp_path / "typo.hpp"
  header.write_text(TYPO_HPP)
  assert _generate(header, output, tmp_path) != 0
  said = capsys.readouterr().err
  assert "buildutil::lable" in said
  assert "label, meta" in said, "the valid set is not named"
  assert "typo.hpp:6" in said
  assert not output.exists(), "a half-annotated header was written anyway"


def test_a_foreign_attribute_is_not_buildutils_business(tmp_path):
  # strictness stops at the namespace boundary: [[nodiscard]] and
  # [[gnu::pure]] are somebody else's, and refusing them would be absurd
  text = _emitted(_support(tmp_path), """\
#pragma once
namespace demo {
  struct Options
  {
    friend constexpr auto reflect_scheme(Options*);
    [[gnu::deprecated]] [[buildutil::label("x")]] int old { 0 };
    [[deprecated]] auto gone() const -> int;
  };
}
""")
  assert '"old", &Options::old, "", false, "x">' in text
  assert '"gone", &Options::gone' in text


# --- the macro tier, through a real build -----------------------------------

FLIPPED_HPP = """\
#pragma once
#include <_buildutil/reflect.hpp>

namespace demo {

  // Written in the positions an ATTRIBUTE is legal in -- trailing for an
  // enumerator, either side of a member -- so this header reads the same
  // whichever way the macros expand. That is the migration: move the site,
  // then flip the switch.
  enum class [[buildutil::label("role")]] Role : int
  {
    SYSTEM _Label(system),
    USER   _Label(user) = 5
  };
  constexpr auto reflect_scheme(Role*);

  struct Endpoint
  {
    friend constexpr auto reflect_scheme(Endpoint*);

    bool tls  _Label(secure) { false };   /* require TLS */
    int  port _Meta("wire")  { 80 };
  };
}
"""

FLIPPED_CPP = """\
#include <demo/thing/thing.hpp>

constexpr auto E = reflect::scheme_of<demo::Role>();
static_assert(decltype(reflect::scheme_item<0>(E))::LABEL == "system");
static_assert(decltype(reflect::scheme_item<1>(E))::LABEL == "user");
static_assert(static_cast<int>(decltype(reflect::scheme_item<1>(E))::VALUE) == 5);
static_assert(reflect::type_scheme_of<demo::Role>().LABEL == "role");

constexpr auto S = reflect::scheme_of<demo::Endpoint>();
static_assert(decltype(reflect::scheme_item<0>(S))::LABEL == "secure");
static_assert(decltype(reflect::scheme_item<0>(S))::COMMENT == "require TLS");
static_assert(decltype(reflect::scheme_item<1>(S))::tags::SIZE == 1);
static_assert(decltype(reflect::scheme_item<1>(S))::tags::VALUES[0] == "wire");
// a type that said nothing about itself still answers, with the empty shape
static_assert(reflect::type_scheme_of<demo::Endpoint>().LABEL == "");
static_assert(decltype(reflect::type_scheme_of<demo::Endpoint>())::tags::SIZE
              == 0);

int flipped() { return 0; }
"""


def _flipped(root: Path, annotation: str) -> Path:
  cfg = dict(CFG)
  cfg["reflect_annotation"] = annotation
  deposit.ensure(root, cfg, ["reflect"])
  (root / "CMakeLists.txt").write_text(ROOT_CMAKE)
  src = root / "sources"
  src.mkdir(exist_ok=True)
  (src / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  module = src / "demo" / "thing"
  module.mkdir(parents=True)
  (module / "CMakeLists.txt").write_text("Init_submodule()\n")
  (module / "thing.hpp").write_text(FLIPPED_HPP)
  (module / "thing.cpp").write_text(FLIPPED_CPP)
  return root


def test_the_same_header_reads_the_same_whichever_way_the_macros_expand(
    tmp_path):
  # every assertion is a static_assert in thing.cpp, so two green builds
  # ARE the assertion -- and the only difference between them is what
  # `_Label` turned into
  for annotation in ("macro", "attribute"):
    root = tmp_path / annotation
    root.mkdir()
    done = _build(_flipped(root, annotation))
    assert done.returncode == 0, done.stdout + done.stderr


def test_opting_in_ships_the_attribute_expansion(tmp_path):
  root = _flipped(tmp_path, "attribute")
  assert _build(root).returncode == 0
  shipped = (root / "b" / "generated" / "_buildutil"
             / "reflect-macros.hpp").read_text()
  assert "#define _Label(x)  [[buildutil::label(#x)]]" in shipped
  assert "#include <_buildutil/reflect-macros.hpp>" in (
    root / "b" / "generated" / "_buildutil" / "reflect.hpp").read_text()


def test_staying_on_macro_expansion_ships_the_empty_one(tmp_path):
  # the default, and the reason this release breaks nothing: a fleet that
  # writes `_Label(x) SYSTEM` would be writing a LEADING enumerator
  # attribute the moment the default moved, and that is ill-formed
  root = _flipped(tmp_path, "macro")
  assert _build(root).returncode == 0
  shipped = (root / "b" / "generated" / "_buildutil"
             / "reflect-macros.hpp").read_text()
  assert "#define _Label(x)\n" in shipped


def test_attribute_mode_silences_the_warning_it_causes(tmp_path):
  root = _flipped(tmp_path, "attribute")
  done = _build(root)
  assert done.returncode == 0, done.stdout + done.stderr
  said = done.stdout + done.stderr
  assert "attribute" not in said, said
  command = _command_for(_compile_commands(root), "thing.cpp")
  assert ("-Wno-attributes=buildutil::" in command
          or "-Wno-unknown-attributes" in command), command


def test_macro_mode_asks_for_no_suppression_at_all(tmp_path):
  # a project that never opted in must not lose a compiler warning for it
  command = _command_for(_compile_commands(_flipped(tmp_path, "macro")),
                         "thing.cpp")
  assert "-Wno-attributes" not in command
  assert "-Wno-unknown-attributes" not in command


# --- the escape hatch: a project's own spellings ----------------------------

OWN_MACROS_HPP = """\
#pragma once
// This project already means something else by _Label, so it spells the
// annotations itself. buildutil reads these definitions back to learn which
// identifiers to look for -- there is no second list.
#define OX_NAME(x)  [[buildutil::label(#x)]]
#define OX_TAG(...) [[buildutil::meta(__VA_ARGS__)]]
"""

OWN_HPP = """\
#pragma once
#include <_buildutil/reflect.hpp>

namespace demo {
  struct Endpoint
  {
    friend constexpr auto reflect_scheme(Endpoint*);
    bool tls  OX_NAME(secure) { false };
    int  port OX_TAG("wire")  { 80 };
  };
}
"""

OWN_CPP = """\
#include <demo/thing/thing.hpp>

constexpr auto S = reflect::scheme_of<demo::Endpoint>();
static_assert(decltype(reflect::scheme_item<0>(S))::LABEL == "secure");
static_assert(decltype(reflect::scheme_item<1>(S))::tags::VALUES[0] == "wire");
int own() { return 0; }
"""


def test_a_project_spelling_its_own_annotations_is_read_the_same(tmp_path):
  # the collision escape hatch, end to end: the spellings are the project's,
  # the attributes underneath are buildutil's, and a green build IS the
  # assertion (the static_asserts are in thing.cpp)
  cfg = dict(CFG)
  cfg["reflect_annotation"] = "attribute"
  cfg["reflect_macros"] = "sources/labels.hpp"
  deposit.ensure(tmp_path, cfg, ["reflect"])
  (tmp_path / "CMakeLists.txt").write_text(ROOT_CMAKE)
  src = tmp_path / "sources"
  src.mkdir(exist_ok=True)
  (src / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  (src / "labels.hpp").write_text(OWN_MACROS_HPP)
  module = src / "demo" / "thing"
  module.mkdir(parents=True)
  (module / "CMakeLists.txt").write_text("Init_submodule()\n")
  (module / "thing.hpp").write_text(OWN_HPP)
  (module / "thing.cpp").write_text(OWN_CPP)

  done = _build(tmp_path)
  assert done.returncode == 0, done.stdout + done.stderr
  # buildutil defines nothing of its own; the one include reaches the
  # project's file instead
  shipped = (tmp_path / "b" / "generated" / "_buildutil"
             / "reflect-macros.hpp").read_text()
  assert "#define _Label" not in shipped
  assert str(src / "labels.hpp") in shipped


def test_a_macro_file_that_names_no_annotation_is_refused_at_configure(
    tmp_path):
  # pointing at the wrong file would otherwise configure, build, and reflect
  # nothing -- the silent no-op this whole feature exists to rule out
  cfg = dict(CFG)
  cfg["reflect_macros"] = "sources/labels.hpp"
  deposit.ensure(tmp_path, cfg, ["reflect"])
  (tmp_path / "CMakeLists.txt").write_text(ROOT_CMAKE)
  src = tmp_path / "sources"
  src.mkdir(exist_ok=True)
  (src / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  (src / "labels.hpp").write_text("#define OX_NAME(x) x\n")
  done = _configure(tmp_path)
  assert done.returncode != 0
  assert "defines no annotation macro" in done.stdout + done.stderr


def test_methods_are_emitted_in_declaration_order(tmp_path, capsys):
  text = _emitted(tmp_path, """
struct Commands {
  friend constexpr auto reflect_scheme(Commands*);
  void zebra();
  void repeated(int);
  void middle();
  void repeated(double);
  void alpha();
};
""")
  assert (text.index('&Commands::zebra') < text.index('&Commands::middle')
          < text.index('&Commands::alpha'))
  assert '&Commands::repeated' not in text
  assert 'repeated is overloaded (2 declarations)' in capsys.readouterr().err


@pytest.mark.parametrize("mode", list(reflect.Expansion))
@pytest.mark.parametrize("kind", ["member", "method", "enumerator"])
@pytest.mark.parametrize("comment", ["leading", "trailing", "absent"])
@pytest.mark.parametrize("help", ['_Help("help tip")',
                                  '[[buildutil::help("help tip")]]', '_Help("")'])
def test_help_overrides_source_comments(tmp_path, mode, kind, comment, help):
  declarations = {
    "member": f"int item {help};",
    "method": f"{help} void item();",
    "enumerator": f"item {help},",
  }
  declaration = declarations[kind]
  if comment == "leading":
    declaration = "// developer note\n" + declaration
  elif comment == "trailing":
    declaration += " // developer note"
  if kind == "enumerator":
    source = ("enum class S {\n" + declaration
              + "\n};\nconstexpr auto reflect_scheme(S*);")
    reference = "S::item"
  else:
    source = ("struct S {\nfriend constexpr auto reflect_scheme(S*);\n"
              + declaration + "\n};")
    reference = "&S::item"
  text = _emitted(_support(tmp_path, mode),
                  '#include <_buildutil/reflect.hpp>\n' + source)
  expected = "" if help == '_Help("")' else "help tip"
  assert f'"item", {reference}, "{expected}"' in text
  assert "developer note" not in text


def test_duplicate_help_fails_generation(tmp_path, capsys):
  _support(tmp_path)
  header = tmp_path / "duplicate.hpp"
  header.write_text("""
#include <_buildutil/reflect.hpp>
struct S {
  friend constexpr auto reflect_scheme(S*);
  _Help(one) int item _Help(two);
};
""")
  assert _generate(header, tmp_path / "duplicate.reflect.hpp", tmp_path) != 0
  assert 'duplicate buildutil::help' in capsys.readouterr().err


def test_help_on_a_type_reports_that_it_has_no_comment(tmp_path, capsys):
  _support(tmp_path)
  header = tmp_path / "type.hpp"
  header.write_text("""
#include <_buildutil/reflect.hpp>
struct _Help(tip) S { friend constexpr auto reflect_scheme(S*); };
""")
  assert _generate(header, tmp_path / "type.reflect.hpp", tmp_path) != 0
  assert 'type_scheme has no COMMENT' in capsys.readouterr().err
