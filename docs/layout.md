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
written once. It reads it through `buildutil_requires.py` beside it, the
one Require() parser, which the driver also uses and rewrites whenever
its own copy differs.

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
| `rig.test/` | a test-lane module: support code for the suites |

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

A `.test` module is what several suites share: fixtures, fakes, a headless
client, gtest registrations. It builds when tests or benches build, links
`GTest::gtest` itself (so `Require(... TEST)` packages are found in the
bench lane too), and is linked from another module's `TEST` or `BENCH`
group only; a positional link to it is a configure error naming both
modules, whether spelled by leaf, full name or alias. Its own links are
positional and may name test-only packages, because it is test lane
already; a `TEST` or `BENCH` group in its own `Link_dependencies` is a
configure error, since it has no suite for one to land in. It is an object
module, so every object of it reaches each `-tests` and `-benches`
executable that links it, directly or through other modules, and a
registration nothing references still runs in every suite; the same sweep
carries an ordinary `.obj` module into the suites. It has no suite of its
own: a `*.test.cpp`, `*.bench.cpp`, `*.test/` or `*.bench/` subtree, a
python suite, a `*.pybind.cpp` bridge or an `*.install/` tree inside it is
refused by name, in every lane, and so is `Init_submodule(PUBLISH_SYMBOLS)`
on it. It never ships: no archive, no header export, no entry in the package
manifest. Its headers reach the suites at their `sources/`-relative spelling
(`<sdl-rdp/headless-client.test/client.hpp>`). The tag is module-level only:
a `*.test/` directory inside a module holding a `CMakeLists.txt`, or a
`.test` group, reads two ways and is refused.

A shared library can also be selected by presence: `main.so.cpp`,
`main.dll.cpp` or `main.dylib.cpp` — the file holding `DllMain` or its
equivalent, the way `main.cpp` selects an executable. `main.cpp` itself
has no tagged spelling; the glob matches that name exactly.

A shared library module's soname number is its `configure.py`'s
`soversion()`, or else the package major (below), on every platform:
`soversion(0, version="0.4.8")` gives `libX.so.0` and the full-version
file `libX.so.0.4.8` it links to; without one the file is `libX.so.<major>`
with the link `libX.so` beside it, and the install mirror ships the file
and its links. A version fact lives beside what it describes, so there is
no `buildutil.toml` key for it; a hook that declares one on a module that
does not build as a shared library, or on a group, is refused.

## Exporting symbols

A shared module exports what its definitions mark and nothing else:

```c
#define SDLRDP_ABI_VERSION 7                               /* the ABI header, beside the prototypes */
int sdlrdp_open(const sdlrdp_config*, sdlrdp_handle**);    /* the header stays plain C */
auto _Public_() sdlrdp_open(sdlrdp_config const* c, sdlrdp_handle** h) -> int { ... }
auto _Public_(SDLRDP_ABI_VERSION) sdlrdp_port(sdlrdp_handle const* h) -> uint16_t { ... }
```

`_Public_()` is the normal spelling: it exports the function at the
package's semver major. The package version is the build identity's
`package` field: the version `publish` builds as (`--version` included),
the version conan builds a published recipe as, and for any other build
the last reachable `x.y.z` tag; with no tag it is `0.0.0`, configure says
so once, and `_Public_()` exports at 0, as it does for a version that is
not semver (`publish --version dev`). Two clones of one commit build the
same major. `_Public_(n)` exports at `n`, which after macro expansion is
a decimal integer, zero or more with no leading zeros: a library that
keeps its own ABI counter spells `_Public_(ABI_VERSION)`, one that pins
an old function spells the old number.

The mark goes on the **definition** of a function with external linkage,
never on a prototype. `_Public_` is one name in a header buildutil
force-includes into every C and C++ unit, and one name cannot tell a
module's own units from a consumer's. It is default visibility with gcc
and clang and `__declspec(dllexport)` under MSVC; a consumer calls the
function without `dllimport`, which Windows allows for functions only.
It also puts the function in the section `.text._Public_.<n>`, which is
how the version reaches the link: the mark has no effect on code
generation, and the linker merges the section into `.text`.

On an ELF target, before a shared module links, buildutil reads its
object files and those of the `.obj` modules it links directly, and
every global or weak function in a mark section is an export at its
version. A mark exports from the shared module that compiles the
definition; a library module folded into it by static linkage exports
nothing, because `--exclude-libs,ALL` hides its archive, and
`module_linkage=shared` exports its marks from its own library. A module
whose marks a shared module must re-export is declared `.obj`, whose
objects the link takes whole. The link fails, naming the object and the
symbol, on: a suffix that is neither empty nor such an integer
(`_Public_(1.5)`, `_Public_(x)` with `x` undefined); a marked variable,
thread-local or anything else that is not a function; a mark on a
`static` or anonymous-namespace function, which is local; a mark section
with no global function in it (ppc64 ELFv1, whose functions live in
`.opd`); one symbol marked at two versions; and an LTO object without
machine code, whose marks are not yet in any section. LTO
(`CMAKE_INTERPROCEDURAL_OPTIMIZATION` or `-flto`) needs fat objects, so
on ELF buildutil compiles every module with `-ffat-lto-objects` (gcc,
clang 17 and later; without `-flto` it changes nothing); older clang
emits bitcode, which fails naming the object. The marks become
`<generated>/_buildutil/exports/<module>/exports.map`, one node
`<OUTPUT_NAME>_<n>` per version, the library's output name upper-cased
with every other character `_` (`libplug-in.so` gives `PLUG_IN_7`), each
newer node inheriting the older, `local: *` in the oldest: nothing
unmarked leaves the library, template instantiations of the standard
library included. The node is what a consumer binds to, so renaming the
output name renames every node and is an ABI break. The soname is the
package major or the hook's `soversion()`, never a mark: a node above
the soname is a symbol added since, as glibc's `GLIBC_2.34` lives in
`libc.so.6`. A static module or package the library links never reaches
its dynamic table: the link adds `--exclude-libs,ALL`.

A package major bump moves every `_Public_()` symbol to the new node and
the soname with it, so clients linked against the old one stop loading;
a library that promises compatibility pins its existing functions with
`_Public_(n)` at the bump.

On macOS and Windows no scan runs: the ELF section name is not a valid
Mach-O section name, so there the mark is visibility alone, and under
Windows it is `dllexport`; either way the functions are unversioned and
the soname number is the one rule above. The MSVC spelling
(`code_seg(".text$_Public_.<n>")`) is not compiled by any lane of this
repository's CI. An untagged module built shared by `module_linkage=shared`
(which is what a conan `shared=True` build does) gets the header and no
scan: its marks export by visibility only, unversioned, and it carries no
soname, so `soversion()` is refused there as on any module that is not
`main.<so|dll|dylib>.cpp` or `.so`.

The package version the mark reads is also in `buildinfo.hpp`, which is
included by hand ([options.md](options.md)):
`<module_define_prefix>_PACKAGE_VERSION` (`"1.2.3"`) and
`_PACKAGE_VERSION_MAJOR`, `_MINOR` and `_PATCH`, for a runtime check that
two artifacts of one package agree on their ABI.
`PUBLISH_SYMBOLS` stays for a plugin host resolving into its executable.

A version script can also be written by hand: an `exports.map` beside
`main.<so|dll|dylib>.cpp` or at the module root, or one the module's
`configure.py` emits at a generated root (`emit("exports.map", ...)`).
It replaces the generated one, and the library is then checked after
the link: a marked function the script does not export fails the build
naming the script and the symbol. Editing the script relinks.
`PUBLISH_SYMBOLS` composes with it: the flag lifts hidden visibility, the
script then decides which symbols the dynamic table keeps and under which
node. ld64, link.exe and wasm-ld read no version script, so on macOS,
Windows and Emscripten the library links without it and configure says
so. An `exports.map` in a module that is not a shared library, or a
checked-in one beside a hook-emitted one, is refused naming the files.

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
| `*.test/` | every source under it is a test source for `<module>-tests`; a directory under `sources/` holding a `CMakeLists.txt` is a `.test` module instead (see Kind tags) |
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
| `Require(NAME [TEST\|BENCH\|TOOL\|SYSTEM [FORCE]] [PUBLIC] [VERSION v] [CONAN pkg] [COMPONENTS c...] [PLATFORM os...] [OPTIONS k=v...])` | declares an external dependency once, in `sources/CMakeLists.txt` |
| `Scan_subdirectories()` | computes the dormant set and scans for modules and groups |
| `Init_submodule([PUBLISH_SYMBOLS] [STANDARD 20\|23\|26])` | the whole presence-driven module; `STANDARD` overrides `[project] cxx_standard` and reaches the module's library, its executable, its tests, its benches and its `*.pybind.cpp` bridge; an `Init_python_module()` module and its tests and benches take no `STANDARD` and follow the project's; targets an extension creates itself (watcom's ROM images) are not touched ([toolchains](toolchains.md#version-caps-and-the-c-standard)) |
| `Init_python_module()` | the module builds as a python extension instead of a library |
| `Init_script()` | installs the file named like the module as a program at the mirror |
| `Link_dependencies(<dep>... [TEST <dep>...] [BENCH <dep>...] [RUNTIME <module>...])` | module and package dependencies |
| `Add_generated_source(OUTPUT <rel> SCRIPT <path> [STAGE BUILD\|TEST\|BENCH\|DATA] [DEPENDS ...] [ARGS ...] [SIDE_OUTPUTS ...])` | registers a build-time generator |
| `Resolve_generated_source(<rel> <out_var>)` | the patched copy where one was discovered, else the in-tree source |
| `Init_firmware([LIBRARY] [OPROM] [BASE <hex>] [ORG <hex>])` | the watcom extension's raw 16-bit ROM image |

`Require`'s version is a **floor** and it resolves against the remote:
every conan install passes `--update`, so "fixed in x.y.z and my floor
covers it" just works and a stale cached satisfier cannot masquerade as a
broken fix. The floor is capped at the next major when it reaches conan,
so a non-semver recipe version cannot sort above a real release.
`Require` lives only in `sources/CMakeLists.txt`, the one file the
conanfile reads, once per NAME on a platform (a NAME split by
`PLATFORM`, conan on one target and `SYSTEM` on another, is two lines).
`SYSTEM` says the host's package wins over every conan pin of it
anywhere in the graph: a `<conan>/system@host` wrapper is forced through
the graph, travels to the consumers of a published package (see
packaging.md, "Host packages in a published package"), and conan's copy
is never installed. The project forces the wrapper at the revision this
driver rendered, so `--update` cannot swap in one someone else
published. `CONAN` names the conan package it replaces,
`PUBLIC` is valid with it, and the host is refused before anything
builds when it is below the floor or below any pin it replaced (at
least the pin, same major); a requirement downstream that pins the
package is refused by conan as a version conflict. `FORCE` waives the
pin comparison, never the floor nor the conflict, and conan prints each
waived pin once per conan install. A `SYSTEM` find reaches the host's
own config or Find module, never conan's: `CMAKE_PREFIX_PATH`,
`CMAKE_MODULE_PATH`, `CMAKE_LIBRARY_PATH`, `CMAKE_INCLUDE_PATH` and
`CMAKE_PROGRAM_PATH` lose conan's generators folder and cache for that
find, a `<NAME>_DIR` cached there is forgotten, and a target it imports
from inside the conan cache is refused (the imported locations are
checked, not the find's result variables). The wrapper's generated
config serves conan dependants such as oxbox. The directories of the
shared libraries the find imported go on the app's install rpath so an
installed binary starts with nothing in the environment. A cross build
forces no wrapper, since the probe would describe the build machine, and
refuses a conan copy of the package in its graph; a package built so
declares no host target (see packaging.md). `TEST` and `BENCH` deps only
materialise when tests or benches build, which is what makes `buildutil
build --no-tests` drop GoogleTest from the graph entirely; `TOOL` deps are build tools, and
their bindir is handed to `find_program` and to a module's `configure.py`
ahead of `PATH`. `COMPONENTS` tokens starting `+` or `-` are conan option
shorthand and are stripped before `find_package`; `+x` together with `-x`,
or a shorthand colliding with an explicit `OPTIONS` entry, is refused
rather than resolved by last-wins.

`Link_dependencies` puts its positional arguments PUBLIC on the module
library (INTERFACE if it is header-only), and its `TEST` and `BENCH`
groups PRIVATE on the suite targets. Module names resolve to the library
target, dormant names are dropped, and anything else passes through to
cmake. `RUNTIME` names sibling modules this one loads with `dlopen`
rather than links: each must build as a shared library, and the loader
(an application or a shared library) gets the loader-relative hop to the
sibling's install mirror on its install rpath and the sibling's build
directory on its build rpath, the way a linked sibling's are computed;
an application's test and bench executables get the build rpath too,
since the dynamic loader searches the rpath of the object calling
`dlopen`, and in a test that object is the test executable.
It adds no link, since a plugin that links its host would otherwise
close a cycle; whatever runs the loader (its application, its test and
bench executables) builds the sibling first. A sibling that does not
build for the target platform is refused. Windows has no rpath, and
configure warns that the DLL must sit beside the loading executable.

There is no call for compile definitions and there will not be
one: a constant belongs in the code, in a header the module already has,
where a reader finds it and a debugger shows it. A value chosen per build
is a different thing — see [options.md](options.md).

An extension, or a project's own `cmake/*.cmake`, hooks the build by
**defining** either of two optional commands: `_buildutil_ext_pre_scan()`
runs once before any module is added, and `_buildutil_ext_module(<lib>
<target>)` once per module after its include roots are set. Two
extensions defining the same hook collide silently, last include wins.
