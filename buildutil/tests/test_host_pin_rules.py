"""The rule a SYSTEM Require's host version meets for every pin it replaced."""
from types import SimpleNamespace

import pytest


@pytest.fixture
def recipe(real_conan):
  """The scaffolded conanfile as a module, against the installed conan."""
  import types
  from buildutil import packaging
  path = packaging.REQUIRES_PARSER.parent / "conanfile.py"
  module = types.ModuleType("recipe_under_test")
  module.__file__ = str(path)
  exec(compile(path.read_text(), str(path), "exec"), module.__dict__)
  return module


@pytest.mark.parametrize("pin, host, satisfied", [
  ("3.5.0", "3.5.6", True),
  ("3.5.6", "3.5.6", True),
  ("3.6.3", "3.5.6", False),
  ("2.1.0", "3.5.6", False),
  ("4.0.0", "3.5.6", False),
  ("[>=3 <4]", "3.5.6", True),
  ("[>=3.6 <4]", "3.5.6", False),
])
def test_a_pin_is_met_at_or_above_it_in_its_major(recipe, pin, host,
                                                   satisfied):
  assert recipe._pin_satisfied(pin, host) is satisfied


ENTRY = {"cmake_name": "hostlib", "conan_name": "hostlib",
         "ref": "hostlib/system@host", "version": "[>=1 <2]", "floor": ">=1"}


@pytest.mark.parametrize("resolved, host, pin, force, verdict, text", [
  ("hostlib/system@host", "1.4.0", "hostlib/1.2.0", False, "clean", ""),
  ("hostlib/system@host", "1.4.0", "hostlib/1.4.0", False, "clean", ""),
  ("hostlib/system@host", "1.4.0", "hostlib/1.5.0", False, "refused",
   "consumer: pinner/1.0.0 pins hostlib/1.5.0; the host's hostlib is 1.4.0"),
  ("hostlib/system@host", "1.4.0", "hostlib/1.5.0", True, "warned",
   "hostlib: the host's 1.4.0 is forced over pinner/1.0.0's pin "
   "hostlib/1.5.0 (FORCE)"),
  ("hostlib/system@host", "1.4.0", "hostlib/[>=1.5 <2]", False, "refused",
   "pinner/1.0.0 pins hostlib/[>=1.5 <2]"),
  ("hostlib/system@host", "1.4.0", "hostlib/[>=1.5 <2]", True, "warned",
   "pin hostlib/[>=1.5 <2] (FORCE)"),
  ("hostlib/system@host", "0.9.0", "hostlib/0.8.0", True, "refused",
   'below this project\'s Require(hostlib VERSION ">=1")'),
  ("hostlib/1.2.0", "None", "hostlib/1.2.0", True, "refused",
   "consumer: hostlib/1.2.0 replaced the host's hostlib"),
])
def test_the_pins_it_replaced_refuse_or_warn(recipe, resolved, host, pin,
                                             force, verdict, text):
  """FORCE waives the pins only: the floor and a winning pin still refuse."""
  entry = {**ENTRY, "force": force}
  refusals, warnings = recipe._host_findings(
    "consumer", entry, resolved, host, [("pinner/1.0.0", pin)])
  lines = {"clean": [], "refused": refusals, "warned": warnings}[verdict]
  assert bool(lines) == (verdict != "clean")
  assert len(refusals) + len(warnings) == len(lines)
  assert all(text in line for line in lines)


class _Dependencies(dict):
  """conan's dependencies: `in` and [] by package name."""


def _graph(recipe, monkeypatch, present, cross=False):
  monkeypatch.setitem(recipe.__dict__, "cross_building", lambda c: cross)
  found = SimpleNamespace(ref=SimpleNamespace(name="hostlib",
                                              version="1.2.0", user=None))
  return SimpleNamespace(name="consumer", dependencies=_Dependencies(
    {"hostlib": found} if present else {}))


@pytest.mark.parametrize("lane", ["test", "bench"])
def test_a_test_lane_SYSTEM_entry_may_be_absent(recipe, monkeypatch, lane):
  entry = {**ENTRY, "test": False, "bench": False, lane: True}
  assert not recipe._in_graph(_graph(recipe, monkeypatch, False), entry)


def test_a_runtime_SYSTEM_entry_absent_from_the_graph_fails_its_contract(
    recipe, monkeypatch):
  from conan.errors import ConanException
  entry = {**ENTRY, "test": False, "bench": False}
  with pytest.raises(ConanException, match="graph has no hostlib"):
    recipe._in_graph(_graph(recipe, monkeypatch, False), entry)


def test_a_cross_build_refuses_conans_copy_of_a_SYSTEM_package(recipe,
                                                              monkeypatch):
  graph = _graph(recipe, monkeypatch, True, cross=True)
  entry = {**ENTRY, "test": False, "bench": False}
  assert recipe._in_graph(graph, entry)
  refusals, warnings = recipe._entry_findings(graph, entry)
  assert warnings == []
  assert "hostlib/1.2.0 is in this cross build's graph" in refusals[0]


def _requesting(ref, requests=(), wrappers=()):
  """A graph node: its ref, its wrapper requests, the wrappers it requires."""
  asked = [(SimpleNamespace(ref=SimpleNamespace(name=name, user="host"),
                            options=options), None)
           for name, options in requests]
  nodes = {name: SimpleNamespace(ref=SimpleNamespace(name=name))
           for name in wrappers}
  dependencies = _Dependencies(nodes)
  dependencies.direct_host = SimpleNamespace(items=lambda: asked,
                                             values=nodes.values)
  return SimpleNamespace(ref=ref, dependencies=dependencies)


def _consumer(own, published, wrapper_chain=()):
  """A consumer, one published package, and the wrappers both request."""
  winpr = _requesting("winpr/system@host")
  freerdp = _requesting("freerdp/system@host", wrapper_chain, ["winpr"])
  package = _requesting("pkg/1.0", published)
  root = _requesting("consumer/1.0", own)
  root.dependencies.update({"winpr": winpr, "freerdp": freerdp, "pkg": package})
  root.dependencies.host = _Dependencies(
    {"winpr": winpr, "freerdp": freerdp, "pkg": package})
  return root


PACKAGES = "FreeRDP=freerdp WinPR=winpr"


def test_agreeing_requests_of_a_wrapper_pass(recipe):
  asked = {"components": "", "packages": PACKAGES}
  root = _consumer([("winpr", asked)], [("winpr", asked)],
                   [("winpr", asked)])
  assert recipe._option_conflicts(root) == []


def test_two_component_requests_name_both_and_the_line_to_widen(recipe):
  root = _consumer(
    [("winpr", {"components": "Core Tools", "packages": "WinPR=winpr:Core,Tools"})],
    [("winpr", {"components": "Core", "packages": "WinPR=winpr:Core"})])
  [refusal] = recipe._option_conflicts(root)
  assert "consumer/1.0 asks components='Core Tools' packages=''" in refusal
  assert "pkg/1.0 asks components='Core'" in refusal
  assert "Widen Require(WinPR ... SYSTEM COMPONENTS Core Tools) in pkg/1.0" in (
    refusal)


def test_a_packages_entry_the_wrapper_reads_must_agree(recipe):
  root = _consumer([("freerdp", {"components": "", "packages": PACKAGES})],
                   [("freerdp", {"components": "",
                                 "packages": "FreeRDP=freerdp"})])
  [refusal] = recipe._option_conflicts(root)
  assert "consumer/1.0 asks components='' packages='WinPR=winpr'" in refusal
  assert "pkg/1.0 asks components='' packages=''" in refusal
  assert "every package this wrapper finds" in refusal


def test_a_packages_entry_the_wrapper_never_reads_is_no_conflict(recipe):
  root = _consumer([("winpr", {"components": "", "packages": PACKAGES})],
                   [("winpr", {"components": "", "packages": "WinPR=winpr"})])
  assert recipe._option_conflicts(root) == []


def test_the_forced_wrapper_is_pinned_to_the_rendered_revision(recipe,
                                                               tmp_path):
  from buildutil import packaging
  entry = {"conan_name": "hostlib", "ref": "hostlib/system@host"}
  assert recipe._wrapper_ref(tmp_path, entry) == "hostlib/system@host"
  rendered = packaging.requires_parser().wrapper_recipe(tmp_path, "hostlib")
  rendered.parent.mkdir(parents=True)
  rendered.write_text("text")
  revision = packaging.requires_parser().recipe_revision(b"text")
  assert recipe._wrapper_ref(tmp_path, entry) == (
    f"hostlib/system@host#{revision}")
