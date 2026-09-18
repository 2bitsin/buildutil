"""`buildutil extend` — declare a cmake extension for this project.

buildutil.cmake is the base every project gets; extensions (watcom, ...)
are DECLARED in buildutil.toml ([cmake] extensions) and rendered into the
gitignored runtime dir on every build — nothing is ever committed. This
verb edits the declaration for you and re-renders; editing the toml by
hand is exactly equivalent.
"""
from __future__ import annotations

import re

import typer

from .. import deposit
from ..app import app
from ..config import PROJECT, REPO_ROOT


def _declare(name: str) -> list[str]:
  """Add name to [cmake] extensions in buildutil.toml (idempotent),
  editing the file textually — tomllib reads, nothing stdlib writes."""
  toml = REPO_ROOT / "buildutil.toml"
  exts = list(PROJECT["cmake_extensions"])
  if name in exts:
    return exts
  exts.append(name)
  text = toml.read_text()
  line = f"extensions = {exts!r}".replace("'", '"')
  if re.search(r"(?m)^extensions\s*=", text):
    text = re.sub(r"(?m)^extensions\s*=.*$", line, text, count=1)
  elif "[cmake]" in text:
    text = text.replace("[cmake]", f"[cmake]\n{line}", 1)
  else:
    text += f"\n[cmake]\n{line}\n"
  toml.write_text(text)
  return exts


def listing_lines(available: list[str], declared: set[str]) -> list[str]:
  """The bare-`extend` output. The old form printed each name alone —
  a lone `watcom` with no framing read as the command doing nothing."""
  if not available:
    return ["no cmake extensions ship with this buildutil"]
  lines = ["cmake extensions (declare one: buildutil extend <name>):"]
  for e in available:
    mark = "  [declared in buildutil.toml]" if e in declared else ""
    lines.append(f"  {e}{mark}")
  return lines


@app.command()
def extend(
  name: str = typer.Argument(
    "", help="extension to declare (empty: list what exists)"),
):
  """Declare a cmake extension in buildutil.toml and re-render."""
  if not name:
    for line in listing_lines(deposit.extensions(),
                              set(PROJECT["cmake_extensions"])):
      typer.echo(line)
    return
  if name not in deposit.extensions():
    raise SystemExit(
      f"buildutil: no extension {name!r} "
      f"(have: {', '.join(deposit.extensions())})")
  exts = _declare(name)
  # PROJECT whole — see the note at engine.py's ensure() call
  out = deposit.ensure(REPO_ROOT, PROJECT, exts)
  typer.echo(f"{name} declared in buildutil.toml; rendered at {out}")
