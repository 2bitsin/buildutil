"""buildutil_configure.emit — what bytes a generated file ends up holding.

A generated table is a build input. Written through Path.write_text()'s
default encoding it was the LOCALE's, so the same configure.py produced
different bytes under LANG=C than under a UTF-8 locale — silently mojibake
rather than an error, and only in the environments that had the narrow
locale (CI containers, cron, a runner's service account).
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

PYSUPPORT = Path(__file__).resolve().parents[1] / "pysupport"

SCRIPT = """\
import buildutil_configure as bc
bc.emit("table.inc", "caf\\u00e9 na\\u00efve \\u2014 \\u00b5s\\n")
"""


def _run(tmp_path, env_extra=None) -> Path:
  out = tmp_path / "out"
  shared = tmp_path / "shared"
  out.mkdir(exist_ok=True)
  shared.mkdir(exist_ok=True)
  script = tmp_path / "configure.py"
  script.write_text(SCRIPT, encoding="utf-8")
  env = {
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    "PYTHONPATH": str(PYSUPPORT),
    "CONFIGURE_OUTPUT_DIR": str(out),
    "CONFIGURE_SHARED_DIR": str(shared),
    "CONFIGURE_MANIFEST": str(tmp_path / "manifest"),
    **(env_extra or {}),
  }
  cp = subprocess.run([sys.executable, str(script)], cwd=tmp_path,
                      capture_output=True, text=True, env=env)
  assert cp.returncode == 0, cp.stderr
  return out / "table.inc"


EXPECTED = "café naïve — µs\n".encode("utf-8")


def test_emit_writes_utf8_whatever_the_locale_is(tmp_path):
  assert _run(tmp_path).read_bytes() == EXPECTED


# LANG=C alone proves nothing: since 3.7 python COERCES the C locale to
# UTF-8 (PEP 538) and would pass this test with the locale-dependent
# code still in place. Turn the coercion and UTF-8 mode off as well and
# the interpreter really does fall back to ASCII, which is the situation
# the reporter's runner was in.
NARROW = {"LC_ALL": "C", "LANG": "C",
          "PYTHONCOERCECLOCALE": "0", "PYTHONUTF8": "0"}


def test_a_narrow_locale_does_not_change_the_bytes(tmp_path):
  """The reported case: an environment whose default encoding cannot hold
  the table at all. Locale-dependent, this raises UnicodeEncodeError and
  fails the whole configure step; explicit, it just writes UTF-8."""
  assert _run(tmp_path, NARROW).read_bytes() == EXPECTED


def test_a_file_left_by_the_old_locale_default_is_rewritten(tmp_path):
  """The compare-before-write reads back as utf-8 now. A file the old
  code left in some other encoding may not decode at all — that has to
  count as CHANGED, or the first build after upgrading would keep the
  broken bytes forever."""
  out = tmp_path / "out"
  out.mkdir()
  stale = out / "table.inc"
  stale.write_bytes("café naïve — µs\n".encode("latin-1", "replace"))
  before = stale.read_bytes()
  assert before != EXPECTED
  assert _run(tmp_path).read_bytes() == EXPECTED


def test_emit_still_leaves_an_up_to_date_file_alone(tmp_path):
  """Not rewriting unchanged output is what keeps configure from
  re-triggering every dependent build; the encoding fix must not cost
  that."""
  first = _run(tmp_path)
  stamp = first.stat().st_mtime_ns
  again = _run(tmp_path)
  assert again.stat().st_mtime_ns == stamp, "an unchanged file was rewritten"
