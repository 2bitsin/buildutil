# Build options

A constant belongs in the code. `#define FOO 7` in a header the module
already has says the same thing in the language the code is written in,
where a reader finds it and a debugger shows it, and a token that must
differ by platform is `#if defined(_WIN32)` in that same header — the
preprocessor is not a build system's business. buildutil therefore has no
call for compile definitions and will not grow one.

An **option** is not a constant. A constant never changes under any
circumstances; an option is chosen per build — contracts on in every
build type and off for one measurement, a depth limit, a greeting — and
choosing it at compile time is the only way some conditional compilation
can happen at all. So a project declares its options, with their
defaults, in `buildutil.toml`, and any build command chooses another
value for one build.

## Declaring

```toml
[options]
contracts = true
max_depth = 32
greeting = "hello"
```

The value is the **default**. Names are lower snake case starting with a
letter. Types are exactly three — bool, int and string — with no enum, no
float and no array. A string default may not contain `"`, `;`, `\`, `|`,
`$` or whitespace, because the value travels to the compiler through a
cmake string and used to die there as an unrenderable header.

## Choosing

```console
buildutil build --release --option contracts=off
```

`--option NAME=VALUE` is repeatable and is carried by eight verbs:
`build`, `test`, `run`, `bench`, `coverage`, `analyze`, `godbolt` and
`vscode`. Booleans take `on`/`true`/`yes`/`1` and `off`/`false`/`no`/`0`,
case-insensitively; integers go through a base-aware parse, so `0x20`
works. A value the declaration cannot accept exits 2.

The profile line is where a build says what it was asked for:

```
profile: x86_64-linux-gcc-release (default) options: contracts=off
```

listing only what differs from its default, and nothing when everything
is at its default. Two edges follow from that wording. `test --no-build
--option contracts=off` prints the choice and builds nothing, so it runs
whatever the last build left in the tree — the line says what was
*asked*, not what the binaries were compiled with. And `run` forwards
options it does not know to the target, so a target with an `--option` of
its own needs `buildutil run -- --option ...` or `--args`.

## How it reaches the compiler

Every option becomes a macro `<cmake_option_prefix>_<NAME>`, upper-cased:
prefix `BOSSDEUX` plus `contracts` is `BOSSDEUX_CONTRACTS`. A boolean
arrives as `0` or `1`, an integer as itself, a string as an escaped C++
literal.

The injection is a **header**, not a `-D` per option. The machinery
writes `<build>/generated/<project>/options.hpp` at configure time —
derived data under the build tree, per profile, content-diffed so an
unchanged value never restamps the file and never churns a rebuild — and
force-includes it into every translation unit of every target the project
defines: `-include` on gcc and clang, `/FI` on MSVC, gated to the
preprocessed languages so a `.rc` never sees it, and on no dependency.
One file is what an editor can be pointed at, where a command line would
grow with every declaration; `buildutil vscode` puts the same header on
the generated configuration's force-include list, so IntelliSense and the
debugger read what the compiler read.

Every option is passed on **every** configure, even at its default,
because one left out would be inherited from the cache of whatever the
last build of that tree chose.

## Reading one

```cpp
#if BOSSDEUX_CONTRACTS            // nothing is included for this, ever
  check(precondition);
#endif
using Policy = Contracts<BOSSDEUX_CONTRACTS == 1>;
```

**Read an option with `#if`, never `#ifdef`.** Every declared option is
defined, `0` included, so `#ifdef` is true whatever the value and would
take the on-branch in an off build. Naming the macro in ordinary code — a
template argument, a constant expression, as above — is better still:
there a name nothing declared is a compile error, where under `#if` it
would quietly be `0`. Nothing includes the header and no project's
CMakeLists mentions any of this.

## Build identity

An option is what the build was *asked for*. The **identity** is what the
build is, and no project declares it: every configure stamps
`_bdudata/buildinfo.json` and the machinery reads it back.

| value | where it comes from |
|---|---|
| `version` | `git describe --tags --always --dirty` — `v1.2.3` on a tagged commit, `v1.2.3-5-gabc1234` after it, the short hash where no tag is reachable, `-dirty` appended when tracked files are modified |
| `commit` | `git rev-parse --short HEAD` |
| `tag` | `git describe --tags --exact-match` — empty unless the tag sits on HEAD |
| `time` | the moment of the stamp, UTC, to the minute: `2026-09-20T12:34Z` |
| `dirty` | `git status --porcelain -uno` said something |
| `number` | `_bdudata/buildnum`, counted up on every build and local to the checkout |

Where git cannot answer — an unpacked source tarball, a conan cache
build — the values stand in as `unknown`, `unknown`, empty, empty, off
and `0`. Nothing fails and nothing is guessed.

In cmake they are ordinary variables in the root scope, named
`<cmake_option_prefix>_BUILD_<VALUE>`: `BOSSDEUX_BUILD_VERSION`,
`_BUILD_COMMIT`, `_BUILD_TAG`, `_BUILD_TIME`, `_BUILD_DIRTY` (`ON`/`OFF`)
and `_BUILD_NUMBER`. A project's own `cmake/` can `configure_file` an
about page, a plist or an installer script from them.

In C++ they are macros of the *same* names, written to
`<build>/generated/<project>/buildinfo.hpp` — version, commit, tag and
time as string literals, `dirty` as `0` or `1`, the number as an
integer. The same header carries the package version the export mark
reads ([layout.md](layout.md#exporting-symbols)):
`<module_define_prefix>_PACKAGE_VERSION` and its `_MAJOR`, `_MINOR` and
`_PATCH`:

```cpp
#include "demo/buildinfo.hpp"

std::print("demo {} built {}\n", DEMO_BUILD_VERSION, DEMO_BUILD_TIME);
```

**That include is written by hand**, and this is the one generated header
buildutil does not force-include. The time and the number move on every
build, so a force include would recompile every translation unit of the
project every time; instead the one file that prints a version includes
it, and only that file rebuilds. Nothing is on the compile line for it,
so a misspelled macro is a compile error rather than a quiet `0`.

## buildutil's own options

A separate system with a separate verb, deliberately.
`buildutil config` lists and sets build-time configuration of the
*driver*, persisted in `_bdudata/config.ini`, and today it holds one
entry: `module_linkage`, `static` or `shared`, where `shared` makes
untagged modules shared libraries and contributes a segment to the
profile directory name. It reads no `buildutil.toml` key. The
environment override is `BUILDUTIL_OPT_<NAME>`, one letter away from the
project system's `BUILDUTIL_OPTION_<NAME>` and a different thing.
