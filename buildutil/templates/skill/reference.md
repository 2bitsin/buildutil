# buildutil reference — the full contracts

Deeper detail behind SKILL.md. Everything here is presence-driven:
adding or deleting the file/directory IS the declaration.

## configure.py — the codegen / configure hook

A `configure.py` in a **module** dir (has CMakeLists) or a **group**
dir (no CMakeLists) runs at configure time. Use it whenever build
logic can't be expressed by structure — above all for source
generation. It re-runs when the script or any declared input changes,
and deleting the directory removes every trace (stale output is inert).

The contract is three environment variables the build sets:

    CONFIGURE_OUTPUT_DIR   per-profile root   _build/<profile>/generated/<name>
    CONFIGURE_SHARED_DIR   profile-agnostic   _build/generated/<name>
    CONFIGURE_MANIFEST     report-back file

A script may read those and write the manifest itself (`G <abspath>`
per generated file, `D <abspath>` per input dependency), but the
convenient way is the `buildutil_configure` module, importable inside
the hook (the build puts it on PYTHONPATH):

    import buildutil_configure as bc
    bc.depends("tables/isa.txt")            # changing it re-runs configure
    text = render_table(open("tables/isa.txt").read())
    bc.emit("x86/enums.hpp", text)          # -> #include "x86/enums.hpp"
    bc.emit("decode.cpp", impl)             # compiled into the module
    bc.emit("ids.hpp", ids, shared=True)    # profile-agnostic root

- `emit(relpath, content, shared=False)` — write-if-changed (an
  unchanged input never churns a rebuild) and register. The relpath is
  the include spelling.
- `declare(path)` — register an already-written file.
- `depends(*paths)` — inputs whose change re-runs configure.
- `output_dir(shared=False)` — the emit root, for tools that write
  their own files.

Both roots land on the module's include path automatically; emitted
`.c/.cc/.cpp/.cxx` files compile into the module; an emitted
`foo.test.cpp` becomes test sources, `foo.bench.cpp` bench sources —
the same suffix rules as static files.

**Group-level configure.py**: shared codegen for the whole subtree.
Its include roots are visible to every module at or below the group,
and nowhere else. A compilable source it emits is routed to a module
by PATH: emit it at the module's sources-relative path (an optional
leading `_private_/` is stripped), e.g. a group at `sources/x86/`
emitting `_private_/x86/decode/tables.cpp` hands the file to the
module at `sources/x86/decode`. A path mirroring no module is
include-only.

## *.install/ — shipped data overlays

A module may carry any number of `<name>.install/` dirs
(`locale.install/`, `tables.install/`). Each is a **prefix-rooted
overlay**: `data.install/x/y/z` is staged to `<build>/x/y/z` (so
in-tree runs find it beside the programs) and installs to
`<prefix>/x/y/z`, structure preserved. Files added to the directory
are picked up automatically (CONFIGURE_DEPENDS) — no list to maintain.

## *.patch — pristine-source patch overlay

A committed unified diff generates a patched copy at build time; the
in-tree source is never modified. Two self-describing layouts:

- sibling: `foo/bar.txt.patch` patches `foo/bar.txt`;
- directory: `<name>.patch/` (name may be empty — a bare `.patch/`),
  where the file's path below it mirrors the target relative to the
  directory's parent: `foo/.patch/sub/baz.txt` patches `foo/sub/baz.txt`.

The patched copy lands at `<build>/generated/<source-relative-path>`
(applied with git apply). Resolution rule everywhere: prefer the
patched copy when one exists, else the in-tree source —
`Resolve_generated_source()` is that rule, and generator DEPENDS use
it, so codegen consuming a patched file rebuilds when the patch
changes.

## *.pybind.cpp — the python bridge

A `*.pybind.cpp` in a module builds a pybind11 extension target
`<module>-pybind`, linked PRIVATE against the module's library and
installed under `lib/python`. The FILE is named for the module the
entry defines — the name in its `PYBIND11_MODULE(...)`, which is the
file's own stem — plus Python's SOABI suffix, because that is the name
`import` uses; the target name is the directory's and reaches nothing.
Requirements and limits:

- `pybind11` must be in the project venv — add it to `buildutil.toml`:

      [venv]
      extra_deps = ["pybind11==3.0.4"]

- One entry per module: each is one module name, two have no answer to
  what the single `.so` is called.
- Cross builds skip the bridge (the target has no matching host
  python); missing pybind11 skips it with a STATUS note rather than
  failing the build.

## Embedding the interpreter

The venv's pybind11 joins `CMAKE_PREFIX_PATH` for the whole tree, and
that interpreter is pinned as `Python_EXECUTABLE`/`Python3_EXECUTABLE`,
so a module that embeds python rather than exporting an extension
declares it like any other host dependency:

    Require(Python3 VERSION ">=3.14" SYSTEM COMPONENTS Interpreter Development.Embed)
    Require(pybind11 VERSION ">=3.0" SYSTEM)
    Link_dependencies(pybind11::embed Python3::Python)

The directories of shared libraries a SYSTEM `Require` found go on the
install rpath of every app that links them, so the installed binary
starts with nothing in the environment. Conan libraries do not: they
travel as `[runtime]` payload beside the binary.

## Default library wiring (tests / benches / pybind)

The init scaffold declares, and the conanfile derives from the same
lines:

    Require(GTest VERSION ">=1.17.0" TEST CONAN gtest
            COMPONENTS gtest gtest_main gmock gmock_main)
    Require(benchmark VERSION ">=1.9.0" BENCH CONAN benchmark)

`<module>-tests` links the gtest/gmock components (mains included, so
suites don't write their own `main`); `<module>-benches` links
google-benchmark's `benchmark_main`. TEST/BENCH deps only enter the
graph when tests/benches build, and `Link_dependencies(TEST <module>)`
adds module libraries to the test executable alone.

## The conan remote — a private artifactory, upload by default

buildutil registers the project's conan remote automatically from one
env seam — `CONAN_REMOTE_{NAME,URL,USER,PASS}`, fed by a repo-root
`.env` (gitignored) or CI variables. Under the default name the remote
REPLACES conancenter, so every resolve and upload speaks to the
private mirror (which proxies/caches upstream).

After a successful build, buildutil **uploads the built dependency
binaries to that remote by default**. This is deliberate and desired:
the remote is the fleet's binary cache, and the upload is what makes
the next clean build — any machine, any CI job — fetch prebuilt
dependencies instead of compiling them from source. The switch that
skips it is named `--skip-dependency-upload-so-everyone-rebuilds-from-source` so that it
is never passed casually; it prints a loud warning. With no remote URL configured, everything still works —
resolution degrades to source builds and upload is skipped with a
one-line note.

## Shipping the project as a conan package

A project opts in through `[package]` in buildutil.toml (`kind =
"library" | "application"`, `name`) — written by init's packaging
wizard (`buildutil init`, or `--package=library|application` /
`--no-package` non-interactively; re-running init on an old project is
the upgrade path, regenerating a pre-packaging conanfile with the
original kept as conanfile.py.bak). The scaffold includes a
`test_package/` that consumes the CACHED package like a real consumer.

**The version never lives in the package source.** publish derives it:
base = the last git tag that is a valid semver, plus a build number
bumped on every successful publish (state in _bdudata; a new tag
resets it). `--no-version-autoincrement` republishes the current
number, `--version X` overrides outright, and with no tag and no
terminal to prompt at, publish refuses. Tag the repo — that is the
durable answer.

- An unqualified `buildutil test` also runs the package test:
  export-pkg of the just-built tree, then test_package/ against it.
- `buildutil publish` = build → export-pkg → package test → upload
  recipe + binaries to the project remote (an unconfigured remote is
  an error; `--no-upload` is the local dry-run).
- The recipe packages the LOCAL buildutil-built tree (the install
  mirror, whole); cache source-builds by consumers are refused with an
  explanation — binaries come from the remote that publish populates.
- **Pin runtime deps exactly in a packaged project.** A version-ranged
  `Require` re-resolves in every consumer's cache; drift from the
  publish-time resolution changes the package_id and the published
  binary is never found. publish refuses ranged runtime Requires
  (`--allow-version-ranges` overrides). TEST/BENCH/TOOL/SYSTEM deps
  may keep ranges — they never reach a consumer.
- A dep whose headers your exported headers include must be declared
  `Require(... PUBLIC)` — that maps to conan's `transitive_headers`,
  putting the dep's headers on every consumer's include path. The
  scaffolded `test_package` smoke should include such a header, or it
  proves nothing about API propagation.

## Build-time configuration options

`buildutil config` lists them; `buildutil config NAME=VALUE` sets
(validated against the option's value set); `--reset` forgets. Values
persist in `_bdudata/config.ini` (checkout-local) and resolve env
(`BUILDUTIL_OPT_<NAME>`) > saved > default.

`module_linkage` (static | shared): how UNTAGGED modules build. Shared
mode leaf-names and mirror-installs the libraries, computes
$ORIGIN-relative install rpaths for every app over its link closure,
carries a `-shared` axis in the profile dir (static and shared never
share a build tree), and on a `[package] kind = library` project maps
to conan's standard `shared=True` option (distinct package_ids under
one recipe). Visibility stays hidden — annotate the symbols that are
ABI, or declare `Init_submodule(PUBLISH_SYMBOLS)`.

## Defines discipline

A value that never varies with the build configuration belongs in a
header, not in a build script — do not put defines in CMakeLists. The
machinery itself contributes `<PREFIX>_<NAME>_ENABLED=1` for every
live module, so presence checks (`#if OW_MSTOOLS_ENABLED`) need no
declarations at all.
