# The tree is the build script

A buildutil project holds no build logic. `buildutil.toml` at the repo
root marks the project and carries the handful of facts a directory
listing cannot state (see [config.md](config.md)); everything else — what
builds, what it is called, where it ships, what platform it is for, what
is a test — is read off the directory tree. This page is the whole
vocabulary: the reserved directories, the tags a directory or a file name
may carry, the names buildutil derives from them, and the short list of
cmake calls a project still writes.

## The roots

`sources/` is the one source root, and it is a universal include root, so
`sources/oxbox/serialization/node.hpp` is `<oxbox/serialization/node.hpp>`
everywhere — in the project, in its tests, and in a consumer of the
installed package.

The root `CMakeLists.txt` is four statements and never grows:

```cmake
cmake_minimum_required(VERSION 3.25)
project(demo CXX)
include(buildutil)
add_subdirectory(sources)
```

`sources/CMakeLists.txt` holds the project's `Require(...)` declarations
and one `Scan_subdirectories()`. The scaffolded `conanfile.py` re-parses
that same file for its requirements, so a dependency and its version are
written once.

Everything derived lives under a leading underscore and is gitignored:
`_build/` (one tree per profile), `_install/` (the install mirror),
`_profiles/` (generated conan profiles), `_conanhome/`, `_pyvenv/`, and
`_bdudata/` (the rendered cmake machinery plus `modules.ini`,
`config.ini`, `package-version.ini`, `vscode-inputs.json` and the build
stamp). `buildutil clean --nuke` is exactly `rm -rf _*`.

Three more directories are read when present: `cmake/` at the repo root
(the project's own cmake, included last), `test_package/` (the conan
package test, see [packaging.md](packaging.md)), and `.buildutil/` (a
vendored copy of the driver, see [commands.md](commands.md)).

## The cmake machinery

Nothing is committed into a project. The machinery ships inside the
buildutil package as templates and is rendered — with this project's
prefixes — into the gitignored `_bdudata/cmake/` on every command, and
the driver hands that directory to cmake as `CMAKE_MODULE_PATH`, so
`include(buildutil)` resolves. That one line also carries the universal
policy a root would otherwise restate: the compile database,
`include(CTest)` and `include(GoogleTest)`, the `BUILD_BENCHMARKING`
option, and the coverage and section-GC switches with their flag blocks.

Every rendered file leads with a do-not-edit banner and a version stamp
naming the buildutil that wrote it, files no longer owned are swept, and
`buildutil --version` inside a project reads the stamp back:

```
buildutil 0.82.0 (/usr/local/lib/python3.14/site-packages/buildutil)
  cmake machinery: /project/_bdudata/cmake — STALE, rendered by 0.81.0 (the next build re-renders it)
```

To extend the machinery for one project, drop a `*.cmake` into the
project's own `cmake/` directory. Every file there is globbed, sorted and
`include()`d at the end of the machinery, after every function is
defined, so it can call, wrap or override them; its presence is the whole
declaration. Those files are the project's and buildutil never rewrites
them. That is the seam: `_bdudata/cmake/` is buildutil's and updates
itself, `cmake/` is the project's and is never touched.

## Modules and groups

A directory under `sources/` holding a `CMakeLists.txt` is a **module**
and is added; one without is a **group** and is recursed into. A
directory whose name starts with a dot never builds at all, which is what
`.archive/` is for. `CMakeList.txt` — missing the `s` — is a warning
naming the directory that is not being built, because the alternative is
a subtree that silently disappears.

A module's target name is its `sources/`-relative path with `-` joining
the components: `sources/demo/hello/` is `demo-hello`. Its binary, its
shared library and its archive take the **leaf** name, and it also gets a
namespaced alias, `demo::hello`. `Link_dependencies` accepts the leaf
alone where it is unambiguous; an ambiguous leaf is a configure error
naming both full names.

## Kind tags

What a module builds is in its directory name:

| spelling | what it is |
|---|---|
| `wd.exe/` | an executable — as is any module holding `main.cpp` |
| `parser.lib/` | a static library (`.a` is a synonym; this is the default) |
| `dip.so/` | a shared library (`.dll` and `.dylib` are synonyms) |
| `dipapi.obj/` | an object-only module |

The tag is **never part of a name**: `sources/wd.exe/` is the module `wd`
and ships a binary called `wd`. More than one kind tag is a configure
error, as is a tag that contradicts the files (`.lib` on a module with
`main.cpp`) — a listing that disagrees with the tree is worse than no
listing. `.so`, `.dll` and `.dylib` are synonyms rather than platform
selectors: the target picks the real suffix.

An `.obj` module is for code resolved at run time rather than at link
time — a plugin calling back into its host. Everything linking it takes
every object, including the ones nothing references, however far away the
module is: cmake propagates an object library's members to direct
consumers only, so buildutil puts them on the final executable's link
line itself. `Init_submodule(PUBLISH_SYMBOLS)` is the other half of that
story — it keeps an executable's symbols in the dynamic symbol table and
lifts the hidden-visibility default, and it is deliberately not tied to
the build type: a host whose plugins resolve in Debug and fail in Release
is a binary that works for whoever built it and breaks for whoever ships
it. It covers the module's LIBRARY as well, which is how a shared module
says "export everything" out loud — `WINDOWS_EXPORT_ALL_SYMBOLS` under
MSVC, default visibility everywhere else. Both halves are settled at
generate time, so no later flag can quietly take the visibility back.

A shared library can also be selected by presence: `main.so.cpp`,
`main.dll.cpp` or `main.dylib.cpp` — the file holding `DllMain` or its
equivalent, the way `main.cpp` selects an executable. `main.cpp` itself
has no tagged spelling; the glob matches that name exactly.

## Platform tags

One vocabulary, rendered into the machinery from `buildutil/naming.py` so
the spellings cannot drift:

| tag | target systems | kind |
|---|---|---|
| `win32` | Windows (`windows` is also accepted in `platforms`) | exact |
| `linux` | Linux | exact |
| `macos` | Darwin | exact |
| `emscripten` | Emscripten | exact |
| `posix` | Linux, Darwin, Emscripten, and any unnamed target | family |
| `apple` | Darwin | family |
| `native` | Windows, Linux, Darwin — every target but the browser | family |

The same word is read in four places: on a **module or group directory**
(`sources/helper.macos/` exists on macOS and nowhere else — on any other
target the directory is never entered, so its CMakeLists never runs and
nothing it declares exists), on an **inner directory** of a module
(`host.linux/`, and tags nest: a `*.win32` subtree inside a `host.linux/`
one builds nowhere), on a **source file** (`decode.linux.cpp`), and on a
**data directory** (`ui.embed.win32/`).

Selection is by the **target** platform, cross builds included, never the
host: cmake sets `APPLE` from `CMAKE_SYSTEM_NAME` after `project()`, so a
cross-build from Linux to Darwin is Apple here and picks up the `.mm`.

Specificity is untagged 0, family 1, exact 2, and two applicable things at
the same level claiming one name is a configure error naming both —
nothing orders `ui.embed.posix/` against `ui.embed.apple/` on macOS, and
inventing a tiebreak would be worse than refusing. A directory tagged for
another target is not merely skipped, it is never globbed, so editing a
Windows asset on Linux does not re-run cmake.

Platform and kind tags compose in either order: `helper.macos.exe/` and
`helper.exe.macos/` are the same module.

## Extensions carry a platform of their own

An explicit tag **intersects** the extension's default rather than
replacing it:

| extension | platforms | language enabled |
|---|---|---|
| `.cpp` `.cc` `.cxx` `.c` | all | (from `project()`) |
| `.mm` | Apple | `OBJCXX` |
| `.m` | Apple | `OBJC` |
| `.rc` `.manifest` | Windows | `RC` |
| `.s` `.S` | all | `ASM` |

So `window.mm` needs no tag, `foo.posix.mm` is legal and means macOS, and
`foo.linux.mm` is a configure error naming both — the file would be for no
platform at all and would simply never build, which is exactly the silence
these refusals exist to break. Only the last dot-segment before the
extension is read as a tag, and only if the table holds it: `foo.test.cpp`
and `foo.v2.cpp` are untouched. Languages are enabled at root scope by
presence, from that same table, so a tree with no `.mm` never pays for the
probe. `.asm` is deliberately absent — the watcom extension's
`Init_firmware()` owns it, and claiming it in the module glob would
silently compile a firmware image into a host module.

## Suffix-typed directories

Inside a module, a directory's suffix says what kind of directory it is,
and a platform tag goes **after** the suffix:

| spelling | what it is |
|---|---|
| `*.test/` | every source under it is a test source for `<module>-tests` |
| `*.bench/` | likewise for `<module>-benches` |
| `*.test/*.py` | a pytest suite registered as `<module>-pytest` |
| `*.install/` | runtime data, shipped beside the binary |
| `*.embed/` | resources compiled into the binary |
| `*.patch/` | a patch overlay, mirroring the tree it patches |
| `*.install.<tag>/`, `*.embed.<tag>/` | a platform overlay of the base set |

`ui.linux.embed/` — the tag before the suffix — is refused by name with
the spelling it meant, because it would otherwise be globbed as an
untagged set called `ui.linux` and ship everywhere. A `.cpp` sitting in an
`*.install/` or `*.embed/` subtree is data and is never compiled. The
overlay rules and the resource story are in
[resources.md](resources.md).

Classification runs on the project-relative path, not the absolute one, so
a checkout under `~/scratch.test/` does not turn the whole tree into
tests.

## Headers

Every header under a module installs at the module's `sources`-relative
path, which **is** its in-tree spelling, so a public header that includes
a sibling cannot compile in the project and break on install. Header
extensions are `.h .hpp .hxx .hh .inl .ipp`. Export is opt-out: a leading
underscore on the file or on any directory component keeps a header
private, as do `*.test/`, `*.bench/`, `*.install/`, `*.embed/` and
dot-directories.

The underscore is the one private marker; there is no `*.private/`
directory and no `.private.hpp` suffix. The scaffolded `.gitignore`
anchors its derived-state rule to the repo root (`/_*`, and
`/test_package/_*` for the package test), so `_detail.hpp` inside a
module is tracked like any other source. A project scaffolded before
0.86.1 carries an unanchored `_*` and must anchor it, or its private
headers are never committed.
Kind tags are deliberately **not** stripped here, unlike
the binary mirror, because the in-tree spelling resolves through
`sources/` with literal directory names.

Headers are never filtered by platform tag. Nobody chooses what
`#include "foo.h"` opens — the preprocessor does — so a tagged header
would resolve or not by a rule the include path knows nothing about. A
per-platform header is reached the ordinary way, from the per-platform
source that includes it.

A module's own directory is on its include path automatically, so a header
beside the source is `#include "thing.hpp"` and stays that way from a
subdirectory, from generated code and from a `*.test/` subtree. That root
is **private**: exporting it would put every linked module's directory on
every consumer's include path, where an unqualified `#include "types.h"`
that should have been an error instead resolves by link order. A
header-only module is an INTERFACE library and has no private scope, so
its root is necessarily visible to whoever links it.

Porting a legacy tree is the one case where that leak is the lesser evil,
and it is declared once for the whole project with `[cmake]
export_module_headers`. There is deliberately no per-module form: the fact
being expressed is "this tree was ported", not "this module is special".

## The install mirror

A module's artifact installs at its source-relative path.
`sources/plusplus/wpp386/` ships its binary as `<prefix>/plusplus/wpp386`
and a shared module ships `libdipdwarf.so` in its mirrored parent — the
leaf names the file, kind tags stripped from every component. There is no
destination mapping anywhere: a project that wants a different shipped
layout moves its *source* directories until the mirror is that layout.
The *build* tree deliberately does not mirror — `<build>/bin` stays the
flat working set behind the test and run PATH contracts, carrying a copy
of every staged `*.install/` file so an exe-relative lookup answers
in-tree exactly as it does installed.

## Dormant modules

A module that is not buildable yet is a project fact and is committed, in
`[modules] dormant`. The local `_bdudata/modules.ini` — written by
`buildutil module enable|disable` — overrides it in both directions, so
whoever is porting a module builds it locally without touching a
committed file. A dormant module drops out entirely: no library, no
generated sources, no test target, no `<PREFIX>_<NAME>_ENABLED` define,
and its name is removed from every `Link_dependencies` list, so the seams
that guard it fold away. `[modules.<name>] platforms` is the same
mechanism keyed to the target instead of to a person.

## Symlinked source directories

A module may reach sources that live elsewhere in the repo through a
symlinked directory, and **git's index is the authority** — `git ls-files
-s` mode `120000`, not the filesystem — so a Windows checkout with
`core.symlinks=false` is resolved by reading the one-line text file the
checkout left behind. Classification happens on the link's own name, so
`.test`, `.bench`, platform tags and `main.cpp` all key off the leaf. The
link's target directory becomes a "pool" appended to the include path. A
header present both in the leaf and in a pool it borrows from is a
configure error — same tree, same command, a different artifact, and it
links either way — and a symlinked *header* is refused outright, because
the preprocessor opens whatever the `#include` names and the failure would
otherwise surface as a syntax error inside a file the author never wrote.
An on-disk symlink not yet in git's index is a warning.

## What a project still writes

| call | what it does |
|---|---|
| `Require(NAME [TEST\|BENCH\|TOOL\|SYSTEM] [PUBLIC] [VERSION v] [CONAN pkg] [COMPONENTS c...] [PLATFORM os...] [OPTIONS k=v...])` | declares an external dependency once |
| `Scan_subdirectories()` | computes the dormant set and scans for modules and groups |
| `Init_submodule([PUBLISH_SYMBOLS])` | the whole presence-driven module |
| `Init_python_module()` | the module builds as a python extension instead of a library |
| `Init_script()` | installs the file named like the module as a program at the mirror |
| `Link_dependencies(<dep>... [TEST <dep>...] [BENCH <dep>...])` | module and package dependencies |
| `Add_generated_source(OUTPUT <rel> SCRIPT <path> [STAGE BUILD\|TEST\|BENCH\|DATA] [DEPENDS ...] [ARGS ...] [SIDE_OUTPUTS ...])` | registers a build-time generator |
| `Resolve_generated_source(<rel> <out_var>)` | the patched copy where one was discovered, else the in-tree source |
| `Init_firmware([LIBRARY] [OPROM] [BASE <hex>] [ORG <hex>])` | the watcom extension's raw 16-bit ROM image |

`Require`'s version is a **floor** and it resolves against the remote:
every conan install passes `--update`, so "fixed in x.y.z and my floor
covers it" just works and a stale cached satisfier cannot masquerade as a
broken fix. The floor is capped at the next major when it reaches conan,
so a non-semver recipe version cannot sort above a real release.
`SYSTEM` says the host provides the package: `find_package` still runs and
nothing is asked of conan, and the directories of the shared libraries it
found go on the app's install rpath so an installed binary starts with
nothing in the environment. `TEST` and `BENCH` deps only materialise when
tests or benches build, which is what makes `buildutil build --no-tests`
drop GoogleTest from the graph entirely; `TOOL` deps are build tools, and
their bindir is handed to `find_program` and to a module's `configure.py`
ahead of `PATH`. `COMPONENTS` tokens starting `+` or `-` are conan option
shorthand and are stripped before `find_package`; `+x` together with `-x`,
or a shorthand colliding with an explicit `OPTIONS` entry, is refused
rather than resolved by last-wins.

`Link_dependencies` puts its positional arguments PUBLIC on the module
library (INTERFACE if it is header-only), and its `TEST` and `BENCH`
groups PRIVATE on the suite targets. Module names resolve to the library
target, dormant names are dropped, and anything else passes through to
cmake. There is no call for compile definitions and there will not be
one: a constant belongs in the code, in a header the module already has,
where a reader finds it and a debugger shows it. A value chosen per build
is a different thing — see [options.md](options.md).

An extension, or a project's own `cmake/*.cmake`, hooks the build by
**defining** either of two optional commands: `_buildutil_ext_pre_scan()`
runs once before any module is added, and `_buildutil_ext_module(<lib>
<target>)` once per module after its include roots are set. Two
extensions defining the same hook collide silently, last include wins.
