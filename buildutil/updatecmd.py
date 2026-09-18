"""`buildutil --version` / `buildutil update` — about the TOOL, not a
project (stdlib-only, pre-venv, like init).

Both are intercepted in __main__ before root discovery and the venv
bootstrap: they must work where no project or venv exists, and update
must run on the interpreter that OWNS the installed package — which is
exactly sys.executable at interception time, because the console
script's shebang points at its own environment and the re-exec into
the project venv hasn't happened yet.

The index rule mirrors the deployment's own upgrade step: 'buildutil'
is a guessable name on public PyPI, so an upgrade that falls through
to pypi.org is a supply-chain door. update resolves a PRIVATE index
(--index-url flag, then $BUILDUTIL_INDEX, then any non-public index in
pip's own configuration) and passes it as --index-url ALONE; with none
found it refuses instead of guessing.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from importlib import metadata
from pathlib import Path

from . import redact

PKG_DIR = Path(__file__).resolve().parent

_PUBLIC_HOSTS = ("pypi.org", "pypi.python.org", "pythonhosted.org")


def package_version() -> str:
  """The running package's version. A VENDORED marker (a copy installed
  into a project by `buildutil install`) is the first word — installed
  metadata would answer for whatever pip package also happens to exist
  on the machine, not for the copy actually running. Falls back to the
  checkout's pyproject (marked +uninstalled) so a PYTHONPATH checkout —
  how this suite and every dev box run it — still answers with
  something true."""
  vendored = PKG_DIR / "VENDORED"
  if vendored.is_file():
    return vendored.read_text(encoding="utf-8").strip()
  try:
    return metadata.version("buildutil")
  except metadata.PackageNotFoundError:
    pyproject = PKG_DIR.parent / "pyproject.toml"
    m = (re.search(r'(?m)^version\s*=\s*"([^"]+)"', pyproject.read_text())
         if pyproject.is_file() else None)
    return f"{m.group(1)}+uninstalled" if m else "unknown"


def project_root(start: Path | None = None) -> Path | None:
  """Nearest ancestor holding a buildutil.toml, or None. Deliberately a
  local walk and not config.REPO_ROOT: --version runs BEFORE a project
  (or a venv) is required, and must never fail for the lack of one."""
  cur = (start or Path.cwd()).resolve()
  for d in (cur, *cur.parents):
    if (d / "buildutil.toml").is_file():
      return d
  return None


def deposit_status(root: Path, version: str) -> str:
  """One line on the project's rendered cmake machinery: where it is and
  whether it came from THIS buildutil. The deposit is derived data that
  every build re-renders, so a stale line is a statement of fact, not a
  chore — it says what the next build will fix."""
  from . import deposit
  found = deposit.deposited_version(root)
  where = deposit.cmake_dir(root)
  if not (where / "buildutil.cmake").is_file():
    state = "not rendered yet (the next build renders it)"
  elif found is None:
    # a deposit DOES exist, it just predates stamping — the state every
    # project upgrading to 0.15.0 is in, and reporting it as "not rendered"
    # would be a lie about a 40k file sitting right there
    state = ("STALE, rendered before version stamping "
             "(the next build re-renders it)")
  elif found == version:
    state = "current"
  else:
    state = f"STALE, rendered by {found} (the next build re-renders it)"
  return f"  cmake machinery: {where} — {state}"


def version_string() -> str:
  """'buildutil <version> (<package dir>)'. The path matters: a stale
  shadow install (the 0.5.17 shadow-install incident) announces itself
  here instead of silently answering for the wrong copy. Inside a
  project a second line reports the DEPOSITED machinery's version —
  the same question one level down, and the one you cannot answer by
  looking at the package alone."""
  version = package_version()
  lines = [f"buildutil {version} ({PKG_DIR})"]
  root = project_root()
  if root is not None:
    lines.append(deposit_status(root, version))
  return "\n".join(lines)


def parse_pip_config(text: str) -> list[str]:
  """Index URLs out of `pip config list` output — lines shaped
  `scope.index-url='…'` / `scope.extra-index-url='…'`.

  pip prints the value REPR-STYLE, so the legal multi-line ini form

      [global]
      extra-index-url =
          https://…/simple

  arrives as '\\nhttps://…' — whitespace as LITERAL two-character
  escapes inside the quotes, not real whitespace. Taking them verbatim
  glued '\\n' onto the first URL's scheme; pip then rejected the
  index AND echoed it with the credential UNMASKED (pip only redacts
  URLs it can parse). Decode the escapes to the whitespace they stand
  for, then split — a value holding several URLs is a list per pip's
  own semantics."""
  urls: list[str] = []
  for m in re.finditer(r"(?:extra-)?index-url='([^']*)'", text):
    value = m.group(1)
    for escape in ("\\n", "\\r", "\\t"):
      value = value.replace(escape, " ")
    urls += value.split()
  return urls


def is_public(url: str) -> bool:
  return any(host in url for host in _PUBLIC_HOSTS)


def pick_index(flag: str | None, env: dict, pip_config: str) -> str | None:
  """The private index to upgrade from, or None (= refuse)."""
  if flag:
    return flag
  if env.get("BUILDUTIL_INDEX"):
    return env["BUILDUTIL_INDEX"]
  candidates = (env.get("PIP_INDEX_URL", "").split()
                + env.get("PIP_EXTRA_INDEX_URL", "").split()
                + parse_pip_config(pip_config))
  return next((u for u in candidates if u and not is_public(u)), None)


def resolve_pin(flag_pin: str, toml_pin: str) -> str:
  """The version `update` installs, or "" for latest. The --pin flag
  wins; else the project's [buildutil] version from buildutil.toml
  ("latest" and absent both mean newest). An exact pin DOWNGRADES too:
  pip's `==` spec replaces whatever is installed, newer included."""
  if flag_pin:
    return flag_pin
  if toml_pin and toml_pin.lower() != "latest":
    return toml_pin
  return ""


# The first release that understands [buildutil] version. A pin BELOW it
# is a trap: the downgraded copy can't see the pin, its next update jumps
# back to latest, which downgrades again — an endless downgrade→upgrade
# ping-pong. Refuse instead of stepping into the loop.
MIN_PIN = (0, 12, 0)


def _version_tuple(version: str) -> tuple[int, ...]:
  return tuple(int(part) for part in re.findall(r"\d+", version)[:3]) or (0,)


def pin_floor_error(pin: str) -> str | None:
  """The refusal message for a pin below MIN_PIN, or None when fine."""
  if not pin or _version_tuple(pin) >= MIN_PIN:
    return None
  floor = ".".join(map(str, MIN_PIN))
  return (f"buildutil update: refusing to install {pin} — versions below "
          f"{floor} don't understand version pinning, so the pinned copy's "
          "own next update would jump back to latest and the pin would "
          "downgrade it again (an endless ping-pong). Pin >= "
          f"{floor}, or pip install the old version manually if you "
          "really need it.")


def _installed_version(python: str) -> str:
  """Probe the dist-info on disk from a FRESH interpreter — this
  process imported the old copy and would answer stale. -E so a
  PYTHONPATH checkout (with a stale egg-info) can't answer for the
  environment being probed."""
  probe = subprocess.run(
    [python, "-E", "-c",
     "import importlib.metadata as m; print(m.version('buildutil'))"],
    capture_output=True, text=True)
  return probe.stdout.strip() if probe.returncode == 0 else "unknown"


def update(argv: list[str]) -> None:
  ap = argparse.ArgumentParser(
    prog="buildutil update",
    description="upgrade the installed buildutil package via pip, from "
                "a PRIVATE index only (public PyPI is refused: the "
                "name is not ours upstream)")
  ap.add_argument("--pin", default="",
                  help="install exactly this version instead of latest")
  ap.add_argument("--index-url", default=None,
                  help="package index to install from (overrides "
                       "$BUILDUTIL_INDEX and pip's configuration)")
  ap.add_argument("--dry-run", action="store_true",
                  help="print the pip command that would run, run nothing")
  a = ap.parse_args(argv)

  # never swap the driver under a running build (reconcile's guard)
  from . import config
  if config.HAVE_PROJECT and config.BUILD_PID_FILE.exists():
    try:
      os.kill(int(config.BUILD_PID_FILE.read_text().strip()), 0)
      sys.exit(f"buildutil update: a build is live "
               f"({config.BUILD_PID_FILE}) — finish or kill it first")
    except (OSError, ValueError):
      pass                                # stale pid file — not a build

  pip_config = subprocess.run(
    [sys.executable, "-E", "-m", "pip", "config", "list"],
    capture_output=True, text=True).stdout
  index = pick_index(a.index_url, dict(os.environ), pip_config)
  if not index:
    sys.exit(
      "buildutil update: no private package index found (checked "
      "--index-url, $BUILDUTIL_INDEX, $PIP_INDEX_URL/$PIP_EXTRA_INDEX_URL "
      "and pip's config). Refusing to fetch 'buildutil' from public "
      "PyPI — the name is not ours there. Pass --index-url <url>.")

  # update runs happily OUTSIDE any project (no toml required); inside
  # one, the project may pin the version everyone's update lands on —
  # reproducible checkouts, downgrade included (== replaces newer too).
  toml_pin = (config.PROJECT["buildutil_version"]
              if config.HAVE_PROJECT else "")
  pin = resolve_pin(a.pin, toml_pin)
  if pin and not a.pin:
    print(f"pin from buildutil.toml [buildutil] version = {pin}")
  floor_error = pin_floor_error(pin)
  if floor_error:
    sys.exit(floor_error)
  spec = f"buildutil=={pin}" if pin else "buildutil"

  # A VENDORED copy upgrades IN PLACE: the tracked <root>/.buildutil/
  # buildutil is the installation, so pip's environment is the wrong
  # target -- the new version is fetched into a scratch dir with the
  # same private-index rule and copied over the vendored tree, version
  # stamp renewed, ready to commit. pip is only the fetch vehicle.
  if (PKG_DIR / "VENDORED").is_file():
    import tempfile
    with tempfile.TemporaryDirectory() as scratch:
      fetch = [sys.executable, "-E", "-m", "pip", "install",
               "--target", scratch, "--no-deps",
               "--index-url", index, spec]
      shown = " ".join(redact.credentials(part) for part in fetch)
      print(f"+ {shown}")
      if a.dry_run:
        return
      subprocess.check_call(fetch)
      from .installcmd import VENDOR_PARENT, vendor_into
      # the project root off the running copy's location — one level
      # higher for the current layout (<root>/.buildutil/buildutil)
      # than for a pre-0.42 copy at <root>/buildutil, which this
      # re-vendor migrates in passing
      parent = PKG_DIR.parent
      root = parent.parent if parent.name == VENDOR_PARENT else parent
      vendor_into(root, source=Path(scratch) / "buildutil")
    return

  # -E: pip decides "already satisfied" from what it can IMPORT, so a
  # PYTHONPATH checkout carrying a stale egg-info would silently no-op
  # the upgrade of the real environment (found live, 0.6.0 era)
  cmd = [sys.executable, "-E", "-m", "pip", "install", "--upgrade",
         "--index-url", index, spec]
  shown = " ".join(redact.credentials(part) for part in cmd)
  print(f"+ {shown}")
  if a.dry_run:
    return
  before = _installed_version(sys.executable)
  if subprocess.run(cmd).returncode != 0:
    sys.exit("buildutil update: pip failed (see above)")
  after = _installed_version(sys.executable)
  print(f"buildutil {before} -> {after}"
        + (" (already current)" if before == after else ""))
