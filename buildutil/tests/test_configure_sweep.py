"""A data_dir() tree holds exactly what this run put there.

`emit`/`emit_bytes` content-diff and declare, and nothing ever swept the
tree against that, so a hook that RENAMED its payload shipped both names:
a `*.embed/` directory is declared by its contents, and the file an
earlier run wrote was still sitting in it. The build stayed green --
xoctet's UI started shipping gzipped and the binary carried the
uncompressed originals as well, which made the change's entire point
false, and nothing said a word until someone counted `strings`.

These run the hook the way _buildutil_exec_configure does: the three
environment variables, and the module importable.
"""
import os
import subprocess
import sys
from pathlib import Path

PYSUPPORT = Path(__file__).resolve().parents[1] / "pysupport"


def _run(tmp_path: Path, body: str) -> Path:
  """One configure run. Returns the per-profile output root."""
  out = tmp_path / "out"
  shared = tmp_path / "shared"
  out.mkdir(exist_ok=True)
  shared.mkdir(exist_ok=True)
  script = tmp_path / "configure.py"
  script.write_text("import buildutil_configure as cfg\n" + body)
  done = subprocess.run(
    [sys.executable, str(script)], cwd=tmp_path, capture_output=True,
    text=True,
    env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
         "PYTHONPATH": str(PYSUPPORT),
         "CONFIGURE_OUTPUT_DIR": str(out),
         "CONFIGURE_SHARED_DIR": str(shared),
         "CONFIGURE_PLATFORM_TAGS": "posix,linux",
         "CONFIGURE_MANIFEST": str(tmp_path / "manifest")})
  assert done.returncode == 0, done.stdout + done.stderr
  return out


def _listing(directory: Path) -> list[str]:
  return sorted(str(p.relative_to(directory)) for p in directory.rglob("*"))


EMITS = "cfg.emit('ui.embed/{}', 'payload')\n"


def test_a_renamed_payload_does_not_ship_twice(tmp_path):
  """The reported failure, at its smallest."""
  _run(tmp_path, EMITS.format("app.js"))
  out = _run(tmp_path, EMITS.format("app.js.gz"))
  assert _listing(out / "ui.embed") == ["app.js.gz"]


def test_an_unchanged_payload_is_left_alone(tmp_path):
  """The content-diff is why the tree is not simply wiped: rewriting
  identical bytes moves an mtime and rebuilds whatever embeds them."""
  out = _run(tmp_path, EMITS.format("app.js"))
  before = (out / "ui.embed" / "app.js").stat().st_mtime_ns
  _run(tmp_path, EMITS.format("app.js"))
  assert (out / "ui.embed" / "app.js").stat().st_mtime_ns == before


WRITES_BY_HAND = "(cfg.data_dir('ui.embed') / '{}').write_text('payload')\n"


def test_a_file_the_hook_wrote_without_emit_survives(tmp_path):
  """A hook may write into the tree directly -- data_dir() exists to be
  written into -- so 'not in the manifest' cannot be the rule."""
  _run(tmp_path, WRITES_BY_HAND.format("app.js"))
  out = _run(tmp_path, WRITES_BY_HAND.format("app.js"))
  assert _listing(out / "ui.embed") == ["app.js"]


def test_what_the_hook_stopped_writing_by_hand_goes_too(tmp_path):
  _run(tmp_path, WRITES_BY_HAND.format("old.js"))
  out = _run(tmp_path, WRITES_BY_HAND.format("new.js"))
  assert _listing(out / "ui.embed") == ["new.js"]


def test_a_directory_the_sweep_empties_goes_with_its_files(tmp_path):
  """A stale `ui.embed/js/` is the same class of bug as a stale file."""
  _run(tmp_path, EMITS.format("js/app.js"))
  out = _run(tmp_path, EMITS.format("app.js"))
  assert _listing(out / "ui.embed") == ["app.js"]


def test_keep_leaves_a_tree_something_else_co_writes_alone(tmp_path):
  out = _run(tmp_path, EMITS.format("app.js"))
  (out / "ui.embed" / "by-someone-else.js").write_text("payload")
  _run(tmp_path, "cfg.data_dir('ui.embed', keep=True)\n"
                 + EMITS.format("app.js"))
  assert _listing(out / "ui.embed") == ["app.js", "by-someone-else.js"]


def test_only_a_data_directory_is_swept(tmp_path):
  """The contents of a `*.embed/` or `*.install/` tree ARE a declaration;
  a generated header is reached by name and nothing globs its directory,
  so nothing there is deleted for going unmentioned."""
  out = _run(tmp_path, "cfg.emit('tables/enums.hpp', 'enum {};')\n")
  _run(tmp_path, "cfg.emit('tables/other.hpp', 'enum {};')\n")
  assert _listing(out / "tables") == ["enums.hpp", "other.hpp"]


def test_a_tagged_data_directory_is_swept_as_well(tmp_path):
  _run(tmp_path, "cfg.emit('ui.embed.linux/old.js', 'payload')\n")
  out = _run(tmp_path, "cfg.emit('ui.embed.linux/new.js', 'payload')\n")
  assert _listing(out / "ui.embed.linux") == ["new.js"]


def test_the_shared_root_is_swept_on_the_same_terms(tmp_path):
  """`data_dir(shared=True)` is a data directory too, now that the
  conventions read the shared root."""
  _run(tmp_path, "cfg.emit('ui.embed/old.js', 'payload', shared=True)\n")
  _run(tmp_path, "cfg.emit('ui.embed/new.js', 'payload', shared=True)\n")
  assert _listing(tmp_path / "shared" / "ui.embed") == ["new.js"]
