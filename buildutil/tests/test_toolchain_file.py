"""CMAKE_TOOLCHAIN_FILE: absolute, and passed only to a tree cmake will
read it on. A relative path is resolved against the BUILD tree first, so
debris there captures the build; a re-passed one prints as unused-cli."""
from pathlib import Path

import pytest

import buildutil.engine as engine

PROFILE = "x86_64-linux-gcc-debug"
TOOLCHAIN_TEXT = "# conan toolchain\n"


@pytest.fixture
def configure(monkeypatch, tmp_path):
  """Run _cmake_configure in a tmp project and hand back cmake's argv."""
  monkeypatch.setattr(engine, "_stamp_build_info", lambda package="": None)
  monkeypatch.setattr(engine, "_regen_clangd", lambda build_dir: None)
  monkeypatch.setattr(engine, "_wine_msvc_live", lambda: False)
  monkeypatch.setattr(engine, "_osxcross_live", lambda: False)
  monkeypatch.setattr(engine.shutil, "which", lambda name: None)
  import buildutil.vscode
  monkeypatch.setattr(buildutil.vscode, "refresh", lambda active=None: None)
  monkeypatch.chdir(tmp_path)
  calls = []
  monkeypatch.setattr(engine.subprocess, "check_call",
                      lambda argv, **kw: calls.append(argv))

  def run(build_dir=Path("_build") / PROFILE):
    generators = build_dir / "generators"
    generators.mkdir(parents=True, exist_ok=True)
    (generators / "conan_toolchain.cmake").write_text(TOOLCHAIN_TEXT)
    calls.clear()
    engine._cmake_configure(build_dir, "Debug", tests=False, bench=False)
    (argv,) = calls
    return argv
  return run


def _toolchain_arg(argv):
  values = [a.split("=", 1)[1] for a in argv
            if a.startswith("-DCMAKE_TOOLCHAIN_FILE=")]
  return values[0] if values else None


def test_the_toolchain_file_is_passed_absolute(configure, tmp_path):
  value = _toolchain_arg(configure())
  assert Path(value).is_absolute()
  assert value == (tmp_path / "_build" / PROFILE / "generators"
                   / "conan_toolchain.cmake").as_posix()


def test_stale_nested_debris_never_wins(configure, tmp_path):
  """The reported repro: an old tree at <build>/_build/<profile>/."""
  build_dir = Path("_build") / PROFILE
  nested = build_dir / "_build" / PROFILE / "generators"
  nested.mkdir(parents=True)
  (nested / "conan_toolchain.cmake").write_text("# stale\n")
  value = _toolchain_arg(configure(build_dir))
  resolved = tmp_path / build_dir / value      # cmake's own resolution
  assert resolved.read_text() == TOOLCHAIN_TEXT


def test_a_configured_tree_is_not_handed_the_flag_again(configure, tmp_path):
  """Passing it again prints 'Manually-specified variables were not
  used'."""
  first = configure()
  assert _toolchain_arg(first) is not None
  build_dir = tmp_path / "_build" / PROFILE
  (build_dir / "CMakeCache.txt").write_text(
    f"CMAKE_TOOLCHAIN_FILE:FILEPATH={_toolchain_arg(first)}\n")
  assert _toolchain_arg(configure()) is None


def test_a_cache_holding_a_relative_toolchain_is_dropped_once(configure,
                                                              tmp_path):
  configure()
  build_dir = tmp_path / "_build" / PROFILE
  cache = build_dir / "CMakeCache.txt"
  cache.write_text("CMAKE_TOOLCHAIN_FILE:FILEPATH="
                   f"_build/{PROFILE}/generators/conan_toolchain.cmake\n")
  cmakefiles = build_dir / "CMakeFiles"
  (cmakefiles / "3.28.0").mkdir(parents=True)
  (cmakefiles / "3.28.0" / "CMakeSystem.cmake").write_text("# resolved\n")

  value = _toolchain_arg(configure())
  assert Path(value).is_absolute()
  assert not cmakefiles.exists()


def test_a_changed_toolchain_still_drops_the_cache(configure, tmp_path):
  """conan's *_INIT seeds only take on a first configure, so a toolchain
  whose CONTENT moved has to start the tree over."""
  first = configure()
  build_dir = tmp_path / "_build" / PROFILE
  (build_dir / "CMakeCache.txt").write_text(
    f"CMAKE_TOOLCHAIN_FILE:FILEPATH={_toolchain_arg(first)}\n")
  (build_dir / ".buildutil-toolchain.stamp").write_text("# something else\n")
  assert _toolchain_arg(configure()) is not None
  assert not (build_dir / "CMakeCache.txt").exists()


def test_the_profile_is_passed_to_cmake(configure):
  assert f"-DBUILDUTIL_PROFILE={PROFILE}" in configure()
