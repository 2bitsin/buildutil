"""buildutil commands: the pre-venv verbs' typer presence.

init / install / update / setup-skill / cache-build run BEFORE the venv
(and typer) exist — __main__ dispatches them stdlib-only so they work
where no project or venv does. That dispatch made them invisible to
`buildutil --help`, which listed every other subcommand as if these did
not exist. Registering them here gives each its --help entry, and makes
a line with global flags before the subcommand (`buildutil --jobs 2
init`, which skips the pre-dispatch) reach the same implementation
instead of erroring on an unknown command.

Each wrapper forwards its argv verbatim to the same stdlib main — the
detailed flag help lives there (argparse), one `buildutil <verb>
--help` away.
"""
from __future__ import annotations

import typer

from ..app import app

# forward everything: the stdlib argparse behind each verb owns the flags
_FORWARD = {"allow_extra_args": True, "ignore_unknown_options": True}


@app.command(context_settings=_FORWARD)
def init(ctx: typer.Context) -> None:
  """Scaffold a buildable project: buildutil.toml, root CMakeLists,
  sources/ with an example module, conanfile, .gitignore — never
  overwriting existing files. Flags: `buildutil init --help`."""
  from .. import initcmd
  initcmd.main(list(ctx.args))


@app.command(context_settings=_FORWARD)
def install(ctx: typer.Context) -> None:
  """Vendor the running buildutil into the project (.buildutil/ +
  ./buildutil launcher, both committed) so the tool travels with the
  repo; in an untouched directory also runs init. Flags: `buildutil
  install --help`."""
  from .. import installcmd
  installcmd.main(list(ctx.args))


@app.command(context_settings=_FORWARD)
def update(ctx: typer.Context) -> None:
  """Upgrade the installed buildutil from the site package index —
  the pip install, or the vendored copy in place. Flags: `buildutil
  update --help`."""
  from .. import updatecmd
  updatecmd.update(list(ctx.args))


@app.command("setup-skill", context_settings=_FORWARD)
def setup_skill(ctx: typer.Context) -> None:
  """(Re)install the buildutil skill for coding agents detected at the
  project root. Flags: `buildutil setup-skill --help`."""
  from .. import skillcmd
  skillcmd.main(list(ctx.args))


@app.command("cache-build", context_settings=_FORWARD)
def cache_build(ctx: typer.Context) -> None:
  """Build this project inside the conan cache against the toolchain
  conan generated — internal plumbing invoked by published recipes'
  build() (see `publish --bake-buildutil`). Flags: `buildutil
  cache-build --help`."""
  from .. import cachebuild
  cachebuild.main(list(ctx.args))
