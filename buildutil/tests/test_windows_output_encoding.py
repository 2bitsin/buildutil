"""The driver's own output is UTF-8, and on Windows it has to say so.

python encodes stdout in the ANSI codepage as soon as it is not a console
-- a PIPE, which is every CI job -- so the status line's box-drawing rules
end a successful run with

  UnicodeEncodeError: 'charmap' codec can't encode characters in
  position 2-13: character maps to <undefined>

From outside, a CI job looks like this: MSVC compiled the project,
export-pkg and test_package both passed, and the job failed on the line
that says so.
"""
import sys

from buildutil.__main__ import _speak_utf8, main


class _Stream:
  def __init__(self, reconfigurable=True, raises=None):
    self.encoding_asked = None
    self.raises = raises
    if not reconfigurable:
      del self.reconfigure

  def reconfigure(self, encoding=None, **kw):
    if self.raises is not None:
      raise self.raises
    self.encoding_asked = encoding


def test_stdout_and_stderr_are_asked_for_utf8(monkeypatch):
  out, err = _Stream(), _Stream()
  monkeypatch.setattr(sys, "stdout", out)
  monkeypatch.setattr(sys, "stderr", err)
  _speak_utf8()
  assert out.encoding_asked == "utf-8"
  assert err.encoding_asked == "utf-8"


def test_a_stream_that_cannot_be_reconfigured_is_left_alone(monkeypatch):
  # A replaced sys.stdout (pytest's own capture, a library's tee) has no
  # reconfigure at all, and a detached one raises. Neither is a reason to
  # take the process down before it has done anything.
  class _Plain:
    pass
  monkeypatch.setattr(sys, "stdout", _Plain())
  monkeypatch.setattr(sys, "stderr", _Stream(raises=ValueError("detached")))
  _speak_utf8()          # must not raise


def test_only_windows_reconfigures(monkeypatch):
  # POSIX already writes UTF-8; touching the streams there would be a new
  # way to be wrong about someone's locale.
  called = []
  monkeypatch.setattr("buildutil.__main__._speak_utf8",
                      lambda: called.append(True))
  monkeypatch.setattr("platform.system", lambda: "Linux")
  monkeypatch.setattr(sys, "argv", ["buildutil", "--version"])
  main()
  assert called == []

  monkeypatch.setattr("platform.system", lambda: "Windows")
  monkeypatch.setattr(sys, "argv", ["buildutil", "--version"])
  main()
  assert called == [True]


# --------------------------------------------------------------- capture ---
#
# The other half: reconfiguring our own streams fixed what buildutil
# PRINTS. What it READS is decoded by the interpreter's own encoding, and on
# Windows that is the ANSI codepage -- so every `subprocess.run(...,
# text=True)` capture of a tool's output and every `read_text()` of a file
# is a cp1252 decode. A cmake warning with a rule in it, a conan line with
# an em-dash, a source file with an accented comment: UnicodeDecodeError,
# somewhere in the middle of a build. `-X utf8` on the re-exec is the only
# place that can be fixed -- UTF-8 mode cannot be turned on from inside a
# running interpreter.

import subprocess
from pathlib import Path

import buildutil.bootstrap as bootstrap
from buildutil import config


def _re_exec_argv(monkeypatch, system):
  """Drive ensure() to the re-exec and capture the argv it would run."""
  monkeypatch.setattr(bootstrap.platform, "system", lambda: system)
  monkeypatch.setattr(bootstrap, "_create_venv", lambda: None)
  monkeypatch.setattr(bootstrap.config, "VENV_DIR", Path("/nowhere/_pyvenv"))
  monkeypatch.setattr(bootstrap.config, "VENV_PY",
                      Path("/nowhere/_pyvenv/bin/python"))
  monkeypatch.setattr(bootstrap.config, "load_dotenv", lambda: None)
  monkeypatch.delenv("BUILDUTIL_SYSTEM", raising=False)
  monkeypatch.setattr(sys, "argv", ["buildutil", "build", "--release"])

  seen = {}

  def _run(cmd, **kw):
    seen["argv"] = cmd
    return subprocess.CompletedProcess(cmd, 0)

  monkeypatch.setattr(bootstrap.subprocess, "run", _run)
  monkeypatch.setattr(bootstrap.os, "execv",
                      lambda exe, cmd: seen.setdefault("argv", cmd))
  try:
    bootstrap.ensure()
  except SystemExit:
    pass                       # the Windows path exits with the child's code
  return seen["argv"]


def test_the_windows_re_exec_runs_the_interpreter_in_utf8_mode(monkeypatch):
  argv = _re_exec_argv(monkeypatch, "Windows")
  assert argv[1:3] == ["-X", "utf8"], argv
  assert argv[3:5] == ["-m", "buildutil"], argv
  assert argv[5:] == ["build", "--release"], "the command line is untouched"


def test_posix_is_left_alone(monkeypatch):
  argv = _re_exec_argv(monkeypatch, "Linux")
  assert "-X" not in argv
  assert argv[1:3] == ["-m", "buildutil"], argv
