"""Every verb that builds builds Release unless a flag says otherwise.

User, 2026-09-24, on inheriting the last build's profile: "seems like it's
just gonna trip you up every time"."""
from pathlib import Path

import pytest

from buildutil import engine


@pytest.mark.parametrize("flags, expected", [
  ((False, False, False), "Release"), ((True, False, False), "Release"),
  ((False, True, False), "Debug"), ((False, False, True), "RelWithDebInfo"),
  ((True, True, False), "RelWithDebInfo"),
])
def test_explicit_build_types(flags, expected):
  assert engine._resolve_build_type(*flags) == expected


def test_a_verb_may_name_its_own_default_for_no_flag():
  assert engine._resolve_build_type(False, False, False, default="Debug") == "Debug"
  assert engine._resolve_build_type(True, False, False, default="Debug") == "Release"


@pytest.mark.parametrize("flags, origin", [
  ((False, False, False), "default"), ((True, False, False), "--release"),
  ((False, True, False), "--debug"), ((False, False, True), "--relwithdebinfo"),
  ((True, True, False), "--release --debug"),
])
def test_the_profile_origin_is_default_or_the_flags(flags, origin):
  assert engine._profile_origin(*flags) == origin


@pytest.mark.parametrize("flags", [(True, False, True), (False, True, True),
                                    (True, True, True)])
def test_conflicting_build_types(flags):
  import typer
  with pytest.raises(typer.BadParameter, match="--relwithdebinfo"):
    engine._resolve_build_type(*flags)


@pytest.fixture
def profile_cli(tmp_path, monkeypatch):
  import importlib
  from typer.testing import CliRunner
  from buildutil.app import app
  modules = [importlib.import_module(f"buildutil.commands.{name}")
             for name in ("build", "test", "run", "godbolt", "bench", "analyze")]
  monkeypatch.chdir(tmp_path)
  monkeypatch.setenv("CI", "1")
  calls = []

  def settings(build_type):
    return dict(arch="x86_64", os="Linux", compiler="gcc", build_type=build_type)

  def install(profile, build_dir, **kwargs):
    calls.append(build_dir)
    raise RuntimeError("profile captured")

  for module in [engine, *modules]:
    monkeypatch.setattr(module, "_detect_settings", settings)
    monkeypatch.setattr(module, "_ensure_profile", lambda s: Path("profile"))
    monkeypatch.setattr(module, "_module_linkage", lambda: "static")
    monkeypatch.setattr(module, "_conan_install", install)
    monkeypatch.setattr(module, "_enter", lambda *a: None)
    monkeypatch.setattr(module, "_select_compiler", lambda *a: None)
    monkeypatch.setattr(module, "_warn_dependency_upload_skipped", lambda *a: None)
  monkeypatch.setitem(modules[4].PROJECT, "bench_suite", "")
  return lambda args: CliRunner().invoke(app, args), calls


@pytest.mark.parametrize("verb", ["build", "test", "run", "godbolt", "bench",
                                 "analyze", "deps"])
@pytest.mark.parametrize("flags", [["--relwithdebinfo"], ["--release", "--debug"]])
def test_each_verb_selects_relwithdebinfo_directory(profile_cli, verb, flags):
  invoke, calls = profile_cli
  args = [verb, *flags] + (["--target", "hello"] if verb == "run" else [])
  result = invoke(args)
  assert calls == [Path("_build/x86_64-linux-gcc-relwithdebinfo")], result.output
  assert str(result.exception) == "profile captured"


@pytest.mark.parametrize("verb", ["build", "test", "run", "godbolt", "bench",
                                 "analyze", "deps"])
@pytest.mark.parametrize("flag", ["--release", "--debug"])
def test_each_verb_refuses_conflicting_profiles(profile_cli, verb, flag):
  invoke, calls = profile_cli
  result = invoke([verb, "--relwithdebinfo", flag])
  assert result.exit_code == 2, result.output
  assert "cannot be combined" in result.output
  assert calls == []


@pytest.mark.parametrize("suite", ["", "demo.bench"])
@pytest.mark.parametrize("flags, suffix", [([], "release"),
  (["--debug"], "debug"), (["--relwithdebinfo"], "relwithdebinfo"),
  (["--release", "--debug"], "relwithdebinfo"), (["--perf"], "relwithdebinfo")])
def test_bench_profiles_in_both_modes(profile_cli, monkeypatch, suite, flags, suffix):
  from buildutil.commands import bench
  monkeypatch.setitem(bench.PROJECT, "bench_suite", suite)
  invoke, calls = profile_cli
  result = invoke(["bench", *flags])
  assert calls == [Path(f"_build/x86_64-linux-gcc-{suffix}")], result.output


RELEASE_DIR = Path("_build/x86_64-linux-gcc-release")
DEBUG_DIR = Path("_build/x86_64-linux-gcc-debug")
RELEASE_VERBS = ["build", "test", "run", "godbolt", "bench"]


def _bare(verb):
  return [verb, "--target", "hello"] if verb == "run" else [verb]


def _debug_tree():
  """What a `--debug` build leaves, stamp of the retired inheritance included."""
  (DEBUG_DIR / "bin").mkdir(parents=True)
  (DEBUG_DIR / "CMakeCache.txt").write_text("CMAKE_BUILD_TYPE:STRING=Debug\n")
  (DEBUG_DIR.parent / ".last-build-type").write_text("Debug\n")


@pytest.mark.parametrize("verb", RELEASE_VERBS)
def test_a_bare_verb_in_a_debug_tree_builds_release(profile_cli, verb):
  invoke, calls = profile_cli
  _debug_tree()
  result = invoke(_bare(verb))
  assert calls == [RELEASE_DIR], result.output


@pytest.mark.parametrize("verb", RELEASE_VERBS)
def test_a_debug_build_does_not_carry_into_the_next_bare_verb(profile_cli, verb):
  invoke, calls = profile_cli
  invoke(["build", "--debug"])
  result = invoke(_bare(verb))
  assert calls == [DEBUG_DIR, RELEASE_DIR], result.output


def test_analyze_keeps_its_debug_lane_and_never_steers_a_later_test(profile_cli):
  invoke, calls = profile_cli
  analyzed = invoke(["analyze"])
  tested = invoke(["test"])
  assert calls == [DEBUG_DIR, RELEASE_DIR], analyzed.output + tested.output


@pytest.mark.parametrize("verb", ["build", "test"])
@pytest.mark.parametrize("flags, line", [
  ([], "profile: x86_64-linux-gcc-release (default)"),
  (["--debug"], "profile: x86_64-linux-gcc-debug (--debug)"),
])
def test_the_profile_line_says_default_or_names_the_flag(profile_cli, verb,
                                                        flags, line):
  invoke, _ = profile_cli
  _debug_tree()
  assert line in invoke([verb, *flags]).output.splitlines()


def test_a_build_leaves_no_profile_stamp_for_a_later_verb(profile_cli):
  invoke, _ = profile_cli
  invoke(["build", "--debug"])
  assert list(Path("_build").glob(".*")) == []
