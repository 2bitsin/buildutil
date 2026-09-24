"""watchdog_budget_for: the arm-or-skip decision, pure over its inputs."""
from buildutil.app import watchdog_budget_for


def _plan(subcommand="build", requested=180.0, *,
          explicit=False, no_watchdog=False, in_ci=False):
  return watchdog_budget_for(subcommand, requested, explicit=explicit,
                             no_watchdog=no_watchdog, in_ci=in_ci)


def test_interactive_default_arms_the_generic_budget():
  assert _plan() == 180.0


def test_interactive_uses_the_per_verb_budget():
  assert _plan("bench") == 1800.0                 # bench's long override
  assert _plan("coverage") == 2400.0              # gcov build + corpus + guests
  assert _plan("analyze") == 900.0                # the -O2 tidy pass
  assert _plan("publish") == 1800.0               # every configuration from source


# The reason the long verbs have budgets at all: so no caller has to reach
# for --i-am-willingly-circumventing-build-and-test-time-safeguards, which removes the alarm rather than moving it.
def test_every_long_verb_still_arms_something():
  for verb in ("bench", "coverage", "analyze", "publish"):
    assert _plan(verb) is not None


def test_ci_is_off_by_default():
  assert _plan(in_ci=True) is None
  assert _plan("bench", in_ci=True) is None        # even the long verbs


def test_explicit_budget_wins_everywhere():
  assert _plan(requested=600.0, explicit=True) == 600.0
  assert _plan(requested=600.0, explicit=True, in_ci=True) == 600.0   # even in CI


def test_no_watchdog_beats_everything():
  assert _plan(no_watchdog=True) is None
  assert _plan(requested=600.0, explicit=True, no_watchdog=True) is None


# --- the switches are legal anywhere (ruling 2026-08-02) ---------------

from buildutil import watchdog
from buildutil.__main__ import _lift_watchdog_flags


def test_lift_pulls_switches_from_after_the_subcommand():
  flags, rest = _lift_watchdog_flags(
    ["build", "--release", "--i-am-willingly-circumventing-build-and-test-time-safeguards", "--watchdog-budget", "600"])
  assert flags == ["--i-am-willingly-circumventing-build-and-test-time-safeguards", "--watchdog-budget", "600"]
  assert rest == ["build", "--release"]


def test_lift_equals_form_and_already_leading():
  flags, rest = _lift_watchdog_flags(["--watchdog-budget=90", "coverage"])
  assert flags == ["--watchdog-budget=90"]
  assert rest == ["coverage"]


def test_lift_never_crosses_the_double_dash():
  argv = ["run", "--target", "x", "--", "--i-am-willingly-circumventing-build-and-test-time-safeguards", "--watchdog-budget"]
  flags, rest = _lift_watchdog_flags(argv)
  assert flags == []
  assert rest == argv          # the target's argv is forwarded untouched


def test_parse_switches_forms():
  assert watchdog.parse_switches([]) == (False, 180.0, False)
  assert watchdog.parse_switches(["--i-am-willingly-circumventing-build-and-test-time-safeguards"]) == (True, 180.0, False)
  assert watchdog.parse_switches(["--watchdog-budget", "600"]) == (False, 600.0, True)
  assert watchdog.parse_switches(["--watchdog-budget=90"]) == (False, 90.0, True)


def test_arm_for_pre_project_verbs_follow_the_rules(monkeypatch):
  armed = []
  monkeypatch.setattr(watchdog, "arm", lambda budget: armed.append(budget))
  monkeypatch.delenv("CI", raising=False)
  watchdog.arm_for("init", [])
  assert armed == [180.0]                    # generic budget applies
  watchdog.arm_for("init", ["--i-am-willingly-circumventing-build-and-test-time-safeguards"])
  assert armed == [180.0]                    # disabled: nothing armed
  monkeypatch.setenv("CI", "1")
  watchdog.arm_for("update", [])
  assert armed == [180.0]                    # CI: off by default
  watchdog.arm_for("update", ["--watchdog-budget", "60"])
  assert armed == [180.0, 60.0]              # explicit arms even in CI
