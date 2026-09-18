"""--fail-fast / --max-errors resolution (app._effective_max_errors).

The load-bearing behaviour: an explicit --max-errors N wins, --fail-fast alone
means 1 (stop at the first error), neither set means 0 (off / full output)."""
from buildutil import app


def test_off_by_default():
  assert app._effective_max_errors(0, False) == 0


def test_fail_fast_alone_is_one():
  assert app._effective_max_errors(0, True) == 1


def test_explicit_max_errors_wins_over_fail_fast():
  assert app._effective_max_errors(5, True) == 5


def test_explicit_max_errors_without_fail_fast():
  assert app._effective_max_errors(3, False) == 3
