# buildutil

Build scripts? Where buildutil is going, we don't need any build scripts.
The directory structure **is** the build script: a directory under
`sources/` holding a `CMakeLists.txt` is a module, its name says what it
builds and where it ships, a file called `hello.test.cpp` is a test, and
a directory called `ui.embed/` is a set of resources compiled into the
binary. `ls -l` and `find .` tell you most of what there is to know about
a project's build, which is the whole property being bought.

The other half is not having to think about it. buildutil does as much as
it can automatically, with reasonable defaults the whole way down, and
asks the project for the minimum a directory listing cannot state — it
does the ugly part so the project does not have to. What it is, under
that: one command line joining conan, cmake, ninja and a handful of
toolchains across several platforms, plus ctest, GoogleTest, google
benchmark and pytest, so that tests and benchmarks are standard rather
than per-project; plus fill-ins for what the compilers still lack, of
which reflection is the large one. Where something genuinely has to be
scripted, it is scripted in a real language — python, not half-baked
shell.

It began as one C++ project's in-tree driver and was extracted so any
project can carry it.

## Requirements

Python 3.11 or newer, `git`, CMake 3.25 or newer, `ninja`, and a C++20
compiler — gcc 12 or newer, clang 16 or newer, MSVC 2022, or
apple-clang. Ninja is a hard requirement rather than a fallback: every
build uses it.

conan, typer, gcovr and pytest are **not** things to install. buildutil
declares no install-time dependencies of its own, so a machine carrying
only the driver does not drag conan in; instead the first command in a
project builds a `_pyvenv/` beside it and pip-installs the pinned
toolchain, plus whatever the project declares in `[venv] extra_deps`. A
container image that already carries the toolchain sets
`BUILDUTIL_SYSTEM=1` and no per-checkout venv is ever built.

The `reflect` extension additionally needs a real clang on `PATH` (or
`BUILDUTIL_RESOURCE_DIR`), because the bindings' wheel ships the library
without clang's builtin headers.

## Install

```console
$ pip install git+https://github.com/2bitsin/buildutil
```

The public `main` only ever holds releases, so that line installs the
latest one; append `@vX.Y.Z` to pin a particular release. That puts the
driver on `PATH` for every project on the machine.

The other way is to vendor it. `buildutil install` copies the package
into `<repo>/.buildutil/` and writes a `./buildutil` launcher, both
committed, so a fresh clone of the project needs nothing but python — the
tool travels with the repository and `./buildutil build` is the whole
interface. The launcher prefers the vendored copy over anything installed
on the machine, the project venv over `PATH`, and `python3` only to
create that venv. In an untouched directory, `install` also runs `init`.

## Quick start

```console
$ mkdir demo && cd demo
$ buildutil init --name demo
wrote buildutil.toml (name=demo, cmake=DEMO, modules=DEMO)
  CMakeLists.txt written
  conanfile.py written
  .gitignore written
  sources/CMakeLists.txt written
  sources/demo/hello/CMakeLists.txt written
  sources/demo/hello/hello.bench.cpp written
  sources/demo/hello/hello.cpp written
  sources/demo/hello/hello.hpp written
  sources/demo/hello/hello.test.cpp written
  sources/demo/hello/main.cpp written
  ./buildutil launcher written — `./buildutil build` works, fresh clone included
cmake machinery renders at /tmp/demo/_bdudata/cmake (gitignored, derived)
extensions on request: buildutil extend [reflect|watcom]
next: ./buildutil build    (first run bootstraps _pyvenv)
      ./buildutil test
      ./buildutil bench    (benches build behind --include-bench)
```

That is the whole project, and it is worth looking at before building it:

```console
$ find . -not -path './_*'
.
./.gitignore
./CMakeLists.txt
./buildutil
./buildutil.toml
./conanfile.py
./sources
./sources/CMakeLists.txt
./sources/demo
./sources/demo/hello
./sources/demo/hello/CMakeLists.txt
./sources/demo/hello/hello.bench.cpp
./sources/demo/hello/hello.cpp
./sources/demo/hello/hello.hpp
./sources/demo/hello/hello.test.cpp
./sources/demo/hello/main.cpp
```

The root `CMakeLists.txt` is four lines and never grows;
`sources/CMakeLists.txt` holds the project's dependency declarations and
one `Scan_subdirectories()`; `sources/demo/hello/CMakeLists.txt` is a
single `Init_submodule()`. Everything else about that module — a library
from `hello.cpp`, an executable from `main.cpp`, a GoogleTest suite from
`hello.test.cpp`, a benchmark from `hello.bench.cpp` — is read off the
file names.

```console
$ buildutil build
...conan resolves the graph and cmake configures...
[2/8] Building CXX object sources/demo/hello/CMakeFiles/demo-hello-lib.dir/hello.cpp.o
[3/8] Linking CXX static library sources/demo/hello/libdemo-hello-lib.a
[4/8] Building CXX object sources/demo/hello/CMakeFiles/demo-hello.dir/main.cpp.o
[5/8] Linking CXX executable bin/hello
[6/8] Building CXX object sources/demo/hello/CMakeFiles/demo-hello-tests.dir/hello.test.cpp.o
[7/8] Linking CXX executable sources/demo/hello/demo-hello-tests
...cmake installs into _install/...
profile: x86_64-linux-gcc-debug
```

```console
$ buildutil test
    Start 2: buildutil-driver-guard
1/2 Test #2: buildutil-driver-guard ...........   Passed    0.01 sec
    Start 1: Hello.Greeting
2/2 Test #1: Hello.Greeting ...................   Passed    0.00 sec

100% tests passed out of 2

════════════ buildutil: OK — 100% tests passed out of 2 ════════════
```

The guard test is buildutil's own: every registered test requires a
fixture that refuses a bare `ctest`, because a tool driven by hand skips
the venv, the profile, the machinery and the install mirror.

A second module is a directory. Nothing is registered, listed or
declared anywhere:

```console
$ mkdir sources/demo/greeting
$ printf 'Init_submodule()\n' > sources/demo/greeting/CMakeLists.txt
```

Write `greeting.hpp`, `greeting.cpp` and `greeting.test.cpp` in it, and
the module, its library, its headers' install path and its GoogleTest
suite all exist — the suite under its own ctest label, so
`buildutil test demo-greeting` runs exactly it:

```console
$ buildutil test
100% tests passed out of 3

Label Time Summary:
demo-greeting    =   0.00 sec*proc (1 test)
demo-hello       =   0.00 sec*proc (1 test)

Total Test time (real) =   0.01 sec

════════════ buildutil: OK — 100% tests passed out of 3 ════════════
```

A third-party dependency is declared once, in `sources/CMakeLists.txt`,
where cmake's `find_package` and the conan recipe both read it:

```cmake
Require(fmt VERSION ">=10.0.0" CONAN fmt)
```

and the module that uses it links it, next to the sibling module it also
uses:

```cmake
# sources/demo/greeting/CMakeLists.txt
Init_submodule()
Link_dependencies(fmt::fmt)

# sources/demo/hello/CMakeLists.txt
Init_submodule()
Link_dependencies(demo-greeting)
```

```console
$ buildutil run
hello, demo, and goodbye
```

`buildutil run` builds, installs and execs the binary `[run] default`
names. The install tree mirrors the source tree —
`_install/demo/hello` for `sources/demo/hello/` — so what ships is moved
by moving directories, not by writing a destination anywhere.

## The feature tour

**The tree is the declaration.** Kind tags on a directory
(`parser.lib/`, `dip.so/`, `wd.exe/`), platform tags on a directory, a
file or a data set (`host.linux/`, `decode.win32.cpp`,
`ui.embed.macos/`), suffix directories for tests, benches, embedded
resources, shipped data and patches, headers that export by layout, and
modules that go dormant by project policy or by target. One vocabulary,
read the same way everywhere, with a refusal wherever two things claim
one name. → [docs/layout.md](docs/layout.md)

**One command line.** `build`, `test`, `bench`, `coverage`, `run`,
`analyze`, `publish`, `godbolt`, `vscode`, `module`, `config`, `clean`,
and the pre-project verbs `init`, `install`, `update` and `setup-skill`
that run before any venv exists. Global flags cover parallelism, error
caps, log capture, jumping to the first error and a wall-clock watchdog
that exists so a suite which quietly went from four minutes to forty
cannot read like one that was always forty. → [docs/commands.md](docs/commands.md)

**One configuration file.** `buildutil.toml` at the repo root carries
the handful of facts a directory listing cannot state, and the repo root
*is* the nearest ancestor holding it. An empty file is a valid one. →
[docs/config.md](docs/config.md)

**Toolchains and cross builds.** gcc and clang natively, MSVC on
Windows, apple-clang on macOS, and from a Linux host: WebAssembly
through emsdk, Windows through the genuine MSVC toolchain under Wine, and
macOS through osxcross. Profiles are derived and rewritten rather than
written by hand, and ccache is used when it is there. →
[docs/toolchains.md](docs/toolchains.md)

**Tests and benchmarks are standard.** A `*.test.cpp` is a GoogleTest
translation unit, a `*.bench.cpp` a google benchmark, a `*.test.py` a
pytest suite, all registered with ctest, all filterable by module.
Coverage runs the same suites under instrumentation and gates per file as
well as overall; `analyze` runs clang-tidy over the real compile
database. Skips are treated as defects unless someone said otherwise, by
reason. → [docs/testing.md](docs/testing.md)

**Shipping as a conan package.** Two lines of declaration; the recipe
discovers its libraries, headers and binaries from what the build
produced. The version is derived from the last git tag plus a build
number that moves only on a successful upload, and `publish
--bake-buildutil` ships the driver inside the package so a consumer's
`--build=missing` genuinely works. → [docs/packaging.md](docs/packaging.md)

**Resources, data and generated sources.** `*.embed/` compiles files
into the binary behind one generated accessor, `*.install/` ships them
beside it, and the generated shadow tree is read exactly like a source
tree, so a module's `configure.py` can produce either. →
[docs/resources.md](docs/resources.md)

**Reflection, for compilers that have none.** A type opts in with one
friend declaration inside itself; buildutil parses the header with
libclang and delivers the schemes through a generated detour header at
the same include spelling, so a transitively reached type cannot be
missing them — and refuses, at configure time, any include spelling that
would bypass it. → [docs/reflect.md](docs/reflect.md)

**Platform applications.** Objective-C and Objective-C++ as ordinary
module sources with ARC and declared frameworks, macOS `.app` bundles
with their helper bundles generated from a declaration, and python
extensions by the presence of a `*.pybind.cpp`. →
[docs/platform-apps.md](docs/platform-apps.md)

**Build options that are not constants.** A project declares its
options with their defaults; `--option name=value` chooses another for
one build; the choice reaches every translation unit as a macro through
a force-included generated header, and the profile line says what was
asked for. → [docs/options.md](docs/options.md)

**Editors and agents.** `buildutil vscode` regenerates tasks, launch
configurations and IntelliSense from the live module inventory, and
`buildutil setup-skill` installs a skill that teaches a coding agent the
same conventions. → [docs/agents.md](docs/agents.md)

## Platform and toolchain status

Where each lane honestly stands today. This is a snapshot, not a
promise — it is updated as lanes gain coverage.

| host → target | compiler | build | test | package | in the suite |
|---|---|---|---|---|---|
| Linux → Linux | `gcc` | ✅ | ✅ | ✅ | ✅ |
| Linux → Linux | `clang` | ✅ | ✅ | ✅ | 🟡 |
| Linux → WebAssembly | `emscripten` | ✅ | ❌ | 🟡 | ✅ |
| Linux → Windows/x86_64 | `wine-msvc` | 🟡 | 🟡 | 🟡 | 🟡 |
| Linux → macOS/arm64 | `osxcross` | 🟡 | ❌ | 🟡 | 🟡 |
| Windows → Windows/x86_64 | `msvc` | 🟡 | 🟡 | 🟡 | 🟡 |
| macOS → macOS | `apple-clang` | 🟡 | 🟡 | 🟡 | 🟡 |
| any → 16-bit ROM image | Open Watcom | 🟡 | ❌ | ❌ | ❌ |

✅ works and is exercised · 🟡 used by the author, not exercised by the
test suite · ❌ not there

The two `❌` under *test* are not defects: no emulator runs a Mach-O
binary on Linux, and a wasm module has no ctest runner. Bug reports and
patches for any of these lanes are welcome on GitHub — the 🟡 rows are
exactly where an outside report is most useful.

## Development

```console
$ python -m pytest buildutil/tests
```

The suite is self-contained: no C++ project is needed, and the seam tests
spawn fresh interpreters in temporary roots. A release is a tag whose
version matches `pyproject.toml`, and the version reported by
`buildutil --version` is that tag and nothing else.

## License

MIT — see [LICENSE](LICENSE).
