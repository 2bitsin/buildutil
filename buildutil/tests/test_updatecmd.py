"""`--version` and `update`: pre-project, stdlib-only, and with no source
of its own — plain `pip install -U buildutil` unless one is known."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from buildutil import updatecmd

PKG_PARENT = str(Path(updatecmd.__file__).resolve().parents[1])

INDEX = "https://packages.example.com/api/v4/groups/9/-/packages/pypi/simple"
REPO = "https://github.com/owner/buildutil"


def _cli(args, cwd, env_extra=None):
  env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
         "PYTHONPATH": PKG_PARENT, **(env_extra or {})}
  return subprocess.run([sys.executable, "-m", "buildutil", *args],
                        cwd=cwd, capture_output=True, text=True, env=env)


def test_version_string_names_version_and_path():
  out = updatecmd.version_string()
  assert out.startswith("buildutil ")
  assert str(updatecmd.PKG_DIR) in out
  assert "unknown" not in out


def test_version_needs_no_project(tmp_path):
  cp = _cli(["--version"], tmp_path)
  assert cp.returncode == 0 and cp.stdout.startswith("buildutil "), cp.stderr
  # outside a project there is no deposit to report on — and asking must
  # not go looking for one, let alone fail
  assert "cmake machinery" not in cp.stdout


def test_version_reports_the_deposited_machinery(tmp_path):
  """A deposit is derived data that only a build refreshes, so the two
  versions can legitimately differ — and then the line says so."""
  from buildutil import deposit
  (tmp_path / "buildutil.toml").write_text("[project]\nname = 'acme'\n")
  cfg = {"cmake_option_prefix": "ACME", "module_define_prefix": "ACM"}

  version = updatecmd.package_version()
  assert "not rendered yet" in updatecmd.deposit_status(tmp_path, version)
  deposit.ensure(tmp_path, cfg)
  assert "current" in updatecmd.deposit_status(tmp_path, version)
  stale = updatecmd.deposit_status(tmp_path, "99.9.9")
  assert "STALE" in stale and version in stale
  assert "the next build re-renders it" in stale


def test_an_unstamped_deposit_is_stale_not_missing(tmp_path):
  """Every project upgrading into version stamping has a deposit that
  exists and carries no stamp; calling that "not rendered yet" would be a
  lie about a 40k file sitting right there."""
  from buildutil import deposit
  cfg = {"cmake_option_prefix": "ACME", "module_define_prefix": "ACM"}
  deposit.ensure(tmp_path, cfg)
  (deposit.cmake_dir(tmp_path) / "buildutil.cmake").write_text(
    "# a pre-0.15.0 render, no stamp\n")
  line = updatecmd.deposit_status(tmp_path, updatecmd.package_version())
  assert "STALE" in line and "before version stamping" in line
  assert "not rendered yet" not in line


def test_project_root_walks_up_and_gives_up_quietly(tmp_path):
  nested = tmp_path / "sources" / "deep"
  nested.mkdir(parents=True)
  assert updatecmd.project_root(nested) is None
  (tmp_path / "buildutil.toml").write_text("")
  assert updatecmd.project_root(nested) == tmp_path.resolve()


def test_source_resolution_order():
  resolve = updatecmd.resolve_source
  assert resolve(REPO, INDEX, "git+https://host/o/r.git") == REPO
  assert resolve("", INDEX, "git+https://host/o/r.git") == INDEX
  assert resolve("", "", "git+https://host/o/r.git") == "git+https://host/o/r.git"
  assert resolve("", "", "") == ""


def test_toml_update_source_key(tmp_path, monkeypatch):
  from buildutil import config
  (tmp_path / "buildutil.toml").write_text(
    f'[update]\nsource = "{REPO}"\n')
  monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
  monkeypatch.setattr(config, "HAVE_PROJECT", True)
  assert config._load_project()["update_source"] == REPO
  (tmp_path / "buildutil.toml").write_text("")
  assert config._load_project()["update_source"] == ""


def test_the_project_setting_reaches_the_pip_command(monkeypatch, capsys):
  from buildutil import config
  monkeypatch.setattr(config, "HAVE_PROJECT", True)
  monkeypatch.setattr(config, "PROJECT", {"update_source": INDEX,
                                          "buildutil_version": ""})
  monkeypatch.setattr(config, "BUILD_PID_FILE", Path("/nonexistent"))
  updatecmd.update(["--dry-run"])
  assert f"--index-url {INDEX} buildutil" in capsys.readouterr().out


def test_direct_url_of_a_vcs_install_names_the_repository():
  record = json.dumps({"url": "https://host.example.com/o/buildutil.git",
                       "vcs_info": {"vcs": "git", "commit_id": "abc123",
                                    "requested_revision": "main"}})
  assert updatecmd.origin_from_direct_url(record) == (
    "git+https://host.example.com/o/buildutil.git")


def test_direct_url_of_a_url_install_names_the_url():
  record = json.dumps({"url": "https://host.example.com/buildutil-1.0.zip",
                       "archive_info": {"hashes": {"sha256": "ff"}}})
  assert updatecmd.origin_from_direct_url(record) == (
    "https://host.example.com/buildutil-1.0.zip")


def test_an_index_install_records_no_source():
  # pip writes direct_url.json for direct installs ONLY; a local
  # directory is a checkout, not something to upgrade from
  assert updatecmd.origin_from_direct_url(None) == ""
  assert updatecmd.origin_from_direct_url("") == ""
  assert updatecmd.origin_from_direct_url("<not json>") == ""
  local = json.dumps({"url": "file:///src/buildutil", "dir_info": {}})
  assert updatecmd.origin_from_direct_url(local) == ""


def test_no_source_is_plain_pip():
  assert updatecmd.requirement_args("", "") == ["buildutil"]
  assert updatecmd.requirement_args("", "1.2.3") == ["buildutil==1.2.3"]


def test_an_index_source_is_passed_as_index_url():
  assert updatecmd.requirement_args(INDEX, "") == [
    "--index-url", INDEX, "buildutil"]
  assert updatecmd.requirement_args(INDEX, "1.2.3") == [
    "--index-url", INDEX, "buildutil==1.2.3"]


def test_a_git_source_becomes_a_git_requirement(monkeypatch):
  monkeypatch.setattr(updatecmd, "remote_tags", lambda url: ["v1.2.3"])
  assert updatecmd.requirement_args("git+ssh://git@host/o/r", "") == [
    "git+ssh://git@host/o/r"]
  assert updatecmd.requirement_args(REPO, "") == ["git+" + REPO]
  assert updatecmd.requirement_args("https://host.example.com/o/r.git",
                                    "1.2.3") == [
    "git+https://host.example.com/o/r.git@v1.2.3"]


def test_an_index_on_a_forge_host_is_not_a_repository():
  assert updatecmd.source_kind("https://gitlab.com/api/v4/groups/9/-/"
                               "packages/pypi/simple") == "index"
  assert updatecmd.source_kind("https://gitlab.com/owner/buildutil") == "git"
  assert updatecmd.source_kind("https://packages.example.com/simple") == "index"
  assert updatecmd.source_kind("") == ""


def test_the_pin_takes_whichever_tag_the_remote_carries(monkeypatch):
  """Releases are tagged X.Y.Z in some repositories and vX.Y.Z in others,
  so the remote is asked instead of guessed at."""
  asked = []

  def tags(url, available):
    asked.append(url)
    return available

  monkeypatch.setattr(updatecmd, "remote_tags",
                      lambda url: tags(url, ["0.9.0", "1.2.3"]))
  assert updatecmd.git_ref("git+" + REPO, "1.2.3") == "1.2.3"
  assert asked == [REPO], "ls-remote was asked with pip's git+ prefix"

  monkeypatch.setattr(updatecmd, "remote_tags",
                      lambda url: ["v1.2.2", "v1.2.3"])
  assert updatecmd.git_ref("git+" + REPO, "1.2.3") == "v1.2.3"

  monkeypatch.setattr(updatecmd, "remote_tags", lambda url: ["release-1"])
  with pytest.raises(SystemExit, match="tags neither 1.2.3 nor v1.2.3"):
    updatecmd.git_ref("git+" + REPO, "1.2.3")


def test_remote_tags_reads_ls_remote_output(monkeypatch):
  listing = ("a1\trefs/tags/0.9.0\n"
             "b2\trefs/tags/1.2.3\n"
             "c3\trefs/tags/1.2.3^{}\n")
  monkeypatch.setattr(updatecmd.subprocess, "run",
                      lambda *a, **k: subprocess.CompletedProcess(
                        a[0], 0, listing, ""))
  assert updatecmd.remote_tags("https://host/o/r.git") == ["0.9.0", "1.2.3"]


def test_update_without_a_source_upgrades_from_pips_own_configuration(
    monkeypatch, capsys):
  from buildutil import config
  monkeypatch.setattr(updatecmd, "recorded_origin", lambda: "")
  monkeypatch.setattr(config, "HAVE_PROJECT", False)
  updatecmd.update(["--dry-run"])
  out = capsys.readouterr().out.strip()
  assert out.endswith("-m pip install --upgrade buildutil"), out
  assert "--index-url" not in out


def test_a_recorded_git_origin_is_reused(monkeypatch, capsys):
  from buildutil import config
  monkeypatch.setattr(updatecmd, "recorded_origin", lambda: "git+" + REPO)
  monkeypatch.setattr(config, "HAVE_PROJECT", False)
  updatecmd.update(["--dry-run"])
  assert capsys.readouterr().out.strip().endswith("git+" + REPO)


def test_install_stamps_the_origin_and_update_reads_it(tmp_path, monkeypatch):
  """A vendored copy carries no dist-info, so the stamp install writes is
  the only thing left to tell its update where the copy came from."""
  from buildutil import installcmd
  scratch = tmp_path / "scratch"
  (scratch / "buildutil").mkdir(parents=True)
  (scratch / "buildutil" / "__init__.py").write_text("")
  info = scratch / "buildutil-9.9.9.dist-info"
  info.mkdir()
  (info / "METADATA").write_text("Name: buildutil\nVersion: 9.9.9\n")
  (info / "direct_url.json").write_text(json.dumps(
    {"url": REPO, "vcs_info": {"vcs": "git", "commit_id": "abc"}}))
  root = tmp_path / "proj"
  root.mkdir()

  installcmd.vendor_into(root, source=scratch / "buildutil")
  vendored = root / ".buildutil" / "buildutil"
  assert (vendored / "ORIGIN").read_text().strip() == "git+" + REPO

  monkeypatch.setattr(updatecmd, "PKG_DIR", vendored)
  assert updatecmd.recorded_origin() == "git+" + REPO


def test_pip_runs_isolated_from_pythonpath(tmp_path):
  # -E on every pip/probe call: a PYTHONPATH checkout with a stale
  # egg-info otherwise answers for the target env and pip no-ops
  cp = _cli(["update", "--dry-run", "--source", INDEX], tmp_path)
  assert cp.returncode == 0, cp.stderr
  assert "-E -m pip install" in cp.stdout


def test_update_dry_run_masks_credentials(tmp_path):
  url = INDEX.replace("https://", "https://oauth2:sekrit@")
  cp = _cli(["update", "--dry-run", "--index-url", url, "--pin", "1.2.3"],
            tmp_path)
  assert cp.returncode == 0, cp.stderr
  assert "buildutil==1.2.3" in cp.stdout
  assert "sekrit" not in cp.stdout
  assert "<credentials>" in cp.stdout


def test_update_mentions_no_foreign_deployment(tmp_path):
  """update never advises about infrastructure the reader lacks."""
  cp = _cli(["update", "--dry-run", "--source", INDEX], tmp_path,
            {"MDEV_BUILDUTIL_PIN": "9.9.9"})
  assert cp.returncode == 0, cp.stderr
  assert "MDEV" not in cp.stdout and "reconcile" not in cp.stdout


def test_update_guards_a_live_build(tmp_path, monkeypatch):
  from buildutil import config
  pid_file = tmp_path / "_build" / ".build.pid"
  pid_file.parent.mkdir()
  pid_file.write_text(str(os.getpid()))          # a pid that IS alive
  monkeypatch.setattr(config, "HAVE_PROJECT", True)
  monkeypatch.setattr(config, "BUILD_PID_FILE", pid_file)
  with pytest.raises(SystemExit, match="build is live"):
    updatecmd.update(["--source", INDEX, "--dry-run"])


def test_resolve_pin_precedence():
  from buildutil.updatecmd import resolve_pin
  assert resolve_pin("", "") == ""                    # nothing: latest
  assert resolve_pin("", "latest") == ""              # explicit latest
  assert resolve_pin("", "LATEST") == ""              # case-insensitive
  assert resolve_pin("", "0.9.1") == "0.9.1"          # toml pin applies
  assert resolve_pin("0.8.0", "0.9.1") == "0.8.0"     # flag beats toml


def test_toml_buildutil_version_key(tmp_path, monkeypatch):
  from buildutil import config
  (tmp_path / "buildutil.toml").write_text(
    '[buildutil]\nversion = "0.9.1"\n')
  monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
  monkeypatch.setattr(config, "HAVE_PROJECT", True)
  assert config._load_project()["buildutil_version"] == "0.9.1"
  (tmp_path / "buildutil.toml").write_text("")        # empty toml: default
  assert config._load_project()["buildutil_version"] == ""


def test_pin_floor_refuses_pre_pinning_versions():
  from buildutil.updatecmd import pin_floor_error
  assert pin_floor_error("") is None                  # latest: no floor
  assert pin_floor_error("0.12.0") is None            # the floor itself
  assert pin_floor_error("1.0.0") is None
  err = pin_floor_error("0.9.1")
  assert err and "ping-pong" in err and "0.12.0" in err
