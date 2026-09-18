"""Finding vcvars64.bat on Windows. The tests run on Linux, so the probe
is exercised at its seams: the candidate ORDER and the vswhere call, with
the subprocess mocked. Sourcing the bat itself is not covered."""
import subprocess

import pytest

import buildutil.engine as engine

VSWHERE_ARGS = ["-latest", "-products", "*", "-requires",
                "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                "-property", "installationPath"]


@pytest.fixture
def no_vswhere(monkeypatch):
  def missing(argv, **kw):
    raise FileNotFoundError(argv[0])
  monkeypatch.setattr(engine.subprocess, "check_output", missing)
  monkeypatch.delenv("VCVARS_PATH", raising=False)


def _slashed(paths):
  return [str(p).replace("\\", "/") for p in paths]


def _mock_vswhere(monkeypatch, out):
  calls = []
  def check_output(argv, **kw):
    calls.append(argv)
    return out
  monkeypatch.setattr(engine.subprocess, "check_output", check_output)
  return calls


def test_the_private_install_path_is_never_probed(no_vswhere):
  assert not any("VSBT18" in c for c in _slashed(engine._vcvars_candidates()))


def test_vcvars_path_override_is_probed_first(monkeypatch, no_vswhere):
  monkeypatch.setenv("VCVARS_PATH", "/somewhere/vcvars64.bat")
  assert _slashed(engine._vcvars_candidates())[0] == "/somewhere/vcvars64.bat"


def test_vswhere_installs_come_before_the_conventional_layouts(monkeypatch):
  monkeypatch.delenv("VCVARS_PATH", raising=False)
  _mock_vswhere(monkeypatch, "D:\\VS\\2022\\Community\n")
  candidates = _slashed(engine._vcvars_candidates())
  assert candidates[0] == "D:/VS/2022/Community/VC/Auxiliary/Build/vcvars64.bat"
  assert any("Program Files" in c for c in candidates[1:])


def test_vswhere_is_asked_for_the_vc_toolset(monkeypatch):
  monkeypatch.delenv("VCVARS_PATH", raising=False)
  calls = _mock_vswhere(monkeypatch, "")
  engine._vcvars_candidates()
  (argv,) = calls
  assert argv[0].replace("\\", "/").endswith(
    "Microsoft Visual Studio/Installer/vswhere.exe")
  assert argv[1:] == VSWHERE_ARGS


def test_vswhere_output_is_parsed_line_by_line(monkeypatch):
  monkeypatch.delenv("VCVARS_PATH", raising=False)
  _mock_vswhere(monkeypatch, "C:\\A\r\n\nC:\\B  \n")
  candidates = _slashed(engine._vcvars_candidates())[:2]
  assert candidates == ["C:/A/VC/Auxiliary/Build/vcvars64.bat",
                        "C:/B/VC/Auxiliary/Build/vcvars64.bat"]


def test_a_failing_vswhere_is_not_fatal(monkeypatch):
  monkeypatch.delenv("VCVARS_PATH", raising=False)
  def boom(argv, **kw):
    raise subprocess.CalledProcessError(1, argv)
  monkeypatch.setattr(engine.subprocess, "check_output", boom)
  assert engine._vcvars_candidates()


def test_the_conventional_layouts_cover_every_year_and_edition(no_vswhere):
  candidates = _slashed(engine._vcvars_candidates())
  for year in ("2019", "2022", "2026"):
    for edition in ("BuildTools", "Community", "Professional", "Enterprise"):
      assert any(f"/{year}/{edition}/" in c for c in candidates), (year, edition)


def test_not_finding_a_toolchain_names_what_was_probed(no_vswhere):
  with pytest.raises(RuntimeError) as excinfo:
    engine._find_vcvars()
  message = str(excinfo.value)
  assert "VCVARS_PATH" in message
  assert "vswhere" in message
  for year in ("2019", "2022", "2026"):
    assert year in message
