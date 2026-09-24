"""Conan packaging of the project itself (0.47): export, package test,
publish, and the version model.

A project opts in through `[package]` in buildutil.toml (kind = library
| application, name) — written by init's packaging wizard and read by
BOTH the driver (here) and the scaffolded conanfile.

THE VERSION IS DERIVED, not declared (owner ruling): base = the last
git tag that is a valid semantic version, plus a build number bumped on
every publish (`--no-version-autoincrement` holds it). The pair
persists in _bdudata/package-version.ini — checkout-local state, like
every other _bdudata fact; a new tag resets the build counter. When no
semver tag exists the base is PROMPTED at a tty (and saved), refused
otherwise. There is deliberately no version key in [package]: `publish
--version` is the escape hatch for a project that versions some other
way, and it persists nothing.

The flow is export-pkg-based: the tree buildutil already built IS the
package source — `conan export-pkg` runs the recipe's package() against
the local build/install layout (the driver passes --version, the recipe
adopts it via set_version), test_package/ consumes the cached package
like a real consumer, and publish uploads recipe + binaries.

Every conan/git invocation goes through injectable seams so command
assembly and version derivation are testable without either tool.
"""
from __future__ import annotations

import functools
import json
import re
import subprocess
import types
from pathlib import Path

from . import config

SEMVER_TAG = re.compile(r"^v?(\d+\.\d+\.\d+)$")


def configured() -> bool:
  # "none" is a recorded wizard answer, not a packaging config
  return config.PROJECT["package_kind"] in ("library", "application")


def package_name() -> str:
  return config.PROJECT["package_name"] or config.PROJECT["name"].lower()


def has_package_test(root: Path | None = None) -> bool:
  root = root or config.REPO_ROOT
  return (root / "test_package" / "conanfile.py").is_file()


def _state_file(root: Path | None = None) -> Path:
  return (root or config.REPO_ROOT) / "_bdudata" / "package-version.ini"


def _load_state(root: Path | None = None) -> tuple[str, int]:
  """(base, build) as last persisted; ("", 0) when never published."""
  path = _state_file(root)
  base, build = "", 0
  if path.is_file():
    for line in path.read_text().splitlines():
      key, _, value = line.partition("=")
      if key.strip() == "base":
        base = value.strip()
      elif key.strip() == "build":
        try:
          build = int(value.strip())
        except ValueError:
          build = 0
  return base, build


def _save_state(base: str, build: int, root: Path | None = None) -> None:
  path = _state_file(root)
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text("# last published package version (buildutil publish)\n"
                  f"base = {base}\nbuild = {build}\n")


def _git_semver_base(run=subprocess.run) -> str:
  """The newest reachable tag that is a valid semver, '' when none.
  --merged HEAD: a tag on an unmerged branch is not this build's
  version; newest-first so 'last tag' means what it says."""
  proc = run(["git", "tag", "--merged", "HEAD", "--sort=-creatordate"],
             capture_output=True, text=True)
  if proc.returncode != 0:
    return ""                                  # not a git repo
  for line in proc.stdout.splitlines():
    m = SEMVER_TAG.match(line.strip())
    if m:
      return m.group(1)
  return ""


def tag_version(root: Path) -> str:
  """The last reachable x.y.z tag of the tree at root, '' when there is none."""
  def git(command, **kwargs):
    try:
      return subprocess.run(command, cwd=root, **kwargs)
    except OSError:
      return subprocess.CompletedProcess(command, 1, "", "")
  return _git_semver_base(git)


_VERSION_RE = re.compile(r"^(\d+\.\d+\.\d+)\.(\d+)$")


def remote_builds(base: str, run=subprocess.run) -> list[int] | None:
  """The build numbers this base already has ON THE REMOTE, or None
  when the remote cannot answer (unconfigured, unreachable, or an
  output this cannot read). The whole package is listed and filtered
  here rather than passed as a version pattern: a pattern conan
  declines to match looks exactly like an empty remote, and seeding
  from an empty remote is the bug."""
  from . import bootstrap
  name, url, _, _ = bootstrap.conan_remote_env()
  if not url:
    return None
  try:
    proc = run(["conan", "list", f"{package_name()}/*", "-r", name,
                "--format=json"], capture_output=True, text=True)
  except OSError:
    return None
  if proc.returncode != 0:
    return None
  try:
    listing = json.loads(proc.stdout)
  except ValueError:
    return None
  builds = []
  for refs in listing.values():
    if not isinstance(refs, dict):
      continue
    for ref in refs:
      pkg, _, version = str(ref).partition("/")
      m = _VERSION_RE.match(version)
      if pkg == package_name() and m and m.group(1) == base:
        builds.append(int(m.group(2)))
  return builds


def _seed_build(base: str, saved: int, conan) -> int:
  """The highest build number this base is known to have anywhere: the
  counter alone is per-checkout state, so two boxes on the same tag
  both produce x.y.z.1 and conan resolves the range to the higher one
  — the older code winning. The remote is the shared ledger; when it
  cannot answer, the local state still bounds the answer from below."""
  from . import bootstrap
  if not bootstrap.conan_remote_env()[1]:
    return saved
  published = remote_builds(base, run=conan)
  if published is None:
    print("conan remote could not be asked for published build numbers "
          "— using this checkout's counter (a publish from another box "
          "may shadow this one; --version overrides).")
    return saved
  return max([saved, *published])


def resolve_version(bump: bool, override: str = "", git=subprocess.run,
                    ask=input, isatty=None,
                    root: Path | None = None, conan=subprocess.run):
  """The version to package as, and a `commit` callback that persists
  the state — called by publish AFTER the upload succeeded, so a failed
  or dry-run publish never consumes a build number.

  THE VERSION NEVER LIVES IN THE PACKAGE SOURCE (owner ruling) — there
  is no committed version key anywhere. Order: an explicit `override`
  (publish --version) wins verbatim, no state touched; else base = last
  semver git tag (a base CHANGE resets the counter); else the persisted
  base from a previous prompt; else prompt at a tty and refuse anywhere
  else. The build number is one above the highest this base is known to
  carry — the remote's published set, floored by the persisted counter
  — when `bump`, and the persisted one unchanged otherwise."""
  if override:
    return override, lambda: None
  saved_base, saved_build = _load_state(root)
  base = _git_semver_base(git)
  if base and base != saved_base:
    saved_build = 0                            # a new tag restarts builds
  if not base:
    base = saved_base
  if not base:
    import sys
    tty = sys.stdin.isatty() if isatty is None else isatty
    if not tty:
      raise SystemExit(
        "buildutil: cannot infer the package version — no git tag is a "
        "valid semantic version (x.y.z), nothing was entered before, "
        "and this is not a terminal to ask at. Tag the repo (git tag "
        "1.0.0), pass --version, or run interactively to be prompted.")
    entered = ""
    while not SEMVER_TAG.match(entered):
      entered = ask("package base version (semver x.y.z): ").strip()
    base = SEMVER_TAG.match(entered).group(1)
  # only a BUMP consults the remote: holding the number means
  # republishing the one this checkout last used
  build = (_seed_build(base, saved_build, conan) + 1 if bump
           else max(saved_build, 1))
  final_base, final_build = base, build
  return (f"{base}.{build}",
          lambda: _save_state(final_base, final_build, root))


def ref(version: str, user: str | None = None,
        channel: str | None = None) -> str:
  suffix = f"@{user or ''}/{channel or ''}" if user or channel else ""
  return f"{package_name()}/{version}{suffix}"


REQUIRES_PARSER = (Path(__file__).resolve().parent / "templates" / "project"
                   / "buildutil_requires.py")


@functools.cache
def requires_parser() -> types.ModuleType:
  """The Require() parser conanfile.py imports from beside itself."""
  module = types.ModuleType("buildutil_requires")
  module.__file__ = str(REQUIRES_PARSER)
  source = REQUIRES_PARSER.read_text(encoding="utf-8")
  exec(compile(source, str(REQUIRES_PARSER), "exec"), module.__dict__)
  return module


def declared_requires(root: Path | None = None,
                      target_os: str | None = None) -> list[dict]:
  """sources/CMakeLists.txt's Require() lines, as conanfile.py reads them."""
  path = (root or config.REPO_ROOT) / "sources" / "CMakeLists.txt"
  if not path.is_file():
    return []
  return requires_parser().requires(path.read_text(), target_os)


def ranged_runtime_requires(root: Path | None = None) -> list[tuple[str, str]]:
  """Runtime Require() entries whose VERSION is a range — the publish
  footgun: the published binary embeds ONE resolution of the
  range, but every consumer re-resolves it against their own cache and
  remotes. Any drift (a newer pugixml in a warm cache) computes a
  different package_id, conan finds no binary, --build=missing kicks
  in, and the recipe's source-build refusal fires — blaming driver
  discipline for what is really version drift. SYSTEM/TOOL/TEST/BENCH
  deps stay out: they never reach a consumer's graph."""
  ranged = []
  for entry in declared_requires(root):
    if any(entry[lane] for lane in ("system", "tool", "test", "bench")):
      continue
    v = entry["floor"].strip()
    if v == "*" or v.startswith((">", "<", "~", "^")):
      ranged.append((entry["cmake_name"], v))
  return ranged


_HOST_REQUIRES = re.compile(r"(?m)^_HOST_REQUIRES = 1$")


def knows_host_requires(recipe: str) -> bool:
  """Whether a conanfile's text is the template that forces host wrappers."""
  return bool(_HOST_REQUIRES.search(recipe))


def host_requires(root: Path | None = None,
                  target_os: str | None = None) -> list[dict]:
  """The Require(... SYSTEM) lines active on target_os."""
  return [entry for entry in declared_requires(root, target_os)
          if entry["system"]]


def shared_requested() -> bool:
  """Library packaging follows the module_linkage config: shared
  linkage publishes the conan-standard shared=True package_id.
  Applications declare no shared option — always False there."""
  from . import configopts
  return (config.PROJECT["package_kind"] == "library"
          and configopts.get("module_linkage") == "shared")


def export_pkg(version: str, profile: Path, build_profile: Path,
               shared: bool = False,
               run=subprocess.check_call, *, user: str | None = None,
               channel: str | None = None) -> str:
  """Package the locally built tree into the conan cache. The recipe's
  layout() points at the same _build/<profile> dir buildutil built, so
  package() (cmake --install) packages exactly what was just built.
  --version is the ONE derivation reaching conan — the recipe adopts it
  in set_version(), never re-deriving. -tf= : export-pkg would auto-run
  test_package, and the callers here invoke `conan test` explicitly —
  once, not twice.
  Optional user/channel select a namespaced reference for export and testing."""
  run(["conan", "export-pkg", ".", f"--version={version}", "-tf=",
       *(["--user", user] if user else []),
       *(["--channel", channel] if channel else []),
       *((["-o", "&:shared=True"]) if shared else []),
       f"--profile:host={profile}", f"--profile:build={build_profile}"])
  return ref(version, user, channel)


def run_package_test(version: str, profile: Path, build_profile: Path,
                     shared: bool = False,
                     run=subprocess.check_call, *, user: str | None = None,
                     channel: str | None = None) -> None:
  """Consume the cached package the way a consumer would: build and run
  test_package/ against the reference — requesting the same shared
  option the export packaged, or the test computes the other
  package_id and misses the binary. --build=missing lets the test's
  own scaffolding deps resolve without demanding a prebuilt cache.
  Optional user/channel select a namespaced reference for export and testing."""
  run(["conan", "test", "test_package", ref(version, user, channel),
       *((["-o", f"{package_name()}/*:shared=True"]) if shared else []),
       f"--profile:host={profile}", f"--profile:build={build_profile}",
       "--build=missing"])


def upload(version: str, target_os: str | None = None,
           run=subprocess.check_call) -> None:
  """Recipe + binaries to the project remote; a missing remote is an
  ERROR here (unlike the dependency-cache upload, which skips): publish
  without a destination did not publish, and must say so."""
  from . import bootstrap
  name, url, _, _ = bootstrap.conan_remote_env()
  if not url:
    raise SystemExit(
      "buildutil publish: no conan remote configured (CONAN_REMOTE_URL "
      "/ .env / CI_ARTIFACTORY_*) — there is nowhere to publish to. "
      "Configure the remote seam and re-run.")
  run(["conan", "upload", ref(version), "-r", name, "--confirm"])
  # a consumer resolves the forced wrapper from the remote and probes its host
  for host in host_requires(target_os=target_os):
    if not host["test"] and not host["bench"]:
      run(["conan", "upload", host["ref"], "-r", name, "--confirm",
           "--only-recipe"])
