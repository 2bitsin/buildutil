"""The venv console script: written from the running copy, no pip, no
index. The old path ran `pip install --no-index buildutil` inside a
venv that never carries the package — guaranteed 'No matching
distribution found' — and its fallback script died on import when
invoked directly (the package lives OUTSIDE the venv)."""
import os
from pathlib import Path

import pytest

from buildutil import bootstrap, config

PKG_PARENT = str(Path(bootstrap.__file__).resolve().parents[1])


@pytest.fixture
def venv_bin(tmp_path, monkeypatch):
  bin_dir = tmp_path / "_pyvenv" / "bin"
  bin_dir.mkdir(parents=True)
  monkeypatch.setattr(config, "VENV_BIN_DIR", bin_dir)
  monkeypatch.setattr(config, "VENV_PY", bin_dir / "python")
  return bin_dir


def test_script_pins_running_copy_on_sys_path(venv_bin):
  bootstrap.install_venv_symlinks()
  script = venv_bin / "buildutil"
  text = script.read_text()
  assert text.startswith(f"#!{venv_bin / 'python'}\n")
  assert f"sys.path.insert(0, {PKG_PARENT!r})" in text
  assert "from buildutil.__main__ import main" in text
  assert os.access(script, os.X_OK)
  # and the injected path really is where the running package resolves
  assert (Path(PKG_PARENT) / "buildutil" / "__main__.py").is_file()


def test_no_pip_involved(venv_bin):
  # the venv has no python binary at all — a pip invocation would blow
  # up; writing the script must not need one
  bootstrap.install_venv_symlinks()
  assert (venv_bin / "buildutil").is_file()


def test_stale_script_heals(venv_bin):
  script = venv_bin / "buildutil"
  script.write_text("#!/gone/python\nfrom buildutil.__main__ import main\n")
  bootstrap.install_venv_symlinks()
  assert "sys.path.insert" in script.read_text()


def test_legacy_shim_symlink_removed(venv_bin, tmp_path):
  script = venv_bin / "buildutil"
  script.symlink_to(tmp_path / "nonexistent-shim")
  bootstrap.install_venv_symlinks()
  assert not script.is_symlink() and script.is_file()
  assert "sys.path.insert" in script.read_text()


def test_current_script_left_untouched(venv_bin):
  bootstrap.install_venv_symlinks()
  script = venv_bin / "buildutil"
  before = script.stat().st_mtime_ns
  bootstrap.install_venv_symlinks()
  assert script.stat().st_mtime_ns == before
