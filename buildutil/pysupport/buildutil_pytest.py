"""What one buildutil python suite did, for a build that can only see
one ctest entry per suite.

Discovering pytest cases at configure time was ruled out (it means
running the project's tests to find out what they are), so a suite's
case count -- and a suite that skipped every case it has -- was invisible
behind a single `Passed`. This plugin is loaded for every suite buildutil
registers: it counts the cases, writes the counts where `buildutil test`
reads them back, and ends an all-skipped session with the return code
ctest is told to read as a skip.
"""
import json
import os
from pathlib import Path

from pytest import ExitCode

# ctest reads this as "skipped" through the entry's SKIP_RETURN_CODE.
SKIP_EXIT = 77

_counts: dict[str, int] = {}


def pytest_runtest_logreport(report) -> None:
  # one count per case: its call outcome, or the setup that stopped it
  # from ever being called
  if report.when == "call" or (report.when == "setup"
                               and report.outcome != "passed"):
    _counts[report.outcome] = _counts.get(report.outcome, 0) + 1


def pytest_collectreport(report) -> None:
  # a module that skips itself (`pytest.skip(allow_module_level=True)`,
  # the shape a suite with no corpus takes) produces no case reports at
  # all -- pytest counts it as a skip, and so does this
  if report.outcome == "skipped":
    _counts["skipped"] = _counts.get("skipped", 0) + 1


def pytest_sessionfinish(session, exitstatus) -> None:
  destination = os.environ.get("BUILDUTIL_PYTEST_REPORT")
  if destination:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_counts, sort_keys=True))
  ran_nothing_else = (_counts.get("skipped")
                      and not _counts.get("passed"))
  if ran_nothing_else and exitstatus in (ExitCode.OK,
                                         ExitCode.NO_TESTS_COLLECTED):
    session.exitstatus = SKIP_EXIT
