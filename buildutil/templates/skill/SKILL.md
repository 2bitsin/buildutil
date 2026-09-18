---
name: buildutil
description: Drive C++ builds in this project through the buildutil CLI — build, test, bench, run, coverage — and follow its directory-structure conventions. Use when building or testing the project, diagnosing build errors, or adding modules, tests, benches, apps, generated sources, or shipped files to a project that has buildutil.toml at its root.
---

# buildutil — this project's build driver

buildutil runs conan + cmake + ninja + ctest behind one CLI. A project
is marked by `buildutil.toml` at the repo root; everything under the
gitignored `_*` dirs (`_build/`, `_install/`, `_bdudata/`, `_pyvenv/`,
`_conanhome/`) is derived state.

## The mission

**Build logic is expressed by directory structure, not by build
scripts.** What a module is, what it builds, what it tests, what it
ships — all of it is declared by where files sit and what they are
named, so `ls -l` reads as the build's true configuration and the
structure is enforced by construction. Honor that order of preference:

1. **Structure first.** Before writing any build logic, ask whether a
   file name, a suffix tag, or a directory position already expresses
   it. It usually does.
2. **Unavoidable logic goes in `configure.py`** (python, per module or
   per group) — especially source generation. See reference.md.
3. **CMake logic is the last resort**, and when truly forced, keep it
   minimal and put it in the project's own `cmake/` dir — never in
   module CMakeLists, which should hold declarations only
   (`Init_submodule()`, `Require(...)`, `Link_dependencies(...)`).
4. **No macro defines in build scripts.** A constant belongs in a
   header. The machinery itself announces module presence
   (`<PREFIX>_<NAME>_ENABLED=1` for every live module), which covers
   most conditional-compilation needs without declaring anything.

**Never invoke cmake, ninja, ctest or conan directly.** The rendered
machinery refuses them with an explanation — configure, build, test and
dependency resolution all carry driver-owned contracts. `BUILDUTIL=1`
in the environment is the escape hatch for forensic debugging only.

## Invoking

Prefer `./buildutil` when the repo root has it (it routes through the
project venv); plain `buildutil` otherwise. Global flags go BEFORE the
subcommand:

    buildutil [--jobs N] [--watchdog-budget SECONDS] <command> ...

Keep the watchdog armed — it exists so an agent never hangs on a wedged
build. (`--no-watchdog` is for interactive human use.)

## Commands

    buildutil build [--release|--debug] [--target MODULE] [--no-tests]
    buildutil test  [--target MODULE] [-f FILTER]     # ctest/gtest filter
    buildutil bench [--target MODULE]                 # release, --include-bench builds
    buildutil run   --target NAME --args "..."        # build + install + exec
    buildutil coverage                                # gcov/gcovr report
    buildutil publish                                 # conan package: build+test+upload
    buildutil analyze                                 # clang-tidy et al
    buildutil vscode                                  # regen tasks/launch/intellisense
    buildutil module [list|enable NAME|disable NAME]  # local build toggles
    buildutil config [NAME=VALUE ...|--reset]         # build-time config options
    buildutil init | install | update | setup | extend | setup-skill

A plain `build` installs every module's app into the install mirror; a
`--target` build deliberately skips install. `run --target` accepts the
joined module name (`group-leaf`) or the bare app name (the leaf).
Builds land in `_build/<arch-os-compiler-buildtype>/`; apps in
`_build/<profile>/bin/`.

**Binary caching is on by default and desired.** buildutil registers
the project's conan remote (a private artifactory) automatically and
UPLOADS built dependency binaries after builds. That upload is what
lets the next clean build — yours, CI's, another machine's — fetch
binaries instead of compiling dependencies from source. The switch
that skips it is `--skip-dependency-upload-so-everyone-rebuilds-from-source`, and it means
exactly that: without a real reason it only costs everyone rebuild time.

## The tree is the configuration

Structure is declared by directory shape, not by build-script logic:

- A **module** is any dir under `sources/` whose `CMakeLists.txt` calls
  `Init_submodule()`. Dirs without a CMakeLists are **groups** and are
  scanned recursively. Dot-prefixed dirs never build.
- The cmake **target** is the sources-relative path joined with `-`
  (`sources/mstools/rc` → target `mstools-rc`); the **binary** is named
  by the leaf alone (`rc`). App names must be unique tree-wide.
- **Kind tags** on the module dir name pick what it builds:
  `*.obj` (object-only: consumers take all objects, symbols stay
  public), `*.lib`/`*.a` (static — the default), `*.exe` (application),
  `*.so`/`*.dll`/`*.dylib` (shared). One tag max; tags are stripped
  from every derived name. Untagged modules follow the
  `module_linkage` config option (`buildutil config
  module_linkage=shared` — own build tree per linkage; tagged modules
  keep their word; hidden visibility stays: only annotated symbols, or
  a PUBLISH_SYMBOLS module, export across a shared boundary).
- `main.cpp` in a module makes it an app; `main.dll.cpp`/`main.so.cpp`/
  `main.dylib.cpp` make it a shared library (platform preference picks
  among multiple).
- `*.test.cpp` files or a `*.test/` dir → gtest suite `<module>-tests`;
  `*.test.py` or python in `*.test/` → a pytest suite. Tests run with
  `<build>/bin` on PATH and cwd = the module dir, so suites exec the
  project's own tools by bare name and read corpora relatively.
  A directory that is NOT a module — a top-level `tools/` — declares
  its suite instead: `[test] python = ["tools"]` in buildutil.toml
  gives it the same `<dir>-pytest` entry, same interpreter, same
  fixture, cwd = that directory. There, pytest's own `test_*.py` and
  `*_test.py` collect, and `*.test.py` as well.
- `*.bench.cpp` / `*.bench/` → `<module>-benches` (google-benchmark).
- `*.pybind.cpp` → a python extension, installed under `lib/python` and
  named for the module its `PYBIND11_MODULE` defines (the file's stem),
  not for the cmake target. One per module; needs pybind11 in the venv,
  which is then on `CMAKE_PREFIX_PATH` tree-wide, so a module that
  EMBEDS the interpreter writes `Require(pybind11 SYSTEM)` and links
  `pybind11::embed` — see reference.md.
- `*.install/` dirs are **prefix-rooted data overlays**:
  `data.install/x/y/z` ships as `<prefix>/x/y/z` and is staged into the
  build tree so in-tree runs find it too. Several per module, named for
  what they hold (`locale.install/`, `tables.install/`).
- `*.patch` — a committed unified diff whose patched copy is generated
  at build time while the in-tree source stays pristine: a sibling
  `foo.txt.patch`, or a `<name>.patch/` dir mirroring paths beneath it.
- `configure.py` in a module or group dir runs at configure time —
  the codegen hook (see reference.md).
- **The install tree mirrors the source tree**: `sources/a/b/c/` ships
  its binary at `<prefix>/a/b/c`. Headers ship the same way:
  `sources/a/b/c/x.hpp` exports to `include/a/b/c/x.hpp`, which is the
  same string `#include <a/b/c/x.hpp>` already resolves to in-tree
  (`sources/` is a universal include root). Export is opt-out — a
  leading `_` on the file or any directory component keeps a header
  private, as do `*.test/`, `*.bench/`, `*.install/`. To change the
  shipped shape, move source directories.

## Declarations inside a module's CMakeLists

- `Require(pkg VERSION "x.y" [TEST|BENCH|TOOL|SYSTEM|PUBLIC|CONAN name|
  COMPONENTS ...|PLATFORM ...|OPTIONS key=value ...])` in
  `sources/CMakeLists.txt` — the single source of truth for external
  deps; the conanfile parses the same calls. `OPTIONS` are conan
  package options (`OPTIONS use_std_fmt=True`) — never hand-edit
  `default_options` in conanfile.py. Inside `COMPONENTS`, a token
  prefixed `+` or `-` is that same option in shorthand and not a
  find_package component: `Require(Boost VERSION "1.84" CONAN boost
  COMPONENTS system +asio -json)` asks conan for `with_asio=True` and
  `without_json=True` while `system` stays a component.
  `PUBLIC` marks a dep whose headers
  appear in this project's own exported headers — on a
  packaged library it propagates the dep's headers to consumers
  (conan's `transitive_headers`); without it consumers fail to compile.
- `Link_dependencies(<module>... [TEST ...] [BENCH ...])` — THE way a
  module consumes another module, whether that dependency is a real
  library or header-only: same call, and it implies build order (there
  is no separate dependency declaration). Module names resolve to the
  right library targets; external targets (`GTest::gtest`) pass
  through. TEST/BENCH lists link only the test/bench executables.
- `Init_submodule(PUBLISH_SYMBOLS)` — keep the app's symbols in the
  dynamic table (for hosts whose plugins resolve back into them).
- Every live module announces `<PREFIX>_<NAME>_ENABLED=1` to all TUs.

## Generated sources

configure.py output lands in `_build/<profile>/generated/<module>/`
(per-profile) and `_build/generated/<module>/` (profile-agnostic) —
**both are already on the module's include path**, so a generated
header is included exactly as its emit path says (`emit("x86/enums.hpp")`
→ `#include "x86/enums.hpp"`), and generated `.cpp` files compile into
the module automatically. Nothing to add to any CMakeLists.

## Reflection (only when `[cmake] extensions` lists `reflect`)

A type opts in with ONE line inside it — no macro, no attribute, no
base class, and nothing to include:

```cpp
struct Options
{
  friend constexpr auto reflect_scheme(Options*);
  bool verbose { false };   /* the comment becomes the description */
};
```

buildutil generates `<generated>/<sources-relative>/options.reflect.hpp`
and force-includes it into the TUs that include the header — **the
header is never edited**. Then:

```cpp
constexpr auto S = reflect::scheme_of<Options>();      // members
constexpr auto C = reflect::call_scheme_of<Options>(); // operator() params
```

`member_scheme` carries `NAME_STRING`, `REFERENCE` (pointer-to-member),
`COMMENT`, `ENCAPSULATED`; parameter TYPES come from
`reflect::call_traits_of<T>`, never from the generator. Private members
reflect (the tag befriends the entry point). `scheme_of` on an
unreflected type is a compile error, never an empty scheme.

Templates use the template form of the tag:
`template <class U> friend constexpr auto reflect_scheme(Boxed<U>*);`

An ENUM cannot befriend anything (no member list), so it opts in from
beside itself instead — `constexpr auto reflect_scheme(Colour*);` after
the enum. Its `enumerator`s carry `NAME_STRING`, `VALUE`, `COMMENT`.

A declaration can say more than its name: `_Label(secure)` gives it an
alternative EXTERNAL name, `_Meta(wire, hot)` free-form tags captured
verbatim (`LABEL`, `tags::SIZE`, `tags::VALUES` in the scheme;
`reflect::type_scheme_of<T>()` for the type's own). Both are sugar for
real attributes — `[[buildutil::label("secure")]]`,
`[[buildutil::meta(...)]]` — which may be written directly. Include
`<_buildutil/reflect.hpp>` to use the macros. Write them TRAILING
(`bool tls _Label(secure);`, `SYSTEM _Label(system),`): that is where an
attribute is legal, and `[reflect] annotation = "attribute"` then flips
the macros to the real thing with no other edit. A misspelt
`buildutil::` attribute fails the build by name.

## What tests/benches/pybind link against

Scaffolded by init and parsed into the conan graph:

    Require(GTest VERSION ">=1.17.0" TEST CONAN gtest
            COMPONENTS gtest gtest_main gmock gmock_main)
    Require(benchmark VERSION ">=1.9.0" BENCH CONAN benchmark)

`<module>-tests` links gtest/gmock (with mains) automatically;
`<module>-benches` links benchmark_main; a `*.pybind.cpp` needs
`pybind11` in `buildutil.toml`'s `[venv] extra_deps`.

## When something is off

- `buildutil --version` reports the package AND whether the project's
  rendered machinery (`_bdudata/cmake/`) is stale; the next build
  re-renders it. Never edit `_bdudata/` — it is derived and rewritten.
- Project-owned cmake extensions live in the project's `cmake/` dir
  (auto-included); the machinery itself belongs to buildutil.
- `buildutil update` upgrades the tool (vendored copies upgrade
  in place, ready to commit).

For the full contracts — configure.py's API, patch discovery, the
pybind bridge, install overlays, the conan remote seam — read
`reference.md` in this skill's directory.
