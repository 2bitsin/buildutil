import pytest

from buildutil import engine


def test_skipping_the_dependency_upload_warns_with_the_cost(capsys):
  engine._warn_dependency_upload_skipped(True)
  err = capsys.readouterr().err
  assert "--skip-dependency-upload-so-everyone-rebuilds-from-source" in err
  assert "WON'T be cached" in err and "highly encouraged" in err


def test_default_upload_is_silent(capsys):
  engine._warn_dependency_upload_skipped(False)
  assert capsys.readouterr().err == ""


RENAMED = "--skip-dependency-upload-so-everyone-rebuilds-from-source"
BUILD_VERBS = ("build", "test", "run", "bench")


def _app():
  from buildutil.commands import bench, build, publish, run, test  # noqa: F401
  from buildutil.app import app
  return app


def _cli():
  from typer.main import get_command
  return get_command(_app())


def _invoke(*args):
  from typer.testing import CliRunner
  return CliRunner().invoke(_app(), list(args))


def _stderr(result):
  """click 8.2+ keeps stderr apart; older runners fold it into output."""
  try:
    return result.stderr
  except ValueError:
    return result.output


@pytest.mark.parametrize("verb", BUILD_VERBS)
def test_the_old_no_upload_is_refused_with_the_new_name(verb):
  result = _invoke(verb, "--no-upload")
  assert result.exit_code == 2
  err = _stderr(result)
  assert "renamed" in err and RENAMED in err
  assert f"buildutil {verb}:" in err


@pytest.mark.parametrize("verb", BUILD_VERBS)
def test_the_old_no_upload_stays_out_of_help(verb):
  result = _invoke(verb, "--help")
  assert result.exit_code == 0, result.output
  assert "--no-upload" not in result.output
  declared = [opt for param in _cli().commands[verb].params
              for opt in getattr(param, "opts", [])]
  assert RENAMED in declared


def test_publish_keeps_its_own_no_upload():
  option = next(p for p in _cli().commands["publish"].params
                if "--no-upload" in getattr(p, "opts", []))
  assert not option.hidden
