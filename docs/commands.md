# The command line

One driver stands in front of conan, cmake, ninja, ctest and the rest, so
a project is built, tested, measured, analysed and shipped through one
vocabulary that does not change between machines. Global flags go
**before** the subcommand; per-command options go after it. The two
watchdog switches are the exception and are legal anywhere on the line,
because they are about the invocation rather than about the verb.

## Driver discipline

A buildutil project is driven. `cmake`, `ninja`, `ctest` and `conan`
invoked by hand fail with an explanation naming the buildutil command to
use instead: the driver exports `BUILDUTIL=<version>` to every child it
runs, and the rendered machinery checks for it at configure, on every
build through an always-built guard target, and before any test through a
ctest setup fixture, while the scaffolded conanfile checks it in
`validate()`. Driving a tool by hand skips the venv, the conan profile,
the rendered machinery and the install mirror — half-working results that
cost more than the refusal. For forensic debugging, `BUILDUTIL=1 <tool>
...` is the explicit opt-out, and it behaves identically to the driver,
because the PATH and working-directory contracts are ctest test
properties rather than runner flags.

## Global flags

| flag | default | what it does |
|---|---|---|
| `--version`, `-V` | | prints the version and the package path, plus the rendered machinery's own version inside a project |
| `--write-log-to PATH` | | tees fd 1 and fd 2 — this process and every subprocess — to a file |
| `--clear-logs` | off | with `--write-log-to`, deletes every `*.log` beside it first |
| `--max-errors N` | 0 | becomes `-fmax-errors` on gcc, `-ferror-limit` on clang; no equivalent on MSVC |
| `--fail-fast` | off | sugar for `--max-errors 1`; an explicit `--max-errors` wins |
| `--jobs N`, `-j N` | 80 | build parallelism as a **percent** of cores, clamped to 1..100 |
| `--jump-to-error N` | 0 | on a failed build, opens the first N in-project error sites with `code -r -g` |
| `--no-watchdog` | off | drops the wall-clock budget for this invocation |
| `--watchdog-budget SECONDS` | 180 | moves the budget; an explicit value arms the watchdog even in CI |
| `--no-timing` | off | suppresses the `buildutil: command took N.Ns` line |
| `--no-conan-update` | off | resolves version ranges against the local conan cache only |

The **watchdog** warns at a third of the budget, says so again at two
thirds, and at the budget terminates the whole child tree and exits 3.
Its stated purpose is a sense of time: a suite that quietly went from four
minutes to forty reads exactly like one that was always forty, so a budget
wants to be tight enough that an ordinary run trips the warning. It is off
by default when `$CI` is set, because the job timeout is already the
bound, and the per-verb defaults are 1800 s for `bench`, 2400 s for
`coverage` and 900 s for `analyze`.

`--option NAME=VALUE` is repeatable and accepted by `build`, `test`,
`run`, `bench`, `coverage`, `analyze`, `godbolt` and `vscode`; it chooses
one of the project options `buildutil.toml` declares, for one build. See
[options.md](options.md).

`--compiler` selects a lane on `deps`, `build`, `test`, `bench`,
`coverage`, `run`, `analyze`, `godbolt` and `publish`; the spellings are
`gcc`, `clang`, `apple-clang`, `msvc`, `wine-msvc`, `osxcross` and
`emscripten`, and the default is autodetection (`analyze` defaults to
clang). See [toolchains.md](toolchains.md). `--conan-home PATH` moves the
conan cache off the default `<repo>/_conanhome`.

The dependency upload after a build is the fleet's binary cache, so it
happens by default. The switch that turns it off is
`--skip-dependency-upload-so-everyone-rebuilds-from-source`: nobody types
that casually, which is the point — the old `--no-upload` had become a
flag people passed out of habit, its warning filtered away, and every
pipeline rebuilt ffmpeg, opencv and x264 from source. Typing `--no-upload`
on a build verb now names the replacement and exits 2. `publish
--no-upload` is a different flag on a different verb and still means a
local dry run.

## The pre-project verbs

`init`, `install`, `update`, `setup-skill` and `cache-build` run before
any project or venv exists: they are stdlib-only and are dispatched before
typer is even imported. Everything else requires a `buildutil.toml` in the
current directory or an ancestor and refuses without one.

**`buildutil init`** scaffolds a buildable project: `buildutil.toml`, a
four-line root `CMakeLists.txt`, a `sources/CMakeLists.txt` with
GoogleTest and google-benchmark wired through `Require(...)`, a
`conanfile.py` that parses those same calls, a `.gitignore` covering the
`_*` convention, a `./buildutil` launcher, and an example `hello` module
carrying a library, an executable, a test and a bench. Existing files are
never overwritten and the example is skipped when `sources/` already
exists; `--bare` gets the toml and the machinery alone. `--name`,
`--cmake-prefix` and `--module-prefix` name the project and its prefixes,
`--package library|application`, `--no-package` and `--package-name`
answer the packaging question ahead of the prompt, and `--no-agents`
skips the agent skill.

**`buildutil install`** vendors the whole running package into a tracked
`<repo>/.buildutil/` and writes the `./buildutil` launcher, so a fresh
clone needs nothing but python. In an untouched directory it also runs
`init` unless `--no-init`. The launcher prefers a vendored copy over
anything installed, the project venv over `PATH`, and `python3` only to
create that venv; it is POSIX `sh`, because a fresh system promises no
more than that. A foreign `./buildutil` is never clobbered.

**`buildutil update`** self-upgrades the copy that is running, on the
interpreter that owns it. It is `pip install -U buildutil`, and pip's own
configuration decides where that comes from; a source is used only when
one is known, in order: `--source URL` (an index URL or a git URL,
`--index-url` still accepted), then `[update] source` in
`buildutil.toml`, then wherever the running copy was installed from — pip
records a git or URL install, and `buildutil install` stamps the origin
beside a vendored copy. With a git source a pin picks whichever of
`X.Y.Z` and `vX.Y.Z` the repository actually tags, asked over `git
ls-remote`, because forges differ on the prefix. Outside a project it
updates to latest; inside one, `[buildutil] version` pins it and `--pin`
overrides that. A pin below 0.12.0 is refused — older copies cannot see
the pin, so their next update would jump to latest and ping-pong forever
— and an exact pin downgrades, since pip's `==` replaces whatever is
installed. `--dry-run` prints the command with any credentials masked,
and the command refuses to run while a build is live.

**`buildutil setup-skill`** installs the agent skill; see
[agents.md](agents.md). **`buildutil cache-build`** is not for hands: a
published recipe's `build()` calls it to build inside the conan cache,
and it is described in [packaging.md](packaging.md).

## Building

**`buildutil setup`** re-renders the cmake machinery, rewrites the venv
console script, forces a fresh conan remote registration and login, and
installs the agent skill (`--no-agents` skips that).

**`buildutil deps`** pre-installs the conan dependency graph without
building. With none of `--release`, `--debug` or `--relwithdebinfo`, all
three configurations are warmed.

**`buildutil build`** runs conan install, cmake configure and cmake build,
then installs into `_install/` and uploads the dependency binaries.
`--release` and `--debug` pick the configuration (Debug is this verb's
default), `--no-tests` drops the test targets and the test-only
requirements with them, `--include-bench` builds the benches that are off
by default, `--target NAME[,NAME...]` builds only those cmake targets and
skips the install and the upload, and `--gc-sections` links with
`--gc-sections --print-gc-sections` and writes the dropped sections,
demangled, to `_build/<profile>/gc-sections.log` as a dead-code report.

**`buildutil clean`** wipes transient artifacts: bare, the coverage report
and every `*.gcda`; `--all` takes `_build/` and `_install/`; `--nuke` is
`rm -rf _*` at the project root behind a confirmation `--yes` skips.
**`buildutil kill`** kills the process group of a build recorded in
`_build/.build.pid`. **`buildutil cache-clean`** prunes the conan cache's
non-critical folders.

**`buildutil module list|enable|disable`** moves modules in and out of the
build locally, writing `_bdudata/modules.ini`; the committed default is
`[modules] dormant`. **`buildutil config`** is buildutil's own build-time
configuration, persisted in `_bdudata/config.ini` — today one option,
`module_linkage`, whose `shared` value makes untagged modules shared
libraries. **`buildutil extend [NAME]`** declares a cmake extension in
`buildutil.toml`; bare, it lists what ships and what is declared.

## Running, testing and measuring

`test`, `bench`, `coverage`, `run` and `analyze` have a page of their own:
[testing.md](testing.md). `publish` has [packaging.md](packaging.md).
`vscode` regenerates the editor wiring and is described in
[agents.md](agents.md).

## `buildutil godbolt`

A source-and-assembly HTML report, named in tribute to Matt Godbolt's
Compiler Explorer and built the same way: every translation unit is
recompiled with `-S -g` under its exact flags from
`compile_commands.json`, so the assembly is the chosen configuration's
and not an approximation of it. Instructions map back to source lines
through the `.loc` directives, directive noise and unreferenced labels
are filtered, and every C++ symbol is demangled through one batched
`c++filt` (optional — without it the report degrades to mangled names).

Each page shows the source and its assembly side by side with matching
lines sharing a colour: hover highlights the counterpart, click scrolls
the other pane there, assembly inlined from another file is dimmed with a
`path:line` tooltip, and every unmapped line says why in its tooltip.
Every call, jump and symbol reference is a link — to that function's
assembly on its own page, across translation units, or to the local
label — and the index's function inventory links the same way. The pages
are self-contained HTML: no CDN, no node, no docker.

The project's code leads. A function counts as project code when its line
info maps into the repo; dependency and standard-library template bodies
the project never transitively references are hidden outright as
instantiation noise, and the hidden count is printed rather than left
silent. Referenced dependency code is kept so links never dead-end, but
always second — folded behind a disclosure on the index and grouped in
the jump menu. Test and bench translation units are excluded outright,
since scaffolding assembly is not what the report is for.

`-f REGEX` (repeatable, matched against demangled names) scopes the
report to chosen functions and `-m MODULE` to chosen modules;
`--project-only` drops dependency code completely and `--all-functions`
shows everything, and asking for both is an error. `--include-tests` and
`--include-benches` bring the scaffolding back, `--release` / `--debug`
pick the configuration, `--no-build` reuses what is there, and `-o DIR`
picks the output directory, which defaults to
`_build/<profile>/godbolt-report/`. gcc and clang only; MSVC exits 2.

## Exit codes

`0` is success. `1` is the ordinary failure — a build that did not
compile, tests that failed, a coverage gate that was not met, a binary
`run` could not find, an aborted `clean --nuke`, an unknown module name.
`2` is a usage error: a bad `--option` value, a malformed `config`
assignment, a compiler that is not installed, `--no-upload` on a build
verb, a version range in a published recipe without
`--allow-version-ranges`. `3` is the watchdog. Every `buildutil.toml`
validation failure exits 1 with its own message, at the command the
project ran rather than three layers down.
