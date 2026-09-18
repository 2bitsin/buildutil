"""`--version` and `update`: about the TOOL, not a project — intercepted
in __main__ before root discovery and the venv bootstrap, stdlib-only,
and running on the interpreter that owns the installed package."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse
from importlib import metadata
from pathlib import Path

from . import redact

PKG_DIR = Path(__file__).resolve().parent

ORIGIN_STAMP = "ORIGIN"


def package_version() -> str:
  """The version of the copy that is RUNNING: installed metadata answers
  for whatever pip package also exists on the machine."""
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
  """Nearest ancestor holding a buildutil.toml — not config.REPO_ROOT,
  which requires the project --version must live without."""
  cur = (start or Path.cwd()).resolve()
  for d in (cur, *cur.parents):
    if (d / "buildutil.toml").is_file():
      return d
  return None


def deposit_status(root: Path, version: str) -> str:
  """One line on the rendered cmake machinery: derived data every build
  re-renders, so a stale line states a fact, not a chore."""
  from . import deposit
  found = deposit.deposited_version(root)
  where = deposit.cmake_dir(root)
  if not (where / "buildutil.cmake").is_file():
    state = "not rendered yet (the next build renders it)"
  elif found is None:
    state = ("STALE, rendered before version stamping "
             "(the next build re-renders it)")
  elif found == version:
    state = "current"
  else:
    state = f"STALE, rendered by {found} (the next build re-renders it)"
  return f"  cmake machinery: {where} — {state}"


def version_string() -> str:
  """Version and package path — a stale shadow install shows in the path."""
  version = package_version()
  lines = [f"buildutil {version} ({PKG_DIR})"]
  root = project_root()
  if root is not None:
    lines.append(deposit_status(root, version))
  return "\n".join(lines)


def origin_from_direct_url(record: str | None) -> str:
  """The source pip recorded for a direct install (PEP 610); a directory
  install and an index install have no source to reuse."""
  try:
    data = json.loads(record or "")
  except ValueError:
    return ""
  if not isinstance(data, dict) or "dir_info" in data:
    return ""
  url = str(data.get("url") or "")
  vcs_info = data.get("vcs_info")
  if isinstance(vcs_info, dict) and url:
    return "{}+{}".format(vcs_info.get("vcs", "git"), url)
  return url


def recorded_origin() -> str:
  """Where the running copy came from — a vendored tree carries no
  dist-info of its own, so install leaves the stamp beside it."""
  stamp = PKG_DIR / ORIGIN_STAMP
  if stamp.is_file():
    return stamp.read_text(encoding="utf-8").strip()
  try:
    record = metadata.distribution("buildutil").read_text("direct_url.json")
  except metadata.PackageNotFoundError:
    return ""
  return origin_from_direct_url(record)


def resolve_source(flag: str, toml_source: str, origin: str) -> str:
  """Where to install from; empty means pip's own configuration decides."""
  return flag or toml_source or origin


_FORGE_HOSTS = ("github", "gitlab")


def source_kind(source: str) -> str:
  """Which pip vehicle a source names — a forge URL of the owner/repo
  shape is a repository, an index URL on the same host is not."""
  if not source:
    return ""
  if source.startswith("git+"):
    return "git"
  split = urllib.parse.urlsplit(source)
  path = split.path.rstrip("/")
  repo_shaped = (any(host in split.netloc for host in _FORGE_HOSTS)
                 and len([part for part in path.split("/") if part]) == 2)
  return "git" if path.endswith(".git") or repo_shaped else "index"


def remote_tags(url: str) -> list[str]:
  listing = subprocess.run(["git", "ls-remote", "--tags", url],
                           capture_output=True, text=True).stdout
  return list(dict.fromkeys(
    re.findall(r"refs/tags/(\S+?)(?:\^\{\})?$", listing, re.M)))


def git_ref(url: str, pin: str) -> str:
  """The tag naming version `pin`: repositories differ on whether releases
  are tagged X.Y.Z or vX.Y.Z, so the remote decides which one exists."""
  tags = remote_tags(url.removeprefix("git+"))
  ref = next((tag for tag in (pin, f"v{pin}") if tag in tags), "")
  if not ref:
    sys.exit(f"buildutil update: {redact.credentials(url)} tags neither "
             f"{pin} nor v{pin} — pin a version that repository releases.")
  return ref


def requirement_args(source: str, pin: str) -> list[str]:
  """What follows `pip install`: the requirement, and the index it comes
  from when the source names one."""
  kind = source_kind(source)
  if kind == "git":
    url = source if source.startswith("git+") else "git+" + source
    return [f"{url}@{git_ref(url, pin)}"] if pin else [url]
  spec = f"buildutil=={pin}" if pin else "buildutil"
  return ["--index-url", source, spec] if kind == "index" else [spec]


def resolve_pin(flag_pin: str, toml_pin: str) -> str:
  """The version to install, or "" for latest. An exact pin DOWNGRADES
  too: pip's `==` spec replaces whatever is installed, newer included."""
  if flag_pin:
    return flag_pin
  if toml_pin and toml_pin.lower() != "latest":
    return toml_pin
  return ""


# A pin below the first release that understands [buildutil] version is a
# trap: the downgraded copy can't see the pin, its next update jumps back
# to latest, and the pin downgrades it again — an endless ping-pong.
MIN_PIN = (0, 12, 0)


def _version_tuple(version: str) -> tuple[int, ...]:
  return tuple(int(part) for part in re.findall(r"\d+", version)[:3]) or (0,)


def pin_floor_error(pin: str) -> str | None:
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
  """Probed from a FRESH interpreter: this process imported the old copy
  and would answer stale."""
  probe = subprocess.run(
    [python, "-E", "-c",
     "import importlib.metadata as m; print(m.version('buildutil'))"],
    capture_output=True, text=True)
  return probe.stdout.strip() if probe.returncode == 0 else "unknown"


def _parse_args(argv: list[str]) -> argparse.Namespace:
  ap = argparse.ArgumentParser(
    prog="buildutil update",
    description="upgrade the installed buildutil package with pip; with "
                "no source configured this is `pip install -U buildutil` "
                "and pip's own configuration decides where from")
  ap.add_argument("--pin", default="",
                  help="install exactly this version instead of latest")
  ap.add_argument("--source", "--index-url", dest="source", default="",
                  help="index URL or git URL to install from (overrides "
                       "[update] source in buildutil.toml)")
  ap.add_argument("--dry-run", action="store_true",
                  help="print the pip command that would run, run nothing")
  return ap.parse_args(argv)


def _guard_live_build(config) -> None:
  """Never swap the driver under a running build (reconcile's guard)."""
  if not (config.HAVE_PROJECT and config.BUILD_PID_FILE.exists()):
    return
  try:
    os.kill(int(config.BUILD_PID_FILE.read_text().strip()), 0)
  except (OSError, ValueError):
    return
  sys.exit(f"buildutil update: a build is live "
           f"({config.BUILD_PID_FILE}) — finish or kill it first")


def _echo(cmd: list[str], dry_run: bool) -> bool:
  """Show the command with credentials masked; False = stop here."""
  print("+ " + " ".join(redact.credentials(part) for part in cmd))
  return not dry_run


def _update_vendored(requirement: list[str], dry_run: bool) -> None:
  """A vendored copy IS the project's installation, so pip's environment
  is the wrong target: fetch to scratch, copy over the tracked tree."""
  import tempfile
  with tempfile.TemporaryDirectory() as scratch:
    fetch = [sys.executable, "-E", "-m", "pip", "install",
             "--target", scratch, "--no-deps", *requirement]
    if not _echo(fetch, dry_run):
      return
    subprocess.check_call(fetch)
    from .installcmd import VENDOR_PARENT, vendor_into
    parent = PKG_DIR.parent
    root = parent.parent if parent.name == VENDOR_PARENT else parent
    vendor_into(root, source=Path(scratch) / "buildutil")


def _update_installed(requirement: list[str], dry_run: bool) -> None:
  # -E: pip decides "already satisfied" from what it can IMPORT, so a
  # PYTHONPATH checkout carrying a stale egg-info would no-op the upgrade
  cmd = [sys.executable, "-E", "-m", "pip", "install", "--upgrade",
         *requirement]
  if not _echo(cmd, dry_run):
    return
  before = _installed_version(sys.executable)
  if subprocess.run(cmd).returncode != 0:
    sys.exit("buildutil update: pip failed (see above)")
  after = _installed_version(sys.executable)
  print(f"buildutil {before} -> {after}"
        + (" (already current)" if before == after else ""))


def update(argv: list[str]) -> None:
  a = _parse_args(argv)
  from . import config
  _guard_live_build(config)
  project = config.PROJECT if config.HAVE_PROJECT else {}
  pin = resolve_pin(a.pin, project.get("buildutil_version", ""))
  if pin and not a.pin:
    print(f"pin from buildutil.toml [buildutil] version = {pin}")
  floor_error = pin_floor_error(pin)
  if floor_error:
    sys.exit(floor_error)
  source = resolve_source(a.source, project.get("update_source", ""),
                          recorded_origin())
  requirement = requirement_args(source, pin)
  if (PKG_DIR / "VENDORED").is_file():
    _update_vendored(requirement, a.dry_run)
  else:
    _update_installed(requirement, a.dry_run)
