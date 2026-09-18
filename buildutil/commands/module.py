"""buildutil module — toggle which sources/ modules build.

Thin CLI over buildutil.modules; writes _bdudata/modules.ini, the same file
cmake reads to drop dormant modules from the build. Take effect on the next
configure (re-run a build).
"""
from __future__ import annotations

import typer

from .. import modules
from ..app import app
from ..config import MODULES_INI, REPO_ROOT

SOURCES = REPO_ROOT / "sources"

module_app = typer.Typer(no_args_is_help=True, help="Enable/disable sources/ modules (local build toggles).")
app.add_typer(module_app, name="module")


def _check(names: list[str]) -> None:
  known = set(modules.available(SOURCES))
  unknown = [name for name in names if name not in known]
  if unknown:
    typer.echo(f"module: unknown module(s) {', '.join(unknown)}; "
               f"valid: {', '.join(sorted(known))}", err=True)
    raise typer.Exit(1)


@module_app.command("list")
def module_list() -> None:
  """Show every available module and whether it's on or dormant."""
  for name, dormant in modules.status(SOURCES, MODULES_INI):
    typer.echo(f"  {'off' if dormant else 'on '}  {name}")


@module_app.command("enable")
def module_enable(names: list[str] = typer.Argument(..., help="Module name(s) to build.")) -> None:
  """Mark module(s) as building (remove from [disabled])."""
  _check(names)
  for name in names:
    modules.set_dormant(MODULES_INI, name, dormant=False)
  typer.echo(f"enabled: {', '.join(names)}   (re-run a build to apply)")


@module_app.command("disable")
def module_disable(names: list[str] = typer.Argument(..., help="Module name(s) to skip.")) -> None:
  """Mark module(s) dormant (add to [disabled]); they drop from the build."""
  _check(names)
  for name in names:
    modules.set_dormant(MODULES_INI, name, dormant=True)
  typer.echo(f"disabled: {', '.join(names)}   (re-run a build to apply)")
