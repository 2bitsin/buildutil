# Tests, benches, coverage, run and analyze

A test is a file in a place, not a declaration. Every `*.test.cpp`, and
everything under a `*.test/` subtree, becomes one `<module>-tests`
executable linked against GoogleTest; every `*.bench.cpp` and `*.bench/`
subtree becomes `<module>-benches` against google-benchmark; a
`*.test.py` file or a `.py` under a `*.test/` directory becomes a pytest
suite. Nothing is listed anywhere, and the same names drive ctest
filtering, coverage attribution and the editor's task list.

## `buildutil test`

The verb builds incrementally, runs ctest, and then runs the project's
python suites. Its build type is unique among the verbs: with neither
`--release` nor `--debug` it follows whatever `build` last acted on, and
falls back to Release on a tree that was never built.

The ctest invocation is:

```
ctest --test-dir <build_dir> [--output-on-failure] --no-tests=error
      --timeout <T> [-L ^(mod1|mod2)$] [-R <FILTER>] [--parallel <N>]
```

`--no-tests=error` is unconditional: a filter that matches nothing, or
discovery that broke, is a failure rather than a silent zero. Positional
targets become ctest **labels**, not names — `buildutil test widget` runs
everything the module `widget` owns — while `--filter`/`-f` is a regex
over test *names*. `--timeout` is ctest's per-test wall clock and
defaults to 60 seconds, `--parallel` opts into parallel execution (serial
is the default) with `--jobs`, `--quiet` drops `--output-on-failure`, and
`--no-build` runs what is already there, refusing when there is no test
tree to run. `--no-native` runs only the python suites and `--no-pytest`
only the native ones.

`-O`/`--test-option` passes an option through to pytest: `name` becomes
`--name`, `name:value` becomes `--name=value`, and a **one-character**
name becomes a short option, so `-O "m:not guest"` becomes `-m 'not
guest'`. That rule is load-bearing, because pytest registers `-k` and
`-m` with no long form and `--k=expr` would be taken as a path.

The command ends in one banner line, which is the thing to read:

```
════════════ buildutil: OK — 100% tests passed out of 3 ════════════
```

and the failing shapes are `BUILD FAILED`, `TESTS FAILED` with ctest's
own summary, `PYTEST FAILED` and `PACKAGE TEST FAILED`. An unqualified
`buildutil test` — no targets, no filter, building — also runs the conan
package test when the project is packaged, resolving the version without
consuming a build number and without prompting.

## How a test target is put together

A module's suite links the module's library and `GTest::gtest_main`, and
gets the module's own directory restated as a private include root, since
a translation unit under `*.test/` is not next to its headers. Where the
module also has an executable, the suite depends on it, so an end-to-end
suite can never run a stale binary and pass.

Registration is `gtest_discover_tests`, never `add_test`, with three
details worth knowing. The label is the module target, which is what
makes `ctest -L` and `buildutil test <module>` work. A working directory
is deliberately **not** passed to `gtest_discover_tests` — that option
sets the discovery directory too, and recent CMake writes a discovery
JSON file there, which landed in module source directories, got exported
into conan packages and produced a second recipe revision that hid the
binaries built elsewhere. Instead the working directory is applied at
ctest time through a generated include file. And every registered test
requires a setup fixture that runs the driver guard, so a bare `ctest` is
refused.

Every test process gets `<build>/bin` prepended to `PATH` and the module
directory as its working directory — as **test properties**, not runner
flags, so `BUILDUTIL=1 ctest` in the build tree behaves exactly like
`buildutil test`. A contract only the driver honours breaks the moment
someone runs ctest, which is the first thing anyone does when a test
fails.

## Python suites

There are three paths and they do not overlap. A **module-local** suite
is found by presence: `*.test.py` beside the sources, or any `.py` under
a `*.test/` directory. A **non-module** suite is declared, because
nothing else would find it:

```toml
[test]
python = ["tools", "examples/columns"]
```

and `qa/py` becomes the ctest entry `qa-py-pytest`. A declared path that
is not a directory, or a directory holding no test file at all, fails the
configure by name — the failure being replaced is a suite that silently
runs nothing. The third path is buildutil's own: `tools/*/pytest.ini`
suites are run directly, skipping any directory `[test] python` already
covers, and pytest's "no tests collected" exit is treated as success
there.

Each suite is **one** ctest entry, not one per case: discovering cases
would mean running pytest at configure time, and a configure step that
executes the project's tests to find out what they are is a worse trade
than a coarser count. The entry runs `python -m pytest
--import-mode=importlib ... -q -rs` with the suite directory as the
working directory. The import mode is mandatory, because a file named
`check.test.py` would otherwise be imported as the module `check.test`
and die. The interpreter is the driver's own, never a hard-coded venv
path, and a missing pytest is a configure-time error rather than a test
that cannot run — ctest does not fail on a command it cannot find, it
reports `***Not Run`, which is neither a pass nor a failure and reads
like neither. `-rs` is passed so skip reasons land in the entry's
output, which makes a suite that skipped its way to green
distinguishable from one that ran; `buildutil test` prints each suite's
case counts next to the ctest summary, and a suite that skipped
everything ends as a ctest **Skipped** entry rather than a clean pass.

## `buildutil bench`

Two shapes behind one verb, chosen by whether `[bench] suite` is set.
With it, the suite runs as `python -m <suite>` with `tools/` on
`PYTHONPATH` and `--repeats`, `--only` and `--json` forwarded. Without
it, buildutil builds with benchmarking on and runs every `*-benches`
executable it finds under the build tree, narrowed by positional module
names and by `--filter`, which becomes google-benchmark's own
`--benchmark_filter`. Release is the default build type here;
`--perf` switches to RelWithDebInfo and wraps each binary in `perf
record`, printing a `perf report` afterwards and leaving the profile
data in the build tree.

Benches are not registered with ctest and never run as part of `test`,
and `buildutil build` does not build them unless `--include-bench` is
given. There is no baseline storage and no run-to-run comparison:
google-benchmark's own output goes straight to the terminal.

## `buildutil coverage`

Always Debug — there is no `--release` — and it shares the Debug build
directory with instrumentation flipped on, so switching between a plain
Debug build and a coverage run re-configures. Every stale `.gcda` under
the build tree is swept first and the count is printed, because gcc
stamps `.gcda` with the `.gcno` checksum and a single recompiled
translation unit makes gcov bail and gcovr abort.

Then ctest runs (`-L` here is a plain regex, unlike `test`'s anchored
alternation), the python suites run, and gcovr produces
`_build/coverage-report/`: `index.html` with its detail pages,
`summary.txt`, a Cobertura `coverage.xml`, `coverage.json` and
`summary.json`. Test sources are excluded by pattern, `[coverage]
exclude` adds project regexes for vendored code, and a couple of gcov
error classes are tolerated deliberately: orphaned notes files from
removed sources, and gcov's branch counters wrapping negative, which
gcovr otherwise treats as fatal and writes no report at all.

Two gates: `--fail-under PCT` is gcovr's own gate on the global rollup,
and `--fail-per-file PCT` is buildutil's, because gcovr's is global-only.
The per-file gate prints one line per module, skips dormant modules by
reading `_bdudata/modules.ini`, skips suffix matches given with `--skip`,
ignores files with no lines, and exits 1 naming every module below the
bar.

A python-driven bridge needs one declaration to be measured. `[coverage]
bridge_dirs` names the build directories of pybind extensions, and the
first one that exists is exported into the pytest environment as
`<module_define_prefix>_BRIDGE_DIR` so the suite imports the instrumented
build rather than an installed one. A declared bridge that is not there,
and `--no-pytest`, both print what will not be collected, because a
module covered only from python otherwise reads as near-zero and looks
like a code problem.

## `buildutil run`

Builds incrementally, installs, and then **execs** the installed binary,
so the target inherits stdout, stderr and signals with nothing between
Ctrl-C and its handler, and it inherits the working directory unchanged.

The target comes from `--target` or from `[run] default` (with per-OS
overrides), and it may be spelled either as the joined module name — what
the editor tasks and cmake use — or as the bare binary name. Unknown
options are forwarded to the target verbatim, and so is everything after
a literal `--`; since `--option` is now a flag `run` knows, a target with
an `--option` of its own needs `buildutil run -- --option ...` or
`--args`. `--args "STRING"` takes one shell-style string, and it is
remembered per application in `_bdudata/vscode-inputs.json`, where it
becomes the editor prompt's next default and the launch configuration's
argv.

Under `--no-build` the build tree is preferred over the install tree,
because benches and build-time generators build but never install, and
compiler detection is skipped entirely — it is only meaningful when
building. On macOS, when the artifact is `<binary>.app`, the launch is
`open -W -n` rather than an exec of the file inside: the plist is what
names the sub-process bundles and `LSUIElement` only applies to a
launched app. On Windows, `--psexec` launches into the interactive
session with the working directory pinned, since psexec otherwise starts
the target in the system directory. A binary that is not there is
reported with a roster of what is installed.

## `buildutil analyze`

One analyser, clang-tidy, and Linux only. It defaults `--compiler` to
clang, which routes the build through clang's own profile and build
directory, because clang-tidy is a clang frontend and a gcc compile
database carries flags clang's driver rejects — the translation unit is
then silently skipped, which is the worst of both outcomes. Rules live in
`.clang-tidy` at the repo root.

A full build runs first: clang-tidy needs the generated headers on disk
and every translation unit in `compile_commands.json`, and only units
present in that database are analysed, with headers covered transitively
through the header filter. Positional arguments accept a file, a
directory, or a bare module name as shorthand for `sources/<name>`, and
`[analyze] exclude` is a list of path substrings.

`--check PATTERN` narrows the check set, `--fix` applies fix-its in place
and forces serial execution — parallel `--fix` lets two units including
the same header rewrite it simultaneously — and `--profile` reports each
check's wall time across the whole run. A result cache is used when the
project vendors one, with the cache directory under `_build/` so
`buildutil clean` wipes it, bypassed for `--fix` and for `--profile`.

Results are classified rather than counted: a `clang-diagnostic-*` means
the frontend could not parse the unit, which is reported and explicitly
**not** gated on, while any other bracketed check name is a real
violation and exits 1. Objective-C is deliberately not analysed — the
glob is `*.cpp` only, so clang-tidy is never handed a translation unit it
would need an Apple SDK to parse.

## Skips

A skip is treated as a defect unless someone said otherwise, by reason.
The project's own CI allows exactly two skips, matched by their text — a
compiler that predates `#embed`, and a resource test that needs ccache —
echoes them, and fails the job on any other. The allowance is by reason
rather than by count so it cannot quietly widen, and because a count
fails the day a test is *added* and teaches people to bump the number
instead of asking what it protects.
