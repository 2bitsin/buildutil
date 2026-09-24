"""Explicit wasm selection, isolated host policy, and an optional SDK build."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from buildutil import config, engine


@pytest.fixture
def sdk(tmp_path, monkeypatch):
  root = tmp_path / "sdk/upstream/emscripten"
  root.mkdir(parents=True)
  for name in ("emcc", "em++"):
    wrapper = root / name
    wrapper.write_text(f"#!{sys.executable}\nprint('clang version 24.0.0')\n")
    wrapper.chmod(0o755)
  toolchain = root / "cmake/Modules/Platform/Emscripten.cmake"
  toolchain.parent.mkdir(parents=True)
  toolchain.touch()
  monkeypatch.setenv("PATH", str(root))
  monkeypatch.delenv("EMSDK", raising=False)
  monkeypatch.setenv("CC", "")
  monkeypatch.setenv("CXX", "")
  monkeypatch.setattr(engine.platform, "system", lambda: "Linux")
  return root


def test_detection_and_explicit_selection(sdk, monkeypatch):
  monkeypatch.setattr(engine, "_scan_compiler", lambda _: "/native/gcc")
  assert engine._available_compilers()["emscripten"] == str(sdk / "emcc")
  engine._select_compiler(None, "auto")
  assert os.environ["CC"] == "/native/gcc"
  assert not engine._emscripten_live()
  engine._select_compiler("emscripten", "auto")
  assert os.environ["CC"] == str(sdk / "emcc")
  assert os.environ["CXX"] == str(sdk / "em++")
  assert engine._emscripten_live()


def test_emsdk_without_path_and_linux_only(sdk, monkeypatch):
  monkeypatch.setenv("EMSDK", str(sdk.parents[1]))
  monkeypatch.setenv("PATH", "")
  assert engine._emscripten_root() == sdk
  monkeypatch.setattr(engine.platform, "system", lambda: "Darwin")
  assert engine._emscripten_root() is None


def test_cross_only_is_not_auto(sdk, monkeypatch):
  monkeypatch.setattr(engine, "_scan_compiler", lambda _: None)
  with pytest.raises(engine.typer.Exit):
    engine._select_compiler(None, "auto")


@pytest.mark.parametrize("build_type", ["Debug", "Release", "RelWithDebInfo"])
def test_profile(sdk, tmp_path, monkeypatch, build_type):
  monkeypatch.chdir(tmp_path)
  engine._select_compiler("emscripten", "auto")
  monkeypatch.setattr(engine, "_gcc_install_dir", lambda: pytest.fail("native flags"))
  project = dict(config.PROJECT, conan_options=["pkg/*:shared=False"],
                 conan_options_os={"emscripten": ["sdl/*:opengl=False"],
                                   "linux": ["sdl/*:x11=False"]}, conan_conf=[])
  monkeypatch.setattr(config, "PROJECT", project)
  profile = engine._ensure_profile(engine._detect_settings(build_type))
  assert profile.name == f"wasm-emscripten-clang-{build_type.lower()}"
  assert profile.read_text().splitlines() == [
    "[settings]", "arch=wasm", "os=Emscripten", "compiler=clang",
    "compiler.version=20", "compiler.cppstd=26", "compiler.libcxx=libc++",
    f"build_type={build_type}", "", "[options]", "pkg/*:shared=False",
    "sdl/*:opengl=False", "", "[conf]",
    "tools.cmake.cmaketoolchain:generator=Ninja",
    f"tools.cmake.cmaketoolchain:user_toolchain=['{sdk}/cmake/Modules/Platform/Emscripten.cmake']",
    f"tools.build:compiler_executables={{'c': '{sdk}/emcc', 'cpp': '{sdk}/em++'}}",
  ]
  monkeypatch.setattr(engine, "_ensure_cross_build_profile", lambda: Path("native"))
  assert engine._host_build_profiles(profile) == (profile, Path("native"))


def test_version_falls_back_to_verbose(sdk, monkeypatch):
  monkeypatch.setattr(engine.subprocess, "check_output", lambda *a, **k: "emcc 6.0.9")
  monkeypatch.setattr(engine.subprocess, "run", lambda *a, **k:
                      subprocess.CompletedProcess(a, 0, "", "clang version 24.0.0git"))
  assert engine._emscripten_clang_version() == "20"


def test_emscripten_smoke(tmp_path):
  if engine._emscripten_root() is None:
    pytest.skip("emsdk unavailable: set EMSDK or source emsdk_env.sh")
  env = dict(os.environ)
  for key in tuple(env):
    if key.startswith(("CONAN_REMOTE_", "CI_ARTIFACTORY_")) or key in (
        "BUILDUTIL_ROOT", "CC", "CXX"):
      env.pop(key)
  env.update(PYTHONPATH=str(Path(__file__).resolve().parents[2]),
             BUILDUTIL_SYSTEM="1", CONAN_HOME=str(tmp_path / "conan"),
             EM_CACHE=str(tmp_path / "em-cache"),
             CCACHE_DISABLE="1", EMCC_CORES="1")
  command = [sys.executable, "-m", "buildutil", "--i-am-willingly-circumventing-build-and-test-time-safeguards"]
  subprocess.run(command + ["init", "--name", "wasmhello", "--no-package",
                            "--no-agents"], cwd=tmp_path, env=env,
                 stdin=subprocess.DEVNULL, check=True)
  with (tmp_path / "buildutil.toml").open("a") as toml:
    toml.write('\n[modules.http]\nplatforms = ["native"]\n')
  http = tmp_path / "sources/http"
  http.mkdir()
  (http / "CMakeLists.txt").write_text('message(FATAL_ERROR "http is dormant")\n')
  hello = tmp_path / "sources/wasmhello/hello"
  (hello / "web.emscripten.cpp").write_text(
    '#ifndef __EMSCRIPTEN__\n#error wrong target\n#endif\n'
    '#ifdef WASMHELLO_HTTP_ENABLED\n#error dormant define\n#endif\n')
  (hello / "sockets.native.cpp").write_text('#error native is not the browser\n')
  (hello / "files.posix.cpp").write_text(
    '#ifndef __EMSCRIPTEN__\n#error posix includes Emscripten\n#endif\n')
  result = subprocess.run(command + ["--jobs", "1", "build", "--compiler", "emscripten",
                            "--no-tests", "--release",
                            "--skip-dependency-upload-so-everyone-rebuilds-from-source"],
                 cwd=tmp_path, env=env, stdin=subprocess.DEVNULL,
                 capture_output=True, text=True)
  print(result.stdout, end="")
  print(result.stderr, end="", file=sys.stderr)
  result.check_returncode()
  assert "dormant modules (project defaults + _bdudata/modules.ini): http" in result.stdout
  databases = list((tmp_path / "_build").rglob("compile_commands.json"))
  assert databases
  commands = json.loads(databases[0].read_text())
  assert any(c["file"].endswith("web.emscripten.cpp") for c in commands)
  assert not any(c["file"].endswith("sockets.native.cpp") for c in commands)
  assert any(c["file"].endswith("files.posix.cpp") for c in commands)
  assert all("WASMHELLO_HTTP_ENABLED" not in c["command"] for c in commands)
  for suffix in ("html", "js", "wasm"):
    assert (tmp_path / "_install/wasmhello" / f"hello.{suffix}").is_file()


def test_options_emscripten_from_toml(tmp_path, monkeypatch):
  (tmp_path / "buildutil.toml").write_text(
    '[conan]\noptions_emscripten = ["sdl/*:opengl=False"]\n')
  monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
  monkeypatch.setattr(config, "HAVE_PROJECT", True)
  assert config._load_project()["conan_options_os"] == {
    "emscripten": ["sdl/*:opengl=False"]}


def test_other_cross_wrappers_cannot_override_wasm(sdk, monkeypatch):
  engine._select_compiler("emscripten", "auto")
  monkeypatch.setattr(engine.shutil, "which", lambda name: f"/tools/{name}")
  assert not engine._wine_msvc_live()
  assert not engine._osxcross_live()


def test_incomplete_sdk_is_refused(sdk):
  (sdk / "cmake/Modules/Platform/Emscripten.cmake").unlink()
  with pytest.raises(RuntimeError, match="Incomplete Emscripten SDK"):
    engine._select_compiler("emscripten", "auto")


def test_native_build_profile(sdk, tmp_path, monkeypatch):
  engine._select_compiler("emscripten", "auto")
  monkeypatch.setenv("CONAN_HOME", str(tmp_path / "conan"))
  monkeypatch.setattr(engine, "_scan_compiler", lambda _: "/native/gcc")
  monkeypatch.setattr(engine.subprocess, "check_output", lambda *a, **k: "15.1.0")
  profile = engine._ensure_cross_build_profile()
  assert profile.read_text().splitlines() == [
    "[settings]", "os=Linux", "arch=x86_64", "build_type=Release",
    "compiler=gcc", "compiler.version=15", "compiler.cppstd=26",
    "compiler.libcxx=libstdc++11", "[conf]",
    "tools.cmake.cmaketoolchain:generator=Ninja",
    "tools.build:compiler_executables={'c': '/native/gcc', 'cpp': '/native/g++'}",
  ]
