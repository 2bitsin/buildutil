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
  """Every configuration-shaped step publish takes, in order, with the
  heavy seams silenced."""
  from buildutil.commands import publish as pub
  seen = {"configured": [], "built": [], "exported": [], "tested": [],
          "uploaded": [], "committed": 0}

  def commit():
    seen["committed"] += 1

  for name in ("_enter", "_select_compiler", "_conan_install",
               "_upload_to_remote", "_status"):
    monkeypatch.setattr(pub, name, lambda *a, **k: None)
  monkeypatch.setattr(pub, "_detect_settings", lambda bt: {"bt": bt})
  monkeypatch.setattr(pub, "_ensure_profile", lambda s: Path(s["bt"]))
  monkeypatch.setattr(pub, "_profile_name", lambda s: f"x-{s['bt']}".lower())
  monkeypatch.setattr(pub, "_host_build_profiles", lambda p: (p, p))
  monkeypatch.setattr(pub, "_cmake_configure",
                      lambda d, bt, **k: seen["configured"].append(bt))
  monkeypatch.setattr(pub, "_cmake_build",
                      lambda d, *a, **k: seen["built"].append(d.name))
  monkeypatch.setattr(pub, "REPO_ROOT", tmp_path)
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
                      lambda v: seen["uploaded"].append(v))
  monkeypatch.setattr(packaging, "ref", lambda v: f"s/{v}")
  monkeypatch.setenv("CONAN_REMOTE_URL", "https://repo.example/conan")
  return seen


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


def test_the_package_test_runs_against_each_configuration(record):
  _publish()
  assert record["tested"] == ["Release", "Debug"]


def test_one_version_and_one_build_number_cover_both(record):
  _publish()
  assert record["uploaded"] == ["1.2.3.4"]
  assert record["committed"] == 1


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
