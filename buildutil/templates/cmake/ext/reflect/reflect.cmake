# C++ reflection codegen. Declared with `buildutil extend reflect` (or
# [cmake] extensions in buildutil.toml); a project that does not declare it
# pays nothing -- no scan, no flags, no libclang in the venv.
#
# A type opts in by befriending the generator's entry point:
#
#     struct HelloWorld: Command
#     {
#       friend constexpr auto reflect_scheme(HelloWorld*);
#       bool verbose{ false };            /* say more */
#     };
#
# and buildutil emits TWO files per tagged header into the generated root:
#
#   generated/<rel>/x.reflect.hpp   the schemes
#   generated/<rel>/x.hpp           the DETOUR -- #pragma once, the real
#                                   header by absolute path, then the
#                                   schemes
#
# and puts the generated root AHEAD of sources/ on every include path. So
# #include "<rel>/x.hpp" -- anywhere, at any depth -- resolves to the detour,
# and a translation unit that has the class always has its schemes. Nothing
# under sources/ is touched, and the real header is still parsed in place, so
# an error or a goto-definition in class code lands in the file the author
# edits.
#
# The deprecated force-include modes ([reflect] include = source|module)
# remain one release; a per-TU delivery can silently miss a transitively
# reached class.
#
# What a declaration says about itself beyond its name -- an external LABEL,
# free-form META tags -- is written either as a real attribute in buildutil's
# namespace ([[buildutil::label("system")]]) or with the shipped `_Label` /
# `_Meta` macros. [reflect] annotation says what those macros expand to:
# "macro" (the default) is nothing at all, "attribute" is the real thing,
# and only the latter adds the per-compiler suppression flags below.
#
# Two phases, and the split is forced rather than chosen:
# set_source_files_properties() runs at CONFIGURE time, so the output NAMES
# must be known then -- but the CONTENTS need not be, and parsing is the
# expensive half.
#
#   configure   one python pass: grep for the tag, derive output paths
#               without parsing, refuse any include spelling that would go
#               around a detour, render the support header, write the
#               declarations below.
#   build       one add_custom_command per header: libclang parses and
#               emits both files, and writes a depfile so the whole include
#               closure is a dependency and not just the header itself.

set(_buildutil_reflect_namespace "@REFLECT_NAMESPACE@")
set(_buildutil_reflect_scan_mode "@REFLECT_SCAN@")
set(_buildutil_reflect_include_mode "@REFLECT_INCLUDE@")
set(_buildutil_reflect_annotation "@REFLECT_ANNOTATION@")
set(_buildutil_reflect_macros "@REFLECT_MACROS@")

# [reflect] macros names buildutil's own header ("auto"), nothing ("none"),
# or the project's own file -- and a file is named the way a project names
# things, from the repo root.
function(_buildutil_reflect_macro_path out)
  set(spelling "${_buildutil_reflect_macros}")
  if(spelling STREQUAL "auto" OR spelling STREQUAL "none"
     OR IS_ABSOLUTE "${spelling}")
    set(${out} "${spelling}" PARENT_SCOPE)
  else()
    set(${out} "${CMAKE_SOURCE_DIR}/${spelling}" PARENT_SCOPE)
  endif()
endfunction()

# An unknown attribute is a WARNING on every compiler, and in attribute mode
# there is one per annotated declaration -- so a project that opts in gets
# them silenced, and only that project.
#
# gcc takes the namespace and keeps warning about everything else (12+, and
# older gcc has no such option at all -- hence the version guard). clang has
# no such granularity, so its flag is blunt; that is the cost of the tier,
# and what the generator's strict validation of the buildutil:: namespace
# buys back. MSVC spells the same warning C5030.
function(_buildutil_reflect_suppress)
  add_compile_options(
    "$<$<AND:$<COMPILE_LANG_AND_ID:CXX,GNU>,$<VERSION_GREATER_EQUAL:$<CXX_COMPILER_VERSION>,12>>:-Wno-attributes=buildutil::>"
    "$<$<COMPILE_LANG_AND_ID:CXX,Clang,AppleClang>:-Wno-unknown-attributes>"
    "$<$<COMPILE_LANG_AND_ID:CXX,MSVC>:/wd5030>")
endfunction()

# Property names cannot carry / or . -- keys are derived, never the path.
function(_buildutil_reflect_key path out)
  string(REGEX REPLACE "[^A-Za-z0-9]" "_" key "${path}")
  set(${out} "${key}" PARENT_SCOPE)
endfunction()

# Called by the scan's generated .cmake, once per tagged header. Recording
# into globals rather than parsing a manifest in cmake: the python side
# already knows the answer, so it emits calls instead of data.
function(_buildutil_reflect_declare header reflect_rel detour_rel users)
  set_property(GLOBAL APPEND PROPERTY _buildutil_reflect_headers "${header}")
  _buildutil_reflect_key("${header}" key)
  set_property(GLOBAL PROPERTY _buildutil_reflect_rel_${key} "${reflect_rel}")
  set_property(GLOBAL PROPERTY _buildutil_reflect_detour_${key} "${detour_rel}")
  set_property(GLOBAL PROPERTY _buildutil_reflect_users_${key} "${users}")
endfunction()

function(_buildutil_reflect_python out_env out_exe)
  # The venv interpreter, because that is where libclang lands; the buildutil
  # package itself is not installed there, so its parent goes on PYTHONPATH
  # the same way BUILDUTIL_PYSUPPORT reaches the configure-hook library.
  set(pypath "${BUILDUTIL_PYPATH}")
  if(NOT pypath AND BUILDUTIL_PYSUPPORT)
    get_filename_component(pypath "${BUILDUTIL_PYSUPPORT}" DIRECTORY)
    get_filename_component(pypath "${pypath}" DIRECTORY)
  endif()
  set(${out_env} "PYTHONPATH=${pypath}" PARENT_SCOPE)
  set(${out_exe} "${Python3_EXECUTABLE}" PARENT_SCOPE)
endfunction()

function(_buildutil_reflect_std out)
  if(CMAKE_CXX_STANDARD)
    set(${out} "c++${CMAKE_CXX_STANDARD}" PARENT_SCOPE)
  else()
    set(${out} "c++23" PARENT_SCOPE)
  endif()
endfunction()

# --- phase 1: configure -----------------------------------------------------

function(_buildutil_ext_pre_scan)
  set(generated "${CMAKE_BINARY_DIR}/generated")
  set(declarations "${CMAKE_BINARY_DIR}/_buildutil-reflect.cmake")
  _buildutil_reflect_python(env exe)
  _buildutil_reflect_macro_path(macros)

  execute_process(
    COMMAND "${CMAKE_COMMAND}" -E env "${env}"
            "${exe}" -m buildutil.reflect scan
            --root "${CMAKE_SOURCE_DIR}"
            --sources "${CMAKE_SOURCE_DIR}/sources"
            --generated "${generated}"
            --out "${declarations}"
            --namespace "${_buildutil_reflect_namespace}"
            --scan "${_buildutil_reflect_scan_mode}"
            --include "${_buildutil_reflect_include_mode}"
            --annotation "${_buildutil_reflect_annotation}"
            --macros "${macros}"
            --compiler "${CMAKE_CXX_COMPILER}"
    RESULT_VARIABLE result OUTPUT_VARIABLE output ERROR_VARIABLE errors)
  if(NOT result EQUAL 0)
    message(FATAL_ERROR
      "buildutil reflect: scan failed (${result})\n${output}${errors}")
  endif()

  # The support surface the scan just rendered -- reflect.hpp, and
  # reflect-macros.hpp when the macro tier is on -- is PUBLIC API of any
  # package whose installed headers say #include <_buildutil/reflect.hpp>,
  # so the extension installs it beside them. The DIRECTORY, not named
  # files: the surface is whatever the generator emitted, and a project
  # hand-listing files is how a grown surface ships broken, which is what
  # a package found at publish time. Registered before the
  # no-tagged-headers return: the support header renders unconditionally,
  # and a project whose schemes are all hand-written still ships it.
  install(DIRECTORY "${generated}/_buildutil/"
          DESTINATION "include/_buildutil"
          FILES_MATCHING PATTERN "*.hpp")

  # Before the early return below: a project in attribute mode is in it
  # whether or not any header carries the tag today.
  if(_buildutil_reflect_annotation STREQUAL "attribute")
    _buildutil_reflect_suppress()
  endif()

  # Adding the tag to an EXISTING header changes no glob, so nothing here
  # would notice it on its own. It does not have to: `buildutil build` always
  # configures before it builds, so the scan re-runs every time, and raw
  # ninja is refused by the driver guard. Listing the declarations file as a
  # configure dependency keeps a hand-run cmake honest as well.
  set_property(DIRECTORY "${CMAKE_SOURCE_DIR}"
               APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${declarations}")
  include("${declarations}")

  get_property(headers GLOBAL PROPERTY _buildutil_reflect_headers)
  if(NOT headers)
    return()
  endif()

  if(_buildutil_reflect_include_mode STREQUAL "detour")
    # The whole delivery mechanism, in one line: the generated root goes in
    # FRONT of everything, so <rel>/x.hpp finds the detour and not the real
    # header. At DIRECTORY scope from sources/ rather than per target,
    # because a target's include path STARTS with its directory's -- which
    # is the only way to also get in front of the roots a test or bench
    # executable restates for itself when it links nothing (see the
    # MODULE_LIBRARY branch in buildutil.cmake).
    include_directories(BEFORE "${generated}")
  else()
    # WARNING and not DEPRECATION: message(DEPRECATION) prints nothing
    # unless CMAKE_WARN_DEPRECATED is on, and a deprecation nobody is shown
    # is not one.
    message(WARNING
      "buildutil reflect: [reflect] include = \"${_buildutil_reflect_include_mode}\" "
      "is the force-include path, which cannot deliver a scheme reached "
      "transitively. Drop the key to take the "
      "default \"detour\"; this mode goes away next release.")
  endif()

  # Every command is created HERE, in one directory, rather than in each
  # module: add_custom_command(OUTPUT) needs no target, only a consumer, and
  # the single target below is that consumer. A module then owes reflection
  # exactly one add_dependencies edge instead of a rule of its own.
  #
  # The output NAMES are all this phase can settle. The rules themselves wait
  # for the end of this directory (_buildutil_reflect_emit_commands), because
  # what they have to carry is the consuming module's include path and not
  # one module target exists yet. Declaring the consumer before its producers
  # is fine: add_custom_target(DEPENDS) binds a file to the rule that makes it
  # at GENERATE time, and both ends land in this same directory.
  _buildutil_reflect_std(std)
  set(outputs "")
  foreach(header IN LISTS headers)
    _buildutil_reflect_key("${header}" key)
    get_property(rel GLOBAL PROPERTY _buildutil_reflect_rel_${key})
    get_property(detour GLOBAL PROPERTY _buildutil_reflect_detour_${key})
    list(APPEND outputs "${generated}/${rel}")
    set_source_files_properties("${generated}/${rel}"
                                PROPERTIES GENERATED TRUE)
    if(detour)
      list(APPEND outputs "${generated}/${detour}")
      set_source_files_properties("${generated}/${detour}"
                                  PROPERTIES GENERATED TRUE)
    endif()
  endforeach()

  # Same DEFER argument trap as _buildutil_reflect_whole_module below: a
  # deferred call's arguments expand in the DEFERRED scope, so the state
  # rides globals rather than being passed.
  set_property(GLOBAL PROPERTY _buildutil_reflect_generated "${generated}")
  set_property(GLOBAL PROPERTY _buildutil_reflect_env "${env}")
  set_property(GLOBAL PROPERTY _buildutil_reflect_exe "${exe}")
  set_property(GLOBAL PROPERTY _buildutil_reflect_std "${std}")
  set_property(GLOBAL PROPERTY _buildutil_reflect_macro_file "${macros}")
  cmake_language(DEFER DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
                 CALL _buildutil_reflect_emit_commands)

  add_custom_target(buildutil-reflect DEPENDS ${outputs})
endfunction()

# Which module owns a header, and therefore whose include path parses it.
#
# Longest directory prefix wins, so a module nested under a group beats the
# group. A header no module claims -- one sitting in a source pool -- takes
# the UNION of every module instead: the field lesson is that
# a PARTIAL include path is worse than none at all (clang then hard-fails on
# the next unreachable header instead of degrading), so where the answer is
# not knowable the answer has to be a superset, never a guess.
function(_buildutil_reflect_owner header_abs out)
  get_property(dirs GLOBAL PROPERTY _buildutil_reflect_module_dirs)
  set(best "")
  set(best_length 0)
  foreach(dir IN LISTS dirs)
    string(FIND "${header_abs}" "${dir}/" at)
    if(at EQUAL 0)
      string(LENGTH "${dir}" length)
      if(length GREATER best_length)
        set(best "${dir}")
        set(best_length ${length})
      endif()
    endif()
  endforeach()

  set(libs "")
  if(best)
    _buildutil_reflect_key("${best}" key)
    get_property(libs GLOBAL PROPERTY _buildutil_reflect_dirlib_${key})
  else()
    foreach(dir IN LISTS dirs)
      _buildutil_reflect_key("${dir}" key)
      get_property(one GLOBAL PROPERTY _buildutil_reflect_dirlib_${key})
      list(APPEND libs "${one}")
    endforeach()
    list(REMOVE_DUPLICATES libs)
  endif()
  set(${out} "${libs}" PARENT_SCOPE)
endfunction()

# The include path of a target, as --include-dir arguments.
#
# INCLUDE_DIRECTORIES is read as a GENERATOR expression and not with
# get_target_property: the raw property holds only what the target set on
# itself, while the same property evaluated at generate time also carries the
# INTERFACE_INCLUDE_DIRECTORIES of everything it links -- conan packages
# included. That is what makes the Link_dependencies() ordering a non-issue:
# the module hook runs BEFORE a module's link edges exist, but nothing is
# read until every directory in the project has been processed.
#
# $<JOIN> plus COMMAND_EXPAND_LISTS on the command is what turns one
# semicolon-joined string into separate arguments. The $<BOOL:> guard is for
# the target that has no include directories at all: without it the argument
# would survive as a bare `--include-dir` swallowing whatever came next,
# whereas an empty expansion simply vanishes.
function(_buildutil_reflect_include_flags libs out)
  set(flags "")
  foreach(lib IN LISTS libs)
    if(NOT TARGET ${lib})
      continue()
    endif()
    set(dirs "$<TARGET_PROPERTY:${lib},INCLUDE_DIRECTORIES>")
    # The \; are literal semicolons that are NOT list separators: each lib
    # contributes ONE list element, one add_custom_command argument, and one
    # genex -- which COMMAND_EXPAND_LISTS then splits on those same
    # semicolons once it has a value. An unescaped ; here would tear the
    # genex into fragments at list(APPEND) and never survive to be evaluated.
    list(APPEND flags
      "$<$<BOOL:${dirs}>:--include-dir\;$<JOIN:${dirs},\;--include-dir\;>>")
  endforeach()
  set(${out} "${flags}" PARENT_SCOPE)
endfunction()

# Deferred to the end of the sources directory: every module CMakeLists has
# run by now, so every module target and every Link_dependencies() edge that
# feeds an include path exists.
function(_buildutil_reflect_emit_commands)
  get_property(headers GLOBAL PROPERTY _buildutil_reflect_headers)
  get_property(generated GLOBAL PROPERTY _buildutil_reflect_generated)
  get_property(env GLOBAL PROPERTY _buildutil_reflect_env)
  get_property(exe GLOBAL PROPERTY _buildutil_reflect_exe)
  get_property(std GLOBAL PROPERTY _buildutil_reflect_std)
  get_property(macros GLOBAL PROPERTY _buildutil_reflect_macro_file)

  # A project's own macro file TEACHES the generator its spellings, so
  # editing it changes what every header means and every reflect file is
  # stale. buildutil's own header is derived from config and re-rendered by
  # the scan, so it needs no such edge.
  set(macro_dep "")
  if(NOT macros STREQUAL "auto" AND NOT macros STREQUAL "none")
    set(macro_dep "${macros}")
  endif()

  set(explicit "")
  if(CMAKE_VERSION VERSION_GREATER_EQUAL 3.27)
    # Without this cmake pools the consuming target's whole dependency
    # closure onto every rule, which is a ninja cycle waiting to happen --
    # the same reason Add_generated_source sets it.
    set(explicit DEPENDS_EXPLICIT_ONLY)
  endif()

  foreach(header IN LISTS headers)
    _buildutil_reflect_key("${header}" key)
    get_property(rel GLOBAL PROPERTY _buildutil_reflect_rel_${key})
    get_property(detour_rel GLOBAL PROPERTY _buildutil_reflect_detour_${key})
    set(header_abs "${CMAKE_SOURCE_DIR}/${header}")
    set(output "${generated}/${rel}")
    file(RELATIVE_PATH spelling "${CMAKE_SOURCE_DIR}/sources" "${header_abs}")

    _buildutil_reflect_owner("${header_abs}" owners)
    _buildutil_reflect_include_flags("${owners}" inherited)

    # Both files come out of ONE parse. A second rule for the detour would
    # buy nothing -- its content is derived from paths alone -- and would
    # cost a second python start per header.
    set(detour_args "")
    set(detour_out "")
    if(detour_rel)
      set(detour_out "${generated}/${detour_rel}")
      set(detour_args --detour "${detour_out}"
                      --sources "${CMAKE_SOURCE_DIR}/sources")
    endif()

    # The generator's OWN include path keeps sources/ in front, which is the
    # opposite of the compiler's on purpose: it must parse the REAL header,
    # never a detour that may or may not have been written yet, so that what
    # it reads does not depend on build order.
    add_custom_command(
      OUTPUT "${output}" ${detour_out}
      COMMAND "${CMAKE_COMMAND}" -E env "${env}"
              "${exe}" -m buildutil.reflect generate
              --header "${header_abs}"
              --output "${output}"
              ${detour_args}
              --depfile "${output}.d"
              --namespace "${_buildutil_reflect_namespace}"
              --macros "${macros}"
              --std "${std}"
              --include-spelling "${spelling}"
              --include-dir "${CMAKE_SOURCE_DIR}/sources"
              --include-dir "${generated}"
              ${inherited}
      DEPENDS "${header_abs}" ${macro_dep}
      DEPFILE "${output}.d"
      COMMENT "reflect ${spelling}"
      VERBATIM
      COMMAND_EXPAND_LISTS
      ${explicit})
  endforeach()
endfunction()

# --- phase 2: per module ----------------------------------------------------

function(_buildutil_ext_module lib target)
  if(NOT TARGET buildutil-reflect)
    return()
  endif()
  # The ordering edge has to be stated even where the include path already
  # resolves: a link edge orders linking, not the object compilation that
  # reads the generated header.
  add_dependencies(${lib} buildutil-reflect)
  if(NOT lib STREQUAL "${target}" AND TARGET ${target})
    add_dependencies(${target} buildutil-reflect)
  endif()

  # Whose include path parses this module's headers. Recorded rather than
  # derived: the library name is `foo` or `foo-lib` depending on whether the
  # module has an entry point, and buildutil.cmake is the only thing entitled
  # to answer that. _buildutil_reflect_emit_commands reads it back once every
  # module has been added.
  set_property(GLOBAL APPEND PROPERTY _buildutil_reflect_module_dirs
               "${CMAKE_CURRENT_SOURCE_DIR}")
  _buildutil_reflect_key("${CMAKE_CURRENT_SOURCE_DIR}" owner)
  set_property(GLOBAL PROPERTY _buildutil_reflect_dirlib_${owner} "${lib}")

  if(_buildutil_reflect_include_mode STREQUAL "detour")
    # Nothing to place. The header the TU already includes carries its own
    # schemes, so there is no per-TU flag, no .cpp -> header mapping, and
    # nothing here that can be got wrong for one translation unit and right
    # for the next. That absence IS the fix.
    return()
  endif()

  if(_buildutil_reflect_include_mode STREQUAL "module")
    # Deferred: Link_dependencies() runs AFTER Init_submodule() in a module's
    # CMakeLists, so the dependency edges this mode needs do not exist yet.
    #
    # The call takes no arguments and the state rides globals, because a
    # DEFERred call's arguments expand in the DEFERRED scope -- where a
    # function local like ${lib} is simply empty. Same rule the component
    # manifest writer follows.
    _buildutil_reflect_key("${CMAKE_CURRENT_SOURCE_DIR}" here)
    set_property(GLOBAL PROPERTY _buildutil_reflect_lib_${here} "${lib}")
    set_property(GLOBAL PROPERTY _buildutil_reflect_target_${here} "${target}")
    cmake_language(DEFER DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
                   CALL _buildutil_reflect_whole_module)
  else()
    _buildutil_reflect_per_source()
  endif()
endfunction()

function(_buildutil_reflect_forced rel out)
  if(MSVC)
    set(${out} "/FI${rel}" PARENT_SCOPE)
  else()
    set(${out} "-include" "${rel}" PARENT_SCOPE)
  endif()
endfunction()

# include = source: DEPRECATED. Each .cpp is force-included with exactly the
# reflect headers it asked for; a header reached only transitively is
# silently missed. Kept one release for a project that
# cannot move yet.
function(_buildutil_reflect_per_source)
  get_property(headers GLOBAL PROPERTY _buildutil_reflect_headers)
  foreach(header IN LISTS headers)
    _buildutil_reflect_key("${header}" key)
    get_property(rel GLOBAL PROPERTY _buildutil_reflect_rel_${key})
    get_property(users GLOBAL PROPERTY _buildutil_reflect_users_${key})
    _buildutil_reflect_forced("${rel}" flag)
    foreach(user IN LISTS users)
      set(user_abs "${CMAKE_SOURCE_DIR}/${user}")
      # Source properties are directory-scoped, and a module's sources all
      # live in its own directory (its *.test/ subtree included -- those are
      # globbed, not add_subdirectory'd), so setting them from here is right.
      string(FIND "${user_abs}" "${CMAKE_CURRENT_SOURCE_DIR}/" at)
      if(at EQUAL 0)
        set_property(SOURCE "${user_abs}" APPEND PROPERTY
                     COMPILE_OPTIONS ${flag})
      endif()
    endforeach()
  endforeach()
endfunction()

# include = module: DEPRECATED. Every TU of the module gets every reflect
# header of the module and of the modules it links. No mapping needed, and
# nothing is missed WITHIN the closure of modules a target links -- but a
# header travelling further than that still arrives bare, and the cost in
# the meantime is that every TU parses reflect headers it never uses.
function(_buildutil_reflect_whole_module)
  _buildutil_reflect_key("${CMAKE_CURRENT_SOURCE_DIR}" here)
  get_property(lib GLOBAL PROPERTY _buildutil_reflect_lib_${here})
  get_property(target GLOBAL PROPERTY _buildutil_reflect_target_${here})
  if(NOT lib)
    return()
  endif()
  set(wanted "${CMAKE_CURRENT_SOURCE_DIR}")
  get_property(needs GLOBAL PROPERTY _buildutil_cmp_needs_${target})
  foreach(need IN LISTS needs)
    list(APPEND wanted "${CMAKE_SOURCE_DIR}/sources/${need}")
  endforeach()

  get_property(headers GLOBAL PROPERTY _buildutil_reflect_headers)
  set(flags "")
  foreach(header IN LISTS headers)
    set(header_abs "${CMAKE_SOURCE_DIR}/${header}")
    foreach(dir IN LISTS wanted)
      string(FIND "${header_abs}" "${dir}/" at)
      if(at EQUAL 0)
        _buildutil_reflect_key("${header}" key)
        get_property(rel GLOBAL PROPERTY _buildutil_reflect_rel_${key})
        _buildutil_reflect_forced("${rel}" flag)
        list(APPEND flags ${flag})
        break()
      endif()
    endforeach()
  endforeach()
  if(flags)
    target_compile_options(${lib} PRIVATE ${flags})
  endif()
endfunction()
