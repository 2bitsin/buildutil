"""`buildutil install` — the tool travels with the project.

The contract, verbatim from the owner: a vendored copy is tracked with
the project, upgradeable in place, a fresh clone builds on a system
with no buildutil installed, and `./buildutil build --args` is the way
to say it — through the venv the first run bootstraps. The load-bearing
claims are proven by actually running the `./buildutil` launcher from a
bare environment — not by asserting files landed.
"""
import os
import stat
import subprocess
import sys
from pathlib import Path

from buildutil import installcmd

PKG = Path(installcmd.__file__).resolve().parent

VENDORED_AT = Path(".buildutil") / "buildutil"


def _bare_env(tmp_path):
  """An environment the way a fresh system offers it: no PYTHONPATH to
  the real package, a `python3` on PATH (shimmed to this interpreter so
  the test is hermetic), and the usual sh utilities."""
  bindir = tmp_path / "_shim-bin"
  bindir.mkdir(exist_ok=True)
  shim = bindir / "python3"
  if not shim.exists():
    shim.symlink_to(sys.executable)
  return {"PATH": os.pathsep.join([str(bindir), "/usr/bin", "/bin"]),
          "HOME": str(tmp_path)}


def _launcher(root, *args, env=None):
  return subprocess.run([str(root / "buildutil"), *args], cwd=root,
                        capture_output=True, text=True, env=env)


def test_install_vendors_a_runnable_copy(tmp_path):
  installcmd.vendor_into(tmp_path)
  dst = tmp_path / VENDORED_AT
  assert (dst / "__main__.py").is_file()
  assert (dst / "templates" / "cmake" / "base" / "buildutil.cmake").is_file()
  assert not list(dst.rglob("__pycache__")), "cache dirs were vendored"
  version = (dst / "VENDORED").read_text().strip()
  assert version and "\n" not in version
  # the copy answers for ITSELF, through the launcher, from a bare env
  out = _launcher(tmp_path, "--version", env=_bare_env(tmp_path))
  assert out.returncode == 0, out.stderr
  assert version in out.stdout, (
    "the vendored copy does not report its own stamped version:\n"
    + out.stdout)


def test_launcher_is_executable_posix_sh(tmp_path):
  installcmd.vendor_into(tmp_path)
  launcher = tmp_path / "buildutil"
  assert launcher.is_file()
  assert launcher.stat().st_mode & stat.S_IXUSR
  assert launcher.read_text().startswith("#!/bin/sh\n")


def test_launcher_prefers_the_project_venv(tmp_path):
  """`./buildutil` must run through the venv the bootstrap creates —
  the owner's word — not through whatever python3 is ambient. Proven by
  planting a marker-emitting _pyvenv python and watching it get used."""
  installcmd.vendor_into(tmp_path)
  venv_bin = tmp_path / "_pyvenv" / "bin"
  venv_bin.mkdir(parents=True)
  stub = venv_bin / "python"
  stub.write_text("#!/bin/sh\necho VENV-PYTHON-USED >&2\n"
                  f"exec {sys.executable} \"$@\"\n")
  stub.chmod(0o755)
  out = _launcher(tmp_path, "--version", env=_bare_env(tmp_path))
  assert out.returncode == 0, out.stderr
  assert "VENV-PYTHON-USED" in out.stderr, (
    "the launcher did not run through _pyvenv/bin/python")
  version = (tmp_path / VENDORED_AT / "VENDORED").read_text().strip()
  assert version in out.stdout


def test_launcher_falls_back_to_python3_before_the_venv_exists(tmp_path):
  """First run on a fresh clone: no _pyvenv yet, python3 must carry it
  (its bootstrap is what creates the venv)."""
  installcmd.vendor_into(tmp_path)
  assert not (tmp_path / "_pyvenv").exists()
  out = _launcher(tmp_path, "--version", env=_bare_env(tmp_path))
  assert out.returncode == 0, out.stderr
  version = (tmp_path / VENDORED_AT / "VENDORED").read_text().strip()
  assert version in out.stdout


def test_launcher_in_a_nonvendored_project_uses_the_console_script(tmp_path):
  """init writes the same launcher without a vendored copy; there the
  venv console script (which pins the installed package) is the target,
  arguments forwarded verbatim."""
  installcmd.write_launcher(tmp_path)
  venv_bin = tmp_path / "_pyvenv" / "bin"
  venv_bin.mkdir(parents=True)
  stub = venv_bin / "buildutil"
  stub.write_text("#!/bin/sh\necho \"CONSOLE:$@\"\n")
  stub.chmod(0o755)
  out = _launcher(tmp_path, "build", "--jobs", "3",
                  env=_bare_env(tmp_path))
  assert out.returncode == 0, out.stderr
  assert "CONSOLE:build --jobs 3" in out.stdout


def test_launcher_falls_back_to_path_install(tmp_path):
  """No vendored copy, no venv: a `buildutil` on PATH (the managed-box
  shape) is used."""
  installcmd.write_launcher(tmp_path)
  bindir = tmp_path / "_shim-bin"
  bindir.mkdir()
  stub = bindir / "buildutil"
  stub.write_text("#!/bin/sh\necho \"PATHBIN:$@\"\n")
  stub.chmod(0o755)
  env = {"PATH": os.pathsep.join([str(bindir), "/usr/bin", "/bin"]),
         "HOME": str(tmp_path)}
  out = _launcher(tmp_path, "test", env=env)
  assert out.returncode == 0, out.stderr
  assert "PATHBIN:test" in out.stdout


def test_launcher_never_clobbers_a_foreign_file(tmp_path):
  """A project's own ./buildutil script is the project's — kept."""
  own = tmp_path / "buildutil"
  own.write_text("#!/bin/sh\necho mine\n")
  installcmd.write_launcher(tmp_path)
  assert own.read_text() == "#!/bin/sh\necho mine\n"


def test_install_migrates_the_pre042_layout(tmp_path):
  """A copy vendored by 0.41 sits at <root>/buildutil — the launcher's
  path. Re-running install moves the vendoring to .buildutil/ and
  retires the old directory so the launcher can land."""
  legacy = tmp_path / "buildutil"
  legacy.mkdir()
  (legacy / "VENDORED").write_text("0.41.1\n")
  (legacy / "__main__.py").write_text("")
  installcmd.vendor_into(tmp_path)
  assert (tmp_path / VENDORED_AT / "VENDORED").is_file()
  assert (tmp_path / "buildutil").is_file(), (
    "the legacy directory was not replaced by the launcher")


def test_a_fresh_clone_inits_with_the_vendored_copy_alone(tmp_path):
  """Empty dir + vendored copy + `./buildutil init` = a project, with
  no buildutil installed anywhere the child can see."""
  installcmd.vendor_into(tmp_path)
  out = _launcher(tmp_path, "init", "--name", "acme", "--bare",
                  env=_bare_env(tmp_path))
  assert out.returncode == 0, out.stdout + out.stderr
  assert (tmp_path / "buildutil.toml").is_file()
  assert (tmp_path / "_bdudata" / "cmake" / "buildutil.cmake").is_file()


def test_install_in_an_untouched_directory_also_inits(tmp_path):
  """The one-command story: install where no trace of buildutil exists
  runs init too (non-tty, so defaults apply — prompting is tty-only)."""
  proc = subprocess.run(
    [sys.executable, "-m", "buildutil", "install"],
    cwd=tmp_path, capture_output=True, text=True,
    env={"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path),
         "PYTHONPATH": str(PKG.parent)})
  assert proc.returncode == 0, proc.stdout + proc.stderr
  assert (tmp_path / VENDORED_AT / "VENDORED").is_file()
  assert (tmp_path / "buildutil").is_file(), "no ./buildutil launcher"
  assert (tmp_path / "buildutil.toml").is_file(), (
    "install did not run init in an untouched directory")
  assert (tmp_path / "CMakeLists.txt").is_file(), (
    "init did not scaffold the root CMakeLists")


def test_no_init_vendors_only(tmp_path):
  proc = subprocess.run(
    [sys.executable, "-m", "buildutil", "install", "--no-init"],
    cwd=tmp_path, capture_output=True, text=True,
    env={"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path),
         "PYTHONPATH": str(PKG.parent)})
  assert proc.returncode == 0, proc.stdout + proc.stderr
  assert (tmp_path / VENDORED_AT / "VENDORED").is_file()
  assert not (tmp_path / "buildutil.toml").exists()


def test_install_inside_a_project_lands_at_the_project_root(tmp_path):
  """Vendoring belongs beside buildutil.toml, wherever cwd is."""
  (tmp_path / "buildutil.toml").write_text('[project]\nname = "x"\n')
  sub = tmp_path / "sources" / "deep"
  sub.mkdir(parents=True)
  proc = subprocess.run(
    [sys.executable, "-m", "buildutil", "install"],
    cwd=sub, capture_output=True, text=True,
    env={"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path),
         "PYTHONPATH": str(PKG.parent)})
  assert proc.returncode == 0, proc.stdout + proc.stderr
  assert (tmp_path / VENDORED_AT / "VENDORED").is_file()
  assert not (sub / ".buildutil").exists()


def test_reinstalling_over_the_running_copy_is_a_noop(tmp_path):
  """The degenerate case: `install` run BY the vendored copy in its own
  project must not delete the files it is executing from."""
  installcmd.vendor_into(tmp_path)
  proc = _launcher(tmp_path, "install", "--no-init",
                   env=_bare_env(tmp_path))
  assert proc.returncode == 0, proc.stdout + proc.stderr
  assert "already running the vendored copy" in proc.stdout
  assert (tmp_path / VENDORED_AT / "__main__.py").is_file()


def test_init_prompts_at_a_terminal(tmp_path, monkeypatch):
  """Run init, answer the questions, ready to go. Only at a tty with
  nothing on the line — a pipeline that got prompted would hang."""
  from buildutil import initcmd
  monkeypatch.chdir(tmp_path)
  monkeypatch.setattr("sys.stdin.isatty", lambda: True)
  # name, cmake prefix (default), module prefix, conan remote (none),
  # packaging choice (no)
  answers = iter(["acme", "", "ACM", "", "3"])
  monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
  initcmd.main(["--bare"])
  toml = (tmp_path / "buildutil.toml").read_text()
  assert 'name = "acme"' in toml
  assert 'cmake_option_prefix = "ACME"' in toml     # default accepted
  assert 'module_define_prefix = "ACM"' in toml     # answer taken


def test_init_scaffold_includes_the_launcher(tmp_path, monkeypatch):
  from buildutil import initcmd
  monkeypatch.chdir(tmp_path)
  initcmd.main(["--name", "acme"])
  launcher = tmp_path / "buildutil"
  assert launcher.is_file()
  assert launcher.stat().st_mode & stat.S_IXUSR


def test_vendoring_stamps_the_sources_version_not_the_runners(tmp_path):
  """The update path: a NEW version fetched into scratch (pip --target
  leaves dist-info beside the package) must be stamped as itself — the
  running copy's version is exactly the wrong answer there."""
  scratch = tmp_path / "scratch"
  (scratch / "buildutil").mkdir(parents=True)
  (scratch / "buildutil" / "__init__.py").write_text("")
  info = scratch / "buildutil-9.9.9.dist-info"
  info.mkdir()
  (info / "METADATA").write_text("Name: buildutil\nVersion: 9.9.9\n")
  dest_root = tmp_path / "proj"
  dest_root.mkdir()
  installcmd.vendor_into(dest_root, source=scratch / "buildutil")
  stamped = (dest_root / VENDORED_AT / "VENDORED").read_text().strip()
  assert stamped == "9.9.9", (
    f"stamped {stamped!r} — the running copy's version, not the source's")
