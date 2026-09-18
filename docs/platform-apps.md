# Platform applications — Objective-C, macOS bundles, python

Three things a cross-platform C++ project runs into the moment it grows a
user interface or an embedding, and all three are shapes rather than
logic: a macOS GUI cannot avoid Objective-C++, a macOS application is a
directory rather than a binary, and a python extension is named for what
its entry file defines. None of them is worth a project's own cmake.

## Objective-C and Objective-C++

An `NSApplication` subclass is not expressible in C++, and anything
embedding a browser needs one, so `.mm` and `.m` are ordinary module
sources: globbed by `Init_submodule()` alongside `*.cpp` and classified
by the same tags, so `platform.macos.mm` builds on macOS only and
`foo.test.mm` is a test translation unit. `OBJCXX` and `OBJC` are enabled
at root scope by presence — a tree with no `.mm` never pays for the
compiler probe, and a tree with one never says a word about it. A `.h`
next to a `.m` is found the ordinary way and ships, or stays private
under a leading underscore, by the same layout rule as every other
header.

ARC is the default: every Objective-C and Objective-C++ translation unit
compiles with `-fobjc-arc`, and a module holding manual-retain code opts
out. Apple frameworks are declared per module rather than linked by hand,
and the declaration is inert off macOS, so a cross-platform module
declares it once:

```toml
[modules.legacygui]
objc_arc = false

[modules.gui]
frameworks = ["Cocoa", "Foundation"]
```

C++-only flags are language-guarded, so a `.m` — which is C — gets no
"argument unused during compilation" noise.

**"Apple" means the target, never the host.** cmake sets `APPLE` from
`CMAKE_SYSTEM_NAME` after `project()`, so a cross-build from Linux to
Darwin is Apple here and picks up the `.mm`; a native Linux build never
does, and the files are ignored without a warning.

Two silent traps this closes. First, `.mm` is *also* in cmake's C++
source extensions, so a `.mm` compiled without `OBJCXX` enabled is built
as **C++** — which happens to work under clang and stops working under
anything else. Second, cmake does not derive the Objective-C++ compiler
from the C++ one: a language enabled after `project()` gets its own
compiler search, and on a cross build that search finds the **host**
`c++`, so the `.mm` compiles, links against the wrong runtime, and the
build has quietly stopped being a cross build. buildutil defaults both
Objective-C compilers to the C and C++ ones it was given before enabling
the languages, and the cross lane's profile names the Objective-C++
compiler outright and absolutely.

Two tools deliberately do not see Objective-C: `buildutil analyze` globs
`*.cpp` only, so clang-tidy is never handed a translation unit it would
need an Apple SDK to parse, and `buildutil coverage` treats `*.test.mm`
and `*.test.m` as scaffolding exactly as it treats `*.test.cpp`. ccache
covers all four languages.

## macOS application bundles

On macOS an application is a directory, and a framework loaded at run
time is found at `../Frameworks` relative to the executable *inside* it.
A project whose dependency ships one has no choice about the shape, so
the shape is not what it should be writing:

```toml
[bundle.macos]
module = "xoctet"               # optional; default: the project name
name = "xoctet"                 # optional; CFBundleName, default: the module
identifier = "com.example.app"
version = "0.1.0"
plist = { LSUIElement = true, LSMinimumSystemVersion = "12.0" }

[bundle.macos.helpers]          # sub-process bundles in Contents/Frameworks
module = "xoctet-helper"        # ONE executable, run under several names
variants = ["", "Alerts", "GPU", "Plugin", "Renderer"]
```

That produces `<name>.app` with a generated `Info.plist`, installs it at
the module's mirror, and assembles one helper bundle per variant —
`<CFBundleName> Helper[ (<variant>)].app`, identifier
`<identifier>.helper[.<variant>]`, each with its own plist and a copy of
the one helper executable, because the framework picks the bundle by name
and the program inside is identical. `suffix` overrides the `" Helper"`
in the middle, and an empty-string variant is the unsuffixed helper.
Declare the helper module macOS-only by tagging its directory
(`sources/xoctet-helper.macos/`), and its framework arrives through
`[runtime] from` on both platforms.

The `Info.plist` is written by python rather than configured from a text
template the project would have to carry, because `plist` is arbitrary
TOML with real types and a text template cannot tell `true` the boolean
from `"true"` the string. Everything a bundle needs regardless — the
package type, the principal class, the Dock-hiding on helpers — is filled
in, and a `plist` key overrides it. Helper names and identifiers are
computed in python rather than in cmake, since the unsuffixed variant is
an empty string and that cannot survive a cmake list.

One thing genuinely had to change in the machinery for this.
`Init_submodule` emits an install rule for the application, and cmake
refuses at *generate* time to install a bundle target through a rule with
no bundle destination — so a project could not simply set the property
itself, and every consumer assembled the bundle out of post-build copies
instead, which then cannot use the package's own framework-copying
function, since that needs a real bundle target to hang its generator
expression off. The install rule names both destinations and the knot
unties. A dependency's dylibs and frameworks go into
`Contents/Frameworks` inside the bundle rather than beside it; see
[packaging.md](packaging.md).

`buildutil run` knows about this: on macOS, when the artifact is
`<binary>.app`, it launches with `open -W -n` rather than exec'ing the
file inside — which is not the same thing, since the plist is what names
the sub-process bundles and `LSUIElement` only applies to a launched app.
Code signing, entitlements and notarisation are not buildutil's: a bundle
is assembled, and signing it is the project's own step.

## Python: an extension, and an interpreter inside the app

A `*.pybind.cpp` in a module is a **python extension**, by presence and
nothing else. The module builds as usual, the entry file builds as a
second artifact linking it, and it installs to `lib/python`.

The extension is named for the module the entry *defines* — the name in
its `PYBIND11_MODULE(...)`, which is the file's own stem — with python's
SOABI suffix, so `sources/tash/python/tash.pybind.cpp` ships
`tash.cpython-314-x86_64-linux-gnu.so`, importable as `tash`. The cmake
target name is a different thing derived from the directory, and a file
named after *it* is one no `import` statement can reach. A module holds
at most one entry: each is a module name, and two of them in one
directory have no answer to what the single shared object is called. The
bridge is skipped on a cross build and when pybind11 is absent.

pybind11 comes from the interpreter the driver runs under — the project
venv through `[venv] extra_deps`, the image python under
`BUILDUTIL_SYSTEM`, a consumer's conan python in a cache build — and is
located once for the whole tree. Its cmake package joins the prefix path
at root scope, and that same interpreter is pinned as the python
executable so pybind11 binds against it rather than against whatever
`python3` comes first on `PATH`.

A module that **embeds** the interpreter instead of exporting an
extension declares it like any other host dependency and links it:

```cmake
# sources/CMakeLists.txt
Require(Python3 VERSION ">=3.14" SYSTEM COMPONENTS Interpreter Development.Embed)
Require(pybind11 VERSION ">=3.0" SYSTEM)

# the module
Link_dependencies(pybind11::embed Python3::Python)
```

An installed binary finds the host libraries it links, because the
directories of the shared libraries a `Require(... SYSTEM)` found go on
the application's install rpath — libpython lives wherever the image put
it, and that is not the loader's default path. Only `SYSTEM`
dependencies: a conan library travels as `[runtime]` payload beside the
binary, and its cache path baked into an rpath would let an artifact that
ships no payload still start on the machine that built it, and nowhere
else.

Measuring a bridge from its python side needs one more line, `[coverage]
bridge_dirs`; see [testing.md](testing.md).
