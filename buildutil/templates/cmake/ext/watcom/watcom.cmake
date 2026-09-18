# Open Watcom firmware images: every *.asm (nasm, OMF) and *.c
# (wcc) in the module links into ONE raw image at offset 0 -- asm
# objects first, so the entry table at the head of the (single) asm
# shell is image offset 0, the contract the IVT pokes rely on. The C
# is called by symbol; there are no offset contracts between the
# objects. bin2cpp renders the image as a constexpr byte array named
# after the first asm stem (<STEM>_ROM). A native Watcom on PATH wins;
# the docker wrappers in tools/watcom are the fallback.
#
# See contrib/reference/open-watcom/quick-reference.md for the flag rationale (-zdf especially:
# without it __interrupt prologues emit DGROUP fixups raw can't hold).

# Init_firmware([OPROM] [BASE <hex segment>] [ORG <hex offset>]) -- the
# explicit entry a firmware module calls in place of Init_submodule. It links
# this directory's *.asm + *.c into ONE raw 16-bit ROM image and surfaces it
# as a constexpr byte array named for the image stem (mybios.c ->
# firmware/mybios.hpp, MYBIOS_ROM). The layout is the OPROM keyword, not a
# filename tag: OPROM = an option ROM (55AA header + orom_finalize stamp);
# default = the system layout (reset tail at BASE:FFF0). BASE/ORG place the
# code: segment and offset in HEX, defaulting to BASE F000 ORG 0000 (OPROM
# defaults its base to C800). The module is published as an INTERFACE library
# (its sources/-relative name, e.g. bios_system, alias bios::system) carrying
# the generated include root, so a C++ consumer does
# Link_dependencies(bios::system) and #include "firmware/...".
#
# Firmware-specific by design: the general Init_submodule no longer sniffs *.fw
# files (only the .rom.bin resource embed stays general there), so nothing but a
# deliberate Init_firmware call pulls in Watcom.
# file-scope capture: in a function body CMAKE_CURRENT_LIST_DIR is
# the CALLER's dir, so sibling machinery must be pinned here
set(_buildutil_cmake_dir "${CMAKE_CURRENT_LIST_DIR}")
function(Init_firmware)
  cmake_parse_arguments(FW "LIBRARY;OPROM" "BASE;ORG" "" ${ARGN})
  _buildutil_module_name(target)
  if(FW_LIBRARY)
    # a firmware library: shared primitives a sibling firmware module includes
    # over the group wcc path (e.g. lib/fw.h). Header-only today, so it
    # publishes no ROM -- just an INTERFACE target for the dependency graph; when
    # it grows a *.c it can compile to a Watcom static lib the images link.
    add_library(${target} INTERFACE)
    target_include_directories(${target} INTERFACE "${CMAKE_CURRENT_SOURCE_DIR}")
    _buildutil_module_alias(alias)
    if(alias)
      add_library(${alias} ALIAS ${target})
    endif()
    return()
  endif()
  if(FW_OPROM)
    set(layout orom)
  else()
    set(layout system)
  endif()
  # where the code links: BASE = segment, ORG = offset, both hex. The system
  # BIOS default is F000:0000; an option ROM defaults its segment to C800.
  if(NOT FW_BASE)
    if(FW_OPROM)
      set(FW_BASE "C800")
    else()
      set(FW_BASE "F000")
    endif()
  endif()
  if(NOT FW_ORG)
    set(FW_ORG "0000")
  endif()
  file(GLOB fw_asm CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/*.asm")
  file(GLOB fw_c   CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/*.c")
  if(NOT fw_asm AND NOT fw_c)
    message(FATAL_ERROR "${target}: Init_firmware() found no *.asm / *.c")
  endif()
  if(fw_c)
    list(GET fw_c 0 first)
  else()
    list(GET fw_asm 0 first)
  endif()
  # the image + its constexpr array take the MODULE's name (bios_system ->
  # BIOS_SYSTEM_ROM): every firmware module's source is just main.c, so the
  # module directory is the distinguishing name, not the file stem.
  set(stem "${target}")

  set(fw_dir "${CMAKE_BINARY_DIR}/generated/${target}/firmware")
  file(MAKE_DIRECTORY "${fw_dir}")
  set(headers "")
  if(fw_c)
    # asm + C link as ONE raw Watcom image (asm objects first -- entry table at 0)
    _buildutil_add_watcom_firmware(${target} "${fw_dir}" headers
                                  "${fw_asm}" "${fw_c}" "${stem}" "${layout}"
                                  "${FW_BASE}" "${FW_ORG}")
  else()
    # asm-only image: a flat nasm binary, no Watcom link
    find_program(@CMAKE_OPTION_PREFIX@_NASM nasm REQUIRED)
    string(TOUPPER "${stem}" ident)
    string(REPLACE "-" "_" ident "${ident}")
    set(bin "${fw_dir}/${stem}.bin")
    set(hpp "${fw_dir}/${stem}.hpp")
    add_custom_command(OUTPUT "${bin}"
      COMMAND "${@CMAKE_OPTION_PREFIX@_NASM}" -f bin -o "${bin}" "${first}"
      DEPENDS "${first}"
      COMMENT "nasm ${stem}.asm")
    add_custom_command(OUTPUT "${hpp}"
      COMMAND "${CMAKE_COMMAND}" "-DINPUT=${bin}" "-DOUTPUT=${hpp}"
              "-DIDENT=${ident}_ROM" -P "${_buildutil_cmake_dir}/bin2cpp.cmake"
      DEPENDS "${bin}" "${_buildutil_cmake_dir}/bin2cpp.cmake"
      COMMENT "bin2cpp ${stem}.bin")
    list(APPEND headers "${hpp}")
  endif()

  add_custom_target(${target}-firmware DEPENDS ${headers})
  add_library(${target} INTERFACE)
  target_include_directories(${target} INTERFACE
    "${CMAKE_BINARY_DIR}/generated/${target}")
  add_dependencies(${target} ${target}-firmware)
  _buildutil_module_alias(alias)
  if(alias)
    add_library(${alias} ALIAS ${target})
  endif()
endfunction()

function(_buildutil_add_watcom_firmware target fw_dir headers_var asm_sources c_sources
                                       image_stem layout base org)
  # image_stem names this ROM (bios, video); layout is `system` (BASE segment
  # + RESET tail) or `orom` (an option ROM, no RESET); base/org are the hex
  # segment:offset the code links at (Init_firmware's BASE/ORG). This lets
  # one module ship both -- the system BIOS and the video option ROM.
  string(TOUPPER "${image_stem}" ident)
  string(REPLACE "-" "_" ident "${ident}")   # dashed module -> valid C identifier
  # @CMAKE_OPTION_PREFIX@_PREBUILT_FIRMWARE: a dir of
  # ready <image>.bin from the firmware CI stage -- cross builds
  # (msvc-wine, osxcross) consume the artifact and need NO watcom at all
  if(@CMAKE_OPTION_PREFIX@_PREBUILT_FIRMWARE)
    set(bin "${@CMAKE_OPTION_PREFIX@_PREBUILT_FIRMWARE}/${image_stem}.bin")
    if(NOT EXISTS "${bin}")
      message(FATAL_ERROR "${target}: prebuilt firmware ${bin} missing")
    endif()
    set(hpp "${fw_dir}/${image_stem}.hpp")
    add_custom_command(OUTPUT "${hpp}"
      COMMAND "${CMAKE_COMMAND}" "-DINPUT=${bin}" "-DOUTPUT=${hpp}"
              "-DIDENT=${ident}_ROM" -P "${_buildutil_cmake_dir}/bin2cpp.cmake"
      DEPENDS "${bin}" "${_buildutil_cmake_dir}/bin2cpp.cmake"
      COMMENT "bin2cpp ${image_stem}.bin (prebuilt)")
    set(headers "${${headers_var}}")
    list(APPEND headers "${hpp}")
    set(${headers_var} "${headers}" PARENT_SCOPE)
    message(STATUS "${target}: firmware from prebuilt ${bin}")
    return()
  endif()
  find_program(@CMAKE_OPTION_PREFIX@_NASM nasm REQUIRED)
  # the wcc/wlink pick is re-judged every configure: a cached hit pins
  # whichever the FIRST configure saw, and CI runners only grow the
  # native toolchain after their first pipeline
  unset(@CMAKE_OPTION_PREFIX@_WCC CACHE)
  unset(@CMAKE_OPTION_PREFIX@_WLINK CACHE)
  find_program(@CMAKE_OPTION_PREFIX@_WCC wcc
    HINTS "$ENV{WATCOM}/binl64" "$ENV{WATCOM}/binl"
    PATHS "${CMAKE_SOURCE_DIR}/tools/watcom" REQUIRED)
  find_program(@CMAKE_OPTION_PREFIX@_WLINK wlink
    HINTS "$ENV{WATCOM}/binl64" "$ENV{WATCOM}/binl"
    PATHS "${CMAKE_SOURCE_DIR}/tools/watcom" REQUIRED)
  message(STATUS "watcom: wcc=${@CMAKE_OPTION_PREFIX@_WCC} wlink=${@CMAKE_OPTION_PREFIX@_WLINK}")

  set(objects "")
  foreach(src IN LISTS asm_sources)
    cmake_path(GET src STEM LAST_ONLY stem)
    string(REGEX REPLACE "\\.fw$" "" stem "${stem}")
    set(obj "${fw_dir}/${stem}.asm.o")
    # incbin (the video ROM's bundled fonts) resolves relative to -i; depend on
    # the source dir's *.rom.bin so a font change re-assembles the image.
    cmake_path(GET src PARENT_PATH src_dir)
    file(GLOB asm_blobs CONFIGURE_DEPENDS "${src_dir}/*.rom.bin")
    add_custom_command(OUTPUT "${obj}"
      COMMAND "${@CMAKE_OPTION_PREFIX@_NASM}" -f obj "-i${src_dir}/" -o "${obj}" "${src}"
      DEPENDS "${src}" ${asm_blobs}
      COMMENT "nasm ${stem}.asm")
    list(APPEND objects "${obj}")
  endforeach()
  foreach(src IN LISTS c_sources)
    cmake_path(GET src STEM LAST_ONLY stem)
    string(REGEX REPLACE "\\.fw$" "" stem "${stem}")
    set(obj "${fw_dir}/${stem}.c.o")
    # firmware headers: the module's own beside the .c, plus the shared
    # primitives (the shared fw header) one level up at the bios group root -- both on the
    # include path, both a dependency so an edit to either re-runs wcc
    # split the BIOS across system/ and display/ under the group).
    cmake_path(GET src PARENT_PATH src_dir)
    cmake_path(GET src_dir PARENT_PATH group_dir)
    file(GLOB_RECURSE c_headers CONFIGURE_DEPENDS "${group_dir}/*.h")
    add_custom_command(OUTPUT "${obj}"
      COMMAND "${@CMAKE_OPTION_PREFIX@_WCC}" -0 -ms -s -os -zl -zu -zdf -zc -w4
              "-i=${src_dir}" "-i=${group_dir}" "-fo=${obj}" "${src}"
      DEPENDS "${src}" ${c_headers}
      COMMENT "wcc ${stem}.c")
    list(APPEND objects "${obj}")
  endforeach()

  set(lnk "${fw_dir}/${image_stem}.lnk")
  set(bin "${fw_dir}/${image_stem}.bin")
  set(hpp "${fw_dir}/${image_stem}.hpp")
  list(JOIN objects "," object_list)
  # a raw ROM has no stack segment and no program entry by design --
  # W1014 (stack segment not found) and W1023 (no starting address) are
  # pure noise for this shape, so the lnk disables both at the source
  if(layout STREQUAL "orom")
    # option ROM: code from BASE:ORG (default C800:0000); the 55AA header is
    # the asm shim's first bytes and POST far-calls init at BASE:0003. No
    # RESET tail. wlink's raw bin carries no size byte or checksum, so
    # orom_finalize.py stamps both (pad to a 512-block multiple, block count
    # at [2], trailing checksum to zero the 8-bit modular sum) -- else
    # ScanOptionRoms rejects the image.
    file(WRITE "${lnk}"
      "format raw bin\n"
      "option quiet, nodefaultlibs\n"
      "disable 1014, 1023\n"
      "order clname 'CODE' segaddr=0x${base} offset=0x${org} segment '_TEXT'\n"
      "file ${object_list}\n"
      "name ${bin}.raw\n")
    add_custom_command(OUTPUT "${bin}"
      COMMAND "${@CMAKE_OPTION_PREFIX@_WLINK}" "@${lnk}"
      COMMAND "${BUILDUTIL_PY}" -m buildutil.orom_finalize "${bin}.raw" "${bin}"
      DEPENDS ${objects} "${lnk}"
      COMMENT "wlink + finalize ${image_stem}.bin (option ROM)")
  else()
    # system BIOS: one whole 64K image for the BASE segment (default F000).
    # Code grows from BASE:ORG and the reset tail (class FAR_DATA, segment
    # RESET) is pinned at BASE:FFF0, so the default image covers F0000-FFFFF
    # including the reset vector at FFFF:0000 -- it maps as a single plain
    # blob, never sliced or composited. The gap between the two is zero
    # fill; nothing is reserved in it. This wlink honors SEGADDR/OFFSET at the
    # CLASS level only.
    file(WRITE "${lnk}"
      "format raw bin\n"
      "option quiet, nodefaultlibs\n"
      "disable 1014, 1023\n"
      "order clname 'CODE' segaddr=0x${base} offset=0x${org} segment '_TEXT' "
      "clname 'FAR_DATA' segaddr=0x${base} offset=0xFFF0 segment 'RESET'\n"
      "file ${object_list}\n"
      "name ${bin}\n")
    add_custom_command(OUTPUT "${bin}"
      COMMAND "${@CMAKE_OPTION_PREFIX@_WLINK}" "@${lnk}"
      DEPENDS ${objects} "${lnk}"
      COMMENT "wlink ${image_stem}.bin")
  endif()
  add_custom_command(OUTPUT "${hpp}"
    COMMAND "${CMAKE_COMMAND}" "-DINPUT=${bin}" "-DOUTPUT=${hpp}"
            "-DIDENT=${ident}_ROM" -P "${_buildutil_cmake_dir}/bin2cpp.cmake"
    DEPENDS "${bin}" "${_buildutil_cmake_dir}/bin2cpp.cmake"
    COMMENT "bin2cpp ${image_stem}.bin")

  set(headers "${${headers_var}}")
  list(APPEND headers "${hpp}")
  set(${headers_var} "${headers}" PARENT_SCOPE)
endfunction()
