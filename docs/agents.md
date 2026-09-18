# Editors and agents

Two commands exist so that the conventions in this documentation reach
the two things that read a project without asking: an editor, and a
coding agent. Neither is a separate configuration to maintain — both are
regenerated from the live module inventory, and both are safe to run at
any time.

## `buildutil vscode`

Regenerates `.vscode/c_cpp_properties.json`, `tasks.json` and
`launch.json` from the modules that actually exist. It also runs
automatically on every configure, with a failure downgraded to a note on
stderr, because editor wiring is a convenience and a convenience must
never fail a build.

What it writes: per-module build, run and test tasks in debug,
relwithdebinfo and release flavours — and bench tasks, release only — for
exactly what each module has; godbolt tasks per module and whole-tree in
all three flavours, since the assembly *is* the flavour's; a launch
configuration per executable target, application, tests and benches
alike, in the debug-information flavours, pointing at build-tree binaries
with matching pre-launch build tasks; and dependency tasks per
configuration. Every generated task carries `--no-watchdog`,
`--max-errors=3` and `--jump-to-error=1`, which is the shape an editor
wants: no wall-clock budget behind a breakpoint, a short error list, and
the first error opened.

Run, test and bench tasks prompt for arguments or a filter, empty
allowed. vscode cannot persist a prompt's value, so buildutil remembers
it in `_bdudata/vscode-inputs.json`: the last value becomes the prompt's
next default *and* the launch configuration's argv, so running a program
from the terminal and then debugging it does not mean typing the
arguments twice.

`c_cpp_properties.json` is **upserted**: the just-built profile's entry
is refreshed and moved to the top, which is vscode's default, and no
other entry is ever dropped, so a tree that is also built for another
lane keeps its configuration. `tasks.json` and `launch.json` are
rewritten, and `.vscode/settings.json` is deliberately never touched —
that file is the person's, not the project's. Where the project declares
`[options]`, the generated configuration force-includes the same options
header the compiler gets, so IntelliSense and the debugger agree with the
compiler about what a file says. On macOS the launch configurations point
inside the application bundle, and a helper module gets none, since
launched alone it has nothing to serve.

Consumers of the generated files need the C/C++ extension, a debug
adapter (`gdb`, or `lldb` on macOS), a python debug adapter for the
python launch configuration, and either a `./buildutil` launcher or
`buildutil` on `PATH`.

## `buildutil setup-skill`

Installs a skill that teaches a coding agent this project's command line
and directory conventions — the driver discipline, the derived `_*`
directories, the command cheat sheet, the kind and platform tags, and a
reflect section that appears only when the project declares that
extension.

The target comes from `--agent-type`, or is autodetected from the
configuration directories at the project root, and every detected agent
gets its own copy under its own configuration directory. Exactly two
files are installed per agent, and they are removed and re-copied rather
than merged, so the copy is regenerated rather than drifting. `--zip
PATH` writes a portable archive of the same skill instead.

`buildutil init` and `buildutil setup` install it automatically for every
detected agent unless `--no-agents` is given.
