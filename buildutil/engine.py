"""buildutil build engine: compiler detection/selection, conan profile
generation, the conan + cmake orchestration, IDE-config regen, and the shared
run helpers. Pure build logic — no typer commands (those live in commands/)."""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import typer

from . import config, modules
from . import options as _project_options
from .config import (BDUDATA_DIR, BUILD_PID_FILE, CMAKE_PREFIX,
                     DEFAULT_CONAN_HOME, INSTALL_PREFIX,
                     MODULE_DEFINE_PREFIX, MODULES_INI, REPO_ROOT, VENV_PY,
                     cmake_path)


def _deposit_dir():
  from . import deposit
  return deposit.cmake_dir(Path(HERE))


def _enabled_module_defines() -> list[str]:
  """<PREFIX>_<NAME>_ENABLED=1 for every module that builds -- the same
  modules.ini toggle cmake reads, surfaced to the preprocessor so code
  can gate on a sibling module's presence."""
  dormant = modules.dormant_set(MODULES_INI, system=_target_system())
  return modules.enabled_definitions(REPO_ROOT / "sources", MODULES_INI,
                                     defaults=dormant)


def _target_system() -> str:
  """CMake's target system for the selected compiler lane. A cross
  compiler takes precedence over the machine running the driver."""
  if _emscripten_live():
    return "Emscripten"
  if _wine_msvc_live():
    return "Windows"
  if _osxcross_live():
    return "Darwin"
  return platform.system()


HERE = REPO_ROOT          # the name helper/command bodies use for the repo root


def parallel_args(parallel: bool, jobs: int) -> list[str]:
  """ctest --parallel args when opted in, else empty. Serial is the default
  safety floor — a runaway test then stays isolated for the per-test --timeout
  to kill, instead of an N-way storm pegging the box. jobs<=0 means all cores."""
  if not parallel:
    return []
  return ["--parallel", str(jobs if jobs > 0 else (os.cpu_count() or 1))]



# Cap detected compiler versions at the highest value conan's bundled
# settings.yml knows about. A newer compiler still builds correctly — we
# just don't advertise a version conan would reject.
_CONAN_KNOWN_MAX_VERSION = {
  "gcc": 15,
  "clang": 20,
  "apple-clang": 16,
  # Bumped to cover VS18-era MSVC toolsets (14.5x, 14.6x). Conan
  # 2.27.x's settings.yml accepts these; older conans may need an
  # extension in CONAN_HOME/settings_user.yml.
  "msvc": 196,
}



# Candidate sets: plain name, versioned name-NN, plus well-known
# absolute paths where containers stash a side-installed trunk
# compiler (the devenv-cpp convention). Absolute paths are tried
# unconditionally — useful when a runner's PATH misses /opt/*/bin.
_GCC_CANDIDATES = (
  ["gcc"] + [f"gcc-{n}" for n in range(25, 9, -1)]
  + ["/opt/gcc/bin/gcc", "/usr/local/bin/gcc"])

_CLANG_CANDIDATES = (
  ["clang"] + [f"clang-{n}" for n in range(25, 9, -1)]
  + ["/opt/clang/bin/clang", "/usr/local/bin/clang"])



def _scan_compiler(candidates: list[str]) -> str | None:
  """Highest-major-version compiler among the candidate names/paths,
  or None when none resolve. Containers ship a side-installed trunk
  compiler next to an older distro default, so naming alone can't
  pick the newest — probe each candidate's -dumpversion."""
  best: tuple[int, str] | None = None
  for name in candidates:
    if "/" in name:
      path = name if Path(name).is_file() else None
    else:
      path = shutil.which(name)
    if not path:
      continue
    try:
      raw = subprocess.check_output(
        [path, "-dumpversion"], text=True
      ).strip().split(".")[0]
      major = int(raw)
    except (subprocess.CalledProcessError, ValueError, OSError):
      continue
    if best is None or major > best[0]:
      best = (major, path)
  return best[1] if best else None



def _default_cc() -> str:
  """Host default C driver, used when neither --compiler nor $CC pins
  one. Prefers the newest gcc, then clang (Linux); cl on Windows,
  clang on macOS."""
  system = platform.system()
  if system == "Windows":
    return "cl"
  if system == "Darwin":
    return "clang"
  return (_scan_compiler(_GCC_CANDIDATES)
          or _scan_compiler(_CLANG_CANDIDATES) or "gcc")



def _gcc_install_dir() -> str | None:
  """The `install:` dir of the gcc the gcc profile builds with — what
  clang's --gcc-install-dir wants so a clang build picks up that gcc's
  libstdc++ (the system gcc may be too old for C++23 <print>)."""
  gcc = _default_cc()
  try:
    out = subprocess.check_output([gcc, "-print-search-dirs"], text=True)
  except (subprocess.CalledProcessError, FileNotFoundError, OSError):
    return None
  for line in out.splitlines():
    if line.startswith("install:"):
      return line.split(":", 1)[1].strip()
  return None



def _ensure_cc_env(cc: str) -> None:
  """Propagate the picked compiler to child processes (cmake, conan).
  We only export CC/CXX when the caller didn't already pin them, so an
  explicit `CC=...` from the user still wins."""
  if cc in ("cl", "clang", "gcc"):
    # Plain names — let the child do its own PATH lookup.
    return
  if platform.system() != "Linux":
    return
  # gcc-N → g++-N; /opt/foo/bin/gcc → /opt/foo/bin/g++. The C++ driver
  # name is always 'g++' modulo the same suffix the C driver wears.
  base = Path(cc).name
  if base.startswith("gcc"):
    cxx_base = "g++" + base[len("gcc"):]
    cxx = str(Path(cc).with_name(cxx_base)) if "/" in cc else cxx_base
  else:
    cxx = cc
  os.environ.setdefault("CC",  cc)
  os.environ.setdefault("CXX", cxx)



def _cxx_for(cc: str) -> str:
  """The C++ driver paired with a C driver name/path (gcc->g++,
  clang->clang++, cl->cl), any version suffix preserved."""
  base = Path(cc).name
  if base in ("cl", "cl.exe"):
    return cc
  for c_token, cxx_token in (("gcc", "g++"), ("clang", "clang++")):
    if base.startswith(c_token):
      cxx_base = cxx_token + base[len(c_token):]
      return str(Path(cc).with_name(cxx_base)) if "/" in cc else cxx_base
  return cc



def _available_compilers() -> dict[str, str]:
  """Compilers installed on this host, as conan compiler name -> C
  driver path. gcc and clang on Linux, apple-clang on macOS, msvc on
  Windows."""
  system = platform.system()
  if system == "Windows":
    return {"msvc": "cl"}
  if system == "Darwin":
    cc = shutil.which("clang")
    return {"apple-clang": cc} if cc else {}
  found: dict[str, str] = {}
  gcc = _scan_compiler(_GCC_CANDIDATES)
  if gcc:
    found["gcc"] = gcc
  clang = _scan_compiler(_CLANG_CANDIDATES)
  if clang:
    found["clang"] = clang
  if _wine_msvc_live():                               # the msvc-wine container
    found["wine-msvc"] = shutil.which("cl")
  if _osxcross_live():                                 # the osxcross container
    found["osxcross"] = shutil.which("oa64-clang++")
  root = _emscripten_root()
  if root:
    found["emscripten"] = str(root / "emcc")
  return found



# Autodetect order when no compiler is named: first one installed wins.
_COMPILER_PREFERENCE = ("gcc", "clang", "apple-clang", "msvc", "wine-msvc",
                        "osxcross")



def _lane_override_hint(want: str) -> str:
  if want != "wine-msvc":
    return ""
  return (f" A `cl` on PATH claims this lane only when it is the msvc-wine "
          f"wrapper (msvcenv.sh beside it, or a wine exec in it); set "
          f"{WINE_MSVC_ENV}=1 to claim it anyway, 0 to suppress it.")


def _select_compiler(choice: str | None, default: str) -> None:
  """Resolve a --compiler selection and pin CC/CXX for the build.

  `choice` is the user's --compiler value; None falls back to the
  command's `default`. `default` is a compiler name, or 'auto' to
  autodetect (gcc, else clang, else apple-clang, else msvc). A named
  compiler that isn't installed on this host is a hard error, so a
  typo or a missing toolchain fails loudly instead of silently
  building with the wrong one."""
  available = _available_compilers()
  want = choice or default
  if want == "auto":
    want = next((c for c in _COMPILER_PREFERENCE if c in available), None)
    if want is None:
      typer.echo("buildutil: no supported compiler found on this host.",
                 err=True)
      raise typer.Exit(code=2)
  if want not in available:
    have = ", ".join(sorted(available)) or "none"
    typer.echo(
      f"buildutil: compiler '{want}' is not installed on this host "
      f"(available: {have}).{_lane_override_hint(want)}", err=True)
    raise typer.Exit(code=2)
  cc = available[want]
  if want == "emscripten":
    conf = _emscripten_conf()
    os.environ["CC"] = conf["c"]
    os.environ["CXX"] = conf["cpp"]
    return
  if want == "wine-msvc":
    print(f"buildutil: cross-compiling for Windows through the msvc-wine "
          f"lane ({cc}); {WINE_MSVC_ENV}=0 suppresses it")
    return                                     # cmake gets cl explicitly
  if want == "osxcross":
    os.environ["CC"] = "oa64-clang"            # the arm64 wrappers; conan +
    os.environ["CXX"] = "oa64-clang++"         # cmake read CC/CXX like native
    return
  os.environ["CC"] = cc
  os.environ["CXX"] = _cxx_for(cc)



_VCVARS_REL = Path("VC", "Auxiliary", "Build", "vcvars64.bat")
_VS_YEARS = ("2026", "2022", "2019")
_VS_EDITIONS = ("BuildTools", "Community", "Professional", "Enterprise")
_VSWHERE_ARGS = ["-latest", "-products", "*", "-requires",
                 "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                 "-property", "installationPath"]


_PROGRAM_FILES = {"ProgramFiles": r"C:\Program Files",
                  "ProgramFiles(x86)": r"C:\Program Files (x86)"}


def _program_files(var: str) -> Path:
  return Path(os.environ.get(var) or _PROGRAM_FILES[var])


def _program_files_roots() -> list[Path]:
  return list(dict.fromkeys(_program_files(var) for var in _PROGRAM_FILES))


def _vswhere() -> Path:
  return (_program_files("ProgramFiles(x86)") / "Microsoft Visual Studio" /
          "Installer" / "vswhere.exe")


def _vswhere_installs() -> list[Path]:
  """The install roots Visual Studio's own locator reports, newest first.

  vswhere.exe ships with every VS installer since 2017 and is the
  vendor-supported way to find an install on any drive, year and edition.
  Its absence is not an error: the conventional layouts still get probed.
  """
  try:
    out = subprocess.check_output([str(_vswhere()), *_VSWHERE_ARGS],
                                  text=True, errors="replace")
  except (OSError, subprocess.SubprocessError):
    return []
  return [Path(line.strip()) for line in out.splitlines() if line.strip()]


def _vcvars_candidates() -> list[Path]:
  """Where vcvars64.bat may live, most authoritative first: the explicit
  override, then what the installer reports, then the conventional
  per-year, per-edition layouts under both Program Files roots."""
  override = os.environ.get("VCVARS_PATH")
  candidates = [Path(override)] if override else []
  candidates += [install / _VCVARS_REL for install in _vswhere_installs()]
  candidates += [root / "Microsoft Visual Studio" / year / edition / _VCVARS_REL
                 for root in _program_files_roots()
                 for year in _VS_YEARS for edition in _VS_EDITIONS]
  return list(dict.fromkeys(candidates))


def _find_vcvars() -> Path:
  candidates = _vcvars_candidates()
  found = next((c for c in candidates if c.exists()), None)
  if found is not None:
    return found
  probed = "\n  ".join(str(c) for c in candidates)
  raise RuntimeError(
    f"cl.exe not on PATH and no vcvars64.bat found.\n"
    f"Probed $VCVARS_PATH, {_vswhere()}, and:\n  {probed}\n"
    f"Set $VCVARS_PATH to your install's vcvars64.bat, or run from a "
    f"Visual Studio Developer Prompt."
  )


def _ensure_msvc_env_on_path() -> None:
  """If on Windows and `cl` isn't already on PATH, locate vcvars64.bat,
  source it via cmd, and pull the resulting environment into our own
  process. Mirrors what a Visual Studio Developer Prompt does for an
  interactive user, so buildutil works the same whether invoked from a
  regular shell, a Dev Prompt, or a CI runner with no shell init at all.
  """
  if platform.system() != "Windows":
    return
  import shutil
  if shutil.which("cl") is not None:
    return
  vcvars = _find_vcvars()

  # Read as bytes + decode with mbcs (the Windows ANSI codepage, what cmd
  # actually emits) + errors=replace -- a strict cp1252/utf-8 decode trips
  # on non-ASCII bytes VS injects into env values like LIB and INCLUDE.
  print(f"+ sourcing {vcvars}", flush=True)
  raw = subprocess.check_output(
    f'cmd /c ""{vcvars}" >NUL && set"', shell=True,
  )
  out = raw.decode("mbcs", errors="replace")
  for line in out.splitlines():
    if "=" in line:
      key, _, value = line.partition("=")
      os.environ[key] = value.rstrip("\r")


def _detect_user_session_id() -> int | None:
  """Find the Windows session id of the logged-in user's interactive
  shell, identified by the running explorer.exe process. Returns None
  if no logged-in user is detectable (headless / pre-login state).
  Used by --psexec to hand the right session to `psexec -i`."""
  if platform.system() != "Windows":
    return None
  try:
    out = subprocess.check_output(
      ["tasklist", "/fi", "imagename eq explorer.exe", "/fo", "csv", "/nh"],
      text=True,
    )
  except (subprocess.CalledProcessError, FileNotFoundError):
    return None
  # tasklist /fo csv columns: "ImageName","PID","SessionName","Session#","MemUsage"
  for line in out.splitlines():
    parts = [p.strip().strip('"') for p in line.split(",")]
    if len(parts) >= 4 and parts[0].lower() == "explorer.exe":
      try:
        return int(parts[3])
      except ValueError:
        continue
  return None



def _detect_msvc_version() -> str:
  # Make sure cl + the rest of the MSVC environment are on PATH; if a
  # Dev Prompt isn't sourced, do it ourselves from vcvars64.bat. Any
  # downstream `cl`, `cmake`, etc. call works the same way after this
  # whether buildutil was launched from a plain shell or a CI runner.
  _ensure_msvc_env_on_path()
  # VCToolsVersion holds the exact MSVC toolset selected after vcvars
  # ran, e.g. "14.50.33807". That's the most accurate source: recipes
  # like boost's b2 invoke `vcvars.bat <major>.<minor>` from
  # compiler.version, so the value we put in the conan profile MUST
  # resolve to a toolset directory that actually exists on disk.
  vc_tools = os.environ.get("VCToolsVersion", "")
  m = re.match(r"(\d+)\.(\d+)\.", vc_tools)
  if m:
    vc_major, vc_minor = int(m.group(1)), int(m.group(2))
    if vc_major == 14:
      # Toolset 14.YY → conan 19N where N = YY // 10.
      # 14.40 -> 194, 14.50 -> 195, 14.60 -> 196.
      return str(190 + vc_minor // 10)

  # Fallback: parse cl's banner. Used when VCToolsVersion isn't set
  # (no Dev Prompt / vcvars), so we can at least surface the version.
  result = subprocess.run(["cl"], capture_output=True, text=True)
  banner = result.stderr or result.stdout
  m = re.search(r"Version (\d+)\.(\d+)\.", banner)
  if not m:
    raise RuntimeError(
      f"Could not parse cl.exe version banner — is cl on PATH? "
      f"(run from a Visual Studio developer prompt). Banner: {banner[:200]}"
    )
  major, minor = int(m.group(1)), int(m.group(2))
  return str(major * 10 + minor // 10)



def _detect_compiler() -> tuple[str, str]:
  cc = os.environ.get("CC") or _default_cc()
  _ensure_cc_env(cc)
  name_hint = Path(cc).name.lower().removesuffix(".exe")

  if name_hint == "cl":
    name = "msvc"
    raw = _detect_msvc_version()
  elif "clang" in name_hint:
    name = "apple-clang" if platform.system() == "Darwin" else "clang"
    raw = subprocess.check_output(
      [cc, "-dumpversion"], text=True
    ).strip().split(".")[0]
  else:
    name = "gcc"
    raw = subprocess.check_output(
      [cc, "-dumpfullversion", "-dumpversion"], text=True
    ).strip().split(".")[0]

  cap = _CONAN_KNOWN_MAX_VERSION.get(name)
  version = str(min(int(raw), cap)) if cap is not None else raw
  return name, version



WINE_MSVC_ENV = "BUILDUTIL_WINE_MSVC"
_WINE_EXEC = re.compile(r"\bwine\d*\b")


def _is_msvc_wine_wrapper(cl: Path) -> bool:
  """msvc-wine's `cl` is a shell wrapper that sources msvcenv.sh beside
  it and execs wine; reflect.py already reads that file, so the marker is
  load-bearing. The name alone proves nothing — OpenCL and Common Lisp
  launchers are called `cl` too."""
  if (cl.parent / "msvcenv.sh").is_file():
    return True
  try:
    with cl.open("rb") as handle:
      head = handle.read(4096).decode("utf-8", errors="replace")
  except OSError:
    return False
  return "msvcenv.sh" in head or _WINE_EXEC.search(head) is not None


def _wine_msvc_live() -> bool:
  """The msvc-wine container: the wrapped genuine cl on PATH of a Linux
  host. Selects the Windows/msvc cross settings below. $BUILDUTIL_WINE_MSVC
  settles it either way for a host the probe reads wrong."""
  override = os.environ.get(WINE_MSVC_ENV)
  if override:
    return override.strip().lower() not in ("0", "off", "false", "no")
  if platform.system() != "Linux" or _emscripten_live():
    return False
  cl = shutil.which("cl")
  return cl is not None and _is_msvc_wine_wrapper(Path(cl).resolve())


def _wine_msvc_version() -> str:
  """conan's msvc version from the wrapper's banner: 19.51 -> 195."""
  probe = subprocess.run(["cl"], capture_output=True, text=True)
  matched = re.search(r"Version (\d+)\.(\d+)", probe.stderr + probe.stdout)
  if not matched:
    typer.echo("buildutil: cl answered no version banner", err=True)
    raise typer.Exit(code=2)
  return f"{matched.group(1)}{int(matched.group(2)) // 10}"



def _emscripten_root() -> Path | None:
  """Locate emsdk's compiler directory from EMSDK or its PATH wrapper."""
  if platform.system() != "Linux":
    return None
  sdk = os.environ.get("EMSDK")
  wrapper = (shutil.which(str(Path(sdk) / "upstream/emscripten/em++"))
             if sdk else shutil.which("em++"))
  return Path(wrapper).resolve().parent if wrapper else None


def _emscripten_live() -> bool:
  """The explicitly selected wasm lane; installation alone is inert."""
  return (platform.system() == "Linux"
          and Path(os.environ.get("CC", "")).name == "emcc")


def _emscripten_conf() -> dict[str, str]:
  """Absolute compiler and CMake SDK paths for the host-context profile."""
  root = _emscripten_root()
  if root is None:
    raise RuntimeError("Emscripten not found: set EMSDK or source emsdk_env.sh")
  toolchain = root / "cmake/Modules/Platform/Emscripten.cmake"
  if not toolchain.is_file() or not (root / "emcc").is_file():
    raise RuntimeError(f"Incomplete Emscripten SDK at {root}")
  return {"c": str(root / "emcc"), "cpp": str(root / "em++"),
          "toolchain": str(toolchain)}


def _emscripten_clang_version() -> str:
  """Read LLVM's major version, never the emsdk release number."""
  banner = subprocess.check_output(
    [_emscripten_conf()["cpp"], "--version"], text=True)
  matched = re.search(r"clang version (\d+)", banner)
  if not matched:
    probe = subprocess.run([_emscripten_conf()["cpp"], "-v"],
                           capture_output=True, text=True, check=True)
    matched = re.search(r"clang version (\d+)", probe.stdout + probe.stderr)
  if not matched:
    raise RuntimeError("em++ did not report a clang version")
  return str(min(int(matched.group(1)), _CONAN_KNOWN_MAX_VERSION["clang"]))


def _osxcross_live() -> bool:
  """osxcross on a Linux host: the arm64 wrapper (oa64-clang++) on PATH.
  Selects the Macos/arm64 cross settings below — the mac-side sibling of
  _wine_msvc_live(). The oa64-clang wrappers bake in -arch arm64 and
  the packaged SDK's -isysroot, so a Linux box emits a real arm64 Mach-O with
  no Mac runner in the loop."""
  return (platform.system() == "Linux" and not _emscripten_live()
          and shutil.which("oa64-clang++") is not None)


def _osxcross_conf() -> dict[str, str]:
  """The toolchain facts osxcross-conf prints as `export KEY=VALUE` lines.
  OSXCROSS_SDK is the one we need: the packaged SDK's path, handed to conan
  so CMakeToolchain never shells out to a nonexistent xcrun on Linux."""
  out = subprocess.check_output(["osxcross-conf"], text=True)
  conf: dict[str, str] = {}
  for line in out.splitlines():
    matched = re.match(r"(?:export\s+)?(\w+)=(.*)", line.strip())
    if matched:
      conf[matched.group(1)] = matched.group(2).strip().strip('"')
  return conf


def _osxcross_tool(name: str) -> str | None:
  """The osxcross cctools binutil (ar, ranlib, ...) for the arm64 Mach-O target.
  CMake otherwise defaults CMAKE_AR to the host GNU ar, which writes System-V
  archives ld64 rejects as 'unknown-unsupported file format'. cctools are
  <triple>-<name> next to the oa64-clang wrapper; pick the arm64 one, else the
  format-flexible llvm-<name>."""
  wrapper = shutil.which("oa64-clang++")
  if wrapper:
    bindir = Path(wrapper).parent
    matches = (sorted(bindir.glob(f"arm64-apple-*-{name}"))
               or sorted(bindir.glob(f"aarch64-apple-*-{name}")))
    if matches:
      return str(matches[0])
  return shutil.which(f"llvm-{name}") or shutil.which(name)


def _osxcross_clang_version() -> str:
  """osxcross ships mainline LLVM clang; conan tracks the mac target as
  apple-clang. Report the driver's major capped at conan's known apple-clang
  max — the same value a native Apple-clang mac build resolves to."""
  raw = subprocess.check_output(
    ["oa64-clang++", "-dumpversion"], text=True
  ).strip().split(".")[0]
  return str(min(int(raw), _CONAN_KNOWN_MAX_VERSION["apple-clang"]))


def _detect_settings(build_type: str) -> dict[str, str]:
  if _emscripten_live():
    return {
      "arch": "wasm", "os": "Emscripten", "compiler": "clang",
      "compiler.version": _emscripten_clang_version(),
      "compiler.cppstd": "26", "compiler.libcxx": "libc++",
      "build_type": build_type,
    }
  arch_map = {"x86_64": "x86_64", "AMD64": "x86_64", "aarch64": "armv8",
              "arm64": "armv8"}   # macOS reports Apple Silicon as 'arm64'
  os_map = {"Linux": "Linux", "Darwin": "Macos", "Windows": "Windows"}
  if _wine_msvc_live():
    # the cross build: host settings say Windows/msvc exactly like the
    # real runner; the wrapped cl compiles, wine executes
    return {
      "arch": "x86_64",
      "os": "Windows",
      "compiler": "msvc",
      "compiler.version": _wine_msvc_version(),
      "compiler.cppstd": "23",
      "compiler.runtime": "dynamic",
      "compiler.runtime_type": "Debug" if build_type == "Debug" else "Release",
      "build_type": build_type,
    }
  if _osxcross_live():
    # the mac cross build: host settings say Macos/arm64/apple-clang exactly
    # like the M2, but the oa64-clang wrappers compile a real arm64 Mach-O on
    # Linux. No emulator exists to run mac binaries here, so this is a
    # release-BUILD lane only — the test suite stays on the Linux runner.
    return {
      "arch": "armv8",
      "os": "Macos",
      "compiler": "apple-clang",
      "compiler.version": _osxcross_clang_version(),
      "compiler.cppstd": "26",
      "compiler.libcxx": "libc++",
      "build_type": build_type,
    }
  compiler, compiler_version = _detect_compiler()
  os_name = os_map.get(platform.system(), platform.system())

  # Use the host's real arch on every OS, macOS included: Apple Silicon
  # builds arm64, Intel builds x86_64. A native (non-Rosetta) python
  # is what makes platform.machine() report 'arm64' on Apple Silicon.
  arch = arch_map.get(platform.machine(), platform.machine())

  # cppstd selection: we want 26 (so the project compiles at
  # -std=c++2c). gcc/clang versions below 14 don't accept cppstd=26
  # in conan's profile plugin yet, so fall back to 23 there — the
  # cmake side promotes the project's own targets to /std:c++latest
  # / -std=c++2c through buildutil.cmake either way, so the C++26
  # features still light up where the actual compiler supports them.
  # MSVC also stays at 23 (conan 2.x caps it there).
  if compiler == "msvc":
    cppstd = "23"
  elif compiler in ("gcc", "clang") and int(compiler_version) < 14:
    cppstd = "23"
  else:
    cppstd = "26"

  settings = {
    "arch": arch,
    "os": os_name,
    "compiler": compiler,
    "compiler.version": compiler_version,
    "compiler.cppstd": cppstd,
    "build_type": build_type,
  }
  # libcxx / runtime selection differs per toolchain.
  if compiler == "msvc":
    settings["compiler.runtime"] = "dynamic"
    # conan needs runtime_type explicitly. RelWithDebInfo links the
    # release CRT (Debug CRT pulls in iterator-debug-level mismatches
    # with shipped binaries). Debug uses the debug CRT.
    settings["compiler.runtime_type"] = (
      "Debug" if build_type == "Debug" else "Release"
    )
  elif compiler == "apple-clang":
    settings["compiler.libcxx"] = "libc++"
  else:  # gcc, clang
    settings["compiler.libcxx"] = "libstdc++11"
  return settings



def _module_linkage() -> str:
  from . import configopts
  return configopts.get("module_linkage")


def _profile_name(settings: dict[str, str], linkage: str | None = None) -> str:
  """arch-os-compiler[-shared]-buildtype. The linkage axis sits BEFORE
  the build type (parsers that strip the last segment keep working),
  and only the non-default value appears — every existing profile dir
  name is unchanged. Own build tree per linkage: a flip can never mix
  static and shared artifacts in one dir. The scaffolded conanfile's
  _profile_name computes the same name (agreement-tested)."""
  if linkage is None:
    linkage = _module_linkage()
  parts = [settings["arch"], settings["os"], settings["compiler"]]
  if linkage != "static":
    parts.append(linkage)
  parts.append(settings["build_type"])
  return "-".join(parts).lower()



def _osxcross_binutils() -> tuple[dict[str, str], list[str]]:
  """The Darwin cctools the dependency graph is told about, as cmake
  variables and as environment: a cmake-shaped dependency reads CMAKE_AR
  out of the toolchain conan generates, an autotools-shaped one reads AR
  out of its environment."""
  # Without CMAKE_AR a dependency archives with the host's GNU ar, and
  # ld64 does not read a System-V archive -- it ignores the file, so the
  # miss surfaces as an undefined symbol in whatever links it, or never
  # (openssl's fips.dylib could not link providers/libfips.a).
  archiver = {name: _osxcross_tool(name) for name in ("ar", "ranlib")}
  extra: dict[str, str] = {}
  buildenv: list[str] = []
  if all(archiver.values()):
    extra |= {"CMAKE_AR": archiver["ar"], "CMAKE_RANLIB": archiver["ranlib"]}
    buildenv += [f"AR={archiver['ar']}", f"RANLIB={archiver['ranlib']}"]
  else:
    # Both or neither -- ar and ranlib have to agree on the format they
    # write -- but never silently, which is how the failure above hides.
    missing = ", ".join(sorted(n for n, path in archiver.items() if not path))
    print(f"buildutil: osxcross profile has no {missing} for the arm64 "
          "target -- dependency builds will archive with this container's "
          "own ar, and ld64 does not read those",
          file=sys.stderr, flush=True)
  # Independently optional, unlike the archiver pair: nothing has to
  # agree with it, and only a dependency enabling Objective-C asks.
  install_name_tool = _osxcross_tool("install_name_tool")
  if install_name_tool:
    extra["CMAKE_INSTALL_NAME_TOOL"] = install_name_tool
  else:
    print("buildutil: osxcross profile has no install_name_tool for the "
          "arm64 target -- a dependency that enables Objective-C will fail "
          "cmake's binutil search", file=sys.stderr, flush=True)
  return extra, buildenv


def _osxcross_profile_entries() -> tuple[list[str], list[str]]:
  """The osxcross lane's ([conf], [buildenv]) profile entries: the SDK,
  the compilers and the binutils every dependency is built with."""
  lines = []
  # conan's CMakeToolchain resolves an os=Macos SDK by shelling out to
  # xcrun -- which does not exist on Linux. Hand it the packaged SDK path
  # outright so it skips xcrun entirely. The oa64-clang wrappers already
  # inject -isysroot, but cmake's CMAKE_OSX_SYSROOT wants it spelled too.
  sdk = _osxcross_conf().get("OSXCROSS_SDK", "")
  if sdk:
    lines.append(f"tools.apple:sdk_path={sdk}")
  # Named here, or a dep compiles with whatever CC the shell held.
  # 'objcpp' is absolute because a language enabled after project() gets
  # its own compiler search, which finds the HOST c++ and refuses a bare
  # name ("is not a full path and was not found in the PATH"); 'c'/'cpp'
  # are resolved against PATH by project() and need no path.
  objcxx = shutil.which("oa64-clang++") or "oa64-clang++"
  lines.append("tools.build:compiler_executables="
               "{'c': 'oa64-clang', 'cpp': 'oa64-clang++', "
               f"'objcpp': '{objcxx}'}}")
  extra, buildenv = _osxcross_binutils()
  if extra:
    lines.append(f"tools.cmake.cmaketoolchain:extra_variables={extra}")
  return lines, buildenv


def _ensure_profile(settings: dict[str, str]) -> Path:
  profiles_dir = Path("_profiles")
  profiles_dir.mkdir(exist_ok=True)
  path = profiles_dir / _profile_name(settings)

  lines = ["[settings]"]
  for k, v in settings.items():
    lines.append(f"{k}={v}")

  # [buildenv] is collected separately and written LAST: everything after
  # the "[conf]" header below lands in that section, so a section opened in
  # the middle would swallow every conf line that follows it.
  buildenv: list[str] = []

  # Dep option policy is the PROJECT's, not the driver's: [conan]
  # options / options_<os> in buildutil.toml land in the profile's
  # [options] section (bossdeux uses this to build SDL3 backend-less
  # on Linux, say). The per-OS key wins by being listed after the
  # plain one — conan takes the last match per option.
  os_key = {"Linux": "linux", "Windows": "windows",
            "Macos": "macos", "Emscripten": "emscripten"}.get(
              settings.get("os", ""), "")
  toml_options = (config.PROJECT["conan_options"]
                  + config.PROJECT["conan_options_os"].get(os_key, []))
  if toml_options:
    lines += ["", "[options]", *toml_options]

  # Ninja for everyone (Linux / Windows / macOS): generates
  # compile_commands.json for tooling (LSP, clang-tidy), parallelizes
  # well, and avoids CMake's IDE generators going wrong on bleeding-
  # edge compilers (CMake's "Visual Studio 18 2026" generator literally
  # doesn't exist yet, so any package that doesn't override the
  # generator dies on VS18).
  lines += ["", "[conf]", "tools.cmake.cmaketoolchain:generator=Ninja"]

  if _wine_msvc_live():
    # mt.exe (manifest embedding) is the one MSVC tool that reliably
    # dies under wine; manifests are optional for these binaries -- neuter
    # it for every dep build (and the project configure matches).
    # Embedded (/Z7) keeps debug info IN the .obj for OUR targets (CMP0141
    # NEW); the deep fix for the mspdbsrv hang -- which also covers deps
    # that pin an old cmake_minimum_required (CMP0141 OLD -> /Zi) -- is the
    # CI job's _CL_=/Z7 env, forcing embedded onto EVERY cl before CMake.
    lines.append("tools.cmake.cmaketoolchain:extra_variables="
                 "{'CMAKE_MT': '/usr/bin/true', "
                 "'CMAKE_POLICY_DEFAULT_CMP0141': 'NEW', "
                 "'CMAKE_MSVC_DEBUG_INFORMATION_FORMAT': 'Embedded'}")
    # Cap the dep-build parallelism HARD. Each cl runs a full wine context,
    # so conan's default (one job per core -- ~88 on this runner) spawns
    # dozens of wine processes at once: it slogs the shared host and races
    # the preview compiler into C1001 ICEs. A small fixed count builds a
    # little slower but stays stable and keeps the box responsive.
    lines.append("tools.build:jobs=6")

  if _osxcross_live():
    conf, env = _osxcross_profile_entries()
    lines += conf
    buildenv += env

  if settings.get("os") == "Emscripten":
    conf = _emscripten_conf()
    lines.append("tools.cmake.cmaketoolchain:user_toolchain="
                 f"{[conf['toolchain']]!r}")
    lines.append("tools.build:compiler_executables="
                 f"{{'c': {conf['c']!r}, 'cpp': {conf['cpp']!r}}}")

  # Per-package [conf] workarounds are the project's too: [conan] conf
  # lines from buildutil.toml (e.g. a -Wno-* for one dep whose build
  # trips on a newer standard) are appended into the [conf] section.
  lines += config.PROJECT["conan_conf"]

  # clang on Linux uses libstdc++; pin it to the same (side-installed)
  # gcc the gcc profile builds with, so it sees that gcc's standard
  # library rather than a too-old system one missing C++23/26 headers.
  if (settings.get("compiler") == "clang"
      and settings.get("os") == "Linux" and platform.system() == "Linux"):
    gcc_dir = _gcc_install_dir()
    if gcc_dir:
      flag = f"--gcc-install-dir={gcc_dir}"
      for _key in ("cxxflags", "cflags", "exelinkflags", "sharedlinkflags"):
        lines.append(f"tools.build:{_key}=['{flag}']")

  # MSVC packages built through conan's MSBuild integration (boost
  # et al) also need vs_version and (for offline / portable VS
  # layouts) installation_path in the same [conf] section, since
  # vswhere can't see installs not registered with the VS Installer.
  if settings.get("compiler") == "msvc":
    _MSVC_TO_VS = {
      "190": "14", "191": "15", "192": "16",
      "193": "17", "194": "17",
      "195": "18", "196": "18",
    }
    vs_version = (
      os.environ.get("MSVC_VS_VERSION")
      or _MSVC_TO_VS.get(settings["compiler.version"], "18")
    )
    lines.append(f"tools.microsoft.msbuild:vs_version={vs_version}")
    # vcvars64.bat exports VSINSTALLDIR with a trailing backslash —
    # strip it; conan accepts either form but the bare path matches
    # what users hand-write when overriding via CI.
    vs_install = os.environ.get(
      "VSINSTALLDIR", ""
    ).rstrip("\\").rstrip("/")
    if vs_install:
      lines.append(
        f"tools.microsoft.msbuild:installation_path={vs_install}"
      )

  if buildenv:
    lines += ["", "[buildenv]", *buildenv]

  # Always rewrite — the profile is fully derived from settings + env,
  # there's no user customisation to preserve, and stale profiles
  # (missing the [conf] generator line) are how every "why is only
  # one profile on Ninja" / "why is this build using Make" question
  # starts.
  content = "\n".join(lines) + "\n"
  if not path.exists() or path.read_text() != content:
    path.write_text(content)
  return path



def _resolve_build_type(release: bool, debug: bool) -> str:
  if release and debug:
    return "RelWithDebInfo"
  if release:
    return "Release"
  return "Debug"



def _resolve_conan_home(override: str | None) -> Path:
  """Pick a CONAN_HOME, in order of precedence:
     1. --conan-home flag (override)
     2. CONAN_HOME env var (CI uses this to point at /cache/...)
     3. <repo>/_conanhome default (local dev)
  """
  if override:
    return Path(override).expanduser().resolve()
  env = os.environ.get("CONAN_HOME")
  if env:
    return Path(env).expanduser().resolve()
  return DEFAULT_CONAN_HOME



def _enter(conan_home: str | None = None) -> None:
  os.chdir(HERE)
  # the driver marker, inherited by every child (cmake, ninja, ctest,
  # conan, generator scripts): the deposit's direct-invocation guards
  # check it, so those tools invoked OUTSIDE buildutil fail with an
  # explanation instead of half-working around the driver's contracts
  from .updatecmd import package_version
  os.environ["BUILDUTIL"] = package_version()
  # the RESOLVED linkage rides the env too, so the conanfile's layout()
  # (a child process) computes the same _build/<profile> dir the driver
  # is about to use — one resolution, everywhere
  from . import configopts
  os.environ["BUILDUTIL_OPT_MODULE_LINKAGE"] = configopts.get("module_linkage")
  # materialize the cmake machinery (rendered, gitignored, derived):
  # nothing is committed in the project — configure() below hands the
  # dir to cmake as CMAKE_MODULE_PATH so `include(buildutil)` resolves
  from . import deposit
  from .config import PROJECT
  # PROJECT goes in WHOLE, never a hand-picked subset: a call site that
  # copies today's keys silently drops tomorrow's, which is exactly how
  # [cmake] export_module_headers shipped inert in 0.17.1.
  deposit.ensure(Path(HERE), PROJECT, PROJECT["cmake_extensions"])
  home = _resolve_conan_home(conan_home)
  home.mkdir(parents=True, exist_ok=True)
  os.environ["CONAN_HOME"] = str(home)
  # the remote seam (CONAN_REMOTE_* — a repo-root .env feeds it): stamp-
  # guarded, so this is a file read per run and a real registration+login
  # only when the env changed or the remote vanished from CONAN_HOME
  from . import bootstrap
  bootstrap.ensure_conan_remote()



def _ensure_cross_build_profile() -> Path:
  """The BUILD-context profile shared by every cross lane (wine-msvc,
  osxcross): where a native Linux gcc exists (osxcross's devenv-cpp), pin it
  explicitly so build-side tooling (cmake, ninja, b2, configure-time codegen)
  compiles for the runner and never inherits the mac cross clang osxcross puts
  in CC/CXX. The msvc-wine container (ubuntu, no gcc) has no native compiler to
  pin, so it falls back to the compiler-less minimal profile it always used —
  probing a missing gcc there is what broke the wine build."""
  profile = _resolve_conan_home(None) / "profiles" / "cross-build-linux"
  profile.parent.mkdir(parents=True, exist_ok=True)
  gcc = _scan_compiler(_GCC_CANDIDATES)
  if not gcc:                                        # e.g. the msvc-wine container
    profile.write_text("[settings]\nos=Linux\narch=x86_64\nbuild_type=Release\n")
    return profile
  gxx = _cxx_for(gcc)
  major = subprocess.check_output(
    [gcc, "-dumpfullversion", "-dumpversion"], text=True
  ).strip().split(".")[0]
  version = str(min(int(major), _CONAN_KNOWN_MAX_VERSION["gcc"]))
  cppstd = "26" if int(version) >= 14 else "23"
  profile.write_text(
    "[settings]\n"
    "os=Linux\narch=x86_64\nbuild_type=Release\n"
    "compiler=gcc\n"
    f"compiler.version={version}\n"
    f"compiler.cppstd={cppstd}\n"
    "compiler.libcxx=libstdc++11\n"
    "[conf]\n"
    "tools.cmake.cmaketoolchain:generator=Ninja\n"
    f"tools.build:compiler_executables={{'c': '{gcc}', 'cpp': '{gxx}'}}\n"
  )
  return profile


def _host_build_profiles(profile: Path) -> tuple[Path, Path]:
  """(host, build) profile pair — the build side diverges only on the
  cross lanes. One derivation for install/export-pkg/package-test, so
  packaging can never pair profiles differently than the build did."""
  build_profile = (_ensure_cross_build_profile()
                   if _emscripten_live() or _wine_msvc_live() or _osxcross_live()
                   else profile)
  return profile, build_profile


def _conan_install(profile: Path, tests: bool = True) -> None:
  """Resolve dependencies for `profile` — WITH `--update` by default.

  Without --update, conan resolves version ranges against the local
  cache first and never re-checks the remote for a range something
  cached already satisfies. That served stale versions three times in
  one night: a dependency published 0.3.0.1 Release-only, a cached copy
  satisfied `>=0.3`, and Debug builds kept failing long after 0.3.0.2
  shipped the fix — the miss looked like the fix not working.

  Owner ruling (2026-08-19): the common situation is the default —
  ranges always resolve against the remote, so "fixed in X.Y.Z, floor
  covers it" just works. The site registry is a network-local caching
  proxy, so reachability is not a real cost. The EXCEPTION gets the
  switch: the global `--no-conan-update` (env BUILDUTIL_NO_CONAN_UPDATE,
  set by the root callback) skips --update for offline / frozen-cache
  work.
  """
  _, build_profile = _host_build_profiles(profile)
  env = dict(os.environ)
  if not tests:
    # conanfile drops test_requires
    env[f"{CMAKE_PREFIX}_SKIP_TEST_DEPS"] = "1"
  argv = [
    "conan", "install", ".",
    f"--profile:host={profile}",
    f"--profile:build={build_profile}",
    "--build=missing",
  ]
  if not os.environ.get("BUILDUTIL_NO_CONAN_UPDATE"):
    argv.append("--update")
  subprocess.check_call(argv, env=env)



def _regen_clangd(build_dir: Path) -> None:
  """Point clangd at the just-configured build dir's compile DB, so the
  editor's language server resolves includes exactly as the build does. The
  dir changes per profile/build-type (debug vs relwithdebinfo vs a clang dir),
  so rewrite it every configure rather than leave a stale committed path. The
  build is gcc; strip the gcc-only flag clang's driver rejects and re-add the
  constexpr budget in clang's own spelling. Best-effort — never fail a build
  over an IDE convenience, so swallow-and-warn."""
  if not (build_dir / "compile_commands.json").exists():
    return
  try:
    Path(".clangd").write_text(
      "# Generated by buildutil on configure — gitignored, do not edit.\n"
      "# Points clangd at the active build's compile DB; refreshed per build.\n"
      "CompileFlags:\n"
      f"  CompilationDatabase: _build/{build_dir.name}\n"
      "  Remove: [-fconstexpr-ops-limit=*]\n"
      "  Add:    [-fconstexpr-steps=100000000]\n")
  except OSError as error:
    print(f"buildutil: skipped .clangd refresh ({error})", file=sys.stderr)



def _embed_fallback_arg() -> str:
  """[resources]: embed declared files as a generated byte array even
  where the compiler has #embed. A DIAGNOSTIC switch, not project policy
  -- it is how a box with gcc 16 exercises the path MSVC takes, and a
  bisect handle when a resource looks wrong -- so it is an env var rather
  than a buildutil.toml key. Always passed, never omitted: it is a cache
  variable, so a dir once configured with it ON would keep the array back
  end after the env var went away."""
  return ("-DBUILDUTIL_EMBED_FALLBACK="
          + ("ON" if os.environ.get("BUILDUTIL_EMBED_FALLBACK") else "OFF"))


def _ccache_launcher_args() -> list[str]:
  """-DCMAKE_<LANG>_COMPILER_LAUNCHER=ccache for the native gcc/clang and
  osxcross lanes when ccache is on PATH. The osxcross
  wrappers are thin shims around the real clang + packaged SDK, so that lane
  forces CCACHE_COMPILERCHECK=content (ccache reads it per compile): the
  default mtime check would hash the shim, not what it execs, and an
  SDK/clang bump inside an unchanged wrapper could serve stale hits.
  wine-msvc stays out: ccache-under-wine-cl is unverified (/Z7 vs /Zi,
  compiler identification through wine).

  When ccache is absent or the lane is gated out, the launcher cache
  entries are explicitly cleared rather than left alone: COMPILER_LAUNCHER
  is a cache variable, so a dir once configured with ccache would keep
  exec'ing a launcher that no longer exists (e.g. after a box upgrade to
  an image without ccache) until the build dir was wiped by hand."""
  # Every language buildutil ever compiles. OBJC/OBJCXX are live only on
  # an Apple TARGET, and cmake ignores a launcher for a language that was
  # never enabled -- but leaving them out would have meant the one
  # platform where a module has .m/.mm sources compiling them uncached,
  # silently, for no reason anyone could find in this file.
  languages = ("C", "CXX", "OBJC", "OBJCXX")
  clear = [f"-DCMAKE_{lang}_COMPILER_LAUNCHER=" for lang in languages]
  if _wine_msvc_live():
    return clear
  ccache = shutil.which("ccache")
  if not ccache:
    return clear
  if _osxcross_live():
    os.environ.setdefault("CCACHE_COMPILERCHECK", "content")
  return [f"-DCMAKE_{lang}_COMPILER_LAUNCHER={ccache}"
          for lang in languages]


_CACHED_TOOLCHAIN = re.compile(r"(?m)^CMAKE_TOOLCHAIN_FILE:[^=]*=(.*)$")


def _cached_toolchain(cmake_cache: Path) -> str | None:
  match = _CACHED_TOOLCHAIN.search(cmake_cache.read_text(errors="replace"))
  return match.group(1).strip() if match else None


def _drop_unusable_cache(build_dir: Path, toolchain: Path,
                         toolchain_text: str) -> bool:
  """Whether cmake will see this as a FIRST configure, dropping what it
  must not keep.

  conan folds its toolchain flags in through *_INIT seeds that take
  effect only on a first configure, so a toolchain whose CONTENT moved is
  ignored by a configured dir. A cache recording a RELATIVE toolchain
  path is worse: cmake resolves it against the BUILD tree before the
  source tree, so debris at <build>/<profile>/_build/<profile>/ captures
  every compile against an old dependency with nothing warning — and
  cmake caches its answer in CMakeFiles/, so that goes too.
  """
  cmake_cache = build_dir / "CMakeCache.txt"
  if not cmake_cache.exists():
    return True
  if _cached_toolchain(cmake_cache) != cmake_path(toolchain):
    cmake_cache.unlink()
    shutil.rmtree(build_dir / "CMakeFiles", ignore_errors=True)
    return True
  stamp = build_dir / ".buildutil-toolchain.stamp"
  if not stamp.exists() or stamp.read_text() != toolchain_text:
    cmake_cache.unlink()
    return True
  return False


def _osxcross_configure_args() -> list[str]:
  """The -D flags the project's own cmake invocation needs on the
  osxcross lane: the Darwin binutils, the linker that synthesises what
  ld64 does not, and the rpath policy that keeps a signature valid."""
  if not _osxcross_live():
    return []
  args = []
  # CMake defaults CMAKE_AR to the host GNU ar, whose System-V archives
  # ld64 rejects. install_name_tool it searches for only once a language
  # is enabled after Platform/Darwin -- which is what a module's .mm
  # does -- and then hard errors, guessing a prefix from the compiler
  # name that the oa64-clang wrappers do not carry. Name all three.
  for variable, tool in (("AR", "ar"), ("RANLIB", "ranlib"),
                         ("INSTALL_NAME_TOOL", "install_name_tool")):
    found = _osxcross_tool(tool)
    if found:
      args.append(f"-DCMAKE_{variable}={found}")
  # SDL's Cocoa objects reference clang's arm64 objc_msgSend selector stubs
  # (_objc_msgSend$sel); osxcross's cctools ld64 doesn't synthesize them, LLD's
  # Mach-O linker does. Point every link at ld64.lld.
  args += [f"-DCMAKE_{flavour}_LINKER_FLAGS=-fuse-ld=lld"
           for flavour in ("EXE", "SHARED", "MODULE")]
  # ld64 ad-hoc signs every arm64 Mach-O, and cmake's install rewrites
  # the copy's RPATH with install_name_tool -- changing bytes the
  # CodeDirectory covers, with no codesign on Linux to recompute them.
  # Linking with the INSTALL rpath leaves the install nothing to edit;
  # the build-tree rpath it costs runs nothing on this machine anyway.
  args.append("-DCMAKE_BUILD_WITH_INSTALL_RPATH=ON")
  return args


def _cmake_configure(build_dir: Path, build_type: str, *,
                     tests: bool, bench: bool,
                     coverage: bool = False, gc_sections: bool = False) -> None:
  """Configure build_dir directly against the conan toolchain.

  Not `cmake --preset`: conan names every preset `conan-<build_type>`,
  so two profiles that share a build_type (e.g. a gcc build and a
  clang build) both emit `conan-debug` into CMakeUserPresets.json and
  cmake rejects the duplicate. This is conan's documented no-preset
  equivalent.

  Every build-shaping option is passed explicitly so a re-configure
  of a shared dir (coverage vs plain build, say) can't inherit a
  stale cached value."""
  _stamp_build_info()
  toolchain = (build_dir / "generators" / "conan_toolchain.cmake").resolve()
  stamp = build_dir / ".buildutil-toolchain.stamp"
  toolchain_text = toolchain.read_text() if toolchain.exists() else ""
  first_configure = _drop_unusable_cache(build_dir, toolchain, toolchain_text)
  wine_cross = (
    [f"-DCMAKE_C_COMPILER={shutil.which('cl')}",
     f"-DCMAKE_CXX_COMPILER={shutil.which('cl')}",
     "-DCMAKE_MT=/usr/bin/true",
     "-DCMAKE_MSVC_DEBUG_INFORMATION_FORMAT=Embedded",   # /Z7: no PDB, no mspdbsrv (our own targets, CMP0141 NEW)
     "-DCMAKE_POLICY_DEFAULT_CMP0141=NEW",
     "-DCMAKE_CROSSCOMPILING_EMULATOR=wine"]
    if _wine_msvc_live() else [])
  wine_cross += _osxcross_configure_args()
  # the firmware-artifact stage: a dir of prebuilt .bin images
  # lets a cross build skip watcom entirely
  prebuilt_firmware = os.environ.get("BUILDUTIL_PREBUILT_FIRMWARE")
  if prebuilt_firmware:
    wine_cross.append(
      f"-D{CMAKE_PREFIX}_PREBUILT_FIRMWARE={prebuilt_firmware}")
  # Every path-valued -D goes through config.cmake_path(): CMake re-emits
  # some of these as cmake CODE, where a Windows backslash is an escape
  # (the whole story is in that function).
  subprocess.check_call([
    "cmake", "-S", ".", "-B", cmake_path(build_dir), "-G", "Ninja",
    *wine_cross,
    *_ccache_launcher_args(),
    # cmake caches the toolchain and calls a re-passed one unused
    *([f"-DCMAKE_TOOLCHAIN_FILE={cmake_path(toolchain)}"]
      if first_configure else []),
    # Configure-time codegen (configure.py) runs under find_package(Python3);
    # point it at the venv interpreter so a script's venv-installed deps
    # ([venv] extra_deps) resolve.
    f"-DPython3_EXECUTABLE={cmake_path(VENV_PY)}",
    "-DCMAKE_POLICY_DEFAULT_CMP0091=NEW",
    f"-DCMAKE_BUILD_TYPE={build_type}",
    f"-DBUILD_TESTING={'ON' if tests else 'OFF'}",
    f"-DBUILD_BENCHMARKING={'ON' if bench else 'OFF'}",
    # untagged modules build as this (config option module_linkage);
    # kind-tagged modules keep their word regardless
    f"-DBUILDUTIL_MODULE_LINKAGE={_module_linkage()}",
    # the build-shape switches: CANONICAL names, acted on by the rendered
    # machinery rather than by a block each project's root pasted for
    # itself, so they are the same two names in every project
    f"-DBUILDUTIL_COVERAGE={'ON' if coverage else 'OFF'}",
    f"-DBUILDUTIL_GC_SECTIONS={'ON' if gc_sections else 'OFF'}",
    # the project's own [options], every one of them, for the same reason
    *_project_options.cmake_arguments(CMAKE_PREFIX),
    _embed_fallback_arg(),
    f"-D{CMAKE_PREFIX}_MAX_ERRORS="
    f"{os.environ.get('BUILDUTIL_MAX_ERRORS', '0')}",
    # Every building module announces itself as <PREFIX>_<NAME>_ENABLED=1
    # so a TU can `#if` on a sibling; the dormant set (modules.ini)
    # drops out. Both prefixes come from buildutil.toml.
    f"-D{MODULE_DEFINE_PREFIX}_MODULE_DEFINES="
    f"{';'.join(_enabled_module_defines())}",
    # the machinery seam (0.3.0): the rendered machinery lives in the
    # gitignored runtime dir, cmake finds them via MODULE_PATH, and
    # the python halves are called out of THIS interpreter's package
    f"-DCMAKE_MODULE_PATH={cmake_path(_deposit_dir())}",
    f"-DBUILDUTIL_PY={cmake_path(sys.executable)}",
    f"-DBUILDUTIL_PYSUPPORT="
    f"{cmake_path(Path(__file__).resolve().parent / 'pysupport')}",
    # The buildutil package's PARENT, so an extension can run
    # `python -m buildutil.<mod>` under the VENV interpreter -- which is where
    # a declared extension's deps (libclang) are, and where the package
    # itself is not.
    f"-DBUILDUTIL_PYPATH={cmake_path(Path(__file__).resolve().parent.parent)}",
  ])
  stamp.write_text(toolchain_text)
  from . import vscode as _vscode
  try:
    _vscode.refresh(active=build_dir.name)
  except (OSError, ValueError) as error:
    print(f"buildutil: skipped .vscode refresh ({error})", file=sys.stderr)
  _regen_clangd(build_dir)



# --jump-to-error: on a failed build, jump the editor to where the error is
# reported. A pure location parser + the editor launch, kept separate.

_ERROR_LOCATION = re.compile(r'^(.+?):(\d+):(\d+):\s*error:', re.MULTILINE)


def _parse_error_locations(output: str, root: Path = HERE) -> list[str]:
  """The `path:line:col` of every gcc/clang error in `output` that lands inside
  `root`, deduped in first-seen order.

  Anchored on `: error:`, so the include / instantiation chain (`In file
  included from …`, `… required from …`) and `note:` / `warning:` lines never
  match — only the point where the error itself is reported. Both line and
  column are required; locations outside the project are dropped."""
  root = Path(root).resolve()
  seen: set[str] = set()
  locations: list[str] = []
  for raw_path, line, col in _ERROR_LOCATION.findall(output):
    path = Path(raw_path)
    path = (path if path.is_absolute() else root / path).resolve()
    if not path.is_relative_to(root):
      continue
    location = f"{path}:{line}:{col}"
    if location not in seen:
      seen.add(location)
      locations.append(location)
  return locations


def _jump_to_error(output: str, count: int) -> None:
  """Open the first `count` project error locations from a failed build in the
  editor — `code -r -g <file:line:col>` (reuse the window, jump to line:col)."""
  locations = _parse_error_locations(output)[:count]
  if not locations:
    return
  if not shutil.which("code"):
    print("buildutil --jump-to-error: 'code' is not on PATH; would have opened "
          + ", ".join(locations), file=sys.stderr)
    return
  for location in locations:
    print(f"buildutil --jump-to-error: opening {location}", flush=True)
    subprocess.run(["code", "-r", "-g", location])


def _run_build_proc(cmd: list[str], capture: bool) -> tuple[int, str]:
  """Run a `cmake --build` in its own session (so `buildutil kill` can take the
  whole ninja/compiler group down). When `capture` is set, tee the combined
  output to our stdout AND collect it (for --jump-to-error); otherwise the child
  inherits our fds directly, keeping the compiler's TTY colour."""
  proc = subprocess.Popen(
    cmd,
    stdout=subprocess.PIPE if capture else None,
    stderr=subprocess.STDOUT if capture else None,
    text=True, start_new_session=True)
  BUILD_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
  BUILD_PID_FILE.write_text(str(proc.pid))
  collected = ""
  try:
    if capture:
      chunks: list[str] = []
      for line in proc.stdout:
        sys.stdout.write(line)
        chunks.append(line)
      collected = "".join(chunks)
    code = proc.wait()
  finally:
    BUILD_PID_FILE.unlink(missing_ok=True)
  return code, collected


_GC_SECTION_LINE = re.compile(r"removing unused section '([^']+)' in file '([^']+)'")


def _gc_section_symbol(section: str) -> str:
  """The (mangled) symbol a dropped section carries. A C++ symbol section is
  `.<class>.<mangled>` with the mangled name starting at `_Z`; anonymous
  data/const sections (`.rodata.cst4`, `.group`) carry none, so fall back to
  the section name itself."""
  marker = section.find("_Z")
  return section[marker:] if marker != -1 else section


def _demangle(names: list[str]) -> list[str]:
  """Best-effort batch demangle through c++filt — one subprocess for the whole
  list, names that aren't mangled pass through untouched. If c++filt is missing
  or the line count doesn't round-trip, return the raw names (grouping still
  works, just unreadable)."""
  filt = shutil.which("c++filt")
  if not filt:
    return names
  try:
    out = subprocess.run([filt], input="\n".join(names),
                         capture_output=True, text=True, check=True).stdout
  except (subprocess.CalledProcessError, OSError):
    return names
  demangled = out.splitlines()
  return demangled if len(demangled) == len(names) else names


def _summarize_gc_sections(dropped: list[str]) -> str:
  """Merge the raw `removing unused section` lines by demangled symbol, count
  how many merged into each, and render a count / running-cumulative / symbol
  table sorted by count — so the heaviest dead symbols (and how much of the
  total they account for) sit at the top."""
  sections = [match.group(1) for line in dropped
              if (match := _GC_SECTION_LINE.search(line))]
  counts = Counter(_demangle([_gc_section_symbol(s) for s in sections]))
  rendered = [f"# gc-sections: {len(sections)} dropped sections, "
              f"{len(counts)} unique symbols",
              f"# {'count':>6}  {'cumul':>7}  symbol"]
  cumulative = 0
  for symbol, count in counts.most_common():
    cumulative += count
    rendered.append(f"  {count:6d}  {cumulative:7d}  {symbol}")
  return "\n".join(rendered) + "\n"


def _write_gc_sections_log(build_dir: Path, output: str) -> None:
  """Capture the linker's `--print-gc-sections` lines (each a dropped,
  unreferenced section = dead-code candidate) into gc-sections.log, plus a
  merged-by-symbol, demangled count/cumulative roll-up into gc-sections.summary."""
  dropped = [line.strip() for line in output.splitlines()
             if "removing unused section" in line]
  log_path = build_dir / "gc-sections.log"
  log_path.write_text("\n".join(dropped) + ("\n" if dropped else ""))
  summary_path = build_dir / "gc-sections.summary"
  summary_path.write_text(_summarize_gc_sections(dropped))
  print(f"\ngc-sections: {len(dropped)} dropped section(s) → {log_path}")
  print(f"gc-sections: merged-by-symbol roll-up → {summary_path}")
  if not dropped:
    print("gc-sections: nothing captured — an up-to-date build may not have "
          "re-linked. Re-run after `buildutil clean --all` for a full report.")


def _cmake_build(build_dir: Path, targets: list[str] | None = None,
                 gc_sections: bool = False) -> None:
  # --jobs (buildutil) pins cmake --build --parallel; serial (1) makes the build
  # stop at the first failing file instead of letting in-flight parallel jobs
  # spew. Unset falls back to all cores.
  # --jobs is a PERCENT of the cores (ruling 2026-07-17), default 80 so a
  # build never starves the rest of the box; the count floors at 1
  percent = int(os.environ.get("BUILDUTIL_JOBS_PERCENT") or 80)
  jobs = str(max(1, (os.cpu_count() or 1) * percent // 100))
  cmd = ["cmake", "--build", str(build_dir), "--parallel", jobs]
  for target in (targets or []):
    cmd += ["--target", target]
  # --jump-to-error needs the build output captured so it can find the error
  # locations; otherwise let the child inherit our fds (live, coloured).
  jump = int(os.environ.get("BUILDUTIL_JUMP_TO_ERROR", "0"))
  code, output = _run_build_proc(cmd, capture=jump > 0 or gc_sections)
  if gc_sections:
    _write_gc_sections_log(build_dir, output)
  if code != 0:
    if jump > 0:
      _jump_to_error(output, jump)
    raise subprocess.CalledProcessError(code, cmd)



LAST_BUILD_TYPE_FILE = Path("_build") / ".last-build-type"


def _record_last_build_type(build_type: str) -> None:
  """Remember which configuration was last built, so `test` can follow it
  instead of silently choosing its own."""
  try:
    LAST_BUILD_TYPE_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_BUILD_TYPE_FILE.write_text(build_type + "\n", encoding="utf-8")
  except OSError:
    pass          # a stamp we cannot write is not worth failing a build over


def last_build_type() -> str | None:
  """The configuration `build` last acted on, or None if it never has."""
  try:
    value = LAST_BUILD_TYPE_FILE.read_text(encoding="utf-8").strip()
  except OSError:
    return None
  return value if value in ("Debug", "Release", "RelWithDebInfo") else None


def _warn_dependency_upload_skipped(skipped: bool) -> None:
  if skipped:
    print(
      "\n  ⚠  --skip-dependency-upload-so-everyone-rebuilds-from-source is\n"
      "     set: the dependencies just built WON'T be cached in the project's\n"
      "     conan remote. Uploading them is highly encouraged: every later\n"
      "     clean build (yours, CI, other machines) then downloads them in\n"
      "     seconds instead of compiling them again for minutes. If there is\n"
      "     no real reason to skip it, drop the switch and re-run.\n",
      file=sys.stderr)


def _stamp_build_info() -> None:
  """Autoincrement the local build number and record the commit alongside.
  _bdudata/buildinfo.json is there for a project's version codegen to
  declare as an input — writing it re-runs that codegen next configure."""
  BDUDATA_DIR.mkdir(exist_ok=True)
  counter = BDUDATA_DIR / "buildnum"
  number = (int(counter.read_text().strip() or "0") + 1
            if counter.exists() else 1)
  counter.write_text(f"{number}\n")
  commit, dirty = "unknown", False
  try:
    described = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                               capture_output=True, text=True, cwd=REPO_ROOT)
    if described.returncode == 0:
      commit = described.stdout.strip()
      status = subprocess.run(["git", "status", "--porcelain", "-uno"],
                              capture_output=True, text=True, cwd=REPO_ROOT)
      dirty = status.returncode == 0 and status.stdout.strip() != ""
  except OSError:
    pass                                       # no git: stays "unknown"
  (BDUDATA_DIR / "buildinfo.json").write_text(json.dumps(
    {"number": number, "commit": commit, "dirty": dirty}) + "\n")


def _full_build(build_type: str, tests: bool = True, upload: bool = True,
                bench: bool = False, install: bool = True,
                targets: list[str] | None = None,
                gc_sections: bool = False) -> None:
  settings = _detect_settings(build_type)
  profile = _ensure_profile(settings)
  build_dir = Path("_build") / _profile_name(settings)
  # Say which tree this is acting on, and record it for `test`. The two
  # commands are used as a pair (`build && test`) but defaulted
  # differently -- build to Debug, test to Release -- and neither said
  # so, so a fix could land in one tree while the suite ran the other.
  # Recorded BEFORE the build, not after: if the build fails, a `test`
  # run next should still be looking at the tree you were working in.
  _record_last_build_type(build_type)
  print(f"profile: {_profile_name(settings)}{_project_options.profile_note()}")
  _conan_install(profile, tests=tests)
  _cmake_configure(build_dir, build_type, tests=tests, bench=bench,
                   gc_sections=gc_sections)
  _cmake_build(build_dir, targets, gc_sections=gc_sections)
  # A targeted build is a partial dev build: skip the whole-tree install and the
  # cache upload (they assume every target is present).
  if install and not targets:
    _install_tree(build_dir, tests=tests, bench=bench)
  if upload and not targets:
    _upload_to_remote()



def _install_tree(build_dir: Path, *, tests: bool, bench: bool) -> None:
  """The built tree into _install/, suites included.

  The suites install as their own components, excluded from the DEFAULT
  install: a packaged project's `package()` is a plain `cmake --install`,
  and a conan package has no business shipping megabytes of test binary.
  The local tree is a dev artifact and still gets them -- it is where the
  vscode launch configs point."""
  install = ["cmake", "--install", str(build_dir),
             "--prefix", str(INSTALL_PREFIX)]
  subprocess.check_call(install)
  for component in (["tests"] if tests else []) + (["benches"] if bench else []):
    subprocess.check_call(install + ["--component", component])


def _upload_to_remote() -> None:
  """Push every package in the local conan cache to the project remote.

  Default behaviour for every build-triggering subcommand, gated by
  what the remote seam can actually do (bootstrap.upload_target):
  no URL, an anonymous remote or a login the server refused all skip
  with a one-line note rather than failing the build at the server.
  """
  from . import bootstrap
  name, note = bootstrap.upload_target()
  if not name:
    print(note, flush=True)
    return
  # self-heal a vanished remote: the registration lives in CONAN_HOME,
  # and a retried CI job can start on a cache the pipeline's cleanup
  # already dropped — 'Remote doesn't exist' killed exactly such a
  # retry. Registration is idempotent and reads the same env.
  probe = subprocess.run(
    ["conan", "remote", "list"], capture_output=True, text=True)
  if f"{name}:" not in probe.stdout:
    print(f"conan remote {name!r} not registered — re-registering",
          flush=True)
    bootstrap.ensure_conan_remote(force=True)
  subprocess.check_call(
    ["conan", "upload", "*", "-r", name, "--confirm"])



def _status(outcome: str) -> None:
  """One unambiguous result line at the end of a run, so a single `buildutil`
  invocation reports its own outcome — no shell `echo $?` / grep chains needed."""
  print(f"\n════════════ buildutil: {outcome} ════════════", flush=True)



def _run_pytests(env: dict[str, str] | None = None,
                 extra_args: list[str] | None = None) -> None:
  """Run pytest for every `tools/*` directory that ships a `pytest.ini`."""
  args = list(extra_args or [])
  # a directory declared in [test] python already ran as a ctest entry
  declared = [(HERE / one).resolve()
              for one in config.PROJECT.get("test_python_suites", [])]
  for ini in sorted((HERE / "tools").glob("*/pytest.ini")):
    pytest_cwd = ini.parent
    within = pytest_cwd.resolve()
    if any(one == within or one in within.parents for one in declared):
      continue
    typer.echo(f"\n=== {pytest_cwd.relative_to(HERE)}: pytest ===")
    cmd = [sys.executable, "-m", "pytest", *args]
    result = subprocess.run(cmd, cwd=str(pytest_cwd), env=env)
    # pytest exit 5 = "no tests collected"; an empty suite is fine, not a
    # failure.
    if result.returncode not in (0, 5):
      raise subprocess.CalledProcessError(result.returncode, cmd)



def _disabled_modules() -> set[str]:
  """Module names dormant per _bdudata/modules.ini — excluded from the per-file
  coverage gate the same way the build excludes them. Empty when the file is
  absent (CI ships none and builds everything)."""
  from . import modules
  from .config import MODULES_INI
  return set(modules.load(MODULES_INI)[1])


import types as _module_types
# Re-export everything defined here (helpers keep their leading underscore) so
# commands can `from ..engine import *`; imported modules are filtered out.
__all__ = [
    _name
    for _name, _value in tuple(globals().items())
    if not _name.startswith("__") and not isinstance(_value, _module_types.ModuleType)
]
