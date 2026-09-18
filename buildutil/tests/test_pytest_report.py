"""The case counts behind one ctest entry, and the all-skipped verdict.

A `*.test/` python suite is registered as a single ctest entry, so
`Passed` is everything the ctest summary can say about it: how many cases
ran, and whether any of them did anything, is invisible. A suite that
skips every case for want of a corpus reads exactly like a suite that
proved something. buildutil_pytest.py is the plugin that answers both,
and `buildutil test` prints what it wrote.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from buildutil import deposit
from buildutil.commands.test import PYTEST_REPORTS, pytest_suite_lines

PYSUPPORT = Path(deposit.__file__).resolve().parent / "pysupport"

MIXED = """\
import pytest


def test_it_ran():
  assert True


@pytest.mark.skip(reason="no corpus")
def test_it_did_not():
  assert False
"""

EVERY_CASE_SKIPS = """\
import pytest


@pytest.mark.skip(reason="no corpus")
def test_it_did_not():
  assert False
"""

NOTHING_IMPORTS = """\
import pytest

pytest.skip("no corpus", allow_module_level=True)


def test_it_did_not():
  assert False
"""


def _pytest(directory: Path, source: str, report: Path):
  (directory / "test_suite.py").write_text(source)
  return subprocess.run(
    [sys.executable, "-m", "pytest", "-p", "buildutil_pytest",
     "--import-mode=importlib", str(directory), "-q", "-rs"],
    capture_output=True, text=True, cwd=directory,
    env={**os.environ, "PYTHONPATH": str(PYSUPPORT),
         "BUILDUTIL_PYTEST_REPORT": str(report)})


def test_the_counts_are_what_pytest_counted(tmp_path):
  report = tmp_path / "r" / "widget-pytest.json"
  run = _pytest(tmp_path, MIXED, report)
  assert run.returncode == 0, run.stdout + run.stderr
  assert json.loads(report.read_text()) == {"passed": 1, "skipped": 1}


def test_a_suite_that_skipped_everything_exits_as_a_skip(tmp_path):
  """77 is what the entry's SKIP_RETURN_CODE reads, so ctest calls this
  Skipped rather than a clean pass -- the whole point of the ticket."""
  report = tmp_path / "r" / "widget-pytest.json"
  run = _pytest(tmp_path, EVERY_CASE_SKIPS, report)
  assert run.returncode == 77, run.stdout + run.stderr
  assert json.loads(report.read_text()) == {"skipped": 1}


def test_a_module_that_skips_itself_counts_and_skips_too(tmp_path):
  """The shape the ticket was filed about: a missing corpus skips at
  import, so pytest collects no case at all and exits 5 -- which would
  otherwise be a FAILED entry, for a reason nobody reads."""
  report = tmp_path / "r" / "widget-pytest.json"
  run = _pytest(tmp_path, NOTHING_IMPORTS, report)
  assert run.returncode == 77, run.stdout + run.stderr
  assert json.loads(report.read_text()) == {"skipped": 1}


def test_a_failure_stays_a_failure(tmp_path):
  report = tmp_path / "r" / "widget-pytest.json"
  run = _pytest(tmp_path, "def test_no():\n  assert False\n", report)
  assert run.returncode == 1, run.stdout
  assert json.loads(report.read_text()) == {"failed": 1}


def _report(build: Path, suite: str, counts: dict) -> None:
  directory = build / PYTEST_REPORTS
  directory.mkdir(parents=True, exist_ok=True)
  (directory / f"{suite}.json").write_text(json.dumps(counts))


def test_the_summary_names_every_suite_and_its_cases(tmp_path):
  _report(tmp_path, "widget-pytest", {"passed": 9, "skipped": 8})
  _report(tmp_path, "tools-pytest", {"passed": 2})
  assert pytest_suite_lines(tmp_path) == [
    "tools-pytest: 2 passed", "widget-pytest: 9 passed, 8 skipped"]


def test_a_tree_with_no_python_suite_says_nothing(tmp_path):
  assert pytest_suite_lines(tmp_path) == []
