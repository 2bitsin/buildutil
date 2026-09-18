"""BUILDUTIL_SYSTEM=1 means the running interpreter IS the toolchain.

Both consequences below came from a consumer's CI, where the toolchain images
set the variable and never grow a project venv: anything aimed at "the
venv python" must name THIS python (the reflect scan execed a _pyvenv
that did not exist), and a buildutil.toml declaration -- [venv]
extra_deps, or a declared reflect extension's clang bindings -- has no
project venv to land in, so ensure() installs it here instead.
"""
import importlib.metadata
import os
import subprocess
import sys
import types
import zipfile
from pathlib import Path

import pytest

from buildutil import bootstrap, config


def test_system_mode_points_venv_py_at_the_running_interpreter():
  # config decides at import, so ask a fresh interpreter with the variable
  # set rather than reloading the module under everybody else's feet
  out = subprocess.run(
    [sys.executable, "-c",
     "import buildutil.config as c; print(c.VENV_PY)"],
    capture_output=True, text=True, check=True,
    env={**__import__("os").environ, "BUILDUTIL_SYSTEM": "1"})
  assert out.stdout.strip() == sys.executable


def test_without_system_mode_venv_py_stays_the_project_venv():
  env = {k: v for k, v in __import__("os").environ.items()
         if k != "BUILDUTIL_SYSTEM"}
  out = subprocess.run(
    [sys.executable, "-c",
     "import buildutil.config as c; print(c.VENV_PY)"],
    capture_output=True, text=True, check=True, env=env)
  assert out.stdout.strip().endswith("_pyvenv/bin/python") \
      or "_pyvenv" in out.stdout


def _ensure_under_system_mode(monkeypatch, extensions, probe_rc):
  """Run ensure() in system mode with a stubbed probe; report pip installs."""
  installed = []
  monkeypatch.setenv("BUILDUTIL_SYSTEM", "1")
  monkeypatch.setattr(config, "PROJECT",
                      {**config.PROJECT, "cmake_extensions": extensions})
  monkeypatch.setattr(bootstrap.subprocess, "run",
                      lambda *a, **k: types.SimpleNamespace(
                        returncode=probe_rc))
  monkeypatch.setattr(bootstrap.os, "access", lambda *a, **k: True)
  monkeypatch.setattr(bootstrap, "_run", lambda cmd: installed.append(cmd))
  bootstrap.ensure()
  return installed


def test_system_mode_heals_missing_clang_bindings(monkeypatch):
  installed = _ensure_under_system_mode(monkeypatch, ["reflect"], probe_rc=1)
  assert len(installed) == 1
  assert all(dep in installed[0] for dep in config.REFLECT_DEPS)


def test_system_mode_leaves_present_bindings_alone(monkeypatch):
  assert _ensure_under_system_mode(monkeypatch, ["reflect"], probe_rc=0) == []


def test_system_mode_without_reflect_installs_nothing(monkeypatch):
  # the base deps are the image's promise, not ensure()'s to re-check
  assert _ensure_under_system_mode(monkeypatch, [], probe_rc=1) == []


def _wheel(directory, name, version):
  """A minimal pure-python wheel, so the test needs no index."""
  dist = f"{name}-{version}"
  path = directory / f"{dist}-py3-none-any.whl"
  with zipfile.ZipFile(path, "w") as z:
    z.writestr(f"{name}/__init__.py", "VALUE = 1\n")
    z.writestr(f"{dist}.dist-info/METADATA",
               f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n\n")
    z.writestr(f"{dist}.dist-info/WHEEL", "Wheel-Version: 1.0\n"
               "Generator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
    z.writestr(f"{dist}.dist-info/RECORD", "")
  return path


def _project(tmp_path, extra_deps):
  root = tmp_path / "proj"
  root.mkdir()
  deps = ", ".join(f'"{d}"' for d in extra_deps)
  (root / "buildutil.toml").write_text(
    f'[project]\nname = "demo"\n\n[venv]\nextra_deps = [{deps}]\n')
  return root


def _ensure_in_venv(tmp_path, extra_deps):
  """ensure() under a throwaway venv in system mode; returns that python."""
  venv = tmp_path / "venv"
  subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
  py = venv / "bin" / ("python.exe" if os.name == "nt" else "python")
  wheels = tmp_path / "wheels"
  wheels.mkdir()
  _wheel(wheels, "bdudemo", "1.0.0")
  proc = subprocess.run(
    [str(py), "-c", "from buildutil import bootstrap; bootstrap.ensure()"],
    cwd=str(_project(tmp_path, extra_deps)),
    capture_output=True, text=True,
    env={**os.environ, "BUILDUTIL_SYSTEM": "1", "HOME": str(tmp_path),
         "PYTHONPATH": str(Path(bootstrap.__file__).resolve().parents[1]),
         "PIP_NO_INDEX": "1", "PIP_FIND_LINKS": str(wheels)})
  assert proc.returncode == 0, proc.stdout + proc.stderr
  return py


def _has(py, name):
  return subprocess.run([str(py), "-c", f"import {name}"],
                        capture_output=True).returncode == 0


@pytest.mark.skipif(os.name == "nt", reason="posix venv layout")
def test_system_mode_installs_a_declared_extra_dep(tmp_path):
  py = _ensure_in_venv(tmp_path, ["bdudemo==1.0.0"])
  assert _has(py, "bdudemo")


@pytest.mark.skipif(os.name == "nt", reason="posix venv layout")
def test_system_mode_installs_nothing_without_a_declaration(tmp_path):
  # PIP_NO_INDEX would not save this: the wheel is on the find-links path
  py = _ensure_in_venv(tmp_path, [])
  assert not _has(py, "bdudemo")


def test_an_exact_pin_already_installed_is_satisfied():
  version = importlib.metadata.version("pytest")
  assert bootstrap._unsatisfied([f"pytest=={version}"]) == []
  assert bootstrap._unsatisfied(["pytest==0.0.1"]) == ["pytest==0.0.1"]
  assert bootstrap._unsatisfied(["nosuchdist"]) == ["nosuchdist"]


def test_system_mode_refuses_an_unwritable_interpreter(monkeypatch, capsys):
  monkeypatch.setenv("BUILDUTIL_SYSTEM", "1")
  monkeypatch.setattr(config, "PROJECT",
                      {**config.PROJECT, "cmake_extensions": [],
                       "venv_extra_deps": ["bdudemo==1.0.0"]})
  monkeypatch.setattr(bootstrap.os, "access", lambda *a, **k: False)
  monkeypatch.setattr(bootstrap, "_run",
                      lambda cmd: pytest.fail("installed anyway"))
  with pytest.raises(SystemExit) as exit:
    bootstrap.ensure()
  assert "bdudemo==1.0.0" in str(exit.value)
  assert "not writable" in str(exit.value)
