"""The include-scope rule, compiled rather than asserted.

Text assertions on buildutil.cmake say what the machinery *reads* like; only
a real configure+build says what it *does*. This drives cmake over a
two-module tree and checks the three things that matter:

  * a module's own TU — including one in a nested subdirectory, which the
    quoted-include rule does NOT cover — resolves its module's headers
    unqualified;
  * a CONSUMER that links the module does NOT, because the root is PRIVATE.
    With the root exported this same code compiles silently against another
    module's internal header, chosen by link order;
  * the supported spelling, qualified through sources/, works throughout.

Skipped where there is no cmake or no C++ compiler; the rest of the suite
is pure python and stays that way.
"""
import shutil
import subprocess

import pytest

from buildutil import deposit

CFG = {"cmake_option_prefix": "ACME", "module_define_prefix": "ACM"}

pytestmark = pytest.mark.skipif(
  shutil.which("cmake") is None or
  not (shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")),
  reason="needs cmake and a C++ compiler")

ROOT_CMAKE = """\
cmake_minimum_required(VERSION 3.25)
project(scopecheck CXX)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
add_subdirectory(sources)
"""


def _tree(root, beta_source: str, beta_cmake: str = "Init_submodule()\n",
          cfg: dict | None = None):
  """A two-module project: alpha owns a header, beta is the consumer."""
  deposit.ensure(root, cfg or CFG)
  (root / "CMakeLists.txt").write_text(ROOT_CMAKE)
  src = root / "sources"
  (src / "alpha" / "sub").mkdir(parents=True)
  (src / "beta").mkdir(parents=True)
  (src / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  (src / "alpha" / "CMakeLists.txt").write_text("Init_submodule()\n")
  (src / "alpha" / "types.h").write_text(
    "#pragma once\ninline int alpha_secret() { return 7; }\n")
  (src / "alpha" / "alpha.cpp").write_text(
    '#include "types.h"\nint alpha_root() { return alpha_secret(); }\n')
  # a TU that is NOT next to the header: the case the private root exists for
  (src / "alpha" / "sub" / "deep.cpp").write_text(
    '#include "types.h"\nint alpha_deep() { return alpha_secret() + 1; }\n')
  (src / "beta" / "CMakeLists.txt").write_text(beta_cmake)
  (src / "beta" / "beta.cpp").write_text(beta_source)
  return root


def _build(root) -> subprocess.CompletedProcess:
  cfg = subprocess.run(["cmake", "-S", str(root), "-B", str(root / "b")],
                       capture_output=True, text=True)
  assert cfg.returncode == 0, cfg.stdout + cfg.stderr
  return subprocess.run(["cmake", "--build", str(root / "b")],
                        capture_output=True, text=True)


def test_a_modules_own_tus_reach_its_headers_unqualified(tmp_path):
  _tree(tmp_path, "int beta_ok() { return 1; }\n")
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr
  # the nested TU is the proof: its own directory holds no types.h
  assert "deep.cpp" in built.stdout


def test_a_consumer_does_not_inherit_the_modules_own_directory(tmp_path):
  """The regression this scope exists to prevent. Exported, this builds —
  and beta silently compiles against alpha's internal header."""
  _tree(tmp_path,
        '#include "types.h"\nint beta_leak() { return alpha_secret(); }\n',
        "Init_submodule()\nLink_dependencies(alpha)\n")
  built = _build(tmp_path)
  assert built.returncode != 0, (
    "beta compiled an unqualified include of alpha's PRIVATE header — the "
    "module's own dir is leaking to consumers again")
  assert "types.h" in built.stdout + built.stderr


def test_a_ported_tree_can_export_module_headers_project_wide(tmp_path):
  """The escape hatch for a legacy tree, declared ONCE in buildutil.toml:
  [cmake] export_module_headers = true. The SAME beta source that is a hard
  error above now compiles — which is the whole point, and also exactly the
  hazard, so nothing about this is a default."""
  _tree(tmp_path,
        '#include "types.h"\nint beta_leak() { return alpha_secret(); }\n',
        "Init_submodule()\nLink_dependencies(alpha)\n",
        cfg={**CFG, "export_module_headers": True})
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr


def test_exporting_needs_no_per_module_boilerplate(tmp_path):
  """52 identical target_include_directories(<module> PUBLIC .) lines were
  the thing being removed: the modules' own CMakeLists stay bare."""
  _tree(tmp_path,
        '#include "types.h"\nint beta_leak() { return alpha_secret(); }\n',
        "Init_submodule()\nLink_dependencies(alpha)\n",
        cfg={**CFG, "export_module_headers": True})
  for module in ("alpha", "beta"):
    text = (tmp_path / "sources" / module / "CMakeLists.txt").read_text()
    assert "target_include_directories" not in text


def test_the_qualified_spelling_still_resolves(tmp_path):
  """sources/ is the module's published surface and is untouched by any of
  this — <module>/foo.h keeps working, for consumers and strangers alike."""
  _tree(tmp_path,
        '#include "alpha/types.h"\nint beta_ok() { return alpha_secret(); }\n',
        "Init_submodule()\nLink_dependencies(alpha)\n")
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr


def test_project_local_cmake_is_included_and_can_call_the_machinery(tmp_path):
  """The `cmake/` seam, exercised by cmake itself: files are included in
  sorted order, after the machinery, and a function defined in one is
  callable from the root CMakeLists."""
  _tree(tmp_path, "int beta_ok() { return 1; }\n")
  local = tmp_path / "cmake"
  local.mkdir()
  (local / "a_first.cmake").write_text(
    'message(STATUS "LOCAL a_first")\n'
    'function(Acme_hello)\n  message(STATUS "ACME HELLO")\nendfunction()\n')
  (local / "b_second.cmake").write_text('message(STATUS "LOCAL b_second")\n')
  (tmp_path / "CMakeLists.txt").write_text(ROOT_CMAKE + "Acme_hello()\n")
  cfg = subprocess.run(["cmake", "-S", str(tmp_path), "-B", str(tmp_path / "b")],
                       capture_output=True, text=True)
  assert cfg.returncode == 0, cfg.stdout + cfg.stderr
  out = cfg.stdout
  assert "ACME HELLO" in out, "a project-local function was not callable"
  assert out.index("LOCAL a_first") < out.index("LOCAL b_second"), "unsorted"
