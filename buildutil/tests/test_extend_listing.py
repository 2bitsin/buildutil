"""Bare `buildutil extend` must read as a listing, not as a no-op —
the old output was each extension name alone on a line."""
from buildutil.commands.extend import listing_lines


def test_listing_frames_the_output():
  lines = listing_lines(["watcom"], set())
  assert lines[0].startswith("cmake extensions")
  assert "buildutil extend <name>" in lines[0]
  assert lines[1].strip() == "watcom"


def test_declared_marker():
  lines = listing_lines(["watcom"], {"watcom"})
  assert "[declared in buildutil.toml]" in lines[1]


def test_empty_says_so():
  assert listing_lines([], set()) == [
    "no cmake extensions ship with this buildutil"]
