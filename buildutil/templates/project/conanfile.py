"""Conan recipe for @NAME@ — written once by `buildutil init`, then
project-owned.

Source of truth for required packages is sources/CMakeLists.txt — the
Require(...) calls are parsed here so both the cmake build and the
conan graph agree on versions. Whether this project itself SHIPS as a
conan package is read from buildutil.toml's [package] section (kind =
library|application, name) — the same committed fact the
driver's `buildutil publish` acts on. Without it the recipe is a plain
consumer, exactly as before.
"""

from __future__ import annotations

import os
import json
import re
from pathlib import Path

from conan import ConanFile
from conan.tools.cmake import CMakeDeps, CMakeToolchain, cmake_layout


def _package_section() -> dict:
  """[package] from buildutil.toml beside this file; {} = consumer-only.
  A kind of "none" records "the wizard asked, the answer was no"."""
  toml = Path(__file__).resolve().parent / "buildutil.toml"
  if not toml.is_file():
    return {}
  import tomllib
  section = tomllib.loads(toml.read_text(encoding="utf-8")).get("package", {})
  return section if section.get("kind") in ("library", "application") else {}


_PKG = _package_section()


REQUIRE_RE = re.compile(
  # Package name permits letters, digits, underscores, and hyphens
  # (yaml-cpp et al. aren't \w-only).
  r'^\s*Require\s*\(\s*([\w-]+)\s+VERSION\s+"([^"]+)"(.*?)\)',
  re.MULTILINE | re.DOTALL,
)


_KEYWORDS = {"TEST", "BENCH", "TOOL", "SYSTEM", "CONAN", "COMPONENTS",
             "PLATFORM", "OPTIONS", "PUBLIC"}


def _coerce_option(value: str):
  """Conan option values as their natural types: True/False, ints,
  everything else verbatim."""
  if value in ("True", "False"):
    return value == "True"
  try:
    return int(value)
  except ValueError:
    return value


def _parse_extra(extra: str) -> dict:
  tokens = extra.replace("\n", " ").split()
  out = {"test": False, "bench": False, "tool": False, "system": False,
         "public": False, "conan": None, "components": [], "platform": [],
         "options": {}}
  sugar = []
  i = 0
  while i < len(tokens):
    # exact-case matching, same as cmake_parse_arguments: a lowercase
    # `system` is a VALUE (Boost's system component!), not the SYSTEM
    # keyword -- the .upper() this used to do read `COMPONENTS system`
    # as SYSTEM and silently dropped the package from the conan graph
    t = tokens[i]
    if t == "TEST":
      out["test"] = True
      i += 1
    elif t == "SYSTEM":
      out["system"] = True
      i += 1
    elif t == "BENCH":
      out["bench"] = True
      i += 1
    elif t == "TOOL":
      out["tool"] = True
      i += 1
    elif t == "PUBLIC":
      out["public"] = True
      i += 1
    elif t == "CONAN":
      out["conan"] = tokens[i + 1]
      i += 2
    elif t == "COMPONENTS":
      i += 1
      while i < len(tokens) and tokens[i] not in _KEYWORDS:
        # A leading + or - makes the token conan OPTION shorthand rather
        # than a find_package component. Collected, not applied: OPTIONS
        # may be written after COMPONENTS on the same call, and the
        # conflict between the two spellings is only knowable once both
        # have been read.
        if tokens[i][:1] in ("+", "-"):
          sugar.append(tokens[i])
        else:
          out["components"].append(tokens[i])
        i += 1
    elif t == "PLATFORM":
      i += 1
      while i < len(tokens) and tokens[i] not in _KEYWORDS:
        out["platform"].append(tokens[i])
        i += 1
    elif t == "OPTIONS":
      # key=value tokens, passed to conan on the requires call. A token
      # without '=' is refused loudly — a typo'd option that silently
      # vanished would leave the package built with the wrong defaults,
      # which is the exact failure OPTIONS exists to end.
      i += 1
      while i < len(tokens) and tokens[i] not in _KEYWORDS:
        key, eq, value = tokens[i].partition("=")
        if not eq or not key:
          raise ValueError(
            f"Require OPTIONS token {tokens[i]!r} is not key=value")
        out["options"][key] = _coerce_option(value)
        i += 1
    else:
      i += 1
  _apply_sugar(out, sugar)
  return out


def _apply_sugar(out: dict, sugar: list) -> None:
  """COMPONENTS tokens written +name / -name, as the options they mean.

  `+asio` is `with_asio=True`, `-json` is `without_json=True` — the
  per-library options a boost-shaped recipe exposes, one token instead of
  one key=value line each. Whether the recipe HAS that option is conan's
  question and conan answers it by name; nothing here keeps a copy of
  every recipe's option list.
  """
  signs = {}
  for token in sugar:
    sign, name = token[0], token[1:]
    if not name:
      raise ValueError(
        f"Require COMPONENTS token {token!r} names no option")
    if signs.setdefault(name, sign) != sign:
      raise ValueError(
        f"Require COMPONENTS has both '+{name}' and '-{name}'")
    for spelling in (f"with_{name}", f"without_{name}"):
      if spelling in out["options"]:
        raise ValueError(
          f"Require COMPONENTS {token!r} and OPTIONS "
          f"{spelling}={out['options'][spelling]!r} both set an option "
          f"for {name!r}")
    out["options"]["with_" + name if sign == "+" else "without_" + name] = True
  if sugar and out["system"]:
    raise ValueError(
      "Require COMPONENTS option shorthand with SYSTEM is meaningless — "
      "a SYSTEM dep is the host's package and conan never builds it")


def _to_conan_version(v: str) -> str:
  v = v.strip()
  if v == "*":
    return "[*]"
  ops = (">=", "<=", ">", "<", "~", "^")
  if not any(v.startswith(op) for op in ops):
    return v
  # Cap the range at (major+1) so non-semver recipe versions like
  # "cci.20210126" — which sort lexically above real releases — don't
  # get picked by conan's resolver.
  m = re.match(r"^[><=~^]+\s*(\d+)\.", v)
  if m:
    major = int(m.group(1))
    return f"[{v} <{major + 1}]"
  return f"[{v}]"


def _parse_requires(recipe_folder: Path, target_os: str) -> list[dict]:
  text = (recipe_folder / "sources" / "CMakeLists.txt").read_text()
  entries = []
  for name, version, extra in REQUIRE_RE.findall(text):
    info = _parse_extra(extra)
    # Skip deps gated to other platforms. Empty PLATFORM list means
    # "all platforms".
    if info["platform"] and target_os not in info["platform"]:
      continue
    # SYSTEM: the host provides it. find_package only, no conan graph
    # entry -- asking conan for a recipe that does not exist fails the
    # install outright, which is what made projects reach for a bare
    # find_package that works only because this parser cannot see it.
    if info["system"]:
      continue
    entries.append({
      "conan_name": info["conan"] or name.lower(),
      "version": _to_conan_version(version),
      "test":  info["test"],
      "bench": info["bench"],
      "tool":  info["tool"],
      "public": info["public"],
      "options": info["options"],
    })
  return entries


class ProjectRecipe(ConanFile):
  name = _PKG.get("name", "@CONAN_NAME@")
  settings = "os", "compiler", "build_type", "arch"
  if _PKG:
    package_type = {"library": "library",
                    "application": "application"}[_PKG["kind"]]
    if _PKG["kind"] == "library":
      # the conan-standard shared option, linked to buildutil's
      # module_linkage config: the driver passes -o &:shared=True when
      # the config says shared, and conan resolves the "library" type
      # (and distinct package_ids) from it
      options = {"shared": [True, False]}
      default_options = {"shared": False}
    # exports ride WITH the recipe into the cache — the cached copy
    # still reads its [package] section and parses the Require() calls
    # at graph time (exports_sources only materialize for builds)
    exports = ("buildutil.toml", "sources/CMakeLists.txt")
    # THE EXCLUSIONS ARE THE POINT OF THE SECOND LINE. sources/* goes in
    # wholesale, and conan hashes what it exports into the RECIPE
    # REVISION -- so one generated file that exists on one machine and
    # not another splits a release across two revisions, and consumers
    # resolve only the latest one, which hides every binary published
    # under the other. That is not hypothetical: one package
    # shipped a second revision from its Windows runner whose manifest
    # differed by five cmake_test_discovery_<hash>.json files (cmake 4.2
    # wrote them into the test's working directory, which was the source
    # tree -- fixed in the machinery, this is the belt to that brace),
    # and __pycache__ under sources/ is the same class of accident, which
    # is why every publish job also sets PYTHONDONTWRITEBYTECODE.
    #
    # A `!pattern` entry is conan's own exclusion syntax and is evaluated
    # after the includes.
    exports_sources = ("CMakeLists.txt", "buildutil.toml", "sources/*",
                       "cmake/*", ".buildutil/*",
                       "!sources/**/cmake_test_discovery_*.json",
                       "!sources/**/__pycache__/**",
                       "!sources/**/*.pyc")

  def set_version(self):
    # ONE derivation, and the version never lives in the source: the
    # driver computes it (last semver git tag + publish build number,
    # or its --version switch) and passes --version here; adopt it.
    # 0.0.0 is the consumer-only placeholder nothing ever publishes.
    self.version = self.version or "0.0.0"

  def validate(self):
    # this project is controlled by buildutil, which exports BUILDUTIL=
    # <version> to every child it drives — a bare `conan install .`
    # has no such parent and would resolve a graph outside the driver's
    # profile/venv contracts
    if not os.environ.get("BUILDUTIL"):
      from conan.errors import ConanInvalidConfiguration
      raise ConanInvalidConfiguration(
        "this project is controlled by buildutil — run `buildutil build` "
        "(conan is orchestrated: profile, CONAN_HOME and the dependency "
        "graph all come from the driver). If you really need direct "
        "conan, set BUILDUTIL=1 in the environment.")

  def layout(self):
    cmake_layout(self)
    profile = _profile_name(self.settings)
    self.folders.build = f"_build/{profile}"
    self.folders.generators = f"_build/{profile}/generators"

  def generate(self):
    CMakeToolchain(self).generate()
    CMakeDeps(self).generate()

  def requirements(self):
    target_os = str(self.settings.os)
    for entry in _parse_requires(Path(self.recipe_folder), target_os):
      if entry["tool"]:
        continue                       # build_requirements() owns these
      ref = f"{entry['conan_name']}/{entry['version']}"
      # OPTIONS ride the requires call itself (conan 2 supports it on
      # every requires kind) rather than default_options: the option
      # then follows the entry's own gating — a PLATFORM-skipped or
      # SYSTEM dep never leaves a stray pattern behind.
      kwargs = {"options": entry["options"]} if entry["options"] else {}
      # PUBLIC: this dep's headers appear in headers this package
      # exports, so consumers must see them too — conan does not
      # propagate a static-lib requirement's headers by default, and
      # every consumer of such a package failed to compile.
      # transitive_libs stays derived (True for a static library).
      if entry["public"]:
        kwargs["transitive_headers"] = True
      # BENCH deps follow the same test-only semantics as TEST: they
      # only land when BUILD_BENCHMARKING is on, never propagate into
      # the runtime graph. conan has no "bench_requires" so we fold
      # them onto test_requires.
      if entry["test"] or entry["bench"]:
        if os.environ.get("@CMAKE_OPTION_PREFIX@_SKIP_TEST_DEPS") != "1":
          self.test_requires(ref, **kwargs)   # --no-tests drops these
      else:
        self.requires(ref, **kwargs)

  def build_requirements(self):
    target_os = str(self.settings.os)
    for entry in _parse_requires(Path(self.recipe_folder), target_os):
      if entry["tool"]:
        kwargs = {"options": entry["options"]} if entry["options"] else {}
        self.tool_requires(
          f"{entry['conan_name']}/{entry['version']}", **kwargs)

  # ---- packaging (active only with [package] in buildutil.toml) ----
  # The flow is export-pkg-based: `buildutil publish` (and the package
  # half of `buildutil test`) builds the tree as always, then packages
  # THE LOCAL BUILD via package() below. A cache source-build (a
  # consumer's --build=missing) works when the package carries its own
  # driver (`publish --bake-buildutil`, or any vendored project —
  # exports_sources ships .buildutil/*); without it the build is
  # refused with an explanation rather than half-attempted.

  def build(self):
    if not _PKG:
      return
    import sys
    # folders may be unset on a barely-constructed recipe (tests, a
    # bare export) — the refusal below must fire, not an AttributeError
    source = Path(getattr(self, "source_folder", None)
                  or getattr(self, "recipe_folder", None) or ".")
    vendored = source / ".buildutil"
    if (vendored / "buildutil" / "__main__.py").is_file():
      # the baked lane: conan has already resolved this package's own
      # Require()s against the CONSUMER's cache/remotes and generated
      # the toolchain — the vendored buildutil only supplies its cmake
      # machinery and configure contract (stdlib-only `cache-build`:
      # no venv, no nested conan, no network in the cache)
      shared = self.options.get_safe("shared")
      toolchain = Path(self.generators_folder) / "conan_toolchain.cmake"
      previous = os.environ.get("PYTHONPATH")
      os.environ["PYTHONPATH"] = (
        f"{vendored}{os.pathsep}{previous}" if previous else str(vendored))
      try:
        self.run(
          f'"{sys.executable}" -m buildutil cache-build'
          f' --build-dir "{self.build_folder}"'
          f' --toolchain "{toolchain}"'
          f' --build-type {self.settings.build_type}'
          f' --linkage {"shared" if shared else "static"}',
          cwd=str(source))
      finally:
        if previous is None:
          os.environ.pop("PYTHONPATH", None)
        else:
          os.environ["PYTHONPATH"] = previous
      return
    from conan.errors import ConanException
    # version/settings may be unset (tests, a bare export — settings is
    # still the class-level tuple until conan populates it); the story
    # is worth telling either way, with "?" for what is missing
    version = getattr(self, "version", None)
    ref = f"{self.name}/{version}" if version else self.name
    settings = getattr(self, "settings", None)
    def _setting(name):
      return settings.get_safe(name) if hasattr(settings, "get_safe") else "?"
    profile = (f"build_type={_setting('build_type')}, "
               f"compiler={_setting('compiler')}-{_setting('compiler.version')}, "
               f"cppstd={_setting('compiler.cppstd')}")
    raise ConanException(
      f"{self.name}: building this package from source inside the conan "
      "cache is not supported — it was published WITHOUT its build "
      "driver baked in (`buildutil publish --bake-buildutil` changes "
      "that), so binaries come from the project remote, which publish "
      "keeps populated. Fetch a prebuilt binary, or clone the project "
      "and run `buildutil publish` for your profile.\n"
      f"You are here because no published binary matched your profile: "
      f"{profile}.\n"
      "SEE WHAT IS ACTUALLY PUBLISHED FIRST — it settles this in one "
      f"command:\n    conan list \"{ref}:*\" -r <remote>\n"
      "If the published list simply has no entry for your build_type "
      "(publishing Release but not Debug, or the reverse, is the common "
      "case), the fix is on the PUBLISHER: run `buildutil publish` for "
      "the missing profile. Nothing is wrong on your side.\n"
      "Only if your build_type IS published does the package_id-drift "
      "explanation apply: a version-RANGED dependency of this "
      "package resolved differently in your cache than at publish time. "
      "Compare `conan graph info` resolutions against the published "
      "package's requires and align them (update/pin the drifting dep) "
      "instead of building from source.")

  def package(self):
    if not _PKG:
      return
    # the layout() above points build_folder at the same
    # _build/<profile> tree buildutil just built — install it whole;
    # the install tree mirrors the source tree (buildutil convention)
    self.run(f'cmake --install "{self.build_folder}" '
             f'--prefix "{self.package_folder}"')

  def package_info(self):
    if not _PKG:
      return
    root = Path(self.package_folder)
    modules = _cmake_build_modules(root, self.name)
    if modules:
      self.cpp_info.set_property("cmake_build_modules", modules)
    if _PKG["kind"] == "application":
      bindirs = sorted({
        str(p.parent.relative_to(root)) for p in root.rglob("*")
        if p.is_file() and os.access(p, os.X_OK)})
      self.cpp_info.bindirs = bindirs or ["."]
      self.cpp_info.libdirs = []
      self.cpp_info.includedirs = []
      return
    # library: discover what the mirror actually shipped — libs by
    # extension wherever they landed, headers under include/
    libs, libdirs = set(), set()
    for p in root.rglob("*"):
      if not p.is_file():
        continue
      if p.suffix in (".a", ".lib") or p.suffix in (".so", ".dylib") \
         or ".so." in p.name:
        stem = p.name.split(".")[0]
        libs.add(stem[3:] if stem.startswith("lib") else stem)
        libdirs.add(str(p.parent.relative_to(root)))
    # `[package] linkable = false`: what this package ships is loaded at
    # run time, not linked -- libretro cores a front-end dlopens, plugins,
    # a data-only payload. They keep their libdirs (that is where the
    # loader is pointed) and advertise no link target, so a consumer
    # spelling target_link_libraries against the package gets nothing on
    # its link line instead of -l<the-thing-it-must-not-link>.
    if not _PKG.get("linkable", True):
      libs = set()
    incdirs = ["include"] if (root / "include").is_dir() else []
    # COMPONENTS. A multi-module package lets a consumer link ONE module
    # (<pkg>::<module>) instead of the whole package; conan keeps
    # <pkg>::<pkg> as the aggregate that requires them all, so the coarse
    # spelling never breaks. The graph comes from the manifest the build
    # wrote — Link_dependencies is cmake and this recipe cannot see it.
    manifest = root / "share" / "buildutil" / "buildutil-components.json"
    comps = []
    if manifest.is_file():
      try:
        comps = json.loads(manifest.read_text()).get("components", [])
      except ValueError:
        comps = []
    # one module is not worth componentising: the aggregate IS the module
    if len(comps) > 1:
      def _cname(path):
        # sources/-relative path -> component name. Drop a leading element
        # equal to the package name (sources/<pkg>/utilities -> utilities),
        # so the target reads <pkg>::utilities rather than the stuttering
        # <pkg>::<pkg>-utilities. A project without that level is
        # unaffected. Nested modules keep their depth: net/http -> net-http.
        parts = [x for x in path.split("/") if x]
        if parts and parts[0] == self.name:
          parts = parts[1:]
        return "-".join(parts) or self.name
      by_path = {c["path"]: _cname(c["path"]) for c in comps}
      for c in comps:
        name = by_path[c["path"]]
        comp = self.cpp_info.components[name]
        comp.libs = [c["lib"]] if c["lib"] in libs else []
        comp.libdirs = sorted(libdirs) or ["lib"]
        comp.includedirs = incdirs
        reqs = [by_path[n] for n in c.get("needs", []) if n in by_path]
        for ext in c.get("external", []):
          # a conan target is pkg::comp; anything else is a system lib
          if "::" in ext:
            reqs.append(ext)
          else:
            comp.system_libs.append(ext)
        comp.requires = reqs
      return
    self.cpp_info.libs = sorted(libs)
    self.cpp_info.libdirs = sorted(libdirs) or ["lib"]
    self.cpp_info.includedirs = incdirs


def _cmake_build_modules(root: Path, name: str) -> list[str]:
  """`share/cmake/<package>/*.cmake` in the install tree IS a cmake build
  module: a file put there exists to be include()d by find_package, and
  nothing else puts one there. Presence is the declaration, as it is for
  every other directory convention -- a project generates the file into
  its `*.install/` tree and this announces it."""
  directory = root / "share" / "cmake" / name
  return [str(found.relative_to(root))
          for found in sorted(directory.glob("*.cmake"))]


def _module_linkage() -> str:
  """The module_linkage config option, resolved exactly as the driver
  resolves it (env — which the driver exports pre-resolved — then the
  checkout-local ini, then static). layout() needs it because the
  profile DIR carries the linkage axis: static and shared are separate
  build trees, and this recipe must point at the same one the driver
  builds."""
  env = os.environ.get("BUILDUTIL_OPT_MODULE_LINKAGE", "")
  if env in ("static", "shared"):
    return env
  ini = Path(__file__).resolve().parent / "_bdudata" / "config.ini"
  if ini.is_file():
    section = ""
    for raw in ini.read_text().splitlines():
      line = raw.split("#", 1)[0].strip()
      if not line:
        continue
      if line.startswith("[") and line.endswith("]"):
        section = line[1:-1].strip().lower()
      elif section == "options":
        key, _, value = line.partition("=")
        if key.strip() == "module_linkage" and value.strip() in (
            "static", "shared"):
          return value.strip()
  return "static"


def _profile_name(settings) -> str:
  parts = [str(settings.arch), str(settings.os), str(settings.compiler)]
  linkage = _module_linkage()
  if linkage != "static":
    parts.append(linkage)
  parts.append(str(settings.build_type))
  return "-".join(parts).lower()
