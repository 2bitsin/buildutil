"""[runtime] and [bundle.macos]: the declarations, the plist generator,
and the runtime payload end to end against a FAKE dependency.

The point of the fake dependency is that it is the whole contract. If a
package publishing `<DEP>_RUNTIME_*_DIR` global properties (and,
optionally, `<dep>_copy_runtime()`) gets its files beside the binary in
both trees, then buildutil never has to know the word CEF -- and the
next package with a runtime payload adopts the same four property names
and works without a line of buildutil changing.

The macOS half can only be asserted structurally here: a bundle is a
macOS artefact and this box is Linux. What IS checked is that the
declaration parses, that the plist generator writes the right document,
and that the whole feature is inert off macOS.
"""
import plistlib
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from buildutil import bundle, deposit
from buildutil.config import _bundle_macos, _load_project

PYSUPPORT = Path(deposit.__file__).resolve().parent / "pysupport"

e2e = pytest.mark.skipif(
  shutil.which("cmake") is None or shutil.which("ninja") is None,
  reason="needs cmake and ninja")


def project(text: str, tmp_path, monkeypatch):
  (tmp_path / "buildutil.toml").write_text(text)
  monkeypatch.setenv("BUILDUTIL_ROOT", str(tmp_path))
  import buildutil.config as config
  monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
  monkeypatch.setattr(config, "HAVE_PROJECT", True)
  return _load_project()


# ---------------------------------------------------- [runtime] parsing --

def test_runtime_defaults_to_nothing(tmp_path, monkeypatch):
  cfg = project('[project]\nname = "demo"\n', tmp_path, monkeypatch)
  assert cfg["runtime_from"] == [] and cfg["runtime_dirs"] == []


def test_runtime_reads_its_three_keys(tmp_path, monkeypatch):
  cfg = project('[runtime]\nfrom = ["cef"]\ntargets = ["app"]\n'
                'dirs = ["contrib/blobs"]\n', tmp_path, monkeypatch)
  assert cfg["runtime_from"] == ["cef"]
  assert cfg["runtime_targets"] == ["app"]
  assert cfg["runtime_dirs"] == ["contrib/blobs"]


@pytest.mark.parametrize("text, message", [
  ('[runtime]\nwat = 1\n', "unknown key"),
  ('[runtime]\ndirs = ["/etc"]\n', "inside the repo"),
  ('[runtime]\ndirs = ["../x"]\n', "inside the repo"),
])
def test_a_malformed_runtime_names_itself(tmp_path, monkeypatch, text, message):
  with pytest.raises(SystemExit) as raised:
    project(text, tmp_path, monkeypatch)
  assert message in str(raised.value)


# ----------------------------------------------- [bundle.macos] parsing --

BUNDLE = """\
[bundle.macos]
identifier = "com.example.app"
version = "0.1.0"
plist = { LSUIElement = true, LSMinimumSystemVersion = "12.0" }
[bundle.macos.helpers]
module = "xoctet-helper"
variants = ["", "Alerts", "GPU", "Plugin", "Renderer"]
"""


def parsed(text=BUNDLE, name="xoctet"):
  return _bundle_macos(tomllib.loads(text), name)


def test_absent_section_is_no_bundle():
  assert parsed('[project]\nname = "x"\n') == {}


def test_the_module_defaults_to_the_project_name():
  assert parsed()["module"] == "xoctet"


def test_helper_names_and_identifiers_follow_the_rule():
  """The unsuffixed variant is an EMPTY string, which is why the names
  are computed in python: it cannot survive a cmake list."""
  assert parsed()["helpers"] == [
    "xoctet Helper|com.example.app.helper",
    "xoctet Helper (Alerts)|com.example.app.helper.alerts",
    "xoctet Helper (GPU)|com.example.app.helper.gpu",
    "xoctet Helper (Plugin)|com.example.app.helper.plugin",
    "xoctet Helper (Renderer)|com.example.app.helper.renderer",
  ]


def test_plist_values_keep_their_toml_TYPE():
  """A bool is <true/> and a string is <string>; a text template could
  not tell them apart, which is the whole reason for the python step."""
  assert set(parsed()["plist"]) == {
    "LSUIElement=bool:true", "LSMinimumSystemVersion=string:12.0"}


@pytest.mark.parametrize("text, message", [
  ('[bundle.macos]\nversion = "1"\n', "needs `identifier`"),
  ('[bundle.macos]\nidentifier = "a"\nwat = 1\n', "unknown key"),
  ('[bundle.macos]\nidentifier = "a"\n[bundle.macos.helpers]\n'
   'variants = ["GPU"]\n', "but no `module`"),
  ('[bundle.macos]\nidentifier = "a"\nplist = { K = 1.5 }\n',
   "strings, booleans, integers"),
])
def test_a_malformed_bundle_names_itself(text, message):
  with pytest.raises(SystemExit) as raised:
    parsed(text)
  assert message in str(raised.value)


# ------------------------------------------------------ the plist writer --

def test_the_app_plist_carries_what_every_bundle_needs(tmp_path):
  out = tmp_path / "Info.plist"
  assert bundle.main([
    "--output", str(out), "--kind", "app", "--executable", "demo",
    "--identifier", "org.x.demo", "--name", "demo", "--version", "1.2.3",
    "--extra", "LSUIElement=bool:true",
    "--extra", "LSMinimumSystemVersion=string:12.0"]) == 0
  data = plistlib.loads(out.read_bytes())
  assert data["CFBundleExecutable"] == "demo"
  assert data["CFBundleIdentifier"] == "org.x.demo"
  assert data["CFBundleVersion"] == data["CFBundleShortVersionString"] == "1.2.3"
  assert data["CFBundlePackageType"] == "APPL"
  assert data["NSPrincipalClass"] == "NSApplication"
  assert data["LSUIElement"] is True          # a bool, not the string "true"
  assert data["LSMinimumSystemVersion"] == "12.0"


def test_a_helper_plist_hides_from_the_dock_without_being_told(tmp_path):
  out = tmp_path / "Info.plist"
  bundle.main(["--output", str(out), "--kind", "helper",
               "--executable", "demo Helper (GPU)",
               "--identifier", "org.x.demo.helper.gpu",
               "--name", "demo Helper (GPU)"])
  data = plistlib.loads(out.read_bytes())
  assert data["LSUIElement"] is True
  assert data["CFBundleDisplayName"] == "demo Helper (GPU)"
  assert "NSPrincipalClass" not in data


def test_a_project_key_overrides_the_default(tmp_path):
  out = tmp_path / "Info.plist"
  bundle.main(["--output", str(out), "--kind", "helper", "--executable", "d",
               "--identifier", "i", "--name", "n",
               "--extra", "LSUIElement=bool:false"])
  assert plistlib.loads(out.read_bytes())["LSUIElement"] is False


def test_an_unchanged_plist_is_not_restamped(tmp_path):
  out = tmp_path / "Info.plist"
  args = ["--output", str(out), "--kind", "app", "--executable", "d",
          "--identifier", "i", "--name", "n"]
  bundle.main(args)
  stamp = out.stat().st_mtime_ns
  bundle.main(args)
  assert out.stat().st_mtime_ns == stamp


# --------------------------------------------------------- the rendering --

def test_the_declarations_render_into_the_deposit(tmp_path):
  cfg = {"cmake_option_prefix": "D", "module_define_prefix": "D",
         "name": "xoctet",
         "runtime_from": ["cef"], "runtime_targets": [], "runtime_dirs": [],
         "bundle_macos": parsed()}
  machinery = (deposit.ensure(tmp_path, cfg) / "buildutil.cmake").read_text()
  assert '_buildutil_runtime_payload("cef" "" "")' in machinery
  assert '_buildutil_macos_bundle("xoctet" "com.example.app"' \
    in machinery
  assert "@RUNTIME_PAYLOAD@" not in machinery
  assert "@MACOS_BUNDLE@" not in machinery


def test_a_project_declaring_neither_renders_neither(tmp_path):
  machinery = (deposit.ensure(tmp_path, {"cmake_option_prefix": "D",
                                         "module_define_prefix": "D"})
               / "buildutil.cmake").read_text()
  assert "\n_buildutil_runtime_payload(" not in machinery
  assert "\n_buildutil_macos_bundle(" not in machinery


# ---------------------------------------------------------- the payload --

ROOT_CMAKE = """\
cmake_minimum_required(VERSION 3.25)
project(demo CXX)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
add_subdirectory(sources)
"""

# The whole contract a package has to keep, in eight lines. buildutil
# never sees the package's name anywhere but the toml.
FAKE_PACKAGE = """\
set_property(GLOBAL PROPERTY WIDGET_RUNTIME_BINARY_DIR "${CMAKE_SOURCE_DIR}/pkg/bin")
set_property(GLOBAL PROPERTY WIDGET_RUNTIME_RESOURCE_DIR "${CMAKE_SOURCE_DIR}/pkg/res")
set_property(GLOBAL PROPERTY WIDGET_RUNTIME_LIBRARY_DIR "${CMAKE_SOURCE_DIR}/pkg/lib")
"""


def _payload_project(tmp_path, toml, with_function=False):
  (tmp_path / "buildutil.toml").write_text(toml)
  deposit.ensure(tmp_path, {
    "cmake_option_prefix": "D", "module_define_prefix": "D", "name": "demo",
    "runtime_from": tomllib.loads(toml).get("runtime", {}).get("from", []),
    "runtime_targets": [], "runtime_dirs":
      tomllib.loads(toml).get("runtime", {}).get("dirs", [])})
  (tmp_path / "CMakeLists.txt").write_text(ROOT_CMAKE)
  pkg = tmp_path / "pkg"
  for part in ("bin", "res", "lib"):
    (pkg / part).mkdir(parents=True)
  (pkg / "bin" / "helper.dat").write_text("binary payload\n")
  (pkg / "res" / "strings.pak").write_text("resource payload\n")
  (pkg / "lib" / "libwidget.so").write_text("not a real library\n")
  (pkg / "lib" / "libwidget_wrapper.a").write_text("must NOT ship\n")
  fake = FAKE_PACKAGE
  if with_function:
    fake += """\
function(widget_copy_runtime _target)
  add_custom_command(TARGET ${_target} POST_BUILD
    COMMAND "${CMAKE_COMMAND}" -E touch
            "$<TARGET_FILE_DIR:${_target}>/the-package-did-this"
    VERBATIM)
endfunction()
"""
  src = tmp_path / "sources"
  (src / "demo").mkdir(parents=True)
  (src / "CMakeLists.txt").write_text(fake + "Scan_subdirectories()\n")
  (src / "demo" / "CMakeLists.txt").write_text("Init_submodule()\n")
  (src / "demo" / "main.cpp").write_text("int main() { return 0; }\n")
  return tmp_path


def _build_and_install(root, build, prefix):
  configure = subprocess.run(
    ["cmake", "-S", str(root), "-B", str(build), "-G", "Ninja",
     f"-DBUILDUTIL_PY={sys.executable}",
     f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}"], capture_output=True, text=True)
  assert configure.returncode == 0, configure.stdout + configure.stderr
  built = subprocess.run(["cmake", "--build", str(build)],
                         capture_output=True, text=True)
  assert built.returncode == 0, built.stdout + built.stderr
  installed = subprocess.run(
    ["cmake", "--install", str(build), "--prefix", str(prefix)],
    capture_output=True, text=True)
  assert installed.returncode == 0, installed.stdout + installed.stderr
  return configure, built


TOML = '[project]\nname = "demo"\n[runtime]\nfrom = ["widget"]\n'


@e2e
def test_the_payload_lands_in_the_build_tree(tmp_path):
  root = _payload_project(tmp_path, TOML)
  build = tmp_path / "b"
  _build_and_install(root, build, tmp_path / "install")
  beside = build / "bin"
  assert (beside / "helper.dat").is_file()
  assert (beside / "strings.pak").is_file()
  assert (beside / "libwidget.so").is_file()
  assert not (beside / "libwidget_wrapper.a").exists(), \
    "a static archive is not a runtime payload"


@e2e
def test_the_payload_lands_in_the_install_tree(tmp_path):
  """The gap every consumer fell into: the package's own copy function is
  POST_BUILD and only ever touches the build tree, while `buildutil run`
  execs the INSTALLED binary."""
  root = _payload_project(tmp_path, TOML)
  prefix = tmp_path / "install"
  _build_and_install(root, tmp_path / "b", prefix)
  assert (prefix / "helper.dat").is_file()
  assert (prefix / "strings.pak").is_file()
  assert (prefix / "libwidget.so").is_file()
  assert not (prefix / "libwidget_wrapper.a").exists()


@e2e
def test_the_package_function_wins_for_the_build_tree(tmp_path):
  """A package that publishes <dep>_copy_runtime() knows its own layout
  (and sets the rpath while it is there), so buildutil calls it instead
  of copying -- and still owns the install tree."""
  root = _payload_project(tmp_path, TOML, with_function=True)
  build = tmp_path / "b"
  prefix = tmp_path / "install"
  _build_and_install(root, build, prefix)
  assert (build / "bin" / "the-package-did-this").is_file()
  assert not (build / "bin" / "helper.dat").exists(), \
    "buildutil copied the payload itself despite the package's function"
  assert (prefix / "helper.dat").is_file()


@e2e
def test_a_repo_relative_payload_dir_ships_too(tmp_path):
  root = _payload_project(
    tmp_path, '[project]\nname = "demo"\n[runtime]\ndirs = ["extra"]\n')
  (root / "extra").mkdir()
  (root / "extra" / "seed.json").write_text("{}\n")
  build = tmp_path / "b"
  prefix = tmp_path / "install"
  _build_and_install(root, build, prefix)
  assert (build / "bin" / "seed.json").is_file()
  assert (prefix / "seed.json").is_file()


@e2e
def test_the_installed_binary_looks_beside_itself(tmp_path):
  """The flat layout is only a layout if the loader agrees: $ORIGIN has
  to be on the installed rpath, or the binary that ships with its library
  still cannot find it."""
  root = _payload_project(tmp_path, TOML)
  prefix = tmp_path / "install"
  _build_and_install(root, tmp_path / "b", prefix)
  readelf = shutil.which("readelf")
  if not readelf:
    pytest.skip("needs readelf")
  out = subprocess.run([readelf, "-d", str(prefix / "demo")],
                       capture_output=True, text=True).stdout
  assert "$ORIGIN" in out, out


@e2e
def test_a_dependency_with_no_payload_is_refused(tmp_path):
  """A declaration that silently does nothing is the failure every
  presence rule in the machinery exists to prevent."""
  root = _payload_project(tmp_path,
                          '[project]\nname = "demo"\n[runtime]\n'
                          'from = ["nosuchthing"]\n')
  proc = subprocess.run(
    ["cmake", "-S", str(root), "-B", str(tmp_path / "b"), "-G", "Ninja",
     f"-DBUILDUTIL_PY={sys.executable}",
     f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}"], capture_output=True, text=True)
  assert proc.returncode != 0
  assert "'nosuchthing'" in proc.stdout + proc.stderr
  assert "NOSUCHTHING_RUNTIME_" in proc.stdout + proc.stderr


@e2e
def test_a_macos_bundle_declaration_is_inert_off_macos(tmp_path):
  """Declaring a bundle must not change a Linux build in any way -- the
  whole section is APPLE-gated, and a Linux CI run of a macOS project has
  to stay green."""
  root = _payload_project(tmp_path, '[project]\nname = "demo"\n')
  deposit.ensure(root, {"cmake_option_prefix": "D", "module_define_prefix": "D",
                        "name": "demo", "bundle_macos": parsed(name="demo")})
  build = tmp_path / "b"
  _build_and_install(root, build, tmp_path / "install")
  assert (build / "bin" / "demo").is_file()
  assert not (build / "bin" / "demo.app").exists()


@e2e
def test_a_test_executable_gets_the_payload_beside_it_too(tmp_path):
  """A *.test.cpp that starts the thing under test needs exactly what the
  app needs -- and test binaries do not live in <build>/bin, so the app's
  copy is no help. Build tree only: nothing here ships."""
  root = _payload_project(tmp_path, TOML)
  (root / "sources" / "demo" / "probe.test.cpp").write_text(
    "int probe() { return 0; }\n")
  build = tmp_path / "b"
  configure = subprocess.run(
    ["cmake", "-S", str(root), "-B", str(build), "-G", "Ninja",
     "-DBUILD_TESTING=OFF",
     f"-DBUILDUTIL_PY={sys.executable}",
     f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}"], capture_output=True, text=True)
  assert configure.returncode == 0, configure.stdout + configure.stderr
  built = subprocess.run(["cmake", "--build", str(build)],
                         capture_output=True, text=True)
  assert built.returncode == 0, built.stdout + built.stderr


def test_run_launches_a_macos_bundle_with_open():
  """`buildutil run` must not exec the file inside the .app: the plist is
  what names the sub-process bundles, the framework is found relative to
  the bundle, and LSUIElement only applies to a launched app. Asserted on
  the source because the surrounding command is a whole build; what is
  load-bearing is that the branch exists, keys on <binary>.app, and
  reaches `open` rather than the os.execv below it."""
  from buildutil.commands import run as runcmd
  source = Path(runcmd.__file__).read_text()
  assert 'bundle = Path(f"{binary}.app")' in source
  assert '["open", "-W", "-n", str(bundle)]' in source
  assert source.index("open") < source.index("os.execv")
