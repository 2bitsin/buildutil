"""Owner ruling 2026-08-19: version ranges resolve against the
REMOTE by default — every conan install passes `--update`, so "publisher
says fixed in X.Y.Z and my floor covers it" just works and a stale
cached satisfier can never masquerade as a broken fix. The motto: the
common situation is the default; the exception gets the switch — the
global `--no-conan-update` (before the subcommand) skips the --update
for offline work or a deliberately frozen cache.

(This replaces the earlier failure-gated retry design: the site
registry is a network-local caching proxy, so remote reachability is
not a cost worth designing around.)"""
import os
import subprocess
from pathlib import Path

import pytest

from buildutil import engine


def _record_install(monkeypatch):
  calls = []
  monkeypatch.setattr(engine.subprocess, "check_call",
                      lambda argv, env=None: calls.append(argv))
  monkeypatch.setattr(engine, "_host_build_profiles",
                      lambda profile: (profile, profile))
  monkeypatch.delenv("BUILDUTIL_NO_CONAN_UPDATE", raising=False)
  return calls


def test_install_passes_update_by_default(monkeypatch, tmp_path):
  calls = _record_install(monkeypatch)
  engine._conan_install(tmp_path / "profile", tmp_path / "build")
  assert len(calls) == 1
  assert "--update" in calls[0]
  assert "--build=missing" in calls[0]
  assert "--format=json" in calls[0]
  assert f"--out-file={tmp_path / 'build/conan-graph.json'}" in calls[0]


def test_the_escape_hatch_env_keeps_the_resolve_local(monkeypatch,
                                                      tmp_path):
  calls = _record_install(monkeypatch)
  monkeypatch.setenv("BUILDUTIL_NO_CONAN_UPDATE", "1")
  engine._conan_install(tmp_path / "profile", tmp_path / "build")
  assert len(calls) == 1
  assert "--update" not in calls[0]


def test_the_global_switch_sets_the_env_for_the_run(monkeypatch):
  """--no-conan-update is a ROOT option (before the subcommand): the
  callback exports the env the install seam reads."""
  monkeypatch.setenv("CI", "1")               # keeps the watchdog unarmed
  monkeypatch.delenv("BUILDUTIL_NO_CONAN_UPDATE", raising=False)
  from typer.testing import CliRunner
  from buildutil import app as appmod
  seen = {}

  def probe():
    seen["value"] = os.environ.get("BUILDUTIL_NO_CONAN_UPDATE")

  appmod.app.command("probe-conan-update")(probe)
  try:
    result = CliRunner().invoke(
      appmod.app, ["--no-conan-update", "probe-conan-update"])
    assert result.exit_code == 0, result.output
    assert seen["value"] == "1"
  finally:
    appmod.app.registered_commands = [
      c for c in appmod.app.registered_commands if c.callback is not probe]
    os.environ.pop("BUILDUTIL_NO_CONAN_UPDATE", None)


def test_without_the_switch_the_env_stays_unset(monkeypatch):
  monkeypatch.setenv("CI", "1")
  monkeypatch.delenv("BUILDUTIL_NO_CONAN_UPDATE", raising=False)
  from typer.testing import CliRunner
  from buildutil import app as appmod
  seen = {}

  def probe():
    seen["value"] = os.environ.get("BUILDUTIL_NO_CONAN_UPDATE")

  appmod.app.command("probe-conan-update-2")(probe)
  try:
    result = CliRunner().invoke(appmod.app, ["probe-conan-update-2"])
    assert result.exit_code == 0, result.output
    assert seen["value"] is None
  finally:
    appmod.app.registered_commands = [
      c for c in appmod.app.registered_commands if c.callback is not probe]
    os.environ.pop("BUILDUTIL_NO_CONAN_UPDATE", None)
