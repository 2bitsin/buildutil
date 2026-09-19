"""The cross lane images buildutil ships the recipe for.

A lane that only exists as a container someone else built is a lane
nobody can reproduce: the driver's osxcross and wine-msvc support is only
as real as the images it is exercised in. The recipes therefore live in
this repository, and they start from a distribution base and upstream
sources -- no inherited image from another project, which is how the
toolchain, the standard library and the clang the reflect extension needs
became facts nobody could check.
"""
from pathlib import Path

import pytest

IMAGES = Path(__file__).resolve().parents[2] / "images"

LANES = ("osxcross", "msvc-wine")


def _dockerfile(lane: str) -> str:
  return (IMAGES / lane / "Dockerfile").read_text(encoding="utf-8")


@pytest.mark.parametrize("lane", LANES)
def test_every_lane_ships_a_recipe(lane):
  assert (IMAGES / lane / "Dockerfile").is_file()


@pytest.mark.parametrize("lane", LANES)
def test_a_lane_image_starts_from_a_distribution(lane):
  """Self-contained: the FROM is a public distribution image, not
  another project's container off a private registry."""
  froms = [line.split()[1] for line in _dockerfile(lane).splitlines()
           if line.startswith("FROM ")]
  assert froms, "no FROM in the recipe"
  for base in froms:
    assert base.startswith("ubuntu:"), base


@pytest.mark.parametrize("lane", LANES)
def test_a_lane_image_carries_the_reflect_parser(lane):
  """reflect parses headers with libclang, and the pip wheel ships the
  library without clang's builtin headers -- so a lane image with no
  clang cannot run the extension at all, whatever include paths it is
  given. That is not a driver bug to work around; it is an image that
  does not carry its toolchain."""
  assert "clang" in _dockerfile(lane)


def test_the_osxcross_recipe_names_its_one_undownloadable_input():
  """Apple's licence does not allow redistributing the SDK and
  gen_sdk_package.sh only runs on a Mac, so the tarball is an input the
  recipe has to ask for by name rather than fetch."""
  recipe = _dockerfile("osxcross")
  assert "tarballs/" in recipe
  assert (IMAGES / "osxcross" / ".gitignore").read_text().strip() == "tarballs/"


def test_the_osxcross_recipe_carries_lld():
  """osxcross's cctools ld64 does not synthesise clang's arm64
  objc_msgSend selector stubs; every link on that lane asks for lld."""
  assert "lld" in _dockerfile("osxcross")


def test_the_msvc_wine_recipe_carries_wine_and_the_wrappers():
  recipe = _dockerfile("msvc-wine")
  assert "wine" in recipe
  assert "msvc-wine" in recipe and "vsdownload.py" in recipe
