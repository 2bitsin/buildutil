"""apply_patch.py — the generic patch-overlay git-apply helper the
rendered machinery (buildutil.cmake) calls. Part of the package since
the extraction; run as `python -m buildutil.apply_patch`."""
import subprocess

from buildutil import apply_patch


def _make_diff(tmp_path, name, before, after):
  """A real `a/<name>` / `b/<name>` unified diff, the form the build's patches carry."""
  a = tmp_path / "a"
  b = tmp_path / "b"
  a.mkdir()
  b.mkdir()
  (a / name).write_text(before)
  (b / name).write_text(after)
  result = subprocess.run(["diff", "-u", f"a/{name}", f"b/{name}"],
                          cwd=tmp_path, capture_output=True, text=True)
  patch = tmp_path / f"{name}.patch"
  patch.write_text(result.stdout)
  return patch


def test_apply_patch_writes_patched_copy_leaving_source(tmp_path):
  name = "all-chip-models.xed"
  source = tmp_path / name
  source.write_text("I286REAL: X87\n")
  patch = _make_diff(tmp_path, name, "I286REAL: X87\n", "I286REAL:\n")
  output = tmp_path / "generated" / "sources" / "decodex86" / "dgen" / name

  apply_patch.apply_patch(source, patch, output)

  assert output.read_text() == "I286REAL:\n"            # patched copy
  assert source.read_text() == "I286REAL: X87\n"        # source untouched


def test_apply_patch_raises_when_it_does_not_apply(tmp_path):
  name = "all-chip-models.xed"
  source = tmp_path / name
  source.write_text("totally different content\n")
  patch = _make_diff(tmp_path, name, "I286REAL: X87\n", "I286REAL:\n")
  output = tmp_path / "out" / name

  try:
    apply_patch.apply_patch(source, patch, output)
  except SystemExit as exc:
    assert "did not apply" in str(exc)
  else:
    raise AssertionError("expected a SystemExit for a non-applying patch")
