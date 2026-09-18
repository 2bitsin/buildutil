"""Per-command wall-clock budget (ruling 2026-07-17): every buildutil
command is timed. Past 1min it warns, past 2min it errors, and at 3min
it FAILS -- the whole child tree is torn down and the command exits
nonzero. A slow command is a build-system bug to fix, not time to wait.

Escape hatch (NOT the default): `--no-watchdog` disables the budget for
one invocation, `--watchdog-budget N` moves the fail line. Both are legal
ANYWHERE on the command line (ruling 2026-08-02): __main__ lifts them in
front of the subcommand for typer, and arms the same rules itself for the
pre-project verbs (init, update) that never reach the root callback.
"""
from __future__ import annotations

import os
import signal
import sys
import threading
import time

_START = time.monotonic()


def _say(level: str, budget: str) -> None:
  print(f"buildutil watchdog: {level} -- command past {budget} "
        f"({time.monotonic() - _START:.0f}s elapsed)",
        file=sys.stderr, flush=True)


def _children(pid: int) -> list[int]:
  kids: list[int] = []
  for entry in os.listdir("/proc"):
    if not entry.isdigit():
      continue
    try:
      with open(f"/proc/{entry}/status", encoding="ascii") as status:
        for line in status:
          if line.startswith("PPid:"):
            if int(line.split()[1]) == pid:
              kids.append(int(entry))
            break
    except (OSError, ValueError):
      continue
  return kids


def _kill_tree(pid: int) -> None:
  for kid in _children(pid):
    _kill_tree(kid)
    try:
      os.kill(kid, signal.SIGTERM)
    except OSError:
      pass


def _fail() -> None:
  _say("FAILED", "its budget -- tearing the command down")
  if os.name == "posix":
    _kill_tree(os.getpid())
  os._exit(3)


# Per-verb watchdog budgets, applied when no explicit --watchdog-budget is
# given. The generic 3-minute line holds an incremental build; these three
# verbs are minutes by design and would false-fail against it.
#
# What the watchdog is FOR (ruling 2026-07-28, and it is not what the name
# suggests): it is the AGENT's clock, not a hang detector. CI does not need
# it -- a GitLab job timeout already bounds every job. An interactive or
# agent-driven run has no such bound and, more to the point, an agent has no
# felt sense of elapsed time: a suite that quietly went from four minutes to
# forty because of a change made an hour ago reads exactly like one that was
# always forty. The warn-at-1/3 / error-at-2/3 ladder is the instrument that
# says otherwise, so a budget wants to be tight enough that an ordinary run
# TRIPS THE WARNING -- that is the reading, not a nuisance.
#
# Which is why a verb gets a budget rather than a caller reaching for
# --no-watchdog: disabling it does not move the alarm, it removes the
# instrument. But a budget an honest run FAILS is just as bad, because the
# next thing anyone does is reach for the switch. Both numbers below are
# measured, with headroom for a cold build:
#
#   bench     real OS boots + workloads; the manual CI job runs it bare
#   coverage  measured 870s warm (a gcov -O0 build, 1344 ctest in 78s, and
#             726s of pytest booting guests on the INSTRUMENTED bridge, which
#             runs ~24x slower than release). 2400 warns at 800 -- so every
#             normal run reports its own cost -- errors at 1600, and leaves a
#             cold instrumented rebuild room to finish.
#   analyze   the -O2 clang-tidy pass over the whole tree
_VERB_BUDGETS = {"bench": 1800.0, "coverage": 2400.0, "analyze": 900.0}


def watchdog_budget_for(subcommand: str | None, requested: float, *,
                        explicit: bool, no_watchdog: bool, in_ci: bool) -> float | None:
  """The wall budget to arm the watchdog with, or None to leave it off. An
  explicit --watchdog-budget always wins; --no-watchdog turns it off; in CI it
  is off by default (the GitLab job timeout is the bound); otherwise the
  per-verb budget, else the generic default."""
  if no_watchdog:
    return None
  if explicit:
    return requested
  if in_ci:
    return None
  return _VERB_BUDGETS.get(subcommand, requested)


def parse_switches(flags: list[str]) -> tuple[bool, float, bool]:
  """(no_watchdog, budget, explicit) from lifted command-line switches --
  the pre-project verbs' equivalent of the root callback's typer options."""
  no_watchdog, budget, explicit = False, 180.0, False
  for i, flag in enumerate(flags):
    if flag == "--no-watchdog":
      no_watchdog = True
    elif flag == "--watchdog-budget" and i + 1 < len(flags):
      budget, explicit = float(flags[i + 1]), True
    elif flag.startswith("--watchdog-budget="):
      budget, explicit = float(flag.partition("=")[2]), True
  return no_watchdog, budget, explicit


def arm_for(verb: str, flags: list[str]) -> None:
  """Arm by the standard rules for a verb that never reaches the typer
  root callback (init, update)."""
  no_watchdog, budget, explicit = parse_switches(flags)
  armed = watchdog_budget_for(verb, budget, explicit=explicit,
                              no_watchdog=no_watchdog,
                              in_ci=bool(os.environ.get("CI")))
  if armed is not None:
    arm(armed)


def arm(fail_at: float = 180.0) -> None:
  """Start the budget timers; they are daemons and die with the process."""
  for delay, action in ((fail_at / 3.0, lambda: _say("WARNING", "1/3 budget")),
                        (fail_at * 2.0 / 3.0, lambda: _say("ERROR", "2/3 budget")),
                        (fail_at, _fail)):
    timer = threading.Timer(delay, action)
    timer.daemon = True
    timer.start()


def report() -> None:
  """Print the command's wall time (registered atexit by the entrypoint;
  the root callback's --no-timing switch suppresses it)."""
  if os.environ.get("BUILDUTIL_NO_TIMING"):
    return
  elapsed = time.monotonic() - _START
  print(f"buildutil: command took {elapsed:.1f}s",
        file=sys.stderr, flush=True)
