"""`cache-build` — the baked package's source-build lane (publish
--bake-buildutil): stdlib-only, invoked by the published recipe's
build() inside the conan cache. Conan owns the dependency graph there;
cache-build only renders the cmake machinery and configures/builds
against the toolchain conan generated.

E2e through the real entry point with a fake cmake on PATH: proves the
dispatch stays on the stdlib lane (no venv bootstrap, no typer), the
deposit is rendered, the buildinfo stub exists, and the configure line
carries the driver contract (toolchain, prefixes, defines, BUILDUTIL
env for the guard)."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from buildutil import initcmd

PKG_PARENT = Path(initcmd.__file__).resolve().parents[1]

FAKE_CMAKE = """\
#!/bin/sh
echo "BUILDUTIL=$BUILDUTIL" >> "$CMAKE_LOG"
echo "$@" >> "$CMAKE_LOG"
"""


@pytest.fixture
def cache_tree(tmp_path):
  """A fake conan-cache source folder: exported project + a recorded
  cmake."""
  root = tmp_path / "src"
  (root / "sources").mkdir(parents=True)
  (root / "buildutil.toml").write_text(
    '[project]\nname = "p"\ncmake_option_prefix = "PX"\n'
    'module_define_prefix = "PM"\n'
    '[package]\nkind = "library"\nname = "p"\n')
  (root / "sources" / "CMakeLists.txt").write_text("")
  bin_dir = tmp_path / "bin"
  bin_dir.mkdir()
  cmake = bin_dir / "cmake"
  cmake.write_text(FAKE_CMAKE)
  cmake.chmod(0o755)
  gen = tmp_path / "generators"
  gen.mkdir()
  (gen / "conan_toolchain.cmake").write_text("# toolchain\n")
  return root, bin_dir, gen, tmp_path / "cmake.log"


def _run_cache_build(root, bin_dir, gen, log, extra=()):
  return subprocess.run(
    [sys.executable, "-m", "buildutil", "cache-build",
     "--build-dir", str(root / "_build" / "cache"),
     "--toolchain", str(gen / "conan_toolchain.cmake"),
     "--build-type", "Debug", *extra],
    cwd=root, capture_output=True, text=True,
    env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(root),
         "PYTHONPATH": str(PKG_PARENT), "CMAKE_LOG": str(log)})


def test_a_relative_toolchain_reaches_cmake_absolute(cache_tree):
  """cache-build chdirs into the cache source folder, and cmake resolves
  a relative toolchain against the BUILD tree first."""
  root, bin_dir, gen, log = cache_tree
  relative = Path(os.path.relpath(gen / "conan_toolchain.cmake", root))
  proc = subprocess.run(
    [sys.executable, "-m", "buildutil", "cache-build",
     "--build-dir", str(root / "_build" / "cache"),
     "--toolchain", str(relative), "--build-type", "Debug"],
    cwd=root, capture_output=True, text=True,
    env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(root),
         "PYTHONPATH": str(PKG_PARENT), "CMAKE_LOG": str(log)})
  assert proc.returncode == 0, proc.stdout + proc.stderr
  configure = next(l for l in log.read_text().splitlines() if " -S " in f" {l}")
  assert f"-DCMAKE_TOOLCHAIN_FILE={gen / 'conan_toolchain.cmake'}" in configure


def test_cache_build_configures_and_builds_against_the_toolchain(cache_tree):
  root, bin_dir, gen, log = cache_tree
  proc = _run_cache_build(root, bin_dir, gen, log,
                          extra=("--linkage", "shared"))
  assert proc.returncode == 0, proc.stdout + proc.stderr
  lines = log.read_text().splitlines()
  # two cmake invocations: configure, then --build — each preceded by
  # the BUILDUTIL env the rendered guard requires of every child
  configure = next(l for l in lines if l.startswith("-S ") or " -S " in f" {l}")
  build = next(l for l in lines if l.startswith("--build"))
  assert f"-DCMAKE_TOOLCHAIN_FILE={gen / 'conan_toolchain.cmake'}" in configure
  assert "-DCMAKE_BUILD_TYPE=Debug" in configure
  assert "-DBUILD_TESTING=OFF" in configure
  assert "-DBUILD_BENCHMARKING=OFF" in configure
  assert "-DBUILDUTIL_MODULE_LINKAGE=shared" in configure
  assert "-DBUILDUTIL_COVERAGE=OFF" in configure
  assert "-DBUILDUTIL_GC_SECTIONS=OFF" in configure
  assert "-DPX_COVERAGE" not in configure, (
    "the per-project option prefix is gone from the build-shape switches; "
    "the machinery reads BUILDUTIL_COVERAGE, one name for every project")
  assert "-DPM_MODULE_DEFINES=" in configure
  # .as_posix(), not str(): every path-valued -D is handed to cmake with
  # forward slashes, and on POSIX the two agree --
  # asserting str() here would pass for the wrong reason and let the
  # Windows rendering back in.
  assert (f"-DCMAKE_MODULE_PATH="
          f"{(root / '_bdudata' / 'cmake').as_posix()}") in configure
  assert str(root / "_build" / "cache") in build
  assert all(l != "BUILDUTIL=" for l in lines if l.startswith("BUILDUTIL="))


def test_cache_build_renders_the_machinery_and_the_buildinfo_stub(cache_tree):
  root, bin_dir, gen, log = cache_tree
  proc = _run_cache_build(root, bin_dir, gen, log)
  assert proc.returncode == 0, proc.stdout + proc.stderr
  assert (root / "_bdudata" / "cmake" / "buildutil.cmake").is_file()
  info = (root / "_bdudata" / "buildinfo.json").read_text()
  assert '"commit": "conan-cache"' in info


def test_cache_build_defaults_to_static_linkage(cache_tree):
  root, bin_dir, gen, log = cache_tree
  proc = _run_cache_build(root, bin_dir, gen, log)
  assert proc.returncode == 0, proc.stdout + proc.stderr
  assert "-DBUILDUTIL_MODULE_LINKAGE=static" in log.read_text()


def test_cache_build_refuses_outside_a_project(tmp_path):
  bin_dir = tmp_path / "bin"
  bin_dir.mkdir()
  proc = subprocess.run(
    [sys.executable, "-m", "buildutil", "cache-build",
     "--build-dir", "b", "--toolchain", "t", "--build-type", "Debug"],
    cwd=tmp_path, capture_output=True, text=True,
    env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
         "PYTHONPATH": str(PKG_PARENT)})
  assert proc.returncode != 0
  assert "no buildutil.toml" in proc.stderr + proc.stdout


# ------------------------------------------ [venv] extra_deps on this lane
# A cache build runs under the CONSUMER's conan interpreter, inside
# their cache, so it may neither build a venv there nor pip-install
# behind their back. What it must not do either is let the declaration
# fail as a missing cmake package three steps later.

def _declaring(root, deps):
  root.joinpath("buildutil.toml").write_text(
    '[project]\nname = "p"\ncmake_option_prefix = "PX"\n'
    'module_define_prefix = "PM"\n'
    f"[venv]\nextra_deps = {deps!r}\n"
    '[package]\nkind = "library"\nname = "p"\n'.replace("'", '"'))


def test_a_missing_declared_dependency_is_named_before_cmake(cache_tree):
  root, bin_dir, gen, log = cache_tree
  _declaring(root, ["nosuchdist-buildutil==9.9.9"])
  proc = _run_cache_build(root, bin_dir, gen, log)
  assert proc.returncode != 0
  message = proc.stdout + proc.stderr
  assert "extra_deps" in message
  assert "nosuchdist-buildutil==9.9.9" in message
  assert not log.exists(), "cmake ran despite the missing declaration"


def test_a_satisfied_declaration_builds_as_before(cache_tree):
  root, bin_dir, gen, log = cache_tree
  _declaring(root, ["pytest"])
  proc = _run_cache_build(root, bin_dir, gen, log)
  assert proc.returncode == 0, proc.stdout + proc.stderr
  assert log.exists()


def test_the_opt_in_installs_into_the_running_interpreter(monkeypatch,
                                                          tmp_path):
  from buildutil import cachebuild, config
  monkeypatch.setattr(config, "PROJECT",
                      {**config.PROJECT,
                       "venv_extra_deps": ["nosuchdist-buildutil==9.9.9"]})
  monkeypatch.setenv("BUILDUTIL_CACHE_BUILD_DEPS", "install")
  installed = []
  monkeypatch.setattr(cachebuild.subprocess, "check_call",
                      lambda cmd, **kw: installed.append(cmd))
  cachebuild._require_declared_deps()
  assert installed == [[sys.executable, "-m", "pip", "install",
                        "nosuchdist-buildutil==9.9.9"]]


def test_no_declaration_installs_nothing(monkeypatch):
  from buildutil import cachebuild, config
  monkeypatch.setattr(config, "PROJECT",
                      {**config.PROJECT, "venv_extra_deps": []})
  monkeypatch.setattr(cachebuild.subprocess, "check_call",
                      lambda cmd, **kw: (_ for _ in ()).throw(
                        AssertionError("installed without a declaration")))
  cachebuild._require_declared_deps()
