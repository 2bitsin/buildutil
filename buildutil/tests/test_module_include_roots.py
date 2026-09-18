"""Which include roots a module target carries, and — the load-bearing
half — at which SCOPE.

A module publishes its headers exactly one way: qualified through
`sources/` (`<module>/foo.h`). Its own directory is a BUILD requirement,
PRIVATE, so that the TUs of the module which do not sit next to its headers
(codegen output, a nested source, a `*.test/` subtree) can quote them —
and no further. Export that root instead and every linked module's
directory lands on every consumer's include path, where an unqualified
`#include "types.h"` that used to be a hard compile error resolves to
whichever module happens to come first IN LINK ORDER.

One project-wide escape hatch exists for PORTED trees whose unqualified
cross-module includes are load-bearing: [cmake] export_module_headers in
buildutil.toml, which renders the scope PUBLIC. It is off unless asked
for, has no per-module form, and everything below asserts the default.

The assertions run against the RENDERED deposit rather than the template
text: that is what cmake actually reads, and it covers the extensions too.
"""
import re

from buildutil import deposit

CFG = {"cmake_option_prefix": "ACME", "module_define_prefix": "ACM"}

SOURCES_ROOT = "${CMAKE_SOURCE_DIR}/sources"
MODULE_DIR = "${CMAKE_CURRENT_SOURCE_DIR}"
SCOPES = ("PUBLIC", "PRIVATE", "INTERFACE")

_CALL = re.compile(r"target_include_directories\(\s*([^)]*)\)", re.S)

# The ONLY places a module's own dir may be a usage requirement. An
# INTERFACE library has no other scope to offer — cmake gives such a target
# no PRIVATE — so a header-only module and the watcom firmware LIBRARY each
# export the root they need for their *.test/ subtrees. Both are documented
# where they stand. Adding to this map means exporting some module's
# internal headers tree-wide: do it deliberately, or not at all.
# buildutil.cmake used to have exactly one: a header-only module became an
# INTERFACE library, which has no scope but INTERFACE, so its own dir
# necessarily reached its consumers while every other module's stayed
# PRIVATE. Every module now has a real library (an empty TU where there
# is nothing to compile), so the necessity is gone and the count is 0 —
# the asymmetry was removed rather than re-documented.
EXPORTED_BY_NECESSITY = {"buildutil.cmake": 0, "watcom.cmake": 1}


def _rendered(tmp_path) -> dict[str, str]:
  out = deposit.ensure(tmp_path, CFG, ["watcom"])
  return {p.name: p.read_text() for p in out.iterdir() if p.suffix == ".cmake"}


def _calls(text: str) -> list[tuple[str, str | None, str]]:
  """(target, scope, argument text) for every include call in a file."""
  found = []
  for m in _CALL.finditer(text):
    body = m.group(1)
    tokens = body.split()
    scope = next((t for t in tokens[:3] if t in SCOPES), None)
    found.append((tokens[0] if tokens else "", scope, body))
  return found


def test_the_module_dir_is_never_a_public_usage_requirement(tmp_path):
  offenders = [f"{name}: {body}"
               for name, text in _rendered(tmp_path).items()
               for _, scope, body in _calls(text)
               if scope == "PUBLIC" and MODULE_DIR in body]
  assert not offenders, (
    "a module's own directory must not be PUBLIC — that exports its "
    "internal headers to every consumer, where link order silently "
    "decides which one wins:\n" + "\n".join(offenders))


def test_the_module_dir_is_exported_only_where_cmake_leaves_no_choice(tmp_path):
  for name, text in _rendered(tmp_path).items():
    exported = [body for _, scope, body in _calls(text)
                if scope == "INTERFACE" and MODULE_DIR in body]
    expected = EXPORTED_BY_NECESSITY.get(name, 0)
    assert len(exported) == expected, (
      f"{name}: {len(exported)} INTERFACE call(s) put a module's own dir on "
      f"its consumers, expected {expected}. An INTERFACE library has no "
      f"PRIVATE scope, so this is legitimate ONLY for a header-only target. "
      f"If that is what you added, say so: update EXPORTED_BY_NECESSITY in "
      f"this file and document it at the call site. Otherwise use PRIVATE."
      + "\n" + "\n".join(exported))


def test_the_module_library_keeps_its_own_dir_private(tmp_path):
  """The static branch takes its scope from _buildutil_own_dir_scope now, so
  the guarantee moved with it: rendered WITHOUT the opt-in, that variable
  must be PRIVATE. Reading the call alone no longer tells you the scope,
  which is exactly why this asserts the definition too."""
  machinery = _rendered(tmp_path)["buildutil.cmake"]
  assert f'${{_buildutil_own_dir_scope}} "{MODULE_DIR}"' in machinery, \
    "the static module branch lost its own-dir root"
  assert "if(OFF)" in machinery, (
    "a project that did not ask for it is rendering with module headers "
    "EXPORTED — the qualified-include rule is opt-in, tree-wide")
  assert "set(_buildutil_own_dir_scope PRIVATE)" in machinery


def test_the_export_opt_in_is_the_only_way_to_widen_that_scope(tmp_path):
  """Declared, the same seam renders PUBLIC — and still only via the one
  variable, so there is no per-module way in or out of it."""
  out = deposit.ensure(tmp_path, {**CFG, "export_module_headers": True},
                       ["watcom"])
  machinery = (out / "buildutil.cmake").read_text()
  assert "if(ON)" in machinery
  assert "set(_buildutil_own_dir_scope PUBLIC)" in machinery
  assert machinery.count("_buildutil_own_dir_scope} \"" + MODULE_DIR) == 1


def test_targets_built_from_module_tus_restate_the_private_root(tmp_path):
  """A module TU that does not compile INTO the library — a test, a bench,
  main.cpp, a *.pybind.cpp — inherits nothing through the link now that the
  root is PRIVATE, so each of those targets restates it itself."""
  machinery = _rendered(tmp_path)["buildutil.cmake"]
  calls = _calls(machinery)
  # ${target} is the APP -- the executable holds the bare
  # module name and the library is ${lib}
  for target in ("${test_target}", "${bench_target}",
                 "${target}", "${target}-pybind"):
    assert any(t == target and scope == "PRIVATE" and MODULE_DIR in body
               for t, scope, body in calls), (
      f"{target} never restates {MODULE_DIR}; its TUs cannot quote the "
      f"module's own headers unqualified")


def test_sources_root_comes_first_where_a_call_carries_both(tmp_path):
  """Ordering is the compatibility promise: a project spelling its includes
  <module>/foo.h keeps resolving through sources/ exactly as before."""
  for name, text in _rendered(tmp_path).items():
    for _, _, body in _calls(text):
      if SOURCES_ROOT in body and MODULE_DIR in body:
        assert body.index(SOURCES_ROOT) < body.index(MODULE_DIR), \
          f"{name}: {body}"


def test_the_own_dir_never_precedes_the_codegen_roots(tmp_path):
  """Behind sources/ AND behind the generated roots — ahead of gen_incdirs
  it would re-point includes that already resolved (a checked-in tables.h
  shadowing the generated one, silently, for every angle include)."""
  machinery = _rendered(tmp_path)["buildutil.cmake"]
  for _, _, body in _calls(machinery):
    if "${gen_incdirs}" in body and MODULE_DIR in body:
      assert body.index("${gen_incdirs}") < body.index(MODULE_DIR), body


def test_every_test_process_gets_bin_on_PATH_and_a_defined_cwd(tmp_path):
  """#14's contract, which is environment rather than anything a module
  declares: a suite drives the project's own programs by BARE NAME, the
  way a person at a shell does, and reads its corpora as plain relative
  paths. Asserted on the rendered machinery because the end-to-end proof
  needs GoogleTest, which the cmake e2e harness deliberately does not
  carry — open-watcom-v21's three driver suites are the real-tree case.

  Set as TEST PROPERTIES on purpose: a bare `ctest` in the build tree has
  to behave like `buildutil test`, so this cannot live in the runner."""
  machinery = _rendered(tmp_path)["buildutil.cmake"]
  assert 'ENVIRONMENT_MODIFICATION "PATH=path_list_prepend:' in machinery, (
    "test processes no longer get <build>/bin on PATH — suites would have "
    "to be told tool paths again, which this removed")
  assert "${CMAKE_BINARY_DIR}/bin" in machinery
  # cmake's separator-aware form, not a POSIX-only ':' assumption
  assert "path_list_prepend" in machinery
  broken = "tests no longer run in the module directory; corpora paths break"
  gtest_cwd = 'WORKING_DIRECTORY \\"${CMAKE_CURRENT_SOURCE_DIR}\\")'
  assert gtest_cwd in machinery, broken
  assert 'WORKING_DIRECTORY "${directory}"' in machinery, broken
  assert ('_buildutil_register_python_suite(${target} '
          '"${CMAKE_CURRENT_SOURCE_DIR}"') in machinery, broken


def test_a_test_target_is_built_after_the_app_it_runs(tmp_path):
  """`buildutil test <module>` builds <module>-tests and
  nothing else, so a suite that RUNS its module's program was running
  whatever stale binary lay in the tree — and passing against it. That
  failure is silent and looks exactly like success.

  Asserted on the rendered machinery, not end to end, for the same
  reason the PATH contract above is: creating a gtest target needs
  GoogleTest,
  which the cmake e2e harness deliberately does not carry (confirmed —
  configure fails on `Unknown CMake command gtest_discover_tests`).
  The consumer's 19 Test_drives call sites are the real-tree case."""
  machinery = _rendered(tmp_path)["buildutil.cmake"]
  assert "add_dependencies(${test_target} ${target})" in machinery, (
    "the test target has no build-order edge to its module's app; a "
    "suite that runs it can pass against a stale binary")
  # only where an executable exists — otherwise ${target} IS the library
  # the test already links, and the edge comes through the link
  assert 'NOT _own_lib STREQUAL target' in machinery


def test_test_and_bench_deps_resolve_modules_to_libraries(tmp_path):
  """The 0.23.0 rename mapped the positional Link_dependencies list
  through the module->library resolver and missed the TEST/BENCH lists —
  so Link_dependencies(TEST wlink) tried to link wlink's EXECUTABLE (the
  bare name) and failed configure. Asserted on the rendered machinery for
  the usual reason: creating a real tests target needs GoogleTest, which
  the e2e harness does not carry."""
  machinery = _rendered(tmp_path)["buildutil.cmake"]
  block = machinery[machinery.index("function(Link_dependencies)"):]
  block = block[:block.index("endfunction()")]
  assert 'foreach(_kw TEST BENCH)' in block and \
         '_buildutil_library_of("${dep}" dep_lib)' in block, (
    "the TEST/BENCH lists bypass the module->library resolver; a test-only "
    "dep naming a module with an app links its executable and fails")
  # dormant filtering reaches them too
  assert block.count("REMOVE_ITEM") >= 2, (
    "dormant modules are not dropped from TEST/BENCH dep lists")
