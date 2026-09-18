"""Every buildutil.toml knob the RENDERER reads has to survive the trip
from config to deposit.ensure().

0.17.1 shipped [cmake] export_module_headers inert: config parsed it,
_render() read it, and all four ensure() call sites in between handed
over a freshly built dict holding only the two prefix keys. Each half
was tested and each half was right; nothing tested the seam, so the
feature was dead on arrival in every project that asked for it.

Two guards, because the failure has two shapes. The structural one
catches a call site that hand-picks keys (how it happened). The
behavioural one drives a real command end to end (what it cost)."""
import ast
import subprocess
import sys
from pathlib import Path

from buildutil import deposit

PKG = Path(__file__).resolve().parents[1]

# every module that renders the deposit from a LOADED project config
CALL_SITES = ("engine.py", "commands/build.py", "commands/extend.py")


def _ensure_calls(tree: ast.AST) -> list[ast.Call]:
  return [n for n in ast.walk(tree)
          if isinstance(n, ast.Call)
          and isinstance(n.func, ast.Attribute) and n.func.attr == "ensure"]


def test_no_call_site_hand_picks_the_config_keys():
  """The cfg argument must be PROJECT itself. A dict literal there is the
  bug: it freezes today's key set, so the next [cmake] knob is dropped on
  the floor by a call site written before it existed — silently, because
  _render only ever .get()s what it needs."""
  for rel in CALL_SITES:
    tree = ast.parse((PKG / rel).read_text())
    calls = _ensure_calls(tree)
    assert calls, f"{rel}: no deposit.ensure() call found — did it move?"
    for call in calls:
      cfg = call.args[1]
      assert not isinstance(cfg, ast.Dict), (
        f"{rel}:{call.lineno} builds the deposit cfg as a dict literal. "
        f"Pass PROJECT whole — every knob _render reads lives in it, and "
        f"a subset silently drops the ones added after this line.")
      assert isinstance(cfg, ast.Name) and cfg.id == "PROJECT", (
        f"{rel}:{call.lineno} passes {ast.dump(cfg)}, expected PROJECT")


def test_every_key_the_renderer_reads_exists_in_the_project_defaults():
  """_render reading a key that config never defines is the same bug from
  the other end — it would .get() None forever and nobody would notice."""
  from buildutil.config import _load_project
  defaults = _load_project()
  source = (PKG / "deposit.py").read_text()
  tree = ast.parse(source)
  read = set()
  for node in ast.walk(tree):
    # cfg["x"] and cfg.get("x")
    if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
        and node.value.id == "cfg"
        and isinstance(node.slice, ast.Constant)):
      read.add(node.slice.value)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "cfg"
        and node.args and isinstance(node.args[0], ast.Constant)):
      read.add(node.args[0].value)
  assert read, "found no cfg reads in deposit.py — did the renderer move?"
  missing = sorted(k for k in read if k not in defaults)
  assert not missing, (
    f"deposit._render reads {missing}, which config._load_project never "
    f"defines — those render as their falsy default in every project")


def test_init_honours_an_existing_tomls_export_flag(tmp_path):
  """The behavioural half, through a real command: `buildutil init` in a
  project that already declares the flag must render it ON. This is the
  exact reproduction from the bug report, minus the compiler."""
  (tmp_path / "buildutil.toml").write_text(
    '[project]\nname = "acme"\ncmake_option_prefix = "ACME"\n'
    'module_define_prefix = "ACM"\n'
    "[cmake]\nextensions = []\nexport_module_headers = true\n")
  cp = subprocess.run(
    [sys.executable, "-m", "buildutil", "init", "--bare"],
    cwd=tmp_path, capture_output=True, text=True,
    env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(PKG.parent)})
  assert cp.returncode == 0, cp.stderr
  machinery = (deposit.cmake_dir(tmp_path) / "buildutil.cmake").read_text()
  assert "if(ON)" in machinery, (
    "init re-rendered the deposit but dropped the project's [cmake] "
    "export_module_headers — the knob is inert again")


def test_init_without_the_flag_still_renders_it_off(tmp_path):
  cp = subprocess.run(
    [sys.executable, "-m", "buildutil", "init", "--name", "acme", "--bare"],
    cwd=tmp_path, capture_output=True, text=True,
    env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(PKG.parent)})
  assert cp.returncode == 0, cp.stderr
  machinery = (deposit.cmake_dir(tmp_path) / "buildutil.cmake").read_text()
  assert "if(OFF)" in machinery
