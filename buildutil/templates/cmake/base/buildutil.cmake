# the buildutil machinery — re-rendered into _bdudata/cmake/ on every
# build; the @-prefixes came from this project's buildutil.toml. A
# project root reaches it with `include(buildutil)` and needs nothing
# else: cmake_minimum_required, project(), this line, and the source
# tree.
#
# Conventions:
#   * sources/CMakeLists.txt declares project deps via Require(...).
#     The conanfile reads the same calls to derive its requirements, so
#     the CMake calls are the single source of truth.
#   * Every module calls Init_submodule() and is a library first and
#     foremost; what else it grows is presence-driven -- *.test.cpp /
#     *.test/ add a <module>-tests runner, *.bench.cpp / *.bench/ add
#     <module>-benches, and a main.cpp makes the module an EXECUTABLE
#     linking the library, built into <build>/bin under the module's own
#     DIRECTORY name (sources/mstools/rc ships `rc`).
#   * A module publishes its headers through sources/ (<module>/foo.h).
#     Its own directory is on its PRIVATE include path -- reachable from
#     every TU of the module, never exported to consumers.
#   * A project extends this file without forking it: any *.cmake in the
#     project's own `cmake/` dir is included at the end (see the bottom).

# file-scope capture: in a function body CMAKE_CURRENT_LIST_DIR is
# the CALLER's dir, so sibling machinery must be pinned here
set(_buildutil_cmake_dir "${CMAKE_CURRENT_LIST_DIR}")
include_guard(GLOBAL)

# ---- driver guard (owner ruling): this tree is CONTROLLED BY BUILDUTIL.
# The driver passes -DBUILDUTIL_PY at configure and exports BUILDUTIL=
# <version> to every child process; a bare `cmake -S .` has neither and
# is refused HERE, at the first thing the root CMakeLists includes. The
# same refusal covers `ninja`/`cmake --build` (an always-built custom
# target below) and `ctest` (a setup fixture every registered test
# requires) — see driver_guard.cmake, which carries the message.
if(NOT DEFINED BUILDUTIL_PY AND NOT DEFINED ENV{BUILDUTIL})
  message(FATAL_ERROR
    "this project is controlled by buildutil — configuring by hand skips "
    "the venv, the conan profile and the rendered machinery. Run "
    "`buildutil build` (or ./buildutil build) instead; if you really "
    "need direct cmake, set BUILDUTIL=1 in the environment.")
endif()
add_custom_target(buildutil-driver-guard ALL
  COMMAND "${CMAKE_COMMAND}" -P "${_buildutil_cmake_dir}/driver_guard.cmake"
  COMMENT "buildutil driver guard")

# ===========================================================================
# UNIVERSAL PROJECT POLICY. Everything here is identical for every
# buildutil project, so it lives in the one file every root includes: a
# root is cmake_minimum_required + project() + include(buildutil) + the
# source tree, and a policy change lands with the machinery instead of
# waiting on a hand-edit in each repo.
#
# This file is included FROM the root CMakeLists, so everything below lands
# in ROOT DIRECTORY SCOPE and every module added afterwards inherits it.
# Nothing here may move into a function for that reason.
# ===========================================================================

# The compile database every tool in the box reads: clangd, the vscode
# integration, `buildutil analyze`, the reflect scan. A project without
# it gets a working build and silently broken tooling.
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# enable_testing() + the BUILD_TESTING option (CTest), and
# gtest_discover_tests for _buildutil_add_test_target below (GoogleTest).
# The machinery CALLS both, so the machinery includes them -- a root
# without either fails inside a function, pages from the cause.
include(CTest)
include(GoogleTest)

# Read by Require(... BENCH) and _buildutil_add_bench_target. option()
# leaves an existing cache entry alone, so this declaration never fights
# the driver's own -DBUILD_BENCHMARKING, which lands in the cache before
# this file is read; it is what gives the switch a default and a help
# string for anyone reading the cache.
option(BUILD_BENCHMARKING
  "Build *.bench.cpp targets that link google-benchmark." OFF)

# The two build-shape switches the driver owns (`buildutil coverage`,
# `buildutil build --gc-sections`). One name for every project --
# BUILDUTIL_*, not <PREFIX>_* -- because the machinery acting on them is
# one file, and a per-project spelling only ever existed to let each root
# write the same block again. engine.py passes exactly these.
option(BUILDUTIL_COVERAGE
  "Instrument with --coverage so gcov can collect line/branch data." OFF)
option(BUILDUTIL_GC_SECTIONS
  "Build with -ffunction-sections + linker --gc-sections/--print-gc-sections so the linker lists unreferenced (dead) functions/data at link time." OFF)

if(BUILDUTIL_COVERAGE)
  if(NOT CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang|AppleClang")
    message(FATAL_ERROR "BUILDUTIL_COVERAGE requires gcc or clang.")
  endif()
  # --coverage on both compile + link adds the gcov runtime and emits
  # .gcno alongside object files; -O0 + -g keep line numbers honest.
  # -fprofile-abs-path pins the cwd recorded in .gcno so gcovr never
  # falls back to spawning gcov from $HOME.
  add_compile_options(--coverage -O0 -g -fprofile-abs-path)
  add_link_options(--coverage)
endif()

if(BUILDUTIL_GC_SECTIONS)
  if(MSVC OR APPLE)
    message(FATAL_ERROR "BUILDUTIL_GC_SECTIONS needs GNU ld or lld (Linux gcc/clang); MSVC and Apple ld don't emit --print-gc-sections.")
  endif()
  # One section per function/datum so the linker can drop them
  # individually; --print-gc-sections lists each dropped (dead-code)
  # section, which buildutil captures into gc-sections.log.
  add_compile_options(-ffunction-sections -fdata-sections)
  add_link_options(-Wl,--gc-sections -Wl,--print-gc-sections)
endif()

# the ctest half: one setup fixture, registered lazily by the first
# test-adding function, that every test REQUIRES — a bare `ctest` then
# fails the fixture (with the explanation) instead of running suites
# outside the driver's environment
function(_buildutil_ensure_ctest_guard)
  get_property(_dg_done GLOBAL PROPERTY _buildutil_ctest_guard)
  if(_dg_done)
    return()
  endif()
  set_property(GLOBAL PROPERTY _buildutil_ctest_guard TRUE)
  add_test(NAME buildutil-driver-guard
    COMMAND "${CMAKE_COMMAND}" -P "${_buildutil_cmake_dir}/driver_guard.cmake")
  set_tests_properties(buildutil-driver-guard PROPERTIES
    FIXTURES_SETUP buildutil-driver)
endfunction()

# The scope a module's OWN directory gets on its library target, decided
# once for the whole tree by [cmake] export_module_headers in
# buildutil.toml (rendered here; OFF unless a project opted in). PRIVATE
# is the rule -- consumers include qualified through sources/. PUBLIC is
# the ported-tree escape hatch, where unqualified cross-module includes
# are load-bearing in code nobody is rewriting. See Init_submodule for
# what exporting costs.
if(@EXPORT_MODULE_HEADERS@)
  set(_buildutil_own_dir_scope PUBLIC)
  message(STATUS
    "module headers are EXPORTED ([cmake] export_module_headers): an "
    "unqualified cross-module #include resolves by LINK ORDER")
else()
  set(_buildutil_own_dir_scope PRIVATE)
endif()

function(Require NAME)
  cmake_parse_arguments(REQ "TEST;BENCH;TOOL;SYSTEM;PUBLIC;FORCE" "VERSION;CONAN" "COMPONENTS;PLATFORM;OPTIONS" ${ARGN})
  _buildutil_refuse_misplaced_require(${NAME})
  # PUBLIC says this dep is part of the module's own API -- its headers
  # appear in headers this project EXPORTS, so a consumer of
  # the published package needs them on its include path too. Acted on
  # only by the conanfile reading this same call (transitive_headers on
  # the requires); here it is validated. Meaningless off the runtime
  # graph: TEST/BENCH/TOOL deps never reach a consumer at all
  # (format_json.hpp includes <nlohmann/json.hpp>, every consumer failed
  # to compile).
  if(REQ_PUBLIC AND (REQ_TEST OR REQ_BENCH OR REQ_TOOL))
    message(FATAL_ERROR
      "Require(${NAME}): PUBLIC only makes sense on a runtime "
      "dependency — TEST/BENCH/TOOL deps never propagate to a "
      "package consumer, so PUBLIC there would silently do nothing.")
  endif()
  _buildutil_refuse_lone_force(${NAME} "${REQ_FORCE}" "${REQ_SYSTEM}")
  # OPTIONS are conan package options (key=value), acted on ONLY by the
  # conanfile reading this same call — here they are validated and
  # otherwise ignored. Both refusals are loud on purpose: a typo'd
  # token or an option on a dep conan never sees would otherwise leave
  # the package silently built with the wrong defaults, which is the
  # hand-edited-default_options failure OPTIONS exists to end.
  foreach(_req_opt IN LISTS REQ_OPTIONS)
    if(NOT _req_opt MATCHES "^[^=]+=")
      message(FATAL_ERROR
        "Require(${NAME}): OPTIONS token '${_req_opt}' is not key=value")
    endif()
  endforeach()
  # COMPONENTS tokens written with a leading + or - are conan OPTIONS in
  # shorthand and NOT find_package components: `+asio` is `with_asio=True`,
  # `-json` is `without_json=True`. A boost-shaped recipe carries one
  # option per library, and naming each of them key=value is a line of
  # noise per library. They are stripped here — find_package must never
  # see one — and acted on by the conanfile reading this same call.
  #
  # Whether the recipe HAS with_asio is deliberately not checked. That is
  # conan's knowledge, conan refuses an unknown option and names it, and a
  # copy of every recipe's option list kept here could only go stale.
  set(_req_names "")
  set(_req_sugar "")
  foreach(_req_token IN LISTS REQ_COMPONENTS)
    if(NOT _req_token MATCHES "^[-+]")
      list(APPEND _req_names "${_req_token}")
      continue()
    endif()
    string(SUBSTRING "${_req_token}" 1 -1 _req_option)
    if(NOT _req_option)
      message(FATAL_ERROR
        "Require(${NAME}): COMPONENTS token '${_req_token}' names no option")
    endif()
    list(APPEND _req_sugar "${_req_token}")
    if(_req_token MATCHES "^\\+")
      set(_req_other "-${_req_option}")
    else()
      set(_req_other "+${_req_option}")
    endif()
    list(FIND REQ_COMPONENTS "${_req_other}" _req_at)
    if(NOT _req_at EQUAL -1)
      message(FATAL_ERROR
        "Require(${NAME}): COMPONENTS has both '+${_req_option}' and "
        "'-${_req_option}' — they set opposite options on the same "
        "library, and which one won would depend on nothing you can read.")
    endif()
    # Both spellings of one option on one call is the silent last-wins that
    # OPTIONS exists to end: say which two, and let the author pick.
    foreach(_req_opt IN LISTS REQ_OPTIONS)
      if(_req_opt MATCHES "^(with|without)_${_req_option}=")
        message(FATAL_ERROR
          "Require(${NAME}): COMPONENTS '${_req_token}' and OPTIONS "
          "'${_req_opt}' both set an option for '${_req_option}'. Write "
          "one of them.")
      endif()
    endforeach()
  endforeach()
  if(REQ_SYSTEM AND (REQ_OPTIONS OR _req_sugar))
    message(FATAL_ERROR
      "Require(${NAME}): OPTIONS with SYSTEM is meaningless — a SYSTEM "
      "dep is the host's package, conan never builds it and the options "
      "would silently do nothing. Drop OPTIONS, or drop SYSTEM and name "
      "the conan package.")
  endif()
  # PLATFORM gates the dep to specific OSes (conan-style names: Linux,
  # Windows, Macos). Skip the find_package entirely when the current
  # build target isn't in the list — the conanfile applies the same
  # filter to the conan graph, so the package isn't even fetched.
  if(REQ_PLATFORM)
    set(_sys "${CMAKE_SYSTEM_NAME}")
    if(_sys STREQUAL "Darwin")
      set(_sys "Macos")
    endif()
    list(FIND REQ_PLATFORM "${_sys}" _idx)
    if(_idx EQUAL -1)
      return()
    endif()
  endif()
  _buildutil_claim_require_name(${NAME})
  # TEST/BENCH deps only exist when the matching CMake option is on,
  # so the rest of the build doesn't pay for the find_package call
  # (or expose the headers) when no test/bench target will use them.
  # a .test module serves the benches too, so TEST deps exist in either lane
  if(REQ_TEST AND NOT BUILD_TESTING AND NOT BUILD_BENCHMARKING)
    return()
  endif()
  if(REQ_BENCH AND NOT BUILD_BENCHMARKING)
    return()
  endif()
  # TOOL deps are build-time executables (the conanfile rides them in as
  # tool_requires, landing their bindir on CMAKE_PROGRAM_PATH) -- nothing
  # to find_package; consumers find_program them at the point of use.
  if(REQ_TOOL)
    return()
  endif()
  # The VERSION goes to find_package only for SYSTEM deps. For a conan-
  # managed dep the range was already enforced when conan resolved the
  # graph, and the config find_package will locate IS the package conan
  # installed — re-checking it can only produce false negatives. And it
  # did: conan's generated config-version treats a dot-less version as
  # its own "major" (SameMajorVersion semantics), so a date-versioned
  # package (re2's "20251105") can never satisfy a lower bound like
  # ">=20230301". SYSTEM deps keep the check — there the host provides
  # the package and nobody else has verified anything. Range operators
  # are stripped: find_package takes a plain number (soft lower bound);
  # the range form is only meaningful for conan.
  if(REQ_SYSTEM)
    string(REGEX REPLACE "^[><=~^]+" "" _ver "${REQ_VERSION}")
    _buildutil_find_host_package(${NAME} "${_ver}" "${_req_names}")
  elseif(_req_names)
    find_package(${NAME} REQUIRED COMPONENTS ${_req_names})
  else()
    find_package(${NAME} REQUIRED)
  endif()
endfunction()

function(_buildutil_refuse_misplaced_require name)
  if(NOT CMAKE_CURRENT_SOURCE_DIR STREQUAL "${CMAKE_SOURCE_DIR}/sources")
    message(FATAL_ERROR
      "Require(${name}) in ${CMAKE_CURRENT_LIST_FILE}: Require belongs in "
      "sources/CMakeLists.txt, the only file conanfile.py reads; anywhere "
      "else it never reaches the conan graph. Move it there.")
  endif()
endfunction()

function(_buildutil_refuse_lone_force name force system)
  if(force AND NOT system)
    message(FATAL_ERROR
      "Require(${name}): FORCE without SYSTEM — FORCE takes the host's "
      "package over the conan pins it replaces, and only a SYSTEM dep is "
      "the host's.")
  endif()
endfunction()

function(_buildutil_claim_require_name name)
  get_property(seen GLOBAL PROPERTY _buildutil_required_names)
  if(name IN_LIST seen)
    message(FATAL_ERROR
      "Require(${name}) is declared twice for this platform; the conanfile "
      "would read two requirements of one package. Keep one.")
  endif()
  set_property(GLOBAL APPEND PROPERTY _buildutil_required_names "${name}")
endfunction()

# The conanfile forces a <conan>/system@host wrapper over every conan pin
# of a SYSTEM dep; this find reaches the host's own config or Find module.
# CMAKE_PREFIX_PATH, CMAKE_MODULE_PATH, CMAKE_LIBRARY_PATH,
# CMAKE_INCLUDE_PATH and CMAKE_PROGRAM_PATH lose conan's generators folder
# and cache, and a <NAME>_DIR cached there is forgotten. Afterwards every
# target the find imported is refused if its imported location is inside
# the conan cache; the find's result variables are not checked.
function(_buildutil_find_host_package _bhp_name _bhp_version _bhp_components)
  # an imported target belongs to the directory that found it, and the
  # rpath pass runs in ROOT scope, where it would not exist
  set(CMAKE_FIND_PACKAGE_TARGETS_GLOBAL ON)
  _buildutil_conan_dirs(_bhp_conan_dirs)
  foreach(_bhp_variable IN ITEMS CMAKE_PREFIX_PATH CMAKE_MODULE_PATH
          CMAKE_LIBRARY_PATH CMAKE_INCLUDE_PATH CMAKE_PROGRAM_PATH)
    _buildutil_without_dirs(${_bhp_variable} "${_bhp_conan_dirs}")
  endforeach()
  _buildutil_forget_conan_dir(${_bhp_name} "${_bhp_conan_dirs}")
  get_property(_bhp_before DIRECTORY PROPERTY IMPORTED_TARGETS)
  if(_bhp_components)
    find_package(${_bhp_name} ${_bhp_version} REQUIRED
                 COMPONENTS ${_bhp_components})
  else()
    find_package(${_bhp_name} ${_bhp_version} REQUIRED)
  endif()
  _buildutil_refuse_cache_imports(${_bhp_name} "${_bhp_before}")
  _buildutil_record_host_imports("${_bhp_before}")
endfunction()

# conan's generators folder (the toolchain's own) and its package cache
function(_buildutil_conan_dirs out)
  set(dirs "")
  if(CMAKE_TOOLCHAIN_FILE)
    get_filename_component(generators "${CMAKE_TOOLCHAIN_FILE}" DIRECTORY)
    file(REAL_PATH "${generators}" generators)
    list(APPEND dirs "${generators}")
  endif()
  if(DEFINED ENV{CONAN_HOME})
    file(REAL_PATH "$ENV{CONAN_HOME}" home)
    list(APPEND dirs "${home}")
  endif()
  set(${out} "${dirs}" PARENT_SCOPE)
endfunction()

function(_buildutil_under_dirs path dirs out)
  set(${out} FALSE PARENT_SCOPE)
  if(NOT path)
    return()
  endif()
  file(REAL_PATH "${path}" real)
  foreach(dir IN LISTS dirs)
    cmake_path(IS_PREFIX dir "${real}" NORMALIZE under)
    if(under)
      set(${out} TRUE PARENT_SCOPE)
    endif()
  endforeach()
endfunction()

function(_buildutil_without_dirs variable dirs)
  set(kept "")
  foreach(entry IN LISTS ${variable})
    _buildutil_under_dirs("${entry}" "${dirs}" under)
    if(NOT under)
      list(APPEND kept "${entry}")
    endif()
  endforeach()
  set(${variable} "${kept}" PARENT_SCOPE)
endfunction()

# a dependant's find_dependency caches <NAME>_DIR at the wrapper's config
function(_buildutil_forget_conan_dir name dirs)
  _buildutil_under_dirs("${${name}_DIR}" "${dirs}" under)
  if(under)
    unset(${name}_DIR CACHE)
  endif()
endfunction()

function(_buildutil_refuse_cache_imports name before)
  if(NOT DEFINED ENV{CONAN_HOME})
    return()
  endif()
  file(REAL_PATH "$ENV{CONAN_HOME}/p" cache)
  get_property(after DIRECTORY PROPERTY IMPORTED_TARGETS)
  foreach(imported IN LISTS after)
    if(imported IN_LIST before)
      continue()
    endif()
    _buildutil_imported_directory(${imported} directory)
    _buildutil_under_dirs("${directory}" "${cache}" under)
    if(under)
      message(FATAL_ERROR
        "Require(${name} ... SYSTEM) imported ${imported} from ${directory}, "
        "inside the conan cache: conan's copy of ${name} is in the graph "
        "beside the host's. Run the build through buildutil so the host "
        "wrapper replaces it.")
    endif()
  endforeach()
endfunction()

function(_buildutil_record_host_imports before)
  get_property(after DIRECTORY PROPERTY IMPORTED_TARGETS)
  foreach(imported IN LISTS after)
    if(NOT imported IN_LIST before)
      set_property(GLOBAL APPEND PROPERTY
                   _buildutil_host_imports "${imported}")
    endif()
  endforeach()
endfunction()

function(_buildutil_imported_directory target out)
  set(${out} "" PARENT_SCOPE)
  set(_id_props IMPORTED_LOCATION)
  string(TOUPPER "${CMAKE_BUILD_TYPE}" _id_build)
  if(_id_build)
    list(APPEND _id_props "IMPORTED_LOCATION_${_id_build}")
  endif()
  get_target_property(_id_configs ${target} IMPORTED_CONFIGURATIONS)
  foreach(_id_config IN LISTS _id_configs)
    list(APPEND _id_props "IMPORTED_LOCATION_${_id_config}")
  endforeach()
  foreach(_id_prop IN LISTS _id_props)
    get_target_property(_id_file ${target} ${_id_prop})
    if(_id_file)
      get_filename_component(_id_dir "${_id_file}" DIRECTORY)
      set(${out} "${_id_dir}" PARENT_SCOPE)
      return()
    endif()
  endforeach()
endfunction()

function(_buildutil_module_platforms out name tags)
  _buildutil_platform_suffixes(live)
  foreach(tag IN LISTS tags)
    if(tag IN_LIST live)
      return()
    endif()
  endforeach()
  set(${out} ${${out}} "${name}" PARENT_SCOPE)
endfunction()

# Local module toggles. <repo>/_bdudata/modules.ini is an ini with [enabled]
# and [disabled] sections, one module name per line (#/; comments and blanks
# ignored). Names under [disabled] go dormant — the subdirectory is never
# added, so the lib, its generated sources, and its test target all drop
# out of the build; everything else builds. A missing file means nothing
# dormant, so a fresh checkout / CI builds everything. _bdudata/ is gitignored
# (the `_*` rule), so a local build-speed choice never reaches history;
# toggling a module never touches a CMakeLists. Edit via `./buildutil module`.
function(_buildutil_dormant_modules out)
  set(cfg "${CMAKE_SOURCE_DIR}/_bdudata/modules.ini")
  # The committed project default, rendered from buildutil.toml
  # ([modules] dormant). A clean clone has no modules.ini at all, so
  # without this a tree with partly-ported modules builds them and dies,
  # with nothing in the repo to say they were never meant to build.
  set(dormant @DEFAULT_DORMANT@)
@MODULE_PLATFORMS@
  set(revived "")
  set(section "")
  if(EXISTS "${cfg}")
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${cfg}")
    file(STRINGS "${cfg}" lines)
    foreach(line IN LISTS lines)
      string(STRIP "${line}" line)
      if(line STREQUAL "" OR line MATCHES "^[#;]")
        continue()
      endif()
      if(line MATCHES "^\\[(.+)\\]$")
        string(TOLOWER "${CMAKE_MATCH_1}" section)
      elseif(section STREQUAL "disabled" OR section STREQUAL "enabled")
        # everything up to an inline comment, NOT "up to the first
        # character an identifier could not hold" -- that truncated
        # `mstools-cl` to `mstools`, so disabling a hyphenated module
        # silently made its unhyphenated sibling dormant instead and left
        # the intended one building. modules.py reads the same line the
        # same way; these two parsers have to agree, since one drives the
        # CLI's idea of what is dormant and this one drives the build.
        string(REGEX MATCH "^[^#;]+" name "${line}")
        string(STRIP "${name}" name)
        if(name)
          if(section STREQUAL "disabled")
            list(APPEND dormant "${name}")
          else()
            # [enabled] is an OVERRIDE now, not decoration: it is how a
            # box builds something the project defaults to dormant.
            list(APPEND revived "${name}")
          endif()
        endif()
      endif()
    endforeach()
  endif()
  if(revived AND dormant)
    list(REMOVE_ITEM dormant ${revived})
  endif()
  if(dormant)
    list(REMOVE_DUPLICATES dormant)
  endif()
  set(${out} "${dormant}" PARENT_SCOPE)
endfunction()

# A generated root is reachable BOTH ways or neither: on the include path
# AND as the #embed search path. There are two sanctioned homes for
# codegen -- Add_generated_source (needs a tool the build produces) and a
# presence-driven configure.py -- and which one a module picks must not
# change what its output is reachable AS. It did: Add_generated_source
# set --embed-dir, configure.py's roots got the include path only, so a
# data file was #include-able either way but #embed-able from only one of
# them. Worse in reverse -- deleting a module's last Add_generated_source
# call, which is exactly what migrating to configure.py does, took
# --embed-dir off that module and (PUBLIC on a library) off everything
# linking it. Nothing fails until some consumer actually embeds, at a call
# site nobody edited. One function now, so the two cannot drift again.
# Whether this compiler ACCEPTS --embed-dir at all. The vendor genex below
# is not enough: gcc only learned #embed in 15, and an older gcc rejects
# the flag outright ("unrecognized command-line option"). That stayed
# hidden while only modules with codegen carried generated roots -- most
# compiles had no --embed-dir on them. Making the parent root universal
# put the flag on EVERY compile, and every older toolchain started failing
# on files it had built fine a minute earlier. Probed once, cached by
# cmake, rather than hardcoding a version table per vendor.
# THE PLATFORM VOCABULARY. Rendered from buildutil/naming.py, which is
# the one table -- these tags name a source (decode.linux.cpp), a
# directory inside a module (host.linux/), a MODULE directory
# (sources/helper.macos/) and a data directory (ui.embed.win32/,
# locale.install.posix/), and they were four separate spellings waiting
# to disagree. EXACT tags (win32/linux/macos) name one target; FAMILY
# tags (posix/apple) name a group, and are LESS specific, which is what
# makes an overlay orderable.
@PLATFORM_TABLE@

include(CheckCXXCompilerFlag)
check_cxx_compiler_flag("--embed-dir=${CMAKE_BINARY_DIR}"
                        BUILDUTIL_CXX_ACCEPTS_EMBED_DIR)

# Objective-C++ on macOS, presence-driven like everything else: a tree
# holding a *.mm anywhere under sources/ gets OBJCXX enabled, a *.m gets
# OBJC, and neither costs a compiler probe in a tree that has none.
#
# This is not a convenience. `.mm` is ALSO in
# CMAKE_CXX_SOURCE_FILE_EXTENSIONS, so a .mm compiled without the
# language enabled is silently built as C++ -- which happens to work on
# clang, where the driver sniffs the extension, and stops working the day
# anything else compiles it. The failure is a platform away from the
# cause, so the language is enabled where the file is found.
#
# It has to happen HERE, at root scope: enabling a language from inside a
# module directory re-runs the compiler probe per directory, and APPLE
# itself is only known after the root project() call this file follows.
# LANGUAGES ENABLED BY PRESENCE, from the extension table. A tree with no
# .mm never pays for an Objective-C++ compiler probe, and a tree with one
# never says a word about it.
#
# "Apple" here is the TARGET, not the host: cmake sets APPLE from
# CMAKE_SYSTEM_NAME after project(), so an osxcross cross-build from
# Linux is Apple and a native Linux build is not.
#
# It has to happen at ROOT scope -- enabling a language from inside a
# module directory re-runs the compiler probe per directory -- which is
# why this is here rather than in Init_submodule.
if(DEFINED _buildutil_platform_live_${CMAKE_SYSTEM_NAME})
  set(_buildutil_system "${CMAKE_SYSTEM_NAME}")
else()
  set(_buildutil_system "Linux")
endif()
if(APPLE)
  # Default the Objective-C compilers to the C/C++ ones BEFORE enabling
  # the languages. CMake does not derive them: it re-runs its own
  # compiler search, and on a cross build with no OBJCXX told to it that
  # search finds the HOST c++ -- at which point the .mm compiles, links
  # against the wrong runtime, and the build has quietly stopped being a
  # cross build. clang++ compiles Objective-C++ and clang compiles
  # Objective-C, so the answer is always the compiler already chosen. A
  # toolchain that DID name one wins: this only fills a hole.
  if(NOT CMAKE_OBJCXX_COMPILER AND CMAKE_CXX_COMPILER)
    set(CMAKE_OBJCXX_COMPILER "${CMAKE_CXX_COMPILER}")
  endif()
  if(NOT CMAKE_OBJC_COMPILER AND CMAKE_C_COMPILER)
    set(CMAKE_OBJC_COMPILER "${CMAKE_C_COMPILER}")
  elseif(NOT CMAKE_OBJC_COMPILER AND CMAKE_CXX_COMPILER)
    set(CMAKE_OBJC_COMPILER "${CMAKE_CXX_COMPILER}")
  endif()
endif()
foreach(_bu_ext IN LISTS _buildutil_source_extensions)
  set(_bu_lang "${_buildutil_ext_language_${_bu_ext}}")
  if(NOT _bu_lang)
    continue()
  endif()
  set(_bu_ext_sys "${_buildutil_ext_systems_${_bu_ext}}")
  if(_bu_ext_sys AND NOT _buildutil_system IN_LIST _bu_ext_sys)
    continue()
  endif()
  file(GLOB_RECURSE _bu_found CONFIGURE_DEPENDS
       "${CMAKE_SOURCE_DIR}/sources/*.${_bu_ext}")
  if(_bu_found)
    enable_language(${_bu_lang})
  endif()
endforeach()

function(_buildutil_add_generated_roots target scope)
  foreach(root IN LISTS ARGN)
    target_include_directories(${target} ${scope} "${root}")
    if(BUILDUTIL_CXX_ACCEPTS_EMBED_DIR)
      # gcc/clang spelling; MSVC differs
      target_compile_options(${target} ${scope}
        "$<$<CXX_COMPILER_ID:GNU,Clang,AppleClang>:--embed-dir=${root}>")
    endif()
  endforeach()
endfunction()

# A module directory may carry a KIND TAG saying what it builds, so a
# directory listing tells you how the tree shakes out without reading a
# line of cmake:
#
#   sources/wd.exe/      an executable
#   sources/parser.lib/  a static library   (.a is a synonym)
#   sources/wd.obj/      an object-only module -- every object reaches
#                        whoever links it, nothing dropped
#   sources/dip.so/      a shared library    (.dll / .dylib synonyms)
#   sources/rig.test/    a test-lane module -- built with the suites,
#                        linked only from TEST/BENCH groups, never shipped
#
# The tag is NEVER part of a name. sources/wd.exe/ is the module `wd` and
# ships a binary called `wd` -- otherwise Linux would install `wd.exe`.
# The name is still implied by the directory, which is why this is not
# the name-override that was ruled out for applications.
#
# .so/.dll/.dylib are SYNONYMS, not platform selectors: the platform
# decides the real suffix. A tag that meant "Windows only" would collide
# with the platform axis (host.win32/) and a listing would stop being
# unambiguous about which of the two it was doing.
set(_buildutil_kind_tags "@KIND_TAGS@")

# A module directory may ALSO carry a platform tag, the same one its
# sources carry: sources/helper.macos/ is the module `helper`, and it
# exists on macOS only. Everywhere else the directory is never entered --
# no target, no test, nothing to link against, exactly as if it were not
# in the tree.
#
# This is the module-scale spelling of a rule that already applied to
# every file and every directory INSIDE a module (*.macos.cpp,
# host.linux/). It was the one scale where the convention ran out, and
# the workaround -- if(APPLE) wrapped around Init_submodule() -- is
# project cmake writing logic, which is the thing a declaration exists to
# replace. Renaming the entry point (main.macos.cpp) does not stand in
# for it either: the entry glob matches exactly main.cpp, so that spelling
# produces a module with no executable on any platform at all.
#
# Tags COMPOSE with the kind tags and with each other, in any order:
# helper.macos.exe/ and helper.exe.macos/ read the same, and every tag
# present must be live for the directory to build. The tag is never part
# of the name -- of the module target, of the binary, or of the install
# mirror -- for the same reason a kind tag is not.
# The platform vocabulary lives at the top of this file, rendered from
# buildutil/naming.py -- see THE PLATFORM VOCABULARY there.

function(_buildutil_kind_of dir out_name out_kind)
  get_filename_component(leaf "${dir}" NAME)
  set(kind "")
  set(name "${leaf}")
  set(found "")
  # platform tags come off first and are not counted as kinds: they say
  # WHERE the module builds, never WHAT it builds, so two of them is not
  # the contradiction two kind tags would be
  set(stripping TRUE)
  while(stripping)
    set(stripping FALSE)
    foreach(tag IN LISTS _buildutil_platform_tags)
      if(name MATCHES "^(.+)\\.${tag}$")
        set(name "${CMAKE_MATCH_1}")
        set(stripping TRUE)
        break()
      endif()
    endforeach()
  endwhile()
  # strip repeatedly: a lone pass sees only the LAST suffix, so
  # `thing.lib.so` would read as one tag and the contradiction would go
  # unnoticed -- exactly the silent listing this is meant to prevent
  set(stripping TRUE)
  while(stripping)
    set(stripping FALSE)
    foreach(tag IN LISTS _buildutil_kind_tags)
      if(name MATCHES "^(.+)\\.${tag}$")
        list(APPEND found "${tag}")
        set(name "${CMAKE_MATCH_1}")
        set(stripping TRUE)
        break()
      endif()
    endforeach()
  endwhile()
  list(LENGTH found tag_count)
  if(tag_count GREATER 1)
    string(REPLACE ";" ", ." pretty "${found}")
    message(FATAL_ERROR
      "${dir} carries more than one kind tag (.${pretty}). A module builds "
      "one thing; a listing that says otherwise is lying about the tree.")
  elseif(tag_count EQUAL 1)
    list(GET found 0 kind)
  endif()
  # a tag order like helper.macos.exe leaves `.macos` exposed only after
  # the kind came off -- strip again rather than caring about the order
  set(stripping TRUE)
  while(stripping)
    set(stripping FALSE)
    foreach(tag IN LISTS _buildutil_platform_tags)
      if(name MATCHES "^(.+)\\.${tag}$")
        set(name "${CMAKE_MATCH_1}")
        set(stripping TRUE)
        break()
      endif()
    endforeach()
  endwhile()
  # synonyms collapse to the shape they mean
  if(kind STREQUAL "a")
    set(kind "lib")
  elseif(kind STREQUAL "dll" OR kind STREQUAL "dylib")
    set(kind "so")
  endif()
  set(${out_name} "${name}" PARENT_SCOPE)
  set(${out_kind} "${kind}" PARENT_SCOPE)
endfunction()

# THE SOURCE TREE IS THE INSTALL TREE (owner ruling). A module's artifact
# installs at its source-relative path: sources/a/b/c/ ships its binary
# as <prefix>/a/b/c(.exe), its library as <prefix>/a/b/libc.so -- the
# artifact file lands in the MIRROR of the module's parent, named as the
# platform spells it. There is no destination mapping anywhere; a project
# wanting a different shipped shape moves its SOURCE directories until
# the mirror is that shape. PATH is an environment concern, not a
# build-system one. Kind tags are stripped from every component, since a
# tag is never part of a name -- of a directory's either.
#
# The BUILD tree deliberately does not mirror: <build>/bin stays the flat
# working set, because the single-main.cpp ninja phony collision is
# a build-tree fact and the test/run/vscode PATH contracts point there.
# The mirror is a statement about what ships.
function(_buildutil_mirror_parent_of dir out)
  file(RELATIVE_PATH _mp_rel "${CMAKE_SOURCE_DIR}/sources" "${dir}")
  string(REPLACE "/" ";" _mp_parts "${_mp_rel}")
  list(POP_BACK _mp_parts)
  set(_mp_clean "")
  foreach(_mp_part IN LISTS _mp_parts)
    _buildutil_kind_of("${_mp_part}" _mp_name _mp_ignored)
    list(APPEND _mp_clean "${_mp_name}")
  endforeach()
  if(_mp_clean)
    string(REPLACE ";" "/" _mp_dir "${_mp_clean}")
    set(${out} "${_mp_dir}" PARENT_SCOPE)
  else()
    set(${out} "." PARENT_SCOPE)
  endif()
endfunction()

function(_buildutil_mirror_parent out)
  _buildutil_mirror_parent_of("${CMAKE_CURRENT_SOURCE_DIR}" _mp_result)
  set(${out} "${_mp_result}" PARENT_SCOPE)
endfunction()

# A rpath entry relative to the loading binary's own directory, spelled
# for the TARGET platform: ld.so expands $ORIGIN and ignores
# @loader_path, dyld expands @loader_path and ignores $ORIGIN, so a
# computed rpath is wrong on one of them unless it is asked for here.
# @loader_path rather than @executable_path because the loader of a
# mirrored artifact may be a dylib beside it, not the executable.
function(_buildutil_loader_relative out hop)
  if(APPLE)
    set(token "@loader_path")
  else()
    set(token "$ORIGIN")
  endif()
  if(hop STREQUAL "" OR hop STREQUAL ".")
    set(${out} "${token}" PARENT_SCOPE)
  else()
    set(${out} "${token}/${hop}" PARENT_SCOPE)
  endif()
endfunction()

# A module that dlopens a sibling finds it the way a linked sibling is
# found: loader-relative at the install mirror, and at the sibling's own
# directory in the build tree. glibc searches the caller's DT_RUNPATH.
# Whatever runs the loader builds the loaded module first.
function(_buildutil_apply_runtime_loads)
  get_property(loaders GLOBAL PROPERTY _buildutil_runtime_loaders)
  list(REMOVE_DUPLICATES loaders)
  foreach(loader IN LISTS loaders)
    get_property(loaded GLOBAL PROPERTY _buildutil_loads_${loader})
    list(REMOVE_DUPLICATES loaded)
    get_target_property(loader_type ${loader} TYPE)
    if(NOT loader_type MATCHES "^(SHARED_LIBRARY|EXECUTABLE)$")
      message(FATAL_ERROR
        "${loader}: Link_dependencies(RUNTIME) on a module that builds as "
        "${loader_type}. The rpath that finds the loaded module belongs to "
        "the binary calling dlopen: an application, or a shared library "
        "(a main.<so|dll|dylib>.cpp or a .so tag).")
    endif()
    foreach(dep IN LISTS loaded)
      _buildutil_apply_runtime_load(${loader} ${dep})
    endforeach()
  endforeach()
endfunction()

function(_buildutil_apply_runtime_load loader dep)
  if(NOT TARGET ${dep})
    message(FATAL_ERROR
      "${loader}: Link_dependencies(RUNTIME) names ${dep}, which has no "
      "target in this build: the loader would find nothing to load.")
  endif()
  get_target_property(dep_type ${dep} TYPE)
  if(NOT dep_type STREQUAL "SHARED_LIBRARY")
    message(FATAL_ERROR
      "${loader}: Link_dependencies(RUNTIME) names ${dep}, which builds "
      "as ${dep_type}. Only a shared library can be loaded at run time.")
  endif()
  if(WIN32)
    message(WARNING
      "${loader}: Link_dependencies(RUNTIME ${dep}): Windows has no rpath; "
      "the DLL is found only when it sits beside the loading executable.")
  endif()
  get_target_property(loader_src ${loader} SOURCE_DIR)
  get_target_property(dep_src ${dep} SOURCE_DIR)
  _buildutil_mirror_parent_of("${loader_src}" loader_mirror)
  _buildutil_mirror_parent_of("${dep_src}" dep_mirror)
  file(RELATIVE_PATH hop "/buildutil-prefix/${loader_mirror}"
                         "/buildutil-prefix/${dep_mirror}")
  _buildutil_loader_relative(hop_rpath "${hop}")
  set_property(TARGET ${loader} APPEND PROPERTY INSTALL_RPATH "${hop_rpath}")
  get_target_property(loader_type ${loader} TYPE)
  if(loader_type STREQUAL "SHARED_LIBRARY")
    set_property(TARGET ${loader} APPEND PROPERTY BUILD_RPATH "$<TARGET_FILE_DIR:${dep}>")
  endif()
  foreach(runner IN ITEMS "${loader}" "${loader}-tests" "${loader}-benches")
    if(TARGET ${runner})
      get_target_property(runner_type ${runner} TYPE)
      if(runner_type STREQUAL "EXECUTABLE")
        add_dependencies(${runner} ${dep})
        set_property(TARGET ${runner} APPEND PROPERTY BUILD_RPATH "$<TARGET_FILE_DIR:${dep}>")
      endif()
    endif()
  endforeach()
endfunction()

# What an INSTALLED binary must find: its shared siblings at the install
# mirror, loader-relative, and the host libraries a SYSTEM Require found
# -- never a conan library, whose cache path here would let an
# artifact that ships no [runtime] payload start anyway. Deferred to the end
# of the tree so the full link graph exists; state rides target/global
# properties (deferred args expand in the deferred scope — locals are
# gone).
function(_buildutil_apply_install_rpaths)
  _buildutil_apply_runtime_loads()
  _buildutil_list_export_objects()
  get_property(apps GLOBAL PROPERTY _buildutil_apps)
  get_property(host_imports GLOBAL PROPERTY _buildutil_host_imports)
  foreach(app IN LISTS apps)
    if(NOT TARGET ${app})
      continue()
    endif()
    _buildutil_link_closure(${app} _closure)
    get_target_property(app_src ${app} SOURCE_DIR)
    _buildutil_mirror_parent_of("${app_src}" app_mirror)
    get_target_property(rpaths ${app} INSTALL_RPATH)
    if(NOT rpaths)
      set(rpaths "")
    endif()
    foreach(dep IN LISTS _closure)
      get_target_property(dep_type ${dep} TYPE)
      if(NOT dep_type STREQUAL "SHARED_LIBRARY")
        continue()
      endif()
      get_target_property(dep_imported ${dep} IMPORTED)
      if(dep_imported)
        if(dep IN_LIST host_imports)
          _buildutil_imported_directory(${dep} host_dir)
          if(host_dir)
            list(APPEND rpaths "${host_dir}")
          endif()
        endif()
        continue()
      endif()
      get_target_property(dep_src ${dep} SOURCE_DIR)
      _buildutil_mirror_parent_of("${dep_src}" dep_mirror)
      # both are prefix-relative; anchor them to compute the hop
      file(RELATIVE_PATH hop "/buildutil-prefix/${app_mirror}"
                             "/buildutil-prefix/${dep_mirror}")
      _buildutil_loader_relative(hop_rpath "${hop}")
      list(APPEND rpaths "${hop_rpath}")
    endforeach()
    if(rpaths)
      list(REMOVE_DUPLICATES rpaths)
      set_target_properties(${app} PROPERTIES INSTALL_RPATH "${rpaths}")
    endif()
  endforeach()
endfunction()

function(_buildutil_clean_path_parts dir out_list)
  file(RELATIVE_PATH relative "${CMAKE_SOURCE_DIR}/sources" "${dir}")
  string(REPLACE "/" ";" parts "${relative}")
  set(clean "")
  foreach(part IN LISTS parts)
    _buildutil_kind_of("${part}" part_name _ignored_kind)
    list(APPEND clean "${part_name}")
  endforeach()
  set(${out_list} "${clean}" PARENT_SCOPE)
endfunction()

# The module name for ANY directory: its path under sources/, kind tags
# stripped, joined with '-'. Two places need this -- the library pre-pass
# walks the tree before modules configure, and each module asks about
# itself -- and when they derived it separately, teaching only one about
# kind tags made the pre-pass register `wd.exe` while the module called
# itself `wd`, so the library fell back to the bare name and collided
# with its own executable. One function, both callers.
function(_buildutil_name_for_dir dir out)
  _buildutil_clean_path_parts("${dir}" clean)
  list(JOIN clean "-" name)
  set(${out} "${name}" PARENT_SCOPE)
endfunction()

# The module/target name is the path under sources/ with '-' joining the
# group components (sources/cpu/decoder -> cpu-decoder), so nested modules
# can never clash on a shared leaf name.
#
# This comment said '_' and had done since the machinery was extracted,
# while the line below has always written '-' -- it even contradicted the
# inline example beside it. Flagged by open-watcom-v21 reading the file
# rather than the code.
#
# The '_' that DOES appear in this file is a different namespace and is
# deliberate: a GROUP directory's key (@CMAKE_OPTION_PREFIX@_GROUP_*_<name>
# and its generated/<name> dir) joins with '_'. Those are internal keys,
# consistent with each other, and renaming them would move every existing
# project's generated paths for no gain. Do not "fix" one into the other.
function(_buildutil_module_name out)
  _buildutil_name_for_dir("${CMAKE_CURRENT_SOURCE_DIR}" name)
  set(${out} "${name}" PARENT_SCOPE)
endfunction()

# The name a module's APPLICATION is built and installed under: the leaf
# of its path under sources/ (sources/mstools/rc -> rc), not the joined
# target name. The cmake target keeps the joined form so targets stay
# unique tree-wide; the binary a user types is named after the directory
# that defines it. Grouping is therefore how a project gives two modules
# distinct target names without renaming the binary either one ships --
# a mstools/rc leaf and a top-level wrc coexist, and both install under
# the name they are meant to have.
function(_buildutil_module_app_name out)
  _buildutil_kind_of("${CMAKE_CURRENT_SOURCE_DIR}" leaf _ignored_kind)
  set(${out} "${leaf}" PARENT_SCOPE)
endfunction()

# The namespaced alias for a module: its sources/-relative path with `::`
# at every level (x86/traits -> x86::traits, a/b/c -> a::b::c). CMake puts
# no cap on the `::` depth. Empty for a top-level module, whose real name
# is already its own root (utilities -> utilities, no alias).
function(_buildutil_module_alias out)
  file(RELATIVE_PATH relative
    "${CMAKE_SOURCE_DIR}/sources" "${CMAKE_CURRENT_SOURCE_DIR}")
  if(relative MATCHES "/")
    string(REPLACE "/" "::" alias "${relative}")
    set(${out} "${alias}" PARENT_SCOPE)
  else()
    set(${out} "" PARENT_SCOPE)
  endif()
endfunction()

# A directory holding a CMakeLists.txt is a module; a directory without
# one is a group and is scanned recursively, so modules can be organized
# into nested folders. Dot-prefixed directories (.archive) never build.
function(_buildutil_scan root dormant)
  file(GLOB entries LIST_DIRECTORIES true CONFIGURE_DEPENDS "${root}/*")
  foreach(entry IN LISTS entries)
    if(NOT IS_DIRECTORY "${entry}")
      continue()
    endif()
    get_filename_component(sub "${entry}" NAME)
    if(sub MATCHES "^\\." OR sub IN_LIST dormant)
      continue()
    endif()
    # a platform-tagged directory -- module or group -- is not entered at
    # all on a host its tags exclude
    _buildutil_dir_platform_live("${sub}" _live)
    if(NOT _live)
      continue()
    endif()
    if(EXISTS "${entry}/CMakeLists.txt")
      file(RELATIVE_PATH relative "${CMAKE_CURRENT_SOURCE_DIR}" "${entry}")
      add_subdirectory(${relative})
    else()
      # near-miss module dirs must be LOUD: a dir full of sources whose
      # CMakeLists.txt is missing or misspelled otherwise just silently
      # never builds (live case: sources/main/CMakeList.txt -- no 's' --
      # and the whole project quietly built nothing).
      if(EXISTS "${entry}/CMakeList.txt")
        message(WARNING "${entry}/CMakeList.txt: misspelled -- the module "
                        "scanner needs CMakeLists.txt (trailing 's'); this "
                        "directory is NOT being built")
      else()
        file(GLOB _stray_cpp "${entry}/*.cpp")
        file(GLOB _sub_modules "${entry}/*/CMakeLists.txt")
        if(_stray_cpp AND NOT _sub_modules AND NOT EXISTS "${entry}/configure.py")
          # A POOL is not a near-miss module: a directory whose sources are
          # all compiled through sibling modules' symlinks is built, just
          # not by a CMakeLists of its own. Warning about it was false on
          # its face -- "it is NOT being built" while every file in it is
          # -- and fired on every configure once a project adopted the
          # layout. What IS worth saying is the opposite case: a file
          # sitting in a pool that no link reaches, which really is dead.
          _buildutil_all_link_targets(_linked)
          set(_unreached "")
          foreach(_f IN LISTS _stray_cpp)
            if(NOT _f IN_LIST _linked)
              list(APPEND _unreached "${_f}")
            endif()
          endforeach()
          if(NOT _unreached)
            # every source here is reached: a pool, and correct
          elseif(_unreached STREQUAL "${_stray_cpp}")
            message(WARNING "${entry} holds .cpp sources but no CMakeLists.txt "
                            "-- it is NOT being built (a module needs a "
                            "CMakeLists.txt calling Init_submodule())")
          else()
            string(REPLACE ";" "\n  " _pretty "${_unreached}")
            message(WARNING "${entry} is a source pool -- its files are "
                            "compiled through sibling modules' symlinks -- "
                            "but these are reached by no link, so nothing "
                            "builds them:\n  ${_pretty}")
          endif()
        endif()
      endif()
      if(EXISTS "${entry}/configure.py")
        _buildutil_run_group_configure("${entry}")
      endif()
      _buildutil_scan("${entry}" "${dormant}")
    endif()
  endforeach()
endfunction()

function(Scan_subdirectories)
  _buildutil_dormant_modules(dormant)
  # published for the checks that run at end of tree (a [resources]
  # declaration naming a module that never builds is an error, unless
  # the module is deliberately dormant)
  set_property(GLOBAL PROPERTY _buildutil_dormant_modules "${dormant}")
  cmake_language(DEFER DIRECTORY "${CMAKE_SOURCE_DIR}"
    CALL _buildutil_check_resource_claims)
  if(dormant)
    string(REPLACE ";" ", " _pretty "${dormant}")
    message(STATUS "dormant modules (project defaults + _bdudata/modules.ini): ${_pretty}")
  endif()
  # buildutil hands us @MODULE_DEFINE_PREFIX@_<NAME>_ENABLED=1 for every building module (the
  # dormant set already removed); define them here, before any module is
  # added, so every target inherits the whole set and can `#if` on a sibling.
  set(_defines ${@MODULE_DEFINE_PREFIX@_MODULE_DEFINES})
  foreach(_name IN LISTS dormant)
    string(TOUPPER "${_name}" _stem)
    string(REGEX REPLACE "[^0-9A-Z_]" "_" _stem "${_stem}")
    list(REMOVE_ITEM _defines "@MODULE_DEFINE_PREFIX@_${_stem}_ENABLED=1")
  endforeach()
  add_compile_definitions(${_defines})
  # Extension hook: once, before any module is added, for an extension that
  # needs to survey the whole tree first (reflect greps it for tagged types).
  if(COMMAND _buildutil_ext_pre_scan)
    _buildutil_ext_pre_scan()
  endif()
  _buildutil_register_libraries("${CMAKE_CURRENT_SOURCE_DIR}")
  _buildutil_scan("${CMAKE_CURRENT_SOURCE_DIR}" "${dormant}")
endfunction()

# Which target name carries a module's LIBRARY.
#
# A module with an entry point owns two targets, and cmake target names
# are one namespace, so they cannot both be `wlink`. The executable takes
# the bare module name -- that is the name a person types, and the one
# every consumer spells -- and the library is the one that gets decorated,
# `wlink-lib`. A module with no entry point is untouched: one target,
# bare name, exactly as before.
#
# This has to be a PRE-PASS over the whole tree, not a registry filled in
# as modules configure. `Link_dependencies(zeta)` runs while alpha is
# being added, and the scan is alphabetical, so zeta's own CMakeLists may
# not have run yet -- a registry would answer "no library called zeta-lib"
# and silently link the bare name, which after this change is zeta's
# EXECUTABLE. Deriving it from the directory instead makes the answer
# independent of configure order. It cannot be derived from the module
# NAME either: `-` is both the path joiner and a legal directory
# character (sources/cc-i86 is one top-level module, not cc/i86), so the
# mapping only goes one way and the walk is what disambiguates it.
function(_buildutil_register_libraries root)
  file(GLOB entries LIST_DIRECTORIES true CONFIGURE_DEPENDS "${root}/*")
  foreach(entry IN LISTS entries)
    if(NOT IS_DIRECTORY "${entry}")
      continue()
    endif()
    get_filename_component(sub "${entry}" NAME)
    if(sub MATCHES "^\\.")
      continue()
    endif()
    _buildutil_dir_platform_live("${sub}" _live)
    if(NOT _live AND EXISTS "${entry}/CMakeLists.txt")
      _buildutil_name_for_dir("${entry}" name)
      _buildutil_kind_of("${entry}" _rl_name _rl_kind)
      set_property(GLOBAL PROPERTY _buildutil_module_elsewhere_${name} TRUE)
      set_property(GLOBAL PROPERTY _buildutil_module_elsewhere_${_rl_name} TRUE)
    endif()
    if(NOT _live)
      continue()
    endif()
    if(EXISTS "${entry}/CMakeLists.txt")
      _buildutil_name_for_dir("${entry}" name)
      file(GLOB_RECURSE entry_points CONFIGURE_DEPENDS "${entry}/main.cpp")
      # an .exe TAG makes a module an app even without a main.cpp (its
      # entry lives in the link closure), so the pre-pass must decorate
      # its library too -- otherwise the library takes the bare name and
      # collides with the executable Init_submodule then creates
      _buildutil_kind_of("${entry}" _rl_name _rl_kind)
      if(entry_points OR _rl_kind STREQUAL "exe")
        set_property(GLOBAL PROPERTY _buildutil_lib_${name} "${name}-lib")
      endif()
      if(_rl_kind STREQUAL "test")
        set_property(GLOBAL PROPERTY _buildutil_test_module_${name} TRUE)
      endif()
      # LEAF ALIASES. A module under a group carries the path-joined target
      # name (sources/oxbox/utilities -> oxbox-utilities), but a dependency
      # inside the same package reads better spelled the way the source tree
      # does: Link_dependencies(utilities). Record leaf -> module so
      # _buildutil_library_of can resolve it. The full name always still
      # works, and is the ONLY spelling once a leaf is ambiguous.
      set_property(GLOBAL PROPERTY _buildutil_module_known_${name} TRUE)
      if(NOT _rl_name STREQUAL name)
        get_property(_rl_prev GLOBAL PROPERTY _buildutil_leaf_${_rl_name})
        if(_rl_prev AND NOT _rl_prev STREQUAL name)
          # two modules share a leaf: the short spelling would be a coin
          # flip, so retire it and say which full names to use instead
          set_property(GLOBAL PROPERTY _buildutil_leaf_ambiguous_${_rl_name}
            "${_rl_prev}, ${name}")
        else()
          set_property(GLOBAL PROPERTY _buildutil_leaf_${_rl_name} "${name}")
        endif()
      endif()
    else()
      _buildutil_refuse_test_group("${entry}")
      _buildutil_register_libraries("${entry}")
    endif()
  endforeach()
endfunction()

# `.test` names a module; a group carrying it would also read as test sources.
function(_buildutil_refuse_test_group dir)
  _buildutil_kind_of("${dir}" _tg_name _tg_kind)
  if(_tg_kind STREQUAL "test")
    message(FATAL_ERROR
      "${dir} is tagged .test but holds no CMakeLists.txt, so it is a group, "
      "and .test names a module. Give it a CMakeLists.txt calling "
      "Init_submodule() to make it a test-lane module, or drop the tag.")
  endif()
endfunction()

# The MODULE a dependency name refers to, or empty for a non-module
# (a conan target, a host library). Same leaf-alias resolution as
# _buildutil_library_of, exposed separately so the component manifest can
# tell an in-package edge from an external one.
function(_buildutil_module_of name out)
  get_property(_mo_known GLOBAL PROPERTY _buildutil_module_known_${name})
  if(_mo_known)
    set(${out} "${name}" PARENT_SCOPE)
    return()
  endif()
  get_property(_mo_full GLOBAL PROPERTY _buildutil_leaf_${name})
  set(${out} "${_mo_full}" PARENT_SCOPE)
endfunction()

function(_buildutil_library_of module out)
  # A name that is not itself a module may be a module's LEAF (see the
  # pre-pass): resolve it, so an in-package dependency can be spelled
  # `utilities` rather than `oxbox-utilities`. Non-module names (conan
  # targets like yaml-cpp::yaml-cpp) match nothing and pass through.
  get_property(_lo_known GLOBAL PROPERTY _buildutil_module_known_${module})
  if(NOT _lo_known)
    get_property(_lo_ambig GLOBAL PROPERTY
      _buildutil_leaf_ambiguous_${module})
    if(_lo_ambig)
      message(FATAL_ERROR
        "dependency '${module}' is ambiguous -- ${_lo_ambig} both end in "
        "that name. Spell the full module name of the one you mean.")
    endif()
    get_property(_lo_full GLOBAL PROPERTY _buildutil_leaf_${module})
    if(_lo_full)
      set(module "${_lo_full}")
    endif()
  endif()
  get_property(lib GLOBAL PROPERTY _buildutil_lib_${module})
  if(NOT lib)
    set(lib "${module}")     # no entry point: the library IS the bare name
  endif()
  set(${out} "${lib}" PARENT_SCOPE)
endfunction()

# Classify .cpp files into sources / tests / benches.
#   Tests   — *.test.cpp, or any .cpp under a `*.test/` directory
#             (link GTest::gtest_main, run under ctest).
#   Benches — *.bench.cpp, or any .cpp under a `*.bench/` directory
#             (link benchmark::benchmark_main, run by buildutil bench).
# The directory rule lets a whole subtree (e.g. decoder.test/) be
# tests without every file needing the .test.cpp suffix. One rule for
# static and generated sources alike -- the path decides.
#
# Platform suffixes pick per-OS translation units at scan time, no
# #ifdef wrappers: *.win32.cpp builds on Windows, *.linux.cpp on Linux,
# *.macos.cpp on macOS, *.posix.cpp on both Linux and macOS. Any other
# platform suffix is dropped on this host. Composes with the class
# suffixes (foo.win32.test.cpp is a Windows-only test).
#
# The SAME tags work on a directory, exactly as *.test/ does: everything
# under host.linux/ builds on Linux only, nt.win32/ on Windows only. A
# ported tree that already keeps whole per-host source directories (the
# old linux/ nt/ split) says so by renaming the directory, instead of
# suffixing every file inside it or flattening the split. Tags nest and
# all of them must hold -- a win32 subtree inside a linux one builds
# nowhere, which is what it literally asks for.
# The TARGET system, folded to the systems the tag table knows. Anything
# else is a generic unix and reads as Linux -- which is what the old
# hardcoded if/elseif did, said out loud.
function(_buildutil_target_system out)
  if(DEFINED _buildutil_platform_live_${CMAKE_SYSTEM_NAME})
    set(${out} "${CMAKE_SYSTEM_NAME}" PARENT_SCOPE)
  else()
    set(${out} "Linux" PARENT_SCOPE)
  endif()
endfunction()

# The live tags for the TARGET platform, LEAST SPECIFIC FIRST so a
# caller merging tagged things can just apply them in order. Any system
# the table does not name reads as a generic unix.
function(_buildutil_platform_suffixes out_suffixes)
  if(DEFINED _buildutil_platform_live_${CMAKE_SYSTEM_NAME})
    set(${out_suffixes} "${_buildutil_platform_live_${CMAKE_SYSTEM_NAME}}"
        PARENT_SCOPE)
  else()
    set(${out_suffixes} "${_buildutil_platform_live_default}" PARENT_SCOPE)
  endif()
endfunction()

# Whether a directory NAME's platform tags are all live on this host.
# Used by the module scan (a tagged module directory is never entered)
# and by the library pre-pass (which must agree with it, or a skipped
# module would still have had its library name reserved).
function(_buildutil_dir_platform_live leaf out)
  _buildutil_platform_suffixes(live_suffixes)
  set(result TRUE)
  string(REGEX MATCHALL "\\.(@PLATFORM_TAGS@)(\\.|$)" tags "${leaf}")
  foreach(tag IN LISTS tags)
    string(REGEX REPLACE "^\\.([a-z0-9]+).*$" "\\1" tag "${tag}")
    list(FIND live_suffixes "${tag}" _alive)
    if(_alive EQUAL -1)
      set(result FALSE)
    endif()
  endforeach()
  set(${out} "${result}" PARENT_SCOPE)
endfunction()

# ===========================================================================
# PLATFORM-TAGGED DATA DIRECTORIES -- ui.embed.linux/, locale.install.win32/
# ===========================================================================
#
# The same vocabulary that tags a source and a module directory tags a
# DATA directory. `ui.embed/` is the base set for every platform;
# `ui.embed.posix/` overlays it on Linux and macOS, `ui.embed.linux/`
# overlays that on Linux. Selection is by the TARGET platform, cross
# builds included -- never the host.
#
# What comes back is every APPLICABLE directory under `root` carrying
# `.<suffix>`, in APPLY ORDER, with a parallel list of specificity
# LEVELS: 0 for the untagged base, 1 for a family tag (posix, apple), 2
# for an exact platform. _buildutil_platform_suffixes already returns the
# live tags least-specific-first, which is what makes that order fall out
# of one loop rather than a sort.
#
# A directory tagged for another platform is not merely skipped, it is
# never GLOBBED. These globs are CONFIGURE_DEPENDS, and a glob result is
# a re-configure trigger: editing a Windows asset on Linux must not
# re-run cmake, so the win32 directory must not appear in any glob this
# build performs.
function(_buildutil_platform_data_dirs root suffix out_dirs out_levels)
  set(_pd_dirs "")
  set(_pd_levels "")
  file(GLOB _pd_base LIST_DIRECTORIES true CONFIGURE_DEPENDS
       "${root}/*.${suffix}")
  foreach(_pd_d IN LISTS _pd_base)
    if(NOT IS_DIRECTORY "${_pd_d}")
      continue()
    endif()
    # `ui.linux.embed/` is the tag on the wrong side of the suffix: the
    # base glob would take it as a set named `ui.linux` and ship it
    # EVERYWHERE, which is the silent wrong answer. The suffix says what
    # kind of directory it is; the tag qualifies it, and comes last.
    get_filename_component(_pd_leaf "${_pd_d}" NAME)
    if(_pd_leaf MATCHES "\\.(@PLATFORM_TAGS@)\\.${suffix}$")
      string(REGEX REPLACE "\\.(@PLATFORM_TAGS@)\\.${suffix}$" "" _pd_stem "${_pd_leaf}")
      string(REGEX MATCH "(@PLATFORM_TAGS@)\\.${suffix}$" _pd_m "${_pd_leaf}")
      string(REGEX REPLACE "\\.${suffix}$" "" _pd_tag "${_pd_m}")
      message(FATAL_ERROR
        "${_pd_leaf}: a platform tag goes AFTER the directory suffix, not "
        "before it -- rename it to ${_pd_stem}.${suffix}.${_pd_tag}. Spelled "
        "this way the directory is an untagged base set named "
        "'${_pd_stem}.${_pd_tag}' and ships on every platform, which is "
        "never what the name is trying to say.")
    endif()
    list(APPEND _pd_dirs "${_pd_d}")
    list(APPEND _pd_levels 0)
  endforeach()
  _buildutil_platform_suffixes(_pd_live)
  foreach(_pd_tag IN LISTS _pd_live)
    file(GLOB _pd_hits LIST_DIRECTORIES true CONFIGURE_DEPENDS
         "${root}/*.${suffix}.${_pd_tag}")
    foreach(_pd_d IN LISTS _pd_hits)
      if(IS_DIRECTORY "${_pd_d}")
        list(APPEND _pd_dirs "${_pd_d}")
        list(APPEND _pd_levels "${_buildutil_platform_level_${_pd_tag}}")
      endif()
    endforeach()
  endforeach()
  set(${out_dirs} "${_pd_dirs}" PARENT_SCOPE)
  set(${out_levels} "${_pd_levels}" PARENT_SCOPE)
endfunction()

# The dirs/levels pair from above, appended to the two parallel lists
# _buildutil_add_embedded_resources carries an entry in: the encoded
# "<dir>|<globs>|<prefix>" tuple and its overlay level.
function(_buildutil_append_data_entries dirs levels io_entries io_levels)
  set(_ad_entries "${${io_entries}}")
  set(_ad_levels "${${io_levels}}")
  list(LENGTH dirs _ad_count)
  if(_ad_count GREATER 0)
    math(EXPR _ad_last "${_ad_count} - 1")
    foreach(_ad_i RANGE ${_ad_last})
      list(GET dirs ${_ad_i} _ad_dir)
      list(GET levels ${_ad_i} _ad_level)
      list(APPEND _ad_entries "${_ad_dir}|*|")
      list(APPEND _ad_levels "${_ad_level}")
    endforeach()
  endif()
  set(${io_entries} "${_ad_entries}" PARENT_SCOPE)
  set(${io_levels} "${_ad_levels}" PARENT_SCOPE)
endfunction()

# ONE merge step, over four parallel lists keyed by `rel` -- the resource
# name for *.embed/, the shipped path for *.install/. A more specific
# directory REPLACES what a less specific one put there; a less specific
# one is dropped; EQUAL specificity is a configure error naming both
# directories. Nothing orders `ui.embed.posix/` against `ui.embed.apple/`
# on macOS, and inventing a rule would be worse than refusing.
#
# `what` is the caller's context, said in full, because the reader of
# this message is looking at a tree and not at this file.
function(_buildutil_overlay_one what rel path owner level
         io_names io_files io_owners io_levels)
  set(_ov_names "${${io_names}}")
  set(_ov_files "${${io_files}}")
  set(_ov_owners "${${io_owners}}")
  set(_ov_levels "${${io_levels}}")
  list(FIND _ov_names "${rel}" _ov_at)
  if(_ov_at EQUAL -1)
    list(APPEND _ov_names "${rel}")
    list(APPEND _ov_files "${path}")
    list(APPEND _ov_owners "${owner}")
    list(APPEND _ov_levels "${level}")
  else()
    list(GET _ov_levels ${_ov_at} _ov_prev_level)
    list(GET _ov_owners ${_ov_at} _ov_prev_owner)
    list(GET _ov_files ${_ov_at} _ov_prev_file)
    if(_ov_prev_file STREQUAL "${path}")
      # The SAME file, reached twice -- two [[resources]] sets naming one
      # directory, or a set that overlaps a `*.embed/` tree. One file
      # cannot conflict with itself, and the refusal below is about two
      # DIFFERENT files claiming one name.
      set(_ov_names "${_ov_names}")
    elseif(level GREATER _ov_prev_level)
      list(REMOVE_AT _ov_files ${_ov_at})
      list(INSERT _ov_files ${_ov_at} "${path}")
      list(REMOVE_AT _ov_owners ${_ov_at})
      list(INSERT _ov_owners ${_ov_at} "${owner}")
      list(REMOVE_AT _ov_levels ${_ov_at})
      list(INSERT _ov_levels ${_ov_at} "${level}")
    elseif(level EQUAL _ov_prev_level)
      message(FATAL_ERROR
        "${what}: '${rel}' is claimed by two directories of the same "
        "platform specificity:\n  ${_ov_prev_owner}\n  ${owner}\n"
        "Nothing orders those two, so buildutil will not pick one. Tag "
        "one of them more exactly (<name>.<suffix>.<platform> overlays "
        "<name>.<suffix>.<family> overlays <name>.<suffix>), rename the "
        "file, or give one set its own `prefix`.")
    endif()
  endif()
  set(${io_names} "${_ov_names}" PARENT_SCOPE)
  set(${io_files} "${_ov_files}" PARENT_SCOPE)
  set(${io_owners} "${_ov_owners}" PARENT_SCOPE)
  set(${io_levels} "${_ov_levels}" PARENT_SCOPE)
endfunction()

function(_buildutil_split_source_list files out_sources out_tests out_benches)
  _buildutil_platform_suffixes(live_suffixes)
  _buildutil_target_system(target_system)
  _buildutil_kind_of("${CMAKE_CURRENT_SOURCE_DIR}" _sl_name _sl_kind)
  set(sources "")
  set(tests "")
  set(benches "")
  foreach(f IN LISTS files)
    # Classify on the PROJECT-relative path. These rules read DIRECTORY
    # names, and an absolute path drags in whatever the checkout happens
    # to sit under: a clone in ~/scratch.test/ classified every source in
    # the project as a test, and with the platform tags below it would
    # drop them from the build outright.
    set(rel "${f}")
    # a .test module's own directory names its kind, not its sources' lane
    if(_sl_kind STREQUAL "test")
      string(REPLACE "${CMAKE_CURRENT_SOURCE_DIR}/" "" rel "${rel}")
    endif()
    string(REPLACE "${CMAKE_BINARY_DIR}/" "" rel "${rel}")
    string(REPLACE "${CMAKE_SOURCE_DIR}/" "" rel "${rel}")
    # A module's *.install/ trees are DATA that ship beside the binaries,
    # and its *.embed/ trees are DATA that ship INSIDE them -- neither is
    # module source. The source glob is recursive, so without this a .cpp
    # sitting in there would be compiled into the module -- silently, and
    # only for whoever happened to put one there.
    if(rel MATCHES "(^|/)[^/]*\\.(install|embed)(\\.(@PLATFORM_TAGS@))?/")
      continue()
    endif()
    # directory platform tags -- every tagged component must be live
    set(dead OFF)
    string(REGEX MATCHALL "[^/]*\\.(@PLATFORM_TAGS@)/" dir_tags "${rel}")
    foreach(dir_tag IN LISTS dir_tags)
      string(REGEX REPLACE "^.*\\.([a-z0-9]+)/$" "\\1" dir_tag "${dir_tag}")
      list(FIND live_suffixes "${dir_tag}" _alive)
      if(_alive EQUAL -1)
        set(dead ON)
      endif()
    endforeach()
    if(dead)
      continue()
    endif()
    # WHICH PLATFORMS THIS FILE IS FOR = the EXTENSION's default, narrowed
    # by an explicit tag. An extension already says a great deal: a .mm is
    # Objective-C++ and therefore an Apple source by being one, a .rc is a
    # Windows resource script. Tagging those would repeat the extension.
    # A tag NARROWS (foo.macos.mm is legal and means the same thing); a tag
    # whose systems cannot intersect the extension's is a file claiming two
    # incompatible things, and is refused by name.
    #
    # Only the LAST dot-segment before the extension is read as a tag, and
    # only if the table holds it -- so foo.test.cpp and foo.v2.cpp are
    # untouched, and every spelling that matched before still matches.
    string(REGEX MATCH "\\.([A-Za-z0-9]+)$" _ext_matched "${rel}")
    set(_src_ext "${CMAKE_MATCH_1}")
    set(_src_systems "${_buildutil_ext_systems_${_src_ext}}")
    if(rel MATCHES "\\.(@PLATFORM_TAGS@)\\.(test\\.|bench\\.)?[A-Za-z0-9]+$")
      set(_src_tag "${CMAKE_MATCH_1}")
      set(_tag_systems "${_buildutil_tag_systems_${_src_tag}}")
      if(NOT _src_systems)
        set(_src_systems "${_tag_systems}")
      else()
        set(_narrowed "")
        foreach(_sys IN LISTS _src_systems)
          if(_sys IN_LIST _tag_systems)
            list(APPEND _narrowed "${_sys}")
          endif()
        endforeach()
        if(NOT _narrowed)
          message(FATAL_ERROR
            "${f}: the '.${_src_ext}' extension means this file is for "
            "${_src_systems}, and the '.${_src_tag}' tag says "
            "${_tag_systems}. They do not overlap, so the file is for no "
            "platform at all -- and would simply never build, which is the "
            "silence this refusal exists to break. Drop the tag, or rename "
            "the file.")
        endif()
        set(_src_systems "${_narrowed}")
      endif()
    endif()
    if(_src_systems AND NOT target_system IN_LIST _src_systems)
      continue()
    endif()
    if(rel MATCHES "\\.test\\.[A-Za-z0-9]+$" OR rel MATCHES "(^|/)[^/]*\\.test/")
      list(APPEND tests ${f})
    elseif(rel MATCHES "\\.bench\\.[A-Za-z0-9]+$" OR rel MATCHES "(^|/)[^/]*\\.bench/")
      list(APPEND benches ${f})
    else()
      list(APPEND sources ${f})
    endif()
  endforeach()
  set(${out_sources} ${sources} PARENT_SCOPE)
  set(${out_tests}   ${tests}   PARENT_SCOPE)
  set(${out_benches} ${benches} PARENT_SCOPE)
endfunction()

# Symlinks let a project express variant modules by LAYOUT: several leaf
# modules sharing one common/ pool, each leaf holding links to the sources
# it wants plus its own. `ls` in the leaf is exactly what it compiles and
# the CMakeLists stays a bare Init_submodule().
#
# On POSIX that works today with nothing here -- the compiler is handed
# the leaf path and follows it. The gap is a DEFAULT WINDOWS checkout
# (core.symlinks=false), where git writes each link as a small text file
# holding its target path, which the scanner would hand to the compiler
# as a bogus source.
#
# git's index is the authority, not the content: mode 120000 IS the
# symlink list, so there are no heuristics and no false positive on an
# ordinary source that happens to hold one line. One batched `ls-files`
# per module, cached, because this runs for every module in the tree.
function(_buildutil_git_links dir out)
  get_property(cached GLOBAL PROPERTY _buildutil_links_${dir} SET)
  if(cached)
    get_property(links GLOBAL PROPERTY _buildutil_links_${dir})
    set(${out} "${links}" PARENT_SCOPE)
    return()
  endif()
  set(links "")
  execute_process(
    COMMAND git ls-files -s -- "${dir}"
    WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}"
    OUTPUT_VARIABLE listing RESULT_VARIABLE failed
    ERROR_QUIET OUTPUT_STRIP_TRAILING_WHITESPACE)
  # not a git tree (a tarball export) -- symlinks are already real files
  # there, so there is nothing to resolve and nothing to warn about
  if(NOT failed)
    string(REPLACE "\n" ";" lines "${listing}")
    foreach(line IN LISTS lines)
      if(line MATCHES "^120000 [0-9a-f]+ [0-9]+\t(.+)$")
        list(APPEND links "${CMAKE_SOURCE_DIR}/${CMAKE_MATCH_1}")
      endif()
    endforeach()
  endif()
  set_property(GLOBAL PROPERTY _buildutil_links_${dir} "${links}")
  set(${out} "${links}" PARENT_SCOPE)
endfunction()

# Map a classified list onto what the compiler should actually be given.
# Classification has already happened against the LINK's own name, which
# is what keeps .test/.bench/platform tags and main.cpp keying off the
# leaf exactly as they do on POSIX.
function(_buildutil_resolve_links links files out)
  set(resolved "")
  foreach(f IN LISTS files)
    if(NOT f IN_LIST links OR IS_SYMLINK "${f}")
      list(APPEND resolved "${f}")     # POSIX: the real link, untouched
      continue()
    endif()
    file(READ "${f}" target)
    string(STRIP "${target}" target)
    get_filename_component(link_dir "${f}" DIRECTORY)
    get_filename_component(abs "${link_dir}/${target}" ABSOLUTE)
    if(target STREQUAL "" OR NOT EXISTS "${abs}")
      # what a Windows contributor editing "through" a fake symlink
      # leaves behind. Say so here; as a source it is a parse error
      # pages away from the cause.
      message(FATAL_ERROR
        "${f} is a symlink in git (mode 120000) but the checkout holds a "
        "plain file whose contents are not a path to an existing file "
        "(read: '${target}'). Either the link is corrupt -- someone edited "
        "the file rather than its target -- or the checkout is broken. On "
        "Windows, `git config core.symlinks true` in a Developer Mode "
        "shell checks these out as real links.")
    endif()
    list(APPEND resolved "${abs}")
  endforeach()
  set(${out} "${resolved}" PARENT_SCOPE)
endfunction()

# The header half of the symlink-variant layout. Sources are carried into
# a leaf by links; headers cannot be (a symlinked header is refused --
# nobody chooses what #include opens), so the directory a borrowed source
# CAME FROM has to be reachable, and the link already says which one that
# is. Every project expressing variants by layout needs exactly this, and
# the scanner already holds the link list, so it does not belong in a
# project helper.
#
# APPENDED, never prepended: the leaf's own directory must still win for
# a same-named header. Same reasoning as the include-order note on the
# symlink work, and 0.22.0's collision check guards the other side of it.
function(_buildutil_add_link_pools target scope)
  _buildutil_git_links("${CMAKE_CURRENT_SOURCE_DIR}" _lp_links)
  if(NOT _lp_links)
    return()
  endif()
  _buildutil_link_pools("${_lp_links}" _lp_pools)
  foreach(pool IN LISTS _lp_pools)
    target_include_directories(${target} ${scope} "${pool}")
  endforeach()
endfunction()

function(_buildutil_split_sources out_sources out_tests out_benches)
  _buildutil_target_system(target_system)
  # Every extension the table calls a module source, minus the ones that
  # cannot exist on this target. A .mm is globbed on an Apple target and
  # nowhere else -- not filtered out later, NOT GLOBBED: a
  # CONFIGURE_DEPENDS glob is a re-configure trigger, and editing an
  # Objective-C++ file on Linux must not re-run cmake.
  set(_glob_live "")
  set(_glob_dead "")
  foreach(_ext IN LISTS _buildutil_source_extensions)
    set(_ext_systems "${_buildutil_ext_systems_${_ext}}")
    if(_ext_systems AND NOT target_system IN_LIST _ext_systems)
      list(APPEND _glob_dead "${CMAKE_CURRENT_SOURCE_DIR}/*.${_ext}")
    else()
      list(APPEND _glob_live "${CMAKE_CURRENT_SOURCE_DIR}/*.${_ext}")
    endif()
  endforeach()
  file(GLOB_RECURSE all_cpp CONFIGURE_DEPENDS ${_glob_live})
  # *.s and *.S are one glob on a case-insensitive filesystem
  if(all_cpp)
    list(REMOVE_DUPLICATES all_cpp)
  endif()
  # The files this target CANNOT build still get their tags checked, so a
  # foo.linux.mm is refused on every platform rather than only on the one
  # that would have compiled it. Plain glob, deliberately: no
  # CONFIGURE_DEPENDS, so they add no re-configure trigger here.
  file(GLOB_RECURSE _dead_sources ${_glob_dead})
  if(_dead_sources)
    _buildutil_split_source_list("${_dead_sources}" _d1 _d2 _d3)
  endif()
  _buildutil_split_source_list("${all_cpp}" sources tests benches)
  _buildutil_git_links("${CMAKE_CURRENT_SOURCE_DIR}" links)
  # git's INDEX is the authority for what is a link, which is what makes
  # the Windows fallback possible at all -- but it means a symlink created
  # and not yet staged is invisible here, and the module simply behaves as
  # if it were not there. Say so; the alternative is a file that plainly
  # IS a symlink being silently ignored.
  foreach(_f IN LISTS all_cpp)
    if(IS_SYMLINK "${_f}" AND NOT _f IN_LIST links)
      message(WARNING
        "${_f} is a symlink on disk but is not in git's index, and the "
        "index is what buildutil reads to resolve links (mode 120000) -- "
        "so this file is being ignored. `git add` it.")
    endif()
  endforeach()
  if(links)
    _buildutil_check_header_links("${links}")
    _buildutil_resolve_links("${links}" "${sources}" sources)
    _buildutil_resolve_links("${links}" "${tests}"   tests)
    _buildutil_resolve_links("${links}" "${benches}" benches)
    _buildutil_link_pools("${links}" pools)
    _buildutil_check_pool_shadowing(
      "${CMAKE_CURRENT_SOURCE_DIR}" "${pools}" "${links}")
  endif()
  set(${out_sources} ${sources} PARENT_SCOPE)
  set(${out_tests}   ${tests}   PARENT_SCOPE)
  set(${out_benches} ${benches} PARENT_SCOPE)
endfunction()

# The one case where the two checkouts genuinely disagree, made
# impossible rather than documented.
#
# A quoted #include resolves in the INCLUDING FILE'S OWN directory before
# any -I, and there is no portable way to turn that off. A source that
# lives in the pool and is compiled through a leaf link is opened as
# leaf/foo.cpp on POSIX and as pool/foo.cpp on a fake-symlink checkout,
# so its #include "bar.h" finds the LEAF's bar.h on one and the POOL's on
# the other. Same tree, same command, different artifact -- and it
# compiles and links either way, so nothing tells you.
#
# The condition needs both a leaf header and a pool header of the same
# name, which no layout requires: per-variant headers belong in the leaf
# only, and genuinely shared ones in the pool only. So refuse the overlap
# and the divergence cannot arise -- for every consumer, not only the
# disciplined ones. Suggested by open-watcom-v21, who had already made
# their own tree safe by hand and pointed out that the next project
# would not think to.
#
# The pools have to be read off the LINK TARGETS, not off the compiled
# paths. Where the links are real, resolution deliberately hands the
# compiler the link itself, so every compiled path sits in the module dir
# and a check looking there would find no pool at all -- silently passing
# on exactly the POSIX checkout whose developer is the one about to
# commit the overlap.
# Where one link points, whether it is a real symlink or the text file a
# default Windows checkout leaves. Factored out because three callers now
# need it and a third copy is how two parsers of it came to disagree.
function(_buildutil_link_target f out)
  set(${out} "" PARENT_SCOPE)
  if(NOT EXISTS "${f}")
    return()
  endif()
  if(IS_SYMLINK "${f}")
    file(READ_SYMLINK "${f}" target)
  else()
    file(READ "${f}" target)
    string(STRIP "${target}" target)
  endif()
  if(target STREQUAL "")
    return()
  endif()
  if(IS_ABSOLUTE "${target}")
    set(${out} "${target}" PARENT_SCOPE)
  else()
    get_filename_component(link_dir "${f}" DIRECTORY)
    get_filename_component(abs "${link_dir}/${target}" ABSOLUTE)
    set(${out} "${abs}" PARENT_SCOPE)
  endif()
endfunction()

# Every file under sources/ that some symlink points AT, computed once.
# This is what makes a "pool" recognizable: a directory of sources with no
# CMakeLists whose files are all compiled through sibling modules' links.
function(_buildutil_all_link_targets out)
  get_property(cached GLOBAL PROPERTY _buildutil_link_targets SET)
  if(cached)
    get_property(targets GLOBAL PROPERTY _buildutil_link_targets)
    set(${out} "${targets}" PARENT_SCOPE)
    return()
  endif()
  _buildutil_git_links("${CMAKE_SOURCE_DIR}/sources" links)
  set(targets "")
  foreach(f IN LISTS links)
    _buildutil_link_target("${f}" abs)
    if(abs)
      list(APPEND targets "${abs}")
    endif()
  endforeach()
  set_property(GLOBAL PROPERTY _buildutil_link_targets "${targets}")
  set(${out} "${targets}" PARENT_SCOPE)
endfunction()

function(_buildutil_link_pools links out)
  set(pools "")
  foreach(f IN LISTS links)
    _buildutil_link_target("${f}" abs)
    if(NOT abs)
      continue()
    endif()
    get_filename_component(link_dir "${f}" DIRECTORY)
    get_filename_component(dir "${abs}" DIRECTORY)
    if(NOT dir STREQUAL link_dir)
      list(APPEND pools "${dir}")
    endif()
  endforeach()
  if(pools)
    list(REMOVE_DUPLICATES pools)
  endif()
  set(${out} "${pools}" PARENT_SCOPE)
endfunction()

function(_buildutil_check_pool_shadowing module_dir pools links)
  if(NOT pools)
    return()
  endif()
  file(GLOB own CONFIGURE_DEPENDS
    "${module_dir}/*.h" "${module_dir}/*.hpp" "${module_dir}/*.hxx")
  set(own_names "")
  foreach(header IN LISTS own)
    get_filename_component(name "${header}" NAME)
    list(APPEND own_names "${name}")
  endforeach()
  if(NOT own_names)
    return()
  endif()
  foreach(pool IN LISTS pools)
    file(GLOB shared CONFIGURE_DEPENDS
      "${pool}/*.h" "${pool}/*.hpp" "${pool}/*.hxx")
    foreach(header IN LISTS shared)
      get_filename_component(name "${header}" NAME)
      if(name IN_LIST own_names)
        # Name the borrowed sources that reach this pool. Without them the
        # reader knows a hazard exists but not which file could see it,
        # and has to run the compiler with -H to find out -- which is
        # exactly what the consumer had to do.
        set(_reaching "")
        foreach(_l IN LISTS links)
          _buildutil_link_target("${_l}" _lt)
          if(_lt)
            get_filename_component(_ltd "${_lt}" DIRECTORY)
            if(_ltd STREQUAL pool)
              get_filename_component(_ln "${_l}" NAME)
              list(APPEND _reaching "${_ln}")
            endif()
          endif()
        endforeach()
        if(_reaching)
          string(REPLACE ";" ", " _reaching "${_reaching}")
          set(_via "The sources that reach it from there: ${_reaching}.")
        else()
          set(_via "")
        endif()
        message(FATAL_ERROR
          "${name} exists in both ${module_dir} and ${pool}, and this "
          "module compiles sources out of that pool through symlinks. A "
          "quoted #include of it resolves to the LEAF copy where the "
          "links are real and to the POOL copy where they were checked "
          "out as text -- the same tree building a different artifact per "
          "platform, with no diagnostic either way. ${_via} Rename one, or "
          "keep per-variant headers in the leaf and shared ones in the pool.")
      endif()
    endforeach()
  endforeach()
endfunction()

# A symlinked HEADER cannot be rescued the way a source can. The scanner
# chooses what to hand the compiler for a source; nobody chooses for a
# header -- the preprocessor opens whatever `#include "foo.h"` names, and
# on a fake-symlink checkout that is a one-line text file. It fails as a
# syntax error inside a file the author never wrote, so catch it at
# configure and say what to do instead: symlink SOURCES, and reach shared
# headers through the include roots.
function(_buildutil_check_header_links links)
  foreach(f IN LISTS links)
    if(f MATCHES "\\.(h|hpp|hxx|inc|gh)$" AND NOT IS_SYMLINK "${f}")
      message(FATAL_ERROR
        "${f} is a symlinked HEADER checked out as a plain file. Unlike a "
        "source, this cannot be resolved for you -- the preprocessor opens "
        "the path the #include names, and would read the link's text as "
        "C++. Symlink sources only and reach shared headers through the "
        "module's include roots, or check the tree out with real symlinks "
        "(`git config core.symlinks true`, Developer Mode on Windows).")
    endif()
  endforeach()
endfunction()

# The test binary links the module library; a python (MODULE_LIBRARY)
# target cannot be linked, so its tests build standalone.
function(_buildutil_add_test_target target)
  # BUILD_TESTING is defined by include(CTest); ON by default. Respect
  # it so `-DBUILD_TESTING=OFF` (via `buildutil build --no-tests`) skips
  # test target creation project-wide.
  if(NOT BUILD_TESTING)
    return()
  endif()
  _buildutil_split_sources(_srcs _tests _benches)
  list(APPEND _tests ${ARGN})                # generated test sources routed in
  if(NOT _tests)
    return()
  endif()
  set(test_target ${target}-tests)
  _buildutil_library_of("${target}" lib)
  add_executable(${test_target} ${_tests})
  _buildutil_apply_optimization(${test_target})
  _buildutil_track_suite(${test_target})
  get_target_property(module_type ${lib} TYPE)
  if(module_type STREQUAL "MODULE_LIBRARY")
    # standalone: nothing is linked, so there are no usage requirements to
    # inherit -- restate the roots Init_submodule would have handed down,
    # the module's own dir included (a test TU under a *.test/ subtree is
    # not next to the headers it tests; see Init_submodule)
    target_include_directories(${test_target} PRIVATE
      "${CMAKE_SOURCE_DIR}/sources" "${CMAKE_CURRENT_SOURCE_DIR}")
    _buildutil_apply_compile_flags(${test_target} PRIVATE)
    target_link_libraries(${test_target} PRIVATE GTest::gtest_main)
  else()
    target_link_libraries(${test_target} PRIVATE ${lib} GTest::gtest_main)
    # The module's own dir is PRIVATE on the module (see Init_submodule), so
    # it does NOT come through the link -- restate it. A test TU under a
    # *.test/ subtree does not sit next to the headers it tests, and the
    # quoted-include rule only ever searches the INCLUDING file's own dir.
    target_include_directories(${test_target} PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
    _buildutil_add_link_pools(${test_target} PRIVATE)
  endif()
  # A suite that RUNS its module's own program must not run a stale one.
  # `buildutil test <module>` builds <module>-tests and nothing else, so
  # without this edge an end-to-end suite exec's whatever binary happens
  # to be lying in the tree -- and PASSES against it. That failure is
  # silent, survives a rebuild of the wrong profile, and looks exactly
  # like success.
  #
  # This is the other half of the PATH contract: PATH says WHERE the
  # tools are, this says they are CURRENT. Only for a module that has an
  # executable -- where it does not, ${target} IS the library the test
  # already links, and the edge exists through the link.
  _buildutil_library_of("${target}" _own_lib)
  if(NOT _own_lib STREQUAL target AND TARGET ${target})
    add_dependencies(${test_target} ${target})
  endif()
  _buildutil_mirror_parent(_mirror)
  # A suite is a BUILD-tree artifact: ctest runs it from there, and a
  # packaged project's install tree is what its conan package ships --
  # megabytes of test binary in every consumer's cache, for a program
  # nobody installs to run. Excluded from the default install, still
  # reachable as `cmake --install <build> --component tests`.
  install(TARGETS ${test_target} RUNTIME DESTINATION "${_mirror}"
          COMPONENT tests EXCLUDE_FROM_ALL)
  # Label each discovered test with the owning module, so `ctest -L <mod>`
  # — and `buildutil test --target <mod>` — can filter.
  #
  # And give every test process the two things an end-to-end suite needs,
  # as ENVIRONMENT rather than as anything it declares:
  #
  #   PATH -- <build>/bin prepended, so a suite that drives the project's
  #     own programs invokes them BY BARE NAME, exactly as a person at a
  #     shell would. Nothing buildutil-specific appears in a test source,
  #     and reproducing a failure by hand is the same command with that
  #     directory on PATH. The file in bin/ IS the module name,
  #     so there is no mapping to declare or keep in step. This replaces
  #     per-module wiring that baked RUN-time paths into COMPILE-time
  #     macros -- a restatement of what the build already knew, which
  #     could drift from it silently.
  #
  #   WORKING_DIRECTORY -- the module's own directory, so corpora and
  #     inputs are plain relative paths from where the suite's sources
  #     live, rather than something computed and injected.
  #
  # Set as TEST PROPERTIES, not by the runner: the contract lives with
  # the test, not with whoever invokes it. (A bare `ctest` is refused by
  # the driver-guard fixture since 0.45 — owner ruling — but the refusal
  # is EXPLAINED, and `BUILDUTIL=1 ctest` still behaves identically to
  # the driver precisely because these are properties, not runner flags.)
  # path_list_prepend is cmake's own separator-aware form, so this is not
  # a POSIX-only ':' assumption.
  #
  # AND THE WORKING DIRECTORY IS NOT PASSED TO gtest_discover_tests, which
  # is the whole point of the two lines after it. That option is the
  # directory the DISCOVERY runs in as well as the one the tests run in,
  # and CMake 4.2's GoogleTestAddTests.cmake writes its listing there:
  #
  #     set(json_file
  #       "${arg_TEST_WORKING_DIR}/cmake_test_discovery_${target_hash}.json")
  #
  # So every module got a cmake_test_discovery_<hash>.json IN ITS SOURCE
  # DIRECTORY, conan exported them with the package (sources/* goes in
  # wholesale), and one package shipped a SECOND recipe revision
  # from the Windows runner whose manifest differed from Linux's by
  # exactly those five files -- and since consumers resolve the latest
  # revision, that revision hid the Linux and macOS binaries. CMake 4.4
  # writes it to CMAKE_CURRENT_BINARY_DIR instead, which is why no other
  # lane ever saw it: the Windows desktop builds with the cmake Visual
  # Studio ships (4.2), the containers with a newer one.
  #
  # TEST_LIST is the module's own answer to "set properties on the
  # discovered tests" (its comment says so), and TEST_INCLUDE_FILES is
  # where that list exists -- ctest time, after the discovery include has
  # populated it. So the discovery runs in the build tree, where its
  # scratch file belongs, and the TESTS still run in the module's source
  # directory, which is the contract above.
  _buildutil_ensure_ctest_guard()
  gtest_discover_tests(${test_target}
    DISCOVERY_MODE PRE_TEST
    DISCOVERY_TIMEOUT @DISCOVERY_TIMEOUT@
    TEST_LIST ${test_target}_discovered
    PROPERTIES
      LABELS ${target}
      FIXTURES_REQUIRED buildutil-driver
      ENVIRONMENT_MODIFICATION "PATH=path_list_prepend:${CMAKE_BINARY_DIR}/bin")
  set(_buildutil_workdir_script
      "${CMAKE_CURRENT_BINARY_DIR}/${test_target}_workdir.cmake")
  file(GENERATE OUTPUT "${_buildutil_workdir_script}" CONTENT
"# Generated by buildutil: the discovered tests' working directory.
if(${test_target}_discovered)
  set_tests_properties(\${${test_target}_discovered} PROPERTIES
    WORKING_DIRECTORY \"${CMAKE_CURRENT_SOURCE_DIR}\")
endif()
")
  set_property(DIRECTORY APPEND PROPERTY TEST_INCLUDE_FILES
               "${_buildutil_workdir_script}")
endfunction()

# THE INTERPRETER THE DRIVER IS RUNNING UNDER, for everything that needs a
# python: the project venv's in the normal case, the IMAGE's under
# BUILDUTIL_SYSTEM=1, where there is no _pyvenv at all.
#
# It used to be computed as `${CMAKE_SOURCE_DIR}/_pyvenv/bin/python`, a
# path that simply does not exist on an image whose whole point is that
# it carries the toolchain instead of building one per checkout. ctest
# does not fail on a command it cannot find; it reports "***Not Run",
# which is neither a pass nor a failure and reads like neither.
# A project's first python suite spent a pipeline saying so: green
# locally, Not Run in CI. The pybind bridge probe had the same path and
# the same blind spot, and skipped itself on exactly the images that
# ship pybind11.
#
# BUILDUTIL_PY is what the driver passes; a bare `BUILDUTIL=1 cmake`
# (which the driver guard allows) declares no interpreter, and there the
# ambient python3 is the honest answer rather than a refusal.
function(_buildutil_driver_python out)
  if(BUILDUTIL_PY)
    set(${out} "${BUILDUTIL_PY}" PARENT_SCOPE)
    return()
  endif()
  find_program(_buildutil_ambient_python NAMES python3 python)
  if(NOT _buildutil_ambient_python)
    message(FATAL_ERROR
      "no python: BUILDUTIL_PY is unset (so this is not a driver build) "
      "and neither python3 nor python is on PATH. Run the build through "
      "`buildutil`, which passes the interpreter it runs under.")
  endif()
  message(WARNING
    "BUILDUTIL_PY unset -- python work runs under ${_buildutil_ambient_python}")
  set(${out} "${_buildutil_ambient_python}" PARENT_SCOPE)
endfunction()

# A python test suite, by presence and nothing else: a *.test.py in the
# module, or python inside a *.test/ subtree. It registers exactly as the
# gtest suites do -- same `buildutil test` run, same ctest totals, same
# `ctest -L <module>` filter.
#
# The gap this fills: a driver-style suite spawns the project's own built
# tools on real inputs and compares output. That is subprocess scripting,
# which python is built for and a compiled harness is worst at -- and
# because gtest was the only registered kind, such suites had to be C++,
# which is how compile-time path plumbing got invented in the first place
# Unit tests that LINK module code stay gtest; this is the
# second kind, for suites that RUN programs rather than link them.
#
# It inherits the same contract as every other test process: <build>/bin
# on PATH so tools are invoked by bare name, the module dir as cwd so
# corpora are relative paths, and each suite directory on PYTHONPATH so a
# helper module beside the tests imports by its bare name.
#
# One ctest entry per suite, not per case: discovering cases would mean
# running pytest at CONFIGURE time, and a configure step that executes
# the project's tests to find out what they are is a worse trade than a
# coarser count. pytest's own report still names every case that failed.
function(_buildutil_add_python_test_target target)
  # same gate as the gtest suites: `buildutil build --no-tests` must skip
  # this kind too, or --no-tests stops meaning what it says
  if(NOT BUILD_TESTING)
    return()
  endif()
  _buildutil_python_suite_files(py_tests "${CMAKE_CURRENT_SOURCE_DIR}")
  _buildutil_python_suite_dirs(_dirs "${CMAKE_CURRENT_SOURCE_DIR}")
  list(APPEND py_tests ${_dirs})
  if(NOT py_tests)
    return()
  endif()
  _buildutil_pytest_file_option(_files_option)
  _buildutil_register_python_suite(${target} "${CMAKE_CURRENT_SOURCE_DIR}"
                                   ${py_tests} ${_files_option})
  foreach(_dir IN LISTS _dirs)
    set_property(TEST ${target}-pytest APPEND PROPERTY
      ENVIRONMENT_MODIFICATION "PYTHONPATH=path_list_prepend:${_dir}")
  endforeach()
endfunction()

# What pytest calls a test file: its own two conventions, plus buildutil's
# `*.test.py`. Everything else a suite directory holds is a helper --
# conftest.py, a shared protocol module -- and handing those to pytest as
# explicit paths is what made them into (empty) test files.
function(_buildutil_pytest_patterns out)
  set(${out} "test_*.py" "*_test.py" "*.test.py" PARENT_SCOPE)
endfunction()

# The same vocabulary as pytest reads it: one -o for a directory argument.
function(_buildutil_pytest_file_option out)
  _buildutil_pytest_patterns(_patterns)
  string(JOIN " " _joined ${_patterns})
  set(${out} -o "python_files=${_joined}" PARENT_SCOPE)
endfunction()

# A module's loose `*.test.py` files, outside its `*.test/` suites.
function(_buildutil_python_suite_files out root)
  file(GLOB_RECURSE _found CONFIGURE_DEPENDS "${root}/*.test.py")
  set(_files "")
  foreach(_file IN LISTS _found)
    file(RELATIVE_PATH _rel "${root}" "${_file}")
    if(NOT _rel MATCHES "(^|/)[^/]*\\.test/")
      list(APPEND _files "${_file}")
    endif()
  endforeach()
  set(${out} "${_files}" PARENT_SCOPE)
endfunction()

# The `*.test/` directories under `root` that hold a python suite. A
# directory qualifies by holding a file pytest would collect, so a
# `*.test/` subtree of C++ sources stays a gtest subtree.
function(_buildutil_python_suite_dirs out root)
  set(_dirs "")
  _buildutil_pytest_patterns(_patterns)
  foreach(_pattern IN LISTS _patterns)
    file(GLOB_RECURSE _found CONFIGURE_DEPENDS "${root}/*.test/${_pattern}")
    foreach(_file IN LISTS _found)
      string(REGEX REPLACE "(/[^/]*\\.test)/.*$" "\\1" _dir "${_file}")
      list(APPEND _dirs "${_dir}")
    endforeach()
  endforeach()
  list(REMOVE_DUPLICATES _dirs)
  set(${out} "${_dirs}" PARENT_SCOPE)
endfunction()

# ARGN is what pytest is handed: a module's files, or a declared directory.
function(_buildutil_register_python_suite target directory)
  _buildutil_driver_python(_suite_python)
  # pytest has to be importable by THAT interpreter. In venv mode it
  # always is (it is in the driver's own VENV_DEPS); under
  # BUILDUTIL_SYSTEM the image supplies it, and if an image ever does not,
  # this says so with the file name in hand instead of registering a test
  # that cannot run.
  execute_process(COMMAND "${_suite_python}" -c "import pytest"
                  RESULT_VARIABLE _pytest_missing
                  OUTPUT_QUIET ERROR_QUIET)
  if(NOT _pytest_missing EQUAL 0)
    list(GET ARGN 0 _first_suite)
    message(FATAL_ERROR
      "pytest is missing for ${_first_suite}: ${_suite_python} cannot "
      "import it, so this python suite could not run. In a "
      "project venv the driver installs it; on a BUILDUTIL_SYSTEM image "
      "the image must carry it (pip install pytest into the image's "
      "venv, and file the gap against the image).")
  endif()
  _buildutil_ensure_ctest_guard()
  # --import-mode=importlib: the suite files are NAMED with dots
  # (check.test.py), and pytest's default import mode derives a module
  # name from the stem — "check.test" — then dies on
  # `ModuleNotFoundError: No module named 'check'`. importlib mode
  # loads the file directly and has no opinion about its name.
  #
  # -rs: skip reasons reach the entry's output, which ctest keeps.
  set(_env "PATH=path_list_prepend:${CMAKE_BINARY_DIR}/bin")
  set(_plugin "")
  if(BUILDUTIL_PYSUPPORT)
    # buildutil_pytest.py: the case counts and the all-skipped verdict a
    # single ctest entry cannot otherwise show. A bare `BUILDUTIL=1 cmake`
    # declares no pysupport, and there plain pytest is the honest answer.
    set(_plugin -p buildutil_pytest)
    list(APPEND _env
      "PYTHONPATH=path_list_prepend:${BUILDUTIL_PYSUPPORT}"
      "BUILDUTIL_PYTEST_REPORT=set:${CMAKE_BINARY_DIR}/Testing/buildutil-pytest/${target}-pytest.json")
  endif()
  add_test(NAME ${target}-pytest
    COMMAND "${_suite_python}" -m pytest --import-mode=importlib ${_plugin}
            ${ARGN} -q -rs)
  set_tests_properties(${target}-pytest PROPERTIES
    LABELS ${target}
    # 77 is what buildutil_pytest.py exits when every case skipped, so a
    # suite that ran nothing reads as ctest's own Skipped, not as a pass
    SKIP_RETURN_CODE 77
    WORKING_DIRECTORY "${directory}"
    FIXTURES_REQUIRED buildutil-driver
    ENVIRONMENT_MODIFICATION "${_env}")
endfunction()

# The value `key` carries in a list of `key=value` pairs, empty when it
# carries none. A string compare, not a regex: a key here is a path, and a
# path holds regex metacharacters.
function(_buildutil_pair_value out pairs key)
  set(${out} "" PARENT_SCOPE)
  foreach(_pair IN LISTS pairs)
    string(FIND "${_pair}" "=" _at)
    string(SUBSTRING "${_pair}" 0 ${_at} _name)
    if(_name STREQUAL key)
      math(EXPR _from "${_at} + 1")
      string(SUBSTRING "${_pair}" ${_from} -1 _value)
      set(${out} "${_value}" PARENT_SCOPE)
      return()
    endif()
  endforeach()
endfunction()

# [test] python: a directory that is not a module, as one ctest entry.
# pytest is handed the DIRECTORY, so its own patterns apply, plus *.test.py.
# A declared directory holding none of them fails the configure.
#
# `timeouts` is [test.timeout] as `<suite>=<seconds>` pairs, set as the
# entry's TIMEOUT property -- which ctest honours over its own --timeout,
# so a suite that declares nothing stays bounded by the driver's flag.
function(_buildutil_python_suites suites timeouts)
  if(NOT BUILD_TESTING)
    return()
  endif()
  _buildutil_pytest_patterns(_patterns)
  _buildutil_pytest_file_option(_files_option)
  foreach(_suite IN LISTS suites)
    set(_suite_dir "${CMAKE_SOURCE_DIR}/${_suite}")
    if(NOT IS_DIRECTORY "${_suite_dir}")
      message(FATAL_ERROR
        "buildutil.toml [test] python names \"${_suite}\", which is not a "
        "directory of this repository.")
    endif()
    set(_suite_files "")
    foreach(_pattern IN LISTS _patterns)
      file(GLOB_RECURSE _found CONFIGURE_DEPENDS "${_suite_dir}/${_pattern}")
      list(APPEND _suite_files ${_found})
    endforeach()
    if(NOT _suite_files)
      message(FATAL_ERROR
        "buildutil.toml [test] python names \"${_suite}\", which holds no "
        "test file (test_*.py, *_test.py or *.test.py). Write the suite, or "
        "drop the declaration.")
    endif()
    string(REPLACE "/" "-" _suite_target "${_suite}")
    _buildutil_register_python_suite(${_suite_target} "${_suite_dir}"
      "${_suite_dir}" ${_files_option})
    set_tests_properties(${_suite_target}-pytest PROPERTIES
      ENVIRONMENT "BUILDUTIL_BUILD_DIR=${CMAKE_BINARY_DIR};BUILDUTIL_PROFILE=${BUILDUTIL_PROFILE}")
    _buildutil_pair_value(_seconds "${timeouts}" "${_suite}")
    if(_seconds)
      set_tests_properties(${_suite_target}-pytest PROPERTIES
                           TIMEOUT "${_seconds}")
    endif()
  endforeach()
endfunction()

# Wire any *.bench.cpp files into a `${target}-benches` executable
# that links google-benchmark's benchmark_main. Gated by
# BUILD_BENCHMARKING — same convention as BUILD_TESTING — so a debug
# build doesn't pull in the bench harness unless asked.
function(_buildutil_add_bench_target target)
  if(NOT BUILD_BENCHMARKING)
    return()
  endif()
  _buildutil_split_sources(_srcs _tests _benches)
  list(APPEND _benches ${ARGN})              # generated bench sources routed in
  if(NOT _benches)
    return()
  endif()
  set(bench_target ${target}-benches)
  _buildutil_library_of("${target}" lib)
  add_executable(${bench_target} ${_benches})
  _buildutil_apply_optimization(${bench_target})
  _buildutil_track_suite(${bench_target})
  get_target_property(module_type ${lib} TYPE)
  if(module_type STREQUAL "MODULE_LIBRARY")
    # standalone, same as the test target above: no link, so no inherited
    # roots -- restate them, the module's own dir included
    target_include_directories(${bench_target} PRIVATE
      "${CMAKE_SOURCE_DIR}/sources" "${CMAKE_CURRENT_SOURCE_DIR}")
    _buildutil_apply_compile_flags(${bench_target} PRIVATE)
    target_link_libraries(${bench_target} PRIVATE benchmark::benchmark_main)
  else()
    target_link_libraries(${bench_target}
      PRIVATE ${lib} benchmark::benchmark_main)
    # same as the test target above: the module's own dir is PRIVATE on the
    # module, so it is not inherited through the link -- restate it
    target_include_directories(${bench_target} PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
    _buildutil_add_link_pools(${bench_target} PRIVATE)
  endif()
  _buildutil_mirror_parent(_mirror)
  install(TARGETS ${bench_target} RUNTIME DESTINATION "${_mirror}"
          COMPONENT benches EXCLUDE_FROM_ALL)
endfunction()

# Compile flags for our own targets (the standard is
# _buildutil_set_cxx_standard's, below):
#  * /bigobj (MSVC) — the generated dual-mode mnemonic tests exceed
#    MSVC's COFF per-object section cap (fatal error C1128); /bigobj
#    lifts that limit and is side-effect-free on smaller objects.
#  * /utf-8 (MSVC) — every other compiler reads a source file as UTF-8;
#    MSVC reads it in the system codepage unless the file carries a BOM
#    or this flag says so. A single non-ASCII character in a literal is
#    then two characters: `U'é'` is "error C2015: too many characters in
#    constant", and a string literal silently becomes mojibake. The flag
#    sets both the source and the execution charset, which is what a
#    project whose files are UTF-8 (all of them, no BOMs — the .gitattributes
#    say so) actually means. Found on a project's first native Windows
#    build: thirteen C2015s across two test files.
#  * constant-evaluation caps — the generated decoder tables are
#    ~1M-entry constexpr arrays, each element minted by a constexpr
#    factory call. That blows past every compiler's default
#    constant-evaluation budget, so lift the caps generously.
#  * -fvisibility=hidden (gcc/clang) — ELF exports every symbol by
#    default; defaulting them to private matches MSVC, shrinks the
#    dynamic symbol table, and frees LTO/the linker to inline across
#    TUs. What must stay public says so itself (pybind's module init
#    carries its own default-visibility attribute).
# ARC (automatic reference counting) is the default for every Objective-C
# and Objective-C++ TU buildutil compiles. Modern Apple code is written
# for it, the alternative is hand-written retain/release, and a project
# that has to spell `-fobjc-arc` per module is writing cmake logic again.
#
# The opt-out is a DECLARATION, not a flag: a module that genuinely holds
# manual-retain code (ported, or interoperating with something that does)
# says so in buildutil.toml --
#
#   [modules.legacygui]
#   objc_arc = false
#
# -- and buildutil compiles that module's Objective-C sources without
# ARC. Per module, because ARC is a property of the SOURCE, and a whole
# tree rarely agrees about it.
set(_buildutil_modules_no_arc @MODULES_NO_ARC@)

# Apple system frameworks a module links, from `[modules.<name>]
# frameworks = [...]`. An Objective-C module almost always needs at
# least one (Foundation for the runtime's constant-string class alone),
# and the alternative is a project writing
# `target_link_libraries(... "-framework Cocoa")` under an if(APPLE) --
# cmake logic, in the file that is supposed to be declarations.
#
# Registered per module and applied where the module is built, so a
# cross-platform module declares its Apple frameworks once and every
# other platform ignores the line.
function(_buildutil_module_frameworks module names)
  set_property(GLOBAL PROPERTY _buildutil_frameworks_${module} "${names}")
endfunction()

# The soname: soversion(number, version=) from the module's configure.py,
# else the package major, on every platform; cmake lays out the files.
function(_buildutil_apply_soversion target)
  _buildutil_module_name(_so_module)
  get_property(_so_number GLOBAL PROPERTY _buildutil_module_soversion_${_so_module})
  get_property(_so_version GLOBAL PROPERTY _buildutil_module_version_${_so_module})
  if("${_so_number}" STREQUAL "")
    get_property(_so_number GLOBAL PROPERTY _buildutil_package_major)
  endif()
  set_target_properties(${target} PROPERTIES SOVERSION "${_so_number}")
  if(NOT "${_so_version}" STREQUAL "")
    set_target_properties(${target} PROPERTIES VERSION "${_so_version}")
  endif()
endfunction()

# What only a shared library module may carry: a soname from its hook.
function(_buildutil_refuse_unshared_soversion target kind)
  get_property(declared GLOBAL PROPERTY _buildutil_module_soversion_${target} SET)
  if(declared AND NOT kind STREQUAL "so")
    message(FATAL_ERROR
      "${CMAKE_CURRENT_SOURCE_DIR}/configure.py declares soversion(), but "
      "${target} does not build as a shared library: a soname belongs to a "
      "module with a main.<so|dll|dylib>.cpp or a .so tag.")
  endif()
endfunction()

# exports.map, the linker version script of a shared module, by presence:
# beside main.<so|dll|dylib>.cpp or at the module root, or declared by the
# module's hook in a generated root; one of them, on a shared module only.
function(_buildutil_find_version_script target kind shared_entry out)
  set(checked_in "")
  set(beside "${CMAKE_CURRENT_SOURCE_DIR}")
  foreach(entry IN LISTS shared_entry)
    get_filename_component(entry_dir "${entry}" DIRECTORY)
    list(APPEND beside "${entry_dir}")
  endforeach()
  list(REMOVE_DUPLICATES beside)
  foreach(dir IN LISTS beside)
    file(GLOB hit CONFIGURE_DEPENDS "${dir}/exports.map")
    list(APPEND checked_in ${hit})
  endforeach()
  _buildutil_declared_version_scripts(${target} generated)
  set(all ${checked_in} ${generated})
  if(all AND NOT kind STREQUAL "so")
    list(JOIN all "\n  " pretty)
    message(FATAL_ERROR
      "${CMAKE_CURRENT_SOURCE_DIR} is not a shared library module, yet "
      "holds a linker version script:\n  ${pretty}\nexports.map belongs "
      "to a module with a main.<so|dll|dylib>.cpp or a .so tag.")
  endif()
  list(LENGTH all count)
  if(count GREATER 1)
    list(JOIN all "\n  " pretty)
    message(FATAL_ERROR
      "${CMAKE_CURRENT_SOURCE_DIR} has more than one exports.map, checked "
      "in or declared by its configure.py:\n  ${pretty}\nA shared library "
      "links with one version script.")
  endif()
  set(${out} "${all}" PARENT_SCOPE)
endfunction()

function(_buildutil_declared_version_scripts target out)
  get_property(declared GLOBAL PROPERTY _buildutil_module_declared_${target})
  _buildutil_generated_roots("${target}" roots)
  set(found "")
  foreach(root IN LISTS roots)
    if(EXISTS "${root}/exports.map")
      file(REAL_PATH "${root}/exports.map" wanted)
      foreach(path IN LISTS declared)
        file(REAL_PATH "${path}" real)
        if(real STREQUAL wanted)
          list(APPEND found "${path}")
        endif()
      endforeach()
    endif()
  endforeach()
  set(${out} "${found}" PARENT_SCOPE)
endfunction()

# ELF: the objects' _Public_ marks become the version script before the
# link, a checked-in or declared exports.map instead when there is one,
# and the archives stay out of the dynamic table.
function(_buildutil_apply_exports lib target script)
  if(NOT _buildutil_elf)
    if(script)
      message(WARNING
        "${script}: a linker version script is applied on ELF targets only; "
        "${lib} links for ${CMAKE_SYSTEM_NAME} without it and exports what "
        "its symbol visibility says.")
    endif()
    return()
  endif()
  set(dir "${CMAKE_BINARY_DIR}/generated/_buildutil/exports/${target}")
  get_target_property(node ${lib} OUTPUT_NAME)
  string(TOUPPER "${node}" node)
  string(REGEX REPLACE "[^0-9A-Z]" "_" node "${node}")
  get_property(major GLOBAL PROPERTY _buildutil_package_major)
  _buildutil_require_export_scan(python)
  if(NOT EXISTS "${dir}/linker.rsp")
    file(WRITE "${dir}/linker.rsp" "")
  endif()
  # clang spells LINKER: as -Xlinker, and its driver expands the @file that
  # follows, so an empty file takes the next argument: -Wl, reaches ld whole.
  target_link_options(${lib} PRIVATE "LINKER:--exclude-libs,ALL" "-Wl,@${dir}/linker.rsp")
  if(script)
    set_property(TARGET ${lib} APPEND PROPERTY LINK_DEPENDS "${script}")
  endif()
  add_custom_command(TARGET ${lib} PRE_LINK
    COMMAND ${python} -m buildutil.exports link
            --objects "${dir}/objects.txt" --major "${major}" --node "${node}"
            --map "${script}" --map-out "${dir}/exports.map"
            --rsp "${dir}/linker.rsp" --record "${dir}/marks.json"
    VERBATIM)
  add_custom_command(TARGET ${lib} POST_BUILD
    COMMAND ${python} -m buildutil.exports verify
            --library "$<TARGET_FILE:${lib}>" --record "${dir}/marks.json"
    VERBATIM)
  set_property(GLOBAL APPEND PROPERTY _buildutil_export_scans "${lib}")
  set_property(TARGET ${lib} PROPERTY _buildutil_export_objects "${dir}/objects.txt")
endfunction()

# The scan runs at every ELF shared module's link, so a cmake that cannot
# import it fails here rather than at the first link.
function(_buildutil_require_export_scan out)
  _buildutil_python_command(python)
  get_property(probed GLOBAL PROPERTY _buildutil_export_scan_probed)
  if(NOT probed)
    execute_process(COMMAND ${python} -c "import buildutil.exports"
                    WORKING_DIRECTORY "${CMAKE_BINARY_DIR}"
                    RESULT_VARIABLE missing OUTPUT_QUIET ERROR_VARIABLE reason)
    if(missing)
      message(FATAL_ERROR
        "buildutil: this cmake cannot run buildutil.exports, which every shared "
        "module links through on ELF. Configure with -DBUILDUTIL_PYSUPPORT="
        "<site-packages>/buildutil/pysupport (the driver passes it).\n${reason}")
    endif()
    set_property(GLOBAL PROPERTY _buildutil_export_scan_probed TRUE)
  endif()
  set(${out} "${python}" PARENT_SCOPE)
endfunction()

# What the scan reads: the library's own objects and those of the object
# libraries it links directly, which the link takes whole; an archive's
# marks stay hidden by --exclude-libs,ALL, so no archive is read.
function(_buildutil_list_export_objects)
  get_property(libs GLOBAL PROPERTY _buildutil_export_scans)
  foreach(lib IN LISTS libs)
    set(objects "$<TARGET_OBJECTS:${lib}>")
    get_target_property(links ${lib} LINK_LIBRARIES)
    foreach(dep IN LISTS links)
      if(TARGET ${dep})
        get_target_property(type ${dep} TYPE)
        if(type STREQUAL "OBJECT_LIBRARY")
          list(APPEND objects "$<TARGET_OBJECTS:${dep}>")
        endif()
      endif()
    endforeach()
    get_target_property(listing ${lib} _buildutil_export_objects)
    file(GENERATE OUTPUT "${listing}" CONTENT "$<JOIN:${objects},\n>\n")
  endforeach()
endfunction()

function(_buildutil_apply_objc_options target visibility)
  if(NOT APPLE)
    return()
  endif()
  _buildutil_module_name(_arc_module)
  get_property(_arc_frameworks GLOBAL
               PROPERTY _buildutil_frameworks_${_arc_module})
  foreach(_arc_fw IN LISTS _arc_frameworks)
    target_link_libraries(${target} ${visibility} "-framework ${_arc_fw}")
  endforeach()
  if(_arc_module IN_LIST _buildutil_modules_no_arc)
    return()
  endif()
  target_compile_options(${target} ${visibility}
    "$<$<COMPILE_LANGUAGE:OBJC,OBJCXX>:-fobjc-arc>")
endfunction()

# Which languages a C++ flag may be put on. An Objective-C++ TU IS C++
# and takes all of them; a plain Objective-C .m is C, and clang says so
# -- "argument unused during compilation" -- about every one of them,
# which is noise in any project and an ERROR in one built -Werror. The
# genex costs nothing where no module has Objective-C: OBJC/OBJCXX are
# not even enabled languages then.
set(_buildutil_cxx_langs "$<COMPILE_LANGUAGE:CXX,OBJCXX>")

# Whether a target gets the project's hidden-visibility default. Asked as
# a generator expression because two -fvisibility flags on one command
# line are resolved by POSITION, and position is not intent: this default
# is appended after a module library's PUBLISH_SYMBOLS opt-out, and
# reaches that module's app through the library's INTERFACE later still,
# so appending anything could never win. A genex is read at GENERATE
# time, so no call order defeats it; in an INTERFACE copy it is read
# against the CONSUMER, whose sources are the ones being compiled. MSVC
# has no visibility flag -- there the question is
# WINDOWS_EXPORT_ALL_SYMBOLS.
set(_buildutil_hidden_default "$<AND:$<NOT:$<CXX_COMPILER_ID:MSVC>>,\
$<NOT:$<BOOL:$<TARGET_PROPERTY:_buildutil_publish_symbols>>>>")

if(APPLE OR WIN32 OR EMSCRIPTEN)
  set(_buildutil_elf FALSE)
else()
  set(_buildutil_elf TRUE)
endif()

# The export scan reads machine code, which an LTO object carries only when
# fat; without -flto the flag changes nothing (gcc output byte-identical).
set(_buildutil_fat_lto "$<AND:$<COMPILE_LANGUAGE:C,CXX>,$<OR:$<CXX_COMPILER_ID:GNU>,\
$<AND:$<CXX_COMPILER_ID:Clang>,$<VERSION_GREATER_EQUAL:$<CXX_COMPILER_VERSION>,17>>>>")

set(_buildutil_optimize_always @OPTIMIZE_ALWAYS@)
if(MSVC AND _buildutil_optimize_always)
  # /RTC1 is incompatible with /O2; move the shared flag to a target condition
  # so unlisted targets keep their runtime checks.
  foreach(lang C CXX OBJC OBJCXX)
    if(CMAKE_${lang}_FLAGS_DEBUG MATCHES "(^| )/RTC1( |$)")
      string(REGEX REPLACE "(^| )/RTC1( |$)" " "
        CMAKE_${lang}_FLAGS_DEBUG "${CMAKE_${lang}_FLAGS_DEBUG}")
      add_compile_options(
        "$<$<AND:$<CONFIG:Debug>,$<COMPILE_LANGUAGE:${lang}>,$<NOT:$<BOOL:$<TARGET_PROPERTY:_buildutil_optimized>>>>:/RTC1>")
    endif()
  endforeach()
endif()

function(_buildutil_apply_optimization target)
  _buildutil_module_name(module)
  if(NOT module IN_LIST _buildutil_optimize_always)
    return()
  endif()
  set_property(TARGET ${target} PROPERTY _buildutil_optimized TRUE)
  if(MSVC)
    set_property(TARGET ${target} PROPERTY MSVC_RUNTIME_CHECKS "")
    target_compile_options(${target} PRIVATE "$<$<CONFIG:Debug>:/O2>")
  else()
    target_compile_options(${target} PRIVATE
      "$<$<AND:$<CONFIG:Debug>,$<COMPILE_LANG_AND_ID:C,GNU,Clang,AppleClang>>:-O2>"
      "$<$<AND:$<CONFIG:Debug>,$<COMPILE_LANG_AND_ID:CXX,GNU,Clang,AppleClang>>:-O2>"
      "$<$<AND:$<CONFIG:Debug>,$<COMPILE_LANG_AND_ID:OBJC,Clang,AppleClang>>:-O2>"
      "$<$<AND:$<CONFIG:Debug>,$<COMPILE_LANG_AND_ID:OBJCXX,Clang,AppleClang>>:-O2>")
  endif()
endfunction()

function(_buildutil_apply_compile_flags target visibility)
  _buildutil_apply_optimization(${target})
  set(cxx "${_buildutil_cxx_langs}")
  set(hides "${_buildutil_hidden_default}")
  target_compile_options(${target} ${visibility}
    $<$<CXX_COMPILER_ID:MSVC>:/bigobj;/constexpr:steps100000000;/utf-8>
    "$<$<AND:${cxx},$<CXX_COMPILER_ID:Clang,AppleClang>>:-fconstexpr-steps=100000000>"
    "$<$<AND:${cxx},$<CXX_COMPILER_ID:GNU>>:-fconstexpr-ops-limit=100000000>"
    "$<$<AND:${cxx},$<CXX_COMPILER_ID:GNU>>:-Werror=narrowing>"
    $<$<CXX_COMPILER_ID:Clang,AppleClang,GNU>:-fdollars-in-identifiers>
    "$<${hides}:-fvisibility=hidden>"
    "$<$<AND:${cxx},${hides}>:-fvisibility-inlines-hidden>")
  if(_buildutil_elf)
    target_compile_options(${target} ${visibility} "$<${_buildutil_fat_lto}:-ffat-lto-objects>")
  endif()
  _buildutil_apply_objc_options(${target} ${visibility})
  # `./buildutil --max-errors N` / `--fail-fast` (== N 1), via -D@CMAKE_OPTION_PREFIX@_MAX_ERRORS:
  # stop each compile after N errors for a tight fix-rebuild loop. 0 / undefined = off.
  # gcc spells it -fmax-errors, clang -ferror-limit; MSVC has no equivalent, no-op.
  if(@CMAKE_OPTION_PREFIX@_MAX_ERRORS GREATER 0)
    target_compile_options(${target} ${visibility}
      $<$<CXX_COMPILER_ID:GNU>:-fmax-errors=${@CMAKE_OPTION_PREFIX@_MAX_ERRORS}>
      $<$<CXX_COMPILER_ID:Clang,AppleClang>:-ferror-limit=${@CMAKE_OPTION_PREFIX@_MAX_ERRORS}>)
  endif()
endfunction()

# The C++ standard of a module's own targets: Init_submodule(STANDARD n),
# else [project] cxx_standard, else the lane's. Dependencies keep the
# profile's compiler.cppstd; nothing here touches conan's settings.
set(_buildutil_cxx_standards "@CXX_STANDARDS@")
string(REPLACE ";" ", " _buildutil_cxx_accepted "${_buildutil_cxx_standards}")
set(_buildutil_project_cxx_standard "@CXX_STANDARD@")
# cl alone, the same test as $<CXX_COMPILER_ID:MSVC>: clang-cl has a C++26
# flag in cmake and no conan cap to lift.
if(CMAKE_CXX_COMPILER_ID STREQUAL "MSVC")
  set(_buildutil_cl TRUE)
else()
  set(_buildutil_cl FALSE)
endif()

# The highest standard this lane's compiler takes; cl's 26 is /std:c++latest past conan's cap of 23.
function(_buildutil_cxx_standard_ceiling out)
  set(ceiling "")
  foreach(standard IN LISTS _buildutil_cxx_standards)
    if("cxx_std_${standard}" IN_LIST CMAKE_CXX_COMPILE_FEATURES)
      set(ceiling ${standard})
    endif()
  endforeach()
  if(_buildutil_cl AND ceiling EQUAL 23)
    set(ceiling 26)
  endif()
  set(${out} "${ceiling}" PARENT_SCOPE)
endfunction()

function(_buildutil_check_module_standard module standard missing)
  if("STANDARD" IN_LIST missing)
    message(FATAL_ERROR "module '${module}': Init_submodule(STANDARD) needs a "
      "value (accepted: ${_buildutil_cxx_accepted})")
  endif()
  if(NOT standard STREQUAL "" AND NOT standard IN_LIST _buildutil_cxx_standards)
    message(FATAL_ERROR "module '${module}': Init_submodule(STANDARD ${standard}) "
      "is not a standard buildutil builds (accepted: ${_buildutil_cxx_accepted})")
  endif()
endfunction()

# `source` is the declaration the standard came from, as the error shows it.
function(_buildutil_refuse_above_ceiling standard source)
  _buildutil_cxx_standard_ceiling(ceiling)
  if(ceiling STREQUAL "")
    set(ceiling "none of ${_buildutil_cxx_accepted}")
  elseif(standard GREATER ceiling)
    set(ceiling "C++${ceiling}")
  else()
    return()
  endif()
  message(FATAL_ERROR
    "${source} asks for C++${standard}, but this lane's compiler "
    "(${CMAKE_CXX_COMPILER_ID} ${CMAKE_CXX_COMPILER_VERSION}) tops out at "
    "${ceiling}. Lower the standard or build on a newer compiler; "
    "buildutil never downgrades it.")
endfunction()

# The project-wide standard and its source: [project] cxx_standard, else the lane's; empty where neither names one.
function(_buildutil_project_standard out_standard out_source)
  if(NOT _buildutil_project_cxx_standard STREQUAL "")
    set(standard "${_buildutil_project_cxx_standard}")
    set(source "[project] cxx_standard = ${standard}")
  elseif(_buildutil_cl)
    set(standard 26)
    set(source "the MSVC lane's /std:c++latest")
  else()
    set(standard "${CMAKE_CXX_STANDARD}")
    set(source "the lane's compiler.cppstd=${standard}")
  endif()
  set(${out_standard} "${standard}" PARENT_SCOPE)
  set(${out_source} "${source}" PARENT_SCOPE)
endfunction()

# MIGRATION SHIM, one release only (#175, removed in 0.97.0): a directory's own CMAKE_CXX_STANDARD, checked and warned once.
function(_buildutil_directory_standard_shim standard)
  if(standard STREQUAL BUILDUTIL_CXX_STANDARD)
    return()
  endif()
  file(RELATIVE_PATH dir "${CMAKE_SOURCE_DIR}" "${CMAKE_CURRENT_SOURCE_DIR}")
  set(source "set(CMAKE_CXX_STANDARD ${standard}) in ${dir} (use [project] cxx_standard)")
  _buildutil_refuse_above_ceiling("${standard}" "${source}")
  get_property(warned GLOBAL PROPERTY _buildutil_directory_standard_warned)
  if(NOT warned)
    set_property(GLOBAL PROPERTY _buildutil_directory_standard_warned TRUE)
    message(DEPRECATION "${source}: a directory's own C++ standard is read for "
      "one more release; declare it in buildutil.toml instead.")
  endif()
endfunction()

# A module's standard: its own STANDARD, checked here, else the project's, checked once at include.
function(_buildutil_module_standard module standard missing out)
  _buildutil_check_module_standard("${module}" "${standard}" "${missing}")
  if(standard STREQUAL "")
    _buildutil_project_standard(project_standard ignored_source)
    _buildutil_directory_standard_shim("${project_standard}")
    set(${out} "${project_standard}" PARENT_SCOPE)
    return()
  endif()
  _buildutil_refuse_above_ceiling("${standard}"
    "module '${module}': Init_submodule(STANDARD ${standard})")
  set(${out} "${standard}" PARENT_SCOPE)
endfunction()

# cl: cmake maps 23 to /std:c++latest; the explicit flag keeps 26 should that mapping change.
function(_buildutil_set_cxx_standard standard)
  if(standard STREQUAL "")
    return()
  endif()
  set(cmake_standard ${standard})
  if(_buildutil_cl AND standard EQUAL 26)
    set(cmake_standard 23)
  endif()
  set(targets ${ARGN})
  list(REMOVE_DUPLICATES targets)
  foreach(target IN LISTS targets)
    if(NOT TARGET ${target})
      continue()
    endif()
    set_target_properties(${target} PROPERTIES CXX_STANDARD ${cmake_standard}
      CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)
    if(_buildutil_cl AND standard EQUAL 26)
      target_compile_options(${target} PRIVATE /std:c++latest)
    endif()
  endforeach()
endfunction()

# Checked once, here; cached for `buildutil vscode` and the reflect generator.
_buildutil_project_standard(_buildutil_standard _buildutil_standard_source)
if(NOT _buildutil_standard STREQUAL "")
  _buildutil_refuse_above_ceiling("${_buildutil_standard}" "${_buildutil_standard_source}")
endif()
set(BUILDUTIL_CXX_STANDARD "${_buildutil_standard}" CACHE INTERNAL
    "the resolved C++ standard of the project's own targets")

# Every module is a library first and foremost; the rest is
# presence-driven. A main.cpp is the one TU kept out of the library --
# it becomes the module's EXECUTABLE target -- bare name, the library
# taking ${target}-lib instead -- (built and installed under the
# module's own DIRECTORY name -- see _buildutil_module_app_name) that
# links the library, so application internals are testable
# like any other module code. Tests and benches attach exactly as they
# do everywhere else.
# Presence-driven configure-time codegen: a `configure.py` gets run at
# configure time, discovered by its own presence (same spirit as
# *.test.cpp / *.bench.cpp / *.patch -- zero config, just add the file),
# independent of CMakeLists.txt. Via the buildutil_configure API the script emits into
# a private build-tree dir and declares its inputs. Two homes:
#   * a MODULE dir (has CMakeLists) -> _buildutil_exec_configure runs it in
#     Init_submodule; its include roots + generated sources join the
#     module target.
#   * a GROUP dir (no CMakeLists) -> the scan runs it; the output is shared
#     across the subtree, so its include roots go on the current scope and
#     every descendant module inherits them. A group has no target of its
#     own; a compilable source it emits is routed to a module by PATH
#     STRUCTURE (see _buildutil_ancestor_group_sources).
# Guarantees either way:
#   * delete the dir -> its configure.py goes with it, nothing runs, the
#     build behaves as if it never existed (a stale output dir is inert);
#   * outputs live under _build/ (per-profile _build/<profile>/generated
#     /<name> and shared _build/generated/<name>), swept by clean;
#   * editing configure.py or any declared input re-runs codegen, since
#     they join CMAKE_CONFIGURE_DEPENDS.
function(_buildutil_exec_configure scope dir name out_incdirs out_sources)
  set(${out_incdirs} "" PARENT_SCOPE)
  set(${out_sources} "" PARENT_SCOPE)
  set(script "${dir}/configure.py")
  if(NOT EXISTS "${script}")
    return()
  endif()
  if(NOT Python3_Interpreter_FOUND)
    find_package(Python3 REQUIRED COMPONENTS Interpreter)
  endif()
  _buildutil_generated_roots("${name}" _roots)
  list(GET _roots 0 per_profile)
  list(GET _roots 1 shared)
  set(manifest "${per_profile}/.manifest")
  file(MAKE_DIRECTORY "${per_profile}" "${shared}")
  # The TARGET platform and the tag vocabulary ride in too: the output
  # root is a module directory, so a hook that emits into `ui.embed.<tag>/`
  # has to spell a tag this build actually knows, and one that emits a
  # platform-specific payload at all has to know which platform it is
  # building FOR -- never the host, cross builds included.
  _buildutil_target_system(_cfg_system)
  # comma-separated, not the cmake list separator: a ';' inside an
  # execute_process argument is one more argument, and quoting it past
  # `cmake -E env` is a fight nobody needs to have.
  string(REPLACE ";" "," _cfg_tags "${_buildutil_platform_tags}")
  # THE DECLARED TOOLS. A `Require(<x> ... TOOL)` package's bindir lands on
  # CMAKE_PROGRAM_PATH -- a cmake VARIABLE, which find_program consults and
  # no subprocess inherits. Without this a hook that runs a declared tool
  # finds whatever the machine happens to have on PATH, or nothing, and the
  # one mechanism this build system has for pinning a build-time executable
  # is the one place a configure hook could not use.
  #
  # '|'-separated: ';' is the cmake list separator and ':' is the PATH
  # separator on posix and a drive letter's friend on Windows, so neither
  # survives the trip both ways. A path holding '|' would break this and
  # has never existed.
  string(REPLACE ";" "|" _cfg_programs "${CMAKE_PROGRAM_PATH}")
  execute_process(
    COMMAND "${CMAKE_COMMAND}" -E env
            "CONFIGURE_OUTPUT_DIR=${per_profile}" "CONFIGURE_SHARED_DIR=${shared}"
            "CONFIGURE_MANIFEST=${manifest}" "PYTHONPATH=${BUILDUTIL_PYSUPPORT}"
            "CONFIGURE_SOURCE_DIR=${dir}" "CONFIGURE_MODULE=${name}"
            "CONFIGURE_TARGET_SYSTEM=${_cfg_system}"
            "CONFIGURE_PLATFORM_TAGS=${_cfg_tags}"
            "CONFIGURE_PROGRAM_PATH=${_cfg_programs}"
            "${Python3_EXECUTABLE}" "${script}"
    WORKING_DIRECTORY "${dir}"
    RESULT_VARIABLE result OUTPUT_VARIABLE output ERROR_VARIABLE errors)
  string(STRIP "${output}" output)
  if(NOT result EQUAL 0)
    message(FATAL_ERROR "${name}: configure.py failed\n${output}\n${errors}")
  endif()
  if(output)
    message(STATUS "${name}: ${output}")
  endif()
  set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${script}")
  set(gen_sources "")
  set(declared "")
  set(flagged "")
  if(EXISTS "${manifest}")
    file(STRINGS "${manifest}" lines)
    foreach(line IN LISTS lines)
      string(SUBSTRING "${line}" 0 1 tag)
      string(SUBSTRING "${line}" 2 -1 path)
      if(tag STREQUAL "G")
        list(APPEND declared "${path}")
        _buildutil_checked_in("${path}" "${dir}" checked_in)
        if(checked_in)
          continue()
        endif()
        set_source_files_properties("${path}" PROPERTIES GENERATED TRUE)
        if(path MATCHES "\\.(c|cc|cpp|cxx)$")
          list(APPEND gen_sources "${path}")
        endif()
      elseif(tag STREQUAL "D")
        set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${path}")
      elseif(tag STREQUAL "O" OR tag STREQUAL "M")
        string(FIND "${path}" "\t" tab)
        string(SUBSTRING "${path}" 0 ${tab} file)
        math(EXPR after "${tab} + 1")
        string(SUBSTRING "${path}" ${after} -1 flag)
        file(REAL_PATH "${file}" real)
        list(APPEND flagged "${real}")
        list(APPEND flags_${tag}_${real} "${flag}")
      elseif(tag STREQUAL "S")
        set_property(GLOBAL PROPERTY _buildutil_${scope}_soversion_${name} "${path}")
      elseif(tag STREQUAL "V")
        set_property(GLOBAL PROPERTY _buildutil_${scope}_version_${name} "${path}")
      endif()
    endforeach()
  endif()
  list(REMOVE_DUPLICATES flagged)
  foreach(real IN LISTS flagged)
    _buildutil_record_source_flags(${scope} "${dir}" "${name}" "${real}"
                                   "${flags_O_${real}}" "${flags_M_${real}}")
  endforeach()
  set_property(GLOBAL PROPERTY _buildutil_${scope}_declared_${name} "${declared}")
  set(${out_incdirs} "${per_profile}" "${shared}" PARENT_SCOPE)
  set(${out_sources} "${gen_sources}" PARENT_SCOPE)
endfunction()

# Where `path` sits inside `dir`'s own module, '' when outside it or in a
# nested module (a subdirectory with its own CMakeLists.txt).
function(_buildutil_inside_module path dir out)
  file(REAL_PATH "${dir}" real_dir)
  file(REAL_PATH "${path}" real_path)
  file(RELATIVE_PATH rel "${real_dir}" "${real_path}")
  set(${out} "" PARENT_SCOPE)
  if(rel MATCHES "^\\.\\./" OR IS_ABSOLUTE "${rel}")
    return()
  endif()
  cmake_path(GET real_path PARENT_PATH parent)
  while(NOT parent STREQUAL real_dir)
    if(EXISTS "${parent}/CMakeLists.txt")
      return()
    endif()
    cmake_path(GET parent PARENT_PATH parent)
  endwhile()
  set(${out} "${rel}" PARENT_SCOPE)
endfunction()

# A declared file of the hook's own module, outside its *.test/ subtrees,
# is checked in: the glob compiles it already, and only its flags are news.
function(_buildutil_checked_in path dir out)
  _buildutil_inside_module("${path}" "${dir}" rel)
  if(rel STREQUAL "" OR rel MATCHES "(^|/)[^/]*\\.test/")
    set(${out} FALSE PARENT_SCOPE)
  else()
    set(${out} TRUE PARENT_SCOPE)
  endif()
endfunction()

function(_buildutil_under_any path roots out)
  set(${out} FALSE PARENT_SCOPE)
  foreach(root IN LISTS roots)
    if(NOT EXISTS "${root}")
      continue()
    endif()
    file(REAL_PATH "${root}" real_root)
    file(RELATIVE_PATH rel "${real_root}" "${path}")
    if(NOT rel MATCHES "^\\.\\./" AND NOT IS_ABSOLUTE "${rel}")
      set(${out} TRUE PARENT_SCOPE)
      return()
    endif()
  endforeach()
endfunction()

# A hook's declare(path, options=, defines=), recorded by real path so the
# module that compiles the file finds it whichever spelling reached it. A
# group hook flags its own generated files, a module hook its module's
# sources and the generated files of its own and its groups' hooks; the
# last declare() of one hook replaces, and a second hook is refused.
function(_buildutil_record_source_flags scope dir name real options defines)
  _buildutil_generated_roots("${name}" roots)
  if(scope STREQUAL "module")
    _buildutil_ancestor_group_incdirs(group_roots)
    list(APPEND roots ${group_roots})
    _buildutil_inside_module("${real}" "${dir}" own)
  endif()
  _buildutil_under_any("${real}" "${roots}" generated)
  if(NOT generated AND "${own}" STREQUAL "")
    message(FATAL_ERROR
      "${dir}/configure.py declares compile flags for ${real}, which is "
      "neither a source of its own ${scope} nor a file its hooks generate.")
  endif()
  get_property(owner GLOBAL PROPERTY _buildutil_source_owner_${real})
  if(owner AND NOT owner STREQUAL "${dir}/configure.py")
    message(FATAL_ERROR
      "${real} has compile flags declared by two hooks:\n  ${owner}\n  "
      "${dir}/configure.py\nOne hook owns a file's flags.")
  endif()
  set_property(GLOBAL PROPERTY _buildutil_source_owner_${real} "${dir}/configure.py")
  set_property(GLOBAL PROPERTY _buildutil_source_O_${real} "${options}")
  set_property(GLOBAL PROPERTY _buildutil_source_M_${real} "${defines}")
endfunction()

# COMPILE_OPTIONS and COMPILE_DEFINITIONS on the files a module compiles,
# in the module's own directory scope, where its targets are.
function(_buildutil_apply_source_flags)
  foreach(file IN LISTS ARGN)
    file(REAL_PATH "${file}" real)
    get_property(options GLOBAL PROPERTY _buildutil_source_O_${real})
    get_property(defines GLOBAL PROPERTY _buildutil_source_M_${real})
    if(options OR defines)
      set_source_files_properties("${file}" PROPERTIES
        COMPILE_OPTIONS "${options}" COMPILE_DEFINITIONS "${defines}")
    endif()
  endforeach()
endfunction()

# A group-level configure.py: shared codegen for the whole subtree. Its
# include roots are recorded against the group dir; every module at or
# below that dir picks them up (_buildutil_ancestor_group_incdirs), so the
# generated headers are visible exactly within the subtree and nowhere
# else. Compilable sources it emits are recorded too, for the modules to
# claim by path (_buildutil_ancestor_group_sources).
function(_buildutil_run_group_configure dir)
  file(RELATIVE_PATH relative "${CMAKE_SOURCE_DIR}/sources" "${dir}")
  string(REPLACE "/" "_" name "${relative}")
  _buildutil_exec_configure(group "${dir}" "${name}" incdirs sources)
  get_property(soversion GLOBAL PROPERTY _buildutil_group_soversion_${name} SET)
  if(soversion)
    message(FATAL_ERROR
      "${dir}/configure.py declares soversion(), but ${dir} is a group: "
      "only a shared library module has a soname.")
  endif()
  set_property(GLOBAL APPEND PROPERTY @CMAKE_OPTION_PREFIX@_GROUP_CONFIGURES "${dir}")
  set_property(GLOBAL PROPERTY @CMAKE_OPTION_PREFIX@_GROUP_INCDIRS_${name} "${incdirs}")
  set_property(GLOBAL PROPERTY @CMAKE_OPTION_PREFIX@_GROUP_SOURCES_${name} "${sources}")
endfunction()

# The generated include roots of every group configure.py at or above the
# current module -- i.e. the subtree each group's shared codegen serves.
function(_buildutil_ancestor_group_incdirs out)
  set(result "")
  get_property(groups GLOBAL PROPERTY @CMAKE_OPTION_PREFIX@_GROUP_CONFIGURES)
  foreach(group_dir IN LISTS groups)
    file(RELATIVE_PATH inside "${group_dir}" "${CMAKE_CURRENT_SOURCE_DIR}")
    if(NOT inside MATCHES "^\\.\\.")           # under (or equal to) the group dir
      file(RELATIVE_PATH relative "${CMAKE_SOURCE_DIR}/sources" "${group_dir}")
      string(REPLACE "/" "_" name "${relative}")
      get_property(incdirs GLOBAL PROPERTY @CMAKE_OPTION_PREFIX@_GROUP_INCDIRS_${name})
      list(APPEND result ${incdirs})
    endif()
  endforeach()
  set(${out} "${result}" PARENT_SCOPE)
endfunction()

# Group-generated COMPILABLE sources claimed by the current module. The
# emit path mirrors the static source tree (a leading _private_/ aside),
# so a generated _private_/x86/decode/foo.test.cpp belongs to the module
# at sources/x86/decode -- same ownership-by-path as a static file, and
# the test/bench presence rules apply identically downstream. A source
# whose path mirrors no module is include-only.
function(_buildutil_ancestor_group_sources out)
  set(result "")
  file(RELATIVE_PATH module_rel
    "${CMAKE_SOURCE_DIR}/sources" "${CMAKE_CURRENT_SOURCE_DIR}")
  get_property(groups GLOBAL PROPERTY @CMAKE_OPTION_PREFIX@_GROUP_CONFIGURES)
  foreach(group_dir IN LISTS groups)
    file(RELATIVE_PATH inside "${group_dir}" "${CMAKE_CURRENT_SOURCE_DIR}")
    if(inside MATCHES "^\\.\\.")               # module not under this group
      continue()
    endif()
    file(RELATIVE_PATH relative "${CMAKE_SOURCE_DIR}/sources" "${group_dir}")
    string(REPLACE "/" "_" name "${relative}")
    get_property(roots       GLOBAL PROPERTY @CMAKE_OPTION_PREFIX@_GROUP_INCDIRS_${name})
    get_property(gen_sources GLOBAL PROPERTY @CMAKE_OPTION_PREFIX@_GROUP_SOURCES_${name})
    foreach(path IN LISTS gen_sources)
      foreach(root IN LISTS roots)             # first root containing it decides
        file(RELATIVE_PATH rel "${root}" "${path}")
        if(rel MATCHES "^\\.\\.")
          continue()
        endif()
        string(REGEX REPLACE "^_private_/" "" rel "${rel}")
        string(FIND "${rel}" "${module_rel}/" at)
        if(at EQUAL 0)
          list(APPEND result "${path}")
        endif()
        break()
      endforeach()
    endforeach()
  endforeach()
  set(${out} "${result}" PARENT_SCOPE)
endfunction()

# Runtime data by presence: a module's *.install/ trees are PREFIX-ROOTED
# overlays. `data.install/x/y/z` is staged to <build>/x/y/z and
# in-tree runs find it next to the programs, and installs as bin/x/y/z
# with the structure preserved. The directory existing IS the
# declaration -- no call, no list, nothing to keep in step with the
# filesystem.
#
# CONFIGURE_DEPENDS is not optional here. Without it a data file added to
# the directory is copied by nobody: the build stays green and the file
# simply is not there at run time, which is the failure mode this
# convention exists to remove rather than relocate.
#
# Same suffix-tag rule as *.test/, *.bench/ and *.patch -- a module may
# have several, named for what they hold (locale.install/, tables.install/)
# rather than one anonymous dot-directory.
#
# Platform tags apply here, on the DIRECTORY: `locale.install/` is the
# base, `locale.install.win32/` overlays it on Windows, by shipped path.
# Two phases, and the split is the point -- every applicable tree is
# MERGED first, so only the winner per path is ever staged or installed.
# Emitting as we walked would put both copies at the same destination
# and let install order decide, which is exactly the invented rule the
# same-specificity refusal exists to avoid.
function(_buildutil_install_runtime_data target)
  _buildutil_platform_data_dirs("${CMAKE_CURRENT_SOURCE_DIR}" "install"
                                _rd_dirs _rd_levels)
  # The shadow tree is a source tree: a generator that writes
  # generated/<module>/data.install/ ships that data exactly as the
  # module's own data.install/ does, tags and overlay levels included.
  _buildutil_generated_data_dirs("${target}" "install"
                                 _rd_sh_dirs _rd_sh_levels)
  list(APPEND _rd_dirs ${_rd_sh_dirs})
  list(APPEND _rd_levels ${_rd_sh_levels})
  list(LENGTH _rd_dirs _rd_count)
  if(_rd_count EQUAL 0)
    return()
  endif()
  set(_rd_names "")
  set(_rd_files "")
  set(_rd_owners "")
  set(_rd_lv "")
  math(EXPR _rd_last "${_rd_count} - 1")
  foreach(_rd_i RANGE ${_rd_last})
    list(GET _rd_dirs ${_rd_i} _rd_root)
    list(GET _rd_levels ${_rd_i} _rd_level)
    _buildutil_install_one_runtime_tree("${_rd_root}" "${_rd_level}"
      _rd_names _rd_files _rd_owners _rd_lv)
  endforeach()
  _buildutil_emit_runtime_data("${_rd_names}" "${_rd_files}")
endfunction()

# MERGE phase: one *.install/ tree folded into the four parallel lists.
function(_buildutil_install_one_runtime_tree root level
         io_names io_files io_owners io_levels)
  set(_it_names "${${io_names}}")
  set(_it_files "${${io_files}}")
  set(_it_owners "${${io_owners}}")
  set(_it_levels "${${io_levels}}")
  file(GLOB_RECURSE data CONFIGURE_DEPENDS "${root}/*")
  foreach(f IN LISTS data)
    if(IS_DIRECTORY "${f}")
      continue()
    endif()
    file(RELATIVE_PATH rel "${root}" "${f}")
    _buildutil_overlay_one(
      "runtime data of module at ${CMAKE_CURRENT_SOURCE_DIR}"
      "${rel}" "${f}" "${root}" "${level}"
      _it_names _it_files _it_owners _it_levels)
  endforeach()
  set(${io_names} "${_it_names}" PARENT_SCOPE)
  set(${io_files} "${_it_files}" PARENT_SCOPE)
  set(${io_owners} "${_it_owners}" PARENT_SCOPE)
  set(${io_levels} "${_it_levels}" PARENT_SCOPE)
endfunction()

# One staged runtime file, in BOTH in-tree locations.
#
# The overlay is prefix-rooted, so the build root is where the install
# tree's shape is reproduced -- but executables link into <build>/bin,
# and a program that resolves its assets relative to its own executable
# (the Pascal-parity shape, and the only one a dlopen'ed library's
# neighbours can use) looked beside the binary and found nothing.
# Installed, the same lookup works, because there the app sits at the
# prefix root with the data. Mirroring under bin/ makes the in-tree run
# answer the way the installed one does; both copies are the build
# system's, so the duplication costs a configure-time copy and no
# decision by anyone reading the tree.
function(_buildutil_stage_runtime_file rel src)
  configure_file("${src}" "${CMAKE_BINARY_DIR}/${rel}" COPYONLY)
  configure_file("${src}" "${CMAKE_BINARY_DIR}/bin/${rel}" COPYONLY)
endfunction()

# EMIT phase: the winners, staged over the build root and installed.
function(_buildutil_emit_runtime_data names files)
  list(LENGTH names count)
  if(count EQUAL 0)
    return()
  endif()
  math(EXPR last "${count} - 1")
  foreach(i RANGE ${last})
    list(GET names ${i} rel)
    list(GET files ${i} f)
    get_filename_component(rel_dir "${rel}" DIRECTORY)
    # a PREFIX-ROOTED overlay: the path inside the *.install tree IS the
    # shipped path -- data.install/a/b/c lands at <prefix>/a/b/c, so a
    # module places data anywhere in the install tree by mirroring that
    # location, and no destination rule exists to learn.
    _buildutil_stage_runtime_file("${rel}" "${f}")
    if(rel_dir)
      install(FILES "${f}" DESTINATION "${rel_dir}")
    else()
      install(FILES "${f}" DESTINATION ".")
    endif()
  endforeach()
endfunction()

# Every object of every OBJECT library an executable or a suite depends on,
# however far away, put on its link line.
#
# cmake propagates an OBJECT library's objects to DIRECT consumers only.
# Measured: exe -> static lib -> obj library loses them, because they are
# archived into the static library and then dropped as ordinary archive
# members. So "linking an obj module takes all its objects" is false one
# hop away unless buildutil makes it true, and the failure is invisible --
# the build is green and a symbol resolved at runtime is simply absent.
#
# Deferred to the end of the WHOLE tree, not this directory: a module may
# depend on one the scan has not reached yet, and at that point the
# target does not exist to be walked.
# Runs ONCE, over every executable and suite, at the end of the whole tree.
#
# The app names travel in a global property rather than as deferred-call
# arguments: cmake expands those in the DEFERRED scope, where a function
# local like ${target} no longer exists, so the first version ran with an
# empty name and silently attached nothing.
function(_buildutil_attach_all_object_deps)
  get_property(apps GLOBAL PROPERTY _buildutil_apps)
  foreach(app IN LISTS apps)
    _buildutil_attach_object_deps("${app}")
  endforeach()
  get_property(suites GLOBAL PROPERTY _buildutil_suites)
  foreach(suite IN LISTS suites)
    _buildutil_attach_object_deps("${suite}")
  endforeach()
endfunction()

# Every cmake target reachable from `seed` through link edges.
function(_buildutil_link_closure seed out)
  set(queue "${seed}")
  set(seen "")
  while(queue)
    list(POP_FRONT queue current)
    if(current IN_LIST seen OR NOT TARGET ${current})
      continue()
    endif()
    list(APPEND seen "${current}")
    foreach(prop LINK_LIBRARIES INTERFACE_LINK_LIBRARIES)
      get_target_property(libs ${current} ${prop})
      if(libs)
        foreach(lib IN LISTS libs)
          if(TARGET ${lib})
            list(APPEND queue "${lib}")
          endif()
        endforeach()
      endif()
    endforeach()
  endwhile()
  set(${out} "${seen}" PARENT_SCOPE)
endfunction()

function(_buildutil_attach_object_deps app)
  if(NOT TARGET ${app})
    return()
  endif()
  # an entry-from-closure app takes every object of EVERY member -- its
  # main() is an archive member nothing references, the exact thing a
  # normal link drops; an ordinary app takes only the OBJECT libraries
  get_property(closure_apps GLOBAL PROPERTY _buildutil_closure_entry_apps)
  if(app IN_LIST closure_apps)
    set(want_types "OBJECT_LIBRARY;STATIC_LIBRARY")
  else()
    set(want_types "OBJECT_LIBRARY")
  endif()
  _buildutil_link_closure("${app}" seen)
  set(objects "")
  foreach(current IN LISTS seen)
    get_target_property(type ${current} TYPE)
    if(type IN_LIST want_types)
      list(APPEND objects "$<TARGET_OBJECTS:${current}>")
    endif()
  endforeach()
  if(objects)
    target_sources(${app} PRIVATE ${objects})
  endif()
endfunction()

function(_buildutil_schedule_object_sweep)
  get_property(scheduled GLOBAL PROPERTY _buildutil_obj_sweep_scheduled)
  if(NOT scheduled)
    set_property(GLOBAL PROPERTY _buildutil_obj_sweep_scheduled TRUE)
    cmake_language(DEFER DIRECTORY "${CMAKE_SOURCE_DIR}"
      CALL _buildutil_attach_all_object_deps)
  endif()
endfunction()

function(_buildutil_track_suite suite)
  set_property(GLOBAL APPEND PROPERTY _buildutil_suites "${suite}")
  _buildutil_schedule_object_sweep()
endfunction()

# A module's non-module links as two JSON lists: the host targets a SYSTEM
# find imported, which package_info() maps onto their wrappers, and the rest.
function(_buildutil_manifest_externals module external_out host_out)
  get_property(externals GLOBAL PROPERTY _buildutil_cmp_external_${module})
  get_property(host_imports GLOBAL PROPERTY _buildutil_host_imports)
  set(external "")
  set(host "")
  foreach(item IN LISTS externals)
    if(item IN_LIST host_imports)
      list(APPEND host "\"${item}\"")
    else()
      list(APPEND external "\"${item}\"")
    endif()
  endforeach()
  list(JOIN external ", " external)
  list(JOIN host ", " host)
  set(${external_out} "${external}" PARENT_SCOPE)
  set(${host_out} "${host}" PARENT_SCOPE)
endfunction()

# Writes the component manifest the conan recipe reads. Deferred to the
# end of the tree so every module and every Link_dependencies edge exists.
function(_buildutil_write_component_manifest)
  get_property(_wc_mods GLOBAL PROPERTY _buildutil_components)
  if(NOT _wc_mods)
    return()
  endif()
  set(_wc_json "{\n  \"components\": [")
  set(_wc_first TRUE)
  foreach(_wc_m IN LISTS _wc_mods)
    get_property(_wc_path GLOBAL PROPERTY _buildutil_cmp_path_${_wc_m})
    get_property(_wc_lib GLOBAL PROPERTY _buildutil_cmp_lib_${_wc_m})
    get_property(_wc_needs GLOBAL PROPERTY _buildutil_cmp_needs_${_wc_m})
    set(_wc_need_json "")
    foreach(_wc_n IN LISTS _wc_needs)
      get_property(_wc_np GLOBAL PROPERTY _buildutil_cmp_path_${_wc_n})
      if(_wc_np)
        list(APPEND _wc_need_json "\"${_wc_np}\"")
      endif()
    endforeach()
    _buildutil_manifest_externals(${_wc_m} _wc_ext_json _wc_host_json)
    list(JOIN _wc_need_json ", " _wc_need_json)
    if(NOT _wc_first)
      string(APPEND _wc_json ",")
    endif()
    set(_wc_first FALSE)
    string(APPEND _wc_json
      "\n    {\"path\": \"${_wc_path}\", \"lib\": \"${_wc_lib}\""
      ", \"needs\": [${_wc_need_json}]"
      ", \"external\": [${_wc_ext_json}]"
      ", \"host\": [${_wc_host_json}]}")
  endforeach()
  string(APPEND _wc_json "\n  ]\n}\n")
  set(_wc_out "${CMAKE_BINARY_DIR}/buildutil-components.json")
  file(WRITE "${_wc_out}" "${_wc_json}")
  install(FILES "${_wc_out}" DESTINATION "share/buildutil")
endfunction()

# Include roots, compile flags and extension hook of a module library.
function(_buildutil_configure_module_library lib target gen_incdirs)
  set_target_properties(${lib} PROPERTIES POSITION_INDEPENDENT_CODE ON)
  target_include_directories(${lib} PUBLIC "${CMAKE_SOURCE_DIR}/sources")
  # The PARENT generated root is universal, exactly as sources/ is: a
  # qualified #include "<module>/foo.gh" then resolves from anywhere
  # with nothing declared. That covers the readers who CANNOT link the
  # producer -- a tool consuming a sibling's generated tables, where
  # linking would be a dependency cycle -- which previously needed a
  # project-local override to reach at all.
  _buildutil_add_generated_roots(${lib} PUBLIC
    "${CMAKE_BINARY_DIR}/generated" "${CMAKE_SOURCE_DIR}/_build/generated"
    ${gen_incdirs})
  target_include_directories(${lib}
    ${_buildutil_own_dir_scope} "${CMAKE_CURRENT_SOURCE_DIR}")
  _buildutil_add_link_pools(${lib} ${_buildutil_own_dir_scope})
  _buildutil_apply_compile_flags(${lib} PUBLIC)
  # Extension hook: once per module, AFTER its include roots and flags are
  # set, so an extension adds to a module that is otherwise finished.
  if(COMMAND _buildutil_ext_module)
    _buildutil_ext_module("${lib}" "${target}")
  endif()
endfunction()

# A .test module: an OBJECT library where suites build, never shipped.
function(_buildutil_init_test_module target lib gen_incdirs suites publish)
  _buildutil_refuse_test_module_suites("${suites}" ${publish})
  if(NOT BUILD_TESTING AND NOT BUILD_BENCHMARKING)
    return()
  endif()
  add_library(${lib} OBJECT ${ARGN})
  target_link_libraries(${lib} PUBLIC GTest::gtest)
  _buildutil_configure_module_library(${lib} ${target} "${gen_incdirs}")
  _buildutil_module_alias(alias)
  if(alias)
    add_library(${alias} ALIAS ${lib})
  endif()
  _buildutil_add_resources(${target})
  _buildutil_add_embedded_resources(${target})
endfunction()

# A .test module has no suite of its own and ships nothing.
function(_buildutil_refuse_test_module_suites files publish)
  if(publish)
    message(FATAL_ERROR
      "${CMAKE_CURRENT_SOURCE_DIR} is a .test module and calls "
      "Init_submodule(PUBLISH_SYMBOLS): it is linked into suites, never "
      "loaded, so there is nothing to publish. Drop PUBLISH_SYMBOLS.")
  endif()
  _buildutil_python_suite_files(_ts_python "${CMAKE_CURRENT_SOURCE_DIR}")
  _buildutil_python_suite_dirs(_ts_suites "${CMAKE_CURRENT_SOURCE_DIR}")
  _buildutil_platform_data_dirs("${CMAKE_CURRENT_SOURCE_DIR}" "install"
                                _ts_data _ts_levels)
  set(_ts_found ${files} ${_ts_python} ${_ts_suites} ${_ts_data})
  if(_ts_found)
    string(REPLACE ";" "\n  " _ts_pretty "${_ts_found}")
    message(FATAL_ERROR
      "${CMAKE_CURRENT_SOURCE_DIR} is a .test module, which has no suite "
      "and ships nothing, but holds:\n  ${_ts_pretty}\nMove test cases "
      "into a suite that links it under TEST (or BENCH), and runtime data "
      "and python bridges into the module that ships them.")
  endif()
endfunction()

# A `*.test/` holding a CMakeLists.txt in a module: test sources or a module?
function(_buildutil_refuse_inner_test_modules)
  file(GLOB_RECURSE _it_lists "${CMAKE_CURRENT_SOURCE_DIR}/*/CMakeLists.txt")
  foreach(_it_list IN LISTS _it_lists)
    file(RELATIVE_PATH _it_rel "${CMAKE_CURRENT_SOURCE_DIR}" "${_it_list}")
    get_filename_component(_it_dir "${_it_rel}" DIRECTORY)
    string(REPLACE "/" ";" _it_parts "${_it_dir}")
    foreach(_it_part IN LISTS _it_parts)
      _buildutil_kind_of("${_it_part}" _it_name _it_kind)
      if(_it_kind STREQUAL "test")
        message(FATAL_ERROR
          "${CMAKE_CURRENT_SOURCE_DIR}/${_it_dir} holds a CMakeLists.txt "
          "inside the module ${CMAKE_CURRENT_SOURCE_DIR}, so it reads both "
          "as that module's test sources and as a test-lane module. Move it "
          "out of the module to make it a module, or delete its "
          "CMakeLists.txt to keep it as test sources.")
      endif()
    endforeach()
  endforeach()
endfunction()

# One conan component per module, for the recipe that cannot see the graph.
function(_buildutil_register_component target)
  _buildutil_clean_path_parts("${CMAKE_CURRENT_SOURCE_DIR}" _cmp_clean)
  list(JOIN _cmp_clean "/" _cmp_rel)
  _buildutil_module_app_name(_cmp_leaf)
  set_property(GLOBAL APPEND PROPERTY _buildutil_components "${target}")
  set_property(GLOBAL PROPERTY _buildutil_cmp_path_${target} "${_cmp_rel}")
  set_property(GLOBAL PROPERTY _buildutil_cmp_lib_${target} "${_cmp_leaf}")
  get_property(_cmp_scheduled GLOBAL PROPERTY _buildutil_cmp_scheduled)
  if(NOT _cmp_scheduled)
    set_property(GLOBAL PROPERTY _buildutil_cmp_scheduled TRUE)
    cmake_language(DEFER DIRECTORY "${CMAKE_SOURCE_DIR}"
      CALL _buildutil_write_component_manifest)
  endif()
endfunction()

# Headers ship at their sources/-relative spelling; `_` keeps one private.
function(_buildutil_install_module_headers)
  file(GLOB_RECURSE _hdr_any CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/*.h" "${CMAKE_CURRENT_SOURCE_DIR}/*.hpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/*.hxx" "${CMAKE_CURRENT_SOURCE_DIR}/*.hh"
    "${CMAKE_CURRENT_SOURCE_DIR}/*.inl" "${CMAKE_CURRENT_SOURCE_DIR}/*.ipp")
  if(_hdr_any)
    file(RELATIVE_PATH _hdr_rel
      "${CMAKE_SOURCE_DIR}/sources" "${CMAKE_CURRENT_SOURCE_DIR}")
    # A nested module installs its own headers under its OWN destination.
    # Without this its subtree would also ship from here, putting the same
    # file at two paths.
    set(_hdr_excl "")
    file(GLOB_RECURSE _hdr_nested CONFIGURE_DEPENDS
      "${CMAKE_CURRENT_SOURCE_DIR}/*/CMakeLists.txt")
    foreach(_hdr_n IN LISTS _hdr_nested)
      get_filename_component(_hdr_n "${_hdr_n}" DIRECTORY)
      file(RELATIVE_PATH _hdr_n "${CMAKE_CURRENT_SOURCE_DIR}" "${_hdr_n}")
      list(APPEND _hdr_excl REGEX "(^|/)${_hdr_n}(/|$)" EXCLUDE)
    endforeach()
    install(DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/"
      DESTINATION "include/${_hdr_rel}"
      FILES_MATCHING
        PATTERN "*.h" PATTERN "*.hpp" PATTERN "*.hxx"
        PATTERN "*.hh" PATTERN "*.inl" PATTERN "*.ipp"
        PATTERN "_*" EXCLUDE
        PATTERN "*.test" EXCLUDE
        PATTERN "*.bench" EXCLUDE
        PATTERN "*.install" EXCLUDE
        PATTERN "*.embed" EXCLUDE
        REGEX "(^|/)[^/]*\\.(install|embed)\\.(@PLATFORM_TAGS@)(/|$)" EXCLUDE
        PATTERN ".*" EXCLUDE
        ${_hdr_excl})
  endif()
endfunction()

function(Init_submodule)
  cmake_parse_arguments(MOD "PUBLISH_SYMBOLS" "STANDARD" "" ${ARGN})
  _buildutil_module_name(target)
  _buildutil_module_standard("${target}" "${MOD_STANDARD}"
    "${MOD_KEYWORDS_MISSING_VALUES}" cxx_standard)
  _buildutil_refuse_inner_test_modules()
  _buildutil_split_sources(sources tests benches)
  _buildutil_exec_configure(module "${CMAKE_CURRENT_SOURCE_DIR}" "${target}" gen_incdirs gen_sources)
  _buildutil_ancestor_group_incdirs(group_incdirs)
  _buildutil_ancestor_group_sources(group_sources)
  list(APPEND gen_incdirs ${group_incdirs})
  list(APPEND gen_sources ${group_sources})
  _buildutil_split_source_list("${gen_sources}" gen_srcs gen_tests gen_benches)
  foreach(globbed IN ITEMS sources tests benches)
    if(${globbed})
      list(REMOVE_ITEM gen_srcs ${${globbed}})
      list(REMOVE_ITEM gen_tests ${${globbed}})
      list(REMOVE_ITEM gen_benches ${${globbed}})
    endif()
  endforeach()
  set(entry ${sources})
  list(FILTER entry   INCLUDE REGEX "/main\\.cpp$")
  list(FILTER sources EXCLUDE REGEX "/main\\.cpp$")
  set(pybind_entry ${sources})
  list(FILTER pybind_entry INCLUDE REGEX "\\.pybind\\.cpp$")
  list(FILTER sources      EXCLUDE REGEX "\\.pybind\\.cpp$")
  list(APPEND sources ${gen_srcs})
  # The include roots a module target carries:
  #   * sources/ -- the qualified spelling (<module>/foo.h) from anywhere;
  #   * the module's OWN directory -- because a quoted #include searches the
  #     INCLUDING file's directory, and plenty of a module's translation units
  #     do not sit next to its headers: configure.py output under
  #     generated/<module>, group codegen claimed by path, a source in a nested
  #     subdir, and the test/bench TUs of a *.test/ / *.bench/ subtree. Without
  #     this every one of those has to spell the include qualified, or the
  #     module has to repeat this very line in its own CMakeLists -- which is
  #     what projects were doing, once per module, verbatim. It goes LAST --
  #     after sources/ AND after the codegen roots -- so the claim "adding a
  #     root can only make a previously-FAILING include resolve" is actually
  #     true. Ahead of gen_incdirs it would instead re-point includes that
  #     already resolved: a module holding a checked-in tables.h whose
  #     configure.py also emits one into generated/<module>/ would silently
  #     flip to the checked-in copy for every angle include and for every TU
  #     outside the module dir -- no diagnostic, wrong header.
  #   * gen_incdirs -- this module's own codegen roots plus every ancestor
  #     group's shared ones.
  #
  # SCOPE: the module's own dir is PRIVATE -- a BUILD requirement of this
  # module, never a usage requirement of its consumers. A consumer reaches
  # these headers the one supported way, qualified through sources/
  # (<module>/foo.h). Exporting the dir instead would put EVERY linked
  # module's directory on EVERY consumer's include path, so an unqualified
  # #include "types.h" that used to be a hard compile error would silently
  # resolve to whichever linked module happens to come first IN LINK ORDER
  # -- invisible, order-dependent, and a module's internal headers leaking
  # tree-wide. sources/ and the codegen roots stay PUBLIC: those ARE the
  # module's published surface. The targets that need the private root but
  # do not compile INTO this library -- the -tests / -benches executables --
  # restate it themselves; see _buildutil_add_test_target.
  #
  # A PORTED tree can invert this, ONCE, in buildutil.toml:
  # [cmake] export_module_headers = true. A legacy codebase includes across
  # modules unqualified from thousands of call sites it is not going to
  # rewrite, and the alternative was every module repeating a raw
  # target_include_directories(<module> PUBLIC .) -- per-module boilerplate
  # for a project-wide fact. The hazard above is real and does not go away
  # when you opt in; the tree that opts in already lives with it. New
  # projects never see it: the default is off, tree-wide, and there is no
  # per-module override in either direction.
  # The LIBRARY's target name: bare when this module has no entry point,
  # decorated when it does, because the executable takes the bare name.
  _buildutil_library_of("${target}" lib)
  # What this module BUILDS, from the directory name and from what is in
  # it. Tag and presence must agree: a tag that contradicts the files is a
  # configure error naming both, never a silent precedence -- the whole
  # value of reading the tree with `ls` is that the listing cannot lie.
  _buildutil_kind_of("${CMAKE_CURRENT_SOURCE_DIR}" _ignored_name kind)
  set(shared_entry ${sources})
  list(FILTER shared_entry INCLUDE REGEX "/main\\.(so|dll|dylib)\\.cpp$")
  if(entry AND shared_entry)
    message(FATAL_ERROR
      "${CMAKE_CURRENT_SOURCE_DIR} holds both main.cpp and a "
      "main.<so|dll|dylib>.cpp: it cannot be an executable and a shared "
      "library at once.")
  endif()
  if(shared_entry)
    if(kind AND NOT kind STREQUAL "so")
      message(FATAL_ERROR
        "${CMAKE_CURRENT_SOURCE_DIR} is tagged .${kind} but holds a "
        "main.<so|dll|dylib>.cpp, which says shared library. Remove one.")
    endif()
    set(kind "so")
  endif()
  if(entry AND kind AND NOT kind STREQUAL "exe")
    message(FATAL_ERROR
      "${CMAKE_CURRENT_SOURCE_DIR} is tagged .${kind} but holds a "
      "main.cpp, which says executable. Remove one -- a listing that "
      "disagrees with the files is worse than no listing.")
  endif()
  # An .exe module with NO main.cpp declares its entry point lives in the
  # LINK CLOSURE -- a library it depends on carries main(). cmake needs
  # the symbol at link, and an archive member nothing references is
  # dropped, so such an app links EVERY OBJECT of its whole closure (the
  # obj-kind treatment applied to the closure, at end of tree when the
  # closure is knowable). The consumer's case: the debugger's entry is
  # libgui's guixmain.cpp.
  set(entry_from_closure FALSE)
  if(kind STREQUAL "exe" AND NOT entry)
    set(entry_from_closure TRUE)
    set(entry "${CMAKE_BINARY_DIR}/generated/_buildutil/empty.cpp")
    if(NOT EXISTS "${entry}")
      file(WRITE "${entry}"
        "// Generated by buildutil: an entry-from-closure app has no TU of\n"
        "// its own; the entry point arrives with the closure's objects.\n")
    endif()
  endif()

  # A module with nothing of its own to compile -- header-only, or one
  # whose single TU is main.cpp -- still gets a REAL library. cmake wants
  # at least one source for that, so it gets an empty one.
  #
  # The alternative, an INTERFACE library, answers differently to every
  # question a helper asks: it refuses PRIVATE scope, compiles nothing,
  # and makes the consumer compile everything. That forced every project
  # helper touching "the thing this module compiles into" to test the
  # target's type first and pick a scope -- a dispatch that is not
  # project logic but an unanswered question about buildutil's own target
  # model, copied into every project that met it.
  #
  # It also removes an asymmetry recorded here as accepted: an INTERFACE
  # library has no scope but INTERFACE, so a header-only module's own
  # directory necessarily reached its consumers, while every other
  # module's stayed PRIVATE. Same rule for every module now.
  #
  # CONSEQUENCE worth stating: a consumer that included a header-only
  # module's headers UNQUALIFIED was relying on that leak, and now needs
  # the supported spelling (<module>/foo.h) like everyone else. Trees with
  # [cmake] export_module_headers on are unaffected.
  set(compiles_nothing FALSE)
  if(NOT sources)
    set(compiles_nothing TRUE)
    set(empty_tu "${CMAKE_BINARY_DIR}/generated/_buildutil/empty.cpp")
    if(NOT EXISTS "${empty_tu}")
      file(WRITE "${empty_tu}"
        "// Generated by buildutil: gives a module with no sources of its\n"
        "// own a real library target, so every module answers the same\n"
        "// way about scope. See Init_submodule.\n")
    endif()
    list(APPEND sources "${empty_tu}")
  endif()
  _buildutil_apply_source_flags(${sources} ${entry} ${pybind_entry} ${tests}
                                ${benches} ${gen_tests} ${gen_benches})
  _buildutil_find_version_script(${target} "${kind}" "${shared_entry}"
                                 version_script)
  _buildutil_refuse_unshared_soversion(${target} "${kind}")
  # OBJECT: every object reaches whoever links this module, including the
  # ones nothing references -- for code that is resolved against at
  # runtime rather than at link time. SHARED: the module IS the loadable
  # artifact. STATIC otherwise, which is the default nothing has to say.
  if(kind STREQUAL "test")
    _buildutil_init_test_module(${target} ${lib} "${gen_incdirs}"
      "${tests};${benches};${gen_tests};${gen_benches};${pybind_entry}"
      ${MOD_PUBLISH_SYMBOLS} ${sources})
    _buildutil_set_cxx_standard("${cxx_standard}" ${lib})
    return()
  endif()
  if(kind STREQUAL "obj")
    add_library(${lib} OBJECT ${sources})
  elseif(kind STREQUAL "so")
    add_library(${lib} SHARED ${sources})
    # the LEAF names the file, exactly as for apps: the mirror puts the
    # artifact in its source-path directory, so the joined target name in
    # the filename would repeat the path the directory already states
    _buildutil_module_app_name(_so_name)
    set_target_properties(${lib} PROPERTIES OUTPUT_NAME "${_so_name}")
    _buildutil_apply_soversion(${lib})
    _buildutil_mirror_parent(_mirror)
    install(TARGETS ${lib} LIBRARY DESTINATION "${_mirror}" RUNTIME DESTINATION "${_mirror}")
    _buildutil_apply_exports(${lib} ${target} "${version_script}")
  elseif(BUILDUTIL_MODULE_LINKAGE STREQUAL "shared" AND kind STREQUAL "")
    # the module_linkage CONFIG option (buildutil config): UNTAGGED
    # modules — the ones that did not declare a kind — build shared.
    # A declaration (.lib/.a stays static, .obj stays object) always
    # beats the policy; this branch is only ever reached by the
    # default. Visibility stays HIDDEN
    # (owner ruling): only annotated symbols (or a PUBLISH_SYMBOLS
    # module) export — no export-everything Linux default here.
    add_library(${lib} SHARED ${sources})
    _buildutil_module_app_name(_shared_leaf)
    set_target_properties(${lib} PROPERTIES OUTPUT_NAME "${_shared_leaf}")
    _buildutil_mirror_parent(_mirror)
    install(TARGETS ${lib} LIBRARY DESTINATION "${_mirror}" RUNTIME DESTINATION "${_mirror}")
  else()
    add_library(${lib} STATIC ${sources})
    # A LIBRARY-kind package ([package] in buildutil.toml, rendered
    # here as a constant) ships its static archives at the mirror,
    # leaf-named like every other artifact: sources/a/b/c ->
    # <prefix>/a/b/libc.a. OFF everywhere else, so no pre-0.47
    # project's install output moves. ARCHIVE_OUTPUT_NAME touches only
    # the .a — the cmake target keeps the joined name, and an app
    # sharing the leaf collides with nothing (different file names).
    # ... but only when there is an object in it. The empty TU above
    # gives a source-less module a real target to answer questions with;
    # the archive built from it holds nothing, nothing can link it, and
    # shipping it makes package_info() advertise a library that is not
    # one.
    if(@PACKAGE_LIBS@ AND NOT compiles_nothing)
      _buildutil_module_app_name(_pkg_leaf)
      set_target_properties(${lib} PROPERTIES
        ARCHIVE_OUTPUT_NAME "${_pkg_leaf}")
      _buildutil_mirror_parent(_pkg_mirror)
      install(TARGETS ${lib} ARCHIVE DESTINATION "${_pkg_mirror}")
    endif()
  endif()
  # the PUBLISH_SYMBOLS hatch covers the module LIBRARY too — for a
  # shared default module it is the explicit "export everything" say-so
  # (never the silent Linux default); inert for a static archive
  if(MOD_PUBLISH_SYMBOLS)
    target_compile_options(${lib} PRIVATE
      $<$<NOT:$<CXX_COMPILER_ID:MSVC>>:-fvisibility=default>)
    set_target_properties(${lib} PROPERTIES WINDOWS_EXPORT_ALL_SYMBOLS ON
                          _buildutil_publish_symbols ON)
  endif()
  _buildutil_configure_module_library(${lib} ${target} "${gen_incdirs}")
  _buildutil_register_component(${target})
  _buildutil_install_module_headers()
  _buildutil_module_alias(alias)
  if(alias)
    add_library(${alias} ALIAS ${lib})
  endif()
  if(entry)
    add_executable(${target} ${entry})
    _buildutil_module_app_name(app_name)
    # Applications land in ONE place, <build>/bin, never in the module's
    # own binary dir. The first reason is a hard failure: cmake's ninja
    # generator gives an INTERFACE library target a phony at
    # <binary_dir>/<target>, which is the exact path an app named after
    # its module links to -- so a module whose only source is main.cpp
    # (INTERFACE library: nothing left to compile into one) made ninja
    # refuse to generate the whole tree with "multiple rules generate
    # sources/wfl/wfl". The second is that <build>/bin and _install/bin
    # now hold the same names, so a build-tree binary and an installed
    # one are found the same way.
    #
    # Two modules cannot ship the same app name: install() flattens them
    # into one bin/ and the later one would silently win. Catch it here,
    # naming both modules, rather than at generate time as an opaque
    # "multiple rules generate bin/rc".
    get_property(app_owner GLOBAL PROPERTY _buildutil_app_${app_name})
    if(app_owner)
      message(FATAL_ERROR
        "modules '${app_owner}' and '${target}' both ship an application "
        "named '${app_name}'. An application is installed under its own "
        "directory name, so rename one of the module directories -- "
        "grouping keeps the cmake targets apart but not the binaries.")
    endif()
    set_property(GLOBAL PROPERTY _buildutil_app_${app_name} "${target}")
    set_target_properties(${target} PROPERTIES
      OUTPUT_NAME "${app_name}"
      RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")
    # [bundle.macos]: before the install rule below, which cmake REFUSES
    # at generate time for a MACOSX_BUNDLE target with no BUNDLE
    # DESTINATION -- the exact reason a project could not just set the
    # property itself.
    _buildutil_mark_macos_bundle(${target})
    target_link_libraries(${target} PRIVATE ${lib})
    # HOSTS_PLUGINS: this executable dlopen()s plugins that call back
    # into ITS OWN symbols. Without exported symbols the plugin loads and
    # then fails to resolve them -- at dlopen time on some platforms, at
    # first call on others, and never at build time. ENABLE_EXPORTS is
    # cmake's portable spelling for it (-rdynamic and friends on ELF, an
    # import library on Windows), so a project says what the executable
    # IS rather than which linker flag its platform wants.
    # PUBLISH_SYMBOLS: code loaded at runtime resolves into this
    # executable, so its symbols must be in the dynamic symbol table.
    # ENABLE_EXPORTS is cmake's portable spelling; the visibility default
    # this project applies (-fvisibility=hidden) has to be lifted too, or
    # "published" would be true of the link and false of the compile.
    #
    # NOT tied to the build type: a host whose plugins resolve in Debug
    # and fail in Release is a binary that works for whoever built it and
    # breaks for whoever ships it.
    set_property(GLOBAL APPEND PROPERTY _buildutil_apps "${target}")
    if(entry_from_closure)
      set_property(GLOBAL APPEND PROPERTY _buildutil_closure_entry_apps "${target}")
    endif()
    _buildutil_schedule_object_sweep()
    if(MOD_PUBLISH_SYMBOLS)
      set_target_properties(${target} PROPERTIES ENABLE_EXPORTS ON
                            _buildutil_publish_symbols ON)
      target_compile_options(${target} PRIVATE
        $<$<NOT:$<CXX_COMPILER_ID:MSVC>>:-fvisibility=default>)
    endif()
    # MIGRATION SHIM, one release only. Consumer trees spell
    # $<TARGET_FILE:<module>-app>, and every box installs new buildutil
    # releases automatically within minutes -- a hard break would stop
    # builds on machines nobody is watching. Removed in the next release.
    add_executable(${target}-app ALIAS ${target})
    # main.cpp is a module TU that does NOT compile into the library, so the
    # module's PRIVATE own-dir root does not reach it through the link --
    # same restatement the -tests / -benches targets make. The glob is
    # recursive, so main.cpp need not sit at the module root either.
    target_include_directories(${target} PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
    _buildutil_apply_optimization(${target})
    _buildutil_add_link_pools(${target} PRIVATE)
    # the mirrored spot: sources/a/b/c ships <prefix>/a/b/c -- the leaf
    # is the FILE, its parent path the directories
    _buildutil_mirror_parent(_mirror)
    if(EMSCRIPTEN)
      set_target_properties(${target} PROPERTIES SUFFIX ".html")
      install(FILES "$<TARGET_FILE_DIR:${target}>/$<TARGET_FILE_BASE_NAME:${target}>.js"
                    "$<TARGET_FILE_DIR:${target}>/$<TARGET_FILE_BASE_NAME:${target}>.wasm"
              DESTINATION "${_mirror}")
    endif()
    install(TARGETS ${target} RUNTIME DESTINATION "${_mirror}"
                              BUNDLE  DESTINATION "${_mirror}")
    # a dependency's runtime payload, and the sub-process bundles that
    # live inside this app -- both need the executable to exist
    _buildutil_add_runtime_payload(${target} TRUE)
    _buildutil_add_macos_helpers(${target})
  endif()
  if(pybind_entry)
    _buildutil_add_python_bridge(${target} "${pybind_entry}")
  endif()
  _buildutil_add_resources(${target})
  _buildutil_add_embedded_resources(${target})
  _buildutil_install_runtime_data(${target})
  _buildutil_add_test_target(${target} ${gen_tests})
  _buildutil_add_python_test_target(${target})
  _buildutil_add_bench_target(${target} ${gen_benches})
  if(TARGET ${target}-resources AND TARGET ${target}-tests)
    add_dependencies(${target}-tests ${target}-resources)
  endif()
  # same ordering for the embedded-resources accessor: a *.test.cpp
  # includes the generated header like any other TU of the module
  if(TARGET ${target}-resources-embed AND TARGET ${target}-tests)
    add_dependencies(${target}-tests ${target}-resources-embed)
  endif()
  if(TARGET ${target}-resources-embed AND TARGET ${target}-benches)
    add_dependencies(${target}-benches ${target}-resources-embed)
  endif()
  _buildutil_set_cxx_standard("${cxx_standard}" ${lib} ${target}
    ${target}-tests ${target}-benches ${target}-pybind)
  # A test executable that starts the thing under test needs the same
  # payload beside it -- and it does not live in <build>/bin, so the app's
  # copy is no help. Build tree only: nothing here ships.
  foreach(_rt_extra IN ITEMS "${target}-tests" "${target}-benches")
    if(TARGET ${_rt_extra})
      _buildutil_add_runtime_payload(${_rt_extra} FALSE)
    endif()
  endforeach()
endfunction()

# Binary resources, presence-driven: a <stem>.rom.bin in a module ships verbatim
# as a generated resources/<stem>.rom.hpp holding a constexpr <STEM>_ROM byte
# array (an embedded font, lookup tables -- host-side bytes with no ROM
# tie). This is general and stays here; firmware IMAGES (*.c / *.asm) are
# Init_firmware's job in cmake/watcom.cmake, kept apart so a resource blob never
# drags in a ROM toolchain and a firmware font never masquerades as a host asset.
function(_buildutil_add_resources target)
  file(GLOB rom_blobs CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/*.rom.bin")
  if(NOT rom_blobs)
    return()
  endif()
  set(res_dir "${CMAKE_BINARY_DIR}/generated/${target}/resources")
  file(MAKE_DIRECTORY "${res_dir}")
  set(headers "")
  foreach(blob IN LISTS rom_blobs)
    cmake_path(GET blob STEM LAST_ONLY stem)
    string(REGEX REPLACE "\\.rom$" "" stem "${stem}")
    string(TOUPPER "${stem}" ident)
    string(REPLACE "-" "_" ident "${ident}")
    set(hpp "${res_dir}/${stem}.rom.hpp")
    add_custom_command(OUTPUT "${hpp}"
      COMMAND "${CMAKE_COMMAND}" "-DINPUT=${blob}" "-DOUTPUT=${hpp}"
              "-DIDENT=${ident}_ROM" -P "${_buildutil_cmake_dir}/bin2cpp.cmake"
      DEPENDS "${blob}" "${_buildutil_cmake_dir}/bin2cpp.cmake"
      COMMENT "bin2cpp ${stem}.rom.bin")
    list(APPEND headers "${hpp}")
  endforeach()
  add_custom_target(${target}-resources DEPENDS ${headers})
  # The include root and the generation dependency belong on the module
  # LIBRARY -- in a split module (main.cpp present) ${target} is the
  # EXECUTABLE, and attaching only there left every lib TU that includes
  # the generated header without the -I or the ordering (BossDeux:
  # font.hpp -> resources/vga_8x16.rom.hpp failed in overlay.cpp/video.cpp,
  # both compiled into bdx86gui-lib). PUBLIC on the library reaches the
  # lib's own TUs, the exe through its link, and every consumer -- the
  # same road all other module usage requirements travel.
  _buildutil_library_of(${target} _res_lib)
  add_dependencies(${_res_lib} ${target}-resources)
  target_include_directories(${_res_lib} PUBLIC "${CMAKE_BINARY_DIR}/generated/${target}")
  if(NOT _res_lib STREQUAL "${target}" AND TARGET ${target})
    # the exe's own TUs (main.cpp) may include the header too, and they
    # compile in parallel with the library -- the link dependency alone
    # does not order object compilation after the custom command
    add_dependencies(${target} ${target}-resources)
  endif()
endfunction()

# ===========================================================================
# EMBEDDED RESOURCES -- declared in buildutil.toml, compiled INTO the binary
# ===========================================================================
#
# A project says WHICH files; buildutil decides HOW. There is no runtime
# lookup and no directory to ship: every declared file is in the
# executable, reachable through one generated accessor.
#
#   [resources]
#   dir = "resources"          # repo-root-relative (or a module's own
#   files = ["*.html", "*.js"] # *.embed/ directory -- see below)
#
# renders, right here in this file, into a _buildutil_resource_set() call
# per declaration. The project's CMakeLists never says any of it.
#
# The presence-driven half needs no toml at all: a `<name>.embed/`
# directory inside a module IS the declaration, exactly as `*.install/`
# is for data that ships BESIDE the binary. The two suffixes are the
# same statement about the same tree, one file in and one file out.
#
# TWO BACK ENDS, ONE HEADER (buildutil/embed.py writes both):
#   * `#embed` where the compiler has it -- gcc >= 15, clang >= 19,
#     Apple clang 21. Nothing large passes through cmake or through the
#     C++ parser as text.
#   * a generated `static const unsigned char[]` otherwise -- MSVC has no
#     `#embed` as of VS 2026. An ARRAY, never a string literal: MSVC caps
#     a string literal at 16 KB and there is no cap on an initializer.
# Which one is a COMPILE PROBE, not a version table: the flag being
# accepted and an `#embed` resolving are different claims, and the second
# is the one that matters (the same lesson as
# BUILDUTIL_CXX_ACCEPTS_EMBED_DIR above). -DBUILDUTIL_EMBED_FALLBACK=ON
# forces the array path on a compiler that has `#embed`, which is how the
# suite proves both back ends produce identical bytes.
option(BUILDUTIL_EMBED_FALLBACK
       "Embed declared resources as a generated byte array even where the \
compiler implements #embed (testing, and a bisect handle)" OFF)

# The project's default outer namespace, from [project] name; the
# generated accessors always live one level in, at <ns>::resources.
set(_buildutil_resources_namespace "@RESOURCE_NAMESPACE@")

function(_buildutil_embed_supported out)
  if(DEFINED BUILDUTIL_CXX_HAS_EMBED)
    set(${out} "${BUILDUTIL_CXX_HAS_EMBED}" PARENT_SCOPE)
    return()
  endif()
  set(_probe_dir "${CMAKE_BINARY_DIR}/_buildutil")
  file(MAKE_DIRECTORY "${_probe_dir}")
  file(WRITE "${_probe_dir}/embed-probe.dat" "buildutil")
  include(CheckCXXSourceCompiles)
  check_cxx_source_compiles(
"static const unsigned char probe[] = {
#embed \"${_probe_dir}/embed-probe.dat\"
};
int main() { return probe[0] == 'b' ? 0 : 1; }
" BUILDUTIL_CXX_HAS_EMBED)
  set(${out} "${BUILDUTIL_CXX_HAS_EMBED}" PARENT_SCOPE)
endfunction()

# Which python runs the generator. The driver passes -DBUILDUTIL_PY; a
# bare cmake (this machinery's own e2e suite) gets the interpreter cmake
# finds, with the package reachable through BUILDUTIL_PYSUPPORT's parent
# -- the same route reflect.cmake takes for the same reason.
function(_buildutil_python_command out)
  if(BUILDUTIL_PY)
    set(_bu_exe "${BUILDUTIL_PY}")
  else()
    if(NOT Python3_Interpreter_FOUND)
      find_package(Python3 REQUIRED COMPONENTS Interpreter)
    endif()
    set(_bu_exe "${Python3_EXECUTABLE}")
  endif()
  if(BUILDUTIL_PYSUPPORT)
    # <site>/buildutil/pysupport -> <site>, which is where `import
    # buildutil` resolves from. Set unconditionally: BUILDUTIL_PY names an
    # interpreter, not a PYTHONPATH, and a driver whose venv python does
    # not happen to carry the package would otherwise fail here with
    # nothing but "No module named buildutil.embed".
    get_filename_component(_bu_pkg "${BUILDUTIL_PYSUPPORT}" DIRECTORY)
    get_filename_component(_bu_pkg "${_bu_pkg}" DIRECTORY)
    set(${out} "${CMAKE_COMMAND};-E;env;PYTHONPATH=${_bu_pkg};${_bu_exe}"
        PARENT_SCOPE)
  else()
    set(${out} "${_bu_exe}" PARENT_SCOPE)
  endif()
endfunction()

# ===========================================================================
# PROJECT OPTIONS ([options] in buildutil.toml) -- a value chosen per build,
# injected as a macro. An option is NOT a constant: a constant never changes
# and belongs in the code, while this is what the build was asked for, and a
# macro is the only thing some conditional compilation can read.
#
# The force include rather than a -D per option is the owner's shape: the
# header is also what an IDE and a debugger see, and one file beats a
# command line that grows with every declaration.
#
# Called from ROOT scope: add_compile_options below is a directory property,
# so it reaches every target added afterwards -- Init_submodule's library,
# its executable, its tests and its benches -- and no imported dependency.
# ===========================================================================
function(_buildutil_project_options project declarations)
  set(rendered "")
  foreach(entry IN LISTS declarations)
    string(REPLACE "|" ";" fields "${entry}")
    list(GET fields 0 name)
    list(GET fields 1 kind)
    list(GET fields 2 fallback)
    string(TOUPPER "${name}" upper)
    set(variable "@CMAKE_OPTION_PREFIX@_OPTION_${upper}")
    # set(CACHE) leaves an existing entry alone, exactly as option() does,
    # so the driver's -D lands before this and survives it.
    set(${variable} "${fallback}" CACHE STRING
        "project option ${name} (${kind}) -> @CMAKE_OPTION_PREFIX@_${upper}")
    list(APPEND rendered --option "${name}:${kind}:${${variable}}")
  endforeach()
  set(header "${CMAKE_BINARY_DIR}/generated/${project}/options.hpp")
  _buildutil_python_command(_bu_options_py)
  execute_process(
    COMMAND ${_bu_options_py} -m buildutil.options
            --prefix "@CMAKE_OPTION_PREFIX@" --output "${header}" ${rendered}
    RESULT_VARIABLE failed ERROR_VARIABLE reason)
  if(failed)
    message(FATAL_ERROR "buildutil: could not render ${header}\n${reason}")
  endif()
  set_property(GLOBAL PROPERTY _buildutil_options_header "${header}")
  _buildutil_force_include_flags("${header}" flags)
  add_compile_options(${flags})
endfunction()

# The flag that force-includes one header, as one SHELL: group so cmake's
# option de-duplication cannot drop the second of two `-include`s. The
# preprocessed languages only: a resource script and an unpreprocessed .s
# have no use for the header and no flag spelling for it.
function(_buildutil_force_include_flags header out)
  set(preprocessed "$<COMPILE_LANGUAGE:C,CXX,OBJC,OBJCXX>")
  set(cl "$<CXX_COMPILER_ID:MSVC>")
  set(${out}
    "$<$<AND:${preprocessed},$<NOT:${cl}>>:SHELL:-include \"${header}\">"
    "$<$<AND:${preprocessed},${cl}>:SHELL:/FI \"${header}\">" PARENT_SCOPE)
endfunction()

# _Public_(n) on a function definition exports it at ABI version n, at the
# package's semver major when n is empty; the section it names carries n
# to the export scan. Force-included into every C and C++ unit.
function(_buildutil_public_macro)
  cmake_path(GET _buildutil_cmake_dir PARENT_PATH bdudata)
  set(package "")
  if(EXISTS "${bdudata}/buildinfo.json")
    file(READ "${bdudata}/buildinfo.json" stamped)
    _buildutil_json_field("${stamped}" package "" package)
  endif()
  set(major 0)
  if(package MATCHES "^([0-9]+)\\.[0-9]+\\.[0-9]+")
    set(major "${CMAKE_MATCH_1}")
  elseif(package STREQUAL "")
    message(STATUS "no semver tag reachable: the package is 0.0.0 and _Public_() exports at major 0")
  else()
    message(STATUS "version '${package}' is not semver, major 0")
  endif()
  set_property(GLOBAL PROPERTY _buildutil_package_major "${major}")
  set(header "${CMAKE_BINARY_DIR}/generated/_buildutil/public.h")
  file(CONFIGURE OUTPUT "${header}" CONTENT [=[
/* Generated by buildutil: _Public_(n) exports a function at ABI version n, empty n the package major. */
#ifndef BUILDUTIL_PUBLIC_H
#define BUILDUTIL_PUBLIC_H
#if defined(__GNUC__)
#pragma GCC system_header
#endif
#define BUILDUTIL_STR_(n) #n
#define BUILDUTIL_STR(n) BUILDUTIL_STR_(n)
#if defined(_MSC_VER)
#define _Public_(n) __declspec(dllexport) __declspec(code_seg(".text$_Public_." BUILDUTIL_STR(n)))
#elif defined(_WIN32) || defined(__CYGWIN__)
#define _Public_(n) __declspec(dllexport) __attribute__((section(".text$_Public_." BUILDUTIL_STR(n))))
#elif defined(__ELF__)
#define _Public_(n) __attribute__((visibility("default"), section(".text._Public_." BUILDUTIL_STR(n))))
#else
#define _Public_(n) __attribute__((visibility("default")))
#endif
#endif
]=] @ONLY)
  _buildutil_force_include_flags("${header}" flags)
  add_compile_options(${flags})
endfunction()

# ===========================================================================
# BUILD IDENTITY -- what the build IS, where the options above are what it
# was asked for. The driver stamps _bdudata/buildinfo.json before every
# configure; this reads the six values into cmake variables a project's own
# cmake can use (a configure_file of a web page, an installer, an about
# box) and renders the same six as macros into a header.
#
# The header is NOT force-included and never joins a compile line: the time
# moves on every build and a force include would rebuild every translation
# unit with it. A project that wants the macros writes
# `#include "<project>/buildinfo.hpp"` -- the generated root is already on
# every module's include path, exactly as `<project>/options.hpp` would be.
#
# Called from ROOT scope, so PARENT_SCOPE here is the scope every module
# added afterwards inherits.
# ===========================================================================
function(_buildutil_json_field text field fallback out)
  string(JSON value ERROR_VARIABLE unreadable GET "${text}" "${field}")
  if(unreadable)
    set(value "${fallback}")
  endif()
  set(${out} "${value}" PARENT_SCOPE)
endfunction()

function(_buildutil_build_identity project)
  cmake_path(GET _buildutil_cmake_dir PARENT_PATH bdudata)
  set(stamp "${bdudata}/buildinfo.json")
  set(version "unknown")
  set(commit "unknown")
  set(tag "")
  set(time "")
  set(dirty OFF)
  set(number 0)
  if(EXISTS "${stamp}")
    # the stamp moves every build, so cmake re-runs and the header follows
    set_property(DIRECTORY "${CMAKE_SOURCE_DIR}" APPEND
                 PROPERTY CMAKE_CONFIGURE_DEPENDS "${stamp}")
    file(READ "${stamp}" stamped)
    _buildutil_json_field("${stamped}" version "${version}" version)
    _buildutil_json_field("${stamped}" commit  "${commit}"  commit)
    _buildutil_json_field("${stamped}" tag     "${tag}"     tag)
    _buildutil_json_field("${stamped}" time    "${time}"    time)
    _buildutil_json_field("${stamped}" dirty   "${dirty}"   dirty)
    _buildutil_json_field("${stamped}" number  "${number}"  number)
  else()
    message(STATUS "buildutil: no ${stamp} -- build identity is unknown")
  endif()
  set(@CMAKE_OPTION_PREFIX@_BUILD_VERSION "${version}" PARENT_SCOPE)
  set(@CMAKE_OPTION_PREFIX@_BUILD_COMMIT  "${commit}"  PARENT_SCOPE)
  set(@CMAKE_OPTION_PREFIX@_BUILD_TAG     "${tag}"     PARENT_SCOPE)
  set(@CMAKE_OPTION_PREFIX@_BUILD_TIME    "${time}"    PARENT_SCOPE)
  set(@CMAKE_OPTION_PREFIX@_BUILD_DIRTY   "${dirty}"   PARENT_SCOPE)
  set(@CMAKE_OPTION_PREFIX@_BUILD_NUMBER  "${number}"  PARENT_SCOPE)
  set(header "${CMAKE_BINARY_DIR}/generated/${project}/buildinfo.hpp")
  _buildutil_python_command(_bu_buildinfo_py)
  # Every project pays this call, [options] or none, so a cmake that
  # cannot reach the package at all (the deposit configured by hand, a
  # lane image carrying only cmake and a toolchain) says so and carries
  # on with the variables -- where a BAD VALUE, which only the generator
  # can see, is still a refusal.
  execute_process(COMMAND ${_bu_buildinfo_py} -c "import buildutil.buildinfo"
                  RESULT_VARIABLE no_generator OUTPUT_QUIET ERROR_QUIET)
  if(no_generator)
    message(WARNING "buildutil: ${header} not rendered -- this cmake cannot "
                    "run buildutil.buildinfo. The build identity is in the "
                    "cmake variables either way.")
    return()
  endif()
  execute_process(
    COMMAND ${_bu_buildinfo_py} -m buildutil.buildinfo
            --prefix "@CMAKE_OPTION_PREFIX@" --package-prefix "@MODULE_DEFINE_PREFIX@"
            --input "${stamp}" --output "${header}"
    RESULT_VARIABLE failed ERROR_VARIABLE reason)
  if(failed)
    message(FATAL_ERROR "buildutil: could not render ${header}\n${reason}")
  endif()
endfunction()

# One [resources] declaration, rendered from buildutil.toml. GLOBS and
# MIME are comma-joined (a cmake list would not survive being carried as
# one element of another list); every field is already validated in
# config.py, so nothing here re-argues about shape.
function(_buildutil_resource_set module dir globs prefix ns mime)
  set_property(GLOBAL APPEND PROPERTY _buildutil_resource_modules "${module}")
  set_property(GLOBAL APPEND PROPERTY _buildutil_resource_sets_${module}
               "${dir}|${globs}|${prefix}")
  if(ns)
    set_property(GLOBAL PROPERTY _buildutil_resource_ns_${module} "${ns}")
  endif()
  if(mime)
    set_property(GLOBAL APPEND PROPERTY _buildutil_resource_mime_${module}
                 "${mime}")
  endif()
endfunction()

# A declaration that names a module which never builds would silently do
# nothing -- the failure mode every presence rule in this file exists to
# avoid. Deferred to end of tree, because a module's CMakeLists runs long
# after the declarations are read. Dormant modules are exempt: going
# dormant is a deliberate, local act and must not fail the build.
function(_buildutil_check_resource_claims)
  get_property(declared GLOBAL PROPERTY _buildutil_resource_modules)
  get_property(claimed  GLOBAL PROPERTY _buildutil_resource_claimed)
  get_property(dormant  GLOBAL PROPERTY _buildutil_dormant_modules)
  if(declared)
    list(REMOVE_DUPLICATES declared)
  endif()
  foreach(module IN LISTS declared)
    if(module IN_LIST claimed OR module IN_LIST dormant)
      continue()
    endif()
    message(FATAL_ERROR
      "buildutil.toml declares [resources] for module '${module}', but no "
      "module of that name is in this build. The module is named by its "
      "path under sources/ with '-' joining the components "
      "(sources/gui/panel -> gui-panel); a declaration nothing claims "
      "embeds nothing, silently, which is why this is an error.")
  endforeach()
endfunction()

# ===========================================================================
# THE SHADOW TREE IS A SOURCE TREE -- generated/<module>/ obeys the same
# layout conventions as sources/<module>/
# ===========================================================================
#
# `${CMAKE_BINARY_DIR}/generated/<module>/` was already where configure.py,
# Add_generated_source and the resource generator emit, and already an
# include root and an `#embed` search path. The rule below is what makes
# it a MODULE DIRECTORY rather than a bag of outputs: the data
# conventions read it exactly as they read the source tree.
# `generated/<m>/ui.embed/` is a resource set. `generated/<m>/
# locale.install.win32/` is Windows runtime data. Same globs, same
# platform tags, same overlay levels, same refusals -- and a hand-written
# file and a generated one claiming ONE name is a configure error naming
# both, because a build where you cannot tell which of the two you are
# looking at is worse than one that will not configure.
#
# Nothing here knows what put the files there. A module's configure.py
# runs at the top of Init_submodule, long before these globs, so a hook
# that writes generated/<m>/ui.embed/app.js has thereby added a resource
# -- and `depends()` on its inputs is what re-runs it when they change.
# buildutil deliberately owns no toolchain of its own beyond that: what a
# project transpiles, bundles or compresses is the project's business,
# and buildutil_configure.py is the placement API it does that with.

# A module's two generated roots, in overlay order: the per-profile one
# and the build-invariant shared one, exactly as _buildutil_exec_configure
# hands them to a configure hook. Both are module directories, so both are
# read by the data conventions.
function(_buildutil_generated_roots name out)
  set(${out} "${CMAKE_BINARY_DIR}/generated/${name}"
             "${CMAKE_SOURCE_DIR}/_build/generated/${name}" PARENT_SCOPE)
endfunction()

# Every applicable `*.<suffix>/` directory of a module's generated roots,
# with its overlay level.
function(_buildutil_generated_data_dirs name suffix out_dirs out_levels)
  set(_gd_dirs "")
  set(_gd_levels "")
  _buildutil_generated_roots("${name}" _gd_roots)
  foreach(_gd_root IN LISTS _gd_roots)
    _buildutil_platform_data_dirs("${_gd_root}" "${suffix}" _gd_d _gd_l)
    list(APPEND _gd_dirs ${_gd_d})
    list(APPEND _gd_levels ${_gd_l})
  endforeach()
  set(${out_dirs} "${_gd_dirs}" PARENT_SCOPE)
  set(${out_levels} "${_gd_levels}" PARENT_SCOPE)
endfunction()

# The whole feature, per module. Called from Init_submodule after the
# library exists.
function(_buildutil_add_embedded_resources target)
  get_property(entries GLOBAL PROPERTY _buildutil_resource_sets_${target})
  # A toml-declared set is a BASE: it names its own directory, so there
  # is no tag to read off it, and two of them claiming one name is the
  # same-level refusal it always was.
  set(entry_levels "")
  foreach(_entry IN LISTS entries)
    list(APPEND entry_levels 0)
  endforeach()
  # Presence: a `<name>.embed/` directory in the module, and the
  # platform-tagged overlays of it. CONFIGURE_DEPENDS for the same reason
  # *.install/ needs it -- a file added to the directory that nobody
  # embeds is a green build missing a resource.
  set(gen "${CMAKE_BINARY_DIR}/generated/${target}")   # where this writes
  _buildutil_platform_data_dirs("${CMAKE_CURRENT_SOURCE_DIR}" "embed"
                                _embed_dirs _embed_levels)
  _buildutil_append_data_entries("${_embed_dirs}" "${_embed_levels}"
                                 entries entry_levels)
  # The SHADOW tree, read exactly as the source tree is: generated
  # `*.embed/` directories, their platform tags, their overlay levels.
  # A name a hand-written file and a generated one both claim is the
  # same-specificity refusal.
  _buildutil_generated_data_dirs("${target}" "embed" _sh_dirs _sh_levels)
  _buildutil_append_data_entries("${_sh_dirs}" "${_sh_levels}"
                                 entries entry_levels)
  if(NOT entries)
    return()
  endif()
  set_property(GLOBAL APPEND PROPERTY _buildutil_resource_claimed "${target}")

  get_property(outer GLOBAL PROPERTY _buildutil_resource_ns_${target})
  if(NOT outer)
    set(outer "${_buildutil_resources_namespace}")
  endif()
  set(ns "${outer}::resources")
  # Two modules generating the same namespace define the same accessors
  # twice; the link either fails opaquely or picks one. Say which two.
  get_property(ns_owner GLOBAL PROPERTY _buildutil_resource_nsowner_${outer})
  if(ns_owner AND NOT ns_owner STREQUAL "${target}")
    message(FATAL_ERROR
      "modules '${ns_owner}' and '${target}' both embed resources into "
      "namespace '${ns}'. Give one of them its own `namespace = ...` in "
      "its [[resources]] declaration -- two definitions of ${ns}::find "
      "cannot both be linked.")
  endif()
  set_property(GLOBAL PROPERTY _buildutil_resource_nsowner_${outer} "${target}")

  set(names "")
  set(files "")
  set(owners "")
  set(levels "")
  set(manifest_text "")
  list(LENGTH entries _entry_count)
  math(EXPR _entry_last "${_entry_count} - 1")
  foreach(_entry_i RANGE ${_entry_last})
    list(GET entries ${_entry_i} entry)
    list(GET entry_levels ${_entry_i} level)
    string(REGEX MATCH "^([^|]*)\\|([^|]*)\\|(.*)$" _matched "${entry}")
    set(dir    "${CMAKE_MATCH_1}")
    set(globs  "${CMAKE_MATCH_2}")
    set(prefix "${CMAKE_MATCH_3}")
    if(NOT IS_ABSOLUTE "${dir}")
      set(dir "${CMAKE_SOURCE_DIR}/${dir}")
    endif()
    get_filename_component(dir "${dir}" ABSOLUTE)
    if(NOT IS_DIRECTORY "${dir}")
      message(FATAL_ERROR
        "[resources] for module '${target}': ${dir} is not a directory.")
    endif()
    if(NOT globs)
      set(globs "*")
    endif()
    string(REPLACE "," ";" glob_list "${globs}")
    set(matched "")
    foreach(glob IN LISTS glob_list)
      file(GLOB_RECURSE hits CONFIGURE_DEPENDS RELATIVE "${dir}" "${dir}/${glob}")
      list(APPEND matched ${hits})
    endforeach()
    if(matched)
      list(REMOVE_DUPLICATES matched)
      list(SORT matched)
    endif()
    foreach(rel IN LISTS matched)
      if(IS_DIRECTORY "${dir}/${rel}")
        continue()
      endif()
      set(name "${prefix}${rel}")
      # An overlay by resource NAME: a more specific directory replaces
      # what a less specific one contributed, a new name is added, and
      # equal specificity is the refusal _buildutil_overlay_one makes.
      _buildutil_overlay_one(
        "resources for module '${target}'"
        "${name}" "${dir}/${rel}" "${dir}" "${level}"
        names files owners levels)
    endforeach()
  endforeach()
  list(LENGTH names _name_count)
  if(_name_count GREATER 0)
    math(EXPR _name_last "${_name_count} - 1")
    foreach(_name_i RANGE ${_name_last})
      list(GET names ${_name_i} _mf_name)
      list(GET files ${_name_i} _mf_file)
      string(APPEND manifest_text "${_mf_name}\t${_mf_file}\n")
    endforeach()
  endif()
  if(NOT names)
    message(FATAL_ERROR
      "[resources] for module '${target}' matched no files. An empty "
      "declaration compiles an accessor that can never answer anything; "
      "check `dir` and `files`.")
  endif()

  file(MAKE_DIRECTORY "${gen}")
  set(manifest "${gen}/resources.manifest")
  # Configure-time, so the SET (as opposed to the contents) is fixed
  # before the build starts, and so it is a DEPENDS entry that re-runs
  # the generator when a file is added or removed.
  file(WRITE "${manifest}" "${manifest_text}")

  _buildutil_embed_supported(_has_embed)
  if(BUILDUTIL_EMBED_FALLBACK OR NOT _has_embed)
    set(mode "array")
  else()
    set(mode "embed")
  endif()

  set(mime_args "")
  get_property(mime GLOBAL PROPERTY _buildutil_resource_mime_${target})
  foreach(pair IN LISTS mime)
    string(REPLACE "," ";" pair_list "${pair}")
    foreach(one IN LISTS pair_list)
      list(APPEND mime_args --mime "${one}")
    endforeach()
  endforeach()

  set(hpp "${gen}/resources.hpp")
  set(cpp "${gen}/resources.cpp")
  list(LENGTH names count)
  _buildutil_python_command(_py)
  add_custom_command(
    OUTPUT "${hpp}" "${cpp}"
    COMMAND ${_py} -m buildutil.embed
            --manifest "${manifest}" --header "${hpp}" --source "${cpp}"
            --namespace "${ns}" --mode "${mode}" ${mime_args}
    DEPENDS ${files} "${manifest}"
    COMMENT "resources: ${count} file(s) into ${target} (${mode})"
    VERBATIM COMMAND_EXPAND_LISTS)

  _buildutil_library_of("${target}" _res_lib)
  get_target_property(_res_type ${_res_lib} TYPE)
  if(_res_type STREQUAL "INTERFACE_LIBRARY")
    set(_res_lib "${target}")           # a module whose only TU is main.cpp
  endif()
  # The compiler's depfile tracks `#embed` on gcc and clang, but not on
  # every toolchain and not for the array back end's inputs at all. State
  # it: editing a resource must rebuild the TU that carries it.
  set_source_files_properties("${cpp}" PROPERTIES
    GENERATED TRUE OBJECT_DEPENDS "${files}")
  target_sources(${_res_lib} PRIVATE "${cpp}")
  # The accessor is std::span/std::string_view, so the generated header
  # needs C++20 -- of this library AND of everyone who includes it, hence
  # PUBLIC. A MINIMUM, not a setting: cmake takes the higher of this and
  # whatever standard the project (or its conan toolchain) already asks
  # for, so a C++23/26 tree is untouched and a tree that never said
  # anything stops depending on its compiler's default.
  target_compile_features(${_res_lib} PUBLIC cxx_std_20)
  add_custom_target(${target}-resources-embed DEPENDS "${hpp}" "${cpp}")
  add_dependencies(${_res_lib} ${target}-resources-embed)
  set_property(GLOBAL PROPERTY _buildutil_resource_target_${target}
               "${target}-resources-embed")
  # PUBLIC, on the module LIBRARY, exactly as the .rom.bin route learned
  # to: the header is included from the module's own TUs, from
  # main.cpp, from the tests, and from any consumer.
  _buildutil_add_generated_roots(${_res_lib} PUBLIC "${gen}")
  if(NOT _res_lib STREQUAL "${target}" AND TARGET ${target})
    add_dependencies(${target} ${target}-resources-embed)
  endif()
endfunction()

# ===========================================================================
# RUNTIME PAYLOAD -- files a DEPENDENCY ships that must sit next to the
# executable, declared as `[runtime] from = ["<dep>"]` in buildutil.toml
# ===========================================================================
#
# Some packages are not finished at link time. CEF resolves icudtl.dat,
# the V8 snapshot and its .pak files RELATIVE TO the directory libcef was
# loaded from -- not relative to the executable, and not through
# LD_LIBRARY_PATH, which actively breaks it. The only layout that works
# is the flat one: the library, the data and the executable in one
# directory, with $ORIGIN on the rpath. That is a fact about the
# DEPENDENCY, and every consumer of such a package was re-deriving it.
#
# THE CONTRACT, so buildutil never has to know the word "CEF". A package
# with a runtime payload publishes it from the cmake module its
# find_package loads -- for a dependency named <dep> (<DEP> upper-cased):
#
#   GLOBAL property <DEP>_RUNTIME_BINARY_DIR    contents go beside the exe
#   GLOBAL property <DEP>_RUNTIME_RESOURCE_DIR  contents go beside the exe
#   GLOBAL property <DEP>_RUNTIME_LIBRARY_DIR   its shared libraries do
#   GLOBAL property <DEP>_FRAMEWORK_DIR         macOS: into Contents/Frameworks
#   function <dep>_copy_runtime(<target>)       optional: the BUILD tree
#   function <dep>_copy_framework(<bundle>)     optional: the BUILD tree, macOS
#
# GLOBAL properties rather than variables because a function body sees
# its CALLER's scope, and a plain variable set in the package's module
# expands to "" from inside one -- copying from the filesystem root.
#
# Buildutil calls the package's function where there is one (the package
# knows its own layout, and sets the rpath while it is there) and copies
# the property dirs itself where there is not. The INSTALL tree is always
# buildutil's job: those functions are POST_BUILD, they only ever touch
# the build tree, and `buildutil run` execs the INSTALLED binary -- which
# is exactly the gap every consumer fell into ("Error loading V8 startup
# snapshot file" from a binary that ran fine in _build).
#
# `dirs` ships repo-relative directories with no package behind them, for
# a payload the project itself owns.
function(_buildutil_runtime_payload deps targets dirs)
  set_property(GLOBAL PROPERTY _buildutil_runtime_deps    "${deps}")
  set_property(GLOBAL PROPERTY _buildutil_runtime_targets "${targets}")
  set_property(GLOBAL PROPERTY _buildutil_runtime_dirs    "${dirs}")
endfunction()

# Every shared library in a directory -- what has to travel, as opposed
# to the import libraries and static archives that live beside it and
# must not (cef's lib/ holds libcef.so AND libcef_dll_wrapper.a; shipping
# the second is dead weight in every artifact).
function(_buildutil_shared_libraries dir out)
  file(GLOB found
       "${dir}/*.so" "${dir}/*.so.*" "${dir}/*.dylib" "${dir}/*.dll")
  set(${out} "${found}" PARENT_SCOPE)
endfunction()

# Applied to one executable, from Init_submodule, once the target exists.
# `do_install` is FALSE for the test/bench executables: they need the
# payload beside them to RUN (a *.test.cpp that starts the thing under
# test needs exactly what the app needs), and they ship nowhere.
function(_buildutil_add_runtime_payload target do_install)
  get_property(deps    GLOBAL PROPERTY _buildutil_runtime_deps)
  get_property(want    GLOBAL PROPERTY _buildutil_runtime_targets)
  get_property(dirs    GLOBAL PROPERTY _buildutil_runtime_dirs)
  if(NOT deps AND NOT dirs)
    return()
  endif()
  # `targets` names MODULES; the -tests/-benches executables of a named
  # module are covered by it without anyone spelling them out.
  string(REGEX REPLACE "-(tests|benches)$" "" _rt_module "${target}")
  if(want AND NOT _rt_module IN_LIST want)
    return()
  endif()
  _buildutil_mirror_parent(mirror)
  get_property(is_bundle GLOBAL PROPERTY _buildutil_bundle_target_${target})

  foreach(dep IN LISTS deps)
    string(TOUPPER "${dep}" DEP)
    string(MAKE_C_IDENTIFIER "${DEP}" DEP)
    string(TOLOWER "${dep}" dep_lower)
    get_property(bin_dir GLOBAL PROPERTY ${DEP}_RUNTIME_BINARY_DIR)
    get_property(res_dir GLOBAL PROPERTY ${DEP}_RUNTIME_RESOURCE_DIR)
    get_property(lib_dir GLOBAL PROPERTY ${DEP}_RUNTIME_LIBRARY_DIR)
    get_property(fmk_dir GLOBAL PROPERTY ${DEP}_FRAMEWORK_DIR)
    if(NOT bin_dir AND NOT res_dir AND NOT lib_dir AND NOT fmk_dir
       AND NOT COMMAND ${dep_lower}_copy_runtime
       AND NOT COMMAND ${dep_lower}_copy_framework)
      # A declaration that does nothing is the failure this whole file
      # keeps refusing to allow. Name the contract rather than the symptom.
      message(FATAL_ERROR
        "buildutil.toml [runtime] names '${dep}', but that package "
        "publishes no runtime payload: buildutil looked for the GLOBAL "
        "properties ${DEP}_RUNTIME_{BINARY,RESOURCE,LIBRARY}_DIR / "
        "${DEP}_FRAMEWORK_DIR and for ${dep_lower}_copy_runtime(). Either "
        "the dependency has no payload (drop it from [runtime]) or its "
        "cmake module does not publish one (that is the package's bug).")
    endif()

    # --- the BUILD tree ---------------------------------------------------
    if(APPLE AND is_bundle AND COMMAND ${dep_lower}_copy_framework)
      cmake_language(CALL ${dep_lower}_copy_framework ${target})
    elseif(NOT APPLE AND COMMAND ${dep_lower}_copy_runtime)
      cmake_language(CALL ${dep_lower}_copy_runtime ${target})
    else()
      _buildutil_copy_payload_beside(${target} "${bin_dir}" "${res_dir}"
                                     "${lib_dir}")
    endif()

    # --- the INSTALL tree, always ours -----------------------------------
    if(NOT do_install)
    elseif(APPLE AND is_bundle)
      # inside the bundle, where the loader looks -- not beside it
      if(fmk_dir AND IS_DIRECTORY "${fmk_dir}")
        # the BUNDLE is named after the app (the module's leaf), which is
        # not the cmake target name for a nested module
        _buildutil_module_app_name(_rt_app)
        install(DIRECTORY "${fmk_dir}"
                DESTINATION "${mirror}/${_rt_app}.app/Contents/Frameworks"
                USE_SOURCE_PERMISSIONS)
      endif()
    else()
      foreach(payload IN ITEMS "${bin_dir}" "${res_dir}")
        if(payload AND IS_DIRECTORY "${payload}")
          install(DIRECTORY "${payload}/" DESTINATION "${mirror}"
                  USE_SOURCE_PERMISSIONS)
        endif()
      endforeach()
      if(lib_dir AND IS_DIRECTORY "${lib_dir}")
        _buildutil_shared_libraries("${lib_dir}" shared)
        if(shared)
          install(PROGRAMS ${shared} DESTINATION "${mirror}")
        endif()
      endif()
    endif()
  endforeach()

  # --- the project's own payload dirs -------------------------------------
  foreach(payload IN LISTS dirs)
    set(abs "${CMAKE_SOURCE_DIR}/${payload}")
    if(NOT IS_DIRECTORY "${abs}")
      message(FATAL_ERROR
        "buildutil.toml [runtime] dirs names '${payload}', which is not a "
        "directory in this repo.")
    endif()
    add_custom_command(TARGET ${target} POST_BUILD
      COMMAND "${CMAKE_COMMAND}" -E copy_directory_if_different
              "${abs}" "$<TARGET_FILE_DIR:${target}>"
      COMMENT "runtime payload: ${payload} -> beside ${target}"
      VERBATIM)
    if(do_install)
      install(DIRECTORY "${abs}/" DESTINATION "${mirror}"
              USE_SOURCE_PERMISSIONS)
    endif()
  endforeach()

  # The flat layout only resolves if the loader looks beside the binary.
  # A package's own copy_runtime() sets this too; appending twice is
  # harmless, and a package without one would otherwise install a binary
  # that cannot find the library sitting next to it.
  _buildutil_loader_relative(beside "")
  set_property(TARGET ${target} APPEND PROPERTY BUILD_RPATH "${beside}")
  set_property(TARGET ${target} APPEND PROPERTY INSTALL_RPATH "${beside}")
endfunction()

function(_buildutil_copy_payload_beside target bin_dir res_dir lib_dir)
  foreach(payload IN ITEMS "${bin_dir}" "${res_dir}")
    if(payload AND IS_DIRECTORY "${payload}")
      add_custom_command(TARGET ${target} POST_BUILD
        COMMAND "${CMAKE_COMMAND}" -E copy_directory_if_different
                "${payload}" "$<TARGET_FILE_DIR:${target}>"
        COMMENT "runtime payload -> beside ${target}"
        VERBATIM)
    endif()
  endforeach()
  if(lib_dir AND IS_DIRECTORY "${lib_dir}")
    _buildutil_shared_libraries("${lib_dir}" shared)
    foreach(one IN LISTS shared)
      add_custom_command(TARGET ${target} POST_BUILD
        COMMAND "${CMAKE_COMMAND}" -E copy_if_different
                "${one}" "$<TARGET_FILE_DIR:${target}>"
        VERBATIM)
    endforeach()
  endif()
endfunction()

# ===========================================================================
# macOS APPLICATION BUNDLE -- `[bundle.macos]` in buildutil.toml
# ===========================================================================
#
# On macOS an application is not a binary, it is a directory. A framework
# loaded at run time is found at ../Frameworks relative to the executable
# INSIDE the bundle, so a project whose dependency ships one has no
# choice about the shape. What it should have a choice about is nothing
# else: the plist boilerplate, the Frameworks layout, the helper naming
# rule and the install destination are the same for every such app.
#
# The one thing that genuinely needed fixing in the machinery rather than
# working around: Init_submodule emits `install(TARGETS <app> RUNTIME
# DESTINATION ...)`, and cmake REFUSES at generate time to install a
# MACOSX_BUNDLE target through a rule with no BUNDLE DESTINATION. So
# setting MACOSX_BUNDLE on a buildutil module could not work at all, and
# every consumer assembled the bundle with POST_BUILD copies instead --
# which then cannot use the package's own cef_copy_framework(), because
# that needs a real bundle target to hang $<TARGET_BUNDLE_CONTENT_DIR:>
# off. One BUNDLE DESTINATION on the install rule unties the whole knot.
#
# HELPERS. A sub-process bundle is one .app per variant in
# Contents/Frameworks, ALL running the same executable under different
# names -- the framework picks the bundle by name and the program inside
# is identical. The names and identifiers are computed in config.py, not
# here: the unsuffixed variant is an EMPTY string, which cannot survive a
# cmake list, and a naming rule is a rule rather than a loop.
function(_buildutil_macos_bundle module identifier name version plist
                                 helper_module helpers)
  set_property(GLOBAL PROPERTY _buildutil_bundle_module     "${module}")
  set_property(GLOBAL PROPERTY _buildutil_bundle_identifier "${identifier}")
  set_property(GLOBAL PROPERTY _buildutil_bundle_name       "${name}")
  set_property(GLOBAL PROPERTY _buildutil_bundle_version    "${version}")
  set_property(GLOBAL PROPERTY _buildutil_bundle_plist      "${plist}")
  set_property(GLOBAL PROPERTY _buildutil_bundle_helper     "${helper_module}")
  set_property(GLOBAL PROPERTY _buildutil_bundle_helpers    "${helpers}")
endfunction()

function(_buildutil_bundle_plist_args out)
  get_property(plist GLOBAL PROPERTY _buildutil_bundle_plist)
  string(REPLACE "," ";" plist "${plist}")
  set(args "")
  foreach(pair IN LISTS plist)
    if(pair)
      list(APPEND args --extra "${pair}")
    endif()
  endforeach()
  set(${out} "${args}" PARENT_SCOPE)
endfunction()

function(_buildutil_write_plist output kind executable identifier name)
  get_property(version GLOBAL PROPERTY _buildutil_bundle_version)
  _buildutil_bundle_plist_args(extra)
  _buildutil_python_command(py)
  execute_process(
    COMMAND ${py} -m buildutil.bundle --output "${output}" --kind "${kind}"
            --executable "${executable}" --identifier "${identifier}"
            --name "${name}" --version "${version}" ${extra}
    RESULT_VARIABLE failed ERROR_VARIABLE errors)
  if(NOT failed EQUAL 0)
    message(FATAL_ERROR "buildutil: Info.plist generation failed\n${errors}")
  endif()
endfunction()

# Called from Init_submodule while creating the module's EXECUTABLE, so
# MACOSX_BUNDLE is set before the install rule below it runs.
function(_buildutil_mark_macos_bundle target)
  if(NOT APPLE)
    return()
  endif()
  get_property(module GLOBAL PROPERTY _buildutil_bundle_module)
  if(NOT module STREQUAL "${target}")
    return()
  endif()
  get_property(identifier GLOBAL PROPERTY _buildutil_bundle_identifier)
  get_property(name       GLOBAL PROPERTY _buildutil_bundle_name)
  _buildutil_module_app_name(app_name)
  set(plist "${CMAKE_BINARY_DIR}/generated/_buildutil/${target}-Info.plist")
  _buildutil_write_plist("${plist}" app "${app_name}" "${identifier}" "${name}")
  set_target_properties(${target} PROPERTIES
    MACOSX_BUNDLE TRUE
    MACOSX_BUNDLE_INFO_PLIST "${plist}")
  set_property(GLOBAL PROPERTY _buildutil_bundle_target_${target} TRUE)
endfunction()

# The sub-process bundles, assembled beside the app they live in. Run
# from the APP's directory, deliberately: add_custom_command(TARGET) must
# be issued where the target was created, while $<TARGET_FILE:helper>
# resolves at GENERATE time -- so the helper module may still be
# unconfigured here and the dependency edge is created anyway.
function(_buildutil_add_macos_helpers target)
  if(NOT APPLE)
    return()
  endif()
  get_property(module GLOBAL PROPERTY _buildutil_bundle_module)
  if(NOT module STREQUAL "${target}")
    return()
  endif()
  get_property(helper_module GLOBAL PROPERTY _buildutil_bundle_helper)
  get_property(helpers       GLOBAL PROPERTY _buildutil_bundle_helpers)
  if(NOT helpers)
    return()
  endif()
  string(REPLACE "," ";" helpers "${helpers}")
  set(frameworks "$<TARGET_BUNDLE_CONTENT_DIR:${target}>/Frameworks")
  foreach(entry IN LISTS helpers)
    string(REGEX MATCH "^([^|]*)\\|(.*)$" _matched "${entry}")
    set(label "${CMAKE_MATCH_1}")
    set(ident "${CMAKE_MATCH_2}")
    string(MAKE_C_IDENTIFIER "${label}" slug)
    set(plist "${CMAKE_BINARY_DIR}/generated/_buildutil/${slug}-Info.plist")
    _buildutil_write_plist("${plist}" helper "${label}" "${ident}" "${label}")
    set(app "${frameworks}/${label}.app")
    add_custom_command(TARGET ${target} POST_BUILD
      COMMAND "${CMAKE_COMMAND}" -E make_directory "${app}/Contents/MacOS"
      COMMAND "${CMAKE_COMMAND}" -E copy_if_different
              "${plist}" "${app}/Contents/Info.plist"
      COMMAND "${CMAKE_COMMAND}" -E copy_if_different
              "$<TARGET_FILE:${helper_module}>" "${app}/Contents/MacOS/${label}"
      COMMENT "bundle: ${label}.app"
      VERBATIM)
  endforeach()
  # The genex above already orders the copy after the helper links, but
  # the edge is invisible to anyone reading the graph and add_dependencies
  # is the one that survives a refactor. It works across directories,
  # which add_custom_command(TARGET) does not -- so it is deferred to end
  # of tree, where the helper module certainly exists.
  set_property(GLOBAL PROPERTY _buildutil_bundle_app "${target}")
  cmake_language(DEFER DIRECTORY "${CMAKE_SOURCE_DIR}"
    CALL _buildutil_link_bundle_helper)
endfunction()

function(_buildutil_link_bundle_helper)
  get_property(app    GLOBAL PROPERTY _buildutil_bundle_app)
  get_property(helper GLOBAL PROPERTY _buildutil_bundle_helper)
  if(NOT TARGET ${helper})
    get_property(module GLOBAL PROPERTY _buildutil_bundle_module)
    message(FATAL_ERROR
      "buildutil.toml [bundle.macos.helpers] names module '${helper}', but "
      "no module of that name is in this build. A helper bundle with no "
      "executable inside it is a sub-process that cannot start -- which "
      "shows up as a blank window, never as a build error. (A macOS-only "
      "helper is declared by tagging its directory: sources/${helper}.macos/.)")
  endif()
  add_dependencies(${app} ${helper})
endfunction()

# The DRIVER's interpreter, not a venv-relative path: that skipped the
# bridge on exactly the images that ship pybind11.
function(_buildutil_pybind11_prefix out)
  if(NOT DEFINED CACHE{@CMAKE_OPTION_PREFIX@_PYBIND_DIR})
    _buildutil_driver_python(_bridge_python)
    execute_process(COMMAND "${_bridge_python}" -m pybind11 --cmakedir
                    OUTPUT_VARIABLE pybind_dir OUTPUT_STRIP_TRAILING_WHITESPACE
                    ERROR_QUIET RESULT_VARIABLE pybind_probe)
    if(NOT pybind_probe EQUAL 0)
      set(pybind_dir "")
    endif()
    set(@CMAKE_OPTION_PREFIX@_PYBIND_DIR "${pybind_dir}" CACHE INTERNAL "venv pybind11 cmake dir")
  endif()
  set(${out} "${@CMAKE_OPTION_PREFIX@_PYBIND_DIR}" PARENT_SCOPE)
endfunction()

function(_buildutil_add_python_bridge target entry)
  if(CMAKE_CROSSCOMPILING)
    # the bridge links the HOST python (a Linux .so); a Windows/macOS
    # cross target has no matching python to bind -- skip, presence-style
    message(STATUS "${target}: cross build -- python bridge skipped")
    return()
  endif()
  list(LENGTH entry _pb_entries)
  if(_pb_entries GREATER 1)
    string(REPLACE ";" ", " _pb_named "${entry}")
    message(FATAL_ERROR
      "${target}: ${_pb_named} -- a module holds at most one *.pybind.cpp. "
      "Each one is a PYBIND11_MODULE(<name>) and names the extension file "
      "python imports; two in one module have one target, one .so and no "
      "answer to what it is called. Split them into a module each.")
  endif()
  _buildutil_pybind11_prefix(_pb_prefix)
  if(NOT _pb_prefix)
    message(STATUS "${target}: pybind11 not in the venv -- python bridge skipped")
    return()
  endif()
  if(NOT TARGET pybind11::module)
    list(APPEND CMAKE_PREFIX_PATH "${_pb_prefix}")
    find_package(Python3 COMPONENTS Interpreter Development.Module QUIET)
    find_package(pybind11 CONFIG QUIET)
  endif()
  if(NOT TARGET pybind11::module)
    message(STATUS "${target}: pybind11/python dev not found -- python bridge skipped")
    return()
  endif()
  pybind11_add_module(${target}-pybind ${entry})
  # the name in the entry's PYBIND11_MODULE, which is its stem: the target
  # name is the DIRECTORY's, and tash-python.so is not importable
  get_filename_component(_pb_module "${entry}" NAME)
  string(REGEX REPLACE "\\.pybind\\.cpp$" "" _pb_module "${_pb_module}")
  set_target_properties(${target}-pybind PROPERTIES
                        OUTPUT_NAME "${_pb_module}")
  # In a split module ${target} is the EXECUTABLE, and linking an
  # executable into another target is only legal with ENABLE_EXPORTS --
  # without it configure fails with cmake's opaque "may not be linked
  # into another target". A *.pybind.cpp IS the declaration that
  # this module's symbols get resolved against from outside, so the
  # machinery says ENABLE_EXPORTS itself rather than making every such
  # project discover PUBLISH_SYMBOLS from the error text. Symbol
  # VISIBILITY is untouched: hidden stays the rule, the bridge sees the
  # annotated surface (or the module opts into PUBLISH_SYMBOLS wholesale).
  get_target_property(_pb_type ${target} TYPE)
  if(_pb_type STREQUAL "EXECUTABLE")
    set_target_properties(${target} PROPERTIES ENABLE_EXPORTS ON)
  endif()
  target_link_libraries(${target}-pybind PRIVATE ${target})
  # The module LIBRARY carries the module's usage requirements -- include
  # roots, conan deps, published defines. The exe's pools are PRIVATE and
  # propagate nothing through its link, so a bridge TU with a qualified
  # cross-module include (execfmt/debuginfo.hpp) could not compile.
  # Linking the library hands the bridge what every other consumer gets;
  # in a module without an entry point the library IS ${target}, already
  # linked above.
  _buildutil_library_of(${target} _pb_lib)
  if(NOT _pb_lib STREQUAL "${target}")
    target_link_libraries(${target}-pybind PRIVATE ${_pb_lib})
  endif()
  # a *.pybind.cpp is a module TU kept out of the library too -- restate the
  # module's PRIVATE own-dir root, as -app and -tests do
  target_include_directories(${target}-pybind PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
  _buildutil_apply_compile_flags(${target}-pybind PRIVATE)
  install(TARGETS ${target}-pybind LIBRARY DESTINATION lib/python)
endfunction()

function(Init_python_module)
  _buildutil_module_name(target)
  _buildutil_split_sources(sources tests benches)
  pybind11_add_module(${target} ${sources})
  # same two roots Init_submodule establishes -- the source glob is recursive,
  # so a nested TU is not next to the module's headers either
  target_include_directories(${target} PRIVATE
    "${CMAKE_SOURCE_DIR}/sources" "${CMAKE_CURRENT_SOURCE_DIR}")
  _buildutil_apply_compile_flags(${target} PRIVATE)
  install(TARGETS ${target} LIBRARY DESTINATION lib/python)
  _buildutil_add_test_target(${target})
  _buildutil_add_python_test_target(${target})
  _buildutil_add_bench_target(${target})
  _buildutil_project_standard(cxx_standard ignored_source)
  _buildutil_directory_standard_shim("${cxx_standard}")
  _buildutil_set_cxx_standard("${cxx_standard}" ${target}
    ${target}-tests ${target}-benches)
endfunction()

function(Init_script)
  _buildutil_module_name(target)
  _buildutil_mirror_parent(_mirror)
  install(PROGRAMS "${CMAKE_CURRENT_SOURCE_DIR}/${target}" DESTINATION "${_mirror}")
endfunction()

# A .test module is linked from TEST/BENCH groups; its own links are positional.
function(_buildutil_check_test_lane_links target positional grouped)
  get_property(_tl_self GLOBAL PROPERTY _buildutil_test_module_${target})
  if(_tl_self)
    if(grouped)
      message(FATAL_ERROR
        "${target} is a .test module and has no suite, so its "
        "Link_dependencies TEST/BENCH groups link nothing. Name them "
        "positionally: a .test module is test lane already.")
    endif()
    return()
  endif()
  foreach(_tl_dep IN LISTS positional)
    _buildutil_module_of_spelling("${_tl_dep}" _tl_module)
    if(NOT _tl_module)
      continue()
    endif()
    get_property(_tl_test GLOBAL PROPERTY _buildutil_test_module_${_tl_module})
    if(_tl_test)
      message(FATAL_ERROR
        "${target} links the .test module ${_tl_module} as a runtime "
        "dependency. A test-lane module is built only with the suites and "
        "never ships, so only a suite may link it: "
        "Link_dependencies(TEST ${_tl_dep}) in ${target}.")
    endif()
  endforeach()
endfunction()

# The module a leaf, full name or `::` alias names; empty for anything else.
function(_buildutil_module_of_spelling spelling out)
  string(REPLACE "::" ";" _ms_parts "${spelling}")
  set(_ms_clean "")
  foreach(_ms_part IN LISTS _ms_parts)
    _buildutil_kind_of("${_ms_part}" _ms_name _ms_kind)
    list(APPEND _ms_clean "${_ms_name}")
  endforeach()
  list(JOIN _ms_clean "-" _ms_joined)
  _buildutil_module_of("${_ms_joined}" _ms_module)
  set(${out} "${_ms_module}" PARENT_SCOPE)
endfunction()

# RUNTIME: modules this one loads at run time (dlopen) instead of linking.
# Resolved at the end of the tree, when every sibling target exists.
function(_buildutil_record_runtime_loads target dormant)
  foreach(dep IN LISTS ARGN)
    _buildutil_module_of_spelling("${dep}" dep_module)
    get_property(elsewhere GLOBAL PROPERTY _buildutil_module_elsewhere_${dep})
    if(NOT dep_module AND elsewhere)
      message(FATAL_ERROR
        "${target}: Link_dependencies(RUNTIME) names ${dep}, which does not "
        "build for ${CMAKE_SYSTEM_NAME}: the loader would start and find "
        "nothing to load.")
    endif()
    if(NOT dep_module)
      message(FATAL_ERROR
        "${target}: Link_dependencies(RUNTIME ${dep}) names no module of "
        "this project. RUNTIME is for a sibling module loaded at run time; "
        "a package is linked positionally.")
    endif()
    if(dep_module IN_LIST dormant)
      continue()
    endif()
    _buildutil_library_of("${dep_module}" dep_lib)
    set_property(GLOBAL APPEND PROPERTY _buildutil_runtime_loaders "${target}")
    set_property(GLOBAL APPEND PROPERTY _buildutil_loads_${target} "${dep_lib}")
  endforeach()
endfunction()

# Positional args link into the module library itself (PUBLIC for a
# static lib, INTERFACE for header-only) -- the -app executable and the
# test/bench binaries inherit them through it. The TEST / BENCH
# keywords — mirroring Require's — link only into the ${target}-tests /
# -benches executables, for deps those exercise (e.g. an integration test that
# runs a sibling module) but that the library must not pull in.
function(Link_dependencies)
  cmake_parse_arguments(LINK "" "" "TEST;BENCH;RUNTIME" ${ARGN})
  _buildutil_module_name(target)
  _buildutil_library_of("${target}" lib)
  set(_ld_grouped FALSE)
  if(LINK_TEST OR LINK_BENCH)
    set(_ld_grouped TRUE)
  endif()
  _buildutil_check_test_lane_links("${target}" "${LINK_UNPARSED_ARGUMENTS}"
                                   ${_ld_grouped})
  get_property(_ld_test_lane GLOBAL PROPERTY _buildutil_test_module_${target})
  if(_ld_test_lane AND NOT BUILD_TESTING AND NOT BUILD_BENCHMARKING)
    return()
  endif()
  # a dormant module leaves no target, so drop it from any dependant's link
  # list -- the dependant guards its use behind @MODULE_DEFINE_PREFIX@_<NAME>_ENABLED
  _buildutil_dormant_modules(dormant)
  if(dormant)
    list(REMOVE_ITEM LINK_UNPARSED_ARGUMENTS ${dormant})
  endif()
  # Callers name MODULES; link the module's LIBRARY. Since an executable
  # now holds the bare module name, linking the name verbatim would try to
  # link a dependency's APP -- which cmake reports as a missing library
  # rather than as the mistake it is.
  set(_linked "")
  foreach(dep IN LISTS LINK_UNPARSED_ARGUMENTS)
    _buildutil_library_of("${dep}" dep_lib)
    list(APPEND _linked "${dep_lib}")
    # remember the edge for the component manifest: a consumer linking
    # one component must pull the components IT needs, and only the
    # runtime list reaches a consumer at all
    _buildutil_module_of("${dep}" _dep_mod)
    if(_dep_mod)
      set_property(GLOBAL APPEND PROPERTY
        _buildutil_cmp_needs_${target} "${_dep_mod}")
    else()
      set_property(GLOBAL APPEND PROPERTY
        _buildutil_cmp_external_${target} "${dep}")
    endif()
  endforeach()
  set(LINK_UNPARSED_ARGUMENTS ${_linked})
  if(LINK_UNPARSED_ARGUMENTS)
    get_target_property(tgt_type ${lib} TYPE)
    if(tgt_type STREQUAL "INTERFACE_LIBRARY")
      target_link_libraries(${lib} INTERFACE ${LINK_UNPARSED_ARGUMENTS})
    else()
      target_link_libraries(${lib} PUBLIC ${LINK_UNPARSED_ARGUMENTS})
    endif()
  endif()
  # TEST/BENCH lists resolve module names to LIBRARIES exactly as the
  # positional list does -- they were missed by the 0.23.0 rename, so
  # Link_dependencies(TEST wlink) tried to link wlink's EXECUTABLE (the
  # bare name) and failed configure the moment a test-only dep
  # named a module with an app. Dormant modules drop out the same way
  # too, so a dependant's TEST deps behave like its real ones. Non-module
  # targets (GTest::gtest, host libs) pass through the resolver untouched.
  foreach(_kw TEST BENCH)
    if(LINK_${_kw})
      if(dormant)
        list(REMOVE_ITEM LINK_${_kw} ${dormant})
      endif()
      set(_resolved "")
      foreach(dep IN LISTS LINK_${_kw})
        _buildutil_library_of("${dep}" dep_lib)
        list(APPEND _resolved "${dep_lib}")
      endforeach()
      set(LINK_${_kw} ${_resolved})
    endif()
  endforeach()
  if(LINK_TEST AND TARGET ${target}-tests)
    target_link_libraries(${target}-tests PRIVATE ${LINK_TEST})
  endif()
  if(LINK_BENCH AND TARGET ${target}-benches)
    target_link_libraries(${target}-benches PRIVATE ${LINK_BENCH})
  endif()
  _buildutil_record_runtime_loads(${target} "${dormant}" ${LINK_RUNTIME})
endfunction()

# Patch-overlay: a source file under sources/ may carry a corrected copy generated at build
# time from a committed unified diff, so the in-tree source stays pristine. A patch is
# discovered two ways (both mirror the target's path, so the layout is self-describing):
#   (a) a sibling `<target>.patch` next to the target (foo/bar.txt.patch patches foo/bar.txt); or
#   (b) under a `<anyname>.patch/` directory at a path mirroring the target relative to that
#       directory's parent (foo/.patch/sub/baz.txt patches foo/sub/baz.txt). The name may be
#       empty, so a bare `.patch/` is valid.
# Each discovered patch gets one apply custom command (git apply, via cmake/apply_patch.py)
# whose OUTPUT is the patched copy at ${CMAKE_BINARY_DIR}/generated/<target-relative-to-source>.
# RESOLUTION RULE: to read a source at <rel>, prefer the patched copy if a patch was discovered
# for it, else the in-tree source. Resolve_generated_source() is that rule; both
# Add_generated_source (its DEPENDS) and the dgen loader (via the @MODULE_DEFINE_PREFIX@_GENERATED_DIR env var)
# use it, so a generator consumes — and rebuilds on a change to — the patched copy when present.
function(_buildutil_register_patch target_rel patch_abs)
  set(gen_root "${CMAKE_BINARY_DIR}/generated")
  set(out_abs "${gen_root}/${target_rel}")
  set(src_abs "${CMAKE_SOURCE_DIR}/${target_rel}")
  add_custom_command(
    OUTPUT  "${out_abs}"
    COMMAND "${BUILDUTIL_PY}" -m buildutil.apply_patch
            --source "${src_abs}" --patch "${patch_abs}" --output "${out_abs}"
    DEPENDS "${src_abs}" "${patch_abs}"
    COMMENT "patch-overlay: ${target_rel}"
    VERBATIM)
  set_source_files_properties("${out_abs}" PROPERTIES GENERATED TRUE)
  set_property(GLOBAL PROPERTY "@CMAKE_OPTION_PREFIX@_PATCHED_OUTPUT_${target_rel}" "${out_abs}")
endfunction()

# Discover every patch in the source tree and register its apply command. Runs once per configure
# (guarded by a global property). A single recursive glob enumerates every file under sources/
# (CONFIGURE_DEPENDS reglobs when a patch — or any source — appears/vanishes, the same trigger the
# per-module source globs already use); each file is then classified to its target by name:
#   * a `<anyname>.patch/` directory segment (the name may be empty -> a bare `.patch/`): the file
#     mirrors the target relative to that directory's parent — drop the one `.patch/` segment.
#   * a sibling `<target>.patch` file (no `.patch/` ancestor): the target is the name minus `.patch`.
# A `*` glob segment never matches a leading dot in CMake, so the dot directory can't be matched by
# pattern; recursing the whole tree and filtering by name sidesteps that and serves both forms DRY.
function(_buildutil_discover_patches)
  get_property(done GLOBAL PROPERTY @CMAKE_OPTION_PREFIX@_PATCHES_DISCOVERED)
  if(done)
    return()
  endif()
  set_property(GLOBAL PROPERTY @CMAKE_OPTION_PREFIX@_PATCHES_DISCOVERED TRUE)
  if(NOT Python3_Interpreter_FOUND)
    find_package(Python3 REQUIRED COMPONENTS Interpreter)
  endif()
  file(GLOB_RECURSE entries CONFIGURE_DEPENDS LIST_DIRECTORIES false "${CMAKE_SOURCE_DIR}/sources/*")
  foreach(entry IN LISTS entries)
    if(entry MATCHES "/sources/\\.archive/")
      continue()          # archived modules never build; nor do their patches
    endif()
    if(entry MATCHES "(^|/)[^/]*\\.patch/")
      string(REGEX REPLACE "/[^/]*\\.patch/" "/" target "${entry}")   # directory form: drop `.patch/`
    elseif(entry MATCHES "\\.patch$")
      string(REGEX REPLACE "\\.patch$" "" target "${entry}")          # sibling form: drop the suffix
    else()
      continue()
    endif()
    file(RELATIVE_PATH target_rel "${CMAKE_SOURCE_DIR}" "${target}")
    _buildutil_register_patch("${target_rel}" "${entry}")
  endforeach()
endfunction()

# The resolution rule: out_var <- the patched copy of a source path if one was discovered, else the
# in-tree source. The input may be a target name (passed through), a CMAKE_SOURCE_DIR-relative path,
# or an absolute path; an absolute path under the source tree is relativized first so it keys the
# patch map the same as the relative form (the dgen glob hands DEPENDS absolute paths). An absolute
# path outside the tree passes through untouched.
function(Resolve_generated_source rel out_var)
  _buildutil_discover_patches()
  if(TARGET "${rel}")
    set(${out_var} "${rel}" PARENT_SCOPE)
    return()
  endif()
  if(IS_ABSOLUTE "${rel}")
    file(RELATIVE_PATH under "${CMAKE_SOURCE_DIR}" "${rel}")
    if(under MATCHES "^\\.\\.")                       # outside CMAKE_SOURCE_DIR — leave it alone
      set(${out_var} "${rel}" PARENT_SCOPE)
      return()
    endif()
    set(rel "${under}")
  endif()
  get_property(patched GLOBAL PROPERTY "@CMAKE_OPTION_PREFIX@_PATCHED_OUTPUT_${rel}")
  if(patched)
    set(${out_var} "${patched}" PARENT_SCOPE)
  else()
    set(${out_var} "${CMAKE_SOURCE_DIR}/${rel}" PARENT_SCOPE)
  endif()
endfunction()

# Register a build-time generated source for the current submodule.
#
# Usage (called *after* Init_submodule() inside a module's CMakeLists):
#
#   Add_generated_source(
#     OUTPUT  "decoder/tables.hpp"
#     SCRIPT  "contrib/gen/tables.py"
#     DEPENDS "contrib/gen/tables-input.txt"
#   )
#
# OUTPUT is a relative path under the generated-sources tree at
# ${CMAKE_BINARY_DIR}/generated/<target>/. The macro adds that root to
# the target's include search path (PUBLIC for libs, PRIVATE for execs)
# so consumers can `#include "<target>/..."` for generated headers
# alongside the in-tree ones.
#
# SCRIPT and each DEPENDS entry are resolved relative to
# CMAKE_SOURCE_DIR (the repo root). Absolute paths are accepted as-is.
# Target names in DEPENDS are also valid — cmake adds a build-order
# edge from the named target to the generator. The script is invoked
# with `--output <abs path>` followed by any extra ARGS, and SCRIPT
# is implicitly added to DEPENDS so editing it triggers regeneration.
#
# ARGS is a list of extra arguments appended to the generator command
# after `--output`. Generator-expressions like `$<TARGET_FILE:mytool>`
# work — useful for scripts that shell out to a built executable.
#
# STAGE selects which target the generated file is wired into:
#   BUILD (default) — the submodule's main target (lib or exec).
#   TEST            — ${target}-tests. Skipped if BUILD_TESTING is OFF
#                     or the tests target doesn't exist.
#   BENCH           — ${target}-benches. Skipped if BUILD_BENCHMARKING
#                     is OFF or the benches target doesn't exist.
# Non-BUILD stages let bench/test-only generators co-locate in the
# module's CMakeLists without manual `if(BUILD_BENCHMARKING ...)`
# wrappers — the helper silently no-ops on a stage that's disabled.
function(Add_generated_source)
  cmake_parse_arguments(GEN "" "OUTPUT;SCRIPT;COMMENT;STAGE"
                              "DEPENDS;ARGS;SIDE_OUTPUTS" ${ARGN})
  if(NOT GEN_OUTPUT)
    message(FATAL_ERROR "Add_generated_source: OUTPUT is required")
  endif()
  if(NOT GEN_SCRIPT)
    message(FATAL_ERROR "Add_generated_source: SCRIPT is required")
  endif()
  if(IS_ABSOLUTE "${GEN_OUTPUT}")
    message(FATAL_ERROR
      "Add_generated_source: OUTPUT must be a relative path "
      "(got '${GEN_OUTPUT}')")
  endif()
  if(NOT GEN_STAGE)
    set(GEN_STAGE "BUILD")
  endif()

  _buildutil_module_name(target)

  if(GEN_STAGE STREQUAL "BUILD")
    # The module's LIBRARY, which since the -app rename is no longer the
    # bare module name -- that is now the executable. Attaching here put
    # the generated TU on the app instead: the library's own sources got
    # no build-order edge to the rule that writes it (a clean build then
    # raced and an incremental one hid it), and the TU itself left the
    # library, so nothing linking the module -- its own -tests above all
    # -- saw it at all. Every other call in this file was moved onto
    # _buildutil_library_of; this one was missed.
    _buildutil_library_of("${target}" target)
    # A module whose only source is main.cpp has an INTERFACE library,
    # which cannot carry sources at all. There the executable IS the only
    # compilable target, so it is the right home rather than a fallback.
    get_target_property(_gen_home_type ${target} TYPE)
    if(_gen_home_type STREQUAL "INTERFACE_LIBRARY")
      _buildutil_module_name(target)
    endif()
  elseif(GEN_STAGE STREQUAL "DATA")
    # Runtime DATA, not a target source: the output is a generated
    # *.install entry. OUTPUT is the PREFIX-ROOTED shipped path, exactly
    # as the path inside a *.install/ tree is -- staged at
    # <build>/<path>, installed at <prefix>/<path>, no destination
    # mapping anywhere. The rule hangs off the module's library through
    # a wrapper target so building the module produces its data; the
    # per-rule DEPENDS_EXPLICIT_ONLY below keeps the module's dependency
    # closure off the rule, so the entangled-pair cycle cannot re-enter
    # through this route either.
  elseif(GEN_STAGE STREQUAL "TEST")
    if(NOT BUILD_TESTING)
      return()
    endif()
    set(target "${target}-tests")
  elseif(GEN_STAGE STREQUAL "BENCH")
    if(NOT BUILD_BENCHMARKING)
      return()
    endif()
    set(target "${target}-benches")
  else()
    message(FATAL_ERROR
      "Add_generated_source: STAGE must be BUILD/TEST/BENCH "
      "(got '${GEN_STAGE}')")
  endif()
  if(NOT TARGET ${target})
    return()
  endif()

  if(NOT Python3_Interpreter_FOUND)
    find_package(Python3 REQUIRED COMPONENTS Interpreter)
  endif()

  set(gen_root "${CMAKE_BINARY_DIR}/generated")
  _buildutil_module_name(submodule)
  if(GEN_STAGE STREQUAL "DATA")
    set(out_abs "${CMAKE_BINARY_DIR}/${GEN_OUTPUT}")
  else()
    set(out_abs  "${gen_root}/${submodule}/${GEN_OUTPUT}")
  endif()
  set(side_abs "")                                  # extra files the script emits next to OUTPUT
  foreach(side IN LISTS GEN_SIDE_OUTPUTS)
    if(GEN_STAGE STREQUAL "DATA")
      list(APPEND side_abs "${CMAKE_BINARY_DIR}/${side}")
    else()
      list(APPEND side_abs "${gen_root}/${submodule}/${side}")
    endif()
  endforeach()

  if(IS_ABSOLUTE "${GEN_SCRIPT}")
    set(script_abs "${GEN_SCRIPT}")
  else()
    set(script_abs "${CMAKE_SOURCE_DIR}/${GEN_SCRIPT}")
  endif()

  # Resolve every DEPENDS through the patch-overlay rule, so a dep that carries a patch becomes a
  # dependency on (and a rebuild trigger from) its patched copy — and the patch-apply command is
  # wired in as a build-order edge automatically (its OUTPUT is that copy). Targets pass through.
  set(deps_abs "")
  foreach(dep IN LISTS GEN_DEPENDS)
    Resolve_generated_source("${dep}" resolved_dep)
    list(APPEND deps_abs "${resolved_dep}")
  endforeach()

  # An ARGS element that names a file the generator READS is a dependency,
  # and saying so twice is the largest source of declaration mass in a
  # module that drives a tool over its own sources -- 69 of 72 DEPENDS
  # entries in the reporting tree restated a path ARGS already carried.
  # Worse than verbose: the two can disagree, and a DEPENDS that drops a
  # path ARGS still passes means the generator silently stops rerunning
  # when that input changes.
  #
  # Only what can be decided HERE, with no guessing:
  #   * a generator expression is skipped -- it has no value at configure
  #     time, and $<TARGET_FILE:...> already carries its own build edge;
  #   * a path must actually exist, as a file, relative to the module or
  #     the repo root -- an option value that happens to look like a path
  #     names nothing and adds nothing;
  #   * this rule's OWN outputs are never inputs, or the command would
  #     depend on itself.
  # DEPENDS stays for the edges no command line can show: files the tool
  # opens itself (include fragments, inserts), which was the other 3 of 72.
  foreach(arg IN LISTS GEN_ARGS)
    if(arg MATCHES "\\$<")
      continue()
    endif()
    set(candidate "")
    if(IS_ABSOLUTE "${arg}" AND EXISTS "${arg}")
      set(candidate "${arg}")
    elseif(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/${arg}")
      set(candidate "${CMAKE_CURRENT_SOURCE_DIR}/${arg}")
    elseif(EXISTS "${CMAKE_SOURCE_DIR}/${arg}")
      set(candidate "${CMAKE_SOURCE_DIR}/${arg}")
    endif()
    if(NOT candidate OR IS_DIRECTORY "${candidate}")
      continue()
    endif()
    get_filename_component(candidate "${candidate}" ABSOLUTE)
    if(candidate STREQUAL out_abs OR candidate IN_LIST side_abs
       OR candidate IN_LIST deps_abs)
      continue()
    endif()
    Resolve_generated_source("${candidate}" resolved_dep)
    list(APPEND deps_abs "${resolved_dep}")
  endforeach()

  if(NOT GEN_COMMENT)
    set(GEN_COMMENT "${target}: generating ${GEN_OUTPUT}")
  endif()

  # Each rule OWNS its dependency set. cmake pools a target's dependency
  # closure onto every custom command attached to it, so in a mutually
  # entangled pair (compiler <-> table generator) a tool edge belonging
  # to ONE rule gets stamped onto its siblings and ninja reports a cycle
  # the real file graph does not contain -- measured by the consumer on
  # cmake 4.4.2 (full measurement lives with that project). Per-rule
  # DEPENDS_EXPLICIT_ONLY (cmake 3.27+) stops the pooling; the tool
  # edges each rule genuinely has are re-stated explicitly, derived from
  # the $<TARGET_FILE:...> references already in its ARGS -- nothing new
  # is declared. On cmake older than 3.27 the keyword does not exist and
  # behaviour is unchanged, pooled deps and all.
  set(_gen_tool_deps "")
  foreach(arg IN LISTS GEN_ARGS)
    # double escape: the parser eats one level, the regex needs \$ --
    # a single \$ reaches the engine as a bare $, the end-of-line anchor,
    # and matches nothing, silently
    string(REGEX MATCHALL "\\$<TARGET_FILE:[^>]+>" _gen_tfs "${arg}")
    list(APPEND _gen_tool_deps ${_gen_tfs})
  endforeach()
  set(_gen_explicit "")
  if(CMAKE_VERSION VERSION_GREATER_EQUAL 3.27)
    set(_gen_explicit DEPENDS_EXPLICIT_ONLY)
  endif()

  # The generator opens its dgen inputs through the same rule (dgen_common.resolve_source);
  # @MODULE_DEFINE_PREFIX@_GENERATED_DIR points it at the patched copies' root.
  add_custom_command(
    OUTPUT  "${out_abs}" ${side_abs}
    COMMAND "${CMAKE_COMMAND}" -E env "@MODULE_DEFINE_PREFIX@_GENERATED_DIR=${gen_root}"
            "${Python3_EXECUTABLE}" "${script_abs}" --output "${out_abs}"
            ${GEN_ARGS}
    WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}"
    DEPENDS "${script_abs}" ${deps_abs} ${_gen_tool_deps}
    COMMENT "${GEN_COMMENT}"
    VERBATIM
    COMMAND_EXPAND_LISTS
    ${_gen_explicit}
  )

  if(GEN_STAGE STREQUAL "DATA")
    string(MAKE_C_IDENTIFIER "data-${submodule}-${GEN_OUTPUT}" _data_wrap)
    add_custom_target(${_data_wrap} ALL DEPENDS "${out_abs}" ${side_abs})
    _buildutil_library_of("${submodule}" _data_lib)
    if(TARGET ${_data_lib})
      add_dependencies(${_data_lib} ${_data_wrap})
    endif()
    foreach(_data_out IN ITEMS "${GEN_OUTPUT}" ${GEN_SIDE_OUTPUTS})
      # the same both-locations rule as a *.install/ file, one phase
      # later: this one only exists once the generator has run
      add_custom_command(TARGET ${_data_wrap} POST_BUILD
        COMMAND "${CMAKE_COMMAND}" -E copy_if_different
                "${CMAKE_BINARY_DIR}/${_data_out}"
                "${CMAKE_BINARY_DIR}/bin/${_data_out}")
      get_filename_component(_data_dir "${_data_out}" DIRECTORY)
      if(_data_dir)
        install(FILES "${CMAKE_BINARY_DIR}/${_data_out}" DESTINATION "${_data_dir}")
      else()
        install(FILES "${CMAKE_BINARY_DIR}/${_data_out}" DESTINATION ".")
      endif()
    endforeach()
    return()
  endif()

  set_source_files_properties("${out_abs}" PROPERTIES GENERATED TRUE)
  target_sources(${target} PRIVATE "${out_abs}")
  foreach(side IN LISTS side_abs)                   # side outputs also build before the target
    set_source_files_properties("${side}" PROPERTIES GENERATED TRUE)
    target_sources(${target} PRIVATE "${side}")
  endforeach()

  # The compiler depfile doesn't track `#embed`, so a source that embeds this output
  # never recompiles when it regenerates. Scan the target's compiled sources for an
  # `#embed <…>` of this output (by file name) and wire it via OBJECT_DEPENDS.
  get_filename_component(embed_name "${GEN_OUTPUT}" NAME)
  get_target_property(gen_srcs ${target} SOURCES)
  foreach(src IN LISTS gen_srcs)
    if(NOT src MATCHES "\\.(c|cc|cpp|cxx)$")
      continue()
    endif()
    if(IS_ABSOLUTE "${src}")
      set(src_abs "${src}")
    else()
      set(src_abs "${CMAKE_CURRENT_SOURCE_DIR}/${src}")
    endif()
    if(NOT EXISTS "${src_abs}")
      continue()
    endif()
    file(STRINGS "${src_abs}" embed_lines REGEX "^[[:space:]]*#embed[[:space:]]*[<\"]")
    foreach(line IN LISTS embed_lines)
      string(REGEX MATCH "[<\"]([^>\"]+)[>\"]" matched "${line}")
      get_filename_component(embed_base "${CMAKE_MATCH_1}" NAME)
      if(embed_base STREQUAL embed_name)
        set_source_files_properties("${src_abs}" PROPERTIES OBJECT_DEPENDS "${out_abs}")
      endif()
    endforeach()
  endforeach()

  get_target_property(tgt_type ${target} TYPE)
  if(tgt_type STREQUAL "INTERFACE_LIBRARY")
    set(gen_scope INTERFACE)
  elseif(tgt_type STREQUAL "EXECUTABLE")
    set(gen_scope PRIVATE)
  else()
    set(gen_scope PUBLIC)
  endif()
  # Both roots, and both through the consolidated function so #embed sees
  # exactly what #include does. The parent alone was reachable before,
  # which meant this route resolved a sibling's "<module>/foo.gh" but not
  # its own unqualified "foo.gh" -- an asymmetry with the configure.py
  # route that consumers were papering over with a project-local override.
  _buildutil_add_generated_roots(${target} ${gen_scope}
    "${gen_root}" "${gen_root}/${submodule}")
endfunction()

# TREE-WIDE SETUP, root directory scope. pybind11 is located here rather
# than inside the bridge, where the prefix path died with the call and a
# module that EMBEDS the interpreter re-probed it by hand. Without
# BUILDUTIL_PY a project may have no python at all; the bridge still probes.
if(BUILDUTIL_PY AND NOT CMAKE_CROSSCOMPILING)
  _buildutil_pybind11_prefix(_buildutil_pybind_dir)
  if(_buildutil_pybind_dir)
    list(APPEND CMAKE_PREFIX_PATH "${_buildutil_pybind_dir}")
    # or pybind11's config drops to FindPythonLibs (Require is a function,
    # so the Python3_FOUND that selects FindPython died with the call) and
    # binds against the first python3 on PATH: the wrong ABI tag.
    if(NOT DEFINED Python_EXECUTABLE)
      set(Python_EXECUTABLE "${BUILDUTIL_PY}" CACHE FILEPATH
          "the interpreter buildutil runs under")
    endif()
    if(NOT DEFINED Python3_EXECUTABLE)
      set(Python3_EXECUTABLE "${BUILDUTIL_PY}" CACHE FILEPATH
          "the interpreter buildutil runs under")
    endif()
    if(NOT DEFINED PYBIND11_FINDPYTHON)
      set(PYBIND11_FINDPYTHON ON)
    endif()
  endif()
endif()

cmake_language(DEFER DIRECTORY "${CMAKE_SOURCE_DIR}"
  CALL _buildutil_apply_install_rpaths)

# ---------------------------------------------------------------------------
# Declared resources ([resources] / [[resources]] in buildutil.toml), rendered
# here as one call per declaration. config.py parsed and validated them; this
# is the whole of what a project has to write, and it is not cmake.
# ---------------------------------------------------------------------------
@RESOURCE_SETS@
# ---------------------------------------------------------------------------
# Declared project options ([options]), rendered as the one call that writes
# the header and force-includes it. Empty when the project declares none, so
# a project without options pays nothing.
# ---------------------------------------------------------------------------
@PROJECT_OPTIONS@
# ---------------------------------------------------------------------------
# The build's identity (_bdudata/buildinfo.json), as cmake variables here and
# as macros in a header no compile line carries. Nothing is declared for it:
# every project gets it.
# ---------------------------------------------------------------------------
@BUILD_IDENTITY@
_buildutil_public_macro()
# ---------------------------------------------------------------------------
# Declared runtime payload ([runtime]), macOS bundle ([bundle.macos]) and
# python suites ([test] python), rendered from buildutil.toml. Each is empty
# when the project declares nothing.
# ---------------------------------------------------------------------------
@MODULE_FRAMEWORKS@
@RUNTIME_PAYLOAD@
@MACOS_BUNDLE@
@PYTHON_SUITES@
# ---------------------------------------------------------------------------
# Declared cmake extensions ([cmake] extensions in buildutil.toml), rendered
# into this directory beside this file. Included HERE -- after the machinery
# above, so an extension may call and wrap it, and before the project's own
# cmake/, so a project still has the last word.
#
# An extension hooks the build by DEFINING one of the optional commands the
# machinery calls where present: _buildutil_ext_pre_scan(), once before any
# module is added, and _buildutil_ext_module(<lib> <target>), once per module
# after its include roots are set. Defining neither is fine -- watcom does
# not, it publishes Init_firmware() for a module to call instead.
#
# KNOWN LIMIT: two extensions defining the SAME hook collide, and the last
# one included silently wins. One extension uses hooks today (reflect); the
# day a second does, this must become a dispatch over a registered list
# rather than a bare command name.
# ---------------------------------------------------------------------------
set(_buildutil_extensions @EXTENSIONS@)
foreach(_buildutil_ext IN LISTS _buildutil_extensions)
  if(EXISTS "${_buildutil_cmake_dir}/${_buildutil_ext}.cmake")
    include("${_buildutil_cmake_dir}/${_buildutil_ext}.cmake")
  endif()
endforeach()

# ---------------------------------------------------------------------------
# Project-local cmake, presence-driven: every *.cmake a project drops in its
# own `cmake/` directory is included right here -- sorted, and AFTER all the
# machinery above is defined, so a project helper can call Init_submodule(),
# wrap it, or add functions of its own and every module's CMakeLists will see
# them. Nothing to declare, nothing to register: drop the file in and the
# next configure picks it up (CONFIGURE_DEPENDS re-runs cmake when the set
# changes).
#
# These files are the PROJECT's, not buildutil's. They live in the repo, they
# are committed, and buildutil never renders, rewrites or upgrades them --
# the exact opposite of this file's own directory (_bdudata/cmake/), which is
# derived data and is replaced wholesale on every build. That is the seam:
# machinery upgrades itself and must not be edited; `cmake/` is yours and is
# never touched.
# ---------------------------------------------------------------------------
file(GLOB _buildutil_local_cmake CONFIGURE_DEPENDS
     "${CMAKE_SOURCE_DIR}/cmake/*.cmake")
list(SORT _buildutil_local_cmake)
foreach(_local IN LISTS _buildutil_local_cmake)
  file(RELATIVE_PATH _local_rel "${CMAKE_SOURCE_DIR}" "${_local}")
  message(STATUS "project cmake: ${_local_rel}")
  include("${_local}")
endforeach()
