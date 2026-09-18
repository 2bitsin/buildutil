import pytest

from buildutil import modules


def _make_sources(root, names):
  src = root / "sources"
  for name in names:
    (src / name).mkdir(parents=True)
    (src / name / "CMakeLists.txt").write_text("")
  (src / "no_cmake").mkdir()                 # a dir without CMakeLists is not a module
  return src


def test_available_scans_sources(tmp_path):
  src = _make_sources(tmp_path, ["alpha", "beta"])
  assert modules.available(src) == ["alpha", "beta"]


def test_load_save_roundtrip(tmp_path):
  ini = tmp_path / "modules.ini"
  modules.save(ini, ["alpha", "beta"], ["gamma"])
  enabled, disabled = modules.load(ini)
  assert enabled == ["alpha", "beta"]
  assert disabled == ["gamma"]


def test_missing_file_is_empty(tmp_path):
  assert modules.load(tmp_path / "none.ini") == ([], [])


def test_set_dormant_moves_between_sections(tmp_path):
  ini = tmp_path / "modules.ini"
  modules.save(ini, ["alpha", "beta"], [])
  modules.set_dormant(ini, "beta", True)
  assert modules.load(ini) == (["alpha"], ["beta"])
  modules.set_dormant(ini, "beta", False)
  assert modules.load(ini) == (["alpha", "beta"], [])


def test_status_reports_dormant(tmp_path):
  src = _make_sources(tmp_path, ["alpha", "beta"])
  ini = tmp_path / "modules.ini"
  modules.save(ini, ["alpha"], ["beta"])
  assert modules.status(src, ini) == [("alpha", False), ("beta", True)]


def test_enabled_definitions_skip_dormant(tmp_path):
  src = _make_sources(tmp_path, ["alpha", "beta"])
  ini = tmp_path / "modules.ini"
  modules.save(ini, ["alpha"], ["beta"])
  # only the building module announces itself; the dormant one is
  # absent. The prefix is the SEAM, not a constant — this used to pin
  # the literal "BDX_" bossdeux uses, which is now one buildutil.toml
  # setting among many (config.MODULE_DEFINE_PREFIX; "MOD" when no
  # project config is loaded, as in this suite).
  from buildutil.config import MODULE_DEFINE_PREFIX
  assert modules.enabled_definitions(src, ini) == [
    f"{MODULE_DEFINE_PREFIX}_ALPHA_ENABLED=1"]


def test_a_hyphenated_module_still_makes_a_usable_macro(tmp_path):
  """`cc -D` stops reading the macro name at the first non-identifier
  character, so -DMOD_MSTOOLS-CL_ENABLED=1 defines MOD_MSTOOLS with the
  value "-CL_ENABLED=1": the guard the project wants never exists, and
  the sibling that owns MOD_MSTOOLS gets redefined once per hyphenated
  neighbour, on every TU in the tree."""
  from buildutil.config import MODULE_DEFINE_PREFIX
  src = _make_sources(tmp_path, ["mstools", "mstools-cl", "mstools-rc"])
  ini = tmp_path / "modules.ini"
  defs = modules.enabled_definitions(src, ini)
  assert defs == [
    f"{MODULE_DEFINE_PREFIX}_MSTOOLS_ENABLED=1",
    f"{MODULE_DEFINE_PREFIX}_MSTOOLS_CL_ENABLED=1",
    f"{MODULE_DEFINE_PREFIX}_MSTOOLS_RC_ENABLED=1"]
  # the real acceptance test: every define is one the preprocessor reads
  # whole, name and value, with nothing lost at a stray character
  for d in defs:
    name, _, value = d.partition("=")
    assert name.replace("_", "a").isalnum(), d
    assert value == "1", d


def test_names_that_fold_together_are_refused(tmp_path):
  """Folding to '_' can collide. One guard standing for two modules is
  the silent version of the same bug, so refuse and name both."""
  src = _make_sources(tmp_path, ["foo-bar", "foo_bar"])
  ini = tmp_path / "modules.ini"
  with pytest.raises(SystemExit) as e:
    modules.enabled_definitions(src, ini)
  assert "foo-bar" in str(e.value) and "foo_bar" in str(e.value)


def test_a_dormant_module_cannot_collide(tmp_path):
  """It contributes no define, so it cannot clash with one."""
  src = _make_sources(tmp_path, ["foo-bar", "foo_bar"])
  ini = tmp_path / "modules.ini"
  modules.save(ini, ["foo_bar"], ["foo-bar"])
  from buildutil.config import MODULE_DEFINE_PREFIX
  assert modules.enabled_definitions(src, ini) == [
    f"{MODULE_DEFINE_PREFIX}_FOO_BAR_ENABLED=1"]


def test_the_project_default_makes_a_module_dormant_with_no_ini(tmp_path):
  """The clean-clone case: no _bdudata/modules.ini exists at all, and the
  fact still has to arrive — that is the whole point of committing it."""
  src = _make_sources(tmp_path, ["as", "cc"])
  ini = tmp_path / "modules.ini"          # deliberately absent
  assert modules.dormant_set(ini, defaults=["as"]) == {"as"}
  assert modules.status(src, ini, defaults=["as"]) == [("as", True), ("cc", False)]


def test_the_local_ini_overrides_the_default_in_both_directions(tmp_path):
  """A default, not a lock. Someone porting `as` builds it by putting it
  under [enabled], without touching a committed file."""
  ini = tmp_path / "modules.ini"
  modules.save(ini, ["as"], ["cc"])       # as revived, cc locally dormant
  assert modules.dormant_set(ini, defaults=["as"]) == {"cc"}


def test_enabling_a_default_dormant_module_survives_set_dormant(tmp_path):
  """`buildutil module enable as` has to write something that OUTLASTS the
  toml default — removing it from [disabled] is not enough, it was never
  there. It must land in [enabled]."""
  ini = tmp_path / "modules.ini"
  modules.set_dormant(ini, "as", dormant=False)
  assert "as" in modules.load(ini)[0]
  assert modules.dormant_set(ini, defaults=["as"]) == set()


def test_a_default_dormant_module_contributes_no_enabled_define(tmp_path):
  from buildutil.config import MODULE_DEFINE_PREFIX
  src = _make_sources(tmp_path, ["as", "cc"])
  ini = tmp_path / "modules.ini"
  assert modules.enabled_definitions(src, ini, defaults=["as"]) == [
    f"{MODULE_DEFINE_PREFIX}_CC_ENABLED=1"]
