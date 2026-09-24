"""`publish` covers both configurations.

A package published Release-only cannot be resolved by a consumer whose
default profile is Debug: conan computes a different package_id, finds
no binary, and the consumer ends in the recipe's source-build refusal.
So one publish builds, exports and tests BOTH build types under one
version and one build number, and `--release` / `--debug` narrow it to
the single configuration the caller asked for.
"""
from pathlib import Path

import pytest

from buildutil import packaging


@pytest.fixture
def record(monkeypatch, tmp_path):
  from buildutil.commands import publish as pub
  seen = {"configured": [], "packaged_as": [], "built": [], "exported": [], "tested": [],
          "uploaded": [], "graphs": [], "committed": 0}

  def commit():
    seen["committed"] += 1

  for name in ("_enter", "_select_compiler", "_conan_install",
               "_upload_to_remote", "_status"):
    monkeypatch.setattr(pub, name, lambda *a, **k: None)
  monkeypatch.setattr(pub, "_upload_to_remote",
                      lambda graphs: seen["graphs"].extend(graphs))
  monkeypatch.setattr(pub, "_detect_settings", lambda bt: {"bt": bt})
  monkeypatch.setattr(pub, "_ensure_profile", lambda s: Path(s["bt"]))
  monkeypatch.setattr(pub, "_profile_name", lambda s: f"x-{s['bt']}".lower())
  monkeypatch.setattr(pub, "_host_build_profiles", lambda p: (p, p))
  monkeypatch.setattr(pub, "_cmake_configure",
                      lambda d, bt, **k: (seen["configured"].append(bt),
                                          seen["packaged_as"].append(k["package"])))
  monkeypatch.setattr(pub, "_cmake_build",
                      lambda d, *a, **k: seen["built"].append(d.name))
  monkeypatch.setattr(pub, "REPO_ROOT", tmp_path)
  _record_packaging(monkeypatch, seen, commit)
  return seen


def _record_packaging(monkeypatch, seen, commit):
  seen["upload"] = packaging.upload
  monkeypatch.setattr(packaging, "configured", lambda: True)
  monkeypatch.setattr(packaging, "ranged_runtime_requires", lambda: [])
  monkeypatch.setattr(packaging, "resolve_version",
                      lambda bump, override="": ("1.2.3.4", commit))
  monkeypatch.setattr(packaging, "shared_requested", lambda: False)
  monkeypatch.setattr(packaging, "has_package_test", lambda: True)
  monkeypatch.setattr(packaging, "export_pkg",
                      lambda v, p, b, shared=False:
                      seen["exported"].append(str(p)))
  monkeypatch.setattr(packaging, "run_package_test",
                      lambda v, p, b, shared=False:
                      seen["tested"].append(str(p)))
  monkeypatch.setattr(packaging, "upload",
                      lambda v, target_os: seen["uploaded"].append(v))
  monkeypatch.setattr(packaging, "ref", lambda v: f"s/{v}")
  monkeypatch.setenv("CONAN_REMOTE_URL", "https://repo.example/conan")


def _publish(**overrides):
  from buildutil.commands import publish as pub
  kwargs = dict(release=None, version="1.2.3.4", no_autoincrement=False,
                no_upload=False, allow_version_ranges=False,
                bake_buildutil=False, conan_home=None, compiler="auto")
  kwargs.update(overrides)
  pub.publish(**kwargs)


def test_both_configurations_are_published_by_default(record):
  _publish()
  assert record["configured"] == ["Release", "Debug"]
  assert record["built"] == ["x-release", "x-debug"]
  assert record["exported"] == ["Release", "Debug"]


def test_each_configuration_is_built_as_the_published_version(record):
  """_Public_() exports at the major of the version the package ships as."""
  _publish()
  assert record["packaged_as"] == ["1.2.3.4", "1.2.3.4"]


def test_the_package_test_runs_against_each_configuration(record):
  _publish()
  assert record["tested"] == ["Release", "Debug"]


def test_one_version_and_one_build_number_cover_both(record):
  _publish()
  assert record["uploaded"] == ["1.2.3.4"]
  assert record["committed"] == 1
  assert record["graphs"] == [Path("_build") / f"x-{bt}" / "conan-graph.json"
                              for bt in ("release", "debug")]


def test_release_alone_when_asked(record, capsys):
  _publish(release=True)
  assert record["exported"] == ["Release"]
  out = capsys.readouterr().out
  assert "Debug consumers" in out and "--debug" in out


def test_debug_alone_when_asked(record, capsys):
  _publish(release=False)
  assert record["exported"] == ["Debug"]
  assert "Release consumers" in capsys.readouterr().out


def test_covering_both_prints_no_missing_configuration_note(record, capsys):
  _publish()
  assert "consumers of" not in capsys.readouterr().out


def test_a_dry_run_covers_both_and_consumes_nothing(record):
  _publish(no_upload=True)
  assert record["exported"] == ["Release", "Debug"]
  assert record["uploaded"] == [] and record["committed"] == 0


def test_publish_uploads_only_current_reference_and_dependencies(record,
                                                                monkeypatch):
  import json
  from types import SimpleNamespace
  from buildutil import bootstrap, engine
  from buildutil.commands import publish as pub
  uploads = []
  monkeypatch.setattr(pub, "_upload_to_remote", engine._upload_to_remote)
  monkeypatch.setattr(packaging, "package_name", lambda: "s")
  monkeypatch.setattr(bootstrap, "upload_target", lambda: ("site", ""))
  monkeypatch.setattr(engine.subprocess, "run",
                      lambda *a, **k: SimpleNamespace(stdout="site:"))

  def invoke(argv):
    if argv[1] == "list":
      output = next(a.split("=", 1)[1] for a in argv if a.startswith("--out-file="))
      graph = next(a for a in argv if a.startswith("--graph="))
      package_id = "debug" if "x-debug" in graph else "release"
      data = {"s/1.2.3.3": {}, "s/1.2.3.4": {}, "dep/1": {
        "revisions": {"r": {"packages": {package_id: {}}}}}}
      Path(output).write_text(json.dumps({"Local Cache": data}))
    elif argv[2] == "--list":
      uploads.append(json.loads(Path(argv[3]).read_text())["Local Cache"])
    else:
      uploads.append(argv[2])

  monkeypatch.setattr(engine.subprocess, "check_call", invoke)
  monkeypatch.setattr(packaging, "upload",
                      lambda v, target_os: record["upload"](v, target_os,
                                                            run=invoke))
  _publish()
  assert uploads[0] == "s/1.2.3.4"
  assert set(uploads[1]) == {"dep/1"}
  assert set(uploads[1]["dep/1"]["revisions"]["r"]["packages"]) == {"release", "debug"}


@pytest.mark.parametrize("flags", [["--relwithdebinfo"], ["--release", "--debug"]])
def test_publish_relwithdebinfo_cli(record, flags):
  from typer.testing import CliRunner
  from buildutil.app import app
  result = CliRunner().invoke(app, ["publish", *flags, "--no-upload"])
  assert result.exit_code == 0, result.output
  assert record["configured"] == ["RelWithDebInfo"]
  assert record["built"] == ["x-relwithdebinfo"]


@pytest.mark.parametrize("flag", ["--release", "--debug"])
def test_publish_conflicting_profiles_are_usage_errors(record, flag):
  from typer.testing import CliRunner
  from buildutil.app import app
  result = CliRunner().invoke(app, ["publish", "--relwithdebinfo", flag])
  assert result.exit_code == 2, result.output
  assert "cannot be combined" in result.output
  assert not record["configured"]
