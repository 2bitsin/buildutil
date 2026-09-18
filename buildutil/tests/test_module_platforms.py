"""Target platform defaults share the dormant module override contract."""
import shutil
import subprocess

import pytest

from buildutil import config, deposit, engine, modules, naming
from buildutil.tests.test_module_toggle_e2e import CFG, _tree

needs_cmake = pytest.mark.skipif(shutil.which("cmake") is None,
                                 reason="needs cmake")


def test_emscripten_table():
  assert naming.live_platform_tags("Emscripten") == ("posix", "emscripten")
  assert "native" not in naming.live_platform_tags("Emscripten")
  assert naming.live_platform_tags("Windows") == ("native", "win32")
  assert naming.TAG_SYSTEMS["native"] == ("Windows", "Linux", "Darwin")
  assert naming.PLATFORM_LEVEL["native"] == 1
  assert naming.TAG_SYSTEMS["emscripten"] == ("Emscripten",)
  assert naming.PLATFORM_LEVEL["emscripten"] == 2


def test_config_platforms(tmp_path, monkeypatch):
  path = tmp_path / "buildutil.toml"
  monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
  monkeypatch.setattr(config, "HAVE_PROJECT", True)
  path.write_text('[modules.http]\nplatforms = ["posix", "windows"]\n')
  assert config._load_project()["modules_platforms"] == {"http": ["posix", "win32"]}
  path.write_text('[modules.http]\nplatforms = ["typo"]\n')
  with pytest.raises(SystemExit, match="unknown platform 'typo'.*known:.*emscripten"):
    config._load_project()


@needs_cmake
@pytest.mark.parametrize("system", ["Linux", "Darwin", "Windows", "Emscripten"])
@pytest.mark.parametrize("defaults,enabled,disabled", [([], [], []),
  (["http"], [], []), ([], ["http"], []), ([], [], ["http"]),
  (["http"], ["http"], [])])
def test_platform_dormancy(tmp_path, system, defaults, enabled, disabled):
  _tree(tmp_path, ["http", "core"], defaults)
  platforms = {"http": ["native"]}
  deposit.ensure(tmp_path, dict(CFG, modules_dormant=defaults,
                               modules_platforms=platforms))
  ini = tmp_path / "_bdudata/modules.ini"
  modules.save(ini, enabled, disabled)
  expected = ({"http"} if defaults or disabled or system == "Emscripten" else set())
  expected -= set(enabled)
  assert modules.dormant_set(ini, defaults, system=system,
                             platforms=platforms) == expected
  script = tmp_path / "CMakeLists.txt"
  script.write_text(
    "cmake_minimum_required(VERSION 3.25)\nproject(check CXX)\n"
    f'include("{tmp_path}/_bdudata/cmake/buildutil.cmake")\n'
    f'set(CMAKE_SOURCE_DIR "{tmp_path}")\n'
    f'set(CMAKE_SYSTEM_NAME "{system}")\n'
    '_buildutil_dormant_modules(actual)\n'
    f'if(NOT "${{actual}}" STREQUAL "{";".join(sorted(expected))}")\n'
    '  message(FATAL_ERROR "Unexpected dormant set: ${actual}")\nendif()\n')
  out = subprocess.run(["cmake", "-S", str(tmp_path), "-B", str(tmp_path / "b")],
                       text=True, capture_output=True, stdin=subprocess.DEVNULL)
  assert out.returncode == 0, out.stdout + out.stderr


def test_compiler_lane_defines(tmp_path, monkeypatch):
  _tree(tmp_path, ["http", "core"])
  monkeypatch.setitem(config.PROJECT, "modules_platforms", {"http": ["native"]})
  monkeypatch.setattr(engine, "REPO_ROOT", tmp_path)
  monkeypatch.setattr(engine, "MODULES_INI", tmp_path / "_bdudata/modules.ini")
  monkeypatch.setattr(engine, "_wine_msvc_live", lambda: False)
  monkeypatch.setattr(engine, "_osxcross_live", lambda: False)
  monkeypatch.setattr(engine.platform, "system", lambda: "Linux")
  monkeypatch.setattr(engine, "_emscripten_live", lambda: False)
  assert any("HTTP_ENABLED" in d for d in engine._enabled_module_defines())
  monkeypatch.setattr(engine, "_emscripten_live", lambda: True)
  assert modules.dormant_set(engine.MODULES_INI, system=engine._target_system()) == {"http"}
  assert not any("HTTP_ENABLED" in d for d in engine._enabled_module_defines())


@needs_cmake
@pytest.mark.parametrize("system,selected", [("Linux", "sockets.native.cpp"),
                                              ("Windows", "sockets.native.cpp"),
                                              ("Emscripten", "web.emscripten.cpp")])
def test_tagged_sources(tmp_path, system, selected):
  deposit.ensure(tmp_path, CFG)
  for name in ("sockets.native.cpp", "web.emscripten.cpp"):
    (tmp_path / name).touch()
  script = tmp_path / "CMakeLists.txt"
  script.write_text(
    "cmake_minimum_required(VERSION 3.25)\nproject(check CXX)\n"
    f'include("{tmp_path}/_bdudata/cmake/buildutil.cmake")\n'
    f'set(CMAKE_SYSTEM_NAME "{system}")\n'
    f'_buildutil_split_source_list("{tmp_path}/sockets.native.cpp;{tmp_path}/web.emscripten.cpp" actual tests benches)\n'
    f'if(NOT "${{actual}}" STREQUAL "{tmp_path}/{selected}")\n'
    '  message(FATAL_ERROR "Unexpected sources: ${actual}")\nendif()\n')
  out = subprocess.run(["cmake", "-S", str(tmp_path), "-B", str(tmp_path / "b")],
                       text=True, capture_output=True, stdin=subprocess.DEVNULL)
  assert out.returncode == 0, out.stdout + out.stderr


@needs_cmake
@pytest.mark.parametrize("system", ["Linux", "Emscripten"])
def test_cmake_targets_and_defines(tmp_path, system):
  _tree(tmp_path, ["http", "core"])
  deposit.ensure(tmp_path, dict(CFG, modules_platforms={"http": ["native"]}))
  root = tmp_path / "CMakeLists.txt"
  text = root.read_text().replace('add_subdirectory(sources)',
    f'set(CMAKE_SYSTEM_NAME "{system}")\n'
    'set(ACM_MODULE_DEFINES ACM_HTTP_ENABLED=1 ACM_CORE_ENABLED=1)\n'
    'add_subdirectory(sources)\n'
    'get_directory_property(defs DIRECTORY sources COMPILE_DEFINITIONS)\n')
  if system == "Emscripten":
    text += ('if(TARGET http OR "ACM_HTTP_ENABLED=1" IN_LIST defs)\n'
             '  message(FATAL_ERROR "dormant module survived")\nendif()\n')
  else:
    text += ('if(NOT TARGET http OR NOT "ACM_HTTP_ENABLED=1" IN_LIST defs)\n'
             '  message(FATAL_ERROR "native module missing")\nendif()\n')
  root.write_text(text)
  out = subprocess.run(["cmake", "-S", str(tmp_path), "-B", str(tmp_path / "b")],
                       text=True, capture_output=True, stdin=subprocess.DEVNULL)
  assert out.returncode == 0, out.stdout + out.stderr
