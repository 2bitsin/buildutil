"""buildutil commands: publish — build, package, test, upload."""
from __future__ import annotations

import subprocess
from pathlib import Path

import typer

from ..app import app, _compiler_option
from ..engine import *
from .. import packaging


@app.command()
def publish(
  release: bool = typer.Option(True, "--release/--debug",
                               help="Build type to publish (Release is "
                                    "the default and the norm)."),
  version: str = typer.Option(
    "", "--version",
    help="Publish exactly this version, overriding the derivation "
         "(last semver git tag + build number). Never persisted — the "
         "version never lives in the package source."),
  no_autoincrement: bool = typer.Option(
    False, "--no-version-autoincrement",
    help="Do not bump the build number — republish the current one."),
  no_upload: bool = typer.Option(
    False, "--no-upload",
    help="Stop after the package test — build, export and verify the "
         "package but push nothing (a publish dry-run against the "
         "local cache). Consumes no build number."),
  allow_version_ranges: bool = typer.Option(
    False, "--allow-version-ranges",
    help="Publish even though runtime Require()s carry version ranges. "
         "Consumers re-resolve ranges against their own caches, so any "
         "drift from the publish-time resolution changes the package_id "
         "and the published binary is never found — pin exact versions "
         "unless you accept that."),
  bake_buildutil: bool = typer.Option(
    False, "--bake-buildutil",
    help="Ship the build driver inside the package, so a consumer's "
         "--build=missing builds it from source in the conan cache "
         "instead of hitting the recipe's refusal. A vendored project "
         "(buildutil install) ships its committed .buildutil/ as is; "
         "any other project gets a copy STAGED for the export and "
         "discarded right after — publishing a self-building package "
         "never forces vendoring into the working tree."),
  conan_home: str = typer.Option(
    None, "--conan-home",
    help="Override the CONAN_HOME path."),
  compiler: str = _compiler_option(),
):
  """Publish this project's conan package: build, package the built
  tree into the cache (export-pkg), run test_package/ against it, then
  upload recipe + binaries to the project remote.

  The version is DERIVED, never committed: base = the last git tag
  that is a valid semver, plus a build number bumped on every publish
  (--no-version-autoincrement holds it; --version overrides outright;
  no tag and no terminal to ask at is an error).

  Needs [package] in buildutil.toml (run `buildutil init` for the
  wizard) and — unless --no-upload — a configured conan remote; with
  neither there is nothing to publish, and that is an error, not a
  skip."""
  _enter(conan_home)
  if not packaging.configured():
    typer.echo(
      "buildutil publish: this project has no [package] section in "
      "buildutil.toml — run `buildutil init` (wizard) or `buildutil "
      "init --package=library|application` to configure packaging.",
      err=True)
    raise typer.Exit(code=2)
  ranged = packaging.ranged_runtime_requires()
  if ranged and not allow_version_ranges:
    lines = "\n".join(f'  Require({name} VERSION "{ver}" ...)'
                      for name, ver in ranged)
    typer.echo(
      "buildutil publish: runtime dependencies carry version RANGES:\n"
      f"{lines}\n"
      "The published binary embeds today's resolution of each range, but "
      "every consumer re-resolves it against their own cache and remotes "
      "— any drift computes a DIFFERENT package_id, the binary is never "
      "found, and the consumer ends in the recipe's source-build "
      "refusal. Pin these Require()s to exact versions (the resolutions "
      "`buildutil build` just used), or pass --allow-version-ranges to "
      "accept the risk.", err=True)
    raise typer.Exit(code=2)
  pkg_version, commit_version = packaging.resolve_version(
    bump=not no_autoincrement, override=version)
  reference = packaging.ref(pkg_version)
  if not no_upload:
    from .. import bootstrap
    _, url, _, _ = bootstrap.conan_remote_env()
    if not url:
      typer.echo(
        "buildutil publish: no conan remote configured (CONAN_REMOTE_URL "
        "/ .env / CI_ARTIFACTORY_*) — there is nowhere to publish "
        f"{reference} to. Configure the remote, or pass --no-upload for "
        "a local dry-run.", err=True)
      raise typer.Exit(code=2)

  _select_compiler(compiler, "auto")
  build_type = "Release" if release else "Debug"
  settings = _detect_settings(build_type)
  profile = _ensure_profile(settings)
  build_dir = Path("_build") / _profile_name(settings)
  typer.echo(f"publishing {reference} ({_profile_name(settings)})")

  _conan_install(profile)
  _cmake_configure(build_dir, build_type, tests=True, bench=False)
  _cmake_build(build_dir)

  host, build_prof = _host_build_profiles(profile)
  shared = packaging.shared_requested()
  # --bake-buildutil: the copy only has to exist while conan snapshots
  # exports_sources (export-pkg below) — an unvendored project gets it
  # STAGED for exactly that window and discarded again, so the author
  # is never forced into `buildutil install`. A vendored project's own
  # committed .buildutil/ ships as is, untouched.
  staged = None
  if bake_buildutil:
    from .. import installcmd
    if (REPO_ROOT / ".buildutil" / "buildutil" / "__main__.py").is_file():
      typer.echo("bake: project is vendored — its committed .buildutil/ "
                 "ships in the package as is")
    else:
      staged = installcmd.stage_copy(REPO_ROOT)
      typer.echo("bake: buildutil staged at .buildutil/ for the export "
                 "only; removed again after packaging (run `buildutil "
                 "install` if you want it vendored for real)")
  try:
    packaging.export_pkg(pkg_version, host, build_prof, shared=shared)
  finally:
    if staged is not None:
      from .. import installcmd
      installcmd.discard_staged(staged)
  if packaging.has_package_test():
    packaging.run_package_test(pkg_version, host, build_prof, shared=shared)
  else:
    typer.echo("no test_package/ — packaged UNTESTED (run `buildutil "
               "init` to scaffold the package test)")
  if no_upload:
    typer.echo(f"--no-upload: {reference} packaged and tested in the "
               "local cache, nothing pushed (no build number consumed)")
    _status(f"OK — {reference} (not uploaded)")
    return
  packaging.upload(pkg_version)
  # Surface the single-build_type reality at PUBLISH time: only
  # the build_type this run used was uploaded, so consumers on the other
  # profile have no binary and will hit the recipe's no-binary refusal
  # at consume time — where the cause is far harder to see.
  other = "Debug" if release else "Release"
  typer.echo(
    f"note: only build_type={build_type} binaries were uploaded — "
    f"{other} consumers of {reference} have no binary. To cover them: "
    f"buildutil publish {'--debug' if release else '--release'} "
    "--no-version-autoincrement")
  # the build number is consumed by a SUCCESSFUL publish, nothing else
  commit_version()
  # dependency binaries ride along, same cache-warming default as build
  _upload_to_remote()
  _status(f"PUBLISHED — {reference}")
