"""`--no-build` means no build: the verb runs against the tree already
in the build dir. `test` gained the switch for a pipeline that builds
in one stage and tests in another off a shared cache dir; `run` has
carried it all along."""
import os
from pathlib import Path

import pytest

from buildutil import engine, packaging
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


@pytest.mark.parametrize("flags,build_type,origin", [
  ([], "Release", "default"),
  (["--debug"], "Debug", "--debug"),
  (["--release"], "Release", "--release"),
  (["--relwithdebinfo"], "RelWithDebInfo", "--relwithdebinfo"),
  (["--release", "--debug"], "RelWithDebInfo", "--release --debug"),
])
def test_profile_line_names_its_origin(project, monkeypatch, flags,
                                      build_type, origin):
  monkeypatch.setattr(testcmd, "_profile_name",
                      lambda settings: "x86_64-linux-gcc-" +
                      settings["build_type"].lower())
  monkeypatch.setattr(engine._project_options, "profile_note",
                      lambda: " options: contracts=off")
  profile = "x86_64-linux-gcc-" + build_type.lower()
  build_dir = project / "_build" / profile
  build_dir.mkdir(parents=True)
  (build_dir / "CTestTestfile.cmake").write_text("add_test(one true)\n")
  result = _invoke("test", "--no-build", *flags)
  assert result.exit_code == 0, result.output
  assert (f"profile: {profile} ({origin}) options: contracts=off"
          in result.output.splitlines())


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


@pytest.mark.parametrize("fails", [False, True])
def test_smoke_uses_isolated_reference_and_always_removes(project, calls,
                                                        monkeypatch, fails):
  import subprocess
  monkeypatch.setattr(packaging, "configured", lambda: True)
  monkeypatch.setattr(packaging, "has_package_test", lambda: True)
  monkeypatch.setattr(packaging, "package_name", lambda: "own")
  monkeypatch.setattr(packaging, "resolve_version", lambda **k: ("1.0", None))
  monkeypatch.setattr(packaging, "shared_requested", lambda: False)
  monkeypatch.setattr(testcmd, "_host_build_profiles",
                      lambda p: (Path("host"), Path("build")))

  def invoke(argv):
    calls.append(argv)
    if fails and argv[:2] == ["conan", "test"]:
      raise subprocess.CalledProcessError(1, argv)

  export, test = packaging.export_pkg, packaging.run_package_test
  monkeypatch.setattr(packaging, "export_pkg",
                      lambda *a, **k: export(*a, **k, run=invoke))
  monkeypatch.setattr(packaging, "run_package_test",
                      lambda *a, **k: test(*a, **k, run=invoke))
  monkeypatch.setattr(testcmd.subprocess, "check_call", invoke)
  result = _invoke("test", "--no-pytest")
  assert result.exit_code == int(fails), result.output
  conan = [c for c in calls if isinstance(c, list) and c[0] == "conan"]
  assert conan[0][conan[0].index("--user"):][:4] == [
    "--user", "buildutil", "--channel", "smoke"]
  assert conan[1][3] == "own/1.0@buildutil/smoke"
  assert conan[2] == ["conan", "remove", "own/1.0@buildutil/smoke", "-c"]


@pytest.mark.parametrize("mode", [[], ["--no-build"], ["--no-native"]])
def test_python_lane_receives_the_active_build_environment(project, monkeypatch,
                                                          mode):
  build = _configured_tree(project)
  suite = project / "tools" / "probe"
  suite.mkdir(parents=True)
  (suite / "pytest.ini").write_text("[pytest]\n")
  monkeypatch.setattr(engine, "HERE", project)
  monkeypatch.setitem(engine.config.PROJECT, "test_python_suites", [])
  monkeypatch.setattr(testcmd, "_run_pytests", engine._run_pytests)
  monkeypatch.setenv("BUILDUTIL_BUILD_DIR", "/stale/build")
  monkeypatch.setenv("BUILDUTIL_PROFILE", "stale-profile")
  monkeypatch.setenv("LANE_SENTINEL", "preserved")
  original = dict(os.environ)
  environments = []

  def run(argv, **kwargs):
    if argv[0] != "ctest":
      environments.append(kwargs["env"])
    return _Ctest()

  monkeypatch.setattr(testcmd.subprocess, "run", run)
  result = _invoke("test", *mode)
  assert result.exit_code == 0, result.output
  env, = environments
  assert env["LANE_SENTINEL"] == "preserved"
  if "--no-native" in mode:
    assert "BUILDUTIL_BUILD_DIR" not in env
    assert "BUILDUTIL_PROFILE" not in env
    assert env["PATH"] == original["PATH"]
  else:
    assert env["BUILDUTIL_BUILD_DIR"] == str(build)
    assert env["BUILDUTIL_PROFILE"] == PROFILE
    assert env["PATH"] == str(build / "bin") + os.pathsep + original["PATH"]
  lane = ("BUILDUTIL_BUILD_DIR", "BUILDUTIL_PROFILE", "PATH", "LANE_SENTINEL")
  assert [os.environ.get(key) for key in lane] == [original.get(key) for key in lane]


@pytest.mark.parametrize("labels, args, expected", [
  ([], [], []),
  ([], ["--label-exclude", "slow.*", "--label-exclude", "^flaky$"],
   ["-LE", "slow.*", "-LE", "^flaky$"]),
  (["measurement"], [], ["-LE", "^(measurement)$"]),
  (["measurement", "slow.case"], [], ["-LE", r"^(measurement|slow\.case)$"]),
  (["measurement"], ["measurement"], ["-L", "^(measurement)$"]),
  (["measurement"], ["--label-exclude", "flaky"], ["-LE", "flaky"]),
  (["measurement"], ["measurement", "--label-exclude", "flaky"],
   ["-L", "^(measurement)$", "-LE", "flaky"]),
  (["measurement"], ["--filter", "Case.*"],
   ["-R", "Case.*", "-LE", "^(measurement)$"]),
])
def test_ctest_label_exclusions(project, calls, monkeypatch, labels, args, expected):
  monkeypatch.setitem(testcmd.config.PROJECT, "test_exclude_labels", labels)
  _configured_tree(project)
  result = _invoke("test", "--no-build", "--no-parallel", *args)
  assert result.exit_code == 0, result.output
  ctest = next(c for c in calls if not isinstance(c, str))
  assert ctest == [
    "ctest", "--test-dir", str(Path("_build") / PROFILE),
    "--output-on-failure", "--no-tests=error", "--timeout", "60.0", *expected,
  ]
