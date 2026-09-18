"""ccache launcher injection (engine._ccache_launcher_args).

The load-bearing behaviour: the native and osxcross configures gain the two
CMAKE_<LANG>_COMPILER_LAUNCHER flags exactly when ccache is on PATH (osxcross
additionally pinning CCACHE_COMPILERCHECK=content against shim-hashing stale
hits), the wine-msvc lane never gains them, and every gated-out or
ccache-less configure explicitly CLEARS the launcher cache entries so a dir
once configured with ccache survives ccache disappearing (!211 review F2).
"""
import os

from buildutil import engine

# Every language buildutil can compile. OBJC/OBJCXX joined the list when
# .m/.mm became module sources on an Apple target: cmake ignores a
# launcher for a language it never enabled, and leaving them out meant
# the one platform with Objective-C sources compiled them uncached.
_LANGS = ("C", "CXX", "OBJC", "OBJCXX")
_CLEAR = [f"-DCMAKE_{lang}_COMPILER_LAUNCHER=" for lang in _LANGS]
_SET = [f"-DCMAKE_{lang}_COMPILER_LAUNCHER=/usr/bin/ccache" for lang in _LANGS]


def test_native_with_ccache_on_path(monkeypatch):
  monkeypatch.setattr(engine.shutil, "which",
                      lambda name: "/usr/bin/ccache" if name == "ccache" else None)
  assert engine._ccache_launcher_args() == _SET


def test_native_without_ccache_clears_stale_launcher(monkeypatch):
  # A dir configured while ccache existed must not keep exec'ing the dead
  # launcher after ccache vanishes — the entries are cleared, not skipped.
  monkeypatch.setattr(engine.shutil, "which", lambda name: None)
  assert engine._ccache_launcher_args() == _CLEAR


def test_wine_msvc_lane_stays_out(monkeypatch):
  # _wine_msvc_live() keys on `cl` being on a Linux PATH; with both cl and
  # ccache resolvable the wine gate must still win — and still clear.
  monkeypatch.setattr(engine.shutil, "which", lambda name: f"/usr/bin/{name}")
  monkeypatch.setattr(engine, "_wine_msvc_live", lambda: True)
  monkeypatch.setattr(engine, "_osxcross_live", lambda: False)
  assert engine._ccache_launcher_args() == _CLEAR


def test_osxcross_lane_gains_ccache_with_content_check(monkeypatch):
  monkeypatch.setattr(engine.shutil, "which", lambda name: f"/usr/bin/{name}")
  monkeypatch.setattr(engine, "_wine_msvc_live", lambda: False)
  monkeypatch.setattr(engine, "_osxcross_live", lambda: True)
  monkeypatch.delenv("CCACHE_COMPILERCHECK", raising=False)
  assert engine._ccache_launcher_args() == _SET
  assert os.environ["CCACHE_COMPILERCHECK"] == "content"


def test_osxcross_respects_explicit_compilercheck(monkeypatch):
  monkeypatch.setattr(engine.shutil, "which", lambda name: f"/usr/bin/{name}")
  monkeypatch.setattr(engine, "_wine_msvc_live", lambda: False)
  monkeypatch.setattr(engine, "_osxcross_live", lambda: True)
  monkeypatch.setenv("CCACHE_COMPILERCHECK", "mtime")
  engine._ccache_launcher_args()
  assert os.environ["CCACHE_COMPILERCHECK"] == "mtime"


def test_configure_splices_launcher_into_cmake_argv(monkeypatch, tmp_path):
  # The seam itself (!211 review F3): _cmake_configure must actually pass
  # the helper's flags to cmake — the helper being correct is not enough.
  monkeypatch.setattr(engine.shutil, "which",
                      lambda name: "/usr/bin/ccache" if name == "ccache" else None)
  monkeypatch.setattr(engine, "_stamp_build_info", lambda: None)
  monkeypatch.setattr(engine, "_regen_clangd", lambda build_dir: None)
  import buildutil.vscode
  monkeypatch.setattr(buildutil.vscode, "refresh", lambda active=None: None)
  calls = []
  monkeypatch.setattr(engine.subprocess, "check_call",
                      lambda argv, **kw: calls.append(argv))
  engine._cmake_configure(tmp_path, "Release", tests=False, bench=False)
  (argv,) = calls
  assert "-DCMAKE_C_COMPILER_LAUNCHER=/usr/bin/ccache" in argv
  assert "-DCMAKE_CXX_COMPILER_LAUNCHER=/usr/bin/ccache" in argv
