"""A *.test.py suite runs where the driver runs, image or venv.

The registration used to name `<source>/_pyvenv/bin/python`. Under
BUILDUTIL_SYSTEM=1 -- a container image whose whole point is that it
carries the toolchain instead of building one per checkout -- there is no
_pyvenv, so ctest was given a command that does not exist and reported
"***Not Run": not a failure, not a pass, just a suite that silently never
ran. A project's first python suite spent a pipeline saying so
(green locally, Not Run in CI, four red Linux cells around it).

These tests never create a project venv, so they ARE the system-mode
case: the interpreter passed as BUILDUTIL_PY is the one running pytest
right now.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from buildutil import deposit

PYSUPPORT = Path(deposit.__file__).resolve().parent / "pysupport"
CFG = {"cmake_option_prefix": "PY", "module_define_prefix": "PY"}

e2e = pytest.mark.skipif(
  shutil.which("cmake") is None or shutil.which("ninja") is None,
  reason="needs cmake and ninja")

ROOT_CMAKE = """\
cmake_minimum_required(VERSION 3.25)
project(pysuite CXX)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
enable_testing()
add_subdirectory(sources)
"""

SUITE = """\
def test_the_suite_ran():
  assert True
"""


def _project(tmp_path, suite=SUITE):
  deposit.ensure(tmp_path, CFG)
  (tmp_path / "CMakeLists.txt").write_text(ROOT_CMAKE)
  src = tmp_path / "sources"
  src.mkdir()
  (src / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  module = src / "widget"
  module.mkdir()
  (module / "CMakeLists.txt").write_text("Init_submodule()\n")
  (module / "thing.cpp").write_text("int thing() { return 0; }\n")
  (module / "api.test.py").write_text(suite)
  return tmp_path


def _configure(root, build, *extra):
  return subprocess.run(
    ["cmake", "-S", str(root), "-B", str(build), "-G", "Ninja",
     "-DBUILD_TESTING=ON",
     f"-DBUILDUTIL_PY={sys.executable}",
     f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}", *extra],
    capture_output=True, text=True)


@e2e
def test_the_suite_is_registered_against_the_drivers_interpreter(tmp_path):
  root = _project(tmp_path)
  build = tmp_path / "b"
  proc = _configure(root, build)
  assert proc.returncode == 0, proc.stdout + proc.stderr
  registered = (build / "sources" / "widget" / "CTestTestfile.cmake").read_text()
  assert "widget-pytest" in registered
  assert sys.executable in registered, registered
  assert "_pyvenv" not in registered, (
    "the command must be the interpreter the driver runs under -- a "
    "venv-relative path is ***Not Run on a BUILDUTIL_SYSTEM image")


@e2e
def test_the_suite_actually_runs_without_a_project_venv(tmp_path):
  """The proof the old form could not give: ctest RUNS it. No _pyvenv
  exists anywhere in this project, which is the image's situation."""
  root = _project(tmp_path)
  build = tmp_path / "b"
  assert _configure(root, build).returncode == 0
  assert not (root / "_pyvenv").exists()
  built = subprocess.run(["cmake", "--build", str(build)],
                         capture_output=True, text=True)
  assert built.returncode == 0, built.stdout + built.stderr
  run = subprocess.run(["ctest", "--test-dir", str(build), "-R", "widget-pytest",
                        "--output-on-failure"],
                       capture_output=True, text=True,
                       env={**__import__("os").environ, "BUILDUTIL": "1"})
  assert "Not Run" not in run.stdout, run.stdout
  assert run.returncode == 0, run.stdout + run.stderr
  assert "1 tests passed" in run.stdout or "100% tests passed" in run.stdout


@e2e
def test_a_missing_pytest_is_said_out_loud_at_configure(tmp_path):
  """Instead of registering a test that cannot run. The message names the
  suite file and the interpreter, because the fix is on the machine (or
  in the image), not in the project."""
  root = _project(tmp_path)
  bare = tmp_path / "no-pytest.py"          # an interpreter without pytest
  bare.write_text(
    "import sys, runpy\n"
    "if sys.argv[1:2] == ['-c'] and 'pytest' in sys.argv[2]:\n"
    "    raise SystemExit(1)\n"
    "raise SystemExit(0)\n")
  proc = _configure(root, tmp_path / "b",
                    f"-DBUILDUTIL_PY={sys.executable} {bare}")
  # cmake takes the whole string as the command; a space in it makes the
  # probe fail exactly as a pytest-less interpreter would
  assert proc.returncode != 0
  assert "pytest is missing for" in proc.stderr
  assert "api.test.py" in proc.stderr


# A suite directory is a directory, not a bag of test files: pytest's own
# conventions say which of its `.py` files are tests, and the rest --
# conftest.py, a shared wire-protocol module -- are helpers the suite
# imports.
HELPER_SUITE = {
  "conftest.py": (
    "import pytest\n\n\n"
    "@pytest.fixture\n"
    "def payload():\n"
    "  return 'framed'\n"),
  "_wire.py": "def frame(text):\n  return '<' + text + '>'\n",
  "test_wire.py": (
    "import _wire\n\n\n"
    "def test_the_helper_did_the_framing(payload):\n"
    "  assert _wire.frame(payload) == '<framed>'\n"),
}


def _suite_directory(tmp_path, files=HELPER_SUITE):
  root = _project(tmp_path)
  (root / "sources" / "widget" / "api.test.py").unlink()
  directory = root / "sources" / "widget" / "api.test"
  directory.mkdir()
  for name, text in files.items():
    (directory / name).write_text(text)
  return root


def _ctest(build, name="widget-pytest", *extra):
  return subprocess.run(
    ["ctest", "--test-dir", str(build), "-R", name, *extra],
    capture_output=True, text=True,
    env={**__import__("os").environ, "BUILDUTIL": "1"})


@e2e
def test_a_suite_directory_is_handed_to_pytest_whole(tmp_path):
  root = _suite_directory(tmp_path)
  build = tmp_path / "b"
  proc = _configure(root, build)
  assert proc.returncode == 0, proc.stdout + proc.stderr
  registered = (build / "sources" / "widget" / "CTestTestfile.cmake").read_text()
  assert "python_files=test_*.py" in registered, registered
  assert "conftest.py" not in registered, (
    "conftest.py was named as a test file; pytest finds it by itself")
  assert "_wire.py" not in registered, "a helper module is not a test file"


@e2e
def test_a_helper_module_next_to_the_suite_is_importable(tmp_path):
  """The point of having a suite directory at all: two suites can share
  a wire-protocol module instead of each carrying a copy."""
  root = _suite_directory(tmp_path)
  build = tmp_path / "b"
  assert _configure(root, build).returncode == 0
  built = subprocess.run(["cmake", "--build", str(build)],
                         capture_output=True, text=True)
  assert built.returncode == 0, built.stdout + built.stderr
  run = _ctest(build, "widget-pytest", "-V")
  assert run.returncode == 0, run.stdout + run.stderr
  assert "1 passed" in run.stdout, run.stdout


@e2e
def test_a_test_directory_of_c_sources_registers_no_python_suite(tmp_path):
  """`*.test/` is the gtest subtree convention too, and a directory with
  no python test file in it is not a python suite."""
  root = _suite_directory(tmp_path, files={"helpers.py": "VALUE = 1\n"})
  build = tmp_path / "b"
  proc = _configure(root, build)
  assert proc.returncode == 0, proc.stdout + proc.stderr
  registered = (build / "sources" / "widget" / "CTestTestfile.cmake").read_text()
  assert "widget-pytest" not in registered, registered


MIXED_CASES = """\
import pytest


def test_the_suite_ran():
  assert True


@pytest.mark.skip(reason="no corpus")
def test_the_corpus_is_missing():
  assert False
"""

EVERY_CASE_SKIPS = """\
import pytest


@pytest.mark.skip(reason="no corpus")
def test_the_corpus_is_missing():
  assert False
"""


def _built(tmp_path, suite):
  root = _project(tmp_path, suite=suite)
  build = tmp_path / "b"
  assert _configure(root, build).returncode == 0
  built = subprocess.run(["cmake", "--build", str(build)],
                         capture_output=True, text=True)
  assert built.returncode == 0, built.stdout + built.stderr
  return build


@e2e
def test_the_suites_case_counts_land_in_the_build_tree(tmp_path):
  """What `buildutil test` prints per suite, and the only place the count
  behind a single ctest entry exists at all."""
  build = _built(tmp_path, MIXED_CASES)
  run = _ctest(build)
  assert run.returncode == 0, run.stdout + run.stderr
  counts = build / "Testing" / "buildutil-pytest" / "widget-pytest.json"
  assert json.loads(counts.read_text()) == {"passed": 1, "skipped": 1}


@e2e
def test_a_suite_that_skipped_every_case_is_a_ctest_skip(tmp_path):
  """Not a pass. The suite ran nothing, and ctest says so in the same
  words it uses for every other skipped entry."""
  build = _built(tmp_path, EVERY_CASE_SKIPS)
  run = _ctest(build)
  assert run.returncode == 0, run.stdout + run.stderr
  assert "Skipped" in run.stdout, run.stdout
  counts = build / "Testing" / "buildutil-pytest" / "widget-pytest.json"
  assert json.loads(counts.read_text()) == {"skipped": 1}
