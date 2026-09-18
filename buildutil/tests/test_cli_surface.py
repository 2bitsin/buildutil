"""The CLI surface tells the truth (owner req, 2026-08-18):

1. EVERY subcommand has a --help entry. The pre-venv verbs (init,
   install, update, setup-skill, cache-build) are dispatched by
   __main__ before typer exists, which used to keep them out of
   `buildutil --help` entirely — invisible unless you already knew
   them.
2. The retired verbs (scratch, bench-gate) are GONE — from the listing
   and from dispatch.

All through the real entry point in a throwaway project, the
established stdlib-lane pattern (BUILDUTIL_SYSTEM=1: no venv
bootstrap)."""
import subprocess
import sys
from pathlib import Path

import pytest

from buildutil import initcmd

PKG_PARENT = Path(initcmd.__file__).resolve().parents[1]

PRE_VENV_VERBS = ("init", "install", "update", "setup-skill", "cache-build")


def _run(args, cwd):
  return subprocess.run(
    [sys.executable, "-m", "buildutil", *args],
    cwd=cwd, capture_output=True, text=True,
    env={"PATH": "/usr/bin:/bin", "HOME": str(cwd),
         "PYTHONPATH": str(PKG_PARENT), "BUILDUTIL_SYSTEM": "1"})


@pytest.fixture
def project(tmp_path):
  (tmp_path / "buildutil.toml").write_text('[project]\nname = "x"\n')
  return tmp_path


def test_the_top_level_help_lists_every_pre_venv_verb(project):
  proc = _run(["--help"], project)
  assert proc.returncode == 0, proc.stdout + proc.stderr
  for verb in PRE_VENV_VERBS:
    assert verb in proc.stdout, f"{verb} missing from buildutil --help"


def test_the_retired_verbs_are_gone_from_the_listing(project):
  proc = _run(["--help"], project)
  assert proc.returncode == 0, proc.stdout + proc.stderr
  assert "scratch" not in proc.stdout
  assert "bench-gate" not in proc.stdout


@pytest.mark.parametrize("verb", PRE_VENV_VERBS)
def test_each_pre_venv_verb_answers_its_own_help(verb, tmp_path):
  # no project on purpose: these verbs run where nothing exists yet,
  # and --help must too
  proc = _run([verb, "--help"], tmp_path)
  assert proc.returncode == 0, proc.stdout + proc.stderr
  assert f"buildutil {verb}" in proc.stdout


def test_globals_before_a_pre_venv_verb_reach_it_through_typer(project):
  """`buildutil --jobs 1 init --help` skips the stdlib pre-dispatch
  (argv[0] is a flag) — the typer-registered wrapper must carry it to
  the same argparse instead of 'No such command'."""
  proc = _run(["--jobs", "1", "init", "--help"], project)
  assert proc.returncode == 0, proc.stdout + proc.stderr
  assert "buildutil init" in proc.stdout


def test_the_retired_verbs_are_refused_when_invoked(project):
  for verb in ("scratch", "bench-gate"):
    proc = _run([verb], project)
    assert proc.returncode != 0, f"{verb} still dispatches"
