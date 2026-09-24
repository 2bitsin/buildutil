"""[options]: declared in the toml, chosen on the command line, injected.

An option is not a constant. A constant never changes and belongs in the
code; this is what the build was asked for, so it is chosen per build and
reaches the compiler as a macro -- the only thing some conditional
compilation can read. Everything below holds the three halves of that to
one story: what the toml may declare, what the flag may choose, and what
the compiler is handed.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from buildutil import deposit, options
from buildutil.config import _load_project

PKG_PARENT = Path(deposit.__file__).resolve().parents[1]

CFG = {"name": "demo", "cmake_option_prefix": "DEMO",
       "module_define_prefix": "DM",
       "options": {"contracts": True, "max_depth": 32, "greeting": "hello"}}

DECLARED = CFG["options"]


def project(text, tmp_path, monkeypatch):
  (tmp_path / "buildutil.toml").write_text(text)
  monkeypatch.setenv("BUILDUTIL_ROOT", str(tmp_path))
  import buildutil.config as config
  monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
  monkeypatch.setattr(config, "HAVE_PROJECT", True)
  return _load_project()


# ------------------------------------------------------- the declaration --

def test_no_options_is_the_normal_project(tmp_path, monkeypatch):
  assert project('[project]\nname = "demo"\n', tmp_path, monkeypatch)[
    "options"] == {}


def test_every_value_kind_is_read_with_its_type(tmp_path, monkeypatch):
  cfg = project('[options]\ncontracts = true\nmax_depth = 32\n'
                'greeting = "hello"\n', tmp_path, monkeypatch)
  assert cfg["options"] == DECLARED
  assert [type(value) for value in cfg["options"].values()] == [bool, int, str]


@pytest.mark.parametrize("text, message", [
  ('[options]\nContracts = true\n', "'Contracts' is not an option name"),
  ('[options]\n"max depth" = 1\n', "'max depth' is not an option name"),
  ('[options]\n_hidden = true\n', "'_hidden' is not an option name"),
  ('[options]\nratio = 1.5\n', "ratio must default to a boolean"),
  ('[options]\nnames = ["a"]\n', "names must default to a boolean"),
  ('[options.nested]\nkey = 1\n', "nested must default to a boolean"),
  ('options = 1\n', "[options] must be a table"),
  ('[options]\ngreeting = "a;b"\n', "greeting may not hold any of"),
  ('[options]\ngreeting = "${EVIL}"\n', "greeting may not hold any of"),
])
def test_a_malformed_declaration_names_the_key(tmp_path, monkeypatch, text,
                                               message):
  with pytest.raises(SystemExit) as raised:
    project(text, tmp_path, monkeypatch)
  assert message in str(raised.value)


# -------------------------------------------------------------- the flag --

def test_the_flag_reads_a_value_in_the_declared_kind():
  assert options.parse_flags(
    ["contracts=off", "max_depth=8", "greeting=bye"], DECLARED) == {
      "contracts": False, "max_depth": 8, "greeting": "bye"}


@pytest.mark.parametrize("spelling, value", [
  ("on", True), ("true", True), ("TRUE", True),
  ("off", False), ("false", False), ("Off", False)])
def test_a_boolean_takes_either_spelling(spelling, value):
  assert options.parse_flags([f"contracts={spelling}"],
                             DECLARED)["contracts"] is value


@pytest.mark.parametrize("pair", ["contrakts=off", "contracts"])
def test_a_name_that_is_not_one_lists_the_declared_options(pair):
  """A typo has nothing else to go on."""
  with pytest.raises(ValueError) as raised:
    options.parse_flags([pair], DECLARED)
  assert "contracts, greeting, max_depth" in str(raised.value)


@pytest.mark.parametrize("pair, message", [
  ("contracts=maybe", "contracts is a boolean option"),
  ("max_depth=deep", "max_depth is an integer option"),
  ("greeting=a;b", "greeting may not hold ';'"),
  ("greeting=${EVIL}", "greeting may not hold '$'"),
])
def test_a_value_of_the_wrong_kind_names_the_kind(pair, message):
  with pytest.raises(ValueError) as raised:
    options.parse_flags([pair], DECLARED)
  assert message in str(raised.value)


def test_the_flag_and_the_toml_refuse_the_same_characters():
  """One predicate: a semicolon on the command line used to reach cmake
  and fail there, where the toml's own refusal names the key."""
  assert options.refused_characters("a;b") == ";"
  assert options.refused_characters("plain text") == ""


def test_the_chosen_value_wins_over_the_declared_default(monkeypatch):
  monkeypatch.setenv("BUILDUTIL_OPTION_CONTRACTS", "OFF")
  assert options.chosen(DECLARED) == {
    "contracts": False, "max_depth": 32, "greeting": "hello"}


def test_an_empty_string_is_a_chosen_value(monkeypatch):
  """`--option greeting=` asks for the empty string; only an option
  nobody chose falls back to the declared default."""
  monkeypatch.setenv("BUILDUTIL_OPTION_GREETING", "unset at teardown")
  options.apply(["greeting="], DECLARED)
  assert options.chosen(DECLARED)["greeting"] == ""
  assert options.profile_note(DECLARED) == " options: greeting="


def test_every_option_is_passed_to_cmake_even_at_its_default(monkeypatch):
  """A value left out would be inherited from whatever the last build of
  this tree put in the cache -- the same reason coverage is explicit."""
  monkeypatch.setenv("BUILDUTIL_OPTION_MAX_DEPTH", "8")
  assert options.cmake_arguments("DEMO", DECLARED) == [
    "-DDEMO_OPTION_CONTRACTS=ON", "-DDEMO_OPTION_MAX_DEPTH=8",
    "-DDEMO_OPTION_GREETING=hello"]


def test_the_profile_line_says_only_what_differs(monkeypatch):
  assert options.profile_note(DECLARED) == ""
  monkeypatch.setenv("BUILDUTIL_OPTION_CONTRACTS", "OFF")
  assert options.profile_note(DECLARED) == " options: contracts=off"


def test_the_cli_refuses_an_undeclared_option(tmp_path):
  (tmp_path / "buildutil.toml").write_text(
    '[project]\nname = "demo"\n[options]\ncontracts = true\n')
  proc = subprocess.run(
    [sys.executable, "-m", "buildutil", "build", "--option", "contrakts=off"],
    cwd=tmp_path, capture_output=True, text=True,
    env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
         "PYTHONPATH": str(PKG_PARENT), "BUILDUTIL_SYSTEM": "1"})
  assert proc.returncode == 2, proc.stdout + proc.stderr
  assert "no project option 'contrakts'" in proc.stdout + proc.stderr


# ------------------------------------------------------------ the header --

def test_the_header_renders_one_define_per_kind():
  text = options.header_text("DEMO", list(DECLARED.items()))
  assert "#define DEMO_CONTRACTS 1" in text
  assert "#define DEMO_MAX_DEPTH 32" in text
  assert '#define DEMO_GREETING "hello"' in text


def test_an_overridden_value_is_what_the_macro_expands_to():
  text = options.header_text(
    "DEMO", [("contracts", False), ("max_depth", 8), ("greeting", "bye")])
  assert "#define DEMO_CONTRACTS 0" in text
  assert "#define DEMO_MAX_DEPTH 8" in text
  assert '#define DEMO_GREETING "bye"' in text


def test_a_boolean_is_0_or_1_so_if_can_read_it():
  """`#ifdef` is true for both, so the macro carries a value."""
  assert options.macro_value(True) == "1"
  assert options.macro_value(False) == "0"


def test_an_unchanged_header_is_not_rewritten(tmp_path):
  """Restamping it would rebuild every TU on every configure."""
  path = tmp_path / "options.hpp"
  options.write_header(path, "DEMO", list(DECLARED.items()))
  os.utime(path, (1, 1))
  options.write_header(path, "DEMO", list(DECLARED.items()))
  assert path.stat().st_mtime == 1
  options.write_header(path, "DEMO", [("contracts", False)])
  assert path.stat().st_mtime != 1


def test_a_value_the_environment_carries_is_refused_by_name(monkeypatch):
  """Every consumer of chosen() sits outside the CLI's own refusal, so
  a stale variable used to be a traceback from inside a build."""
  monkeypatch.setenv("BUILDUTIL_OPTION_MAX_DEPTH", "deep")
  with pytest.raises(SystemExit) as raised:
    options.chosen(DECLARED)
  assert "BUILDUTIL_OPTION_MAX_DEPTH is an integer option" in str(raised.value)


@pytest.mark.parametrize("declaration", ["contracts", "contracts:bool",
                                         "contracts:boolean:ON", ":bool:ON"])
def test_a_malformed_generator_declaration_names_it(tmp_path, declaration):
  with pytest.raises(ValueError) as raised:
    options.main(["--prefix", "DEMO", "--output", str(tmp_path / "o.hpp"),
                  "--option", declaration])
  assert "NAME:KIND:VALUE" in str(raised.value)


def test_the_generator_runs_as_a_module_the_machinery_can_call(tmp_path):
  path = tmp_path / "options.hpp"
  assert options.main(["--prefix", "DEMO", "--output", str(path),
                       "--option", "contracts:bool:OFF",
                       "--option", "greeting:string:bye"]) == 0
  assert path.read_text().endswith(
    '#define DEMO_CONTRACTS 0\n#define DEMO_GREETING "bye"\n')


# --------------------------------------------------------- the machinery --

def test_a_project_without_options_renders_no_call(tmp_path):
  machinery = (deposit.ensure(tmp_path, {**CFG, "options": {}})
               / "buildutil.cmake").read_text()
  assert "function(_buildutil_project_options " in machinery
  assert '_buildutil_project_options("' not in machinery


def test_the_declarations_reach_the_machinery_as_one_call(tmp_path):
  machinery = (deposit.ensure(tmp_path, CFG)
               / "buildutil.cmake").read_text()
  assert ('_buildutil_project_options("demo" "contracts|bool|ON;'
          'max_depth|int|32;greeting|string|hello")') in machinery


def test_one_place_force_includes_the_header(tmp_path):
  """Both compiler spellings come out of the same block; two places
  emitting them is how they would come to disagree."""
  machinery = (deposit.ensure(tmp_path, CFG)
               / "buildutil.cmake").read_text()
  assert machinery.count(':SHELL:-include ') == 1
  assert machinery.count(':SHELL:/FI ') == 1


# ------------------------------------------------------------ the editor --

def _editor(monkeypatch, declared):
  import buildutil.config as config
  import buildutil.vscode as vscode
  monkeypatch.setattr(config, "PROJECT", {**config.PROJECT,
                                          "options": declared})
  monkeypatch.setattr(vscode, "PROJECT_NAME", "demo")
  return vscode


def test_the_editor_is_force_fed_the_same_header(monkeypatch):
  """IntelliSense and the debugger read the macros the build compiled."""
  vscode = _editor(monkeypatch, DECLARED)
  assert vscode._forced_include("x86_64-linux-gcc-debug") == {
    "forcedInclude": ["${workspaceFolder}/_build/x86_64-linux-gcc-debug"
                      "/generated/demo/options.hpp"]}


def test_a_project_without_options_gets_no_forced_include(monkeypatch):
  assert _editor(monkeypatch, {})._forced_include("x86_64-linux-gcc-debug") == {}


def test_the_editor_tasks_carry_the_chosen_options(monkeypatch):
  """AFTER the verb: the root callback has no --option, so before it
  every task exits 2 on 'No such option'."""
  vscode = _editor(monkeypatch, DECLARED)
  monkeypatch.setenv("BUILDUTIL_OPTION_CONTRACTS", "OFF")
  task = vscode._buildutil_task(
    "build hello", ["build", "--target", "hello"], log="")
  assert task["args"][-5:] == ["build", "--option", "contracts=off",
                               "--target", "hello"]


def test_a_verb_that_takes_no_option_is_left_alone(monkeypatch):
  """`deps` is not one of the eight, and a flag it never declared would
  kill the task."""
  vscode = _editor(monkeypatch, DECLARED)
  monkeypatch.setenv("BUILDUTIL_OPTION_CONTRACTS", "OFF")
  task = vscode._buildutil_task("deps", ["deps"], log="")
  assert task["args"][-1:] == ["deps"]
