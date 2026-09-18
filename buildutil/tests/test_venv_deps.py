"""The venv refreshes when the declared dep SET changes.

Before this, `_create_venv` decided a venv was current by importing a
hardcoded typer/conan/gcovr/pytest — so [venv] extra_deps, and any dep a
declared cmake extension brings in, were never installed on an existing
checkout.
"""
from buildutil import bootstrap, config


def test_the_fingerprint_follows_the_declared_deps(monkeypatch):
  monkeypatch.setattr(config, "VENV_DEPS", ["typer==1", "conan==2"])
  before = bootstrap.deps_fingerprint()
  monkeypatch.setattr(config, "VENV_DEPS", ["typer==1", "conan==2", "libclang==3"])
  assert bootstrap.deps_fingerprint() != before


def test_the_fingerprint_is_stable_for_the_same_set(monkeypatch):
  monkeypatch.setattr(config, "VENV_DEPS", ["a==1", "b==2"])
  assert bootstrap.deps_fingerprint() == bootstrap.deps_fingerprint()


def test_order_is_part_of_the_set(monkeypatch):
  # deps are a list, not a set: reordering changes what pip resolves
  monkeypatch.setattr(config, "VENV_DEPS", ["a==1", "b==2"])
  one = bootstrap.deps_fingerprint()
  monkeypatch.setattr(config, "VENV_DEPS", ["b==2", "a==1"])
  assert bootstrap.deps_fingerprint() != one
