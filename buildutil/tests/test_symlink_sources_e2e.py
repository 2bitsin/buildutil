"""Symlinked sources, including the checkout where they are not symlinks.

A project can express variant modules by LAYOUT: leaf modules sharing one
pool, each leaf holding links to the sources it wants. `ls` in the leaf is
what it compiles and its CMakeLists stays a bare Init_submodule().

On POSIX the compiler follows the link and nothing here is needed. On a
DEFAULT WINDOWS checkout (core.symlinks=false) git writes each link as a
one-line text file naming its target, which would otherwise be handed to
the compiler as a source. Git's index is the authority — mode 120000 —
so the fallback needs no content guessing.

These tests simulate that checkout the way it actually occurs: commit a
real symlink, then replace the working-tree entry with the text file git
would have written, leaving the index saying 120000.
"""
import shutil
import subprocess

import pytest

from buildutil import deposit

CFG = {"cmake_option_prefix": "ACME", "module_define_prefix": "ACM"}

pytestmark = pytest.mark.skipif(
  shutil.which("cmake") is None or shutil.which("git") is None or
  not (shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")),
  reason="needs cmake, git and a C++ compiler")

ROOT_CMAKE = """\
cmake_minimum_required(VERSION 3.25)
project(links CXX)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/_bdudata/cmake")
include(buildutil)
add_subdirectory(sources)
"""


def _git(root, *args):
  return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)


def _tree(root, link_name="impl.cpp", link_target="../pool/impl.cpp",
          pool_name="impl.cpp", header=False):
  """A pool module and a leaf that links one of its sources."""
  deposit.ensure(root, CFG)
  (root / "CMakeLists.txt").write_text(ROOT_CMAKE)
  src = root / "sources"
  (src / "pool").mkdir(parents=True)
  (src / "leaf").mkdir(parents=True)
  (src / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  (src / "pool" / "CMakeLists.txt").write_text("Init_submodule()\n")
  (src / "pool" / pool_name).write_text("int shared() { return 0; }\n")
  if header:
    (src / "pool" / "shared.h").write_text("#pragma once\ninline int h() { return 0; }\n")
  (src / "leaf" / "CMakeLists.txt").write_text("Init_submodule()\n")
  (src / "leaf" / "main.cpp").write_text(
    "int shared();\nint main() { return shared(); }\n")
  (src / "leaf" / link_name).symlink_to(link_target)
  if header:
    (src / "leaf" / "shared.h").symlink_to("../pool/shared.h")
  _git(root, "init", "-q", ".")
  _git(root, "add", "-A")
  return root


def _defeat_symlinks(root, *names):
  """What a default Windows checkout leaves on disk: the index still says
  mode 120000, the working tree holds a text file naming the target."""
  for name in names:
    path = root / "sources" / "leaf" / name
    target = str(path.readlink()) if path.is_symlink() else None
    path.unlink()
    path.write_text(target if target is not None else "../pool/missing.cpp")


def _configure(root) -> subprocess.CompletedProcess:
  return subprocess.run(["cmake", "-S", str(root), "-B", str(root / "b")],
                        capture_output=True, text=True)


def _build(root) -> subprocess.CompletedProcess:
  cfg = _configure(root)
  assert cfg.returncode == 0, cfg.stdout + cfg.stderr
  return subprocess.run(["cmake", "--build", str(root / "b")],
                        capture_output=True, text=True)


def test_a_real_symlink_compiles(tmp_path):
  """POSIX, unchanged: the compiler follows the link. This is the baseline
  the fallback has to match, so it is asserted rather than assumed."""
  _tree(tmp_path)
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr
  assert (tmp_path / "b" / "bin" / "leaf").is_file()


def test_a_symlink_checked_out_as_text_is_resolved(tmp_path):
  """The Windows default. Without resolution the compiler is handed a file
  containing '../pool/impl.cpp' and fails on it as C++."""
  _tree(tmp_path)
  _defeat_symlinks(tmp_path, "impl.cpp")
  assert not (tmp_path / "sources" / "leaf" / "impl.cpp").is_symlink()
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr
  assert (tmp_path / "b" / "bin" / "leaf").is_file()


def test_a_corrupt_link_is_refused_at_configure(tmp_path):
  """Mode 120000 but the content names nothing that exists — what editing
  'through' a fake symlink leaves behind. As a source it would be a parse
  error pages from the cause."""
  _tree(tmp_path)
  path = tmp_path / "sources" / "leaf" / "impl.cpp"
  path.unlink()
  path.write_text("../pool/does-not-exist.cpp")
  cfg = _configure(tmp_path)
  assert cfg.returncode != 0
  assert "impl.cpp" in cfg.stdout + cfg.stderr


def test_a_symlinked_header_is_refused_with_the_reason(tmp_path):
  """A header cannot be rescued: nobody chooses what #include opens. Fail
  at configure saying so, rather than in the preprocessor."""
  _tree(tmp_path, header=True)
  _defeat_symlinks(tmp_path, "shared.h")
  cfg = _configure(tmp_path)
  assert cfg.returncode != 0
  message = cfg.stdout + cfg.stderr
  assert "shared.h" in message and "include roots" in message


def test_classification_keys_off_the_LINKS_name_not_the_targets(tmp_path):
  """The link's own name decides platform tags, .test/.bench and main.cpp,
  exactly as it does on POSIX where the target's name is never seen. A
  link named for another host must drop out even though its TARGET is a
  plain impl.cpp."""
  _tree(tmp_path, link_name="impl.win32.cpp")
  _defeat_symlinks(tmp_path, "impl.win32.cpp")
  built = _build(tmp_path)
  assert built.returncode != 0, (
    "a .win32 link was compiled on this host — classification followed the "
    "target's name instead of the link's")


def test_a_live_platform_tag_on_the_link_is_kept(tmp_path):
  """The other half, so the test above cannot pass by dropping everything."""
  _tree(tmp_path, link_name="impl.linux.cpp")
  _defeat_symlinks(tmp_path, "impl.linux.cpp")
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr


def _add_header(root, where, name, text="#pragma once\ninline int v() { return 0; }\n"):
  (root / "sources" / where / name).write_text(text)


def test_a_header_in_both_leaf_and_pool_is_refused(tmp_path):
  """The one case where the two checkouts genuinely disagree: a quoted
  #include from a pooled source finds the LEAF copy where links are real
  and the POOL copy where they are text — same tree, different artifact,
  no diagnostic either way. Refuse the overlap and it cannot arise."""
  _tree(tmp_path)
  _add_header(tmp_path, "pool", "shared.h")
  _add_header(tmp_path, "leaf", "shared.h")
  cfg = _configure(tmp_path)
  assert cfg.returncode != 0, "the shadowing overlap configured cleanly"
  message = cfg.stdout + cfg.stderr
  assert "shared.h" in message
  assert "per-variant headers in the leaf" in message


def test_the_same_check_applies_on_a_posix_checkout(tmp_path):
  """It has to fire where the links are REAL too. The tree is what is
  wrong, and it is the POSIX developer who would otherwise ship it —
  their build works, and the divergence lands on someone else."""
  _tree(tmp_path)
  _add_header(tmp_path, "pool", "shared.h")
  _add_header(tmp_path, "leaf", "shared.h")
  assert (tmp_path / "sources" / "leaf" / "impl.cpp").is_symlink()
  assert _configure(tmp_path).returncode != 0


def test_headers_that_do_not_overlap_are_fine(tmp_path):
  """Per-variant headers in the leaf and shared ones in the pool — the
  layout this is meant to protect, not prevent."""
  _tree(tmp_path)
  _add_header(tmp_path, "pool", "shared.h")
  _add_header(tmp_path, "leaf", "variant.h")
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr


def test_a_module_without_links_is_not_subject_to_the_check(tmp_path):
  """No pool, no divergence to prevent: an ordinary module may hold any
  header name it likes, including one another module also has."""
  _tree(tmp_path)
  (tmp_path / "sources" / "leaf" / "impl.cpp").unlink()
  (tmp_path / "sources" / "leaf" / "impl.cpp").write_text(
    "int shared() { return 0; }\n")
  _git(tmp_path, "add", "-A")
  _add_header(tmp_path, "pool", "shared.h")
  _add_header(tmp_path, "leaf", "shared.h")
  built = _build(tmp_path)
  assert built.returncode == 0, built.stdout + built.stderr


def _pool_tree(root, dead_file=False, unlinked_only=False):
  """A pool directory plus a leaf that links out of it — the layout
  symlinked sources enable, and one that must not be warned about."""
  deposit.ensure(root, CFG)
  (root / "CMakeLists.txt").write_text(ROOT_CMAKE)
  src = root / "sources"
  (src / "common").mkdir(parents=True)
  (src / "leaf").mkdir(parents=True)
  (src / "CMakeLists.txt").write_text("Scan_subdirectories()\n")
  (src / "common" / "impl.cpp").write_text("int shared() { return 0; }\n")
  if dead_file or unlinked_only:
    (src / "common" / "orphan.cpp").write_text("int orphan() { return 1; }\n")
  (src / "leaf" / "CMakeLists.txt").write_text("Init_submodule()\n")
  (src / "leaf" / "main.cpp").write_text(
    "int shared();\nint main() { return shared(); }\n")
  if not unlinked_only:
    (src / "leaf" / "impl.cpp").symlink_to("../common/impl.cpp")
  _git(root, "init", "-q", ".")
  _git(root, "add", "-A")
  return root


def test_a_pool_reached_entirely_by_symlinks_is_not_warned_about(tmp_path):
  """'holds .cpp sources but no CMakeLists.txt -- it is NOT being
  built' was false on its face for a pool, and fired on every configure
  of every project using that layout."""
  root = _pool_tree(tmp_path)
  cfg = _configure(root)
  assert cfg.returncode == 0, cfg.stdout + cfg.stderr
  assert "NOT being built" not in cfg.stdout + cfg.stderr, (
    "the pool was reported as an unbuilt near-miss module")


def test_a_pool_file_no_link_reaches_is_reported(tmp_path):
  """The diagnostic actually worth having, which the blanket warning hid:
  a file sitting in a pool that nothing compiles."""
  root = _pool_tree(tmp_path, dead_file=True)
  cfg = _configure(root)
  message = cfg.stdout + cfg.stderr
  assert "orphan.cpp" in message, "a dead pool file was not reported"
  assert "reached by no link" in message
  assert "impl.cpp" not in message.split("reached by no link")[1], (
    "a file that IS reached was named as dead")


def test_a_genuine_near_miss_module_still_warns(tmp_path):
  """No links at all: an ordinary directory of sources somebody forgot to
  give a CMakeLists. The original warning is still right here."""
  root = _pool_tree(tmp_path, unlinked_only=True)
  cfg = _configure(root)
  assert "NOT being built" in cfg.stdout + cfg.stderr


def test_a_borrowed_sources_own_directory_is_reachable(tmp_path):
  """#18: sources are carried into a leaf by links, headers cannot be —
  so the directory a borrowed source came from has to be on the include
  path, and the link already says which one. Nine zero-argument call
  sites in the consumer tree existed only to state that."""
  root = _tree(tmp_path)
  # a header beside the pooled source, reached unqualified from it
  (root / "sources" / "pool" / "helper.h").write_text(
    "#pragma once\ninline int helper() { return 0; }\n")
  (root / "sources" / "pool" / "impl.cpp").write_text(
    '#include "helper.h"\nint shared() { return helper(); }\n')
  _git(root, "add", "-A")
  built = _build(root)
  assert built.returncode == 0, (
    "a pooled source could not reach a header beside it\n"
    + built.stdout + built.stderr)


def test_it_works_on_the_fake_symlink_checkout_too(tmp_path):
  """The pool directory is read off the link TARGET, so it is known in
  both checkouts — not from where the compiler happens to open the file.

  A GUARD, not a regression test: on this checkout the compiler is handed
  the pool path itself, so the includer's own directory already IS the
  pool and the header resolves without any include path. It passes with
  or without the fix. Kept because the two checkouts must not diverge —
  if the fallback ever stops handing over the resolved path, this is what
  notices."""
  root = _tree(tmp_path)
  (root / "sources" / "pool" / "helper.h").write_text(
    "#pragma once\ninline int helper() { return 0; }\n")
  (root / "sources" / "pool" / "impl.cpp").write_text(
    '#include "helper.h"\nint shared() { return helper(); }\n')
  _git(root, "add", "-A")
  _defeat_symlinks(root, "impl.cpp")
  built = _build(root)
  assert built.returncode == 0, built.stdout + built.stderr


def test_the_leafs_own_header_still_wins(tmp_path):
  """Appended, never prepended. A leaf header of the same name must still
  take precedence — which is only observable because 0.22.0 refuses the
  case where BOTH exist, so this asserts the ordering on a name the pool
  does not carry."""
  root = _tree(tmp_path)
  (root / "sources" / "leaf" / "leafonly.h").write_text(
    "#pragma once\ninline int leafv() { return 7; }\n")
  (root / "sources" / "leaf" / "use.cpp").write_text(
    '#include "leafonly.h"\nint use() { return leafv(); }\n')
  _git(root, "add", "-A")
  built = _build(root)
  assert built.returncode == 0, built.stdout + built.stderr


def test_the_collision_message_names_the_source_that_reaches_the_pool(tmp_path):
  """Reported from use: the guard fired correctly but named only the
  header, so proving which file could actually see it needed a `gcc -H`
  run. The link list is right there — say it."""
  _tree(tmp_path)
  (tmp_path / "sources" / "pool" / "shared.h").write_text("#pragma once\n")
  (tmp_path / "sources" / "leaf" / "shared.h").write_text("#pragma once\n")
  cfg = _configure(tmp_path)
  assert cfg.returncode != 0
  message = cfg.stdout + cfg.stderr
  assert "shared.h" in message
  assert "impl.cpp" in message, (
    "the message does not name the borrowed source that reaches the pool")


def test_an_unstaged_symlink_says_why_it_is_being_ignored(tmp_path):
  """Also reported from use: git's index is the authority, so a symlink
  created and not yet added is invisible — and the module behaved as
  though the file simply was not there, with nothing said."""
  _tree(tmp_path)
  (tmp_path / "sources" / "leaf" / "extra.cpp").symlink_to("../pool/impl.cpp")
  # deliberately NOT git added
  cfg = _configure(tmp_path)
  message = cfg.stdout + cfg.stderr
  assert "extra.cpp" in message and "git add" in message, (
    "an unstaged symlink was ignored silently")


def test_a_staged_symlink_produces_no_such_warning(tmp_path):
  """The warning must not fire for the normal case, or it becomes noise
  on every configure — which is the mistake it was."""
  _tree(tmp_path)
  cfg = _configure(tmp_path)
  assert "not in git's index" not in cfg.stdout + cfg.stderr
