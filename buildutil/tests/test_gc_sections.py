"""gc-sections dead-code log (engine._write_gc_sections_log).

The load-bearing behaviour: pull only the linker's `--print-gc-sections` lines
(each a dropped, unreferenced section = dead-code candidate) out of a captured
build, and write nothing else."""
from buildutil import engine


# A realistic GNU ld --print-gc-sections excerpt buried in ordinary build noise.
_BUILD = """\
[1/3] Linking CXX executable sources/bdxdump/bdxdump
/usr/bin/ld: removing unused section '.text._ZN9utilities4sha1' in file 'libutilities.a(sha1.cpp.o)'
/usr/bin/ld: removing unused section '.text._ZN9utilities10ring_buffer' in file 'libutilities.a(ring_buffer.cpp.o)'
[2/3] Linking CXX executable sources/bdxmcp/bdxmcp
ninja: build complete.
"""


def test_extracts_only_the_gc_lines(tmp_path):
  engine._write_gc_sections_log(tmp_path, _BUILD)
  log = (tmp_path / "gc-sections.log").read_text()
  # The two dropped sections, and nothing else from the build.
  assert "sha1" in log
  assert "ring_buffer.cpp.o" in log
  assert "Linking CXX" not in log
  assert "ninja:" not in log
  assert len(log.splitlines()) == 2


def test_empty_log_when_nothing_dropped(tmp_path):
  engine._write_gc_sections_log(tmp_path, "[1/1] Linking\nbuild complete.\n")
  assert (tmp_path / "gc-sections.log").read_text() == ""
