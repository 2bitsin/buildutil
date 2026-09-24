"""declare(path, options=, defines=) writes one manifest line per flag, and refuses what cmake would split."""
import os
import subprocess
import sys
from pathlib import Path

PYSUPPORT = Path(__file__).resolve().parents[1] / "pysupport"


def _run(tmp_path, body):
  script = tmp_path / "configure.py"
  script.write_text("import buildutil_configure as bc\n" + body)
  env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(PYSUPPORT),
         "CONFIGURE_OUTPUT_DIR": str(tmp_path / "out"),
         "CONFIGURE_SHARED_DIR": str(tmp_path / "shared"),
         "CONFIGURE_MANIFEST": str(tmp_path / "manifest"), "CONFIGURE_MODULE": "core"}
  return subprocess.run([sys.executable, str(script)], cwd=tmp_path, capture_output=True,
                        text=True, env=env)


def _manifest(tmp_path):
  return (tmp_path / "manifest").read_text().splitlines()


def test_options_and_defines_are_one_line_each(tmp_path):
  done = _run(tmp_path, 'bc.declare("a.c", options=["-Wall", "-msse2"], defines=["X", "Y=2"])\n')
  assert done.returncode == 0, done.stderr
  path = (tmp_path / "a.c").resolve()
  assert _manifest(tmp_path) == [f"G {path}", f"O {path}\t-Wall", f"O {path}\t-msse2",
                                 f"M {path}\tX", f"M {path}\tY=2"]


def test_a_bare_declare_keeps_the_flags_an_earlier_one_gave(tmp_path):
  done = _run(tmp_path, 'bc.declare("a.c", options=["-Wall"])\nbc.declare("a.c")\n')
  assert done.returncode == 0, done.stderr
  assert f"O {(tmp_path / 'a.c').resolve()}\t-Wall" in _manifest(tmp_path)


def test_a_later_declare_with_options_replaces_them(tmp_path):
  done = _run(tmp_path, 'bc.declare("a.c", options=["-Wall"])\n'
                        'bc.declare("a.c", options=["-Wextra"])\n')
  assert done.returncode == 0, done.stderr
  flags = [line for line in _manifest(tmp_path) if line.startswith("O ")]
  assert flags == [f"O {(tmp_path / 'a.c').resolve()}\t-Wextra"]


def test_a_plain_declare_writes_no_flag_lines(tmp_path):
  done = _run(tmp_path, 'bc.declare("a.c")\n')
  assert done.returncode == 0, done.stderr
  assert _manifest(tmp_path) == [f"G {(tmp_path / 'a.c').resolve()}"]


def test_a_flag_cmake_would_split_is_refused(tmp_path):
  done = _run(tmp_path, 'bc.declare("a.c", options=["-Wa;-Wb"])\n')
  assert done.returncode != 0
  assert "'-Wa;-Wb'" in done.stderr


def test_a_define_that_is_not_a_name_is_refused(tmp_path):
  done = _run(tmp_path, 'bc.declare("a.c", defines=["-DX=1"])\n')
  assert done.returncode != 0
  assert "is not NAME or NAME=value" in done.stderr


def test_an_option_with_a_space_is_refused_with_the_shell_spelling(tmp_path):
  done = _run(tmp_path, 'bc.declare("a.c", options=["-include x.h"])\n')
  assert done.returncode != 0
  assert "spell the group 'SHELL:-include x.h'" in done.stderr


def test_a_shell_group_and_a_spaced_define_pass(tmp_path):
  done = _run(tmp_path, 'bc.declare("a.c", options=["SHELL:-include x.h"], defines=["TITLE=a b"])\n')
  assert done.returncode == 0, done.stderr
