"""A shared module's soversion and full version, declared by its configure.py (#129)."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from buildutil import config, deposit

CFG = {"cmake_option_prefix": "ACME", "module_define_prefix": "ACM"}
PYSUPPORT = Path(deposit.__file__).resolve().parent / "pysupport"

e2e = pytest.mark.skipif(
  not sys.platform.startswith("linux") or shutil.which("readelf") is None
  or shutil.which("cmake") is None or shutil.which("ninja") is None
  or not (shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")),
  reason="needs Linux, readelf, cmake, ninja and a C++ compiler")

ROOT_CMAKE = """\
cmake_minimum_required(VERSION 3.25)
project(soversion CXX)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
add_subdirectory(sources)
"""

CORE_IMPL = """\
__attribute__((visibility("default"))) int core_answer() { return 42; }
"""

TOOL_MAIN = """\
extern int core_answer();
int main() { return core_answer() == 42 ? 0 : 1; }
"""


def _hook(call):
  return f"import buildutil_configure as bc\nbc.{call}\n"


def _run_hook(tmp_path, call):
  script = tmp_path / "configure.py"
  script.write_text(_hook(call))
  env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(PYSUPPORT),
         "CONFIGURE_OUTPUT_DIR": str(tmp_path / "out"),
         "CONFIGURE_SHARED_DIR": str(tmp_path / "shared"),
         "CONFIGURE_MANIFEST": str(tmp_path / "manifest"), "CONFIGURE_MODULE": "core"}
  return subprocess.run([sys.executable, str(script)], cwd=tmp_path, capture_output=True,
                        text=True, env=env)


@pytest.mark.parametrize("call,lines", [
  ("soversion(0)", ["S 0"]),
  ('soversion(3, version="3.4.8")', ["S 3", "V 3.4.8"]),
  ('soversion(1, version="1.2")', ["S 1", "V 1.2"]),
])
def test_the_hook_writes_the_soversion_to_the_manifest(tmp_path, call, lines):
  done = _run_hook(tmp_path, call)
  assert done.returncode == 0, done.stderr
  assert (tmp_path / "manifest").read_text().splitlines() == lines


@pytest.mark.parametrize("call,message", [
  ('soversion("0")', "soversion\\('0'\\) takes a non-negative integer"),
  ("soversion(-1)", "soversion\\(-1\\) takes a non-negative integer"),
  ("soversion(True)", "soversion\\(True\\) takes a non-negative integer"),
  ('soversion(0, version="v3.4")', "one to three dotted numbers"),
  ('soversion(0, version="3.4.8.1")', "one to three dotted numbers"),
])
def test_a_malformed_soversion_is_refused_by_the_hook(tmp_path, call, message):
  done = _run_hook(tmp_path, call)
  assert done.returncode != 0
  assert __import__("re").search(message, done.stderr), done.stderr


@pytest.mark.parametrize("key", ["soversion = 0", 'version = "1.0"'])
def test_the_old_toml_key_names_where_it_moved(tmp_path, monkeypatch, key):
  monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
  monkeypatch.setattr(config, "HAVE_PROJECT", True)
  (tmp_path / "buildutil.toml").write_text(f"[modules.core]\n{key}\n")
  with pytest.raises(SystemExit, match="moved to the module's configure.py: soversion"):
    config._load_project()


def _tree(root, modules):
  deposit.ensure(root, CFG)
  (root / "CMakeLists.txt").write_text(ROOT_CMAKE)
  (root / "sources").mkdir()
  (root / "sources" / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  for directory, files in modules.items():
    module = root / "sources" / directory
    module.mkdir(parents=True)
    for name, text in files.items():
      (module / name).parent.mkdir(parents=True, exist_ok=True)
      (module / name).write_text(text)
  return root


def _configure(root):
  return subprocess.run(["cmake", "-S", str(root), "-B", str(root / "b"), "-G", "Ninja",
                         "-DBUILD_TESTING=OFF", f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}"],
                        capture_output=True, text=True)


def _install(root, directory, call, package=None):
  core = {"CMakeLists.txt": "Init_submodule()\n", "main.so.cpp": CORE_IMPL}
  if call:
    core["configure.py"] = _hook(call)
  library = directory.split("/")[-1] if "/" not in directory else "bp-core"
  tool = {"CMakeLists.txt": f"Init_submodule()\nLink_dependencies({library})\n",
          "main.cpp": TOOL_MAIN}
  _tree(root, {directory: core, "tool": tool})
  if package is not None:
    (root / "_bdudata" / "buildinfo.json").write_text(json.dumps({"package": package}))
  configured = _configure(root)
  assert configured.returncode == 0, configured.stdout + configured.stderr
  for command in (["cmake", "--build", str(root / "b")],
                  ["cmake", "--install", str(root / "b"), "--prefix", str(root / "prefix")]):
    done = subprocess.run(command, capture_output=True, text=True)
    assert done.returncode == 0, " ".join(command) + "\n" + done.stdout + done.stderr
  return root / "prefix"


def _dynamic_section(path):
  return subprocess.run(["readelf", "-d", str(path)], capture_output=True,
                        text=True, check=True).stdout


def _link_chain(directory, *names):
  return [(directory / name).readlink().name for name in names]


@e2e
@pytest.mark.parametrize("directory,mirror", [("core", "."), ("bp/core", "bp")])
def test_the_soname_carries_the_soversion_and_the_mirror_ships_the_links(
    tmp_path, directory, mirror):
  prefix = _install(tmp_path, directory, 'soversion(0, version="3.4.8")')
  shipped = prefix / mirror
  assert "Library soname: [libcore.so.0]" in _dynamic_section(shipped / "libcore.so.3.4.8")
  assert _link_chain(shipped, "libcore.so", "libcore.so.0") == \
    ["libcore.so.0", "libcore.so.3.4.8"]
  assert "Shared library: [libcore.so.0]" in _dynamic_section(prefix / "tool")
  assert subprocess.run([str(prefix / "tool")]).returncode == 0


@e2e
def test_a_soversion_without_a_version_names_the_file_after_it(tmp_path):
  prefix = _install(tmp_path, "core", "soversion(5)")
  assert not (prefix / "libcore.so.5").is_symlink()
  assert _link_chain(prefix, "libcore.so") == ["libcore.so.5"]
  assert "Library soname: [libcore.so.5]" in _dynamic_section(prefix / "libcore.so.5")
  assert subprocess.run([str(prefix / "tool")]).returncode == 0


@e2e
@pytest.mark.parametrize("package,major", [(None, "0"), ("7.1.4", "7")])
def test_a_shared_module_that_declares_none_takes_the_package_major(tmp_path, package, major):
  """No tag, no stamp: the package is 0.0.0, so the soname is libcore.so.0; a 7.x package gives .7."""
  prefix = _install(tmp_path, "core", "", package)
  assert _link_chain(prefix, "libcore.so") == [f"libcore.so.{major}"]
  assert f"Library soname: [libcore.so.{major}]" in _dynamic_section(prefix / f"libcore.so.{major}")
  assert subprocess.run([str(prefix / "tool")]).returncode == 0


@e2e
@pytest.mark.parametrize("directory,files", [
  ("pinned.a", {"impl.cpp": CORE_IMPL}),
  ("plain", {"impl.cpp": CORE_IMPL}),
  ("core", {"impl.cpp": CORE_IMPL, "core.test/main.so.cpp": CORE_IMPL}),
])
def test_a_module_that_does_not_build_shared_is_refused(tmp_path, directory, files):
  _tree(tmp_path, {directory: {**files, "CMakeLists.txt": "Init_submodule()\n",
                               "configure.py": _hook("soversion(0)")}})
  configured = _configure(tmp_path)
  assert configured.returncode != 0
  assert "declares soversion(), but" in configured.stderr
  assert "does not build as a shared library" in configured.stderr.replace("\n  ", " ")


@e2e
def test_a_group_hook_is_refused(tmp_path):
  _tree(tmp_path, {"grouped": {"configure.py": _hook("soversion(0)")},
                   "grouped/core": {"CMakeLists.txt": "Init_submodule()\n",
                                    "main.so.cpp": CORE_IMPL}})
  configured = _configure(tmp_path)
  assert configured.returncode != 0
  assert "is a group: only a shared library module has a soname" in \
    configured.stderr.replace("\n  ", " ")
