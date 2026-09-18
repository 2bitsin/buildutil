# Compiler lanes and cross builds

`--compiler` is the only toolchain selector there is, and the values are
`gcc`, `clang`, `apple-clang`, `msvc`, `wine-msvc`, `osxcross` and
`emscripten`. There is no conan profile to pick and none to write: a
profile is derived from the lane, the build type and the project's
`[conan]` policy, named after what it contains, and rewritten on every
command, because a stale profile is how every "why is this build using
Make" question starts. A named compiler that is not installed is a hard
error naming what is available, so a typo fails loudly rather than
building with the wrong thing.

## Selection

With no `--compiler`, the lane is the first of `gcc`, `clang`,
`apple-clang`, `msvc`, `wine-msvc`, `osxcross` that is present —
`emscripten` is deliberately absent from that order, because installing
an SDK is not a request to build for the browser. `analyze` is the one
verb whose default is `clang` rather than the host's compiler, since
clang-tidy is a clang frontend and a gcc compile database carries flags
clang's driver rejects.

Candidate scanning probes `-dumpversion` and takes the **highest major**
rather than trusting the name, so a container that side-installs a trunk
compiler beside the distribution's is resolved correctly.

Profiles are named `arch-os-compiler[-linkage]-buildtype` — for example
`x86_64-linux-gcc-release` or `wasm-emscripten-clang-debug` — and live in
`_profiles/`, with the matching build tree at `_build/<same name>`. The
linkage segment only appears when it is not `static`, and it sits before
the build type so anything stripping the last segment keeps working.

Every cross lane writes **two** profiles from one derivation: the host
profile targets the requested platform while build tools use a shared
`cross-build-linux` profile with the native gcc — or, where the container
has no gcc at all, a deliberately compiler-less three-line settings
block, because probing a missing gcc there is what broke the wine build.
Packaging pairs profiles the same way the build did, by construction.

## Version caps and the C++ standard

A newer compiler builds correctly; buildutil simply does not advertise a
version conan would reject, so the version in a profile is capped at gcc
15, clang 20, apple-clang 16 and msvc 196. The standard follows from the
lane: MSVC gets 23, because conan caps it there; gcc and clang below
major 14 get 23 and everything else gets 26. On MSVC the machinery
additionally applies `/std:c++latest`, `/bigobj`, a raised constexpr
budget and `/utf-8` **after** cmake's own `/std` flag, so last-wins
promotes past the profile's `/std:c++23`.

## Linux, native

gcc and clang are the exercised lanes. Clang additionally gets
`--gcc-install-dir` pinned so it resolves the same libstdc++ the gcc lane
uses. `BUILDUTIL_GC_SECTIONS` and the coverage instrumentation are
gcc/clang/apple-clang only and are a hard error elsewhere.

## Emscripten (Linux to WebAssembly)

The lane is explicitly selected and never inferred:

```console
buildutil build --compiler emscripten --release --no-tests
```

The SDK is found at `$EMSDK/upstream/emscripten/em++`, else `em++` on
`PATH`, and it must carry both `emcc` and its `Emscripten.cmake` or the
lane refuses by name. The LLVM major comes from `em++ --version`,
falling back to the verbose banner, and never from the emsdk release
number. The profile is `arch=wasm`, `os=Emscripten`, clang, libc++,
C++26, and it hands conan absolute `emcc` and `em++` paths plus emsdk's
`Emscripten.cmake` as the user toolchain; native clang's gcc-oriented
flags never enter it.

The target selects `.emscripten` files and directories and is a member of
`posix` — musl libc, a POSIX-shaped filesystem — and never of `apple` or
`native`, so a `.posix` source builds and a `.native` one does not. The
SDK defaults executables to `.js`; buildutil sets application targets to
`.html` and installs the accompanying `.js` and `.wasm` at the normal
mirrored location, so `buildutil init --name demo` produces
`_install/demo/hello.html` with its two siblings. Serve that directory
over HTTP to open it; `buildutil run` is not a browser launcher.
Target-only dependency policy belongs in `[conan] options_emscripten`.

## wine-msvc (Linux to Windows/x86_64)

The genuine MSVC toolchain, run under Wine. **A `cl` on PATH claims this
lane only when it really is the msvc-wine wrapper** — `msvcenv.sh` beside
it, or a wine exec inside it — so an unrelated `cl` (OpenCL, Common Lisp)
cannot silently turn a native build into a Windows cross build;
`BUILDUTIL_WINE_MSVC=1` claims the lane anyway and `0` suppresses it, and
the driver prints the lane when it takes it.

The profile is `arch=x86_64`, `os=Windows`, `compiler=msvc`, cppstd 23,
dynamic runtime. Two details are load-bearing: the debug information
format is `Embedded` — `/Z7`, no PDB and therefore no `mspdbsrv` hang —
and `tools.build:jobs` is pinned to 6, because each `cl` runs a full wine
context and conan's default of one job per core races the compiler into
internal compiler errors. `CMAKE_MT` is stubbed out and
`CMAKE_CROSSCOMPILING_EMULATOR=wine` is set, so ctest runs the produced
binaries. The reflect generator resolves its include paths from
`msvcenv.sh` beside the wrapper.

## osxcross (Linux to Darwin/arm64)

Selected by `oa64-clang++` on a Linux `PATH`, with apple-clang settings
and the SDK path taken from `osxcross-conf` — otherwise conan's toolchain
shells out to `xcrun`, which does not exist on Linux. The Objective-C++
compiler is named absolutely in the profile, because OBJCXX is enabled
after `project()` and cmake refuses a bare name for a language enabled
that late. The archiver is published through **both** seams, a cmake
variable and `[buildenv]`, because cmake-shaped dependencies read the
first and autotools-shaped ones read the second; half an archiver is
refused with a loud warning rather than half-used. Link flags select
`lld`, since osxcross's ld64 does not synthesise clang's
`_objc_msgSend$sel` stubs. It is a build-only lane: no emulator exists to
run Mach-O binaries on Linux.

## Windows, native

`cl` is found through `$VCVARS_PATH` or a short list of standard
`vcvars64.bat` locations, and the environment it prints is decoded
leniently, because Visual Studio injects non-ASCII bytes into `LIB` and
`INCLUDE` that a strict decode trips over. The MSVC version prefers
`$VCToolsVersion` and maps the toolset to conan's spelling, because
recipes like boost's b2 re-invoke `vcvars.bat <major>.<minor>` from
`compiler.version` and the value must name a toolset directory that
exists on disk. Ninja is the generator on Windows too, never the Visual
Studio one.

Every path-valued `-D` reaches cmake with forward slashes. That is not
cosmetic: `try_compile` copies the inherited module path into its scratch
project as a quoted `set()`, where a backslash starts an escape and a
drive letter after one is not a valid escape, and the compiler-ABI probe
then fails so no Windows build gets past configure. UTF-8 is handled in
three separate places — `/utf-8` on MSVC compiles, the driver's own
streams reconfigured on Windows, and `-X utf8` inserted into the venv
re-exec, since it cannot be turned on from inside a running interpreter.

## macOS, native

apple-clang, `libc++`, and the host architecture; a native (non-Rosetta)
python is what makes the arm64 mapping report itself correctly. The
Objective-C story and the application bundle story are in
[platform-apps.md](platform-apps.md).

## Open Watcom firmware

The `watcom` extension — `buildutil extend watcom` — links a module's
`*.asm` and `*.c` through `wcc` and `wlink` into one raw 16-bit ROM
image, surfaced as a `constexpr` byte array in a generated header and
published as an INTERFACE library. `Init_firmware([LIBRARY] [OPROM] [BASE
<hex segment>] [ORG <hex offset>])` is the call; the defaults are a
`F000` base for a system image and `C800` for an option ROM. It is for
emulator projects that must assemble the firmware they emulate.

## ccache

Presence-driven: no config key and no flag. When `ccache` is on `PATH` it
is put in front of C, C++, Objective-C and Objective-C++ compiles.
Objective-C is included even though cmake ignores a launcher for a
language never enabled, because omitting it meant the one platform with
`.m` sources compiled them uncached and silently. The wine-msvc lane is
excluded, since ccache under a wine `cl` is unverified; osxcross is
included but sets `CCACHE_COMPILERCHECK=content`, because the
`oa64-clang` wrappers are thin shims and ccache's default mtime check
would hash the shim rather than what it execs. When ccache is absent or
the lane is gated out the launchers are explicitly **emptied** rather
than left unset, because `COMPILER_LAUNCHER` is a cache variable and a
tree once configured with ccache would otherwise keep exec'ing a dead
launcher after an image upgrade.

One thing ccache cannot see on its own: its direct mode hashes a source
and its `#include`s, not what `#embed` pulls in, so an edited resource
used to produce a byte-identical binary. The resource generator therefore
writes a digest of every embedded file into the translation unit as a
`constexpr` — a form the preprocessor keeps, unlike a comment.

## What is not here

There is no mingw lane, no `clang-cl`, no Android, no iOS, no universal
binaries and no `sccache`. Code signing, entitlements and notarisation
are absent entirely: a bundle is assembled, and signing it is the
project's own step. The support matrix in the [README](../README.md)
says which of the lanes above are exercised by the test suite and which
are used by the author but not covered.
