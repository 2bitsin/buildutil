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
# and buildutil emits three files per tagged header into the generated root:
#
#   generated/<rel>/x.reflect.hpp   the schemes
#   generated/<rel>/x.hpp           the DETOUR -- the real header by absolute
#                                   path, then the schemes
#   generated/<rel>/x.package.hpp   the same wrapper for the INSTALLED copy,
#                                   which has no generated root to hide
#                                   behind: it ships as include/<rel>/x.hpp
#                                   and the real header beside it as
#                                   x.detoured.hpp
#
# and puts the generated root AHEAD of sources/ on every include path. So
# #include "<rel>/x.hpp" -- anywhere, at any depth -- resolves to the detour,
# and a translation unit that has the class always has its schemes. Nothing
# under sources/ is touched, and the real header is still parsed in place, so
# an error or a goto-definition in class code lands in the file the author
# edits.
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
function(_buildutil_reflect_declare header reflect_rel detour_rel
                                     package_rel installed)
  set_property(GLOBAL APPEND PROPERTY _buildutil_reflect_headers "${header}")
  _buildutil_reflect_key("${header}" key)
  set_property(GLOBAL PROPERTY _buildutil_reflect_rel_${key} "${reflect_rel}")
  set_property(GLOBAL PROPERTY _buildutil_reflect_detour_${key} "${detour_rel}")
  set_property(GLOBAL PROPERTY _buildutil_reflect_package_${key}
               "${package_rel}")
  set_property(GLOBAL PROPERTY _buildutil_reflect_installed_${key}
               "${installed}")
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

function(_buildutil_reflect_kind_state variable out)
  if(DEFINED ${variable})
    set(${out} "${${variable}}" PARENT_SCOPE)
  else()
    set(${out} "ON" PARENT_SCOPE)
  endif()
endfunction()

# --- phase 1: configure -----------------------------------------------------

function(_buildutil_ext_pre_scan)
  set(generated "${CMAKE_BINARY_DIR}/generated")
  set(declarations "${CMAKE_BINARY_DIR}/_buildutil-reflect.cmake")
  _buildutil_reflect_python(env exe)
  _buildutil_reflect_macro_path(macros)
  # `buildutil build --no-tests` is -DBUILD_TESTING=OFF, and a scan that
  # ignored it parsed and gated the headers of suites this build was told
  # not to build. Defaulted here rather than in the scan, because an
  # undefined variable is cmake's "yes" and argparse's "".
  _buildutil_reflect_kind_state(BUILD_TESTING tests)
  _buildutil_reflect_kind_state(BUILD_BENCHMARKING benches)

  execute_process(
    COMMAND "${CMAKE_COMMAND}" -E env "${env}"
            "${exe}" -m buildutil.reflect scan
            --root "${CMAKE_SOURCE_DIR}"
            --sources "${CMAKE_SOURCE_DIR}/sources"
            --generated "${generated}"
            --out "${declarations}"
            --namespace "${_buildutil_reflect_namespace}"
            --annotation "${_buildutil_reflect_annotation}"
            --macros "${macros}"
            --tests "${tests}"
            --benches "${benches}"
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

  # The whole delivery mechanism, in one line: the generated root goes in
  # FRONT of everything, so <rel>/x.hpp finds the detour and not the real
  # header. At DIRECTORY scope from sources/ rather than per target, because
  # a target's include path STARTS with its directory's -- which is the only
  # way to also get in front of the roots a test or bench executable restates
  # for itself when it links nothing (see the MODULE_LIBRARY branch in
  # buildutil.cmake).
  include_directories(BEFORE "${generated}")

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
  set(outputs "")
  foreach(header IN LISTS headers)
    _buildutil_reflect_key("${header}" key)
    get_property(rel GLOBAL PROPERTY _buildutil_reflect_rel_${key})
    get_property(detour GLOBAL PROPERTY _buildutil_reflect_detour_${key})
    list(APPEND outputs "${generated}/${rel}")
    set_source_files_properties("${generated}/${rel}"
                                PROPERTIES GENERATED TRUE)
    get_property(package GLOBAL PROPERTY _buildutil_reflect_package_${key})
    foreach(extra IN ITEMS "${detour}" "${package}")
      if(extra)
        list(APPEND outputs "${generated}/${extra}")
        set_source_files_properties("${generated}/${extra}"
                                    PROPERTIES GENERATED TRUE)
      endif()
    endforeach()
  endforeach()

  # Same DEFER argument trap as _buildutil_reflect_whole_module below: a
  # deferred call's arguments expand in the DEFERRED scope, so the state
  # rides globals rather than being passed.
  set_property(GLOBAL PROPERTY _buildutil_reflect_generated "${generated}")
  set_property(GLOBAL PROPERTY _buildutil_reflect_env "${env}")
  set_property(GLOBAL PROPERTY _buildutil_reflect_exe "${exe}")
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

# The macro state of a target, as --define arguments. Same genex-and-JOIN
# shape as the include path above, and read at generate time for the same
# reason: the evaluated property also carries the INTERFACE definitions of
# everything the target links.
function(_buildutil_reflect_define_flags libs out)
  set(flags "")
  foreach(lib IN LISTS libs)
    if(NOT TARGET ${lib})
      continue()
    endif()
    set(defs "$<TARGET_PROPERTY:${lib},COMPILE_DEFINITIONS>")
    list(APPEND flags
      "$<$<BOOL:${defs}>:--define\;$<JOIN:${defs},\;--define\;>>")
  endforeach()
  set(${out} "${flags}" PARENT_SCOPE)
endfunction()

# The same macro state where a project spelled it as an OPTION instead --
# target_compile_options(lib PRIVATE -DFOO=1), which lands in a different
# property and reaches the compiler just the same.
#
# Read RAW, so an entry that is itself a generator expression can be seen and
# skipped: nothing can inspect one before generate time, and this list is
# built while targets are still being described. Only the macro forms travel.
# A warning or error flag is not macro state, and forwarding one would turn a
# parse whose diagnostics exist to explain a shortfall into a lint.
function(_buildutil_reflect_option_defines libs out)
  set(flags "")
  foreach(lib IN LISTS libs)
    if(NOT TARGET ${lib})
      continue()
    endif()
    get_target_property(options ${lib} COMPILE_OPTIONS)
    foreach(option IN LISTS options)
      if(option MATCHES "\\$<")
        continue()
      elseif(option MATCHES "^[-/]D(.+)$")
        list(APPEND flags --define "${CMAKE_MATCH_1}")
      elseif(option MATCHES "^[-/]U(.+)$")
        list(APPEND flags --undefine "${CMAKE_MATCH_1}")
      endif()
    endforeach()
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
  get_property(macros GLOBAL PROPERTY _buildutil_reflect_macro_file)
  get_property(options_header GLOBAL PROPERTY _buildutil_options_header)
  set(force_include "")
  if(options_header)
    set(force_include --force-include "${options_header}")
  endif()
  # On the command line, so a changed standard reruns every parse.
  set(std "")
  if(BUILDUTIL_CXX_STANDARD)
    set(std --std "c++${BUILDUTIL_CXX_STANDARD}")
  endif()

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
    _buildutil_reflect_define_flags("${owners}" defined)
    _buildutil_reflect_option_defines("${owners}" defined_as_options)

    # All three files come out of ONE parse. A second rule for the two
    # wrappers would buy nothing -- their content is derived from paths
    # alone -- and would cost a second python start per header.
    get_property(package_rel GLOBAL PROPERTY _buildutil_reflect_package_${key})
    set(detour_args "")
    set(detour_out "")
    if(detour_rel)
      set(detour_out "${generated}/${detour_rel}" "${generated}/${package_rel}")
      set(detour_args --detour "${generated}/${detour_rel}"
                      --package "${generated}/${package_rel}"
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
              ${std}
              --include-spelling "${spelling}"
              --include-dir "${CMAKE_SOURCE_DIR}/sources"
              --include-dir "${generated}"
              ${inherited} ${defined} ${defined_as_options} ${force_include}
      DEPENDS "${header_abs}" ${macro_dep} ${options_header}
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

  # Nothing to place: the header the TU already includes carries its own
  # schemes, so there is no per-TU flag and no .cpp -> header mapping that
  # can be got wrong for one translation unit and right for the next.
  #
  # DEFERred because this hook runs BEFORE the module's own header install
  # and the last rule to write a name is the one that survives: the detour
  # has to be that rule.
  cmake_language(DEFER DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
                 CALL _buildutil_reflect_install)
endfunction()

# --- phase 3: what ships ----------------------------------------------------

# A generated artifact ships where the header it shadows ships, and nowhere
# else -- the module's header install skips a private `_*` name, a *.test/ or
# *.bench/ subtree, data directories and dot directories, so this skips the
# same ones.
function(_buildutil_reflect_exported rel out)
  string(REPLACE "/" ";" parts "${rel}")
  foreach(part IN LISTS parts)
    if(part MATCHES "^[_.]"
       OR part MATCHES "\\.(test|bench|install|embed)(\\.|$)")
      set(${out} FALSE PARENT_SCOPE)
      return()
    endif()
  endforeach()
  set(${out} TRUE PARENT_SCOPE)
endfunction()

# The installed layout, per header of THIS module: the schemes beside the
# header, the header itself under the name the packaged wrapper reaches it
# by, and the wrapper at the header's own spelling -- so one #include reaches
# the schemes in a consumer exactly as it does in-tree.
function(_buildutil_reflect_install)
  get_property(headers GLOBAL PROPERTY _buildutil_reflect_headers)
  get_property(generated GLOBAL PROPERTY _buildutil_reflect_generated)
  foreach(header IN LISTS headers)
    set(header_abs "${CMAKE_SOURCE_DIR}/${header}")
    string(FIND "${header_abs}" "${CMAKE_CURRENT_SOURCE_DIR}/" at)
    if(NOT at EQUAL 0)
      continue()
    endif()
    file(RELATIVE_PATH rel "${CMAKE_SOURCE_DIR}/sources" "${header_abs}")
    _buildutil_reflect_exported("${rel}" exported)
    if(NOT exported)
      continue()
    endif()
    _buildutil_reflect_key("${header}" key)
    get_property(schemes GLOBAL PROPERTY _buildutil_reflect_rel_${key})
    get_property(package GLOBAL PROPERTY _buildutil_reflect_package_${key})
    get_property(installed GLOBAL PROPERTY _buildutil_reflect_installed_${key})
    get_filename_component(dir "${rel}" DIRECTORY)
    get_filename_component(name "${rel}" NAME)
    install(FILES "${generated}/${schemes}" DESTINATION "include/${dir}")
    install(FILES "${header_abs}" DESTINATION "include/${dir}"
            RENAME "${installed}")
    install(FILES "${generated}/${package}" DESTINATION "include/${dir}"
            RENAME "${name}")
  endforeach()
endfunction()
