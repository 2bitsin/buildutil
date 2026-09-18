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
it makes the recipe advertise a library that is not one.

## The version never lives in the package source

`buildutil publish` derives it: the base is the last git tag reachable
from `HEAD` that is a valid semantic version, and the build number is
bumped **on a successful upload only**, giving a four-component
`1.2.3.1`. The pair persists in `_bdudata/package-version.ini`, which is
checkout-local state like everything else under `_bdudata`, and a new tag
resets the counter. With no semver tag the base is prompted at a terminal
and saved, and refused otherwise. `--version X.Y.Z.B` overrides and
persists nothing, `--no-version-autoincrement` holds the number, and a
dry run consumes nothing. There is deliberately **no `[package] version`
key**: writing one does nothing.

## Publishing

`buildutil publish` is build → `conan export-pkg` of the built tree →
`conan test test_package` against the cache → upload of the recipe and
its binaries. `--release` is the default and `--debug` the alternative,
and `--no-upload` stops after the package test as a local dry run. An
unconfigured remote is a hard error for `publish`, unlike the dependency
upload after an ordinary build, which is merely skipped with a note when
there is nowhere to send it.

Uploads happen by default whenever a remote with credentials is
configured, and are skipped with a note when one is not: the remote is
the fleet's binary cache, and a pipeline that quietly rebuilds ffmpeg,
opencv and x264 from source is what the default exists to prevent. The
switch that turns it off on a build verb is
`--skip-dependency-upload-so-everyone-rebuilds-from-source`, which nobody
types by habit.

A runtime `Require(NAME VERSION "…")` whose version is a range — `*`, or
anything starting `>`, `<`, `~` or `^` — is refused with exit 2 unless
`--allow-version-ranges`. A published binary is reproducible only if the
graph it was built against is pinned; `SYSTEM`, `TOOL`, `TEST` and
`BENCH` dependencies are exempt, since none of them ends up inside the
package.

## The package test

A packaged project gets a `test_package/` scaffold that consumes the
cached package the way a real consumer does. A library's test builds a
smoke executable against `find_package(<name> REQUIRED CONFIG)` and runs
it; an application's test walks the package folder for something
executable and fails when there is nothing. An unqualified `buildutil
test` runs it too, so the packaging path is exercised without anyone
remembering to.

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
`cache-build` owns nothing but the machinery. Configure-time code
generation runs under the consumer's conan python, so a package whose
generators need `[venv] extra_deps` may still want prebuilt binaries for
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
