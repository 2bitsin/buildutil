"""The driver exports a system@host recipe per SYSTEM Require first."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from buildutil import config, engine, initcmd, packaging

TEMPLATE = (Path(initcmd.__file__).resolve().parent / "templates" / "project"
            / "conanfile.py")

REQUIRES = ('Require(OpenSSL VERSION ">=3" SYSTEM FORCE)\n'
            'Require(FreeRDP-Client VERSION ">=3" SYSTEM TEST)\n'
            'Require(Boost VERSION ">=1.83" SYSTEM CONAN boost COMPONENTS json)\n'
            'Require(SDL2 VERSION ">=2.28" SYSTEM PLATFORM Emscripten)\n'
            'Require(fmt VERSION "10.2.1")\n')
NATIVE = ("openssl", "freerdp-client", "boost")


@pytest.fixture
def project(tmp_path, monkeypatch):
  (tmp_path / "sources").mkdir()
  (tmp_path / "sources" / "CMakeLists.txt").write_text(REQUIRES)
  (tmp_path / "conanfile.py").write_text(TEMPLATE.read_text())
  monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
  monkeypatch.setenv("CONAN_HOME", str(tmp_path / "home"))
  monkeypatch.setattr(engine, "_host_build_profiles",
                      lambda profile: (profile, profile))
  monkeypatch.setattr(engine, "_conan_target_os", lambda: "Linux")
  CACHES.clear()
  return tmp_path


CACHES: dict[str, set[str]] = {}


def _recorded(monkeypatch):
  """subprocess.check_call recorded, and a conan cache per CONAN_HOME that
  conan export fills and _cached_wrapper_revisions lists."""
  calls = []

  def check_call(argv, env=None):
    calls.append(argv)
    if argv[:2] == ["conan", "export"]:
      recipe = (Path(argv[2]) / "conanfile.py").read_bytes()
      revision = packaging.requires_parser().recipe_revision(recipe)
      CACHES.setdefault(os.environ["CONAN_HOME"], set()).add(
        f"{argv[4]}/system@host#{revision}")

  monkeypatch.setattr(engine.subprocess, "check_call", check_call)
  monkeypatch.setattr(engine, "_cached_wrapper_revisions",
                      lambda: set(CACHES.get(os.environ["CONAN_HOME"], ())))
  return calls


def test_the_SYSTEM_Requires_of_a_platform_are_read_by_the_shared_parser(
    project):
  hosts = packaging.host_requires(target_os="Linux")
  assert [(host["cmake_name"], host["conan_name"], host["components"],
           host["test"], host["force"]) for host in hosts] == [
    ("OpenSSL", "openssl", [], False, True),
    ("FreeRDP-Client", "freerdp-client", [], True, False),
    ("Boost", "boost", ["json"], False, False)]
  emscripten = packaging.host_requires(target_os="Emscripten")
  assert "SDL2" in [host["cmake_name"] for host in emscripten]


def test_conan_install_exports_wrappers(project, monkeypatch):
  calls = _recorded(monkeypatch)
  engine._conan_install(project / "profile", project / "build")
  exports = [argv for argv in calls if argv[:2] == ["conan", "export"]]
  assert exports == [
    ["conan", "export", str(project / "_bdudata" / "host" / name),
     "--name", name, "--version", "system", "--user", "host"]
    for name in NATIVE]
  assert calls[-1][:2] == ["conan", "install"]
  recipe = project / "_bdudata" / "host" / "boost" / "conanfile.py"
  assert 'CMAKE_NAME = "Boost"' in recipe.read_text()


def test_a_wrapper_recipe_is_the_same_text_for_every_project(project,
                                                            monkeypatch):
  _recorded(monkeypatch)
  engine._conan_install(project / "profile", project / "build")
  recipe = project / "_bdudata" / "host" / "boost" / "conanfile.py"
  template = packaging.REQUIRES_PARSER.parents[1] / "host" / "conanfile.py"
  assert recipe.read_text() == template.read_text().replace(
    "@CMAKE_NAME@", "Boost")


def test_an_unchanged_wrapper_is_not_exported_again(project, monkeypatch):
  _recorded(monkeypatch)
  engine._conan_install(project / "profile", project / "build")
  calls = _recorded(monkeypatch)
  engine._conan_install(project / "profile", project / "build")
  assert [argv[:2] for argv in calls] == [["conan", "install"]]


def test_a_revision_the_cache_lost_is_exported_again(project, monkeypatch):
  _recorded(monkeypatch)
  engine._conan_install(project / "profile", project / "build")
  CACHES[os.environ["CONAN_HOME"]] = {
    ref for ref in CACHES[os.environ["CONAN_HOME"]]
    if not ref.startswith("boost/")}
  calls = _recorded(monkeypatch)
  engine._conan_install(project / "profile", project / "build")
  assert [argv[4] for argv in calls if argv[:2] == ["conan", "export"]] == [
    "boost"]


def test_the_rendered_revision_is_what_conan_export_computes(tmp_path):
  if shutil.which("conan") is None:
    pytest.skip("needs conan")
  recipe = tmp_path / "wrapper" / "conanfile.py"
  recipe.parent.mkdir()
  recipe.write_text('from conan import ConanFile\n\n\n'
                    'class W(ConanFile):\n  pass\n')
  env = {**os.environ, "CONAN_HOME": str(tmp_path / "home")}
  exported = subprocess.run(
    ["conan", "export", str(recipe.parent), "--name", "wrapped", "--version",
     "system", "--user", "host", "--format=json"],
    env=env, capture_output=True, text=True, check=True)
  revision = packaging.requires_parser().recipe_revision(recipe.read_bytes())
  assert json.loads(exported.stdout)["reference"] == (
    f"wrapped/system@host#{revision}")


def test_a_new_conan_home_gets_the_wrappers_again(project, monkeypatch):
  _recorded(monkeypatch)
  engine._conan_install(project / "profile", project / "build")
  monkeypatch.setenv("CONAN_HOME", str(project / "elsewhere"))
  calls = _recorded(monkeypatch)
  engine._conan_install(project / "profile", project / "build")
  assert sum(argv[:2] == ["conan", "export"] for argv in calls) == 3


def test_the_parser_beside_the_conanfile_is_kept_the_drivers(project,
                                                            monkeypatch):
  _recorded(monkeypatch)
  copy = project / "buildutil_requires.py"
  engine._conan_install(project / "profile", project / "build")
  assert copy.read_text() == packaging.REQUIRES_PARSER.read_text()
  copy.write_text("stale")
  engine._conan_install(project / "profile", project / "build")
  assert copy.read_text() == packaging.REQUIRES_PARSER.read_text()


def test_a_conanfile_older_than_host_packages_is_refused(project,
                                                          monkeypatch):
  calls = _recorded(monkeypatch)
  old = TEMPLATE.read_text().replace("_HOST_REQUIRES = 1\n", "")
  (project / "conanfile.py").write_text(old)
  with pytest.raises(SystemExit, match="buildutil init"):
    engine._conan_install(project / "profile", project / "build")
  assert calls == []


def test_a_SYSTEM_line_for_another_platform_neither_refuses_nor_exports(
    project, monkeypatch):
  (project / "sources" / "CMakeLists.txt").write_text(
    'Require(SDL2 VERSION ">=2.28" SYSTEM PLATFORM Emscripten)\n')
  old = TEMPLATE.read_text().replace("_HOST_REQUIRES = 1\n", "")
  (project / "conanfile.py").write_text(old)
  calls = _recorded(monkeypatch)
  engine._conan_install(project / "profile", project / "build")
  assert [argv[:2] for argv in calls] == [["conan", "install"]]


def test_init_regenerates_a_conanfile_older_than_host_packages(project):
  old = TEMPLATE.read_text().replace("_HOST_REQUIRES = 1\n", "")
  (project / "conanfile.py").write_text(old)
  initcmd._upgrade_conanfile(project, "demo", "DEMO")
  assert packaging.knows_host_requires(
    (project / "conanfile.py").read_text())
  assert (project / "conanfile.py.bak").read_text() == old
