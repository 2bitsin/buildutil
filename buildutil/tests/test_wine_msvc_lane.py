"""Claiming the msvc-wine cross lane. The name `cl` alone is not
evidence: OpenCL and Common Lisp launchers use it too, and a false
positive silently flips the whole build to a Windows cross-build."""
import pytest

import buildutil.engine as engine

WRAPPER = """\
#!/bin/sh
BIN=$(dirname $0)
. $BIN/msvcenv.sh
exec $WINE "$BIN/../../vc/tools/msvc/14.41/bin/Hostx64/x64/cl.exe" "$@"
"""


@pytest.fixture
def linux(monkeypatch):
  monkeypatch.setattr(engine.platform, "system", lambda: "Linux")
  monkeypatch.setattr(engine, "_emscripten_live", lambda: False)
  monkeypatch.delenv("BUILDUTIL_WINE_MSVC", raising=False)


@pytest.fixture
def cl_on_path(monkeypatch, tmp_path):
  def install(body, beside_msvcenv=False):
    cl = tmp_path / "cl"
    cl.write_text(body)
    cl.chmod(0o755)
    if beside_msvcenv:
      (tmp_path / "msvcenv.sh").write_text("MSVCVER=14.41\nSDKVER=10.0.22621.0\n")
    monkeypatch.setattr(engine.shutil, "which",
                        lambda name: str(cl) if name == "cl" else None)
    return cl
  return install


def test_a_bare_cl_never_claims_the_lane(linux, cl_on_path):
  cl_on_path("#!/bin/sh\nexec /usr/lib/opencl/clcc \"$@\"\n")
  assert engine._wine_msvc_live() is False


def test_a_binary_cl_never_claims_the_lane(linux, cl_on_path):
  cl_on_path("\x7fELF\x02\x01\x01\x00" + "\x00" * 200)
  assert engine._wine_msvc_live() is False


def test_msvcenv_beside_the_wrapper_claims_the_lane(linux, cl_on_path):
  cl_on_path(WRAPPER, beside_msvcenv=True)
  assert engine._wine_msvc_live() is True


def test_a_wine_flavoured_cl_claims_the_lane(linux, cl_on_path):
  cl_on_path('#!/bin/sh\nexec wine64 "$HOME/msvc/cl.exe" "$@"\n')
  assert engine._wine_msvc_live() is True


def test_no_cl_at_all(linux, monkeypatch):
  monkeypatch.setattr(engine.shutil, "which", lambda name: None)
  assert engine._wine_msvc_live() is False


def test_the_override_suppresses_a_true_positive(linux, cl_on_path,
                                                 monkeypatch):
  cl_on_path(WRAPPER, beside_msvcenv=True)
  monkeypatch.setenv("BUILDUTIL_WINE_MSVC", "0")
  assert engine._wine_msvc_live() is False


def test_the_override_forces_the_lane(linux, monkeypatch):
  monkeypatch.setattr(engine.shutil, "which", lambda name: None)
  monkeypatch.setenv("BUILDUTIL_WINE_MSVC", "1")
  assert engine._wine_msvc_live() is True


def test_a_non_linux_host_never_claims_the_lane(cl_on_path, monkeypatch):
  monkeypatch.delenv("BUILDUTIL_WINE_MSVC", raising=False)
  monkeypatch.setattr(engine.platform, "system", lambda: "Windows")
  monkeypatch.setattr(engine, "_emscripten_live", lambda: False)
  cl_on_path(WRAPPER, beside_msvcenv=True)
  assert engine._wine_msvc_live() is False


def test_the_unavailable_lane_error_names_the_override(monkeypatch, capsys):
  monkeypatch.setattr(engine, "_available_compilers", lambda: {"gcc": "/usr/bin/gcc"})
  with pytest.raises(engine.typer.Exit):
    engine._select_compiler("wine-msvc", "auto")
  assert "BUILDUTIL_WINE_MSVC" in capsys.readouterr().err


def test_claiming_the_lane_says_so(monkeypatch, capsys):
  monkeypatch.setattr(engine, "_available_compilers",
                      lambda: {"wine-msvc": "/opt/msvc/bin/x64/cl"})
  engine._select_compiler("wine-msvc", "auto")
  out = capsys.readouterr().out
  assert "msvc-wine" in out and "/opt/msvc/bin/x64/cl" in out
