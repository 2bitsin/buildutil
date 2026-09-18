"""`buildutil setup-skill` — the agent skill travels with the project.

The contract: a skill teaching agents the CLI + directory conventions,
installed for a chosen agent (--agent-type) or for every agent whose
dot directory exists at the project root; init and setup install it
automatically unless --no-agents; --zip writes a portable copy.
"""
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from buildutil import skillcmd

PKG = Path(skillcmd.__file__).resolve().parent


def test_the_skill_exists_and_fronts_its_name():
  text = (skillcmd.SKILL_DIR / "SKILL.md").read_text()
  assert text.startswith("---\n")
  assert "name: buildutil" in text.split("---")[1]
  # the guards and the skill teach the same rule
  assert "Never invoke cmake" in text
  # the mission statement leads, and the deep contracts ride along
  assert "Build logic is expressed by directory structure" in text
  assert (skillcmd.SKILL_DIR / "reference.md").is_file()


def test_the_skill_teaches_what_the_machinery_actually_does():
  """Spot-check the load-bearing claims against reality markers so the
  docs can't silently rot: every special suffix the skill names must
  appear in buildutil.cmake, and the configure API names must exist."""
  machinery = (Path(skillcmd.__file__).parent / "templates" / "cmake" /
               "base" / "buildutil.cmake").read_text()
  for marker in (".install", ".patch", ".pybind.cpp", ".test", ".bench"):
    assert marker in machinery, marker
  api = (Path(skillcmd.__file__).parent / "pysupport" /
         "buildutil_configure.py").read_text()
  reference = (skillcmd.SKILL_DIR / "reference.md").read_text()
  for fn in ("def emit", "def declare", "def depends", "def output_dir"):
    assert fn in api, fn
    assert fn.removeprefix("def ") + "(" in reference, fn


def test_install_ships_the_reference_too(tmp_path):
  skillcmd.install_for(tmp_path, "claude")
  base = tmp_path / ".claude" / "skills" / "buildutil"
  assert (base / "SKILL.md").is_file()
  assert (base / "reference.md").is_file()


def test_explicit_agent_type_installs_there(tmp_path):
  skillcmd.install_for(tmp_path, "claude")
  installed = tmp_path / ".claude" / "skills" / "buildutil" / "SKILL.md"
  assert installed.is_file()
  assert "name: buildutil" in installed.read_text()


def test_autodetect_installs_for_every_present_agent(tmp_path):
  (tmp_path / ".claude").mkdir()
  (tmp_path / ".qwen").mkdir()
  installed = skillcmd.auto_install(tmp_path)
  assert sorted(installed) == ["claude", "qwen"]
  assert (tmp_path / ".claude" / "skills" / "buildutil" / "SKILL.md").is_file()
  assert (tmp_path / ".qwen" / "skills" / "buildutil" / "SKILL.md").is_file()
  assert not (tmp_path / ".codex").exists()


def test_no_agent_and_no_flag_is_a_clear_refusal(tmp_path, monkeypatch):
  monkeypatch.chdir(tmp_path)
  with pytest.raises(SystemExit) as e:
    skillcmd.main([])
  assert "--agent-type" in str(e.value)


def test_zip_holds_the_skill(tmp_path):
  out = skillcmd.write_zip(tmp_path / "skill.zip")
  with zipfile.ZipFile(out) as z:
    assert "buildutil/SKILL.md" in z.namelist()
    assert "name: buildutil" in z.read("buildutil/SKILL.md").decode()


def test_init_auto_installs_for_detected_agents(tmp_path, monkeypatch):
  from buildutil import initcmd
  monkeypatch.chdir(tmp_path)
  (tmp_path / ".claude").mkdir()
  initcmd.main(["--name", "acme", "--bare"])
  assert (tmp_path / ".claude" / "skills" / "buildutil" / "SKILL.md").is_file()


def test_init_no_agents_skips_it(tmp_path, monkeypatch):
  from buildutil import initcmd
  monkeypatch.chdir(tmp_path)
  (tmp_path / ".claude").mkdir()
  initcmd.main(["--name", "acme", "--bare", "--no-agents"])
  assert not (tmp_path / ".claude" / "skills").exists()


def test_setup_skill_cli_runs_pre_venv(tmp_path):
  """The subcommand is intercepted before any project/venv exists —
  same stdlib-only lane as init/install."""
  (tmp_path / ".codex").mkdir()
  proc = subprocess.run(
    [sys.executable, "-m", "buildutil", "setup-skill"],
    cwd=tmp_path, capture_output=True, text=True,
    env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
         "PYTHONPATH": str(PKG.parent)})
  assert proc.returncode == 0, proc.stdout + proc.stderr
  assert (tmp_path / ".codex" / "skills" / "buildutil" / "SKILL.md").is_file()
