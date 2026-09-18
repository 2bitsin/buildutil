"""`build` and `test` must not silently disagree about which tree they act on."""
import os
from pathlib import Path

import pytest

from buildutil import engine


@pytest.fixture
def tree(tmp_path, monkeypatch):
  monkeypatch.chdir(tmp_path)
  monkeypatch.setattr(engine, "LAST_BUILD_TYPE_FILE",
                      tmp_path / "_build" / ".last-build-type")
  return tmp_path


def test_no_build_yet_means_no_recorded_type(tree):
  assert engine.last_build_type() is None


def test_a_build_records_the_type_it_acted_on(tree):
  engine._record_last_build_type("Debug")
  assert engine.last_build_type() == "Debug"


def test_a_garbled_stamp_is_ignored_rather_than_obeyed(tree):
  """It feeds a build_type straight into cmake, so anything unrecognised
  has to fall back rather than propagate."""
  engine._record_last_build_type("Debug")
  engine.LAST_BUILD_TYPE_FILE.write_text("; rm -rf /\n")
  assert engine.last_build_type() is None


def test_an_unwritable_stamp_does_not_fail_the_build(tree, monkeypatch):
  """A stamp is a convenience; losing it must never break a build."""
  def boom(*a, **k):
    raise OSError("read-only")
  monkeypatch.setattr(Path, "write_text", boom)
  engine._record_last_build_type("Release")      # must not raise
