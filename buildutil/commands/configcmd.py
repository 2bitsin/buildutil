"""buildutil commands: config — build-time configuration options."""
from __future__ import annotations

import typer

from ..app import app
from .. import configopts


@app.command()
def config(
  assignments: list[str] = typer.Argument(
    None, help="NAME=VALUE pairs to set (validated); none = list."),
  reset: bool = typer.Option(
    False, "--reset",
    help="Forget saved values (of the NAMEs given, or all of them) — "
         "the next build falls back to env/default."),
):
  """List or set build-time configuration options.

  Values persist in _bdudata/config.ini (checkout-local); resolution
  is env (BUILDUTIL_OPT_<NAME>) > saved > default. `buildutil config
  module_linkage=shared` switches every untagged module to a shared
  library — in its own build tree, the profile dir carries the axis.
  """
  assignments = assignments or []
  if reset:
    names = [a for a in assignments]
    configopts.reset(names or None)
    typer.echo("reset: " + (", ".join(names) if names else "all options"))
    return
  if assignments:
    for pair in assignments:
      name, eq, value = pair.partition("=")
      if not eq:
        typer.echo(f"buildutil config: {pair!r} is not NAME=VALUE "
                   "(or pass --reset)", err=True)
        raise typer.Exit(code=2)
      configopts.set_value(name.strip(), value.strip())
      typer.echo(f"{name.strip()} = {value.strip()}  (saved)")
    return
  for name, value, source, help_text in configopts.describe():
    typer.echo(f"{name} = {value}  [{source}]")
    typer.echo(f"    {help_text}")
