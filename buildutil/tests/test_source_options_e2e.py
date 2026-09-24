"""A hook's declare(path, options=, defines=) compiles that one source with them (#142)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from buildutil import deposit

CFG = {"cmake_option_prefix": "ACME", "module_define_prefix": "ACM"}
PYSUPPORT = Path(deposit.__file__).resolve().parent / "pysupport"

pytestmark = pytest.mark.skipif(
  shutil.which("cmake") is None or shutil.which("ninja") is None
  or not (shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")),
  reason="needs cmake, ninja and a C++ compiler")

ROOT_CMAKE = """\
cmake_minimum_required(VERSION 3.25)
project(flagged CXX)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
add_subdirectory(sources)
"""

NEEDS_ANSWER = """\
#ifndef ANSWER
#error ANSWER is not defined
#endif
static_assert(ANSWER == 42, "ANSWER is the declared one");
int generated_answer() { return ANSWER; }
"""

PLAIN = """\
#ifdef ANSWER
#error ANSWER reached a source that did not declare it
#endif
int plain() { return 0; }
"""

HOOK = """\
import buildutil_configure as bc
from pathlib import Path
answer = Path(bc.source_dir(), "answer.txt").read_text().strip()
bc.depends(Path(bc.source_dir(), "answer.txt"))
bc.emit("answer.cpp", {needs!r})
bc.declare(bc.output_dir() / "answer.cpp", options=["-fno-strict-aliasing"],
           defines=[f"ANSWER={{answer}}"])
bc.declare(Path(bc.source_dir(), "driver.cpp"), options=["-fwrapv"])
bc.emit("plain.cpp", {plain!r})
""".format(needs=NEEDS_ANSWER, plain=PLAIN)


def _tree(root, answer="42"):
  deposit.ensure(root, CFG)
  (root / "CMakeLists.txt").write_text(ROOT_CMAKE)
  (root / "sources").mkdir()
  (root / "sources" / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  module = root / "sources" / "core"
  module.mkdir()
  (module / "CMakeLists.txt").write_text("Init_submodule()\n")
  (module / "configure.py").write_text(HOOK)
  (module / "answer.txt").write_text(answer)
  (module / "driver.cpp").write_text("int driver() { return 1; }\n")
  return root


def _configure(root, *extra):
  return subprocess.run(
    ["cmake", "-S", str(root), "-B", str(root / "b"), "-G", "Ninja",
     "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON", f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}", *extra],
    capture_output=True, text=True)


def _build(root):
  configured = _configure(root)
  assert configured.returncode == 0, configured.stdout + configured.stderr
  return subprocess.run(["cmake", "--build", str(root / "b")], capture_output=True, text=True)


def _commands(root):
  entries = json.loads((root / "b" / "compile_commands.json").read_text())
  return {Path(entry["file"]).name: entry["command"] for entry in entries}


def test_the_declared_define_reaches_its_source_only(tmp_path):
  built = _build(_tree(tmp_path))
  assert built.returncode == 0, built.stdout + built.stderr


def test_options_land_on_the_declared_source_only(tmp_path):
  _build(_tree(tmp_path))
  commands = _commands(tmp_path)
  assert "-fno-strict-aliasing" in commands["answer.cpp"]
  assert "-fno-strict-aliasing" not in commands["plain.cpp"]
  assert "-fwrapv" not in commands["answer.cpp"]


def test_a_checked_in_source_takes_options_and_compiles_once(tmp_path):
  _build(_tree(tmp_path))
  entries = json.loads((tmp_path / "b" / "compile_commands.json").read_text())
  drivers = [entry for entry in entries if Path(entry["file"]).name == "driver.cpp"]
  assert len(drivers) == 1, drivers
  assert "-fwrapv" in drivers[0]["command"]


def test_a_changed_define_reconfigures_and_rebuilds_the_file(tmp_path):
  assert _build(_tree(tmp_path)).returncode == 0
  (tmp_path / "sources" / "core" / "answer.txt").write_text("41")
  rebuilt = subprocess.run(["cmake", "--build", str(tmp_path / "b")],
                           capture_output=True, text=True)
  assert rebuilt.returncode != 0
  assert "ANSWER is the declared one" in rebuilt.stdout + rebuilt.stderr


def _hook(root, directory, text):
  hook = root / "sources" / directory / "configure.py"
  hook.parent.mkdir(parents=True, exist_ok=True)
  hook.write_text("import buildutil_configure as bc\nfrom pathlib import Path\n" + text)
  return hook


def test_a_declared_file_under_a_test_subtree_compiles_once_with_its_flags(tmp_path):
  tree = _tree(tmp_path)
  suite = tree / "sources" / "core" / "extra.test"
  suite.mkdir()
  (suite / "extra.test.cpp").write_text("int extra() { return ANSWER; }\n")
  _hook(tree, "core", "bc.declare(Path(bc.source_dir(), 'extra.test', 'extra.test.cpp'), "
                      "defines=['ANSWER=42'])\n")
  (tree / "sources" / "CMakeLists.txt").write_text(
    "add_library(GTest::gtest_main INTERFACE IMPORTED)\nScan_subdirectories()\n")
  assert _configure(tree, "-DBUILD_TESTING=ON").returncode == 0
  entries = json.loads((tree / "b" / "compile_commands.json").read_text())
  extra = [e for e in entries if Path(e["file"]).name == "extra.test.cpp"]
  assert len(extra) == 1, extra
  assert "-DANSWER=42" in extra[0]["command"]


def test_flags_for_a_file_of_another_module_are_refused(tmp_path):
  tree = _tree(tmp_path)
  other = tree / "sources" / "other"
  other.mkdir()
  (other / "CMakeLists.txt").write_text("Init_submodule()\n")
  (other / "other.cpp").write_text("int other() { return 0; }\n")
  _hook(tree, "core", f"bc.declare({str(other / 'other.cpp')!r}, options=['-fwrapv'])\n")
  configured = _configure(tree)
  assert configured.returncode != 0
  assert "neither a source of its own module" in " ".join(configured.stderr.split())


def test_flags_for_a_nested_module_are_refused(tmp_path):
  tree = _tree(tmp_path)
  nested = tree / "sources" / "core" / "inner"
  nested.mkdir()
  (nested / "CMakeLists.txt").write_text("Init_submodule()\n")
  (nested / "inner.cpp").write_text("int inner() { return 0; }\n")
  _hook(tree, "core", "bc.declare(Path(bc.source_dir(), 'inner', 'inner.cpp'), options=['-fwrapv'])\n")
  configured = _configure(tree)
  assert configured.returncode != 0
  assert "neither a source of its own module" in " ".join(configured.stderr.split())


def test_a_file_both_a_group_and_a_module_hook_flag_is_refused_naming_both(tmp_path):
  tree = _tree(tmp_path)
  core = tree / "sources" / "core"
  grouped = tree / "sources" / "grp" / "core"
  grouped.parent.mkdir()
  core.rename(grouped)
  (grouped / "configure.py").unlink()
  group = _hook(tree, "grp", "bc.emit('grp/core/shared.cpp', 'int shared() { return 0; }\\n')\n"
                             "bc.declare(bc.output_dir() / 'grp/core/shared.cpp', options=['-fwrapv'])\n")
  module = _hook(tree, "grp/core", "shared = Path(bc.output_dir()).parent / 'grp' / 'grp/core/shared.cpp'\n"
                                   "bc.declare(shared, options=['-fno-strict-aliasing'])\n")
  configured = _configure(tree)
  assert configured.returncode != 0
  stderr = " ".join(configured.stderr.split())
  assert "has compile flags declared by two hooks" in stderr
  assert str(group) in stderr and str(module) in stderr
