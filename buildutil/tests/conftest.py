import os

import pytest

# The deposit's direct-invocation guards (0.45) refuse cmake/ninja/ctest
# run outside buildutil, checking the BUILDUTIL env marker the driver
# exports. The e2e suites here drive cmake/ninja directly BY DESIGN —
# they test the machinery, not the CLI — so the whole session runs
# marked. test_driver_guard_e2e strips the marker per-subprocess to
# prove the refusals.
os.environ.setdefault("BUILDUTIL", "test-session")


@pytest.fixture(autouse=True)
def _unarmed_watchdog(monkeypatch):
  from buildutil import watchdog
  monkeypatch.setattr(watchdog, "arm", lambda budget: None)
