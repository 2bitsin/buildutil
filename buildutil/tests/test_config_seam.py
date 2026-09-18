"""The project seam: buildutil.toml IS the repo root marker, and every
project-specific knob flows from it. config computes at import time, so
each probe runs a fresh interpreter in a controlled cwd — reload tricks
would test importlib, not the seam."""
import json
import os
import subprocess
import sys
from pathlib import Path

PKG_PARENT = str(Path(__file__).resolve().parents[2])

PROBE = (
  "import json, buildutil.config as c;"
  "print(json.dumps({'root': str(c.REPO_ROOT), 'have': c.HAVE_PROJECT,"
  " 'name': c.PROJECT_NAME, 'cmake': c.CMAKE_PREFIX,"
  " 'mod': c.MODULE_DEFINE_PREFIX, 'deps': c.VENV_DEPS,"
  " 'bridges': c.PROJECT['coverage_bridge_dirs'], 'bench': c.PROJECT['bench_suite'], 'copts': c.PROJECT['conan_options_os'], 'cconf': c.PROJECT['conan_conf'], 'covx': c.PROJECT['coverage_exclude'], 'export': c.PROJECT['export_module_headers'], 'dormant': c.PROJECT['modules_dormant'], 'rns': c.PROJECT['reflect_namespace'], 'rscan': c.PROJECT['reflect_scan'], 'rinc': c.PROJECT['reflect_include'], 'rann': c.PROJECT['reflect_annotation'], 'rmac': c.PROJECT['reflect_macros']}))"
)


def _probe(cwd, env_extra=None):
  env = {**os.environ, "PYTHONPATH": PKG_PARENT, **(env_extra or {})}
  env.pop("BUILDUTIL_ROOT", None) if not (env_extra or {}).get(
    "BUILDUTIL_ROOT") else None
  out = subprocess.check_output(
    [sys.executable, "-c", PROBE], cwd=cwd, env=env, text=True)
  return json.loads(out)


def test_root_is_nearest_ancestor_with_toml(tmp_path):
  (tmp_path / "buildutil.toml").write_text("")
  deep = tmp_path / "a" / "b"
  deep.mkdir(parents=True)
  got = _probe(deep)
  assert got["have"] and got["root"] == str(tmp_path)


def test_no_toml_means_no_project(tmp_path):
  got = _probe(tmp_path)
  assert not got["have"]
  # and the defaults hold: generic prefixes, driver-only deps
  assert got["name"] == "project"
  assert got["cmake"] == "BUILDUTIL" and got["mod"] == "MOD"
  assert not any("pybind11" in d for d in got["deps"])
  assert got["bench"] == ""  # no suite: bench runs the *-benches binaries
  assert got["copts"] == {} and got["cconf"] == [] and got["covx"] == []


def test_env_root_wins_over_walk(tmp_path):
  near = tmp_path / "near"
  near.mkdir()
  (near / "buildutil.toml").write_text("")
  far = tmp_path / "far"
  far.mkdir()
  (far / "buildutil.toml").write_text("")
  got = _probe(near, {"BUILDUTIL_ROOT": str(far)})
  assert got["root"] == str(far)


def test_toml_supplies_every_knob(tmp_path):
  (tmp_path / "buildutil.toml").write_text(
    '[project]\n'
    'name = "bossdeux"\n'
    'cmake_option_prefix = "BOSSDEUX"\n'
    'module_define_prefix = "BDX"\n'
    '[venv]\n'
    'extra_deps = ["pybind11==3.0.4", "capstone==5.0.7"]\n'
    '[coverage]\n'
    'bridge_dirs = ["sources/bdx86emu"]\n'
    '[bench]\n'
    'suite = "inspector.bench"\n'
    '[conan]\n'
    'options_linux = ["sdl/*:x11=False"]\n'
    'conf = ["lexy/*:tools.build:cxxflags+=[\'-w\']"]\n')
  got = _probe(tmp_path)
  assert got["name"] == "bossdeux"
  assert got["cmake"] == "BOSSDEUX" and got["mod"] == "BDX"
  assert "pybind11==3.0.4" in got["deps"] and "capstone==5.0.7" in got["deps"]
  assert got["bridges"] == ["sources/bdx86emu"]
  assert got["bench"] == "inspector.bench"
  assert got["copts"] == {"linux": ["sdl/*:x11=False"]}
  assert got["cconf"] == ["lexy/*:tools.build:cxxflags+=['-w']"]


def test_empty_toml_marks_root_takes_defaults(tmp_path):
  (tmp_path / "buildutil.toml").write_text("")
  got = _probe(tmp_path)
  assert got["have"] and got["cmake"] == "BUILDUTIL"
  # the include-scope rule is opt-IN: silence means qualified includes
  assert got["export"] is False


def test_default_dormant_modules_come_from_the_toml(tmp_path):
  """The committed half of the module state — what a clean clone sees."""
  (tmp_path / "buildutil.toml").write_text(
    '[modules]\ndormant = ["as", "bdiff", "re2c"]\n')
  assert _probe(tmp_path)["dormant"] == ["as", "bdiff", "re2c"]


def test_export_module_headers_is_a_project_wide_opt_in(tmp_path):
  """A ported tree declares it once, here — never per module."""
  (tmp_path / "buildutil.toml").write_text(
    "[cmake]\nexport_module_headers = true\n")
  assert _probe(tmp_path)["export"] is True


def test_the_reflect_annotation_switch_defaults_to_todays_behaviour(tmp_path):
  # PHASE ONE: every site in the fleet writes `_Label(x) SYSTEM`, which is a
  # LEADING enumerator attribute the moment the macro expands to one -- and
  # that is ill-formed. An upgrade must therefore change nothing by itself.
  (tmp_path / "buildutil.toml").write_text(
    '[cmake]\nextensions = ["reflect"]\n')
  got = _probe(tmp_path)
  assert got["rann"] == "macro"
  assert got["rmac"] == "auto"


def test_the_reflect_annotation_switch_is_project_policy(tmp_path):
  # committed, like every other [reflect] key: a fresh clone that quietly
  # built something else is the failure this table exists to prevent
  (tmp_path / "buildutil.toml").write_text(
    '[cmake]\nextensions = ["reflect"]\n'
    '[reflect]\nannotation = "attribute"\nmacros = "sources/labels.hpp"\n')
  got = _probe(tmp_path)
  assert got["rann"] == "attribute"
  assert got["rmac"] == "sources/labels.hpp"
