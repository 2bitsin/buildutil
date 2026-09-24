"""`buildutil init` — start a project (stdlib-only, pre-venv).

The one subcommand that must work where NO project exists yet: it
writes buildutil.toml (never overwrites anything), deposits the base
cmake machinery, and scaffolds a buildable hello-world project — root
CMakeLists, sources/ with GoogleTest + google-benchmark wired through
Require(), a conanfile that parses those same calls, and an example
`hello` module carrying a lib, an app, a test and a bench. Everything
else in the CLI sits behind the venv bootstrap and the buildutil.toml
root discovery; this deliberately sits in front of both.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from . import deposit

PROJECT_TEMPLATES = Path(__file__).resolve().parent / "templates" / "project"
PACKAGE_TEMPLATES = Path(__file__).resolve().parent / "templates" / "package"

PACKAGE_KINDS = ("library", "application", "none")

# template-relative name -> project-relative name. Dotfiles don't ride
# reliably in package data globs, so .gitignore ships as `gitignore`.
_RENAMES = {"gitignore": ".gitignore"}


def _prefix(name: str) -> str:
  return re.sub(r"[^A-Z0-9]", "", name.upper()) or "PROJECT"


def _conan_name(name: str) -> str:
  """A valid conan package name from the project name: lowercase,
  [a-z0-9_+.-], at least two chars starting alphanumeric."""
  n = re.sub(r"[^a-z0-9_+.-]", "", name.lower())
  if len(n) < 2 or not n[0].isalnum():
    return "project"
  return n


def _package_wizard(default_name: str) -> tuple[str, str]:
  """The packaging multiple-choice + follow-ups, tty only. Returns
  (kind, package name); kind "none" records "asked, answered no" so
  the question is never re-asked. No version question — the version
  never lives in the package source (publish derives it)."""
  print("package this project as a conan package?")
  print("  1) library      — ships headers + libraries")
  print("  2) application  — ships executables")
  print("  3) don't package")
  choice = ""
  while choice not in ("1", "2", "3"):
    choice = input("choice [3]: ").strip() or "3"
  kind = {"1": "library", "2": "application", "3": "none"}[choice]
  if kind == "none":
    return kind, ""
  entered = input(f"package name [{default_name}]: ").strip()
  pkg_name = entered or default_name
  print("  (the version is never committed: publish derives it from the "
        "last semver git tag + a build number, or takes --version)")
  return kind, pkg_name


def _remote_wizard() -> dict[str, str]:
  """The conan remote, asked at a terminal. The URL comes first because
  an empty one means there is no remote and the rest is moot; the
  password is read without echo and only ever reaches the .env."""
  url = input("conan remote URL (empty = none): ").strip()
  if not url:
    return {}
  name = input("conan remote name [conancenter]: ").strip() or "conancenter"
  values = {"CONAN_REMOTE_URL": url, "CONAN_REMOTE_NAME": name}
  user = input("conan remote user (empty = anonymous): ").strip()
  if user:
    import getpass
    values["CONAN_REMOTE_USER"] = user
    password = getpass.getpass("conan remote password: ")
    if password:
      values["CONAN_REMOTE_PASS"] = password
  return values


def _ensure_env_ignored(root: Path) -> None:
  """A .env holding a password that git can see is the one mistake this
  onboarding could introduce, so the ignore rule is verified, not
  assumed — `--bare` scaffolds no .gitignore at all."""
  path = root / ".gitignore"
  lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
  if ".env" in [line.strip() for line in lines]:
    return
  with path.open("a", encoding="utf-8") as f:
    f.write("\n# local secrets (CONAN_REMOTE_* etc.), read by buildutil's "
            "dotenv\n.env\n")
  print("  .gitignore: .env added")


def _write_env(root: Path, values: dict[str, str]) -> None:
  """CONAN_REMOTE_* into the repo-root .env — the file buildutil's
  dotenv feeds the remote seam from. A key the file already declares is
  the project's answer, not ours; only key NAMES are ever printed."""
  if not values:
    return
  _ensure_env_ignored(root)
  path = root / ".env"
  text = path.read_text(encoding="utf-8") if path.is_file() else ""
  declared = {line.partition("=")[0].strip() for line in text.splitlines()
              if not line.lstrip().startswith("#")}
  added = {key: value for key, value in values.items() if key not in declared}
  if not added:
    print("  .env declares the conan remote already — kept")
    return
  if not text:
    text = "# local secrets, read by buildutil's dotenv — never committed\n"
  elif not text.endswith("\n"):
    text += "\n"
  path.write_text(text + "".join(f"{k}={v}\n" for k, v in added.items()),
                  encoding="utf-8")
  print(f"  .env: {', '.join(added)} written (gitignored)")


def _package_toml_section(kind: str, pkg_name: str) -> str:
  # no version key, ever: the version never lives in the package source
  lines = ["", "[package]",
           f'kind = "{kind}"   # library | application | none']
  if kind != "none":
    lines += [f'name = "{pkg_name}"']
  return "\n".join(lines) + "\n"


def _scaffold_package(root: Path, kind: str, pkg_name: str) -> None:
  """test_package/ for the chosen kind, never overwriting — same rule
  as the project scaffold."""
  src_root = PACKAGE_TEMPLATES / kind
  for src in sorted(src_root.rglob("*")):
    if not src.is_file() or "__pycache__" in src.parts:
      continue
    rel = src.relative_to(src_root)
    dst = root / rel
    if dst.exists():
      print(f"  {rel} exists — kept")
      continue
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(src.read_text(encoding="utf-8")
                   .replace("@PKG_NAME@", pkg_name), encoding="utf-8")
    print(f"  {rel} written")


def _upgrade_conanfile(root: Path, name: str, cmake_prefix: str) -> None:
  """A pre-packaging conanfile cannot package. When packaging is being
  configured and the existing recipe predates the [package]-aware
  template (no _package_section marker), move it aside and regenerate —
  loudly, so hand edits (Require OPTIONS made most of them obsolete)
  are ported deliberately rather than lost silently."""
  recipe = root / "conanfile.py"
  if not recipe.is_file():
    return
  text = recipe.read_text(encoding="utf-8")
  from . import packaging
  if "_package_section" in text and packaging.knows_host_requires(text):
    return                                    # already the aware template
  backup = root / "conanfile.py.bak"
  recipe.rename(backup)
  template = (PROJECT_TEMPLATES / "conanfile.py").read_text(encoding="utf-8")
  recipe.write_text(template
                    .replace("@NAME@", name)
                    .replace("@CONAN_NAME@", _conan_name(name))
                    .replace("@CMAKE_OPTION_PREFIX@", cmake_prefix),
                    encoding="utf-8")
  print(f"  conanfile.py predates packaging or host packages — regenerated "
        f"(old recipe kept at {backup.name}; port any hand edits, "
        f"then delete it)")


def _scaffold(root: Path, name: str, cmake_prefix: str,
              module_prefix: str) -> None:
  """Render the project scaffold, file by file, never overwriting: a
  file that already exists is the project's, not ours. The example
  hello module only lands when sources/ doesn't exist yet — a tree
  with real modules doesn't want a demo appearing in its build."""
  subs = {"@NAME@": name, "@CONAN_NAME@": _conan_name(name),
          "@CMAKE_OPTION_PREFIX@": cmake_prefix,
          "@MODULE_DEFINE_PREFIX@": module_prefix}
  skip_hello = (root / "sources").exists()
  for src in sorted(PROJECT_TEMPLATES.rglob("*")):
    if not src.is_file():
      continue
    rel = src.relative_to(PROJECT_TEMPLATES)
    # pip byte-compiles the template conanfile.py on install, planting a
    # binary __pycache__ INSIDE the template tree — never scaffold it
    if "__pycache__" in rel.parts:
      continue
    if skip_hello and rel.parts[:2] == ("sources", "hello"):
      continue
    rel = Path(*[_RENAMES.get(part, part) for part in rel.parts])
    # The example module lands UNDER a directory named for the package,
    # because that level is what exported headers are qualified by:
    # sources/<name>/hello/hello.hpp ships as include/<name>/hello/hello.hpp,
    # the same string #include <<name>/hello/hello.hpp> resolves to in-tree.
    # A scaffold without it would teach a shape whose headers collide with
    # every other package's on a consumer's include path.
    if rel.parts[:2] == ("sources", "hello"):
      rel = Path("sources", name, *rel.parts[1:])
    dst = root / rel
    if dst.exists():
      print(f"  {rel} exists — kept")
      continue
    text = src.read_text(encoding="utf-8")
    for token, value in subs.items():
      text = text.replace(token, value)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text, encoding="utf-8")
    print(f"  {rel} written")
  if skip_hello:
    print("  sources/ already exists — example hello module skipped")


def _settings_wizard(a) -> dict[str, str]:
  """The prompted settings, and the remote env the run will write.

  Interactive when it can be: creating a toml at a real terminal with
  nothing specified on the line asks for the key settings, defaults in
  brackets, enter accepts -- run init and you are ready to go. Every
  non-tty caller (CI, install --no-init'd siblings, tests) gets exactly
  the flag/default behaviour; prompting a pipeline would hang it.
  """
  flagged = ({"CONAN_REMOTE_URL": a.conan_remote,
              "CONAN_REMOTE_NAME": a.conan_remote_name or "conancenter"}
             if a.conan_remote else {})
  if ((Path.cwd() / "buildutil.toml").exists() or not sys.stdin.isatty()
      or a.name != Path.cwd().name or a.cmake_prefix or a.module_prefix):
    return flagged
  entered = input(f"project name [{a.name}]: ").strip()
  if entered:
    a.name = entered
  default_prefix = _prefix(a.name)
  entered = input(f"cmake option prefix [{default_prefix}]: ").strip()
  a.cmake_prefix = entered or default_prefix
  entered = input(f"module define prefix [{a.cmake_prefix}]: ").strip()
  a.module_prefix = entered or a.cmake_prefix
  return flagged or _remote_wizard()


def main(argv: list[str]) -> None:
  ap = argparse.ArgumentParser(
    prog="buildutil init",
    description="mark this directory as a buildutil project, deposit "
                "the base cmake machinery, and scaffold a buildable "
                "hello-world tree (CMakeLists + sources/ with "
                "GoogleTest and google-benchmark wired + conanfile + "
                ".gitignore + an example hello module)")
  ap.add_argument("--name", default=Path.cwd().name,
                  help="project name (default: directory name)")
  ap.add_argument("--cmake-prefix", default="",
                  help="cmake option prefix (default: NAME upper-cased)")
  ap.add_argument("--module-prefix", default="",
                  help="module define prefix (default: same as "
                       "--cmake-prefix; bossdeux uses the shorter BDX)")
  ap.add_argument("--bare", action="store_true",
                  help="write buildutil.toml and render the machinery "
                       "only — no CMakeLists/conanfile/example module")
  ap.add_argument("--no-agents", action="store_true",
                  help="do not install the buildutil skill for coding "
                       "agents detected at the project root")
  ap.add_argument("--package", choices=("library", "application"),
                  default=None,
                  help="configure this project to ship as a conan "
                       "package of this kind (skips the wizard)")
  ap.add_argument("--no-package", action="store_true",
                  help="never ask about conan packaging")
  ap.add_argument("--package-name", default="",
                  help="conan package name (default: the project name)")
  ap.add_argument("--conan-remote", default="", metavar="URL",
                  help="conan remote for this project, written as "
                       "CONAN_REMOTE_URL into the gitignored .env. "
                       "Credentials have no flag: the prompted run asks "
                       "for them, CI passes CONAN_REMOTE_USER/PASS in "
                       "the environment")
  ap.add_argument("--conan-remote-name", default="", metavar="NAME",
                  help="name to register that remote under (default: "
                       "conancenter, so the mirror replaces the public "
                       "default)")
  a = ap.parse_args(argv)

  remote_env = _settings_wizard(a)

  cmake_prefix = a.cmake_prefix or _prefix(a.name)
  module_prefix = a.module_prefix or cmake_prefix

  root = Path.cwd()
  toml = root / "buildutil.toml"
  export_headers = False        # a project being CREATED never opts in
  if toml.exists():
    print(f"{toml.name} exists — keeping it (root stays {root})")
    import tomllib
    raw = tomllib.loads(toml.read_text())
    proj = raw.get("project", {})
    a.name = proj.get("name", a.name)
    cmake_prefix = proj.get("cmake_option_prefix", cmake_prefix)
    module_prefix = proj.get("module_define_prefix", module_prefix)
    # init re-renders the deposit, so it owes the existing toml the same
    # [cmake] knobs a build would honour -- config.py cannot be used here
    # (it computes at import, against the cwd of whoever imported it)
    export_headers = bool(
      raw.get("cmake", {}).get("export_module_headers", False))
  else:
    run_section = (
      "" if a.bare else
      "\n[run]\n"
      '# `buildutil run` builds + launches this installed binary\n'
      'default = "hello"\n')
    toml.write_text(
      "[project]\n"
      f'name = "{a.name}"\n'
      f'cmake_option_prefix = "{cmake_prefix}"\n'
      f'module_define_prefix = "{module_prefix}"\n'
      "\n"
      "# [venv]\n"
      '# extra_deps = ["pybind11==3.0.4"]\n'
      "\n"
      "# [coverage]\n"
      '# bridge_dirs = ["sources/mybridge"]\n'
      "\n"
      "[cmake]\n"
      "# opt-in machinery beyond the buildutil.cmake base (buildutil extend)\n"
      "extensions = []\n"
      + run_section)
    print(f"wrote {toml.name} (name={a.name}, cmake={cmake_prefix}, "
          f"modules={module_prefix})")

  if not a.bare:
    _scaffold(root, a.name, cmake_prefix, module_prefix)
    # the repo-root `./buildutil` shortcut — venv-backed, works from a
    # fresh clone whether or not the project also vendors the package
    from .installcmd import write_launcher
    write_launcher(root)

  _write_env(root, remote_env)

  # ---- conan packaging: the committed [package] choice ----
  # An explicit --package wins; otherwise, at a tty and with no choice
  # on record (kind "none" IS a recorded choice), the wizard asks. The
  # answer is committed to buildutil.toml — what a project ships as is
  # a fact about the project, and the conanfile reads the same section.
  # Re-running init on an old project is the UPGRADE path: same wizard,
  # same switches, plus a conanfile regeneration when the recipe
  # predates packaging (old copy kept as conanfile.py.bak).
  import tomllib as _tomllib
  recorded_kind = _tomllib.loads(
    toml.read_text()).get("package", {}).get("kind", "")
  kind = pkg_name = ""
  if a.package:
    kind = a.package
    pkg_name = a.package_name or _conan_name(a.name)
  elif (not a.no_package and not recorded_kind and sys.stdin.isatty()):
    kind, pkg_name = _package_wizard(_conan_name(a.name))
  if kind:
    if recorded_kind:
      print(f"[package] already configured (kind = {recorded_kind}) — "
            "kept; edit buildutil.toml to change it")
    else:
      with toml.open("a", encoding="utf-8") as f:
        f.write(_package_toml_section(kind, pkg_name))
      print(f"[package] kind = {kind} recorded in {toml.name}")
      recorded_kind = kind
  from .packaging import host_requires
  if recorded_kind in ("library", "application") or host_requires(root):
    _upgrade_conanfile(root, a.name, cmake_prefix)
  if recorded_kind in ("library", "application"):
    section = _tomllib.loads(toml.read_text()).get("package", {})
    _scaffold_package(root, recorded_kind,
                      section.get("name") or _conan_name(a.name))

  # agents present at the root (a .claude/, .codex/, ...) get the
  # buildutil skill so they drive the project correctly from turn one
  if not a.no_agents:
    from . import skillcmd
    skillcmd.auto_install(root)

  cfg = {"cmake_option_prefix": cmake_prefix,
         "module_define_prefix": module_prefix,
         "export_module_headers": export_headers,
         # a library-kind package ships its static archives (deposit
         # renders the flag); builds re-render from config.PROJECT with
         # the same key, so init and build agree
         "package_kind": recorded_kind}
  # the cmake MACHINERY never lands in the project tree: it renders into
  # the gitignored runtime dir and the driver hands it to cmake at
  # configure time — the scaffold above is project-OWNED starting code
  # (yours to edit), the machinery is derived and self-refreshing
  out = deposit.ensure(root, cfg)
  print(f"cmake machinery renders at {out} (gitignored, derived)")
  print("extensions on request: buildutil extend "
        f"[{'|'.join(deposit.extensions())}]")
  if not a.bare:
    print("next: ./buildutil build    (first run bootstraps _pyvenv)\n"
          "      ./buildutil test\n"
          "      ./buildutil bench    (benches build behind --include-bench)")


if __name__ == "__main__":
  sys.exit(main(sys.argv[1:]))
