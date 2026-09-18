# buildutil

Config-seamed C++ build driver: conan install, cmake configure/build,
ctest, gcovr coverage, benchmarks, static analysis and vscode wiring
behind one CLI. It began as one C++ project's in-tree driver and was
extracted so that any project can carry it.

## Install

```
pip install git+https://github.com/2bitsin/buildutil
```

The public `main` only ever holds releases, so that line installs the
latest one; append `@vX.Y.Z` to pin a particular release.

That puts the driver on PATH for every project on the machine. The other
way is to vendor it: `buildutil install` copies the package into
`<repo>/.buildutil/` and writes a `./buildutil` launcher, both committed,
so a fresh clone of the project needs nothing but python. The two modes
are laid out under [Two install modes](#two-install-modes) below.

## The seam

buildutil is generic; the project it drives is not. Everything
project-specific lives in **`buildutil.toml` at the repo root**, and the
repo root IS "the nearest ancestor holding `buildutil.toml`"
(`BUILDUTIL_ROOT` overrides). No key is required — an empty file marks
the root and takes every default:

```toml
[project]
name = "bossdeux"                 # vscode task labels, messages
cmake_option_prefix = "BOSSDEUX"  # -D<PREFIX>_COVERAGE / _GC_SECTIONS /
                                  # _MAX_ERRORS / _SKIP_TEST_DEPS ...
module_define_prefix = "BDX"      # <PREFIX>_<NAME>_ENABLED=1 defines,
                                  # <PREFIX>_MODULE_DEFINES cmake list

[options]
contracts = true    # project options: the value is the DEFAULT, `--option
max_depth = 32      # name=value` chooses another for one build, and every
greeting = "hello"  # target of the project compiles with BOSSDEUX_CONTRACTS,
                    # BOSSDEUX_MAX_DEPTH and BOSSDEUX_GREETING defined

[venv]
extra_deps = ["pybind11==3.0.4", "capstone==5.0.7"]

[coverage]
bridge_dirs = ["sources/bdx86emu"]  # pybind bridge build dirs; the first
                                    # one present is exported as
                                    # <PREFIX>_BRIDGE_DIR during pytest
exclude = ["sources/image/contrib/"] # extra gcovr --exclude regexes
                                     # (vendored code)

[analyze]
exclude = ["image/contrib"]   # path substrings clang-tidy skips

[conan]
# the project's dep policy, appended to every generated profile:
# `options`/`options_linux|windows|macos|emscripten` land in [options]
# (per-OS wins),
# `conf` lines in [conf] — e.g. backend choices, per-package workarounds
options_linux = ["sdl/*:x11=False", "sdl/*:wayland=False"]
conf = ["foonathan-lexy/*:tools.build:cxxflags+=['-Wno-deprecated-declarations']"]

[test]
python = ["tools"]         # directories that are NOT modules and hold a
                           # python suite: one `<dir>-pytest` ctest entry
                           # each, the same one a module's *.test.py gets.
                           # Unset: only modules carry python suites.

[bench]
suite = "inspector.bench"  # python bench suite run as `python -m <suite>`
                           # (tools/ on PYTHONPATH). Unset: `buildutil
                           # bench` builds BUILD_BENCHMARKING and runs
                           # every ${module}-benches executable instead.

[run]
default = "bdxmcp"         # `buildutil run` builds + launches this
default_windows = "bdxgui" # installed binary; per-OS overrides
default_macos = "bdxgui"   # (default_linux too). Unset: --target required.

[buildutil]
version = "0.12.0"  # pins what `buildutil update` installs inside this
                    # project (reproducible checkouts; downgrade works —
                    # pip's == replaces newer too). "latest"/absent =
                    # newest. Pins below 0.12.0 are REFUSED: older
                    # copies can't see the pin, so their next update
                    # would jump to latest and ping-pong forever.
```

## Two install modes

- **Vendored** (`buildutil install`): the whole package is copied to
  `<repo>/.buildutil/` and **committed**, with a `./buildutil` launcher
  at the repo root — the tool travels with the project, and
  `./buildutil build --whatever` is the whole interface. A fresh clone
  on a machine with nothing installed runs `./buildutil build` once;
  the bootstrap brings up the project venv (`_pyvenv/`) and every
  invocation runs through it (the launcher prefers the venv, falls
  back to `python3` only to create it). `buildutil update` inside a
  vendored project upgrades the copy in place (same private-index
  rule), ready to commit. Run `install` in an untouched directory and
  it also runs `init` — one command from empty dir to buildable
  project — unless `--no-init`. At a terminal, `init` prompts for the
  project name and prefixes; in a pipeline it stays flag-driven.
  `init` writes the same launcher on its own, so `./buildutil` works
  in non-vendored projects too (venv console script, then PATH).

- **Standalone** (laptops, foreign CI shell runners): the package
  bootstraps `_pyvenv/` in the repo with the pinned toolchain
  (typer/conan/gcovr/pytest + the project's `extra_deps`) and re-execs
  itself under it. Needs python >= 3.11 and network on first run,
  nothing else.
- **In-container** (container images that preinstall the toolchain):
  `BUILDUTIL_SYSTEM=1` is set by the image — the package and pinned
  deps live in the image's venv, no
  per-checkout venv is ever built. A project's own declarations still
  hold: `[venv] extra_deps` (and the clang bindings a declared `reflect`
  extension needs) are pip-installed into that interpreter before conan
  and cmake run, at the declared pins, skipped once they are satisfied,
  from whatever index pip is configured with. No image is expected to
  carry a dep only the projects that declare it ever need. If the
  interpreter's `site-packages` is read-only, buildutil says so and
  stops rather than failing later in cmake — install them yourself or
  unset `BUILDUTIL_SYSTEM` and let `_pyvenv/` carry them.

## .env and the conan remote

A repo-root **`.env`** (KEY=VALUE lines; `#` comments; one pair of
surrounding quotes stripped; gitignored by init) is loaded on every run.
Real environment variables always win over the file — CI-injected values
and shell exports are never overridden.

The conan remote reads one env seam, wherever the values come from
(`.env`, shell, CI): `CONAN_REMOTE_URL` is the switch — set it and
buildutil **replaces conancenter with that URL and logs in
automatically**. `CONAN_REMOTE_NAME` is optional (default `conancenter`:
the mirror is registered literally under the public remote's name; a
custom name also removes the public remote so resolution never races
the mirror). `CONAN_REMOTE_USER`/`CONAN_REMOTE_PASS` are optional —
absent means an anonymous mirror, no login. GitLab CI's
`CI_ARTIFACTORY_{NAME,HREF,USER,PASS}` are the fallback spellings.

Registration runs on EVERY command but is stamp-guarded: a fingerprint
of the seam is stamped into CONAN_HOME after a successful
registration+login, and while it matches (and the remote is still in
remotes.json) nothing happens — so editing `.env` takes effect on the
next command, at zero network cost when nothing changed. If the remote
is unreachable, buildutil degrades to the public conancenter (source
builds) WITHOUT stamping, so the next run retries the mirror.
`buildutil setup` forces a fresh registration+login.

```
# .env
CONAN_REMOTE_URL=https://repo.example.com/artifactory/api/conan/conan-local
CONAN_REMOTE_USER=alice
CONAN_REMOTE_PASS=…token…
```

## Driver discipline — direct tool calls are refused

A buildutil project is DRIVEN: `cmake`, `ninja`, `ctest` and `conan`
invoked by hand fail with an explanation naming the buildutil command
to use instead. The driver exports `BUILDUTIL=<version>` to every child
it runs; the rendered machinery checks it at configure (also satisfied
by the driver's `-DBUILDUTIL_PY`), on every build (an always-built
guard target), and before any test (a ctest setup fixture); the
scaffolded conanfile checks it in `validate()`. Driving a tool by hand
skips the venv, the conan profile, the rendered machinery and the
install mirror — half-working results that cost more than the refusal.
For forensic debugging, `BUILDUTIL=1 <tool> ...` is the explicit
opt-out.

## Shipping a project as a conan package

`buildutil init` asks (multiple choice at a tty; `--package=library|
application` / `--no-package` in scripts) whether the project ships as
a conan package; the answer is committed as `[package]` in
buildutil.toml (kind + name — the wizard's "don't package" is recorded
too, so it never re-asks). A packaged project gets a `test_package/`
scaffold that consumes the cached package like a real consumer, a
library-kind project ships every module's static archive at its
mirrored spot, and re-running init on an old project is the upgrade
path (a pre-packaging conanfile is regenerated, the original kept as
conanfile.py.bak).

**The version never lives in the package source.** `buildutil publish`
derives it: the last git tag that is a valid semver + a build number
bumped on every successful publish (`--no-version-autoincrement` holds
it, `--version` overrides, no tag + no tty = refusal, and a dry run
consumes nothing). publish = build → export-pkg of the built tree →
test_package against the cache → upload recipe + binaries to the
project remote (unconfigured remote = error; `--no-upload` = local dry
run). An unqualified `buildutil test` also runs the package test.

**`publish --bake-buildutil` makes `--build=missing` work.** By
default the published recipe refuses to build from source in the conan
cache — binaries come from the project remote. The switch ships the
build driver inside the package: a vendored project's committed
`.buildutil/` goes as is; any other project gets a copy **staged** at
`.buildutil/` for exactly the moment conan snapshots
`exports_sources`, then discarded — publishing a self-building package
never forces `buildutil install` on the working tree. A consumer whose
profile finds no binary then falls into a real source build: the
recipe invokes the baked driver's `cache-build` (stdlib-only — no
venv, no nested conan, no network), which renders the cmake machinery
and builds against the toolchain conan generated, with the consumer's
OWN resolution of every `Require()`. Projects already vendored get the
lane even without the switch — their `.buildutil/` ships anyway.
Configure-time codegen runs under the consumer's conan python, so a
package whose codegen needs `[venv] extra_deps` may still want
prebuilt binaries for exotic consumers.

## The agent skill

`buildutil setup-skill` installs a skill that teaches a coding agent
the CLI and the directory conventions (and the discipline above). The
target agent comes from `--agent-type claude|codex|qwen|gemini|
copilot|cursor`, or is autodetected from the dot directories at the
project root (`.claude/`, `.codex/`, ...) — each detected agent gets a
copy under its own config dir (`.claude/skills/buildutil/` et al).
`init` and `setup` install it automatically for detected agents unless
`--no-agents`; `--zip PATH` writes a portable zip of the same skill.

## The cmake machinery

Nothing is committed into a project (user req: "without any /helpers
directory in the project itself"). The machinery ships in the package
as templates and is RENDERED — with your `buildutil.toml` prefixes —
into the gitignored runtime dir `_bdudata/cmake/` on every build; the
driver hands that dir to cmake as `CMAKE_MODULE_PATH`, so your root
CMakeLists needs exactly one line: `include(buildutil)`.

That one line carries the UNIVERSAL POLICY too, so a root never restates
it: the compile database, `include(CTest)`/`include(GoogleTest)`, the
`BUILD_BENCHMARKING` option, and the `BUILDUTIL_COVERAGE` /
`BUILDUTIL_GC_SECTIONS` switches with their flag blocks. A whole project
root is five lines:

```cmake
cmake_minimum_required(VERSION 3.25)
project(oxbox CXX)

# the machinery buildutil renders at build time; universal policy
# (compile DB, CTest/GoogleTest, coverage/gc-sections/benchmark
# options) lives there, not here
include(buildutil)

add_subdirectory(sources)
```

**`_bdudata/cmake/` is derived data — never edit it.** Every rendered
file leads with a DO-NOT-EDIT banner and a version stamp naming the
buildutil that wrote it; every build re-renders the whole directory from
the installed package, so an edit made there is discarded silently on the
next build, and an upgrade takes effect on its own with nothing to sync.
`buildutil --version` inside a project reports the deposit's own version
next to the package's:

```
buildutil 0.15.0 (/usr/local/lib/python3.14/site-packages/buildutil)
  cmake machinery: /project/_bdudata/cmake — STALE, rendered by 0.14.1 (the next build re-renders it)
```

**To extend the machinery for one project, drop a `*.cmake` into the
project's own `cmake/` directory.** Every file there is included
automatically at the end of `buildutil.cmake` — sorted, after all the
machinery is defined, so your helper can call `Init_submodule()`, wrap it,
or add functions of its own and every module's CMakeLists will see them.
Nothing to declare and nothing to register: the file's presence is the
whole configuration. These files are yours — committed, and never
rendered, rewritten or upgraded by buildutil. That is the seam:
`_bdudata/cmake/` is ours and updates itself, `cmake/` is yours and is
never touched.

Python halves
(`apply_patch`, `orom_finalize`, the configure-hook library) are never
deposited — the rendered cmake calls them as modules of the running
package (`BUILDUTIL_PY`/`BUILDUTIL_PYSUPPORT`, injected at configure).

**Per-OS sources are picked by name, not by `#ifdef`.** A file tag
selects a translation unit at scan time: `foo.win32.cpp` builds on
Windows, `foo.linux.cpp` on Linux, `foo.macos.cpp` on macOS,
`foo.posix.cpp` on both Linux and macOS; anything tagged for another
host is simply not handed to the compiler. The same tags work on a
**directory** — everything under `host.linux/` is Linux-only, `nt.win32/`
Windows-only — exactly as `*.test/` tags a whole subtree. Ported trees
that already keep per-host source directories rename the directory
instead of suffixing every file in it. Tags compose and all of them must
hold: a `*.win32` subtree inside a `host.linux/` one builds nowhere, and
`foo.win32.test.cpp` is a Windows-only test.

**The tag table, once.** Every one of these rules reads the same
vocabulary, rendered into the machinery from `buildutil/naming.py` so the
spellings cannot drift apart:

| tag | target systems | kind |
|---|---|---|
| `win32` | Windows (`windows` also accepted in `platforms`) | exact |
| `linux` | Linux | exact |
| `macos` | Darwin | exact |
| `emscripten` | Emscripten | exact |
| `posix` | Linux + Darwin + Emscripten (and other unnamed target systems) | family |
| `apple` | Darwin | family |
| `native` | Windows + Linux + Darwin: every target but the browser | family |

A family is **less specific** than an exact platform, which is what lets
tagged things be ordered against each other. Any target system the table
does not name reads as a generic unix.

**The extension carries a default, and a tag only narrows it.** A `.mm`
is Objective-C++ and therefore an Apple source *by being one* — tagging
it `.macos.mm` would repeat the extension, so `window.mm` needs no tag
and is simply not globbed elsewhere:

| extension | platforms | language enabled |
|---|---|---|
| `.cpp` `.cc` `.cxx` `.c` | all | (from `project()`) |
| `.mm` | Apple | `OBJCXX` |
| `.m` | Apple | `OBJC` |
| `.rc` `.manifest` | Windows | `RC` |
| `.s` `.S` | all | `ASM` |

Languages are enabled at root scope **by presence**, from that same
table. `.asm` is deliberately absent: the `watcom` extension's
`Init_firmware()` owns it, and claiming it in the module glob would
silently compile a firmware image into a host module.

An explicit tag intersects the extension's default. `foo.posix.mm` is
legal and means macOS; `foo.linux.mm` is a **configure error** naming
both, because the file would be for no platform at all and would simply
never build. Only the LAST dot-segment before the extension is read as a
tag, and only if the table holds it — `foo.test.cpp` and `foo.v2.cpp`
are untouched, and every spelling that worked before still works.

**Data directories take the tags too, and OVERLAY rather than replace.**
`ui.embed/` and `locale.install/` are the base set for every platform;
`ui.embed.posix/` overlays it on Linux and macOS, `ui.embed.linux/`
overlays *that* on Linux, and `locale.install.win32/` is Windows-only.
The merge is by **name** — the resource name for `*.embed/`, the shipped
path for `*.install/` — so a tagged directory *replaces* what it names
and *adds* what it does not:

```
sources/gui/ui.embed/app.js         base, every platform
sources/gui/ui.embed/logo.svg       base, every platform
sources/gui/ui.embed.posix/app.js   wins over the base on Linux and macOS
sources/gui/ui.embed.linux/app.js   wins over that on Linux
sources/gui/ui.embed.win32/app.js   Windows only; ignored elsewhere
```

Selection is by the **target** platform, cross builds included — never
the host. Specificity is the table above: base (0) < family (1) < exact
(2). Two *applicable* directories of the same specificity claiming one
name is a **configure error naming both** — nothing orders
`ui.embed.posix/` against `ui.embed.apple/` on macOS, and inventing a
rule would be worse than refusing. A directory tagged for another
platform is ignored in silence and is never even globbed, so editing a
Windows asset on Linux does not re-run cmake. The tag goes **after** the
suffix (`ui.embed.linux/`, not `ui.linux.embed/`): the suffix says what
kind of directory it is, the tag qualifies it, and the other spelling —
which would read as an untagged set named `ui.linux` and ship
everywhere — is refused.

**Headers are never filtered by tag.** Nobody chooses what
`#include "foo.h"` opens — the preprocessor does — so a tagged header
would resolve or not by a rule the include path knows nothing about. A
per-platform header is reached the ordinary way, from the per-platform
source that includes it.

**A whole MODULE can carry the tag too.** `sources/helper.macos/` is the
module `helper` and it exists on macOS and nowhere else: on any other
host the directory is never entered, so its CMakeLists never runs and
nothing it declares — target, tests, dependencies — exists at all. Like
every other tag it is **never part of a name**: the target, the binary
and the install mirror are all `helper`, and it composes with the kind
tags in either order (`helper.macos.exe/`, `helper.exe.macos/`). This is
what a macOS-only sub-process executable says instead of an `if(APPLE)`
wrapped around `Init_submodule()` — and renaming the entry point is not a
substitute: the entry glob matches exactly `main.cpp`, so `main.macos.cpp`
would produce a module with no executable on any platform.
For a module that builds on more than one family, use `[modules.<name>] platforms`.

**Objective-C and Objective-C++ are module sources like any other** —
see [Objective-C](#objective-c-and-objective-c) below.

**What a module builds is in its directory name.** `ls sources/` tells
you how the tree shakes out, without opening a CMakeLists:

```
sources/wd.exe/        an executable          (or: any module with main.cpp)
sources/parser.lib/    a static library       (.a is a synonym; the default)
sources/dip.so/        a shared library       (.dll / .dylib synonyms)
sources/dipapi.obj/    an object-only module  (see below)
```

The tag is **never part of a name** — `sources/wd.exe/` is the module `wd`
and ships a binary called `wd`, or Linux would install `wd.exe`. The name
is still implied by the directory. `.so`/`.dll`/`.dylib` are synonyms, not
platform selectors: the platform picks the real suffix, so a tag never
means two things at once. **More than one tag is a configure error**, as
is a tag that contradicts the files (`.lib` on a module with `main.cpp`) —
a listing that disagrees with the tree is worse than no listing.

An **`.obj` module** is for code resolved against at *runtime* rather than
at link time — a plugin calling back into the program that loaded it.
Everything linking it takes **every** object, including the ones nothing
references, which an ordinary static link would drop. That holds however
far away the module is: cmake propagates an object library's members to
direct consumers only, so buildutil puts them on the final executable's
link line itself.

A shared library can also be selected by presence: `main.so.cpp`,
`main.dll.cpp` or `main.dylib.cpp` — the file holding `DllMain` or its
equivalent, the way `main.cpp` selects an executable.

**`Init_submodule(PUBLISH_SYMBOLS)`** keeps an executable's symbols in the
dynamic symbol table (and lifts the hidden-visibility default) for hosts
that code loaded at runtime resolves into. Not tied to the build type: a
host whose plugins resolve in Debug and fail in Release is a binary that
works for whoever built it and breaks for whoever ships it.

**Exported headers ship by layout too.** Every header under a module
installs at the module's `sources/`-relative path:
`sources/oxbox/serialization/node.hpp` ships as
`include/oxbox/serialization/node.hpp`. That destination **is** the
in-tree spelling — `sources/` is already a universal include root, so
`#include <oxbox/serialization/node.hpp>` is the same string inside the
project and in a consumer of the installed package. A public header that
includes a sibling therefore cannot compile here and break on install.
Getting those two strings to agree is the whole rule; give the package
its own directory under `sources/` and the prefix costs one level and
repeats nothing.

Export is **opt-out**: a leading underscore on the file or on any
directory component keeps a header private (`_detail.hpp`,
`_internal/x.hpp`), as do `*.test/`, `*.bench/`, `*.install/` and
dot-directories. Header extensions are `.h .hpp .hxx .hh .inl .ipp`, and
a nested module ships its own headers under its own path. Kind tags are
*not* stripped here, unlike the binary mirror — the in-tree spelling
resolves through `sources/` with literal directory names, so stripping
would put the two spellings back out of step.

**The source tree is the install tree.** A module's artifact installs at
its source-relative path: `sources/plusplus/wpp386/` ships its binary as
`<prefix>/plusplus/wpp386`, a shared module ships `libdipdwarf.so` in its
mirrored parent — the leaf names the file, kind tags stripped from every
component. There is **no destination mapping anywhere**: a project that
wants a different shipped layout moves its *source* directories until the
mirror is that layout. PATH is an environment concern, not a build-system
one. (The *build* tree deliberately does not mirror — `<build>/bin` stays
the flat working set behind the test/run PATH contracts.)

**A constant belongs in the code, not in the build.** There is no call
for compile definitions and there will not be one: `#define FOO 7` in a
header the module already has says the same thing, in the language the
code is written in, where a reader finds it and a debugger shows it.
A token that must differ by platform is `#if defined(_WIN32)` in that
same header — the preprocessor is not a build system's business.
(`Module_defines` existed until 0.71.8 and is gone; nothing in the fleet
used it.)

**An OPTION is not a constant.** That paragraph rules out a compile
definition for a *constant*, and an option is not one: a constant is a
value that never changes under any circumstances, while an option is
chosen per build — contracts on in every build type and off for one
measurement, a depth limit, a greeting — and choosing it at compile time
is the only way some conditional compilation can happen at all. So a
project declares its options, with their defaults, in `buildutil.toml`:

```toml
[options]
contracts = true
max_depth = 32
greeting = "hello"
```

and any build command chooses another value for one build:

```console
buildutil build --release --option contracts=off
```

which prints `profile: x86_64-linux-gcc-release options: contracts=off`
— the profile line says what is not on its defaults, and nothing when
everything is. Every option is then injected as
`<cmake_option_prefix>_<NAME>` into **every target the project
defines** — a module's library, its executable, its tests, its benches —
and into none of its dependencies. A boolean arrives as `0` or `1`, an
integer as itself, a string as a quoted literal. One derivation for the
macro name, one rendering per value kind, one place that applies them:
no project's CMakeLists mentions any of it, and neither does a source
file.

```cpp
#if BOSSDEUX_CONTRACTS            // nothing is included for this, ever
  check(precondition);
#endif
using Policy = Contracts<BOSSDEUX_CONTRACTS == 1>;
```

**Read an option with `#if`, never `#ifdef`.** Every declared option is
defined, `0` included, so `#ifdef` is true whatever the value and would
take the on-branch in an off build; `#if` is what reads the value.
Naming the macro in ordinary code — a template argument, a constant
expression, as above — is better still: there a name nothing declared is
a compile error, where under `#if` it would quietly be `0`. How the
value reaches the compiler is buildutil's business and nobody else's —
see **the options header** in [The shadow tree is a source
tree](#the-shadow-tree-is-a-source-tree).

**Runtime data ships by layout.** A module's `*.install/` directories are **prefix-rooted
overlays**: the path inside the tree *is* the shipped path —
`data.install/x/y/z` installs at `<prefix>/x/y/z` and is staged at the build root the same way, so both trees agree about data.
The directory existing is the whole declaration; nothing is listed and nothing needs keeping in step with the
filesystem. It is data, not source: a `.cpp` in there is copied, never
compiled. Platform tags apply
(`locale.install.win32/` overlays `locale.install/`) — see **the tag
table** in [The cmake machinery](#the-cmake-machinery).

**Resources ship INSIDE the binary.** `*.embed/` is `*.install/`'s
opposite number and reads the same way — one file out, one file in. See
[Resources](#resources) below for the whole feature.

A module's **own directory is on its include path**, automatically — a
bare `Init_submodule()` is still the whole CMakeLists. So a header
beside the source is `#include "thing.hpp"`, and that keeps working from
a `sub/` directory, from generated code, and from a `*.test/` subtree.
The root goes on LAST, after `sources/` and after the codegen roots, so
every include that already resolved still resolves to exactly the same
file.

That root is **PRIVATE**: it belongs to the module's own translation
units and is never handed to consumers. A module publishes its headers
one way — qualified through `sources/`, as `<module>/foo.h`. The
alternative was tried and rejected: exporting the directory puts *every*
linked module's dir on *every* consumer's include path, so an
unqualified `#include "types.h"` that should have been a compile error
instead resolves to whichever module comes first in link order — no
diagnostic, and a module's internal headers leaking tree-wide. The
targets built from a module's TUs that don't compile into the library
(`-tests`, `-benches`, the executable itself, the pybind bridge)
each get the root
directly, so nothing depends on inheritance. One exception, forced by
cmake: a **header-only** module is an INTERFACE library, which has no
PRIVATE scope at all, so its root is necessarily visible to whoever
links it.

If your modules still carry a hand-written
`target_include_directories(<mod> PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})`,
delete it: the machinery already covers the module itself, and the
`PUBLIC` on that line re-creates exactly the leak described above.

**Porting a legacy tree** is the one case where that leak is the lesser
evil — an old codebase includes across modules unqualified from thousands
of call sites nobody is going to rewrite. Say so once, for the whole
project:

```toml
[cmake]
export_module_headers = true
```

Every module's own directory then becomes a usage requirement, so
unqualified cross-module includes resolve. The hazard above is unchanged
and you are accepting it knowingly — link order decides which `types.h`
wins. There is deliberately no per-module form: the fact is project-wide,
so it is declared project-wide, and new projects never encounter it.

- `buildutil init` — the before-anything command (no project, no venv
  needed): writes `buildutil.toml`, renders the machinery, and
  scaffolds a BUILDABLE hello-world tree — root CMakeLists, `sources/`
  with GoogleTest + google-benchmark wired through `Require(...)`, a
  conanfile.py that parses those same Require calls (versions live in
  ONE place), a .gitignore covering the `_*` convention, and an example
  `hello` module (the `hello` executable + a `hello-lib` library +
  `hello-tests` +
  `hello-benches`, all presence-driven). Existing files are NEVER
  overwritten, and the example module is skipped when `sources/`
  already exists; `--bare` gets you the toml + machinery alone.
  `buildutil build` / `test` / `bench` then work out of the box.
- `buildutil extend watcom` — declares the extension in
  `buildutil.toml` (`[cmake] extensions`); editing the toml by hand is
  equivalent. Bare `extend` lists what exists and what's declared.
  A declared extension is `include()`d automatically, and hooks the build
  by DEFINING either of two optional commands: `_buildutil_ext_pre_scan()`
  (once, before any module is added) and `_buildutil_ext_module(<lib>
  <target>)` (once per module, after its include roots are set).
- `buildutil extend reflect` — C++ reflection codegen. See below.
- `Require(<Name> VERSION "x.y" [SYSTEM])` in `sources/CMakeLists.txt`
  declares a dependency once — cmake `find_package`es it and the
  conanfile reads the same call to derive its requirement. The version
  is a FLOOR, and it resolves against the REMOTE: every install passes
  conan's `--update`, so a range always picks the newest satisfier the
  registry has — "publisher says fixed in x.y.z.w and my floor covers
  it" just works, and a stale cached copy can never masquerade as a
  broken fix (a stale one bit three consumers in one night before this
  was the default). The exception has the switch: the global
  `buildutil --no-conan-update <subcommand>` resolves against the
  local cache only, for offline work or a deliberately frozen cache.
  **`SYSTEM`**
  says the host provides it: `find_package` still runs, nothing is asked
  of conan. Use it when the distribution's copy is the one you want (a
  conan build with a wrong compiled-in path, say) — without it, dropping
  the conan name makes conan ask for a recipe that does not exist.
- `buildutil module list|enable|disable` — which `sources/` modules
  build, in two layers. The **project default is committed**, in
  `buildutil.toml`:

  ```toml
  [modules]
  dormant = ["as", "re2c"]     # not buildable yet — a project fact
  ```

  so a clean clone behaves the way the project intends, with the reason
  in the repo rather than in someone's working copy. The **local**
  `_bdudata/modules.ini` (gitignored, written by this command) overrides
  it in *both* directions: `[disabled]` adds, `[enabled]` takes away — so
  whoever is porting `as` builds it locally without touching a committed
  file. A dormant module drops out entirely: no library, no generated
  sources, no test target, and no `<PREFIX>_<NAME>_ENABLED` define, so
  the seams that guard it fold away.
- `buildutil update` — self-upgrade via pip, on the interpreter that
  owns the installed package, from a PRIVATE index only (--index-url,
  else $BUILDUTIL_INDEX, else a non-public index in pip's config;
  public PyPI is refused — the name is not ours upstream). Needs NO
  project — outside one it just updates to latest; inside one, the
  toml's `[buildutil] version` pin wins (see above; `--pin` overrides).
  Skips while a build is live. Where the deployment upgrades the package
  on its own schedule, `update` has nothing to do; it is for laptops and
  foreign runners.
- `buildutil --version` — version AND the package path, so a stale
  shadow install shows itself; inside a project, also the rendered cmake
  machinery's own version (see above).

## Use

```
buildutil init [--name N] [--cmake-prefix P] [--module-prefix M] [--bare]
buildutil update [--pin X.Y.Z] [--index-url URL] [--dry-run]
buildutil --version
buildutil extend [watcom]
buildutil build [--release] [--clang] [--option NAME=VALUE]... ...
buildutil test [--debug] [-f FILTER] [--no-build] [--option NAME=VALUE]...
buildutil deps [--debug|--release|--relwithdebinfo]   # warm conan deps
buildutil godbolt [-f REGEX]... [-m MODULE]... [-o DIR] [--release|--debug]
                  [--project-only|--all-functions] [--include-tests]
                  [--include-benches] [--option NAME=VALUE]...
buildutil coverage | bench | run | analyze | vscode [--option NAME=VALUE]...
buildutil module list|enable|disable
buildutil init | install | update | setup-skill      # pre-venv verbs
buildutil --fail-fast|--max-errors N|--jobs N|--write-log-to F [--clear-logs] <subcommand>
```

`--option NAME=VALUE` is repeatable and accepted by every build verb —
`build`, `test`, `run`, `bench`, `godbolt`, `coverage`, `analyze` and
`vscode` — anywhere the profile flags go. It chooses one of the project
options `buildutil.toml` declares for that one build; see **An OPTION is
not a constant** above.

Two edges worth knowing. `test --no-build --option contracts=off` prints
`options: contracts=off` and builds nothing, so it runs whatever the last
build left in the tree — the profile line says what was *asked for*, not
what the binaries were compiled with. And `run` forwards options it does
not know to the target; `--option` is one it knows now, so a target with
an `--option` of its own needs `buildutil run -- --option ...` (or
`--args`).

Global flags go BEFORE the subcommand — except the watchdog switches
(`--no-watchdog`, `--watchdog-budget N`), which are legal ANYWHERE on
the line and accepted by every subcommand, init and update included
(everything after a literal `--` stays the run target's argv). Every
command runs under the watchdog's budget rules unless disabled
(`--watchdog-budget` for cold conan builds).

`buildutil godbolt` — a source↔assembly HTML report, named in tribute
to Matt Godbolt's Compiler Explorer and built the same way: every TU is
recompiled with `-S -g` under its EXACT flags from compile_commands.json
(so the asm IS the chosen configuration's — pick it with
`--release`/`--debug`/both), instructions map back to source lines via
the `.loc` directives, directive noise and unreferenced labels are
filtered, and every C++ symbol is demangled. Each page shows the source
and its assembly side by side with matching lines sharing a color —
hover highlights the counterpart, click scrolls the other pane there;
asm inlined from OTHER files is dimmed with a `path:line` tooltip, and
every unmapped line says WHY in its tooltip (no line info / no code
emitted). Every call, jump and symbol reference is a LINK — to the
function's assembly on its own page (cross-TU) or to the local label —
so the code can be followed naturally; the index's function inventory
links the same way.

The project's code LEADS. A function counts as project code when its
line info maps into the repo; dependency/std template bodies the
project never (transitively) references are hidden outright — they are
instantiation noise. Referenced dependency code is kept so links never
dead-end, but always second: folded behind a disclosure on the index,
under a "dependency code" group in the jump menu. `--project-only`
drops dependency code completely (links to it render as plain text —
it is not yours to influence); `--all-functions` shows everything.
The hidden count is printed, never silent. Test and bench TUs
(`*.test.cpp` / `*.test/`, `*.bench.cpp` / `*.bench/`) are excluded
outright — scaffolding assembly is not what the report is for —
with `--include-tests` / `--include-benches` to bring them back;
skipped counts are printed.
`-f REGEX` (repeatable, matched against demangled names) scopes the
report to chosen functions, `-m MODULE` to chosen modules, `-o DIR`
picks the output directory (created if missing; default
`_build/godbolt-report/<profile>/index.html`). gcc/clang only.

`buildutil vscode` (also run on every configure) regenerates `.vscode/`
from the live module inventory: per-module build/run/test tasks in
debug/relwithdebinfo/release (+ bench, release only) for exactly what
each module has; godbolt tasks per module AND whole-tree, in all three
flavors (the asm IS the flavor's), with a remembered function-regex
prompt; a launch config per executable target (app/tests/
benches) in the debug-info flavors, pointing at build-tree binaries
with matching pre-launch build tasks; deps tasks per configuration.
Run/test/bench prompts ask for arguments / a filter (empty allowed) —
vscode can't persist prompt values, so buildutil remembers them
(`_bdudata/vscode-inputs.json`): the last value becomes the prompt's
next default and the launch configs' argv. c_cpp_properties.json is
UPSERTED — every build refreshes the just-built profile's entry and
moves it to the top (vscode's default), never dropping other entries.

## Compiler lanes and cross builds

`buildutil build --compiler gcc|clang|apple-clang|msvc` selects a
native compiler; the default prefers the host compiler. Cross lanes
write separate host and build profiles: dependencies target the requested
platform, while build tools use the shared `cross-build-linux` profile
with native gcc/g++ (or the minimal compiler-less fallback when gcc is absent).

The **wine-msvc** lane (`--compiler wine-msvc`) cross-builds
Windows/x86_64 with the genuine MSVC toolchain under Wine. It is claimed
automatically only when the `cl` on PATH really is the msvc-wine wrapper
— `msvcenv.sh` beside it, or a wine exec inside it — so an unrelated `cl`
(OpenCL, Common Lisp) cannot silently turn a native build into a Windows
cross-build. `BUILDUTIL_WINE_MSVC=1` claims the lane anyway, `0`
suppresses it; the driver prints the lane when it takes it.

The **osxcross** lane (`--compiler osxcross`, Linux with `oa64-clang++`
on PATH) builds Macos/armv8 with apple-clang settings. Its SDK path comes
from `osxcross-conf`; compiler wrappers, Darwin ar/ranlib, and the Mach-O
linker keep host tools out of target builds. macOS binaries cannot
run on Linux. Existing osxcross images also activate cross settings when
the wrapper is present.

The **emscripten** lane is explicitly selected:

```console
buildutil build --compiler emscripten --release --no-tests
```

On a Linux box, install once with
`git clone https://github.com/emscripten-core/emsdk /workspace/_tools/emsdk`,
then run `./emsdk install latest` and `./emsdk activate latest` there; the
image will carry it later.
Set `EMSDK=/workspace/_tools/emsdk` or source its `emsdk_env.sh` to put
`em++` on PATH. Installation alone never selects a wasm build.

The host profile uses `os=Emscripten`, `arch=wasm`, clang, libc++, C++26
and the requested build type. The LLVM major comes from `em++ --version`
(or `em++ -v` when the release banner omits it), capped at the driver's
Conan-known clang maximum, just like native compiler detection. The
profile hands Conan absolute `emcc`/`em++` paths and emsdk's
`Emscripten.cmake` as `tools.cmake.cmaketoolchain:user_toolchain`.
Native Linux clang's GCC flags never enter this profile.
The target selects `.emscripten` files and directories and is a member of
`posix` (musl libc, a POSIX-shaped file system), never of `apple` or
`native`: a `.posix` source builds, a `.native` one does not.

The SDK defaults executables to `.js`; buildutil sets application targets
to `.html` and installs the accompanying `.js` and `.wasm` files at the
normal mirrored location. For `buildutil init --name demo`, these are
`_install/demo/hello.html`, `hello.js`, and `hello.wasm`. Serve the directory
with an HTTP server to open the application in a browser; native
`buildutil run` is not a browser launcher.

Target-only dependency policy belongs in the project:

```toml
[conan]
options_emscripten = ["sdl/*:opengl=False"]
```

These options follow the common `options` and never affect Linux,
Windows or macOS profiles. The SDK smoke test skips when emsdk is absent;
with it present, it builds init's hello world and checks all three files.

## Objective-C and Objective-C++

A macOS GUI cannot avoid Objective-C++: an `NSApplication` subclass is
not expressible in C++, and anything embedding a browser needs one. So
`.mm` and `.m` are ordinary module sources.

- **Globbed by `Init_submodule()`** alongside `*.cpp`, and classified by
  the same tags: `platform.macos.mm` builds on macOS only,
  `foo.test.mm` is a test TU, a `*.bench/` subtree works the same.
- **`OBJCXX` / `OBJC` are enabled at root scope, by presence** — a tree
  with no `.mm` never pays for the compiler probe, and a tree with one
  never says a word about it.
- **ARC is the default.** Every Objective-C/C++ TU compiles with
  `-fobjc-arc`. A module holding manual-retain code opts out:

  ```toml
  [modules.legacygui]
  objc_arc = false
  ```
- **Apple frameworks are declared per module**, not linked by hand:

  ```toml
  [modules.gui]
  frameworks = ["Cocoa", "Foundation"]
  ```

  Inert off macOS, so a cross-platform module declares it once.
- **Module platforms** use the same exact and family names as the tag table:

  ```toml
  [modules.http]
  platforms = ["native"]
  ```

  The selected target decides this on every build, including cross builds.
  On other targets the module is dormant: no library, tests, or `_ENABLED`
  define. An empty list makes it dormant everywhere; omitting the key allows
  every target. Unknown names are configuration errors. `[modules] dormant`
  wins over `platforms`, making a listed module dormant everywhere. Local
  `_bdudata/modules.ini` overrides both defaults in either direction.
- **Headers are headers**: a `.h` next to a `.m` is found the ordinary
  way, and ships (or stays private under a leading underscore) by the
  same layout rule as every other header.

**"Apple" means the TARGET, never the host.** cmake sets `APPLE` from
`CMAKE_SYSTEM_NAME` after `project()`, so an osxcross cross-build from
Linux is Apple here and picks up the `.mm`; a native Linux build never
does, and the files are ignored without a warning.

Two traps this closes, both silent:

1. `.mm` is *also* in `CMAKE_CXX_SOURCE_FILE_EXTENSIONS`. A `.mm`
   compiled without `OBJCXX` enabled is built as **C++** — which happens
   to work under clang and stops working under anything else.
2. cmake does **not** derive `CMAKE_OBJCXX_COMPILER` from
   `CMAKE_CXX_COMPILER`. A language enabled after `project()` gets its
   own compiler search, and on a cross build that search finds the
   **host** `c++`: the `.mm` compiles, links against the wrong runtime,
   and the build has quietly stopped being a cross build. buildutil
   defaults both Objective-C compilers to the C/C++ ones it was given
   before enabling the languages, and the osxcross lane's conan profile
   names `objcpp` outright (absolute — a language enabled that late is
   refused a bare name).

Two tools deliberately do **not** see Objective-C: `buildutil analyze`
globs `*.cpp` only, so clang-tidy is never handed a TU it would need an
Apple SDK to parse; `buildutil coverage` treats `*.test.mm` / `*.test.m`
as scaffolding exactly as it treats `*.test.cpp`. ccache covers all four
languages.

Verified two ways: an osxcross Darwin cross-configure and build from
Linux (`.mm` → OBJCXX, `.m` → OBJC, both with `-fobjc-arc`, the C++-only
flags on the `.mm` and not on the `.m`, a Mach-O arm64 binary out), and
a real build on a Mac.

## Resources

Files that must be **in** the executable — a UI served over a scheme
handler, a font, a schema, a default config — with no directory to ship
beside it and no path to resolve at run time. A project declares which
files; buildutil decides how they get there and hands back one accessor.

Declare a directory in `buildutil.toml`:

```toml
[resources]
dir = "resources"                 # repo-root-relative
files = ["*.html", "*.css", "*.js"]   # optional; default: every file, recursively
module = "xoctet"             # optional; default: the [project] name
namespace = "xoctet"          # optional; default: the [project] name
prefix = "ui/"                    # optional; prepended to every resource name
[resources.mime]                  # optional; extensions the built-in table
".xyz" = "application/x-xyz"      # does not know
```

`[[resources]]` (double brackets, repeated) declares several independent
sets. Or declare nothing at all: **a `<name>.embed/` directory inside a
module is the declaration**, exactly as `*.install/` is for the data that
ships beside the binary. Either way the project's CMakeLists stays a bare
`Init_submodule()` — there is no cmake call for this and no `#embed` to
write.

A resource's NAME is its path under the declared directory, `/`-separated
on every platform (`sub/logo.svg`), with `prefix` in front if there is
one. Two declarations producing the same name is a configure error.

A resource does not have to be a file somebody typed: the generated
shadow tree is read the same way, so a module's `configure.py` can
produce one. See [The shadow tree is a source
tree](#the-shadow-tree-is-a-source-tree) below.

`*.embed/` directories take the **platform tags**: `ui.embed.linux/`
overlays `ui.embed.posix/` overlays `ui.embed/`, by resource name, for
the *target* platform. A `ui.embed.win32/index.html` replaces the base
one on Windows and contributes nothing anywhere else. Full rules, with the
`*.install/` half, under **the tag table** in [The cmake machinery](#the-cmake-machinery).

The generated header is `<module>/resources.hpp`, where `<module>` is the
module NAME (the path under `sources/` with `-` joining the components,
as everywhere else) — the same `generated/<module>/` root
`Add_generated_source` and `configure.py` emit into, so `#include
"resources.hpp"` works unqualified from anything that links the module.
One include, one namespace, whichever back end ran:

```cpp
#include "xoctet/resources.hpp"

namespace xoctet::resources {

struct Resource {
  std::string_view name;   // the declared name
  std::string_view mime;   // guessed from the extension
  const unsigned char* data;   // null exactly when the file is empty
  std::size_t size;
  std::span<const std::byte> bytes() const noexcept;
};

std::span<const Resource> all() noexcept;          // sorted by name
const Resource* find(std::string_view) noexcept;   // nullptr if absent
std::span<const std::byte> get(std::string_view) noexcept;  // empty if absent

}
```

`find()` is the accessor that tells **absent** from **empty**: `get()`
returns an empty span for both. Nothing here touches the filesystem, so
deleting the resource directory after a build changes nothing about the
binary. The declaring library carries `cxx_std_20` as a PUBLIC compile
feature (the accessor is `std::span`/`std::string_view`), so including
the header never asks a project to say anything about standards.

**Two back ends, one header.** Which one runs is a compile probe, never a
version table:

| compiler | back end |
|---|---|
| gcc ≥ 15, clang ≥ 19, Apple clang 21 | `#embed` — the compiler reads the bytes; nothing large passes through cmake or the C++ parser as text |
| everything else, MSVC included (no `#embed` as of VS 2026) | a generated `static const unsigned char[]` — an **array**, never a string literal: MSVC caps a string literal at 16 KB and puts no cap on an initializer |

Both produce identical bytes, and the suite asserts it by running the
same tree twice rather than trusting it.
`BUILDUTIL_EMBED_FALLBACK=1 buildutil build` forces the array path on a
compiler that has `#embed` — for seeing what MSVC will do, and as a
bisect handle when a resource looks wrong. An env var, not a toml key:
it is a diagnostic, not something a project decides once. A very large file (tens of MB) costs real compile time on
the array path; the `#embed` path does not care.

**ccache.** buildutil puts ccache in front of every compile, and ccache's
direct mode hashes a source and its `#include`s — *not* what `#embed`
pulls in. Left alone, editing a resource produces a byte-identical binary
(found in xoctet, verified both ways). The generator therefore writes
a SHA-256 of every embedded file into the translation unit as a
`constexpr` — something the preprocessor keeps, unlike a comment — so the
bytes changing changes the compile. A test edits a resource under ccache
and asserts the running binary reports the new bytes.

Adding or removing a file re-runs the generator on its own
(`CONFIGURE_DEPENDS`), and editing one rebuilds the translation unit that
carries it.

### The shadow tree is a source tree

`${build}/generated/<module>/` is where `configure.py`,
`Add_generated_source` and the resource generator already emit, and it is
already an include root and an `#embed` search path. It is also a **module
directory**: the data conventions read it exactly as they read
`sources/<module>/`.

```
generated/<m>/ui.embed/            a resource set, embedded like any other
generated/<m>/ui.embed.linux/      platform-tagged, same overlay levels
generated/<m>/locale.install/      runtime data, shipped like any other
```

Nothing declares any of it — a generator writing a file into
`generated/<m>/ui.embed/` has thereby added a resource. Hand-written and
generated files coexist in one set, and a **name both claim is a configure
error naming both directories**: a build where you cannot tell which of
the two you are looking at is worse than one that will not configure.

A module's `configure.py` runs at the top of `Init_submodule()`, long
before these globs, which is what makes the tree usable: whatever the
hook wrote is simply there when the conventions read the directory.

**The options header lives there too.** A project that declares
`[options]` gets `_build/<profile>/generated/<project>/options.hpp`,
written by the machinery at configure time — one
`#define <cmake_option_prefix>_<NAME> <value>` per option, content-diffed
so a value that did not change never restamps the file and never churns a
rebuild. It is **derived data under `_build/`**: per profile, rewritten
whenever an option changes, never in the source tree, never tracked, and
never something a person edits.

Nothing includes it. The machinery force-includes it into every
translation unit of every target the project defines — `-include` on gcc
and clang, `/FI` on MSVC — so the macros are simply there, in the
preprocessed languages only and on no dependency. `buildutil vscode` puts
the same header on the generated configuration's force-include list, so
IntelliSense and the debugger read what the compiler read. Why it is a
header and not a `-D` per option: one file is what an IDE can be pointed
at, and a command line would grow with every declaration.

### Writing into it — a module's `configure.py`

buildutil owns no UI toolchain, no transpiler and no compressor, and it
is not going to: what a project transpiles, bundles or compresses is the
project's business. What buildutil owes is somewhere to put the output
and the means to put it there — the module's `configure.py`, which the
build already runs at configure time with `buildutil_configure`
importable. The placement half of that module:

```python
import buildutil_configure as cfg

sources = cfg.inputs('*.ts')              # globbed AND declared: editing
                                          # one re-runs this hook
tsc = cfg.tool('tsc', install='npm install -g typescript')
cfg.run([tsc, '--outDir', str(scratch), *map(str, sources)],
        what='the TypeScript type check')  # failure shows tsc's own output
cfg.emit('ui.embed/app.js', text)          # -> generated/<m>/ui.embed/app.js
cfg.emit_bytes('ui.embed/app.js.br', packed)
cfg.data_dir('ui.embed', tag=...)          # the tagged shadow directory
```

| helper | what it is for |
|---|---|
| `output_dir(shared=False)` | the module's generated root, per profile or build-invariant |
| `data_dir(name, tag=None)` | the shadow copy of a data directory, created — the one place that knows the tag goes *after* the suffix and that the vocabulary is closed |
| `emit(path, text)` / `emit_bytes(path, data)` | write under the root, content-diffed so unchanged output never churns a rebuild, and register it |
| `declare(path)` | register a file written some other way |
| `depends(*paths)` / `inputs(*globs)` | inputs whose change re-runs the hook — `inputs()` declares the *directories* too, so a file **added** re-runs it as well |
| `tool(name, install=...)` / `find_tool(name)` / `tool_dirs()` | an external program — **declared TOOL requirements first, `PATH` second** — or a refusal naming both places and how to get it |
| `run(argv, what=...)` | run it; on failure the tool's *own* output is what the build shows |
| `source_dir()` / `module_name()` / `target_system()` | where the hook is, what it configures, and the **target** platform (never the host) |

**A declared tool is reachable from the hook.** A
`Require(<pkg> VERSION "x.y.z" TOOL)` package's bindir lands on
`CMAKE_PROGRAM_PATH` — a cmake *variable*, which `find_program` consults
and no subprocess inherits, so a hook used to find whatever the machine
happened to have or nothing at all. It is handed over now, and `tool()`
searches it before `PATH`: a project that pinned a version means the
pinned one, and the same build runs on the Linux boxes, the macOS runner
and the Windows runner without anyone installing anything globally. The
`PATH` fallback stays, because most of what a hook runs — python, git, a
system compiler — is not a conan package and never will be.

Everything downstream then follows from layout alone: the resource
accessor, the ccache content digest, the `CONFIGURE_DEPENDS` re-glob and
the install tree all treat the emitted file exactly as they treat one a
person typed. A payload that is *stored* compressed says so in its
**name** — `app.js.br` — and the consumer's scheme handler reads that;
buildutil has no encoding field and no compression knob, because neither
would be a fact about the build.

**The older `*.rom.bin` route still works and is untouched**: a
`<stem>.rom.bin` in a module still becomes a generated
`resources/<stem>.rom.hpp` holding a `constexpr std::array<std::byte, N>
<STEM>_ROM` (bossdeux's VGA font). That one is a single blob under a
single symbol with no name lookup and no content type; `[resources]` is
the named-set feature. Neither knows about the other.

## A dependency's runtime payload

Some packages are not finished at link time. CEF resolves `icudtl.dat`,
its V8 snapshot and its `.pak` files **relative to the directory the
library was loaded from** — not relative to the executable, and not
through `LD_LIBRARY_PATH`, which actively breaks it. The only layout that
works is the flat one: library, data and executable in one directory,
`$ORIGIN` on the rpath. That is a fact about the *dependency*, and every
consumer of such a package was re-deriving it by hand.

```toml
[runtime]
from = ["cef"]                  # dependencies with a runtime payload
targets = ["xoctet"]        # optional; default: every executable
dirs = ["contrib/blobs"]        # optional; repo-relative, no package behind it
```

Opt-in per dependency, deliberately: most packages have no payload and
copying their `bin/` would be wrong.

**The contract**, so buildutil never has to know the word "CEF". A
package with a runtime payload publishes it from the cmake module its
`find_package` loads. For a dependency named `<dep>` (`<DEP>`
upper-cased), any of:

| what | meaning |
|---|---|
| GLOBAL property `<DEP>_RUNTIME_BINARY_DIR` | its contents go beside the executable |
| GLOBAL property `<DEP>_RUNTIME_RESOURCE_DIR` | likewise |
| GLOBAL property `<DEP>_RUNTIME_LIBRARY_DIR` | its **shared** libraries do (a static archive sitting next to them does not) |
| GLOBAL property `<DEP>_FRAMEWORK_DIR` | macOS: a `.framework` for `Contents/Frameworks` |
| function `<dep>_copy_runtime(<target>)` | optional: the package populates the BUILD tree itself |
| function `<dep>_copy_framework(<bundle target>)` | optional: the same, into a macOS bundle |

GLOBAL properties rather than variables because a cmake function body
sees its *caller's* scope: a plain variable set in the package's module
expands to `""` from inside one, and the copy then runs from the
filesystem root. A dependency in `[runtime]` that publishes none of the
above is a configure error naming the contract — a declaration that
silently does nothing is the failure every rule here exists to prevent.

buildutil calls the package's function where there is one (the package
knows its own layout, and sets the rpath while it is there) and copies
the property directories itself where there is not. **The install tree is
always buildutil's job**: those functions are `POST_BUILD` and only ever
touch the build tree, while `buildutil run` execs the *installed* binary
— which is exactly the gap consumers fell into, a binary that runs from
`_build` and dies with "Error loading V8 startup snapshot file" from
`_install`. `$ORIGIN` (`@loader_path` on macOS) goes on both rpaths, and
a module's `-tests`/`-benches` executables get the payload beside them
too (they do not live in `<build>/bin`, so the app's copy is no help).

## Python: an extension, and an interpreter inside the app

A `*.pybind.cpp` in a module is a **python extension**, by presence and
nothing else. The module builds as usual; the entry file builds as a
second artifact linking it, and installs to `lib/python`.

**The extension is named for the module the entry DEFINES** — the name
in its `PYBIND11_MODULE(...)`, which is the file's own stem — with
Python's SOABI suffix. `sources/tash/python/tash.pybind.cpp` ships
`tash.cpython-314-x86_64-linux-gnu.so`, importable as `tash`. The cmake
target name is a different thing, derived from the directory
(`tash-python-pybind`), and a file named after it is one no `import`
statement can reach. A module holds at most one entry: each is a module
name, and two of them in one directory have no answer to what the single
`.so` is called.

**pybind11 comes from the interpreter the driver runs under** — the
project venv (a pinned `[venv] extra_deps`), the image python under
`BUILDUTIL_SYSTEM`, a consumer's conan python in a cache build — and is
located **once for the whole tree**. Its cmake package joins
`CMAKE_PREFIX_PATH` at root scope, and that same interpreter is pinned
as `Python_EXECUTABLE` / `Python3_EXECUTABLE` so pybind11 binds against
it rather than against whatever `python3` comes first on `PATH`. A
module that **embeds** the interpreter instead of exporting an extension
declares it like any other host dependency and links it:

```cmake
# sources/CMakeLists.txt
Require(Python3 VERSION ">=3.14" SYSTEM COMPONENTS Interpreter Development.Embed)
Require(pybind11 VERSION ">=3.0" SYSTEM)

# the module
Link_dependencies(pybind11::embed Python3::Python)
```

**An installed binary finds the host libraries it links.** The
directories of the shared libraries a `Require(... SYSTEM)` found go on
the app's install rpath, so `buildutil run` and the binary in
`<build>/bin` both start with nothing in the environment — libpython
lives wherever the image put it, and that is not the loader's default
path. Only SYSTEM deps: a conan library travels as `[runtime]` payload
beside the binary, and its cache path baked into an rpath would let an
artifact that ships no payload still start on the machine that built it.

## macOS application bundles

On macOS an application is a directory, not a binary, and a framework
loaded at run time is found at `../Frameworks` relative to the executable
*inside* it. A project whose dependency ships one has no choice about the
shape — so the shape is not what it should be writing.

```toml
[bundle.macos]
module = "xoctet"           # optional; default: the [project] name
name = "xoctet"             # optional; CFBundleName, default: the module
identifier = "com.example.app"
version = "0.1.0"
plist = { LSUIElement = true, LSMinimumSystemVersion = "12.0" }

[bundle.macos.helpers]          # sub-process bundles in Contents/Frameworks
module = "xoctet-helper"    # ONE executable, run under several names
variants = ["", "Alerts", "GPU", "Plugin", "Renderer"]
```

That produces `<name>.app` with a generated `Info.plist`, installs it at
the module's mirror, and assembles one helper bundle per variant:
`<CFBundleName> Helper[ (<variant>)].app`, identifier
`<identifier>.helper[.<variant>]`, each with its own plist and a copy of
the one helper executable — because the framework picks the bundle by
name and the program inside is identical. `suffix` overrides the
`" Helper"` in the middle. Declare the helper module macOS-only by
tagging its directory (`sources/xoctet-helper.macos/`), and its
framework arrives through `[runtime] from` on both platforms.

The `Info.plist` is written by python (`plistlib`), not `configure_file`d
from a template the project would have to carry: `plist` is arbitrary
TOML with real types, and a text template cannot tell `true` the boolean
from `"true"` the string. Everything a bundle needs regardless
(`CFBundlePackageType`, `NSPrincipalClass`, the Dock-hiding on helpers)
is filled in; a `plist` key overrides it.

**One thing genuinely had to change in the machinery for this.**
`Init_submodule` emits `install(TARGETS <app> RUNTIME DESTINATION ...)`,
and cmake refuses at *generate* time to install a `MACOSX_BUNDLE` target
through a rule with no `BUNDLE DESTINATION` — so a project could not
simply set the property itself, and every consumer assembled the bundle
out of `POST_BUILD` copies instead, which then cannot use the package's
own `cef_copy_framework()` (that needs a real bundle target to hang
`$<TARGET_BUNDLE_CONTENT_DIR:>` off). The install rule now names both
destinations and the knot unties.

`buildutil run` knows: on macOS, when the artefact is `<binary>.app`, it
launches with `open -W -n` rather than exec'ing the file inside — which
is not the same thing, since the plist is what names the sub-process
bundles and `LSUIElement` only applies to a launched app.

## Reflection — the `reflect` extension

The generator parses C++ with libclang, so a project that declares this
extension needs **a clang on PATH**: buildutil pip-installs the bindings, but
that wheel ships the library with none of clang's builtin headers, and the
resource directory they live in can only come from a real clang (`apt install
clang`, `xcode-select --install`) or from `BUILDUTIL_RESOURCE_DIR`. Without one
buildutil says so and stops, rather than parsing every header without
`stddef.h` and reporting the wreckage as a missing project include.

Declare it (`buildutil extend reflect`) and a type opts in with one line:

```cpp
struct HelloWorld: Command
{
  friend constexpr auto reflect_scheme(HelloWorld*);

  bool        verbose { false };   /* say more about what is happening */
  std::string name    { "x" };     // a trailing run works too, and its
                                   // continuation lines are kept

  auto operator() (int  first,     /* the first one */
                   bool second     /* the second one */
                  ) const -> int;
};
```

buildutil emits `<generated>/<sources-relative>/hello-world.reflect.hpp` with
the schemes, plus a three-line **detour** at `<generated>/<sources-relative>/
hello-world.hpp` — the real header by absolute path, then those schemes — and
puts the generated root ahead of `sources/` on every include path. So the
`#include` you already wrote resolves to the detour, and any TU that has the
class has its schemes, however many levels of `#include` away it was reached.
**Your header is never touched**: nothing under `sources/` is generated, the
real header is still parsed in place, and an error or a goto-definition in
class code lands in the file you edit.

A **packaged** project ships the same arrangement, flattened into the one
include root a package has: `include/<sources-relative>/hello-world.hpp` is
the detour, the real header sits beside it as `hello-world.detoured.hpp`, and
`hello-world.reflect.hpp` is beside both. A consumer writes the include it
always wrote and gets the schemes; a consumer that does not use the reflect
extension itself still compiles, because the support surface
(`include/_buildutil/reflect.hpp`) ships too.

```cpp
constexpr auto S = reflect::scheme_of<HelloWorld>();
static_assert(reflect::scheme_size(S) == 2);
static_assert(decltype(reflect::scheme_item<0>(S))::NAME_STRING == "verbose");
static_assert(decltype(reflect::scheme_item<0>(S))::COMMENT ==
              "say more about what is happening");

constexpr auto C = reflect::call_scheme_of<HelloWorld>();       // parameters
static_assert(decltype(reflect::scheme_item<1>(C))::COMMENT == "the second one");
static_assert(reflect::scheme_size(C) ==                        // stale-check
              reflect::call_traits_of<HelloWorld>::ARITY);
```

**Enums opt in too**, but not with a friend — an enum has no member-declaration
list to attach one to, and needs none, since enumerators are public:

```cpp
enum class Colour : std::int16_t { RED = 1, /* the warm one */
                                   BLUE };
constexpr auto reflect_scheme(Colour*);      // beside it, not inside it

constexpr auto E = reflect::scheme_of<Colour>();
static_assert(decltype(reflect::scheme_item<0>(E))::NAME_STRING == "RED");
static_assert(decltype(reflect::scheme_item<0>(E))::COMMENT == "the warm one");
static_assert(decltype(reflect::scheme_item<0>(E))::VALUE == Colour::RED);
```

`enumerator` carries `NAME_STRING`, `VALUE` and `COMMENT`. The names arrive
verbatim — turning `OPTION_UNO` into `--option-uno` is a CLI decision, not a
reflection one.

**Bases come too.** A type that derives from something answers with a
`derived_scheme`, which carries a `base_list` alongside the same members —
`scheme_size` and `scheme_item` still see the members and nothing else, so
nothing written against `class_scheme` has to change:

```cpp
constexpr auto B = reflect::bases_of<HelloWorld>();   // base_list<Command>
static_assert(reflect::bases_size(B) == 1);
static_assert(std::is_same_v<reflect::base_at<0, decltype(B)>, Command>);
static_assert(reflect::has_reflected_bases<HelloWorld>);
```

A base arrives as a **type**, not a value (`base_item<I>` hands back a
`std::type_identity`), because bases are routinely abstract. Only **direct,
public, non-virtual** bases are captured; a type with none keeps emitting a
plain `class_scheme`. A listed base need not be reflected itself — the
`Command` tag above is not — and whether one is worth descending into is
`reflected<Base>`, the consumer's question rather than the generator's.

Each `member_scheme` carries `NAME_STRING`, `REFERENCE` (the pointer-to-member),
`COMMENT` and `ENCAPSULATED`; parameter *types* come from `call_traits_of`, so
the generator never names a type and the output is portable to MSVC and
apple-clang. `reflected<T>` reports honestly, and `scheme_of` on an unreflected
type is a **compile error** — a command whose codegen never ran must not
quietly become a command with no options.

Why a friend declaration rather than an attribute or a macro: it is real C++
(syntax-highlighted, pinned to the type), it needs no include because an
unqualified elaborated friend declaration *is* a declaration, it grants access
to private members, and a typo in the type name is a compile error rather than
a silent no-op. It is also a fixed literal string, so one grep selects both the
files worth parsing and the types within them. An attribute would warn on GCC
and Clang, and silencing that on Clang costs `-Wno-unknown-attributes`, which
also stops catching typo'd standard attributes.

One spelling reaches a reflected header — its **sources-relative** one,
`#include <demo/base/options.hpp>`. A quoted `#include "options.hpp"` from the
file next door searches its own directory first, finds the real header, and
skips the detour; a bare `#include <options.hpp>` is answered by the module's
own include root and does the same. Both are a **configure-time error** naming
the file, the line and the spelling to use, because that miss is exactly the
one that used to ship.

Members, methods and enumerators are emitted in declaration order.

**A scheme you write yourself wins.** Where a header both tags a type and
*defines* `reflect_scheme(T*)` for it — a body, not a `;` — the generator
emits nothing for that type and counts it as answered rather than owed. That
is the opt-out for the case where the hand-written scheme is the point: a test
that holds the framework to reading the TYPE, and would prove nothing against
a generated list. It is per type — a tagged neighbour in the same header is
generated as usual — and a header whose tagged types are all defined that way
is not claimed at all: no schemes, no detour.

Knobs, in `buildutil.toml`, project policy because a fresh clone must build
the same thing:

```toml
[reflect]
namespace  = "reflect"   # baked into user source; changing it breaks consumers
annotation = "macro"     # macro | attribute — what _Label/_Meta/_Help expand to
macros     = "auto"      # auto | none | <path to the project's own file>
```

The detour is the only delivery. `include = "source"` and `include =
"module"` — which pushed the reflect header into each `.cpp`, or into each
target — are **gone**, along with the `scan` key that only chose how they
mapped a `.cpp` to its headers; a project still carrying either is refused
with a message rather than quietly built the other way. What they could not
do is deliver a scheme reached *transitively*: `reflected<T>` is a
requires-expression, so the miss answered *no* rather than failing, the same
class instantiated a consumer's templates two ways in two translation units,
and the linker picked one — 165 tests green over a shipped binary that
rejected its own inherited options.

### Annotations — what a declaration says beyond its name

Two spellings, one meaning. The ground truth is a real C++ attribute in
buildutil's own namespace, which every compiler parses and ignores:

```cpp
struct [[buildutil::label("endpoint")]] Endpoint
{
  friend constexpr auto reflect_scheme(Endpoint*);
  bool tls [[buildutil::label("secure")]] { false };
  int  port [[buildutil::meta("wire", "hot")]] { 80 };
};
enum class Role { SYSTEM [[buildutil::label("system")]], USER };
```

The sugar is `_Label(secure)` / `_Meta(wire, hot)` / `_Help("Use TLS")`, defined in
`<_buildutil/reflect-macros.hpp>` (which `<_buildutil/reflect.hpp>` includes,
so a header that already says `#include <_buildutil/reflect.hpp>  // for
_Label` needs no change). A LABEL is an alternative EXTERNAL name — the name
the declaration carries when it leaves the program; META tags are free-form
text, captured verbatim and in order and **never interpreted here**: what a
tag means belongs to the library that reads it. They reach the schemes as
`LABEL` and `tags` (`tags::SIZE`, `tags::VALUES`), and a type's own
annotation as `reflect::type_scheme_of<T>()`.

`[[buildutil::help("text")]]`, or `_Help(text)` with a bare token or a
string, sets the scheme's `COMMENT` on members, methods, parameters and
enumerators. It overrides both leading and trailing comments, including when
its text is empty. Without it, the trailing comment wins, with a leading
comment as fallback. The help tip and the source comment are two different
things: an author who wants both writes `_Help` and leaves the comment free
for a developer note. Two help annotations on one declaration are an error.

A **leading** comment is read as help only when it is both immediately above
the declaration (a blank line detaches it) and **at most three lines** long.
A design note written above a member is adjacent to it and is not about it,
and without the second half of that rule a forty-line note rendered verbatim
under one option on the `--help` screen. A trailing comment has no such cap:
it starts on the declaration's own line, so what it belongs to was never in
doubt. For help text longer than three lines, write `_Help("...")`.
Types do not support `_Help`: `type_scheme` carries only `LABEL` and tags,
with no `COMMENT`.

The generator reads both spellings out of the RAW token stream, before macro
expansion — which is why `annotation` can move without anything else moving.
`annotation = "macro"` (the default) expands the macros to **nothing**, as
they always have; `annotation = "attribute"` expands them to the real thing
and buildutil then adds the suppression flags an unknown attribute needs to
that project's compiles (`-Wno-attributes=buildutil::` on gcc 12+,
`-Wno-unknown-attributes` on clang, `/wd5030` on MSVC). Opt in only once
every site uses a position an attribute is legal in: after the class-key for
a class or an enum, TRAILING for an enumerator (`SYSTEM _Label(system)`,
ahead of any `= value`), either side of a member, a method or a parameter.
The prefix form `_Label(system) SYSTEM` is a leading enumerator attribute
once expanded, and that is ill-formed.

An attribute in the `buildutil::` namespace that buildutil does not define is
a **hard generation error** naming the attribute and the valid set — which is
what recovers the typo safety clang's blunt suppression gives up. Attributes
in anyone else's namespace are ignored. `macros = "none"` writes no macro
header at all, and a path names the project's own file — the generator reads
the spellings back out of it (a macro is an annotation because its
replacement produces a `buildutil::` attribute), so custom spellings and what
the generator recognises cannot drift apart. With a file of your own,
`annotation` no longer says what the macros expand to (your file does) but
still says whether this project's compiles get the suppression flags.

Parsing is libclang: a system `clang` is preferred (it matches the compiler in
use, and supplies the resource headers the pip wheel does not ship), with the
bundled wheel as fallback. It lands in the project venv, and only when the
extension is declared.

libclang parses a translation unit **directly**, so it gets none of the
favours the clang *driver* does — and the driver is what supplies the
search paths. Two of them are put back by hand: `-resource-dir` from
`clang -print-resource-dir` (the builtin headers), and, on macOS,
`-isysroot` from `SDKROOT` or `xcrun --show-sdk-path`. The second is not
optional there: libc++ lives *inside* the SDK, and without it `<compare>`,
`<cstdint>` and the rest resolve to nothing and every tagged type collapses.
The parse also gets the consuming module's `COMPILE_DEFINITIONS`, so a
tagged type behind an `#ifdef` is there for the generator exactly when it is
there for the compiler.

**The parse runs on the HOST**, whatever the build targets, so a host with no
C++ toolchain loses every standard header. That is reported as what it is —
the generator names the standard headers it could not find and says the
project include path is not the cause — rather than as a missing project
include, which is what it used to look like.

The scan honours `BUILD_TESTING` and `BUILD_BENCHMARKING`: `buildutil build
--no-tests` does not parse, gate or generate for headers under `*.test/`.

On the MSVC lanes, reflect targets `x86_64-pc-windows-msvc` with
`-fms-compatibility -fms-extensions` and explicit system include paths for
the target standard library and Windows SDK. Native Windows uses `INCLUDE`
from `vcvars64`; msvc-wine derives the MSVC and SDK versions from
`msvcenv.sh` beside `cl` and resolves their include directories under the
wrapper’s installation root. Windows headers are parsed against MSVC’s
standard library, even when Linux libstdc++ is installed.

## Development

`python -m pytest buildutil/tests` — the suite is self-contained (no
C++ project needed; the seam tests spawn fresh interpreters in temp
roots). A release is an `X.Y.Z` tag on main, matching the version in
`pyproject.toml`.

## License

MIT — see [LICENSE](LICENSE).
