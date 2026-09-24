# Resources, data and generated sources

Three things share one idea: a directory's contents are the declaration.
`*.embed/` holds files that must be **inside** the binary, `*.install/`
holds files that must ship **beside** it, and the generated shadow tree —
where a module's `configure.py` and every build-time generator write — is
read by exactly the same rules, so a file a program produced is treated
the way a file a person typed is.

## Resources

Files that must be in the executable — a UI served over a scheme handler,
a font, a schema, a default config — with no directory to ship beside it
and no path to resolve at run time. The project says which files;
buildutil decides how they get there and hands back one accessor.

A `<name>.embed/` directory inside a module is the whole declaration. The
other form names a directory anywhere in the repo:

```toml
[resources]
dir = "resources"                     # repo-root-relative
files = ["*.html", "*.css", "*.js"]   # optional; default: every file
module = "xoctet"                     # optional; default: the project name
namespace = "xoctet"                  # optional; default: the project name
prefix = "ui/"                        # optional; prepended to every name
[resources.mime]
".xyz" = "application/x-xyz"          # extensions the built-in table lacks
```

`[[resources]]`, doubled and repeated, declares several independent sets.
Either way the module's CMakeLists stays a bare `Init_submodule()`: there
is no cmake call for this and no `#embed` to write.

A resource's **name** is its path under the declared directory,
`/`-separated on every platform, with `prefix` in front where there is
one. Two declarations producing one name is a configure error.

The generated header is `<module>/resources.hpp`, in the same
`generated/<module>/` root everything else emits into, so `#include
"resources.hpp"` works unqualified from anything that links the module:

```cpp
#include "xoctet/resources.hpp"

namespace xoctet::resources {

struct Resource {
  std::string_view name;
  std::string_view mime;             // guessed from the extension
  const unsigned char* data;         // null exactly when the file is empty
  std::size_t size;
  std::span<const std::byte> bytes() const noexcept;
};

std::span<const Resource> all() noexcept;                   // sorted by name
const Resource* find(std::string_view) noexcept;            // null if absent
std::span<const std::byte> get(std::string_view) noexcept;  // empty if absent

}
```

`find()` is the accessor that tells **absent** from **empty**, which
`get()` cannot. Nothing here touches the filesystem, so deleting the
resource directory after a build changes nothing about the binary. The
declaring library carries `cxx_std_20` as a public compile feature, so
including the header never asks a project to say anything about
standards.

**Two back ends, one header**, chosen by a compile probe and never by a
version table. Where the compiler has `#embed`, the compiler reads the
bytes and nothing large passes through cmake or the C++ parser as text;
everywhere else — MSVC included — the generator writes a `static const
unsigned char[]`, an array rather than a string literal, because MSVC
caps a string literal at 16 KB and puts no cap on an initialiser. Both
produce identical bytes, and the suite asserts that by building the same
tree twice rather than trusting it.
`BUILDUTIL_EMBED_FALLBACK=1` forces the array path on a compiler that has
`#embed` — for seeing what MSVC will do, and as a bisect handle when a
resource looks wrong. It is an environment variable and not a toml key
because it is a diagnostic, not something a project decides once. A very
large file costs real compile time on the array path; the `#embed` path
does not care.

Adding or removing a file re-runs the generator on its own, and editing
one rebuilds the translation unit that carries it — see the note on
ccache in [toolchains.md](toolchains.md) for why that last part needed
work.

The older route still works and is untouched: a `<stem>.rom.bin` in a
module still becomes a generated `resources/<stem>.rom.hpp` holding a
`constexpr std::array<std::byte, N>`. That one is a single blob under a
single symbol with no name lookup and no content type; `[resources]` is
the named-set feature, and neither knows about the other.

## Runtime data

`*.install/` is the opposite number and reads the same way, one file out,
one file in. It is a **prefix-rooted overlay**: the path inside the tree
*is* the shipped path, so `data.install/x/y/z` installs at
`<prefix>/x/y/z` and is staged at the build root the same way, and both
trees agree about data. The directory existing is the whole declaration.
It is data, not source: a `.cpp` in there is copied, never compiled.

In the build tree each staged file lands **twice**: at the build root,
which is the shape the install tree has, and under `<build>/bin/`,
where the executables are. An installed app sits at the prefix root
beside its data, so a program that resolves assets relative to its own
executable — the common shape, and the only one available to a
`dlopen`'ed library's neighbours — finds them; without the `bin/`
mirror the same program found nothing in-tree and everything after
`install`, and in-tree runs stopped matching installed ones. Data
generated by `Add_generated_source(... STAGE DATA)` is mirrored the
same way, one phase later, once the generator has written it.

## Platform overlays

Both kinds take the platform tags, and they **overlay** rather than
replace. `ui.embed/` and `locale.install/` are the base set for every
target; `ui.embed.posix/` overlays it on Linux and macOS,
`ui.embed.linux/` overlays that on Linux, and `locale.install.win32/` is
Windows-only. The merge is by name — the resource name for `*.embed/`,
the shipped path for `*.install/` — so a tagged directory *replaces* what
it names and *adds* what it does not:

```
sources/gui/ui.embed/app.js         base, every target
sources/gui/ui.embed/logo.svg       base, every target
sources/gui/ui.embed.posix/app.js   wins over the base on Linux and macOS
sources/gui/ui.embed.linux/app.js   wins over that on Linux
sources/gui/ui.embed.win32/app.js   Windows only; ignored elsewhere
```

Selection is by the **target** platform, cross builds included. The full
specificity rule, and the refusals that go with it, are in
[layout.md](layout.md).

## The shadow tree is a source tree

`<build>/generated/<module>/` is where `configure.py`,
`Add_generated_source` and the resource generator emit, and it is already
an include root and an `#embed` search path. It is also a **module
directory**: the data conventions read it exactly as they read
`sources/<module>/`.

```
generated/<m>/ui.embed/            a resource set, embedded like any other
generated/<m>/ui.embed.linux/      platform-tagged, same overlay levels
generated/<m>/locale.install/      runtime data, shipped like any other
```

Nothing declares any of it: a generator writing a file into
`generated/<m>/ui.embed/` has thereby added a resource. Hand-written and
generated files coexist in one set, and a name both claim is a configure
error naming both directories — a build where you cannot tell which of
the two you are looking at is worse than one that will not configure. A
module's `configure.py` runs at the top of `Init_submodule()`, long
before these globs, which is what makes the tree usable: whatever the
hook wrote is simply there when the conventions read the directory.

There are two generated roots and both are module directories: the
per-profile one, and a build-invariant shared one, so a payload that
cannot vary by build type is produced once rather than once per profile.
A name both roots claim at equal specificity is refused, naming both.

## A module's `configure.py`

buildutil owns no UI toolchain, no transpiler and no compressor, and it
is not going to: what a project transpiles, bundles or compresses is the
project's business. What buildutil owes is somewhere to put the output
and the means to put it there. The hook runs at configure time with
`buildutil_configure` importable:

```python
import buildutil_configure as cfg

sources = cfg.inputs('*.ts')              # globbed AND declared: editing
                                          # one re-runs this hook
tsc = cfg.tool('tsc', install='npm install -g typescript')
cfg.run([tsc, '--outDir', str(scratch), *map(str, sources)],
        what='the TypeScript type check')
cfg.emit('ui.embed/app.js', text)         # -> generated/<m>/ui.embed/app.js
cfg.emit_bytes('ui.embed/app.js.br', packed)
```

| helper | what it is for |
|---|---|
| `output_dir(shared=False)` | the module's generated root, per profile or build-invariant |
| `data_dir(name, tag=None, shared=False, keep=False)` | the shadow copy of a data directory — the one place that knows the tag goes after the suffix and that the vocabulary is closed |
| `emit(path, text)` / `emit_bytes(path, data)` | write under the root, content-diffed so unchanged output never churns a rebuild, and register it |
| `declare(path, options=None, defines=None)` | register a file written some other way, and the compile options and definitions of that one source |
| `soversion(n, version=None)` | the shared library's soname number and full version (`soversion(0, version="0.4.8")`) |
| `depends(*paths)` / `inputs(*globs)` | inputs whose change re-runs the hook; `inputs()` declares the *directories* too, so a file **added** re-runs it as well |
| `tool(name, install=...)` / `find_tool(name)` / `tool_dirs()` | an external program — declared `TOOL` requirements first, `PATH` second — or a refusal naming both places and how to get it |
| `run(argv, what=...)` | run it; on failure the tool's own output is what the build shows |
| `source_dir()` / `module_name()` / `target_system()` | where the hook is, what it configures, and the **target** platform |

**A data directory holds what this run put there.** A `*.embed/` or
`*.install/` tree is declared by its contents, so a file an earlier run
wrote and this one did not would still ship: renaming an emitted payload
used to be silently additive, and the binary carried both names until
someone wiped the build tree. Every data directory the hook emits into is
swept when the hook ends — what this run wrote stays, the rest goes, and
a directory the sweep empties goes with it. `data_dir(..., keep=True)`
opts a tree out. A hook that finds its output already up to date and
skips the write must still `declare(path)` it, or the sweep reads the
untouched file as a leftover and removes it. Nothing outside a data
directory is ever swept: a
generated header is reached by name, not by glob.

**A source's own flags travel with it.** A hook that learns how a
third-party build compiles its files hands them over per file:
`declare(path, options=['-fno-strict-aliasing'], defines=['HAVE_X',
'N=2'])` becomes that source's `COMPILE_OPTIONS` and
`COMPILE_DEFINITIONS`, so a changed flag reconfigures and recompiles
exactly the files it belongs to. A later `declare()` that passes
`options` or `defines` replaces them; one that passes neither keeps
them. A checked-in source of the module may be declared the same way:
it stays a source, compiled once, with the declared flags. A module hook
flags its module's own sources (not a nested module's) and the files its
own and its groups' hooks generate; a group hook flags the files it
generates; one file flagged by two hooks is refused naming both. A flag
holding `;`, a tab or a newline, an option holding a space (pass each
argument on its own, or spell the group `SHELL:-include x.h`), and a
define that is not `NAME` or `NAME=value`, are refused.

A declared tool is reachable from the hook. A `Require(<pkg> VERSION "…"
TOOL)` package's bindir lands on cmake's program path — a cmake
*variable*, which no subprocess inherits — so a hook used to find
whatever the machine happened to have, or nothing at all. It is handed
over now and searched before `PATH`: a project that pinned a version
means the pinned one, and that is the whole difference between a build
input and a fact about somebody's laptop. The `PATH` fallback stays,
because most of what a hook runs is not a conan package and never will
be.

A payload that is *stored* compressed says so in its **name** —
`app.js.br` — and the consumer's scheme handler reads that. buildutil has
no encoding field and no compression knob, because neither would be a
fact about the build.

## Generators that are not hooks

`Add_generated_source(OUTPUT <rel> SCRIPT <path> ...)` registers a
build-time generator instead of a configure-time one. The output path
must be relative, the stage defaults to `BUILD` (with `TEST`, `BENCH` and
`DATA` as the alternatives), and the script is invoked as `python
<script> --output <abs> <ARGS>` with the repo root as the working
directory and the module's generated directory in the environment. An
`ARGS` entry naming an existing file is promoted to a dependency
automatically, and a `$<TARGET_FILE:...>` in `ARGS` is restated as an
explicit dependency, so the ordering a reader assumes is the ordering
that holds.

Patches are filesystem-driven too: a sibling `<file>.patch`, or a
`<name>.patch/` directory mirroring the tree it patches, produces a
patched copy under the generated root, and `Resolve_generated_source()`
hands back the patched copy where one was discovered and the in-tree
source where none was.
