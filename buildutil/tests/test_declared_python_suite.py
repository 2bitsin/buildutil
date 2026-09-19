"""[test] python: a python suite for a directory that is not a module.

A module's suite is declared by presence (`*.test.py` inside it). A
top-level `tools/` has no CMakeLists to be found through, so before this
the only way to register it was to call two private `_buildutil_*`
functions from the project's root CMakeLists, which is what this ends.
Its `[test.timeout]` half is the bound such a suite carries, one entry
holding however many cases.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from buildutil import deposit

PKG_PARENT = str(Path(__file__).resolve().parents[2])
PYSUPPORT = Path(deposit.__file__).resolve().parent / "pysupport"
CFG = {"cmake_option_prefix": "PY", "module_define_prefix": "PY"}

e2e = pytest.mark.skipif(
  shutil.which("cmake") is None or shutil.which("ninja") is None,
  reason="needs cmake and ninja")

PROBE = ("import json, buildutil.config as c;"
         "print(json.dumps(c.PROJECT['test_python_suites']))")
BOUNDS_PROBE = ("import json, buildutil.config as c;"
                "print(json.dumps(c.PROJECT['test_python_timeouts']))")

ROOT_CMAKE = """\
cmake_minimum_required(VERSION 3.25)
project(declared CXX)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
"""

SUITE = """\
from pathlib import Path


def test_the_suite_ran_in_its_own_directory():
  assert Path.cwd().name == "tools"
"""

SLOW_SUITE = """\
import time


def test_the_case_takes_longer_than_a_gtest_case():
  time.sleep(2)
"""


def _config(tmp_path, toml, probe=PROBE):
  (tmp_path / "buildutil.toml").write_text(toml)
  env = {**os.environ, "PYTHONPATH": PKG_PARENT}
  env.pop("BUILDUTIL_ROOT", None)
  return subprocess.run([sys.executable, "-c", probe], cwd=tmp_path, env=env,
                        capture_output=True, text=True)


def _project(tmp_path, suite=SUITE, timeouts=None):
  deposit.ensure(tmp_path, {**CFG, "test_python_suites": ["tools"],
                            "test_python_timeouts": timeouts or {}})
  (tmp_path / "CMakeLists.txt").write_text(ROOT_CMAKE)
  tools = tmp_path / "tools"
  tools.mkdir()
  if suite is not None:
    (tools / "test_thing.py").write_text(suite)
  return tmp_path


def _said(proc):
  """cmake wraps a message() at its own width, so match on the words."""
  return " ".join(proc.stderr.split())


def _configure(root, build):
  return subprocess.run(
    ["cmake", "-S", str(root), "-B", str(build), "-G", "Ninja",
     "-DBUILD_TESTING=ON",
     f"-DBUILDUTIL_PY={sys.executable}",
     f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}"],
    capture_output=True, text=True)


def test_declared_directories_are_read(tmp_path):
  got = _config(tmp_path, '[test]\npython = ["tools", "scripts/py"]\n')
  assert got.returncode == 0, got.stderr
  assert json.loads(got.stdout) == ["tools", "scripts/py"]


def test_nothing_declared_is_no_suite(tmp_path):
  got = _config(tmp_path, "")
  assert json.loads(got.stdout) == []


def test_an_unknown_key_names_itself(tmp_path):
  got = _config(tmp_path, '[test]\npythons = ["tools"]\n')
  assert got.returncode != 0
  assert "unknown key(s) pythons" in got.stderr


def test_a_path_out_of_the_repo_is_refused(tmp_path):
  for bad in ("/etc", "../elsewhere"):
    got = _config(tmp_path, f'[test]\npython = ["{bad}"]\n')
    assert got.returncode != 0
    assert "inside the repo" in got.stderr


def test_the_declaration_renders_as_one_call(tmp_path):
  deposit.ensure(tmp_path, {**CFG, "test_python_suites": ["tools", "qa/py"]})
  rendered = (tmp_path / "_bdudata" / "cmake" / "buildutil.cmake").read_text()
  assert '_buildutil_python_suites("tools;qa/py" "")' in rendered
  assert "@PYTHON_SUITES@" not in rendered


def test_no_declaration_renders_no_call(tmp_path):
  deposit.ensure(tmp_path, CFG)
  rendered = (tmp_path / "_bdudata" / "cmake" / "buildutil.cmake").read_text()
  assert "_buildutil_python_suites(" not in rendered
  assert "@PYTHON_SUITES@" not in rendered


@e2e
def test_the_entry_is_what_a_module_suite_gets(tmp_path):
  root = _project(tmp_path)
  build = tmp_path / "b"
  proc = _configure(root, build)
  assert proc.returncode == 0, proc.stdout + proc.stderr
  registered = (build / "CTestTestfile.cmake").read_text()
  assert "tools-pytest" in registered
  assert sys.executable in registered, registered
  assert "--import-mode=importlib" in registered
  assert "buildutil-driver" in registered


@e2e
def test_ctest_runs_the_declared_suite(tmp_path):
  root = _project(tmp_path)
  build = tmp_path / "b"
  assert _configure(root, build).returncode == 0
  run = subprocess.run(
    ["ctest", "--test-dir", str(build), "-R", "tools-pytest",
     "--output-on-failure"],
    capture_output=True, text=True, env={**os.environ, "BUILDUTIL": "1"})
  assert "Not Run" not in run.stdout, run.stdout
  assert run.returncode == 0, run.stdout + run.stderr


@e2e
def test_a_declared_directory_with_no_suite_fails_the_configure(tmp_path):
  """The failure this replaces is a declaration that quietly collects
  nothing, so an empty directory is loud at configure time."""
  root = _project(tmp_path, suite=None)
  proc = _configure(root, tmp_path / "b")
  assert proc.returncode != 0
  assert "holds no test file" in _said(proc)


@e2e
def test_a_missing_directory_names_itself(tmp_path):
  root = _project(tmp_path)
  shutil.rmtree(root / "tools")
  proc = _configure(root, tmp_path / "b")
  assert proc.returncode != 0
  assert "not a directory of this repository" in _said(proc)


@e2e
def test_the_buildutil_spelling_collects_too(tmp_path):
  """A suite moved out of a module keeps its name and still runs."""
  root = _project(tmp_path, suite=None)
  (root / "tools" / "api.test.py").write_text(SUITE)
  build = tmp_path / "b"
  assert _configure(root, build).returncode == 0
  run = subprocess.run(
    ["ctest", "--test-dir", str(build), "-R", "tools-pytest", "-V"],
    capture_output=True, text=True, env={**os.environ, "BUILDUTIL": "1"})
  assert run.returncode == 0, run.stdout + run.stderr
  assert "1 passed" in run.stdout, run.stdout


def test_a_declared_bound_is_read_by_suite(tmp_path):
  got = _config(tmp_path, '[test]\npython = ["tools", "examples/scooby"]\n'
                          '[test.timeout]\n"examples/scooby" = 240\n',
                probe=BOUNDS_PROBE)
  assert got.returncode == 0, got.stderr
  assert json.loads(got.stdout) == {"examples/scooby": 240}


def test_no_declared_bound_is_an_empty_table(tmp_path):
  got = _config(tmp_path, '[test]\npython = ["tools"]\n', probe=BOUNDS_PROBE)
  assert got.returncode == 0, got.stderr
  assert json.loads(got.stdout) == {}


def test_a_bound_for_an_undeclared_suite_is_refused_by_name(tmp_path):
  got = _config(tmp_path, '[test]\npython = ["tools"]\n'
                          '[test.timeout]\n"examples/scooby" = 240\n')
  assert got.returncode != 0
  assert "examples/scooby" in got.stderr
  assert "[test] python" in got.stderr


def test_a_bound_that_is_not_a_count_of_seconds_is_refused(tmp_path):
  for bad in ('"soon"', "true", "0", "-5"):
    got = _config(tmp_path, '[test]\npython = ["tools"]\n'
                            f'[test.timeout]\ntools = {bad}\n')
    assert got.returncode != 0, bad
    assert "[test.timeout]" in got.stderr, bad


def test_the_bound_rides_the_registration_call(tmp_path):
  deposit.ensure(tmp_path, {**CFG, "test_python_suites": ["tools", "qa/py"],
                            "test_python_timeouts": {"qa/py": 240}})
  rendered = (tmp_path / "_bdudata" / "cmake" / "buildutil.cmake").read_text()
  assert '_buildutil_python_suites("tools;qa/py" "qa/py=240")' in rendered


def _entry_properties(build, name):
  shown = subprocess.run(
    ["ctest", "--test-dir", str(build), "--show-only=json-v1"],
    capture_output=True, text=True)
  assert shown.returncode == 0, shown.stdout + shown.stderr
  entries = json.loads(shown.stdout)["tests"]
  entry = next(one for one in entries if one["name"] == name)
  return {one["name"]: one["value"] for one in entry.get("properties", [])}


@e2e
def test_a_declared_bound_is_the_entrys_ctest_timeout(tmp_path):
  """A suite is ONE entry however many cases it holds, so --timeout, which
  is right for a gtest case, is wrong for it by construction."""
  root = _project(tmp_path, timeouts={"tools": 240})
  build = tmp_path / "b"
  assert _configure(root, build).returncode == 0
  assert _entry_properties(build, "tools-pytest")["TIMEOUT"] == 240


@e2e
def test_a_suite_with_no_bound_leaves_the_timeout_to_the_driver(tmp_path):
  root = _project(tmp_path)
  build = tmp_path / "b"
  assert _configure(root, build).returncode == 0
  assert "TIMEOUT" not in _entry_properties(build, "tools-pytest")


@e2e
def test_a_declared_bound_outranks_the_drivers_timeout(tmp_path):
  """What the driver's `--timeout` being a floor rests on: ctest honours a
  test's own TIMEOUT over the one on its command line. The same suite with
  no bound is what the flag then kills."""
  root = _project(tmp_path, suite=SLOW_SUITE, timeouts={"tools": 60})
  env = {**os.environ, "BUILDUTIL": "1"}
  bound = tmp_path / "bound"
  assert _configure(root, bound).returncode == 0
  ran = subprocess.run(
    ["ctest", "--test-dir", str(bound), "-R", "tools-pytest", "--timeout", "1"],
    capture_output=True, text=True, env=env)
  assert ran.returncode == 0, ran.stdout + ran.stderr
  bare = tmp_path / "bare"
  deposit.ensure(root, {**CFG, "test_python_suites": ["tools"]})
  assert _configure(root, bare).returncode == 0
  killed = subprocess.run(
    ["ctest", "--test-dir", str(bare), "-R", "tools-pytest", "--timeout", "1"],
    capture_output=True, text=True, env=env)
  assert killed.returncode != 0
  assert "Timeout" in killed.stdout, killed.stdout
