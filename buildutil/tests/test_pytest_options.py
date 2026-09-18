"""pytest_args_from_options: the `-O` passthrough shared by `test` and
`coverage`, pure over its input."""
from buildutil.app import pytest_args_from_options as translate


def test_nothing_in_nothing_out():
  assert translate([]) == []


def test_a_bare_name_is_a_bool_flag():
  assert translate(["update-goldens"]) == ["--update-goldens"]


def test_a_named_value_becomes_a_long_option():
  assert translate(["durations:0"]) == ["--durations=0"]


# The whole reason this is a shared function with a test: pytest registers -k
# and -m with NO long form, so `--k=expr` is not rejected as an unknown flag --
# it is taken as a file path and the run dies with "file or directory not
# found". A one-character name has to come out as a short option.
def test_single_letter_names_become_short_options():
  assert translate(["k:two-groups"]) == ["-k", "two-groups"]
  assert translate(["m:not guest"]) == ["-m", "not guest"]


def test_options_accumulate_in_order():
  assert translate(["m:not guest", "durations:0", "update-goldens"]) == [
    "-m", "not guest", "--durations=0", "--update-goldens"]
