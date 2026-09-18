"""`buildutil vscode` -- manually regenerate the derived .vscode files.
They also refresh automatically on every configure; this is for when you
add a module/target and want the tasks/launch entries now without a full
build."""
from __future__ import annotations

import typer

from ..app import app, _option_option
from .. import vscode


@app.command("vscode")
def vscode_command(
  option: list[str] = _option_option(),
) -> None:
  """Regenerate .vscode/{c_cpp_properties,tasks,launch}.json from the
  live build (profiles, targets, test/bench targets)."""
  modules = vscode.discover()
  written = vscode.refresh()
  typer.echo(f"buildutil vscode: {len(modules)} module(s) under sources/"
             + (f" ({', '.join(sorted(modules))})" if modules else
                " — no tasks/launch targets to offer; a module is a "
                "sources/ subdir with a CMakeLists.txt"))
  typer.echo("buildutil vscode: wrote " + ", ".join(written))
