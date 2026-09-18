"""`--no-build` means no build: the verb runs against the tree already
in the build dir. `test` gained the switch for a pipeline that builds
in one stage and tests in another off a shared cache dir; `run` has
carried it all along."""
from pathlib import Path

import pytest

from buildutil import packaging
from buildutil.commands import run as runcmd
from buildutil.commands import test as testcmd

PROFILE = "x86_64-linux-gcc-release"
BUILD_SEAMS = ("_ensure_profile", "_conan_install", "_cmake_configure",
               "_cmake_build", "_upload_to_remote")


class _Ctest:
  returncode = 0
  stdout = "100% tests passed, 0 tests failed out of 3\n"
  stderr = ""


def _invoke(*args):
  from typer.testing import CliRunner

  from buildutil.app import app
  return CliRunner().invoke(app, list(args))


def _recorder(calls, name):
  def seam(*args, **kwargs):
    calls.append(name)
  return seam


@pytest.fixture
def calls():
  return []


@pytest.fixture
def project(tmp_path, monkeypatch, calls):
  monkeypatch.setenv("CI", "1")              # keeps the watchdog unarmed
  monkeypatch.chdir(tmp_path)
  monkeypatch.setattr(packaging, "configured", lambda: False)
  for module in (testcmd, runcmd):
    monkeypatch.setattr(module, "_enter", lambda *a, **k: None)
    monkeypatch.setattr(module, "_select_compiler", lambda *a, **k: None)
    monkeypatch.setattr(module, "_warn_dependency_upload_skipped", lambda *a, **k: None)
    for name in BUILD_SEAMS:
      monkeypatch.setattr(module, name, _recorder(calls, name))
  monkeypatch.setattr(testcmd, "last_build_type", lambda: None)
  monkeypatch.setattr(testcmd, "_detect_settings",
                      lambda build_type: {"build_type": build_type})
  monkeypatch.setattr(testcmd, "_profile_name", lambda *a, **k: PROFILE)
  monkeypatch.setattr(testcmd, "_run_pytests",
                      _recorder(calls, "pytest"))

  def fake_ctest(argv, **kwargs):
    calls.append(argv)
    return _Ctest()

  monkeypatch.setattr(testcmd.subprocess, "run", fake_ctest)
  return tmp_path


def _configured_tree(root):
  build_dir = root / "_build" / PROFILE
  build_dir.mkdir(parents=True)
  (build_dir / "CTestTestfile.cmake").write_text("add_test(one true)\n")
  return build_dir


def test_no_build_runs_ctest_against_the_prepared_tree(project, calls):
  _configured_tree(project)
  result = _invoke("test", "--no-build")
  assert result.exit_code == 0, result.output
  assert [c for c in calls if isinstance(c, str)] == ["pytest"]
  ctest = next(c for c in calls if not isinstance(c, str))
  assert ctest[0] == "ctest"
  assert ctest[ctest.index("--test-dir") + 1] == str(Path("_build") / PROFILE)


def test_no_build_on_a_missing_tree_says_so_and_builds_nothing(project,
                                                               calls):
  result = _invoke("test", "--no-build")
  assert result.exit_code == 1
  assert str(Path("_build") / PROFILE) in result.output
  assert "buildutil build" in result.output
  assert calls == []


def test_without_the_switch_test_still_builds(project, calls):
  _configured_tree(project)
  result = _invoke("test", "--no-pytest")
  assert result.exit_code == 0, result.output
  assert [c for c in calls if isinstance(c, str)] == list(BUILD_SEAMS)


def test_run_no_build_execs_the_build_tree_binary(project, calls, monkeypatch):
  binary = project / "_build" / PROFILE / "bin" / "hello"
  binary.parent.mkdir(parents=True)
  binary.write_text("#!/bin/sh\n")
  binary.chmod(0o755)
  execs = []
  monkeypatch.setattr(runcmd.os, "execv",
                      lambda path, argv: execs.append(path))
  result = _invoke("run", "--no-build", "--target", "hello")
  assert result.exit_code == 0, result.output
  assert calls == []
  assert execs == [str(binary.relative_to(project))]


def test_both_verbs_spell_the_switch_the_same_way():
  import inspect
  for module in (testcmd, runcmd):
    assert '"--no-build"' in inspect.getsource(module)
    assert "Skip the incremental build/install step" in (
      inspect.getsource(module))
