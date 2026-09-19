# `buildutil.toml`

buildutil is generic; the project it drives is not. Everything
project-specific lives in one file at the repo root, and the repo root
**is** the nearest ancestor holding it (`BUILDUTIL_ROOT` overrides). No
key is required — an empty file marks the root and takes every default.
Validation is strict and names itself at the command the project ran,
not three layers down: unknown keys are refused for `[runtime]`,
`[bundle.macos]`, `[bundle.macos.helpers]`, `[modules.<name>]`, `[test]`,
`[test.timeout]` and `[resources]`, and a platform tag that is not in the
vocabulary is a refusal too.

## `[project]`

| key | default | what it changes |
|---|---|---|
| `name` | `"project"` | editor task labels, messages, the default resource module and namespace, the default bundle module |
| `cmake_option_prefix` | `"BUILDUTIL"` | `-D<PREFIX>_COVERAGE`, `_GC_SECTIONS`, `_MAX_ERRORS`, `_SKIP_TEST_DEPS`, and the macros `[options]` generates |
| `module_define_prefix` | `"MOD"` | the `<PREFIX>_<NAME>_ENABLED=1` defines, the `<PREFIX>_MODULE_DEFINES` list, and the coverage bridge variable |

```toml
[project]
name = "bossdeux"
cmake_option_prefix = "BOSSDEUX"
module_define_prefix = "BDX"
```

## `[options]`

One key per project option, and **the value is the default**. Names are
lower snake case starting with a letter; types are exactly three, bool,
int and string. Every declared option becomes a macro
`<cmake_option_prefix>_<NAME>` force-included into every translation unit
the project compiles. A string default may not carry `"`, `;`, `\`, `|`,
`$` or whitespace, because the value travels to the compiler through a
cmake string. See [options.md](options.md).

```toml
[options]
contracts = true
max_depth = 32
greeting = "hello"
```

## `[venv]` and `[buildutil]` and `[update]`

| key | default | what it changes |
|---|---|---|
| `[venv] extra_deps` | `[]` | extra pip requirements for the project venv, at the project's own pins. A consumer's source build in the conan cache refuses rather than installing them uninvited — see [packaging.md](packaging.md) |
| `[buildutil] version` | `""` | pins what `buildutil update` installs inside this project; `"latest"` or absent means newest, and a pin below 0.12.0 is refused |
| `[update] source` | `""` | where `buildutil update` installs from: a package index URL, or a git URL. Absent, pip's own configuration decides unless the running copy records where it came from |

## `[cmake]` and `[modules]`

| key | default | what it changes |
|---|---|---|
| `[cmake] extensions` | `[]` | opt-in machinery rendered alongside the base — today `reflect` and `watcom`. `buildutil extend <name>` writes this key |
| `[cmake] export_module_headers` | `false` | ported trees only: every module's own directory joins its consumers' include path |
| `[modules] dormant` | `[]` | modules that do not build by default — a project fact, committed |
| `[modules.<name>] platforms` | all | target tags the module builds on; an empty list is dormant everywhere |
| `[modules.<name>] frameworks` | — | Apple system frameworks the module links; inert off macOS |
| `[modules.<name>] objc_arc` | `true` | `false` compiles that module's Objective-C and Objective-C++ without ARC |

```toml
[modules.http]
platforms = ["native"]   # Boost.Asio has no Emscripten transport
```

## `[conan]`

The project's dependency policy, appended to every generated profile.

| key | default | what it changes |
|---|---|---|
| `options` | `[]` | lines appended to the profile's `[options]` |
| `options_linux`, `options_windows`, `options_macos`, `options_emscripten` | — | per-target variants, which win over the plain `options` on that target |
| `conf` | `[]` | lines appended to the profile's `[conf]` |

```toml
[conan]
options_linux = ["sdl/*:x11=False", "sdl/*:wayland=False"]
options_emscripten = ["sdl/*:opengl=False"]
conf = ["tools.build:jobs=25"]
```

## `[test]`, `[bench]`, `[coverage]`, `[analyze]`, `[run]`

| key | default | what it changes |
|---|---|---|
| `[test] python` | `[]` | directories that are **not** modules and hold a pytest suite; each gets a `<dir>-pytest` ctest entry. Repo-relative, no `..`, and a directory with no test file in it fails the configure |
| `[test.timeout]` | `{}` | one entry per declared suite, `"<suite>" = <seconds>`, the wall clock that suite's ctest entry gets. A key that is not in `[test] python` is refused by name, and so is a value that is not a positive number |
| `[bench] suite` | `""` | a python bench suite run as `python -m <suite>`; unset, `buildutil bench` runs the `*-benches` executables instead |
| `[coverage] bridge_dirs` | `[]` | build directories of python bridges; the first one that exists is exported to pytest as `<module_define_prefix>_BRIDGE_DIR` |
| `[coverage] exclude` | `[]` | extra `gcovr --exclude` regexes, for vendored code |
| `[analyze] exclude` | `[]` | path **substrings** clang-tidy skips — not regexes |
| `[run] default` | `""` | the binary `buildutil run` launches |
| `[run] default_linux`, `default_windows`, `default_macos` | — | per-target overrides, which win over `default` |

Note the prefix on the bridge variable: it is the **module define
prefix**, so a project whose prefixes differ gets `BDX_BRIDGE_DIR`, not
`BOSSDEUX_BRIDGE_DIR`. See [testing.md](testing.md).

## `[package]` and `[runtime]`

| key | default | what it changes |
|---|---|---|
| `[package] kind` | `""` | `library`, `application`, or `none` for "asked, answered no" |
| `[package] name` | `""` | the conan package name; empty falls back to the lower-cased project name |
| `[package] linkable` | `true` | `false` keeps `cpp_info.libs` empty for a package whose shared objects are loaded by name rather than linked |
| `[runtime] from` | `[]` | dependencies whose shipped files must sit next to the executable |
| `[runtime] targets` | `[]` | limits that payload to named applications; default is every application |
| `[runtime] dirs` | `[]` | repo-relative directories with no package behind them |

**There is no `[package] version` key.** The version is derived from the
last reachable semver git tag plus a build number, and writing a version
line here does nothing. See [packaging.md](packaging.md).

## `[resources]`

A declared set of files embedded in the binary. `[[resources]]` — double
brackets, repeated — declares several independent sets. The
directory-driven form, `*.embed/`, needs no declaration at all; both are
described in [resources.md](resources.md).

| key | default | what it changes |
|---|---|---|
| `dir` | required | the directory whose files are embedded; repo-relative, no `..` |
| `files` | every file | globs selecting files |
| `module` | `[project] name` | which module carries them |
| `namespace` | derived from `[project] name` | the generated `<ns>::resources` |
| `prefix` | `""` | prepended to every resource name |
| `mime` | `{}` | content types for exotic extensions; every key must start with `.` |

## `[bundle.macos]`

| key | default | what it changes |
|---|---|---|
| `module` | `[project] name` | the module whose executable ships as an `.app` |
| `identifier` | **required** | the bundle identifier; empty is an error |
| `name` | the module | `CFBundleName` |
| `version` | `"0.0.0"` | the bundle version |
| `plist` | `{}` | extra `Info.plist` keys — string, bool, int or list of strings |
| `helpers.module` | `""` | the module whose one executable every helper bundle runs |
| `helpers.suffix` | `" Helper"` | what is appended to the bundle name for helpers |
| `helpers.variants` | `[]` | helper variants; an empty string means the unsuffixed helper |

See [platform-apps.md](platform-apps.md).

## `[reflect]`

| key | default | what it changes |
|---|---|---|
| `namespace` | `"reflect"` | the namespace baked into generated code, and therefore into project source |
| `annotation` | `"macro"` | `macro` or `attribute` — what `_Label`, `_Meta` and `_Help` expand to |
| `macros` | `"auto"` | `auto`, `none`, or a path to the project's own macro file |
| `include` | `"detour"` | `detour` is the only delivery; `source` and `module` are refused |
| `scan` | `"direct"` | read only by the deprecated force-include modes |

See [reflect.md](reflect.md).

## `.env` and the conan remote

A repo-root `.env` — `KEY=VALUE` lines, `#` comments, one pair of
surrounding quotes stripped, gitignored by `init` — is loaded on every
run, and a real environment variable always wins over the file, so
CI-injected values and shell exports are never overridden.

The conan remote reads one seam, wherever the values come from.
`CONAN_REMOTE_URL` is the switch: set it and buildutil replaces
conancenter with that URL and logs in. `CONAN_REMOTE_NAME` is optional
and defaults to `conancenter`, so the mirror is registered literally
under the public remote's name; a custom name additionally removes the
public remote so resolution never races the mirror.
`CONAN_REMOTE_USER` and `CONAN_REMOTE_PASS` are optional — absent means
an anonymous mirror, no login, and no dependency upload after a build.
`buildutil init` asks for all four and writes them here. GitLab CI's `CI_ARTIFACTORY_{HREF,NAME,
USER,PASS}` are the fallback spellings.

```
# .env
CONAN_REMOTE_URL=https://repo.example.com/artifactory/api/conan/conan-local
CONAN_REMOTE_USER=alice
CONAN_REMOTE_PASS=…token…
```

Registration runs on every command but is stamp-guarded: a fingerprint of
the seam is written into the conan home after a successful registration
and login, and while it matches and the remote is still registered,
nothing happens — so editing `.env` takes effect on the next command at
no network cost. A login failure is **classified** rather than retried
blindly: wrong credentials are never retried, because retrying arms a
registry's brute-force lockout; a lockout message stops immediately; only
a transient error is retried, six times, ten seconds apart. If the remote
stays unreachable buildutil disables it and registers the public
conancenter as a degraded fallback without stamping, so the next run
tries the mirror again. `buildutil setup` forces a fresh registration.

## Environment variables

`BUILDUTIL_ROOT` overrides root discovery. `BUILDUTIL_VENV_DIR` moves the
project venv off a bind mount. `BUILDUTIL_SYSTEM=1` says the running
interpreter **is** the toolchain, so no per-checkout venv is ever built —
what a container image sets. `BUILDUTIL_EMBED_FALLBACK=1` forces the
non-`#embed` resource back end, `BUILDUTIL_PREBUILT_FIRMWARE` points the
build at prebuilt firmware, `BUILDUTIL_LIBCLANG` and
`BUILDUTIL_RESOURCE_DIR` aim the reflect generator at a particular
libclang and its resource headers, `BUILDUTIL_WINE_MSVC` claims or
suppresses the wine-msvc lane, and `BUILDUTIL_CACHE_BUILD_DEPS=install`
lets a source build inside the conan cache pip-install the package's
`[venv] extra_deps` into the interpreter running it. `CI` turns the watchdog off by default.
Each of these is a diagnostic or a fact about a machine rather than
something a project decides, which is why none of them is a toml key.
