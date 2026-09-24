"""The Require() lines of sources/CMakeLists.txt, for buildutil and conanfile.py."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

REQUIRE_RE = re.compile(
  # Package name permits letters, digits, underscores, and hyphens
  # (yaml-cpp et al. aren't \w-only).
  r'^\s*Require\s*\(\s*([\w-]+)\s+VERSION\s+"([^"]+)"(.*?)\)',
  re.MULTILINE | re.DOTALL,
)


FLAGS = {"TEST": "test", "BENCH": "bench", "TOOL": "tool", "SYSTEM": "system",
         "FORCE": "force", "PUBLIC": "public"}
LISTS = {"CONAN": "conan", "COMPONENTS": "components", "PLATFORM": "platform",
         "OPTIONS": "options"}
KEYWORDS = FLAGS.keys() | LISTS.keys()


def _coerce_option(value: str):
  """Conan option values as their natural types: True/False, ints,
  everything else verbatim."""
  if value in ("True", "False"):
    return value == "True"
  try:
    return int(value)
  except ValueError:
    return value


def _keyword_values(tokens: list[str]) -> dict[str, list[str]]:
  """Each keyword's value tokens; a flag's list is empty, exact case as in
  cmake_parse_arguments, so `COMPONENTS system` is Boost's component."""
  values, current = {}, None
  for token in tokens:
    if token in KEYWORDS:
      current = token
      values.setdefault(token, [])
    elif current in LISTS:
      values[current].append(token)
  return values


def _options(tokens: list[str]) -> dict:
  """OPTIONS key=value tokens; a token without '=' is refused, since a typo
  that vanished would build the package with the wrong defaults."""
  options = {}
  for token in tokens:
    key, eq, value = token.partition("=")
    if not eq or not key:
      raise ValueError(f"Require OPTIONS token {token!r} is not key=value")
    options[key] = _coerce_option(value)
  return options


def parse_extra(extra: str) -> dict:
  """The keywords after Require(NAME VERSION "v") as one record."""
  values = _keyword_values(extra.split())
  out = {field: keyword in values for keyword, field in FLAGS.items()}
  components = values.get("COMPONENTS", [])
  out["conan"] = next(iter(values.get("CONAN", [])), None)
  out["components"] = [token for token in components if token[:1] not in "+-"]
  out["platform"] = values.get("PLATFORM", [])
  out["options"] = _options(values.get("OPTIONS", []))
  _apply_sugar(out, [token for token in components if token[:1] in "+-"])
  if out["force"] and not out["system"]:
    raise ValueError(
      "Require FORCE without SYSTEM: FORCE takes the host's package over "
      "the conan pins it replaces, and only a SYSTEM dep is the host's")
  return out


def _apply_sugar(out: dict, sugar: list) -> None:
  """COMPONENTS tokens written +name / -name, as the options they mean.

  `+asio` is `with_asio=True`, `-json` is `without_json=True` — the
  per-library options a boost-shaped recipe exposes, one token instead of
  one key=value line each. Whether the recipe HAS that option is conan's
  question and conan answers it by name; nothing here keeps a copy of
  every recipe's option list.
  """
  signs = {}
  for token in sugar:
    sign, name = token[0], token[1:]
    if not name:
      raise ValueError(
        f"Require COMPONENTS token {token!r} names no option")
    if signs.setdefault(name, sign) != sign:
      raise ValueError(
        f"Require COMPONENTS has both '+{name}' and '-{name}'")
    for spelling in (f"with_{name}", f"without_{name}"):
      if spelling in out["options"]:
        raise ValueError(
          f"Require COMPONENTS {token!r} and OPTIONS "
          f"{spelling}={out['options'][spelling]!r} both set an option "
          f"for {name!r}")
    out["options"]["with_" + name if sign == "+" else "without_" + name] = True
  if sugar and out["system"]:
    raise ValueError(
      "Require COMPONENTS option shorthand with SYSTEM is meaningless — "
      "a SYSTEM dep is the host's package and conan never builds it")


def to_conan_version(v: str) -> str:
  v = v.strip()
  if v == "*":
    return "[*]"
  ops = (">=", "<=", ">", "<", "~", "^")
  if not any(v.startswith(op) for op in ops):
    return v
  # Cap the range at (major+1) so non-semver recipe versions like
  # "cci.20210126" — which sort lexically above real releases — don't
  # get picked by conan's resolver.
  m = re.match(r"^[><=~^]+\s*(\d+)\.", v)
  if m:
    major = int(m.group(1))
    return f"[{v} <{major + 1}]"
  return f"[{v}]"


def requires(text: str, target_os: str | None = None) -> list[dict]:
  """The Require() calls active on target_os, or on any platform if None."""
  entries = []
  for name, version, extra in REQUIRE_RE.findall(text):
    info = parse_extra(extra)
    if target_os and info["platform"] and target_os not in info["platform"]:
      continue
    conan = info["conan"] or name.lower()
    entries.append({
      "cmake_name": name,
      "conan_name": conan,
      "version": to_conan_version(version),
      "floor": version,
      "ref": (f"{conan}/system@host" if info["system"]
              else f"{conan}/{to_conan_version(version)}"),
      **info,
    })
  return entries


def wrapper_recipe(root: Path, conan: str) -> Path:
  """Where the driver renders conan's system@host recipe for a project."""
  return root / "_bdudata" / "host" / conan / "conanfile.py"


def recipe_revision(recipe: bytes) -> str:
  """conan's hash revision of an export holding only this conanfile.py."""
  summary = f"conanfile.py: {hashlib.md5(recipe).hexdigest()}\n"
  return hashlib.md5(summary.encode()).hexdigest()
