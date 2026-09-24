"""Conan packaging (owner req): init wizard/switches record a committed
[package] choice (kind + name — NEVER a version: the version never
lives in the package source), the recipe grows packaging methods from
that same section, test_package/ is scaffolded per kind, old projects
upgrade via the same wizard, publish derives the version from git tags
plus a bumped build number, and the publish/test plumbing assembles the
right conan commands. Everything below runs without conan or git — the
calls go through packaging.py's injectable seams, and the recipe is
exec'd against stub conan modules (the established template pattern)."""
import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from buildutil import initcmd, packaging

TEMPLATES = Path(initcmd.__file__).resolve().parent / "templates"


# ------------------------------------------------------- the recipe --

def _conan_stubs():
  for name, attrs in (("conan", {"ConanFile": object}),
                      ("conan.tools", {}),
                      ("conan.errors", {
                        "ConanException": type("ConanException", (Exception,), {}),
                        "ConanInvalidConfiguration":
                          type("ConanInvalidConfiguration", (Exception,), {})}),
                      ("conan.tools.build", {"cross_building": lambda conanfile: False}),
                      ("conan.tools.scm", {"Version": object}),
                      ("conan.tools.cmake", {"CMakeDeps": object,
                                             "CMakeToolchain": object,
                                             "cmake_layout": lambda *a: None})):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
      setattr(mod, k, v)
    sys.modules.setdefault(name, mod)


def _recipe_module(tmp_path, toml_text=None):
  """Render the conanfile template INTO tmp_path (beside an optional
  buildutil.toml) and import it — _package_section reads beside
  __file__, exactly as in a real project root."""
  _conan_stubs()
  if toml_text is not None:
    (tmp_path / "buildutil.toml").write_text(toml_text)
  src = (TEMPLATES / "project" / "conanfile.py").read_text().replace(
    "@CONAN_NAME@", "fallbackname").replace("@CMAKE_OPTION_PREFIX@", "X")
  path = tmp_path / "conanfile.py"
  path.write_text(src)
  spec = importlib.util.spec_from_file_location(
    f"recipe_{tmp_path.name}", path)
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  mod.cross_building = lambda conanfile: False
  return mod


def test_recipe_without_package_section_is_the_old_consumer(tmp_path):
  mod = _recipe_module(tmp_path, '[project]\nname = "x"\n')
  assert mod.ProjectRecipe.name == "fallbackname"
  assert not hasattr(mod.ProjectRecipe, "exports_sources")


def test_recipe_reads_package_identity_from_the_toml(tmp_path):
  mod = _recipe_module(
    tmp_path, '[package]\nkind = "library"\nname = "serialize"\n')
  assert mod.ProjectRecipe.name == "serialize"
  assert mod.ProjectRecipe.package_type == "library"
  # the conan-standard shared option, linked to module_linkage
  assert mod.ProjectRecipe.options == {"shared": [True, False]}
  assert mod.ProjectRecipe.default_options == {"shared": False}
  assert "sources/*" in mod.ProjectRecipe.exports_sources


def test_generated_junk_under_sources_is_excluded_from_the_export(tmp_path):
  """conan hashes what it exports into the RECIPE REVISION, and sources/*
  goes in wholesale -- so a generated file that exists on one machine and
  not another splits a release across two revisions, and consumers see
  only the latest, which hides every binary published under the other.

  One package shipped exactly that: a second revision from its
  Windows runner whose manifest differed by five
  cmake_test_discovery_<hash>.json files. The machinery no longer writes
  them into the source tree; these patterns are the belt to that brace,
  and they cover the same class -- __pycache__, .pyc -- that every publish
  job already sets PYTHONDONTWRITEBYTECODE for."""
  mod = _recipe_module(
    tmp_path, '[package]\nkind = "library"\nname = "serialize"\n')
  excluded = set(mod.ProjectRecipe.exports_sources)
  for pattern in ("!sources/**/cmake_test_discovery_*.json",
                  "!sources/**/__pycache__/**",
                  "!sources/**/*.pyc"):
    assert pattern in excluded, pattern
  # the exclusions must come after the includes -- conan applies them in
  # order, and a `!` before its include matches nothing
  patterns = list(mod.ProjectRecipe.exports_sources)
  assert patterns.index("sources/*") < min(
    patterns.index(p) for p in patterns if p.startswith("!"))


def test_recipe_adopts_the_driver_version_never_its_own(tmp_path):
  """set_version: the CLI --version the driver passes wins; without one
  (consumer flows) the placeholder applies. No version is ever read
  from any committed file."""
  mod = _recipe_module(
    tmp_path, '[package]\nkind = "library"\nname = "s"\n')
  r = mod.ProjectRecipe()
  r.version = "1.2.3.4"                       # what --version delivers
  r.set_version()
  assert r.version == "1.2.3.4"
  r2 = mod.ProjectRecipe()
  r2.version = None
  r2.set_version()
  assert r2.version == "0.0.0"


def test_recorded_none_is_not_a_packaging_config(tmp_path):
  mod = _recipe_module(tmp_path, '[package]\nkind = "none"\n')
  assert mod.ProjectRecipe.name == "fallbackname"


def test_package_info_discovers_the_library_mirror(tmp_path):
  mod = _recipe_module(
    tmp_path, '[package]\nkind = "library"\nname = "s"\n')
  pkg = tmp_path / "pkg"
  (pkg / "ser" / "core").mkdir(parents=True)
  (pkg / "ser" / "core" / "libserial.a").write_bytes(b"!<arch>\n")
  (pkg / "include" / "ser").mkdir(parents=True)
  (pkg / "include" / "ser" / "api.h").write_text("")
  r = mod.ProjectRecipe()
  r.package_folder = str(pkg)
  r.cpp_info = SimpleNamespace(libs=None, libdirs=None, includedirs=None)
  r.package_info()
  assert r.cpp_info.libs == ["serial"]
  assert r.cpp_info.libdirs == ["ser/core"]
  assert r.cpp_info.includedirs == ["include"]


def test_cache_source_build_is_refused_with_the_story(tmp_path):
  mod = _recipe_module(
    tmp_path, '[package]\nkind = "library"\nname = "s"\n')
  ConanException = mod.ConanException
  class Settings:
    def get_safe(self, name):
      return {"build_type": "Debug", "compiler": "gcc",
              "compiler.version": "14", "compiler.cppstd": "23"}.get(name)
  r = mod.ProjectRecipe()
  r.version = "0.3.0.1"
  r.settings = Settings()
  with pytest.raises(ConanException) as excinfo:
    r.build()
  story = str(excinfo.value)
  assert "buildutil publish" in story
  # the refusal names the consumer's OWN profile, tells them to list
  # what is published, and puts the build_type miss BEFORE the
  # package_id-drift explanation — a Debug consumer of a Release-only
  # publish must not be sent on a ranged-dependency hunt.
  assert "build_type=Debug" in story
  assert 'conan list "s/0.3.0.1:*"' in story
  assert story.index("build_type=Debug") < story.index("package_id")


def test_the_refusal_survives_a_bare_recipe_without_settings(tmp_path):
  """conan populates settings/version late; the refusal must not crash
  when raised from a barely-constructed recipe (that would replace the
  story with an AttributeError)."""
  mod = _recipe_module(
    tmp_path, '[package]\nkind = "library"\nname = "s"\n')
  ConanException = mod.ConanException
  with pytest.raises(ConanException, match="buildutil publish"):
    mod.ProjectRecipe().build()


# --------------------------------------------- the version model --

@pytest.fixture(autouse=True)
def _no_ambient_remote(monkeypatch):
  for name in ("CONAN_REMOTE_URL", "CONAN_REMOTE_NAME", "CI_ARTIFACTORY_HREF",
               "CI_ARTIFACTORY_NAME"):
    monkeypatch.delenv(name, raising=False)


def _fake_project(monkeypatch, **cfg):
  from buildutil import config
  base = {"package_kind": "", "package_name": "", "name": "proj"}
  base.update(cfg)
  monkeypatch.setattr(config, "PROJECT", {**config.PROJECT, **base})


def _git_tags(*tags):
  def fake_run(cmd, **kw):
    assert cmd[:2] == ["git", "tag"]
    return SimpleNamespace(returncode=0,
                           stdout="".join(t + "\n" for t in tags))
  return fake_run


def test_version_is_last_semver_tag_plus_publish_build(tmp_path, monkeypatch):
  _fake_project(monkeypatch, package_kind="library", package_name="s")
  git = _git_tags("wip-branch-tag", "v1.2.3", "1.0.0")
  v1, commit = packaging.resolve_version(bump=True, git=git, root=tmp_path)
  assert v1 == "1.2.3.1"        # non-semver skipped, v-prefix stripped
  commit()
  v2, commit2 = packaging.resolve_version(bump=True, git=git, root=tmp_path)
  assert v2 == "1.2.3.2", "every publish bumps the build number"
  commit2()
  # --no-version-autoincrement: republish the current number
  v3, _ = packaging.resolve_version(bump=False, git=git, root=tmp_path)
  assert v3 == "1.2.3.2"


def test_an_uncommitted_bump_consumes_nothing(tmp_path, monkeypatch):
  """A failed or dry-run publish never eats a build number: the bump
  only persists through the commit callback."""
  _fake_project(monkeypatch, package_kind="library", package_name="s")
  git = _git_tags("1.0.0")
  v1, _never_committed = packaging.resolve_version(
    bump=True, git=git, root=tmp_path)
  v2, _ = packaging.resolve_version(bump=True, git=git, root=tmp_path)
  assert v1 == v2 == "1.0.0.1"


def test_a_new_tag_resets_the_build_counter(tmp_path, monkeypatch):
  _fake_project(monkeypatch, package_kind="library", package_name="s")
  _, commit = packaging.resolve_version(
    bump=True, git=_git_tags("1.0.0"), root=tmp_path)
  commit()
  v, _ = packaging.resolve_version(
    bump=True, git=_git_tags("2.0.0", "1.0.0"), root=tmp_path)
  assert v == "2.0.0.1"


def test_version_override_wins_and_persists_nothing(tmp_path, monkeypatch):
  _fake_project(monkeypatch, package_kind="library", package_name="s")
  v, commit = packaging.resolve_version(
    bump=True, override="7.7.7", git=_git_tags("1.0.0"), root=tmp_path)
  assert v == "7.7.7"
  commit()
  assert not packaging._state_file(tmp_path).exists()


def test_no_tag_prompts_at_a_tty_and_the_answer_sticks(tmp_path, monkeypatch):
  _fake_project(monkeypatch, package_kind="library", package_name="s")
  answers = iter(["not-a-version", "3.1.4"])
  v, commit = packaging.resolve_version(
    bump=True, git=_git_tags(), ask=lambda p: next(answers),
    isatty=True, root=tmp_path)
  assert v == "3.1.4.1", "invalid input re-asked, then accepted"
  commit()
  # saved: the next resolve needs no prompt and no tag
  v2, _ = packaging.resolve_version(
    bump=False, git=_git_tags(),
    ask=lambda p: (_ for _ in ()).throw(AssertionError("re-prompted")),
    isatty=True, root=tmp_path)
  assert v2 == "3.1.4.1"


def test_no_tag_no_tty_is_a_refusal(tmp_path, monkeypatch):
  _fake_project(monkeypatch, package_kind="library", package_name="s")
  with pytest.raises(SystemExit, match="cannot infer"):
    packaging.resolve_version(bump=True, git=_git_tags(),
                              isatty=False, root=tmp_path)


def test_the_refusal_never_recommends_a_key_nothing_reads(tmp_path,
                                                          monkeypatch):
  """[package] has no version key: config parses kind, name and nothing
  else, and [package] takes no unknown-key check, so a version line
  written on this advice would silently do nothing."""
  _fake_project(monkeypatch, package_kind="library", package_name="s")
  with pytest.raises(SystemExit) as refusal:
    packaging.resolve_version(bump=True, git=_git_tags(),
                              isatty=False, root=tmp_path)
  assert "[package] version" not in str(refusal.value)
  assert "--version" in str(refusal.value)


# --------------------------------- the build number and the remote --
# A counter kept per checkout makes two boxes publish the same
# x.y.z.1: conan resolves a range to the HIGHEST build number, so the
# second publish is shadowed by the first — older code winning, which
# is what happened to every oxbox minor back to 0.14.

def _remote_versions(*refs, returncode=0, stdout=None):
  """A `conan list --format=json` answer naming these references."""
  def fake_run(cmd, **kw):
    assert cmd[:2] == ["conan", "list"], cmd
    body = json.dumps({"mirror": {ref: {"revisions": {}} for ref in refs}})
    return SimpleNamespace(returncode=returncode,
                           stdout=body if stdout is None else stdout)
  return fake_run


@pytest.fixture
def remote(monkeypatch):
  monkeypatch.setenv("CONAN_REMOTE_NAME", "mirror")
  monkeypatch.setenv("CONAN_REMOTE_URL", "https://repo.example/conan")


def test_the_build_number_seeds_from_the_remote(tmp_path, monkeypatch,
                                                remote):
  """The other box published .177 from its own counter; this box has
  never published at all and must not answer .1."""
  _fake_project(monkeypatch, package_kind="library", package_name="s")
  v, _ = packaging.resolve_version(
    bump=True, git=_git_tags("0.19.0"), root=tmp_path,
    conan=_remote_versions("s/0.19.0.176", "s/0.19.0.177"))
  assert v == "0.19.0.178"


def test_the_local_counter_still_wins_when_it_is_ahead(tmp_path,
                                                       monkeypatch, remote):
  """An upload that did not reach the remote's index yet must not hand
  the same number out twice."""
  _fake_project(monkeypatch, package_kind="library", package_name="s")
  _, commit = packaging.resolve_version(
    bump=True, git=_git_tags("1.0.0"), root=tmp_path,
    conan=_remote_versions("s/1.0.0.1", "s/1.0.0.2"))
  commit()                                        # local state at .3
  v, _ = packaging.resolve_version(
    bump=True, git=_git_tags("1.0.0"), root=tmp_path,
    conan=_remote_versions("s/1.0.0.1"))
  assert v == "1.0.0.4"


def test_another_base_on_the_remote_is_not_this_base(tmp_path, monkeypatch,
                                                     remote):
  _fake_project(monkeypatch, package_kind="library", package_name="s")
  v, _ = packaging.resolve_version(
    bump=True, git=_git_tags("2.0.0"), root=tmp_path,
    conan=_remote_versions("s/1.9.0.42", "other/2.0.0.9", "s/2.0.0.3"))
  assert v == "2.0.0.4"


def test_an_unreachable_remote_falls_back_to_local_state(tmp_path,
                                                         monkeypatch,
                                                         remote, capsys):
  _fake_project(monkeypatch, package_kind="library", package_name="s")
  v, _ = packaging.resolve_version(
    bump=True, git=_git_tags("1.0.0"), root=tmp_path,
    conan=_remote_versions(returncode=1, stdout="ERROR: no route to host"))
  assert v == "1.0.0.1"
  assert "remote" in capsys.readouterr().out


def test_an_unparseable_answer_falls_back_to_local_state(tmp_path,
                                                         monkeypatch,
                                                         remote):
  _fake_project(monkeypatch, package_kind="library", package_name="s")
  v, _ = packaging.resolve_version(
    bump=True, git=_git_tags("1.0.0"), root=tmp_path,
    conan=_remote_versions(stdout="not json at all"))
  assert v == "1.0.0.1"


def test_no_remote_configured_asks_nothing(tmp_path, monkeypatch):
  _fake_project(monkeypatch, package_kind="library", package_name="s")
  monkeypatch.delenv("CONAN_REMOTE_URL", raising=False)
  monkeypatch.delenv("CI_ARTIFACTORY_HREF", raising=False)
  def refuse(cmd, **kw):
    raise AssertionError("queried a remote that is not configured")
  v, _ = packaging.resolve_version(
    bump=True, git=_git_tags("1.0.0"), root=tmp_path, conan=refuse)
  assert v == "1.0.0.1"


def test_holding_the_number_never_asks_the_remote(tmp_path, monkeypatch,
                                                  remote):
  """--no-version-autoincrement republishes the number this checkout
  last used; seeding it from the remote would publish a different one."""
  _fake_project(monkeypatch, package_kind="library", package_name="s")
  def refuse(cmd, **kw):
    raise AssertionError("queried the remote while holding the number")
  v, _ = packaging.resolve_version(
    bump=False, git=_git_tags("1.0.0"), root=tmp_path, conan=refuse)
  assert v == "1.0.0.1"


def test_an_override_never_asks_the_remote(tmp_path, monkeypatch, remote):
  _fake_project(monkeypatch, package_kind="library", package_name="s")
  def refuse(cmd, **kw):
    raise AssertionError("queried the remote for an explicit version")
  v, _ = packaging.resolve_version(
    bump=True, override="9.9.9.9", git=_git_tags("1.0.0"), root=tmp_path,
    conan=refuse)
  assert v == "9.9.9.9"


# ------------------------------------------ ranged runtime Requires --

def _sources_with(tmp_path, text):
  (tmp_path / "sources").mkdir(exist_ok=True)
  (tmp_path / "sources" / "CMakeLists.txt").write_text(text)
  return tmp_path


def test_ranged_runtime_requires_flags_only_the_drifters(tmp_path):
  """The BossDeux failure: the published binary embedded pugixml 1.16,
  a consumer's warm cache resolved the same range to 1.15 → different
  package_id → 'no binary' → the misleading source-build refusal.
  Exact pins and non-runtime deps must NOT be flagged."""
  root = _sources_with(
    tmp_path,
    'Require(pugixml VERSION ">=1.15 <2" CONAN pugixml)\n'
    'Require(yaml-cpp VERSION "0.9.0" CONAN yaml-cpp)\n'
    'Require(nlohmann_json VERSION "~3.12" PUBLIC)\n'
    'Require(GTest VERSION ">=1.17.0" TEST CONAN gtest)\n'
    'Require(benchmark VERSION ">=1.9.0" BENCH CONAN benchmark)\n'
    'Require(cmake VERSION ">=3.29" TOOL)\n'
    'Require(ZLIB VERSION ">=1.3" SYSTEM)\n'
    'Require(anything VERSION "*")\n')
  assert packaging.ranged_runtime_requires(root) == [
    ("pugixml", ">=1.15 <2"),
    ("nlohmann_json", "~3.12"),
    ("anything", "*"),
  ]


def test_ranged_requires_tolerate_a_missing_listing(tmp_path):
  assert packaging.ranged_runtime_requires(tmp_path) == []


@pytest.mark.skipif(importlib.util.find_spec("click") is None,
                    reason="needs click/typer (CLI-level test)")
def test_publish_refuses_ranged_runtime_requires(tmp_path, monkeypatch):
  """The guard sits in the publish command, before any version is
  derived or any build starts — the range is a fact of the committed
  listing, not of the build."""
  from click.testing import CliRunner
  from typer.main import get_command
  from buildutil.app import app as cli_app
  from buildutil import config
  _fake_project(monkeypatch, package_kind="library", package_name="s")
  _sources_with(tmp_path, 'Require(pugixml VERSION ">=1.15 <2")\n')
  monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
  monkeypatch.setattr("buildutil.commands.publish._enter",
                      lambda *a, **k: None)
  result = CliRunner().invoke(get_command(cli_app), ["publish", "--no-upload"])
  assert result.exit_code == 2
  assert "version RANGES" in result.output
  assert "pugixml" in result.output


# ----------------------------------------------- command assembly --

@pytest.mark.parametrize("user,channel", [(None, None), ("buildutil", "smoke")])
def test_export_and_test_carry_version_and_profiles(monkeypatch, user, channel):
  _fake_project(monkeypatch, package_kind="application",
                package_name="tool")
  calls = []
  packaging.export_pkg("0.3.0.2", Path("/p/host"), Path("/p/build"),
                       run=calls.append, user=user, channel=channel)
  packaging.run_package_test("0.3.0.2", Path("/p/host"), Path("/p/build"),
                             run=calls.append, user=user, channel=channel)
  assert calls[0][:3] == ["conan", "export-pkg", "."]
  assert "--version=0.3.0.2" in calls[0]
  assert "--profile:host=/p/host" in calls[0]
  assert "--profile:build=/p/build" in calls[0]
  reference = "tool/0.3.0.2" + ("@buildutil/smoke" if user else "")
  assert calls[1][:4] == ["conan", "test", "test_package", reference]
  if user:
    assert calls[0][calls[0].index("--user"):][:4] == [
      "--user", user, "--channel", channel]
  else:
    assert "--user" not in calls[0] and "--channel" not in calls[0]
  assert "--build=missing" in calls[1]


def test_upload_refuses_without_a_remote(monkeypatch):
  _fake_project(monkeypatch, package_kind="library", package_name="s")
  from buildutil import bootstrap
  monkeypatch.setattr(bootstrap, "conan_remote_env",
                      lambda: ("conancenter", None, None, None))
  with pytest.raises(SystemExit, match="nowhere to publish"):
    packaging.upload("1.0.0.1")
  monkeypatch.setattr(bootstrap, "conan_remote_env",
                      lambda: ("site", "https://x", "u", "p"))
  calls = []
  packaging.upload("1.0.0.1", run=calls.append)
  assert calls == [["conan", "upload", "s/1.0.0.1", "-r", "site",
                    "--confirm"]]


def test_upload_publishes_the_runtime_host_wrapper_recipes(tmp_path,
                                                          monkeypatch):
  _fake_project(monkeypatch, package_kind="library", package_name="s")
  from buildutil import config
  monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
  root = tmp_path
  (root / "sources").mkdir()
  (root / "sources" / "CMakeLists.txt").write_text(
    'Require(OpenSSL VERSION ">=3" SYSTEM)\n'
    'Require(GTest VERSION ">=1" SYSTEM TEST CONAN gtest)\n')
  from buildutil import bootstrap
  monkeypatch.setattr(bootstrap, "conan_remote_env",
                      lambda: ("site", "https://x", "u", "p"))
  calls = []
  packaging.upload("1.0.0.1", run=calls.append)
  assert calls[1:] == [["conan", "upload", "openssl/system@host", "-r",
                        "site", "--confirm", "--only-recipe"]]


def test_ref_falls_back_to_the_project_name(monkeypatch):
  _fake_project(monkeypatch, package_kind="library")
  assert packaging.ref("1.0.0.1") == "proj/1.0.0.1"


def test_configured_treats_none_as_unconfigured(monkeypatch):
  _fake_project(monkeypatch, package_kind="none")
  assert not packaging.configured()
  _fake_project(monkeypatch, package_kind="library")
  assert packaging.configured()


# --------------------------------------------------- init wizard --

def test_package_switch_records_the_choice_and_scaffolds(tmp_path, monkeypatch):
  monkeypatch.chdir(tmp_path)
  initcmd.main(["--name", "acme", "--package", "library",
                "--package-name", "serialize", "--no-agents"])
  toml = (tmp_path / "buildutil.toml").read_text()
  assert 'kind = "library"' in toml
  assert 'name = "serialize"' in toml
  section = toml.split("[package]")[1]
  assert "version" not in section, "the version never lives in the source"
  smoke = tmp_path / "test_package" / "smoke.cpp"
  assert smoke.is_file()
  assert "serialize" in (tmp_path / "test_package" / "CMakeLists.txt").read_text()
  # the fresh conanfile is already the [package]-aware template
  assert "_package_section" in (tmp_path / "conanfile.py").read_text()
  assert not (tmp_path / "conanfile.py.bak").exists()


def test_application_kind_scaffolds_the_app_test(tmp_path, monkeypatch):
  monkeypatch.chdir(tmp_path)
  initcmd.main(["--name", "tool", "--package", "application", "--no-agents"])
  test_cf = (tmp_path / "test_package" / "conanfile.py").read_text()
  assert "executables" in test_cf
  assert not (tmp_path / "test_package" / "smoke.cpp").exists()


def test_wizard_asks_at_a_tty_and_dont_package_is_remembered(tmp_path, monkeypatch):
  monkeypatch.chdir(tmp_path)
  monkeypatch.setattr("sys.stdin.isatty", lambda: True)
  # name, prefixes, no conan remote, don't package
  answers = iter(["acme", "", "", "", "3"])
  monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
  initcmd.main(["--bare", "--no-agents"])
  assert 'kind = "none"' in (tmp_path / "buildutil.toml").read_text()
  # re-running init must NOT re-ask: the recorded "none" answers it
  monkeypatch.setattr("builtins.input",
                      lambda prompt="": (_ for _ in ()).throw(
                        AssertionError("re-asked despite recorded choice")))
  initcmd.main(["--bare", "--no-agents"])


def test_wizard_full_library_answers(tmp_path, monkeypatch):
  monkeypatch.chdir(tmp_path)
  monkeypatch.setattr("sys.stdin.isatty", lambda: True)
  # name, prefixes ×2, no conan remote, choice=library, package name
  # (default accepted); NO version question — the version never lives
  # in the source
  answers = iter(["ser", "", "", "", "1", ""])
  monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
  initcmd.main(["--bare", "--no-agents"])
  toml = (tmp_path / "buildutil.toml").read_text()
  assert 'kind = "library"' in toml
  assert 'name = "ser"' in toml               # default accepted
  assert "version" not in toml.split("[package]")[1]


def test_no_package_never_asks_nor_records(tmp_path, monkeypatch):
  monkeypatch.chdir(tmp_path)
  monkeypatch.setattr("sys.stdin.isatty", lambda: True)
  monkeypatch.setattr("builtins.input",
                      lambda prompt="": (_ for _ in ()).throw(
                        AssertionError("asked despite --no-package")))
  initcmd.main(["--name", "acme", "--bare", "--no-package", "--no-agents"])
  assert "[package]" not in (tmp_path / "buildutil.toml").read_text()


def test_upgrading_an_old_project_regenerates_the_conanfile(tmp_path, monkeypatch):
  """The upgrade path (owner req): a project initialized before
  packaging gains it via the same switches; its pre-packaging recipe is
  kept as .bak, never silently lost."""
  monkeypatch.chdir(tmp_path)
  (tmp_path / "buildutil.toml").write_text('[project]\nname = "old"\n')
  (tmp_path / "conanfile.py").write_text(
    "# ancient hand-maintained recipe\nclass R: pass\n")
  initcmd.main(["--bare", "--package", "library", "--no-agents"])
  assert 'kind = "library"' in (tmp_path / "buildutil.toml").read_text()
  assert (tmp_path / "conanfile.py.bak").read_text().startswith(
    "# ancient hand-maintained recipe")
  assert "_package_section" in (tmp_path / "conanfile.py").read_text()
  assert (tmp_path / "test_package" / "conanfile.py").is_file()


def test_a_recorded_choice_survives_switch_disagreement(tmp_path, monkeypatch):
  monkeypatch.chdir(tmp_path)
  (tmp_path / "buildutil.toml").write_text(
    '[project]\nname = "p"\n[package]\nkind = "library"\nname = "p"\n')
  initcmd.main(["--bare", "--package", "application", "--no-agents"])
  # the committed record wins; changing it is a toml edit, not a re-init
  assert 'kind = "library"' in (tmp_path / "buildutil.toml").read_text()


# ------------------------------------------------- CLI plumbing --

@pytest.mark.skipif(importlib.util.find_spec("click") is None,
                    reason="the CLI lane needs typer/click")
def test_publish_help_reaches_the_cli(tmp_path):
  """publish is registered on the typer app — proven from the stdlib
  lane via --help in a throwaway project (no conan, no build)."""
  (tmp_path / "buildutil.toml").write_text('[project]\nname = "x"\n')
  pkg_parent = Path(initcmd.__file__).resolve().parents[1]
  proc = subprocess.run(
    [sys.executable, "-m", "buildutil", "publish", "--help"],
    cwd=tmp_path, capture_output=True, text=True,
    env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
         "PYTHONPATH": str(pkg_parent), "BUILDUTIL_SYSTEM": "1"})
  assert proc.returncode == 0, proc.stdout + proc.stderr
  assert "--no-version-autoincrement" in proc.stdout


# --------------------------------- the static-archive gap (e2e) --

import shutil

from buildutil import deposit

CFG = {"cmake_option_prefix": "ACME", "module_define_prefix": "ACM"}

e2e = pytest.mark.skipif(
  shutil.which("cmake") is None or shutil.which("ninja") is None or
  not (shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")),
  reason="needs cmake, ninja and a C++ compiler")

ROOT_CMAKE = """\
cmake_minimum_required(VERSION 3.25)
project(pkglibs CXX)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
add_subdirectory(sources)
"""


def _lib_tree(tmp_path, package_kind):
  deposit.ensure(tmp_path, {**CFG, "package_kind": package_kind})
  (tmp_path / "CMakeLists.txt").write_text(ROOT_CMAKE)
  src = tmp_path / "sources"
  src.mkdir()
  (src / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  mod = src / "ser" / "core"
  mod.mkdir(parents=True)
  (mod / "CMakeLists.txt").write_text("Init_submodule()\n")
  (mod / "impl.cpp").write_text("int core_answer() { return 42; }\n")
  for cmd in (["cmake", "-S", str(tmp_path), "-B", str(tmp_path / "b"),
               "-G", "Ninja"],
              ["cmake", "--build", str(tmp_path / "b")],
              ["cmake", "--install", str(tmp_path / "b"),
               "--prefix", str(tmp_path / "prefix")]):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
  return tmp_path / "prefix"


@e2e
def test_a_library_package_ships_its_static_archives(tmp_path):
  """The flagship use case (a standalone serialization library): a
  packaged LIBRARY project must ship the .a of every module at its
  mirrored, leaf-named spot — without this the package holds headers
  and nothing to link."""
  prefix = _lib_tree(tmp_path, "library")
  archive = prefix / "ser" / "libcore.a"
  assert archive.is_file(), "\n".join(
    str(p) for p in prefix.rglob("*"))


@e2e
def test_an_unpackaged_project_install_does_not_move(tmp_path):
  """The other half of the gate: no [package] (or kind none) keeps the
  pre-0.47 mirror byte-identical — no stray archives appear in any
  existing project's install output."""
  prefix = _lib_tree(tmp_path, "")
  assert not list(prefix.rglob("*.a")), list(prefix.rglob("*"))


# ------------------------------- what a package says it is ------------

class _Components(dict):
  """conan's cpp_info.components: a name creates its component."""

  def __missing__(self, name):
    self[name] = SimpleNamespace(libs=None, libdirs=None, includedirs=None,
                                 system_libs=[], requires=None)
    return self[name]


class _CppInfo(SimpleNamespace):
  """Enough of conan's cpp_info for package_info to fill in."""

  def __init__(self):
    super().__init__(libs=None, libdirs=None, includedirs=None,
                     bindirs=None, properties={}, components=_Components())

  def set_property(self, name, value):
    self.properties[name] = value


def _described(tmp_path, toml_text, files, manifest=None, wrappers=(),
               cross=False):
  """package_info() over a package tree of exactly these files."""
  mod = _recipe_module(tmp_path, toml_text)
  mod.cross_building = lambda conanfile: cross
  pkg = tmp_path / "pkg"
  for rel in files:
    path = pkg / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
  if manifest is not None:
    path = pkg / "share" / "buildutil" / "buildutil-components.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"components": manifest}))
  recipe = mod.ProjectRecipe()
  recipe.package_folder = str(pkg)
  recipe.cpp_info = _CppInfo()
  direct_host = {wrapper.ref.name: wrapper for wrapper in wrappers}
  recipe.dependencies = SimpleNamespace(direct_host=direct_host)
  recipe.package_info()
  return recipe.cpp_info


def _info(**properties):
  return SimpleNamespace(get_property=properties.get, components={})


def _dependency(name, root_target=None, host_targets=None, **components):
  """A direct dependency: component names to their cmake_target_name."""
  cpp_info = _info(cmake_target_name=root_target,
                   buildutil_host_targets=host_targets)
  cpp_info.components = {component: _info(cmake_target_name=target)
                         for component, target in components.items()}
  return SimpleNamespace(ref=SimpleNamespace(name=name), cpp_info=cpp_info)


def _wrapper(name, targets):
  """A <name>/system@host dependency declaring these host targets."""
  return _dependency(name, host_targets=targets)


OXBOX = _dependency("oxbox", platform="oxbox::platform")

FREERDP_WRAPPERS = (
  _wrapper("freerdp-server",
           {"freerdp-server": "freerdp-server::freerdp-server"}),
  _wrapper("freerdp", {"freerdp": "freerdp::freerdp"}),
  _wrapper("winpr", {"winpr": "winpr::winpr"}),
  _wrapper("openssl", {"OpenSSL::SSL": "openssl::ssl",
                       "OpenSSL::Crypto": "openssl::crypto"}),
  OXBOX)


CORES = '[package]\nkind = "library"\nname = "cores"\n'
NOT_LINKABLE = CORES + "linkable = false\n"
PAYLOAD = ["lib/genesis_plus_gx_libretro.so", "include/cores/libretro.h"]


@pytest.mark.parametrize("chain", [
  ["libSDL3.so", "libSDL3.so.0", "libSDL3.so.3.4.8"],
  ["libSDL3.dylib", "libSDL3.0.dylib", "libSDL3.3.4.8.dylib"],
])
def test_a_versioned_shared_library_is_named_once(tmp_path, chain):
  """A soversion ships the full-version file behind two links (#129)."""
  lib = tmp_path / "pkg" / "lib"
  lib.mkdir(parents=True)
  (lib / chain[-1]).write_bytes(b"")
  for link, target in zip(chain, chain[1:]):
    (lib / link).symlink_to(target)
  info = _described(tmp_path, CORES, [])
  assert info.libs == ["SDL3"]
  assert info.libdirs == ["lib"]


def test_a_runtime_payload_is_advertised_as_a_library_by_default(tmp_path):
  """The state of affairs the ticket is about: every file with a library
  suffix becomes a link target, whatever it is for."""
  info = _described(tmp_path, CORES, PAYLOAD)
  assert info.libs == ["genesis_plus_gx_libretro"]


def test_a_package_that_declares_itself_unlinkable_advertises_no_libs(tmp_path):
  """Libretro cores are dlopen'd by a front-end and never linked -- every
  one of them exports the same retro_* symbols, so a consumer could link
  at most one and would still load the rest by path. Presence cannot tell
  such a shared object from one meant to be linked; the project says so."""
  info = _described(tmp_path, NOT_LINKABLE, PAYLOAD)
  assert info.libs == []
  assert info.libdirs == ["lib"], "the loader still has to be pointed at it"
  assert info.includedirs == ["include"]


def test_a_cmake_file_under_the_packages_own_share_is_a_build_module(tmp_path):
  """The other half of the same gap: a project can GENERATE a cmake
  module and ship it through the `*.install/` layout rule, but nothing
  connected it to `cmake_build_modules`, which is what makes
  find_package include it."""
  info = _described(tmp_path, NOT_LINKABLE,
                    PAYLOAD + ["share/cmake/cores/genesis-plus-gx.cmake"])
  assert info.properties["cmake_build_modules"] == [
    "share/cmake/cores/genesis-plus-gx.cmake"]


def test_a_cmake_file_somewhere_else_is_not_a_build_module(tmp_path):
  """`share/cmake/<package>/` is the place, so a dependency's exported
  config lying elsewhere in the tree is not included into consumers."""
  info = _described(tmp_path, CORES,
                    PAYLOAD + ["share/cmake/other/thing.cmake",
                               "lib/cmake/cores/thing.cmake"])
  assert "cmake_build_modules" not in info.properties


def test_an_unlinkable_package_says_so_for_every_component_too(tmp_path):
  """The component branch answers the same question about the same
  files, and a multi-module package must not disagree with itself."""
  info = _described(
    tmp_path, NOT_LINKABLE, PAYLOAD + ["cores/libgenesis.a"],
    manifest=[{"path": "cores/genesis", "lib": "genesis", "needs": [],
               "external": []},
              {"path": "cores/snes", "lib": "snes", "needs": [],
               "external": []}])
  assert [c.libs for c in info.components.values()] == [[], []]


def test_a_linkable_package_still_componentises_its_libraries(tmp_path):
  info = _described(
    tmp_path, CORES, ["cores/libgenesis.a", "include/cores/api.h"],
    manifest=[{"path": "cores/genesis", "lib": "genesis", "needs": [],
               "external": []},
              {"path": "cores/snes", "lib": "snes", "needs": [],
               "external": []}])
  assert info.components["genesis"].libs == ["genesis"]
  assert info.components["snes"].libs == []


def test_a_host_target_names_the_wrapper_component_it_is(tmp_path):
  """sdl-rdp's links, not -lfreerdp-server: each target is its own
  wrapper's, even when FreeRDP-Server's config imported winpr first or a
  conan dependency's config created OpenSSL::SSL before the SYSTEM find."""
  info = _described(
    tmp_path, CORES, ["lib/libbackend.a", "lib/libsample.a"],
    manifest=[{"path": "backend", "lib": "backend", "needs": [],
               "external": ["oxbox::platform", "OpenSSL::SSL"],
               "host": ["freerdp-server", "freerdp", "winpr"]},
              {"path": "sample", "lib": "sample", "needs": ["backend"],
               "external": [], "host": []}],
    wrappers=FREERDP_WRAPPERS)
  assert info.components["backend"].requires == [
    "oxbox::platform", "openssl::ssl", "freerdp-server::freerdp-server",
    "freerdp::freerdp", "winpr::winpr"]
  assert info.components["backend"].system_libs == []
  assert info.components["sample"].requires == ["backend"]


BACKEND = [{"path": "backend", "lib": "backend", "needs": [],
            "external": ["oxbox::platform"],
            "host": ["freerdp", "winpr", "OpenSSL::Crypto3"]},
           {"path": "sample", "lib": "sample", "needs": ["backend"],
            "external": [], "host": []}]


def test_a_host_target_no_wrapper_declares_is_refused_by_name(tmp_path):
  with pytest.raises(Exception, match=(
      r"module backend links \['winpr', 'OpenSSL::Crypto3'\], which a SYSTEM "
      r"find imported and no host wrapper this package requires declares")):
    _described(tmp_path, CORES, ["lib/libbackend.a", "lib/libsample.a"],
               manifest=BACKEND, wrappers=(FREERDP_WRAPPERS[1], OXBOX))


def test_a_cross_build_declares_no_host_target(tmp_path):
  """A cross build forces no wrapper: its host targets are the target's."""
  info = _described(tmp_path, CORES, ["lib/libbackend.a", "lib/libsample.a"],
                    manifest=BACKEND, wrappers=(OXBOX,), cross=True)
  assert info.components["backend"].requires == ["oxbox::platform"]
  assert info.components["backend"].system_libs == []


def test_a_conan_dependencys_cmake_targets_are_its_components(tmp_path):
  """#153: OpenSSL::SSL from Require(OpenSSL ... CONAN openssl) is
  openssl::ssl, a root target is the package, a default spelling stays."""
  openssl = _dependency("openssl", root_target="OpenSSL::OpenSSL",
                        ssl="OpenSSL::SSL", crypto="OpenSSL::Crypto")
  fake = _dependency("fake", a=None, b="Fake::B")
  info = _described(
    tmp_path, CORES, ["lib/libbackend.a", "lib/libsample.a"],
    manifest=[{"path": "backend", "lib": "backend", "needs": [],
               "external": ["OpenSSL::SSL", "OpenSSL::OpenSSL", "fake::a",
                            "Fake::B", "pthread"], "host": []},
              {"path": "sample", "lib": "sample", "needs": ["backend"],
               "external": [], "host": []}],
    wrappers=(openssl, fake))
  assert info.components["backend"].requires == [
    "openssl::ssl", "openssl::openssl", "fake::a", "fake::b"]
  assert info.components["backend"].system_libs == ["pthread"]


def test_a_namespaced_link_no_dependency_declares_is_refused(tmp_path):
  with pytest.raises(Exception, match=(
      r"module backend links \['Nowhere::lib'\], which no direct dependency")):
    _described(tmp_path, CORES, ["lib/libbackend.a", "lib/libsample.a"],
               manifest=[{"path": "backend", "lib": "backend", "needs": [],
                          "external": ["Nowhere::lib"], "host": []},
                         {"path": "sample", "lib": "sample", "needs": [],
                          "external": [], "host": []}],
               wrappers=(OXBOX,))


def test_a_missing_conan_binary_is_no_answer(monkeypatch):
  monkeypatch.setenv("CONAN_REMOTE_URL", "https://repo.example/conan")
  def absent(*args, **kwargs):
    raise FileNotFoundError(2, "No such file or directory", "conan")
  assert packaging.remote_builds("1.2.3", run=absent) is None
