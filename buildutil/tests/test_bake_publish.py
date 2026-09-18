"""publish --bake-buildutil: the package carries its own build driver,
so a consumer's --build=missing builds from source in the conan cache.

Two seams, tested without conan or git:
- the RECIPE: build() switches from the refusal to invoking the baked
  driver's `cache-build` exactly when .buildutil/ rode along in
  exports_sources (the template is exec'd against stub conan modules,
  the established pattern);
- the COMMAND: --bake-buildutil vendors the running buildutil into the
  project (installcmd.vendor_into) before packaging, and the default
  does not."""
import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from buildutil import initcmd, packaging

TEMPLATES = Path(initcmd.__file__).resolve().parent / "templates"


# ------------------------------------------------- the recipe seam --

def _conan_stubs():
  for name, attrs in (("conan", {"ConanFile": object}),
                      ("conan.tools", {}),
                      ("conan.tools.cmake", {"CMakeDeps": object,
                                             "CMakeToolchain": object,
                                             "cmake_layout": lambda *a: None})):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
      setattr(mod, k, v)
    sys.modules.setdefault(name, mod)


def _recipe(tmp_path):
  _conan_stubs()
  (tmp_path / "buildutil.toml").write_text(
    '[package]\nkind = "library"\nname = "s"\n')
  src = (TEMPLATES / "project" / "conanfile.py").read_text().replace(
    "@CONAN_NAME@", "fallbackname").replace("@CMAKE_OPTION_PREFIX@", "X")
  path = tmp_path / "conanfile.py"
  path.write_text(src)
  spec = importlib.util.spec_from_file_location(
    f"recipe_{tmp_path.name}", path)
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  recipe = mod.ProjectRecipe()
  recipe.source_folder = str(tmp_path)
  recipe.recipe_folder = str(tmp_path)
  recipe.generators_folder = str(tmp_path / "gen")
  recipe.build_folder = str(tmp_path / "bld")
  recipe.settings = SimpleNamespace(build_type="Debug")
  recipe.options = SimpleNamespace(get_safe=lambda name: None)
  return recipe


def test_a_baked_package_builds_in_cache_through_the_vendored_driver(
    tmp_path):
  recipe = _recipe(tmp_path)
  vendored = tmp_path / ".buildutil" / "buildutil"
  vendored.mkdir(parents=True)
  (vendored / "__main__.py").write_text("")
  calls = []
  recipe.run = lambda cmd, cwd=None: calls.append((cmd, cwd))
  recipe.build()
  assert len(calls) == 1
  cmd, cwd = calls[0]
  assert "-m buildutil cache-build" in cmd
  assert "--build-type Debug" in cmd
  assert "--linkage static" in cmd            # no shared option set
  assert str(tmp_path / "gen" / "conan_toolchain.cmake") in cmd
  assert cwd == str(tmp_path)


def test_the_baked_lane_maps_the_shared_option_to_linkage(tmp_path):
  recipe = _recipe(tmp_path)
  vendored = tmp_path / ".buildutil" / "buildutil"
  vendored.mkdir(parents=True)
  (vendored / "__main__.py").write_text("")
  recipe.options = SimpleNamespace(get_safe=lambda name: True)
  calls = []
  recipe.run = lambda cmd, cwd=None: calls.append(cmd)
  recipe.build()
  assert "--linkage shared" in calls[0]


def test_an_unbaked_package_still_refuses_the_cache_build(tmp_path):
  recipe = _recipe(tmp_path)
  errors = types.ModuleType("conan.errors")
  class ConanException(Exception): ...
  errors.ConanException = ConanException
  sys.modules["conan.errors"] = errors
  with pytest.raises(ConanException, match="bake-buildutil"):
    recipe.build()


# ------------------------------------------------ the command seam --
# Staging is REAL here (the actual package copied into tmp): what these
# pin is the lifecycle — present exactly while conan snapshots
# exports_sources, gone after, and an author's own vendored copy never
# touched. Forcing `buildutil install` on a publishing author was the
# owner's explicit objection to the first cut.

def _quiet_publish(monkeypatch, tmp_path, export=None):
  """Silence every heavy seam publish crosses; REPO_ROOT moves to
  tmp_path so staging lands there. Returns the record."""
  from buildutil.commands import publish as pub
  record = {"exported": False, "staged_at_export": None}

  def default_export(*a, **k):
    record["exported"] = True
    record["staged_at_export"] = (
      tmp_path / ".buildutil" / "buildutil" / "__main__.py").is_file()
    return "s/1.2.3.4"

  for name in ("_enter", "_select_compiler", "_conan_install",
               "_cmake_configure", "_cmake_build", "_upload_to_remote",
               "_status"):
    monkeypatch.setattr(pub, name, lambda *a, **k: None)
  monkeypatch.setattr(pub, "_detect_settings", lambda bt: {"bt": bt})
  monkeypatch.setattr(pub, "_ensure_profile", lambda s: Path("prof"))
  monkeypatch.setattr(pub, "_profile_name", lambda s: "prof")
  monkeypatch.setattr(pub, "_host_build_profiles", lambda p: (p, p))
  monkeypatch.setattr(pub, "REPO_ROOT", tmp_path)
  monkeypatch.setattr(packaging, "configured", lambda: True)
  monkeypatch.setattr(packaging, "ranged_runtime_requires", lambda: [])
  monkeypatch.setattr(packaging, "resolve_version",
                      lambda bump, override="": ("1.2.3.4", lambda: None))
  monkeypatch.setattr(packaging, "ref", lambda v: f"s/{v}")
  monkeypatch.setattr(packaging, "shared_requested", lambda: False)
  monkeypatch.setattr(packaging, "has_package_test", lambda: False)
  monkeypatch.setattr(packaging, "export_pkg", export or default_export)
  return record


def _publish(**overrides):
  from buildutil.commands import publish as pub
  kwargs = dict(release=True, version="1.2.3.4", no_autoincrement=False,
                no_upload=True, allow_version_ranges=False,
                bake_buildutil=False, conan_home=None, compiler="auto")
  kwargs.update(overrides)
  pub.publish(**kwargs)


def test_bake_stages_for_the_export_and_discards_after(monkeypatch,
                                                       tmp_path):
  record = _quiet_publish(monkeypatch, tmp_path)
  _publish(bake_buildutil=True)
  assert record["exported"]
  assert record["staged_at_export"] is True
  assert not (tmp_path / ".buildutil").exists()


def test_the_default_publish_never_stages(monkeypatch, tmp_path):
  record = _quiet_publish(monkeypatch, tmp_path)
  _publish()
  assert record["exported"]
  assert record["staged_at_export"] is False
  assert not (tmp_path / ".buildutil").exists()


def test_a_vendored_project_ships_its_own_copy_untouched(monkeypatch,
                                                         tmp_path):
  vendored = tmp_path / ".buildutil" / "buildutil"
  vendored.mkdir(parents=True)
  (vendored / "__main__.py").write_text("# the project's own copy\n")
  record = _quiet_publish(monkeypatch, tmp_path)
  _publish(bake_buildutil=True)
  assert record["staged_at_export"] is True
  # untouched: same content, no VENDORED refresh, still there after
  assert (vendored / "__main__.py").read_text() == \
    "# the project's own copy\n"
  assert not (vendored / "VENDORED").exists()


def test_a_failing_export_still_discards_the_staged_copy(monkeypatch,
                                                         tmp_path):
  def exploding_export(*a, **k):
    raise RuntimeError("export died")
  _quiet_publish(monkeypatch, tmp_path, export=exploding_export)
  with pytest.raises(RuntimeError):
    _publish(bake_buildutil=True)
  assert not (tmp_path / ".buildutil").exists()
