import subprocess
import sys

import pytest

from buildutil import deposit
from buildutil.tests.test_package_tree_e2e import CFG, PYSUPPORT, _run, _tree, e2e


LISTING = r'''
#include <chrono>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <thread>
int main(int argc, char **argv) {
  for (int i = 1; i < argc; ++i) {
    if (std::strcmp(argv[i], "--gtest_list_tests") == 0) {
      std::ofstream("discovery-ran").put('1');
      std::this_thread::sleep_for(std::chrono::seconds(2));
      std::puts("Widget.\n  Workdir");
      return 0;
    }
  }
  return std::ifstream("test-input").good() ? 0 : 1;
}
'''


@e2e
@pytest.mark.parametrize("seconds, succeeds", [(1, False), (10, True)])
def test_discovery_is_deferred_and_bounded(tmp_path, seconds, succeeds):
  root = _tree(tmp_path)
  deposit.ensure(root, {**CFG, "test_discovery_timeout": seconds})
  module = root / "sources" / "widget"
  (module / "thing.test.cpp").write_text(LISTING)
  (module / "test-input").write_text("fixture")
  build = root / "b"
  _run("cmake", "-S", str(root), "-B", str(build), "-G", "Ninja",
       "-DBUILD_TESTING=ON", "-DBUILD_BENCHMARKING=OFF",
       f"-DBUILDUTIL_PY={sys.executable}", f"-DBUILDUTIL_PYSUPPORT={PYSUPPORT}")
  _run("cmake", "--build", str(build))
  assert not list(root.rglob("discovery-ran"))
  includes = (build / "sources/widget/CTestTestfile.cmake").read_text()
  assert includes.index("_include.cmake") < includes.index(
    "widget-tests_workdir.cmake")
  ran = subprocess.run(
    ["ctest", "--test-dir", str(build), "--output-on-failure"],
    capture_output=True, text=True)
  output = ran.stdout + ran.stderr
  assert (ran.returncode == 0) is succeeds, output
  if succeeds:
    assert "Widget.Workdir" in output
    assert "100% tests passed" in output
  else:
    assert "timeout" in output.lower(), output
  assert list(build.rglob("discovery-ran"))
  assert not (module / "discovery-ran").exists()
