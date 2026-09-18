"""The cmake machinery, runtime-rendered: NOTHING is committed in a
project. ensure() renders base + toml-declared extensions into the
gitignored `_bdudata/cmake/`, always fresh (derived data), and the
driver hands that dir to cmake as CMAKE_MODULE_PATH. Python helpers
are never deposited — the rendered cmake calls them as modules of the
running package."""
import subprocess
import sys
from pathlib import Path

from buildutil import deposit

CFG = {"cmake_option_prefix": "ACME", "module_define_prefix": "ACM"}


def test_ensure_renders_base_into_runtime_dir(tmp_path):
  out = deposit.ensure(tmp_path, CFG)
  assert out == tmp_path / "_bdudata" / "cmake"
  assert sorted(p.name for p in out.iterdir()) == [
    "bin2cpp.cmake", "buildutil.cmake", "driver_guard.cmake"]
  machinery = (out / "buildutil.cmake").read_text()
  assert "@CMAKE_OPTION_PREFIX@" not in machinery
  assert "ACME_MAX_ERRORS" in machinery and "ACM_MODULE_DEFINES" in machinery
  # python is NEVER deposited: the rendered cmake calls package modules
  assert "-m buildutil.apply_patch" in machinery
  assert "apply_patch.py" not in {p.name for p in out.iterdir()}
  # MACHINERY siblings are referenced next to the including file, never
  # project-side — bin2cpp is reached through _buildutil_cmake_dir
  assert '"${_buildutil_cmake_dir}/bin2cpp.cmake"' in machinery
  # ...so the ONE project-side `cmake/` reference is the local-extension
  # hook, which includes the PROJECT's files and never the machinery's
  assert machinery.count("${CMAKE_SOURCE_DIR}/cmake") == 1
  assert '"${CMAKE_SOURCE_DIR}/cmake/*.cmake"' in machinery


def test_export_module_headers_renders_off_unless_declared(tmp_path):
  """No placeholder survives, and an absent key renders OFF — a project
  that never heard of the option keeps the qualified-include rule."""
  machinery = (deposit.ensure(tmp_path, CFG) / "buildutil.cmake").read_text()
  assert "@EXPORT_MODULE_HEADERS@" not in machinery
  assert "if(OFF)" in machinery
  on = (deposit.ensure(tmp_path, {**CFG, "export_module_headers": True})
        / "buildutil.cmake").read_text()
  assert "if(ON)" in on


def test_default_dormant_modules_render_into_the_machinery(tmp_path):
  """cmake needs the committed default too — it is what decides which
  subdirectories are added, and a clean clone has no modules.ini to read."""
  plain = (deposit.ensure(tmp_path, CFG) / "buildutil.cmake").read_text()
  assert "@DEFAULT_DORMANT@" not in plain
  assert "set(dormant )" in plain or "set(dormant)" in plain
  seeded = (deposit.ensure(tmp_path, {**CFG, "modules_dormant": ["as", "re2c"]})
            / "buildutil.cmake").read_text()
  assert 'set(dormant "as" "re2c")' in seeded


def test_ensure_is_always_fresh(tmp_path):
  out = deposit.ensure(tmp_path, CFG)
  (out / "buildutil.cmake").write_text("# stale\n")
  deposit.ensure(tmp_path, CFG)
  # derived data: local edits to the RENDERED output do not survive —
  # the seam for changes is the package (or buildutil.toml), never here
  assert "ACME_MAX_ERRORS" in (out / "buildutil.cmake").read_text()


def test_every_rendered_file_warns_and_stamps_its_version(tmp_path):
  """The deposit is derived data an editor will otherwise mistake for
  source. Every rendered file leads with a DO-NOT-EDIT banner naming the
  buildutil that wrote it, and points at the seam that DOES persist —
  the project's own `cmake/` dir."""
  from buildutil.updatecmd import package_version
  out = deposit.ensure(tmp_path, CFG, ["watcom"])
  for name in ("buildutil.cmake", "bin2cpp.cmake", "watcom.cmake"):
    text = (out / name).read_text()
    first = text.splitlines()[0]
    assert first.startswith(deposit.STAMP), f"{name}: no stamp on line 1"
    assert first.split()[-1] == package_version(), name
    assert "DO NOT EDIT" in text, name
    assert "cmake/` directory" in text, f"{name}: no pointer to the seam"
  # and the banner is a cmake COMMENT — a deposit that does not parse is
  # worse than an unmarked one
  assert all(line.startswith("#")
             for line in (out / "buildutil.cmake").read_text().splitlines()[:8])


def test_deposited_version_reads_the_stamp_back(tmp_path):
  from buildutil.updatecmd import package_version
  assert deposit.deposited_version(tmp_path) is None   # nothing rendered yet
  deposit.ensure(tmp_path, CFG)
  assert deposit.deposited_version(tmp_path) == package_version()
  # a deposit predating stamping reads as unknown, not as a crash
  (deposit.cmake_dir(tmp_path) / "buildutil.cmake").write_text("# old\n")
  assert deposit.deposited_version(tmp_path) is None


def test_a_version_bump_alone_rewrites_the_deposit(tmp_path, monkeypatch):
  """The stamp rides IN the rendered text, so an upgrade that changed no
  template still changes every file — which is what makes 'the next build
  re-renders it' true rather than merely intended."""
  out = deposit.ensure(tmp_path, CFG)
  before = (out / "buildutil.cmake").read_text()
  monkeypatch.setattr("buildutil.updatecmd.package_version", lambda: "99.9.9")
  deposit.ensure(tmp_path, CFG)
  after = (out / "buildutil.cmake").read_text()
  assert after != before
  assert deposit.deposited_version(tmp_path) == "99.9.9"


def test_a_file_that_is_no_longer_ours_is_swept(tmp_path):
  """The deposit IS the rendered set. An extension that stops being
  declared must not linger — CMAKE_MODULE_PATH would still resolve it."""
  out = deposit.ensure(tmp_path, CFG, ["watcom"])
  assert (out / "watcom.cmake").is_file()
  deposit.ensure(tmp_path, CFG)                        # watcom undeclared now
  assert not (out / "watcom.cmake").exists()
  assert (out / "buildutil.cmake").is_file()


def test_project_local_cmake_is_included_last_and_never_rendered(tmp_path):
  """The other half of the seam: `_bdudata/cmake/` is ours and is rewritten
  every build; the project's own `cmake/` dir is the project's and buildutil
  never touches it. The hook is presence-driven — no declaration, no
  registration — and comes LAST so a project helper can call, wrap or
  override anything the machinery defines."""
  out = deposit.ensure(tmp_path, CFG)
  machinery = (out / "buildutil.cmake").read_text()
  assert 'file(GLOB _buildutil_local_cmake CONFIGURE_DEPENDS' in machinery
  assert '"${CMAKE_SOURCE_DIR}/cmake/*.cmake"' in machinery
  assert "list(SORT _buildutil_local_cmake)" in machinery   # deterministic
  # after every function the machinery defines
  assert machinery.rindex("\nfunction(") < machinery.index("_buildutil_local_cmake")
  # a project file dropped in `cmake/` is NOT deposited, rendered or read —
  # only include()d by the cmake above, at configure time
  (tmp_path / "cmake").mkdir()
  local = tmp_path / "cmake" / "acme.cmake"
  local.write_text("# @CMAKE_OPTION_PREFIX@ stays verbatim: not ours\n")
  deposit.ensure(tmp_path, CFG)
  assert local.read_text() == "# @CMAKE_OPTION_PREFIX@ stays verbatim: not ours\n"
  assert "acme.cmake" not in {p.name for p in out.iterdir()}


def test_extensions_come_from_declaration(tmp_path):
  assert "watcom" in deposit.extensions()
  out = deposit.ensure(tmp_path, CFG, ["watcom"])
  assert "watcom.cmake" in {p.name for p in out.iterdir()}
  w = (out / "watcom.cmake").read_text()
  assert "ACME_" in w and "@" + "CMAKE_OPTION_PREFIX@" not in w
  assert "-m buildutil.orom_finalize" in w
  # an unknown declaration refuses loudly, naming what exists
  try:
    deposit.ensure(tmp_path, CFG, ["msdos"])
  except SystemExit as e:
    assert "watcom" in str(e)
  else:
    raise AssertionError("unknown extension did not refuse")


def test_init_end_to_end(tmp_path):
  """`python -m buildutil init` in an empty dir — THE fresh-project
  story: writes buildutil.toml (with the [cmake] section), renders the
  machinery into _bdudata, commits NOTHING project-side, works with no
  venv and no third-party deps."""
  pkg_parent = str(Path(deposit.__file__).resolve().parents[1])
  cp = subprocess.run(
    [sys.executable, "-m", "buildutil", "init", "--name", "acme",
     "--module-prefix", "ACM"],
    cwd=tmp_path, capture_output=True, text=True,
    env={"PATH": "/usr/bin:/bin", "PYTHONPATH": pkg_parent})
  assert cp.returncode == 0, cp.stderr
  toml = (tmp_path / "buildutil.toml").read_text()
  assert 'name = "acme"' in toml and "[cmake]" in toml
  assert (tmp_path / "_bdudata" / "cmake" / "buildutil.cmake").exists()
  assert not (tmp_path / "cmake").exists(), "machinery leaked project-side"
  # the scaffolded root CMakeLists carries the one machinery hook
  assert "include(buildutil)" in (tmp_path / "CMakeLists.txt").read_text()
  cp2 = subprocess.run(
    [sys.executable, "-m", "buildutil", "init"],
    cwd=tmp_path, capture_output=True, text=True,
    env={"PATH": "/usr/bin:/bin", "PYTHONPATH": pkg_parent})
  assert cp2.returncode == 0 and "keeping it" in cp2.stdout, cp2.stdout


def test_watcom_template_base_org_and_warning_disables():
  """Init_firmware takes BASE/ORG (hex segment:offset, default F000:0000;
  OPROM bases at C800) and the lnk scripts disable wlink's W1014/W1023 —
  a raw ROM has no stack segment and no start address by design."""
  from pathlib import Path
  import buildutil
  template = (Path(buildutil.__file__).parent
              / "templates" / "cmake" / "ext" / "watcom" / "watcom.cmake")
  text = template.read_text()
  assert 'cmake_parse_arguments(FW "LIBRARY;OPROM" "BASE;ORG" "" ${ARGN})' in text
  assert text.count("disable 1014, 1023") == 2       # both layouts
  assert 'segaddr=0x${base} offset=0x${org}' in text
  assert 'set(FW_BASE "F000")' in text and 'set(FW_BASE "C800")' in text
  assert 'set(FW_ORG "0000")' in text


def test_msvc_reads_the_sources_as_utf8(tmp_path):
  """/utf-8, and it is not cosmetic: every other compiler reads a source
  file as UTF-8 and MSVC reads it in the system codepage without this, so
  `U'é'` is "error C2015: too many characters in constant" and a string
  literal quietly becomes mojibake -- thirteen of them across two test
  files, the first time one project was compiled by a native cl."""
  machinery = (deposit.ensure(tmp_path, CFG) / "buildutil.cmake").read_text()
  msvc_options = [line for line in machinery.splitlines()
                  if "CXX_COMPILER_ID:MSVC>:/std:c++latest" in line]
  assert msvc_options, "the MSVC compile-options genex moved"
  assert "/utf-8" in msvc_options[0]


def test_test_discovery_never_runs_in_the_source_tree(tmp_path):
  """gtest_discover_tests' WORKING_DIRECTORY is where the DISCOVERY runs
  too, and CMake 4.2 writes its listing there:

      set(json_file
        "${arg_TEST_WORKING_DIR}/cmake_test_discovery_${target_hash}.json")

  Pointed at the module's sources -- which is where the tests must run --
  that put a generated file under sources/ in every module, conan exported
  them (sources/* goes in wholesale), and one package shipped a
  SECOND recipe revision from the Windows runner differing from Linux's by
  exactly those five files. Consumers resolve the latest revision, so that
  one hid the Linux and macOS binaries.

  The discovery runs in the build tree now and the tests keep their
  working directory through TEST_LIST at ctest time. Measured on cmake
  4.2.3 and 4.4.2: json in the build dir, WORKING_DIRECTORY still the
  module's source dir."""
  machinery = (deposit.ensure(tmp_path, CFG) / "buildutil.cmake").read_text()
  start = machinery.index("gtest_discover_tests(")
  call = machinery[start:machinery.index("\n  set(_buildutil_workdir_script",
                                         start)]
  assert "WORKING_DIRECTORY" not in call, (
    "gtest_discover_tests must not be handed a working directory: it is "
    "the DISCOVERY's cwd as well as the tests', and cmake 4.2 drops "
    "cmake_test_discovery_<hash>.json into it")
  assert "TEST_LIST ${test_target}_discovered" in call

  # ...and the tests still get it, at ctest time, where that list exists
  assert "TEST_INCLUDE_FILES" in machinery
  assert ('WORKING_DIRECTORY \\"${CMAKE_CURRENT_SOURCE_DIR}\\"') in machinery
