"""--jump-to-error location parsing (engine._parse_error_locations).

The load-bearing behaviour: anchor on the error itself, never the include /
instantiation chain or note lines, and only inside the project."""
from buildutil import engine


# A realistic gcc diagnostic: an include chain, then the error, then a note.
_GCC = """\
In file included from {root}/sources/decodex86/decoder.hpp:4,
                 from {root}/sources/decodex86/decoder.cpp:1:
{root}/sources/decodex86/instruction.hpp:373:52: error: 'ValueT' has not been declared
  373 |       return {{ ._type = ValueT::TYPE }};
      |                          ^~~~~~
{root}/sources/decodex86/instruction.hpp:380:7: note: declared here
"""


def test_picks_the_error_not_the_include_chain(tmp_path):
  locations = engine._parse_error_locations(_GCC.format(root=tmp_path),
                                            root=tmp_path)
  assert locations == [
    f"{tmp_path}/sources/decodex86/instruction.hpp:373:52"]


def test_drops_locations_outside_the_project(tmp_path):
  output = "/usr/include/c++/15/vector:200:5: error: no matching function\n"
  assert engine._parse_error_locations(output, root=tmp_path) == []


def test_requires_a_column(tmp_path):
  # A column-less diagnostic (linker-style) is not a jump target.
  output = f"{tmp_path}/sources/x.cpp:42: error: undefined reference\n"
  assert engine._parse_error_locations(output, root=tmp_path) == []


def test_dedups_in_first_seen_order(tmp_path):
  one = f"{tmp_path}/sources/a.cpp:10:3: error: one\n"
  two = f"{tmp_path}/sources/b.cpp:5:1: error: two\n"
  locations = engine._parse_error_locations(one + two + one, root=tmp_path)
  assert locations == [
    f"{tmp_path}/sources/a.cpp:10:3",
    f"{tmp_path}/sources/b.cpp:5:1",
  ]
