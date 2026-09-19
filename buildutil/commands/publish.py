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
  release: bool = typer.Option(None, "--release/--debug",
                               help="Publish ONLY this configuration. "
                                    "The default is both, because a "
                                    "consumer on the other one resolves "
                                    "no binary at all."),
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

  BOTH configurations are published under one version and one build
  number: a Debug consumer of a Release-only package computes a
  different package_id, finds no binary and ends in the recipe's
  source-build refusal. --release or --debug narrows the run to that
  one configuration and says which consumers it leaves uncovered.

  The version is DERIVED, never committed: base = the last git tag
  that is a valid semver, plus a build number bumped on every publish
  (--no-version-autoincrement holds it; --version overrides outright;
  no tag and no terminal to ask at is an error).

  Needs [package] in buildutil.toml (run `buildutil init` for the
  wizard) and — unless --no-upload — a configured conan remote; with
  neither there is nothing to publish, and that is an error, not a
  skip."""
  _enter(conan_home)
  _refuse_unpackaged_project()
  _refuse_version_ranges(allow_version_ranges)
  pkg_version, commit_version = packaging.resolve_version(
    bump=not no_autoincrement, override=version)
  reference = packaging.ref(pkg_version)
  build_types = (["Release", "Debug"] if release is None
                 else ["Release"] if release else ["Debug"])
  if not no_upload:
    _require_remote(reference)

  _select_compiler(compiler, "auto")
  tested = packaging.has_package_test()
  if not tested:
    typer.echo("no test_package/ — packaging UNTESTED (run `buildutil "
               "init` to scaffold the package test)")
  staged = _stage_bake(bake_buildutil)
  try:
    for build_type in build_types:
      _publish_one(pkg_version, build_type, tested=tested)
  finally:
    _discard_bake(staged)
  if no_upload:
    typer.echo(f"--no-upload: {reference} packaged and tested in the "
               "local cache, nothing pushed (no build number consumed)")
    _status(f"OK — {reference} (not uploaded)")
    return
  packaging.upload(pkg_version)
  _note_uncovered_configuration(build_types, reference)
  # the build number is consumed by a SUCCESSFUL publish, nothing else
  commit_version()
  # dependency binaries ride along, same cache-warming default as build
  _upload_to_remote()
  _status(f"PUBLISHED — {reference}")


def _refuse_unpackaged_project() -> None:
  if packaging.configured():
    return
  typer.echo(
    "buildutil publish: this project has no [package] section in "
    "buildutil.toml — run `buildutil init` (wizard) or `buildutil "
    "init --package=library|application` to configure packaging.",
    err=True)
  raise typer.Exit(code=2)


def _refuse_version_ranges(allowed: bool) -> None:
  ranged = packaging.ranged_runtime_requires()
  if not ranged or allowed:
    return
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


def _require_remote(reference: str) -> None:
  from .. import bootstrap
  if bootstrap.conan_remote_env()[1]:
    return
  typer.echo(
    "buildutil publish: no conan remote configured (CONAN_REMOTE_URL "
    "/ .env / CI_ARTIFACTORY_*) — there is nowhere to publish "
    f"{reference} to. Configure the remote, or pass --no-upload for "
    "a local dry-run.", err=True)
  raise typer.Exit(code=2)


def _stage_bake(bake_buildutil: bool):
  """--bake-buildutil: the copy only has to exist while conan snapshots
  exports_sources — an unvendored project gets it STAGED for exactly
  that window and discarded again, so the author is never forced into
  `buildutil install`. A vendored project's own committed .buildutil/
  ships as is, untouched."""
  if not bake_buildutil:
    return None
  from .. import installcmd
  if (REPO_ROOT / ".buildutil" / "buildutil" / "__main__.py").is_file():
    typer.echo("bake: project is vendored — its committed .buildutil/ "
               "ships in the package as is")
    return None
  typer.echo("bake: buildutil staged at .buildutil/ for the export "
             "only; removed again after packaging (run `buildutil "
             "install` if you want it vendored for real)")
  return installcmd.stage_copy(REPO_ROOT)


def _discard_bake(staged) -> None:
  if staged is None:
    return
  from .. import installcmd
  installcmd.discard_staged(staged)


def _publish_one(pkg_version: str, build_type: str, *, tested: bool) -> None:
  """One configuration into the cache: build it, export the built tree
  as this version's binary for that configuration, and consume it the
  way a consumer would. Both trees carry the SAME version — they differ
  in package_id, which is what a consumer resolves on."""
  settings = _detect_settings(build_type)
  profile = _ensure_profile(settings)
  build_dir = Path("_build") / _profile_name(settings)
  typer.echo(f"publishing {packaging.ref(pkg_version)} "
             f"({_profile_name(settings)})")
  _conan_install(profile)
  _cmake_configure(build_dir, build_type, tests=True, bench=False)
  _cmake_build(build_dir)
  host, build_prof = _host_build_profiles(profile)
  shared = packaging.shared_requested()
  packaging.export_pkg(pkg_version, host, build_prof, shared=shared)
  if tested:
    packaging.run_package_test(pkg_version, host, build_prof, shared=shared)


def _note_uncovered_configuration(build_types: list[str],
                                  reference: str) -> None:
  """A narrowed publish leaves the other configuration with no binary
  at all, and the failure that causes surfaces at CONSUME time, where
  the cause is far harder to see. Say it here."""
  if len(build_types) > 1:
    return
  published = build_types[0]
  other = "Debug" if published == "Release" else "Release"
  typer.echo(
    f"note: only build_type={published} binaries were uploaded — "
    f"{other} consumers of {reference} have no binary. To cover them: "
    f"buildutil publish --{other.lower()} --no-version-autoincrement")
