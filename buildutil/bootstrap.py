"""Venv bootstrap + re-exec for buildutil.

Stdlib-only (plus config). Runs on whatever interpreter launched the repo-root
`buildutil` shim, brings up the project-local venv, then re-execs `python -m
buildutil` under it so every third-party dep (typer, conan, …) resolves.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
import time
from pathlib import Path

from . import config, redact


def _run(cmd: list[str], env: dict | None = None) -> None:
  # Pin cwd to REPO_ROOT so child processes can't drop artifacts a level up.
  print("+", " ".join(cmd), flush=True)
  subprocess.check_call(cmd, env=env, cwd=str(config.REPO_ROOT))


def install_venv_symlinks() -> None:
  """Put a working `buildutil` console script on the venv's bin dir.

  The venv does NOT carry the buildutil package — VENV_DEPS is the
  toolchain only; the package lives wherever the OUTER interpreter
  found it (a pip site-packages, a checkout). So the previous
  `pip install --no-index buildutil` inside the venv could never
  succeed ("No matching distribution found" on every fresh venv, and
  without --no-index it would go fishing in an index for a package we
  are already running), and the fallback script it left behind died on
  `import buildutil` when invoked directly. Write the script ourselves
  and pin the running copy's parent onto sys.path — the same path
  ensure() hands the re-exec via PYTHONPATH. Content-compared so a
  stale script (old package location, recreated venv) heals on the
  next run; a legacy shim-symlink is still removed first."""
  if platform.system() == "Windows":
    return
  link = config.VENV_BIN_DIR / "buildutil"
  if link.is_symlink():
    link.unlink()
    print(f"legacy shim symlink removed: {link}")
  pkg_parent = str(Path(__file__).resolve().parents[1])
  script = (f"#!{config.VENV_PY}\n"
            "import sys\n"
            f"sys.path.insert(0, {pkg_parent!r})\n"
            "from buildutil.__main__ import main\n"
            "sys.exit(main())\n")
  if not link.exists() or link.read_text() != script:
    link.write_text(script)
    link.chmod(0o755)
    print(f"console script written: {link}")


def conan_remote_env() -> tuple[str, str | None, str | None, str | None]:
  """The remote seam, resolved in ONE place: CONAN_REMOTE_{NAME,URL,USER,
  PASS} first (a repo-root .env feeds these — config.load_dotenv), GitLab's
  CI_ARTIFACTORY_{NAME,HREF,USER,PASS} as the CI fallback. URL is the gate:
  unset means no private remote is configured. NAME defaults to
  'conancenter' — registering the private mirror UNDER that name literally
  replaces the public default, so every later resolve and upload speaks to
  the mirror. USER/PASS are optional (anonymous mirrors exist)."""
  url = os.environ.get("CONAN_REMOTE_URL") or os.environ.get("CI_ARTIFACTORY_HREF")
  name = (os.environ.get("CONAN_REMOTE_NAME")
          or os.environ.get("CI_ARTIFACTORY_NAME") or "conancenter")
  user = os.environ.get("CONAN_REMOTE_USER") or os.environ.get("CI_ARTIFACTORY_USER")
  pw = os.environ.get("CONAN_REMOTE_PASS") or os.environ.get("CI_ARTIFACTORY_PASS")
  return name, url, user, pw


def _conan_bin() -> str | None:
  """The venv's pinned conan when it exists, else whatever PATH holds
  (BUILDUTIL_SYSTEM images carry conan in the image venv, not _pyvenv)."""
  venv_conan = config.VENV_BIN_DIR / f"conan{config.VENV_EXE_SUFFIX}"
  return str(venv_conan) if venv_conan.exists() else shutil.which("conan")


def _resolved_conan_home() -> Path:
  """CONAN_HOME as the current command resolved it (engine._enter exports
  it before remote work runs); the config default before any _enter."""
  return Path(os.environ.get("CONAN_HOME") or config.DEFAULT_CONAN_HOME)


def _login_failure_kind(output: str) -> str:
  """Classify a failed `conan remote login` by its output: 'auth' — the
  server answered and said NO (definitive, retrying only feeds a
  brute-force lockout); 'lockout' — the server is refusing because of
  the failures themselves (403 recurrent-failures; a retry re-arms it);
  'transient' — everything else (no route, timeout, 5xx), the only kind
  worth retrying."""
  if "blocked due to recurrent request failures" in output:
    return "lockout"
  if "Wrong user or password" in output or "Bad credentials" in output \
     or re.search(r"\b40[13]\b", output):
    # a plain 401/403 is the server SAYING NO — permissions or
    # credentials, either way a retry cannot change the answer
    return "auth"
  return "transient"


def _login_credentials(name: str, user: str, pw: str) -> dict:
  """The per-remote credential env vars conan 2 reads: the password
  reaches conan without ever appearing in argv. conan upper-cases the
  remote name with dashes as underscores (remote_credentials.py)."""
  suffix = name.replace("-", "_").upper()
  return {f"CONAN_LOGIN_USERNAME_{suffix}": user,
          f"CONAN_PASSWORD_{suffix}": pw}


def _login_with_retries(conan: str, name: str, user: str, pw: str,
                        env: dict) -> bool:
  """Log in, retrying ONLY connection-level failures: the mac shell
  runners have an intermittent link to the remote (recurring "no route to
  host"), while a definitive auth rejection has answered the question and
  re-sending the same bad credential arms JFrog's brute-force lockout —
  after which even the CORRECT password gets a 403 that masquerades as
  'wrong user or password', and every further retry re-arms the block."""
  login_env = {**env, **_login_credentials(name, user, pw)}
  for attempt in range(6):
    proc = subprocess.run([conan, "remote", "login", name, user],
                          env=login_env, capture_output=True, text=True)
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    if proc.returncode == 0:
      return True
    kind = _login_failure_kind(proc.stdout + proc.stderr)
    if kind == "auth":
      print(f"ERROR: {name} REJECTED the credentials — not retrying "
            "(retries against a wrong password trip the server's "
            "brute-force lockout). Fix CONAN_REMOTE_USER/PASS (.env / "
            "CI_ARTIFACTORY_*) and re-run.")
      return False
    if kind == "lockout":
      print(f"ERROR: {name} is refusing logins after repeated failures "
            "(server lockout) — stopping: every retry re-arms the "
            "block. Wait out the server's retry-after above, verify "
            "the credentials, then re-run.")
      return False
    print(f"  remote login failed (flaky link) -- retry {attempt + 1}/6 in 10s")
    time.sleep(10)
  return False


def register_conan_remote() -> bool:
  """Register + log into the project's conan remote from the env seam
  (conan_remote_env — CONAN_REMOTE_* / .env / CI_ARTIFACTORY_*). No URL
  configured: no-op. USER+PASS present: log in (with retries — flaky
  links). Under the default name the mirror literally replaces
  conancenter; a custom name additionally removes the public remote so
  resolution never races the mirror. Returns True when the remote ended
  registered and usable (the stamp ensure_conan_remote checks), False
  when skipped or degraded."""
  name, url, user, pw = conan_remote_env()
  if not url:
    print("conan remote URL unset (CONAN_REMOTE_URL / .env) — "
          "skipping remote registration.")
    return False
  conan = _conan_bin()
  if not conan:
    print("conan executable not found — skipping remote registration.")
    return False

  home = _resolved_conan_home()
  home.mkdir(parents=True, exist_ok=True)
  # retry downloads over a flaky remote link -- core.* confs are legal ONLY in
  # global.conf (a profile or a -c both reject them), so append them here once
  gconf = home / "global.conf"
  text = gconf.read_text() if gconf.exists() else ""
  if "core.download:retry" not in text:
    if text and not text.endswith("\n"):
      text += "\n"
    gconf.write_text(text + "core.download:retry=8\ncore.download:retry_wait=15\n")
  env = {**os.environ, "CONAN_HOME": str(home)}

  print(f"+ conan remote add {name} {redact.credentials(url)} --force")
  subprocess.check_call(
    [conan, "remote", "add", name, url, "--force"], env=env)
  if user and pw:
    print(f"+ conan remote login {name} {user} (password from env)")
    remote_reachable = _login_with_retries(conan, name, user, pw, env)
  else:
    # no credentials: an anonymous mirror. There is nothing to probe
    # without a login round-trip, so trust the URL as given.
    remote_reachable = True

  if remote_reachable:
    if name != "conancenter":
      # The private remote proxies + caches conancenter, so a direct
      # conancenter query races the cache and adds an upstream dependency
      # every build. Under the DEFAULT name the add --force above already
      # replaced it; a custom name leaves the public remote behind — drop
      # it. Tolerated so this stays idempotent across pruned containers.
      print("+ conan remote remove conancenter")
      subprocess.run(
        [conan, "remote", "remove", "conancenter"], env=env, check=False)
  else:
    # The link stayed down through every retry. A connection error on the
    # first remote makes conan ABORT resolution outright -- it never falls
    # through to a healthy remote -- so disable the dead JFrog remote and
    # fall back to the public conancenter (which JFrog only proxies anyway,
    # so every dep is resolvable there). Slower -- source build via
    # --build=missing -- but it keeps the artifact obtainable when the
    # private link is down instead of failing setup outright.
    print(f"WARNING: no working login to {name} -- disabling it and "
          "degrading to public conancenter (source build)")
    subprocess.run(
      [conan, "remote", "disable", name], env=env, check=False)
    subprocess.run(
      [conan, "remote", "add", "conancenter",
       "https://center2.conan.io", "--force"], env=env, check=False)
  return remote_reachable


def ensure_conan_remote(force: bool = False) -> None:
  """The every-run gate: make the configured remote real WITHOUT paying a
  network round-trip on every command. A fingerprint of the env seam
  (name|url|user|pass) is stamped into CONAN_HOME after a successful
  registration; while the stamp matches AND the remote is still present in
  remotes.json, nothing runs. Editing the .env (or wiping the stamp)
  re-registers and re-logs-in on the next command; `buildutil setup`
  forces it. A failed registration leaves no stamp, so the next run
  retries instead of trusting a degraded state."""
  name, url, user, pw = conan_remote_env()
  if not url:
    if force:
      print("conan remote URL unset (CONAN_REMOTE_URL / .env) — "
            "no remote to register.")
    return
  home = _resolved_conan_home()
  stamp = home / "buildutil-remote.stamp"
  fingerprint = hashlib.sha256(
    "\x1f".join([name, url, user or "", pw or ""]).encode()).hexdigest()
  if not force:
    try:
      registered = any(
        r.get("name") == name and r.get("url") == url
        for r in json.loads((home / "remotes.json").read_text())["remotes"])
    except (OSError, ValueError, KeyError, TypeError):
      registered = False
    if registered and stamp.exists() and stamp.read_text() == fingerprint:
      return
  if register_conan_remote():
    home.mkdir(parents=True, exist_ok=True)
    stamp.write_text(fingerprint)


def deps_fingerprint() -> str:
  """A stamp of the dep SET, so a venv that predates a change to it is
  refreshed. The health probe below cannot answer this: it imports a fixed
  four packages, so anything added later -- [venv] extra_deps, or a declared
  cmake extension pulling its own dep in -- was invisible, and an existing
  checkout silently never installed it."""
  return hashlib.sha256(
    "\x1f".join(config.VENV_DEPS).encode()).hexdigest()


def _deps_stamp() -> Path:
  return config.VENV_DIR / "buildutil-deps.stamp"


def _create_venv() -> None:
  """Create _pyvenv if missing, pip-install the build deps, then wire up the
  buildutil symlink and register the conan remote. Idempotent — returns
  once the venv interpreter exists AND carries the toolchain: a bare
  venv that merely holds the buildutil package (the repo shim creates
  exactly that as its carrier) must still get the pinned deps, or the
  CLI dies on `import typer` one call later."""
  if config.VENV_PY.exists():
    stamp = _deps_stamp()
    fingerprint = deps_fingerprint()
    healthy = subprocess.run(
      [str(config.VENV_PY), "-c", "import typer, conan, gcovr, pytest"],
      capture_output=True).returncode == 0
    if healthy and stamp.is_file() and stamp.read_text() == fingerprint:
      return
    print("venv exists but lacks the toolchain — installing the deps"
          if not healthy else
          "declared dependencies changed — installing them")
    _run([str(config.VENV_PY), "-m", "pip", "install", "--upgrade", "pip"])
    _run([str(config.VENV_PY), "-m", "pip", "install", *config.VENV_DEPS])
    _deps_stamp().write_text(deps_fingerprint())
    # the FULL creation tail, not just the deps: this branch returned
    # early once, and every lane whose venv began life as the shim's
    # bare carrier ran conan with NO REGISTERED REMOTE — macos fell
    # back to anonymous conancenter source builds, windows died
    # uploading to a remote that didn't exist.
    install_venv_symlinks()
    ensure_conan_remote(force=True)
    return
  print(f"creating venv at {config.VENV_DIR}")
  # Python's venv refuses a symlinked target dir, so create at the resolved
  # real path; a CI _pyvenv symlink then points at it transparently.
  venv_target = config.VENV_DIR.resolve() if config.VENV_DIR.is_symlink() else config.VENV_DIR
  _run([sys.executable, "-m", "venv", str(venv_target)])
  _run([str(config.VENV_PY), "-m", "pip", "install", "--upgrade", "pip"])
  _run([str(config.VENV_PY), "-m", "pip", "install", *config.VENV_DEPS])
  _deps_stamp().write_text(deps_fingerprint())
  install_venv_symlinks()
  ensure_conan_remote(force=True)


def _unsatisfied(deps: list[str]) -> list[str]:
  missing = []
  for dep in deps:
    name = re.split(r"[<>=!~\[; ]", dep, maxsplit=1)[0]
    pin = dep.partition("==")[2].strip() if "==" in dep else ""
    try:
      have = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
      missing.append(dep)
    else:
      # anything but an exact pin is pip's call once the name is present
      if pin and have != pin:
        missing.append(dep)
  return missing


def _install_declared_deps() -> None:
  # System mode has no project venv for a declaration to land in, so
  # `[venv] extra_deps` reached cmake as a missing package; the base
  # deps stay unchecked, an image without conan being broken, not a gap.
  wanted = _unsatisfied(config.PROJECT["venv_extra_deps"])
  if "reflect" in config.PROJECT["cmake_extensions"]:
    # by import: the wheel also has to hand over a loadable libclang
    probe = subprocess.run([sys.executable, "-c", "import clang.cindex"],
                           capture_output=True)
    if probe.returncode != 0:
      wanted = [*config.REFLECT_DEPS, *wanted]
  if not wanted:
    return
  site = sysconfig.get_paths()["purelib"]
  if not os.access(site, os.W_OK):
    sys.exit(f"buildutil.toml declares {', '.join(wanted)}, which "
             f"BUILDUTIL_SYSTEM=1 puts in {site} — not writable. Install "
             f"them into {sys.executable}, or unset BUILDUTIL_SYSTEM and "
             f"let the project venv carry them.")
  print(f"declared dependencies missing from {sys.executable} — "
        f"installing {', '.join(wanted)}")
  _run([sys.executable, "-m", "pip", "install", *wanted])


def ensure() -> None:
  """Make the venv exist and re-exec under it when needed. Returns only when
  the current process is the venv interpreter, with the venv's bin dir on
  PATH so child `conan`/`cmake` calls resolve the pinned versions.

  BUILDUTIL_SYSTEM=1 skips the project venv entirely: the running
  interpreter IS the toolchain. A container image that already carries
  the package and every pinned dep sets it, because building a second
  venv per checkout would only drift from the image. It is an
  explicit opt-in, not autodetection: a laptop that happens to have
  typer+conan on the system python should still get the pinned venv."""
  config.load_dotenv()
  if os.environ.get("BUILDUTIL_SYSTEM") == "1":
    _install_declared_deps()
    return
  _create_venv()

  # Identify the venv by sys.prefix (= venv root) vs sys.base_prefix: on
  # Debian the venv's python is a symlink to the system one, so comparing
  # executables would falsely match.
  if Path(sys.prefix).resolve() != config.VENV_DIR.resolve():
    # Make THIS package importable to the venv python. Pre-extraction
    # this was hardcoded tools/ (the bossdeux layout); now it is
    # wherever the running copy actually lives — a pip site-packages,
    # a checkout, or the legacy tools/ dir, all the same expression.
    pkg_parent = str(Path(__file__).resolve().parents[1])
    existing = os.environ.get("PYTHONPATH", "")
    if pkg_parent not in existing.split(os.pathsep):
      os.environ["PYTHONPATH"] = (
        pkg_parent + (os.pathsep + existing if existing else ""))
    cmd = [str(config.VENV_PY), "-m", "buildutil", *sys.argv[1:]]
    if platform.system() == "Windows":
      # -X utf8 is the other half of reconfiguring our own stdout: that
      # fixed what we PRINT, this fixes what we READ and CAPTURE. Under
      # the ANSI codepage every `subprocess.run(..., text=True)` decodes a
      # tool's output in cp1252 and every `read_text()` reads a file the
      # same way -- so a cmake warning with a box-drawing rule, a conan
      # line with an em-dash, or a source file with an accented comment
      # raises UnicodeDecodeError somewhere in the middle of a build. UTF-8
      # mode makes the whole interpreter agree with what the toolchain
      # actually writes, and it has to be on the RE-EXEC because it cannot
      # be turned on from inside a running interpreter.
      cmd.insert(1, "-X")
      cmd.insert(2, "utf8")
      # os.execv on Windows detaches rather than replacing the process, so
      # the build would run unsupervised — wait + propagate instead.
      sys.exit(subprocess.run(cmd).returncode)
    os.execv(str(config.VENV_PY), cmd)

  # Running under the venv now. os.execv didn't source activate, so put the
  # venv bin dir on PATH ourselves for subprocess `conan`/`cmake`/`gcovr`.
  venv_bin = str(config.VENV_BIN_DIR)
  if venv_bin not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = venv_bin + os.pathsep + os.environ.get("PATH", "")
