"""[test] python: a python suite for a directory that is not a module.

A module's suite is declared by presence (`*.test.py` inside it). A
top-level `tools/` has no CMakeLists to be found through, so before this
the only way to register it was to call two private `_buildutil_*`
functions from the project's root CMakeLists, which is what this ends.
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


def _config(tmp_path, toml):
  (tmp_path / "buildutil.toml").write_text(toml)
  env = {**os.environ, "PYTHONPATH": PKG_PARENT}
  env.pop("BUILDUTIL_ROOT", None)
  return subprocess.run([sys.executable, "-c", PROBE], cwd=tmp_path, env=env,
                        capture_output=True, text=True)


def _project(tmp_path, suite=SUITE):
  deposit.ensure(tmp_path, {**CFG, "test_python_suites": ["tools"]})
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
  assert '_buildutil_python_suites("tools;qa/py")' in rendered
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
