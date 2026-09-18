"""Dormant modules, read by the code that actually drops them.

_bdudata/modules.ini is parsed twice: by modules.py, which is what the
CLI reports, and by buildutil.cmake, which is what the build obeys. They
have to agree. They did not for any name holding a character an
identifier cannot: cmake matched `^[A-Za-z0-9_]+`, so `mstools-cl` under
[disabled] read as `mstools` — the CLI said one module was dormant while
the build dropped a different one and kept building the named one.

Only cmake can show that, so this drives a real configure.
"""
import shutil
import subprocess

import pytest

from buildutil import deposit, modules

CFG = {"cmake_option_prefix": "ACME", "module_define_prefix": "ACM"}

pytestmark = pytest.mark.skipif(
  shutil.which("cmake") is None or
  not (shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")),
  reason="needs cmake and a C++ compiler")

ROOT_CMAKE = """\
cmake_minimum_required(VERSION 3.25)
project(toggles CXX)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
include(CTest)
add_subdirectory(sources)
"""


def _tree(root, names, defaults=()):
  deposit.ensure(root, {**CFG, "modules_dormant": list(defaults)})
  (root / "CMakeLists.txt").write_text(ROOT_CMAKE)
  src = root / "sources"
  src.mkdir(exist_ok=True)
  (src / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  for name in names:
    (src / name).mkdir(parents=True)
    (src / name / "CMakeLists.txt").write_text("Init_submodule()\n")
    (src / name / "unit.cpp").write_text(
      f"int {name.replace('-', '_')}_unit() {{ return 0; }}\n")
  return root


def _targets(root) -> str:
  cfg = subprocess.run(["cmake", "-S", str(root), "-B", str(root / "b")],
                       capture_output=True, text=True)
  assert cfg.returncode == 0, cfg.stdout + cfg.stderr
  helpstr = subprocess.run(
    ["cmake", "--build", str(root / "b"), "--target", "help"],
    capture_output=True, text=True)
  return helpstr.stdout


def test_disabling_a_hyphenated_module_drops_that_module(tmp_path):
  """...and not its unhyphenated sibling, which is what used to happen."""
  _tree(tmp_path, ["foo", "foo-bar"])
  modules.save(tmp_path / "_bdudata" / "modules.ini", ["foo"], ["foo-bar"])
  targets = _targets(tmp_path)
  assert "foo-bar" not in targets, "the named module is still building"
  assert "foo" in targets, (
    "disabling foo-bar took foo out of the build — the ini name was "
    "truncated at the hyphen again")


def test_disabling_nothing_builds_everything(tmp_path):
  _tree(tmp_path, ["foo", "foo-bar"])
  modules.save(tmp_path / "_bdudata" / "modules.ini", ["foo", "foo-bar"], [])
  targets = _targets(tmp_path)
  assert "foo-bar" in targets and "foo" in targets


def test_the_two_parsers_agree_on_what_is_dormant(tmp_path):
  """modules.py drives the CLI's answer, buildutil.cmake drives the build.
  A name either parser mangles is a lie told to somebody."""
  _tree(tmp_path, ["foo", "foo-bar"])
  ini = tmp_path / "_bdudata" / "modules.ini"
  modules.save(ini, ["foo"], ["foo-bar"])
  assert modules.load(ini)[1] == ["foo-bar"]          # what the CLI reports
  assert "foo-bar" not in _targets(tmp_path)          # what the build does


def test_a_clean_clone_honours_the_committed_default(tmp_path):
  """No _bdudata/modules.ini exists — the state a
  fresh clone is in — and the project's own declaration still has to drop
  the module. Before this, the fact lived only in the gitignored ini, so a
  clone built partly-ported modules and died with nothing committed to say
  they were never meant to build."""
  _tree(tmp_path, ["foo", "notready"], defaults=["notready"])
  assert not (tmp_path / "_bdudata" / "modules.ini").exists()
  targets = _targets(tmp_path)
  assert "notready" not in targets, "the committed default did not apply"
  assert "foo" in targets


def test_a_box_can_revive_a_default_dormant_module(tmp_path):
  """A default, not a lock: whoever is porting it builds it locally without
  editing a committed file."""
  _tree(tmp_path, ["foo", "notready"], defaults=["notready"])
  modules.save(tmp_path / "_bdudata" / "modules.ini", ["notready"], [])
  targets = _targets(tmp_path)
  assert "notready" in targets, "[enabled] did not override the toml default"


def test_the_two_parsers_agree_about_the_committed_default_too(tmp_path):
  """The two-parser lesson applied to the new layer: modules.py answers the CLI and
  buildutil.cmake answers the build, and the merge rule now lives in both. A
  disagreement here is invisible in either file on its own."""
  cases = [
    (["notready"], [], [], {"notready"}),          # toml only
    ([], [], ["notready"], {"notready"}),          # ini only
    (["notready"], ["notready"], [], set()),       # ini revives it
    (["notready"], [], ["foo"], {"notready", "foo"}),   # both add
  ]
  for defaults, enabled, disabled, expected in cases:
    root = tmp_path / f"case{len(list(tmp_path.iterdir()))}"
    root.mkdir()
    _tree(root, ["foo", "notready"], defaults=defaults)
    ini = root / "_bdudata" / "modules.ini"
    if enabled or disabled:
      modules.save(ini, enabled, disabled)
    assert modules.dormant_set(ini, defaults=defaults) == expected, (
      f"python disagrees for {defaults=} {enabled=} {disabled=}")
    targets = _targets(root)
    for name in ("foo", "notready"):
      built = name in targets
      assert built == (name not in expected), (
        f"cmake disagrees with python for {name}: cmake built={built}, "
        f"python says dormant={name in expected} ({defaults=} {enabled=} "
        f"{disabled=})")


def _ctest_names(root) -> str:
  subprocess.run(["cmake", "-S", str(root), "-B", str(root / "b")],
                 capture_output=True, text=True, check=True)
  out = subprocess.run(["ctest", "--show-only=json-v1", "--test-dir", str(root / "b")],
                       capture_output=True, text=True)
  return out.stdout


def test_a_python_test_file_registers_a_suite_by_presence(tmp_path):
  """A *.test.py registers exactly as a *.test.cpp does — no cmake
  surface, no declaration. Driver-style suites spawn the project's own
  tools, which is subprocess scripting; making them C++ is what bred the
  compile-time path plumbing since removed."""
  _tree(tmp_path, ["tool"])
  (tmp_path / "sources" / "tool" / "smoke.test.py").write_text(
    "def test_ok():\n    assert True\n")
  names = _ctest_names(tmp_path)
  assert "tool-pytest" in names, "a *.test.py registered no suite"


def test_python_in_a_test_subtree_registers_too(tmp_path):
  """The directory half of the convention, matching *.test/ for C++."""
  _tree(tmp_path, ["tool"])
  suite = tmp_path / "sources" / "tool" / "driver.test"
  suite.mkdir()
  (suite / "run.py").write_text("def test_ok():\n    assert True\n")
  assert "tool-pytest" in _ctest_names(tmp_path)


def test_a_python_suite_carries_the_same_runner_contract(tmp_path):
  """Same PATH and cwd as every other test process, so a python suite
  invokes built tools by bare name exactly as a gtest one does."""
  _tree(tmp_path, ["tool"])
  (tmp_path / "sources" / "tool" / "smoke.test.py").write_text(
    "def test_ok():\n    assert True\n")
  spec = _ctest_names(tmp_path)
  assert "path_list_prepend" in spec and "bin" in spec
  assert str(tmp_path / "sources" / "tool") in spec      # cwd
  assert "-pytest" in spec


def test_a_module_without_python_registers_no_suite(tmp_path):
  """Presence-driven means absence-driven too."""
  _tree(tmp_path, ["tool"])
  assert "tool-pytest" not in _ctest_names(tmp_path)

