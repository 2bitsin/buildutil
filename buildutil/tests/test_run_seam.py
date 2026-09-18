"""`buildutil run`'s default binary comes from the [run] table — the
pre-extraction driver hardcoded bossdeux's bdxmcp/bdxgui here, so on
any other project a bare `run` died on 'binary not found: bdxmcp'."""
import inspect

from buildutil.config import default_run_target


def _project(default="", **per_os):
  return {"run_default": default, "run_default_os": per_os}


def test_per_os_override_wins():
  p = _project("bdxmcp", windows="bdxgui", macos="bdxgui")
  assert default_run_target("Linux", p) == "bdxmcp"
  assert default_run_target("Windows", p) == "bdxgui"
  assert default_run_target("Darwin", p) == "bdxgui"


def test_default_alone_serves_every_os():
  p = _project("hello")
  for system in ("Linux", "Windows", "Darwin"):
    assert default_run_target(system, p) == "hello"


def test_unconfigured_is_empty_never_a_bossdeux_binary():
  p = _project()
  for system in ("Linux", "Windows", "Darwin"):
    assert default_run_target(system, p) == ""


def test_run_module_carries_no_project_binary_names():
  from buildutil.commands import run
  src = inspect.getsource(run)
  assert "bdxmcp" not in src and "bdxgui" not in src
