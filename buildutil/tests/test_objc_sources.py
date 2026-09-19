"""Objective-C and Objective-C++ as first-class module sources.

A macOS GUI cannot avoid Objective-C++: an NSApplication subclass is not
expressible in C++, and CEF requires one. So `.mm` and `.m` are module
sources like any other -- globbed by Init_submodule, classified by the
same platform and test/bench tags, compiled with ARC, and with the
languages enabled by presence at root scope.

WHAT DECIDES: the TARGET platform, never the host. cmake sets `APPLE`
from CMAKE_SYSTEM_NAME after project(), so an osxcross cross-build from
Linux is Apple and gets the .mm; a Linux build never sees it. The trap
this exists to close is quieter than a missing file: `.mm` is ALSO in
CMAKE_CXX_SOURCE_FILE_EXTENSIONS, so a .mm compiled without OBJCXX
enabled is silently built as C++ -- which happens to work under clang
and stops working under anything else.

The real macOS compile is asserted on a Mac (see the project's own
runbook). What this box can prove is the globbing, the language enable,
the ARC flag and the platform gate; the Darwin-cross configure below
does that with a toolchain file rather than a mocked variable.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from buildutil import deposit
from buildutil.config import _modules_without_arc

import tomllib

PYSUPPORT = Path(deposit.__file__).resolve().parent / "pysupport"



def _rendered() -> str:
  """The machinery as a project actually sees it. The template carries
  @PLACEHOLDER@s -- the platform tag table among them, rendered from
  naming.py -- so asserting on the template would assert on the wrong
  text."""
  import tempfile
  from buildutil import deposit as _deposit
  out = _deposit.ensure(Path(tempfile.mkdtemp()),
                        {"cmake_option_prefix": "A",
                         "module_define_prefix": "A"})
  return (out / "buildutil.cmake").read_text()


MACHINERY = _rendered()

e2e = pytest.mark.skipif(
  shutil.which("cmake") is None or shutil.which("ninja") is None,
  reason="needs cmake and ninja")


# ------------------------------------------------------- the declaration --

def arc(text):
  return _modules_without_arc(tomllib.loads(text))


def test_arc_is_the_default_and_needs_no_declaration():
  assert arc('[modules]\ndormant = ["a"]\n') == []


def test_a_module_can_opt_out_of_arc():
  assert arc("[modules.legacygui]\nobjc_arc = false\n") == ["legacygui"]


def test_opting_in_explicitly_is_the_same_as_saying_nothing():
  assert arc("[modules.gui]\nobjc_arc = true\n") == []


def test_the_opt_out_sits_beside_dormant_without_colliding():
  """`[modules]` already carries a `dormant` LIST; a per-module sub-table
  lives in the same TOML section, which is why the shape is
  [modules.<name>] rather than a second table nobody would connect."""
  text = '[modules]\ndormant = ["as"]\n[modules.gui]\nobjc_arc = false\n'
  assert arc(text) == ["gui"]
  from buildutil.config import _load_project  # the dormant half still reads
  assert tomllib.loads(text)["modules"]["dormant"] == ["as"]


@pytest.mark.parametrize("text, message", [
  ("[modules.gui]\nwat = 1\n", "unknown key"),
  ('[modules.gui]\nobjc_arc = "no"\n', "must be true or false"),
])
def test_a_malformed_module_table_names_itself(text, message):
  with pytest.raises(SystemExit) as raised:
    arc(text)
  assert message in str(raised.value)


def test_the_opt_out_renders_into_the_deposit(tmp_path):
  out = deposit.ensure(tmp_path, {"cmake_option_prefix": "A",
                                  "module_define_prefix": "A",
                                  "modules_no_arc": ["legacygui"]})
  machinery = (out / "buildutil.cmake").read_text()
  assert 'set(_buildutil_modules_no_arc "legacygui")' in machinery
  assert "@MODULES_NO_ARC@" not in machinery


# --------------------------------------------------------- the machinery --

def test_objc_languages_are_enabled_by_presence_at_root_scope():
  """Enabled from the extension table, not from a hardcoded if: a tree
  with no .mm never pays for the compiler probe."""
  assert "foreach(_bu_ext IN LISTS _buildutil_source_extensions)" in MACHINERY
  assert "enable_language(${_bu_lang})" in MACHINERY
  assert 'file(GLOB_RECURSE _bu_found CONFIGURE_DEPENDS' in MACHINERY
  # gated on the TARGET being what the extension allows -- cmake derives
  # APPLE (and CMAKE_SYSTEM_NAME) after project(), so an osxcross
  # cross-build counts
  assert "_buildutil_ext_systems_${_bu_ext}" in MACHINERY


def test_the_module_glob_takes_mm_and_m_on_apple_only():
  """Not globbed-then-filtered: NOT GLOBBED. A CONFIGURE_DEPENDS glob is a
  re-configure trigger, so editing an Objective-C++ file on Linux must not
  re-run cmake."""
  from buildutil import naming
  assert naming.SOURCE_EXTENSIONS["mm"] == ("Darwin",)
  assert naming.SOURCE_EXTENSIONS["m"] == ("Darwin",)
  assert naming.SOURCE_EXTENSIONS["cpp"] == ()
  block = MACHINERY.split("function(_buildutil_split_sources")[1]
  block = block.split("endfunction()")[0]
  assert "_buildutil_ext_systems_${_ext}" in block
  assert "list(APPEND _glob_dead" in block
  assert "file(GLOB_RECURSE all_cpp CONFIGURE_DEPENDS ${_glob_live})" in block


def test_objc_sources_carry_the_same_tags_as_cpp():
  """The tag alternation is RENDERED from naming.PLATFORM_TAGS, and the
  class suffixes no longer name extensions at all -- any extension the
  table calls a source can be a test or a bench TU."""
  from buildutil import naming
  alternation = naming.platform_alternation()
  assert (r'"\\.(' + alternation + r')\\.(test\\.|bench\\.)?[A-Za-z0-9]+$"') \
    in MACHINERY
  assert r'"\\.test\\.[A-Za-z0-9]+$"' in MACHINERY
  assert r'"\\.bench\\.[A-Za-z0-9]+$"' in MACHINERY


def test_arc_is_applied_per_language_not_per_target():
  assert '"$<$<COMPILE_LANGUAGE:OBJC,OBJCXX>:-fobjc-arc>"' in MACHINERY


def test_cxx_only_flags_do_not_reach_a_plain_objective_c_tu():
  """A .m is C. clang reports every C++ flag on its command line as
  "argument unused during compilation" -- noise anywhere, an error under
  -Werror. -fvisibility=hidden and -fdollars-in-identifiers are valid in
  C and stay unguarded."""
  assert "set(_buildutil_cxx_langs \"$<COMPILE_LANGUAGE:CXX,OBJCXX>\")" \
    in MACHINERY
  for flag in ("-fconstexpr-steps=100000000", "-fconstexpr-ops-limit=100000000",
               "-fvisibility-inlines-hidden"):
    line = next(l for l in MACHINERY.splitlines() if flag in l
                and "target_compile_options" not in l)
    assert "${cxx}" in line, f"{flag} is not language-guarded: {line}"


def test_ccache_covers_the_objc_languages():
  from buildutil import engine
  source = Path(engine.__file__).read_text()
  assert '("C", "CXX", "OBJC", "OBJCXX")' in source


def test_clang_tidy_does_not_try_to_scan_objective_c():
  """`buildutil analyze` globs *.cpp, so an Objective-C++ TU is simply
  not offered to clang-tidy -- which is the wanted behaviour, not an
  oversight, and is asserted so a future 'let's glob everything' knows."""
  from buildutil.commands import analyze
  source = Path(analyze.__file__).read_text()
  assert 'rglob("*.cpp")' in source
  assert 'rglob("*.mm")' not in source


def test_coverage_treats_an_objc_test_tu_as_scaffolding():
  from buildutil.commands import coverage
  source = Path(coverage.__file__).read_text()
  assert r'".*\.test\.mm$"' in source
  assert r'".*\.test\.m$"' in source


# ------------------------------------------------------------------- e2e --

ROOT_CMAKE = """\
cmake_minimum_required(VERSION 3.25)
project(objc CXX)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
add_subdirectory(sources)
"""


def _scaffold(tmp_path, no_arc=()):
  deposit.ensure(tmp_path, {"cmake_option_prefix": "OBJ",
                            "module_define_prefix": "OBJ",
                            "modules_no_arc": list(no_arc)})
  (tmp_path / "CMakeLists.txt").write_text(ROOT_CMAKE)
  src = tmp_path / "sources"
  (src / "gui").mkdir(parents=True)
  (src / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  (src / "gui" / "CMakeLists.txt").write_text("Init_submodule()\n")
  (src / "gui" / "main.cpp").write_text("int main() { return 0; }\n")
  # Deliberately NOT valid C++: if the glob ever picks these up off an
  # Apple target, the build fails loudly instead of compiling ObjC as C++
  (src / "gui" / "platform.macos.mm").write_text(
    "#import <Foundation/Foundation.h>\n"
    "@interface Thing : NSObject @end\n@implementation Thing @end\n")
  (src / "gui" / "shim.macos.m").write_text(
    "#import <Foundation/Foundation.h>\n"
    "@interface Shim : NSObject @end\n@implementation Shim @end\n")
  return tmp_path


@e2e
def test_objective_c_sources_are_inert_on_a_non_apple_target(tmp_path):
  root = _scaffold(tmp_path)
  build = tmp_path / "b"
  configure = subprocess.run(
    ["cmake", "-S", str(root), "-B", str(build), "-G", "Ninja",
     f"-DBUILDUTIL_PY={sys.executable}",
     f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}"], capture_output=True, text=True)
  assert configure.returncode == 0, configure.stdout + configure.stderr
  built = subprocess.run(["cmake", "--build", str(build)],
                         capture_output=True, text=True)
  assert built.returncode == 0, built.stdout + built.stderr
  # neither language enabled, neither file compiled
  assert "OBJCXX" not in (build / "CMakeCache.txt").read_text()
  compdb = (build / "compile_commands.json").read_text()
  assert ".mm" not in compdb and "shim.macos.m" not in compdb


# ------------------------------------------------- the cross-build trap --

def test_the_objc_compilers_default_to_the_cxx_ones_before_enabling():
  """The quiet cross-build failure. CMake does NOT derive
  CMAKE_OBJCXX_COMPILER from CMAKE_CXX_COMPILER -- it re-runs its own
  search, and on a cross build with nothing told to it that search finds
  the HOST c++. The .mm then compiles, links against the wrong runtime,
  and the build has stopped being a cross build with no diagnostic."""
  assert 'set(CMAKE_OBJCXX_COMPILER "${CMAKE_CXX_COMPILER}")' in MACHINERY
  assert 'set(CMAKE_OBJC_COMPILER "${CMAKE_C_COMPILER}")' in MACHINERY
  # and the defaulting comes BEFORE the enable, or it does nothing
  assert (MACHINERY.index("CMAKE_OBJCXX_COMPILER")
          < MACHINERY.index("enable_language(${_bu_lang})"))


def test_the_osxcross_profile_names_an_absolute_objcpp():
  """conan spells it `objcpp`, CMake calls it OBJCXX. Absolute on
  purpose: `c`/`cpp` are resolved against PATH by project(), but a
  language enabled later is refused a bare name ("is not a full path and
  was not found in the PATH")."""
  from buildutil import engine
  source = Path(engine.__file__).read_text()
  assert "'objcpp': '{objcxx}'" in source
  assert 'shutil.which("oa64-clang++")' in source


def test_the_osxcross_lane_names_install_name_tool(monkeypatch):
  """CMakeFindBinUtils only looks for it once a language is enabled AFTER
  Platform/Darwin.cmake -- which is exactly what enabling OBJCXX does --
  and then hard errors. Both seams name it: the project's own configure
  and the profile every dependency is built through."""
  from buildutil import engine
  monkeypatch.setattr(engine, "_osxcross_live", lambda: True)
  monkeypatch.setattr(engine, "_osxcross_tool", lambda name: f"/xc/{name}")
  assert "-DCMAKE_INSTALL_NAME_TOOL=/xc/install_name_tool" \
         in engine._osxcross_configure_args()
  extra, _ = engine._osxcross_binutils()
  assert extra["CMAKE_INSTALL_NAME_TOOL"] == "/xc/install_name_tool"


# ------------------------------------------------------ Apple frameworks --

def frameworks(text):
  from buildutil.config import _module_frameworks
  return _module_frameworks(tomllib.loads(text))


def test_a_module_declares_the_apple_frameworks_it_links():
  assert frameworks('[modules.gui]\nframeworks = ["Cocoa", "Foundation"]\n') \
    == {"gui": ["Cocoa", "Foundation"]}


def test_a_bare_string_is_one_framework():
  assert frameworks('[modules.gui]\nframeworks = "Cocoa"\n') == {"gui": ["Cocoa"]}


def test_declaring_none_is_not_an_entry():
  assert frameworks('[modules.gui]\nobjc_arc = false\n') == {}


def test_a_framework_name_is_checked():
  with pytest.raises(SystemExit) as raised:
    frameworks('[modules.gui]\nframeworks = ["-framework Cocoa"]\n')
  assert "is not a framework name" in str(raised.value)


def test_frameworks_render_into_the_deposit(tmp_path):
  out = deposit.ensure(tmp_path, {"cmake_option_prefix": "A",
                                  "module_define_prefix": "A",
                                  "modules_frameworks": {"gui": ["Cocoa"]}})
  machinery = (out / "buildutil.cmake").read_text()
  assert '_buildutil_module_frameworks("gui" "Cocoa")' in machinery
  assert "@MODULE_FRAMEWORKS@" not in machinery


def test_frameworks_are_linked_only_where_the_module_builds():
  assert 'target_link_libraries(${target} ${visibility} "-framework ${_arc_fw}")' \
    in MACHINERY
  # inside the APPLE-gated helper, so the declaration is inert elsewhere
  block = MACHINERY.split("function(_buildutil_apply_objc_options")[1] \
                   .split("endfunction()")[0]
  assert "if(NOT APPLE)" in block and "-framework" in block


# ------------------------------------------- the extension platform table --

def test_the_extension_carries_the_platform_default():
  """A .mm is Objective-C++ and therefore an Apple source BY BEING ONE;
  tagging it .macos.mm would repeat the extension. window.mm needs no tag
  and is simply not globbed off an Apple target."""
  from buildutil import naming
  assert naming.SOURCE_EXTENSIONS["mm"] == ("Darwin",)
  assert naming.SOURCE_EXTENSIONS["rc"] == ("Windows",)
  assert naming.SOURCE_EXTENSIONS["manifest"] == ("Windows",)
  assert naming.SOURCE_EXTENSIONS["cpp"] == ()
  assert naming.SOURCE_EXTENSIONS["s"] == ()
  # .asm is deliberately absent: the watcom extension's Init_firmware()
  # owns it, and claiming it here would silently compile a firmware
  # image into a host module
  assert "asm" not in naming.SOURCE_EXTENSIONS


def test_a_tag_only_narrows_and_a_contradiction_is_refused():
  assert "do not overlap" in MACHINERY
  assert "the file is for no" in MACHINERY


def test_every_language_is_enabled_from_the_one_table():
  """Not an if(APPLE) with two globs in it: the loop reads the same
  extension table the module glob reads, so adding an extension adds its
  language too."""
  assert "foreach(_bu_ext IN LISTS _buildutil_source_extensions)" in MACHINERY
  assert "enable_language(${_bu_lang})" in MACHINERY
  assert '_buildutil_ext_language_${_bu_ext}' in MACHINERY


def test_headers_are_never_filtered_by_tag():
  """Nobody chooses what #include \"foo.h\" opens -- the preprocessor
  does -- so a tagged header would resolve or not by a rule the include
  path knows nothing about."""
  from buildutil import naming
  for ext in naming.HEADER_EXTENSIONS:
    assert ext not in naming.SOURCE_EXTENSIONS


def test_families_are_less_specific_than_exact_platforms():
  from buildutil import naming
  assert naming.PLATFORM_LEVEL["posix"] == naming.PLATFORM_LEVEL["apple"] == 1
  assert naming.PLATFORM_LEVEL["linux"] == naming.PLATFORM_LEVEL["macos"] == 2
  assert naming.live_platform_tags("Darwin") == ("native", "posix", "apple", "macos")
  assert naming.live_platform_tags("Linux") == ("native", "posix", "linux")
  assert naming.live_platform_tags("Windows") == ("native", "win32")
  # anything the table does not name is a generic unix
  assert naming.live_platform_tags("FreeBSD") == ("native", "posix", "linux")
