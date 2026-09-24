"""ctest runs in parallel unless asked not to.

User, 2026-09-24: "have --parallel switch to --no-parallel and have it
default to parallel"; "leave 0.7 x core count the default for parallel"."""
import math

import pytest

from buildutil import engine
from buildutil.commands import coverage  # noqa: F401  registers the verb
from buildutil.tests.test_no_build_seam import (_configured_tree, _invoke,
                                                calls, project)
from buildutil.tests.test_skip_dependency_upload import _stderr

CORES = 88


@pytest.fixture
def cores(monkeypatch):
  monkeypatch.setattr(engine.os, "cpu_count", lambda: CORES)
  return CORES


def _ctest(calls):
  return next(c for c in calls if isinstance(c, list) and c[0] == "ctest")


@pytest.mark.parametrize("count, jobs", [(88, 61), (10, 7), (2, 1), (1, 1),
                                         (None, 1)])
def test_the_default_is_seven_tenths_of_the_cores_at_least_one(
    monkeypatch, count, jobs):
  monkeypatch.setattr(engine.os, "cpu_count", lambda: count)
  assert engine.parallel_args(False, 0) == ["--parallel", str(jobs)]


def test_jobs_overrides_the_share_and_serial_passes_nothing(cores):
  assert engine.parallel_args(False, 3) == ["--parallel", "3"]
  assert engine.parallel_args(True, 3) == []


def test_a_bare_test_runs_ctest_over_the_core_share(project, calls, cores):
  _configured_tree(project)
  result = _invoke("test", "--no-build", "--no-pytest")
  assert result.exit_code == 0, result.output
  ctest = _ctest(calls)
  assert ctest[ctest.index("--parallel") + 1] == str(math.floor(0.7 * cores))


def test_no_parallel_runs_ctest_serially(project, calls, cores):
  _configured_tree(project)
  result = _invoke("test", "--no-build", "--no-pytest", "--no-parallel")
  assert result.exit_code == 0, result.output
  assert "--parallel" not in _ctest(calls)


@pytest.mark.parametrize("verb", ["test", "coverage"])
def test_the_retired_parallel_is_refused_naming_the_default(project, calls,
                                                            verb):
  result = _invoke(verb, "--parallel")
  assert result.exit_code == 2, result.output
  err = _stderr(result)
  assert f"buildutil {verb}:" in err
  assert "parallel by default" in err and "--no-parallel" in err
  assert calls == []


@pytest.mark.parametrize("verb", ["test", "coverage"])
def test_help_offers_no_parallel_and_hides_the_retired_switch(verb):
  help_text = " ".join(_invoke(verb, "--help").output.replace("│", " ").split())
  assert "--no-parallel" in help_text
  assert "--parallel " not in help_text.replace("--no-parallel", "")
