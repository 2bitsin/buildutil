"""Path-valued -D arguments reach cmake with forward slashes.

CMake re-emits some of what we pass it AS CMAKE CODE: try_compile copies
the inherited CMAKE_MODULE_PATH into its scratch project as a quoted
set(), and there a backslash starts an escape sequence -- `C:\\Devel` is
"Invalid character escape '\\D'" and the compiler-ABI probe never
completes.  No Windows build got past configure until this held -- the
first runs of `buildutil publish` on Windows are what found it.

The tests run on Linux, so the Windows rendering is produced the only
honest way available there: a PureWindowsPath, whose flavour
config.cmake_path() preserves.  Every one of these values is a Path (or
sys.executable) on the real runner.
"""
from pathlib import PureWindowsPath

import pytest

from buildutil import config
import buildutil.engine as engine


ROOT = PureWindowsPath(r"C:\Devel\builds\project")


def test_cmake_path_renders_a_windows_path_with_forward_slashes():
  assert config.cmake_path(ROOT / "_bdudata" / "cmake") == (
    "C:/Devel/builds/project/_bdudata/cmake")


def test_cmake_path_leaves_a_posix_path_alone():
  assert config.cmake_path("/builds/project/_bdudata/cmake") == (
    "/builds/project/_bdudata/cmake")


def test_configure_passes_no_backslash_in_any_define(monkeypatch, tmp_path):
  """The seam, not just the helper: every path the driver interpolates
  into a -D is rendered through it."""
  monkeypatch.setattr(engine, "_stamp_build_info", lambda package="": None)
  monkeypatch.setattr(engine, "_regen_clangd", lambda build_dir: None)
  monkeypatch.setattr(engine, "_wine_msvc_live", lambda: False)
  monkeypatch.setattr(engine, "_osxcross_live", lambda: False)
  monkeypatch.setattr(engine.shutil, "which", lambda name: None)
  import buildutil.vscode
  monkeypatch.setattr(buildutil.vscode, "refresh", lambda active=None: None)

  # the four values that are Windows-shaped on a Windows runner
  windows_python = ROOT / "_pyvenv" / "Scripts" / "python.exe"
  monkeypatch.setattr(engine, "_deposit_dir",
                      lambda: ROOT / "_bdudata" / "cmake")
  monkeypatch.setattr(engine, "VENV_PY", windows_python)
  monkeypatch.setattr(engine.sys, "executable", windows_python)

  calls = []
  monkeypatch.setattr(engine.subprocess, "check_call",
                      lambda argv, **kw: calls.append(argv))
  engine._cmake_configure(tmp_path, "Release", tests=False, bench=False)

  (argv,) = calls
  offenders = [a for a in argv if a.startswith("-D") and "\\" in a]
  assert not offenders, (
    "these -D values would reach cmake with backslashes, and the ones cmake "
    f"re-emits as code stop the build dead: {offenders}")
  assert (f"-DCMAKE_MODULE_PATH="
          f"{(ROOT / '_bdudata' / 'cmake').as_posix()}") in argv
  assert f"-DBUILDUTIL_PY={windows_python.as_posix()}" in argv
