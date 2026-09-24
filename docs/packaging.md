# Shipping a project as a conan package

A project says once, in `buildutil.toml`, that it ships as a package and
what kind it is; everything else the recipe needs it discovers from what
the build already produced. `buildutil publish` builds, exports the built
tree as the package, runs the package test against the conan cache like a
real consumer, and uploads. The version is never written down anywhere.

```toml
[package]
kind = "library"        # library | application | none
name = "oxbox"
```

`buildutil init` asks the question — multiple choice at a terminal,
`--package=library|application` or `--no-package` in a script — and
commits the answer, including the "don't package" answer, so it never
re-asks. Re-running `init` on an older project is the upgrade path: a
pre-packaging recipe is regenerated and the original kept as
`conanfile.py.bak`.

## A package describes itself from what it ships

`package_info()` discovers the libraries, their directories and the
headers out of the package tree, so a project normally declares nothing
beyond the two lines above. A `library` recipe gets `package_type =
"library"` and a `shared` option defaulting to off, and every module's
static archive installs at its mirrored spot; an `application` recipe
gets executables in `bindirs` and empty `libdirs` and `includedirs`.

Two facts presence cannot supply. The first is that what a package ships
is **loaded**, not linked:

```toml
[package]
kind = "library"
linkable = false
```

which keeps `cpp_info.libs` empty while the library and include
directories stay — for a package whose shared objects are opened by name
at run time, where a consumer must not get `-lthe_thing_it_loads` on its
link line. Nothing in the tree can tell such a shared object from one
meant to be linked, so the project says so. The second is presence-driven
again: a `share/cmake/<package name>/*.cmake` in the install tree **is** a
cmake build module and is announced as one, so a project that generates a
`.cmake` into an `*.install/` tree has `find_package` include it — which
is how a package announces the runtime payload described below.

## The install tree is what the package ships

Module test and bench executables are build-tree artifacts — ctest runs
them where they were built — so they are excluded from the default
install rather than published inside every consumer's cache. They are
still reachable: `cmake --install <build> --component tests` (or
`benches`) stages them where one is wanted, and `buildutil build` does
exactly that into the local `_install/` so the local mirror stays
complete. A module with no sources of its own gets a real library target
from a generated empty translation unit, but the archive built from it
holds no object and is not installed: nothing can link it, and shipping
it makes the recipe advertise a library that is not one. A `.test` module
never ships at all: no archive, no headers, no component.

## The version never lives in the package source

`buildutil publish` derives it: the base is the last git tag reachable
from `HEAD` that is a valid semantic version, and the build number is
bumped **on a successful upload only**, giving a four-component
`1.2.3.1`. The number is one above the highest this base is known to
carry anywhere — a `conan list` against the configured remote, floored
by the counter persisted in `_bdudata/package-version.ini` — so two
boxes publishing the same tag no longer both produce `1.2.3.1`, where
the second is shadowed by the first for every range consumer. The local
counter is checkout-local state like everything else under `_bdudata`,
and a new tag resets it. An unreachable or unconfigured remote falls
back to that counter with a note, and `--version` remains the offline
escape. With no semver tag the base is prompted at a terminal
and saved, and refused otherwise. `--version X.Y.Z.B` overrides and
persists nothing, `--no-version-autoincrement` holds the number, and a
dry run consumes nothing. There is deliberately **no `[package] version`
key**: writing one does nothing.

## Publishing

`buildutil publish` is build → `conan export-pkg` of the built tree →
`conan test test_package` against the cache → upload of the recipe and
its binaries, **for both configurations**: Release and then Debug, one
version and one build number, uploaded together. A package that exists
Release-only cannot be resolved by a consumer whose profile is Debug —
conan computes a different `package_id`, finds no binary and lands in
the recipe's source-build refusal — so covering both is the default and
publish time is roughly double a single-configuration build. An explicit
[profile flag](commands.md#building) narrows the run to that configuration,
including RelWithDebInfo, and says at the
end which consumers it left without a binary. `--no-upload` stops after
the package test as a local dry run. An unconfigured remote is a hard
error for `publish`, unlike the dependency upload after an ordinary
build, which is merely skipped with a note when there is nowhere to send
it.

Dependency uploads include only binaries built or found in the cache in
this invocation's resolved Conan graphs. The project's own package name
is excluded at every version and user/channel; unrelated cached packages
are never uploaded. Publish combines the graphs of all built configurations.

The dependency upload after a build happens by default, because the
remote is the fleet's binary cache and a pipeline that quietly rebuilds
ffmpeg, opencv and x264 from source is what the default exists to
prevent. It runs only where it can succeed: the seam needs a URL,
`CONAN_REMOTE_USER` and `CONAN_REMOTE_PASS`, and a login the server
accepted at registration time. No URL, an anonymous remote, or
credentials the server refused each skip the upload with a one-line
note, so reading from someone else's remote never pushes a local cache
at it. The switch that turns the upload off where it would otherwise
run is `--skip-dependency-upload-so-everyone-rebuilds-from-source`,
which nobody types by habit.

A runtime `Require(NAME VERSION "…")` whose version is a range — `*`, or
anything starting `>`, `<`, `~` or `^` — is refused with exit 2 unless
`--allow-version-ranges`. A published binary is reproducible only if the
graph it was built against is pinned; `SYSTEM`, `TOOL`, `TEST` and
`BENCH` dependencies are exempt, since none of them ends up inside the
package: a `SYSTEM` one is whatever the consumer's host has, checked
against the floor on the consumer's machine.

## Host packages in a published package

A runtime `Require(NAME VERSION "<floor>" SYSTEM)` puts
`<conan>/system@host` in the graph: a wrapper recipe buildutil renders
from the NAME alone into `_bdudata/host/<conan>/`, so its text is the
same for every project. The driver exports it at a conan install when
the cache lacks that `<ref>#<rrev>` (`conan list`), and the project's
recipe forces the wrapper pinned to that revision, computed from the
rendered text the way conan hashes an export. A recipe in the cache has
no rendering and requires the wrapper unpinned; its consumer's pin, or
conan's latest revision, resolves it. The line's `COMPONENTS` and the
project's other `SYSTEM` lines reach the wrapper as the `components`
and `packages` options. Two recipes in one graph asking one wrapper for
different `components`, or for different `packages` entries of the
packages it finds, are refused, naming both recipes, both values and
the Require line to widen: conan keeps one value per option and would
drop the other silently.

At a conan install the wrapper runs `find_package(NAME)` in a scratch
cmake project outside any toolchain and describes each library target
the package's own config imported (interface targets included) as a
conan component: the library file by its full name at the host's
absolute directory, the include directories, the defines and the link
items. It publishes the map from each target to the component that
declares it as the `buildutil_host_targets` property, which the
project's recipe and other wrappers read. A target a nested
`find_dependency` imported belongs to that package: the wrapper requires
the dependency's own wrapper and points the component there, and without
a `SYSTEM` line for the dependency it refuses and names the line to add.
The C runtime's own libraries are no package: `Threads::Threads`,
`-pthread`, `dl` (`CMAKE_DL_LIBS`), `m` and `rt` become system libs. A
link item it cannot describe (a generator expression other than
`$<LINK_ONLY:...>`, or a namespaced target the find did not import) is
refused by name. A package whose find sets no `<NAME>_VERSION` is a
probe failure.

The probe is cached in `CONAN_HOME/buildutil-host/`, keyed on the
wrapper's recipe text, the NAME, the components, the `os`, `arch` and
`build_type` settings, and the environment variables `CMAKE_PREFIX_PATH`,
`<NAME>_ROOT`, `<NAME upper>_ROOT`, `PATH` and `PKG_CONFIG_PATH`. A
cached probe is redone when a file the find read (config and version
files, a Find module's version header, each `.pc` file the find asked
pkg-config for, the libraries) changed, or when the set of
`<prefix>/{lib,lib64,lib/*,share}/cmake/<NAME>*` directories under the
search prefixes (`<NAME>_ROOT`, `CMAKE_PREFIX_PATH`, the prefixes of
`PATH`, `/usr/local`, `/usr`, `/`) changed.

The wrapper's `package_type` is `unknown`, so conan never puts the
host's library directories on `LD_LIBRARY_PATH` ahead of a package's own
`RUNPATH`. The requirement is `force=True`, so it replaces every pin of
that package in the package's subtree, and every dependant of it (oxbox
pinning openssl, say) builds from source once per host version and
profile; a dependant that is not baked is refused with the usual
message. The wrapper's package id modes are `full_package_mode`: a
dependant's package id carries the wrapper's package id (the host
version and the components) and never its recipe revision, so an edit
to the wrapper template, or a revision someone else published, re-ids
nothing. The exported `sources/CMakeLists.txt` carries the line, so the
published recipe re-derives the force and the refusals in every
consumer's graph: a consumer builds and links the host's library without
a `SYSTEM` line of its own, and a consumer whose own requirement pins
the package, or that has a sibling pinning it, gets conan's version
conflict, naming both; such a consumer adds its own `SYSTEM` line.
Publish uploads the recipes of the runtime `SYSTEM` wrappers beside the
package (recipe only: the binary is the consumer's own probe).

A module of a multi-module package links a dependency by the cmake
target name its config declares (`OpenSSL::SSL`, `Fake::A`), and
`package_info()` turns each into the conan component that declares it:
one map over every direct dependency, built from conan's
`cmake_target_name` of the package and of each component (with the
default `<conan>::<component>` spelling kept) and from a host wrapper's
`buildutil_host_targets`. A namespaced link no direct dependency declares
is refused by name; a plain name is a system library.

The component manifest records the targets a `SYSTEM` find imported
under `host`, and `package_info()` points each at the wrapper component
that declares it, so a static component tells its consumer to link the
host's library; a host target no wrapper declares is refused by name. A
cross build forces no wrapper, so its components declare no host target:
the target's package is the consumer's own `SYSTEM` line on that
platform. A runtime `SYSTEM` wrapper no component links through
`Link_dependencies` is unused, and conan refuses the package; link it
through `Link_dependencies`, never by hand.

## The package test

A packaged project gets a `test_package/` scaffold that consumes the
cached package the way a real consumer does. A library's test builds a
smoke executable against `find_package(<name> REQUIRED CONFIG)` and runs
it; an application's test walks the package folder for something
executable and fails when there is nothing. An unqualified `buildutil
test` runs it too, so the packaging path is exercised without anyone
remembering to. This smoke exports under `@buildutil/smoke` and removes
that reference in a `finally` block, including when the package test fails.
Publish exports the plain reference.

## `--bake-buildutil` and building in the cache

By default a published recipe refuses to build from source in the conan
cache: binaries come from the remote. `publish --bake-buildutil` ships
the driver inside the package so a consumer's `--build=missing` actually
works. A vendored project's committed `.buildutil/` goes as it is; any
other project gets a copy **staged** at `.buildutil/` for exactly the
moment conan snapshots the exported sources and discarded in a `finally`
— publishing a self-building package never forces `buildutil install` on
the working tree.

A consumer whose profile finds no binary then falls into a real source
build: the recipe invokes the baked driver's `cache-build`, which is
stdlib-only — no venv, no nested conan, no network — renders the cmake
machinery and configures and builds against the toolchain conan already
generated, with the consumer's own resolution of every `Require()`. The
division of labour is the point: conan owns the dependency graph,
`cache-build` owns nothing but the machinery.

Configure-time code generation runs under the consumer's conan python,
which has never seen the package's `[venv] extra_deps`. A cache build
must not grow a venv inside the conan cache, and installing into a
consumer's interpreter uninvited is not the driver's call either, so a
declaration that interpreter does not already satisfy stops the build
up front, naming the requirement — rather than surfacing later as a
cmake package that cannot be found. The consumer says yes with
`BUILDUTIL_CACHE_BUILD_DEPS=install`, which pip-installs exactly the
missing declarations into that interpreter. A package whose generators
need `[venv] extra_deps` therefore still wants prebuilt binaries for
exotic consumers.

## A dependency's runtime payload

Some packages are not finished at link time. A browser framework resolves
its data files **relative to the directory the library was loaded from** —
not relative to the executable, and not through the library search path,
which actively breaks it. The only layout that works is the flat one:
library, data and executable in one directory with `$ORIGIN` on the
rpath. That is a fact about the *dependency*, and every consumer of such a
package was re-deriving it by hand.

```toml
[runtime]
from = ["cef"]            # dependencies with a runtime payload
targets = ["xoctet"]      # optional; default: every executable
dirs = ["contrib/blobs"]  # optional; repo-relative, no package behind it
```

It is opt-in per dependency, deliberately: most packages have no payload
and copying their `bin/` would be wrong.

The contract is published by the package, so buildutil never has to know
the dependency's name. For a dependency `<dep>`, with `<DEP>` upper-cased,
any of these announced from the cmake module its `find_package` loads:

| what | meaning |
|---|---|
| global property `<DEP>_RUNTIME_BINARY_DIR` | its contents go beside the executable |
| global property `<DEP>_RUNTIME_RESOURCE_DIR` | likewise |
| global property `<DEP>_RUNTIME_LIBRARY_DIR` | its **shared** libraries do |
| global property `<DEP>_FRAMEWORK_DIR` | macOS: a `.framework` for `Contents/Frameworks` |
| function `<dep>_copy_runtime(<target>)` | optional: the package populates the build tree itself |
| function `<dep>_copy_framework(<bundle>)` | optional: the same, into a macOS bundle |

Global properties rather than variables, because a cmake function body
sees its *caller's* scope: a plain variable set in the package's module
expands to nothing from inside one, and the copy then runs from the
filesystem root. A dependency named in `[runtime]` that publishes none of
the above is a configure error naming the contract — a declaration that
silently does nothing is the failure every rule here exists to prevent.

buildutil calls the package's function where there is one, since the
package knows its own layout, and copies the property directories itself
where there is not. **The install tree is always buildutil's job**: those
functions are `POST_BUILD` and only ever touch the build tree, while
`buildutil run` execs the *installed* binary — which is exactly the gap
consumers fell into, a program that runs from the build tree and dies
from the install tree. `$ORIGIN`, or `@loader_path` on macOS, goes on
both rpaths, and a module's test and bench executables get the payload
beside them too, since they do not live in `<build>/bin` and the
application's copy is no help to them.
