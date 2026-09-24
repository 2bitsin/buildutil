import os
import sys

import pytest

# The deposit's direct-invocation guards (0.45) refuse cmake/ninja/ctest
# run outside buildutil, checking the BUILDUTIL env marker the driver
# exports. The e2e suites here drive cmake/ninja directly BY DESIGN —
# they test the machinery, not the CLI — so the whole session runs
# marked. test_driver_guard_e2e strips the marker per-subprocess to
# prove the refusals.
os.environ.setdefault("BUILDUTIL", "test-session")

# conanfile.py imports the Require() parser a project ships beside it; the
# e2e suites prove the recipe's own copy, exported with it, is what conan loads
from buildutil import packaging  # noqa: E402
sys.modules.setdefault("buildutil_requires", packaging.requires_parser())


@pytest.fixture(autouse=True)
def _unarmed_watchdog(monkeypatch):
  from buildutil import watchdog
  monkeypatch.setattr(watchdog, "arm", lambda budget: None)


@pytest.fixture
def real_conan(monkeypatch):
  """The installed conan for one test, the stub modules back afterwards."""
  conan = [name for name in sys.modules if name.partition(".")[0] == "conan"]
  for name in conan:
    monkeypatch.delitem(sys.modules, name)
  pytest.importorskip("conan.tools.scm")
  yield
  for name in [name for name in sys.modules
               if name.partition(".")[0] == "conan"]:
    del sys.modules[name]
