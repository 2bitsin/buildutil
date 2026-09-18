"""`--version` and `update`: pre-project, stdlib-only, and never
public PyPI — 'buildutil' is a guessable name upstream, so an upgrade
that falls through to pypi.org is a supply-chain door (the same rule
the deployment's own upgrade step enforces)."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from buildutil import updatecmd

PKG_PARENT = str(Path(updatecmd.__file__).resolve().parents[1])


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
  """The second question --version answers: not 'which buildutil is
  installed' but 'which one rendered the cmake this project builds with'.
  A deposit is derived data that only a build refreshes, so the two can
  legitimately differ — and then the line says so."""
  from buildutil import deposit
  (tmp_path / "buildutil.toml").write_text("[project]\nname = 'acme'\n")
  cfg = {"cmake_option_prefix": "ACME", "module_define_prefix": "ACM"}

  version = updatecmd.package_version()
  # nothing rendered yet
  assert "not rendered yet" in updatecmd.deposit_status(tmp_path, version)
  deposit.ensure(tmp_path, cfg)
  assert "current" in updatecmd.deposit_status(tmp_path, version)
  # a deposit left behind by an older buildutil names itself, loudly
  stale = updatecmd.deposit_status(tmp_path, "99.9.9")
  assert "STALE" in stale and version in stale
  assert "the next build re-renders it" in stale


def test_an_unstamped_deposit_is_stale_not_missing(tmp_path):
  """Every project upgrading into version stamping has a deposit that
  exists and carries no stamp. Calling that "not rendered yet" would be a
  lie about a 40k file sitting right there — it is stale, and the next
  build fixes it."""
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


def test_pick_index_prefers_flag_then_env_then_pip_config():
  assert updatecmd.pick_index("https://x/simple", {}, "") == "https://x/simple"
  assert updatecmd.pick_index(
    None, {"BUILDUTIL_INDEX": "https://e/simple"}, "") == "https://e/simple"
  cfg = ":env:.extra-index-url='https://gitlab.example/api/v4/groups/9/-/packages/pypi/simple'"
  assert "gitlab.example" in updatecmd.pick_index(None, {}, cfg)


def test_public_pypi_never_picked():
  cfg = "global.index-url='https://pypi.org/simple'"
  assert updatecmd.pick_index(None, {}, cfg) is None
  env = {"PIP_INDEX_URL": "https://pypi.org/simple"}
  assert updatecmd.pick_index(None, env, "") is None


def test_pip_config_multi_url_value():
  """What `pip config list` ACTUALLY emits for a multi-line ini value:
  repr-escaped — whitespace as literal \\n / \\t two-char sequences
  inside the quotes. The earlier version of this test modeled a real
  newline, which pip never prints, and that wrong model is exactly how
  the bug shipped: the literal escape glued itself onto the first URL's
  scheme, pip rejected the index and echoed it credential-unmasked."""
  cfg = (r"global.extra-index-url='https://pypi.org/simple\n"
         r"https://private.example/simple'")
  assert updatecmd.parse_pip_config(cfg) == [
    "https://pypi.org/simple", "https://private.example/simple"]
  assert updatecmd.pick_index(None, {}, cfg) == "https://private.example/simple"


def test_pip_config_single_url_on_a_continuation_line():
  """The shape verbatim, as a provisioning script writes it: one URL
  on an indented continuation line — value starts with a
  literal \\n escape that must NOT reach the URL."""
  cfg = (r"global.extra-index-url='\nhttps://oauth2:token@gitlab.example"
         r"/api/v4/groups/80/-/packages/pypi/simple'")
  assert updatecmd.parse_pip_config(cfg) == [
    "https://oauth2:token@gitlab.example"
    "/api/v4/groups/80/-/packages/pypi/simple"]


def test_pip_config_real_whitespace_still_splits():
  # defensive: if pip ever prints raw whitespace, splitting must hold
  cfg = ("global.extra-index-url='https://a.example/simple "
         "https://b.example/simple'")
  assert updatecmd.parse_pip_config(cfg) == [
    "https://a.example/simple", "https://b.example/simple"]


def test_update_refuses_without_private_index(tmp_path):
  cp = _cli(["update"], tmp_path,
            {"PIP_CONFIG_FILE": os.devnull})  # hide any host pip.conf
  assert cp.returncode != 0
  assert "Refusing" in cp.stderr and "public PyPI" in cp.stderr


def test_pip_runs_isolated_from_pythonpath(tmp_path):
  # -E on every pip/probe call: a PYTHONPATH checkout with a stale
  # egg-info otherwise answers for the target env and pip no-ops
  url = "https://x/simple"
  cp = _cli(["update", "--dry-run", "--index-url", url], tmp_path,
            {"PIP_CONFIG_FILE": os.devnull})
  assert cp.returncode == 0, cp.stderr
  assert "-E -m pip install" in cp.stdout


def test_update_dry_run_masks_credentials(tmp_path):
  url = "https://oauth2:sekrit@gitlab.example/api/v4/groups/9/-/packages/pypi/simple"
  cp = _cli(["update", "--dry-run", "--index-url", url, "--pin", "1.2.3"],
            tmp_path, {"PIP_CONFIG_FILE": os.devnull})
  assert cp.returncode == 0, cp.stderr
  assert "buildutil==1.2.3" in cp.stdout
  assert "sekrit" not in cp.stdout
  assert "<credentials>" in cp.stdout


def test_update_guards_a_live_build(tmp_path, monkeypatch):
  from buildutil import config
  pid_file = tmp_path / "_build" / ".build.pid"
  pid_file.parent.mkdir()
  pid_file.write_text(str(os.getpid()))          # a pid that IS alive
  monkeypatch.setattr(config, "HAVE_PROJECT", True)
  monkeypatch.setattr(config, "BUILD_PID_FILE", pid_file)
  with pytest.raises(SystemExit, match="build is live"):
    updatecmd.update(["--index-url", "https://x/simple", "--dry-run"])


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
