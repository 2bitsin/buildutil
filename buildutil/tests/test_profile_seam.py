"""The generated conan profile carries the PROJECT's dep policy, not
the driver's: [conan] options / options_<os> / conf from buildutil.toml.
Pre-scrub the profile hardcoded bossdeux's SDL3 backend options (every
Linux project silently inherited a headless SDL) and a foonathan-lexy
warning suppression."""
import pytest

from buildutil import engine


GCC = {"arch": "x86_64", "os": "Linux", "compiler": "gcc",
       "compiler.version": "14", "compiler.cppstd": "26",
       "compiler.libcxx": "libstdc++11", "build_type": "Debug"}


@pytest.fixture
def in_tmp(tmp_path, monkeypatch):
  monkeypatch.chdir(tmp_path)
  return tmp_path


def _project(monkeypatch, **overrides):
  base = {"conan_options": [], "conan_options_os": {}, "conan_conf": []}
  base.update(overrides)
  patched = dict(engine.config.PROJECT)
  patched.update(base)
  monkeypatch.setattr(engine.config, "PROJECT", patched)


def test_bare_project_gets_a_policy_free_profile(in_tmp, monkeypatch):
  _project(monkeypatch)
  text = engine._ensure_profile(dict(GCC)).read_text()
  assert "[options]" not in text
  assert "sdl/" not in text and "lexy" not in text
  assert "tools.cmake.cmaketoolchain:generator=Ninja" in text


def test_toml_options_and_conf_land(in_tmp, monkeypatch):
  _project(monkeypatch,
           conan_options=["mypkg/*:shared=False"],
           conan_options_os={"linux": ["sdl/*:x11=False"]},
           conan_conf=["dep/*:tools.build:cxxflags+=['-Wno-error']"])
  text = engine._ensure_profile(dict(GCC)).read_text()
  options_at = text.index("[options]")
  assert "mypkg/*:shared=False" in text[options_at:]
  assert "sdl/*:x11=False" in text[options_at:]
  assert "dep/*:tools.build:cxxflags+=['-Wno-error']" in text


def test_os_scoped_options_skip_other_platforms(in_tmp, monkeypatch):
  _project(monkeypatch, conan_options_os={"windows": ["pkg/*:gui=True"]})
  text = engine._ensure_profile(dict(GCC)).read_text()
  assert "pkg/*:gui=True" not in text


MACOS = {"arch": "armv8", "os": "Macos", "compiler": "apple-clang",
         "compiler.version": "16", "compiler.cppstd": "26",
         "compiler.libcxx": "libc++", "build_type": "Release"}


@pytest.fixture
def osxcross(monkeypatch):
  """The cross lane, without the container: the four probes the osxcross
  branch of _ensure_profile makes, answered with this image's real
  shapes."""
  monkeypatch.setattr(engine, "_osxcross_live", lambda: True)
  monkeypatch.setattr(engine, "_wine_msvc_live", lambda: False)
  monkeypatch.setattr(engine, "_osxcross_conf",
                      lambda: {"OSXCROSS_SDK": "/opt/osxcross/target/SDK/MacOSX26.0.sdk"})
  monkeypatch.setattr(engine.shutil, "which",
                      lambda name: f"/opt/osxcross/target/bin/{name}")
  monkeypatch.setattr(
    engine, "_osxcross_tool",
    lambda name: f"/opt/osxcross/target/bin/aarch64-apple-darwin25-{name}")


def test_the_osxcross_profile_names_the_darwin_archiver(in_tmp, monkeypatch,
                                                        osxcross):
  # without these, a dependency built in that container archives with
  # the host GNU ar and ld64 IGNORES the result -- silently for a lane that
  # links nothing, loudly (openssl's fips.dylib) for one that does. Both
  # seams, because a cmake-built dep reads CMAKE_AR out of the toolchain
  # conan generates and an autotools one reads AR out of the environment.
  _project(monkeypatch)
  text = engine._ensure_profile(dict(MACOS)).read_text()

  assert ("tools.cmake.cmaketoolchain:extra_variables="
          "{'CMAKE_AR': '/opt/osxcross/target/bin/aarch64-apple-darwin25-ar', "
          "'CMAKE_RANLIB': "
          "'/opt/osxcross/target/bin/aarch64-apple-darwin25-ranlib'") in text
  assert text.count("tools.cmake.cmaketoolchain:extra_variables=") == 1

  buildenv_at = text.index("[buildenv]")
  assert "AR=/opt/osxcross/target/bin/aarch64-apple-darwin25-ar" \
         in text[buildenv_at:]
  assert "RANLIB=/opt/osxcross/target/bin/aarch64-apple-darwin25-ranlib" \
         in text[buildenv_at:]


def test_the_osxcross_profile_names_the_install_name_tool(in_tmp, monkeypatch,
                                                          osxcross):
  # A dependency that enables Objective-C (SDL does) makes cmake search
  # for install_name_tool AFTER Platform/Darwin is read, and hard-error
  # when the guessed prefix finds nothing. The driver's own configure
  # names it; the dependency profile has to as well.
  _project(monkeypatch)
  text = engine._ensure_profile(dict(MACOS)).read_text()

  assert ("'CMAKE_INSTALL_NAME_TOOL': '/opt/osxcross/target/bin/"
          "aarch64-apple-darwin25-install_name_tool'") in text


def test_a_missing_install_name_tool_keeps_the_archiver(in_tmp, monkeypatch,
                                                        osxcross, capsys):
  # Not part of the archiver's all-or-nothing gate: ar and ranlib must
  # agree with each other, install_name_tool answers to nothing.
  _project(monkeypatch)
  monkeypatch.setattr(engine, "_osxcross_tool",
                      lambda name: None if name == "install_name_tool"
                      else f"/opt/osxcross/target/bin/aarch64-apple-darwin25-{name}")
  text = engine._ensure_profile(dict(MACOS)).read_text()

  assert "CMAKE_AR" in text and "CMAKE_INSTALL_NAME_TOOL" not in text
  assert "no install_name_tool" in capsys.readouterr().err


def test_buildenv_comes_after_every_conf_line(in_tmp, monkeypatch, osxcross):
  # A section header in the middle of [conf] swallows every conf line
  # after it -- including the project's own, which are appended late.
  _project(monkeypatch, conan_conf=["dep/*:tools.build:cxxflags+=['-Wno-x']"])
  text = engine._ensure_profile(dict(MACOS)).read_text()
  assert text.index("[conf]") < text.index("dep/*:tools.build:cxxflags") \
         < text.index("[buildenv]")


def test_a_native_profile_grows_no_buildenv(in_tmp, monkeypatch):
  # The archiver is a CROSS fact. A native build's ar is the right one
  # already, and pinning a path into every profile would be a new way to
  # be wrong on someone else's machine.
  _project(monkeypatch)
  assert "[buildenv]" not in engine._ensure_profile(dict(GCC)).read_text()


def test_half_an_archiver_is_refused_out_loud(in_tmp, monkeypatch, osxcross,
                                              capsys):
  """Both or neither -- ar and ranlib must agree on the archive format --
  but a SILENT neither is how the whole failure class hides: the
  dependencies quietly go back to the host's ar and ld64 ignores what it
  writes rather than erroring."""
  _project(monkeypatch)
  monkeypatch.setattr(engine, "_osxcross_tool",
                      lambda name: None if name == "ranlib"
                      else "/opt/osxcross/target/bin/aarch64-apple-darwin25-ar")
  text = engine._ensure_profile(dict(MACOS)).read_text()

  assert "CMAKE_AR" not in text and "[buildenv]" not in text
  warning = capsys.readouterr().err
  assert "no ranlib" in warning and "ld64" in warning
