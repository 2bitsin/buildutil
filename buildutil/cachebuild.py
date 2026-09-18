"""`buildutil cache-build` — build THIS project inside the conan cache
(stdlib-only, pre-venv, like init).

Invoked by the published recipe's build() when the package carries a
vendored buildutil (`publish --bake-buildutil`, or any vendored
project): a consumer's `--build=missing` then builds the package from
source instead of hitting the recipe's refusal.

The division of labour that keeps this small: conan OWNS the dependency
graph — by the time build() runs, the consumer's own install has
resolved every Require() against THEIR cache/remotes and generated the
toolchain + find_package config for this build. What the cache is
missing is only buildutil's side of the contract: the rendered cmake
machinery (_bdudata/cmake) and the configure options the driver
normally passes. So: render the deposit, stamp a buildinfo stub, and
run plain cmake against conan's toolchain — no venv, no nested conan,
no network.

Deliberately NOT here: the driver's ccache/cross-lane (wine-msvc,
osxcross) configure extras. Cross lanes publish binaries; a cache
source-build is the native-consumer fallback path.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def main(argv: list[str]) -> None:
  ap = argparse.ArgumentParser(
    prog="buildutil cache-build",
    description="build this project inside the conan cache against the "
                "toolchain conan generated. Internal plumbing: the "
                "published recipe's build() invokes it with cwd = the "
                "cache source folder; there is no reason to run it by "
                "hand in a working tree (use `buildutil build`).")
  ap.add_argument("--build-dir", required=True,
                  help="conan's build folder for this package build")
  ap.add_argument("--toolchain", required=True,
                  help="path to the generated conan_toolchain.cmake")
  ap.add_argument("--build-type", required=True,
                  help="CMAKE_BUILD_TYPE — the consumer's "
                       "settings.build_type")
  ap.add_argument("--linkage", choices=("static", "shared"),
                  default="static",
                  help="linkage for untagged modules; the recipe maps "
                       "its conan `shared` option here")
  a = ap.parse_args(argv)
  # before the chdir, and absolute: cmake resolves a relative toolchain
  # against the BUILD tree before the source tree
  toolchain = Path(a.toolchain).resolve()

  from . import config, deposit, modules
  root = config.require_project()
  os.chdir(root)

  deposit_dir = deposit.ensure(root, config.PROJECT,
                               config.PROJECT["cmake_extensions"])
  # A project's version codegen may declare buildinfo.json as an input;
  # a cache build has no build counter, and saying so beats a
  # missing-file failure at configure.
  config.BDUDATA_DIR.mkdir(exist_ok=True)
  (config.BDUDATA_DIR / "buildinfo.json").write_text(json.dumps(
    {"number": 0, "commit": "conan-cache", "dirty": False}) + "\n")

  # The BUILDUTIL=<version> contract the driver gives every child — the
  # rendered guard refuses configure/build without it. The vendored
  # copy's VENDORED marker carries the version this package was baked
  # with; running un-vendored (development) falls back to a label.
  marker = Path(__file__).resolve().parent / "VENDORED"
  version = (marker.read_text(encoding="utf-8").strip()
             if marker.is_file() else "cache-build")
  env = dict(os.environ, BUILDUTIL=version)

  build_dir = Path(a.build_dir)
  defines = modules.enabled_definitions(root / "sources",
                                        config.MODULES_INI)
  prefix = config.CMAKE_PREFIX
  # Path-valued -D arguments go through config.cmake_path() for the reason
  # given there, and this is the lane a CONSUMER runs: it would fail on
  # their machine, in the compiler probe, before building anything.
  subprocess.check_call([
    "cmake", "-S", ".", "-B", config.cmake_path(build_dir), "-G", "Ninja",
    f"-DCMAKE_TOOLCHAIN_FILE={config.cmake_path(toolchain)}",
    "-DCMAKE_POLICY_DEFAULT_CMP0091=NEW",
    f"-DCMAKE_BUILD_TYPE={a.build_type}",
    # a consumer's cache build ships binaries, not this package's QA
    "-DBUILD_TESTING=OFF",
    "-DBUILD_BENCHMARKING=OFF",
    f"-DBUILDUTIL_MODULE_LINKAGE={a.linkage}",
    "-DBUILDUTIL_COVERAGE=OFF",
    "-DBUILDUTIL_GC_SECTIONS=OFF",
    f"-D{prefix}_MAX_ERRORS=0",
    f"-D{config.MODULE_DEFINE_PREFIX}_MODULE_DEFINES={';'.join(defines)}",
    f"-DCMAKE_MODULE_PATH={config.cmake_path(deposit_dir)}",
    f"-DBUILDUTIL_PY={config.cmake_path(sys.executable)}",
    f"-DBUILDUTIL_PYSUPPORT="
    f"{config.cmake_path(Path(__file__).resolve().parent / 'pysupport')}",
    # configure-time codegen runs under find_package(Python3); in the
    # cache there is no project venv — the interpreter running this
    # (the consumer's conan python) is the one that exists
    f"-DPython3_EXECUTABLE={config.cmake_path(sys.executable)}",
  ], env=env)
  subprocess.check_call(["cmake", "--build", config.cmake_path(build_dir)],
                        env=env)
