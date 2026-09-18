"""`buildutil init` scaffolds a buildable hello-world tree — and never
overwrites what a project already owns."""
import ast

import pytest

from buildutil import initcmd


@pytest.fixture
def fresh(tmp_path, monkeypatch):
  monkeypatch.chdir(tmp_path)
  return tmp_path


def test_scaffold_lands_complete(fresh, capsys):
  initcmd.main(["--name", "demo"])
  for rel in ("buildutil.toml", "CMakeLists.txt", "conanfile.py",
              ".gitignore", "sources/CMakeLists.txt",
              "sources/demo/hello/CMakeLists.txt",
              "sources/demo/hello/hello.hpp",
              "sources/demo/hello/hello.cpp", "sources/demo/hello/main.cpp",
              "sources/demo/hello/hello.test.cpp",
              "sources/demo/hello/hello.bench.cpp"):
    assert (fresh / rel).is_file(), rel


def test_gtest_and_benchmark_wired(fresh):
  initcmd.main(["--name", "demo"])
  deps = (fresh / "sources" / "CMakeLists.txt").read_text()
  assert 'Require(GTest VERSION ">=1.17.0" TEST CONAN gtest' in deps
  assert 'Require(benchmark VERSION ">=1.9.0" BENCH CONAN benchmark' in deps
  assert "Scan_subdirectories()" in deps


def test_the_scaffolded_root_is_bare(fresh):
  """Owner req: the root CMakeLists is the bare essentials and nothing
  else. Everything universal -- the compile DB, CTest/GoogleTest, the
  benchmark/coverage/gc-sections options and their flag blocks -- lives
  in the rendered machinery, so a project never carries a copy that
  cannot be upgraded. The whole file is four statements."""
  initcmd.main(["--name", "demo"])
  root_cml = (fresh / "CMakeLists.txt").read_text()
  statements = [line for line in root_cml.splitlines()
                if line.strip() and not line.lstrip().startswith("#")]
  assert statements == ["cmake_minimum_required(VERSION 3.25)",
                        "project(demo CXX)",
                        "include(buildutil)",
                        "add_subdirectory(sources)"], statements


def test_prefixes_and_name_substituted(fresh):
  initcmd.main(["--name", "My-Demo", "--cmake-prefix", "MYD"])
  root_cml = (fresh / "CMakeLists.txt").read_text()
  assert "project(My-Demo CXX)" in root_cml
  assert "@" not in root_cml
  conanfile = (fresh / "conanfile.py").read_text()
  # the conan name is the template's FALLBACK since [package] took over
  assert '_PKG.get("name", "my-demo")' in conanfile
  assert 'os.environ.get("MYD_SKIP_TEST_DEPS")' in conanfile
  assert "@" not in conanfile  # no token left behind
  # the package directory is the project name verbatim -- it is the
  # prefix every exported header is qualified by, so it has to be the
  # name the project actually calls itself
  greeting = (fresh / "sources" / "My-Demo" / "hello" / "hello.cpp").read_text()
  assert '"hello, My-Demo"' in greeting


def test_conanfile_is_valid_python(fresh):
  initcmd.main(["--name", "demo"])
  ast.parse((fresh / "conanfile.py").read_text())


def test_gitignore_covers_derived_state(fresh):
  initcmd.main(["--name", "demo"])
  lines = (fresh / ".gitignore").read_text().splitlines()
  assert "_*" in lines
  assert "CMakeUserPresets.json" in lines


def test_rerun_never_overwrites(fresh):
  initcmd.main(["--name", "demo"])
  marker = "# project-owned edit\n"
  cml = fresh / "CMakeLists.txt"
  cml.write_text(marker)
  initcmd.main(["--name", "demo"])
  assert cml.read_text() == marker


def test_existing_sources_skips_hello(fresh):
  (fresh / "sources").mkdir()
  initcmd.main(["--name", "demo"])
  assert not (fresh / "sources" / "demo").exists()
  # the deps file still lands — it's per-file, only the demo is skipped
  assert (fresh / "sources" / "CMakeLists.txt").is_file()


def test_bare_writes_toml_only(fresh):
  initcmd.main(["--name", "demo", "--bare"])
  assert (fresh / "buildutil.toml").is_file()
  assert not (fresh / "CMakeLists.txt").exists()
  assert not (fresh / "conanfile.py").exists()
  assert not (fresh / "sources").exists()


def test_installed_pycache_never_scaffolds(fresh, monkeypatch, tmp_path_factory):
  """pip byte-compiles the template conanfile.py on install, so the
  INSTALLED template tree carries a binary __pycache__ — scaffolding
  must skip it (reading it as text crashed init 0.4.0-dev live)."""
  import shutil
  templates = tmp_path_factory.mktemp("tpl") / "project"
  shutil.copytree(initcmd.PROJECT_TEMPLATES, templates)
  cache = templates / "__pycache__"
  cache.mkdir()
  (cache / "conanfile.cpython-314.pyc").write_bytes(bytes(range(256)))
  monkeypatch.setattr(initcmd, "PROJECT_TEMPLATES", templates)
  initcmd.main(["--name", "demo"])
  assert not (fresh / "__pycache__").exists()
  assert (fresh / "conanfile.py").is_file()


def test_conan_name_sanitized():
  assert initcmd._conan_name("My Project!") == "myproject"
  assert initcmd._conan_name("X") == "project"     # too short for conan
  assert initcmd._conan_name("--x") == "project"   # must start alnum


def test_scaffolded_hello_includes_resolve(fresh):
  """The example module lands at sources/<name>/hello/, so its
  sources/-rooted include spelling carries the package directory — a
  bare "hello/hello.hpp" resolves nowhere and the scaffold refuses to
  compile."""
  initcmd.main(["--name", "demo"])
  for rel in ("hello.cpp", "main.cpp", "hello.test.cpp",
              "hello.bench.cpp"):
    text = (fresh / "sources" / "demo" / "hello" / rel).read_text()
    assert '#include "demo/hello/hello.hpp"' in text, rel
    assert "@NAME@" not in text
