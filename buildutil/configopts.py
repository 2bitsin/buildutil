"""Build-time configuration options — the menuconfig-lite core.

An option has a value SET (validated), a default, and help; its value
persists in _bdudata/config.ini (checkout-local, like modules.ini) and
resolves env > saved > default, with `buildutil config` as the CLI
(list / NAME=VALUE / --reset). The registry below is buildutil's own
(built-in) options; project-declared options (a Config_option() call
parsed like Require) arrive with the full system once its vocabulary
is ruled on.

First consumer: module_linkage (owner idea) — untagged modules, the
static-by-default majority, switch between static and shared as a
CONFIGURATION, while kind-tagged modules keep their word. The value
participates in the profile dir name, so both linkages coexist as
separate build trees and a flip never mixes artifacts.
"""
from __future__ import annotations

import os
from pathlib import Path

OPTIONS = {
  "module_linkage": {
    "values": ("static", "shared"),
    "default": "static",
    "help": "how untagged modules (no kind tag, no main.*) build; "
            "kind-tagged modules are declarations and keep their word. "
            "Shared libraries keep -fvisibility=hidden: only annotated "
            "symbols (or a PUBLISH_SYMBOLS module) export.",
  },
}


def _path(root: Path | None = None) -> Path:
  if root is None:
    from . import config
    root = config.REPO_ROOT
  return Path(root) / "_bdudata" / "config.ini"


def load(root: Path | None = None) -> dict[str, str]:
  """The persisted [options] section; unknown keys survive round-trips
  (a newer buildutil may have written them)."""
  path = _path(root)
  values: dict[str, str] = {}
  if not path.is_file():
    return values
  section = ""
  for raw in path.read_text().splitlines():
    line = raw.split("#", 1)[0].strip()
    if not line:
      continue
    if line.startswith("[") and line.endswith("]"):
      section = line[1:-1].strip().lower()
    elif section == "options" and "=" in line:
      key, _, value = line.partition("=")
      values[key.strip()] = value.strip()
  return values


def save(values: dict[str, str], root: Path | None = None) -> None:
  path = _path(root)
  path.parent.mkdir(parents=True, exist_ok=True)
  lines = ["# build-time configuration (buildutil config; gitignored)",
           "[options]"]
  lines += [f"{key} = {value}" for key, value in sorted(values.items())]
  path.write_text("\n".join(lines) + "\n")


def _validate(name: str, value: str) -> str:
  spec = OPTIONS.get(name)
  if spec is None:
    raise SystemExit(
      f"buildutil config: unknown option {name!r} "
      f"(have: {', '.join(sorted(OPTIONS))})")
  if value not in spec["values"]:
    raise SystemExit(
      f"buildutil config: {name} must be one of "
      f"{', '.join(spec['values'])} — not {value!r}")
  return value


def get(name: str, root: Path | None = None) -> str:
  """env (BUILDUTIL_OPT_<NAME>) > saved > default — the same order the
  scaffolded conanfile applies, so driver and recipe never disagree."""
  spec = OPTIONS[name]
  env = os.environ.get(f"BUILDUTIL_OPT_{name.upper()}", "")
  if env:
    return _validate(name, env)
  saved = load(root).get(name, "")
  if saved:
    return _validate(name, saved)
  return spec["default"]


def set_value(name: str, value: str, root: Path | None = None) -> None:
  _validate(name, value)
  values = load(root)
  values[name] = value
  save(values, root)


def reset(names: list[str] | None = None, root: Path | None = None) -> None:
  """Forget saved values (all of them when names is empty) — the next
  resolve falls back to env/default."""
  values = load(root)
  for name in (names or list(values)):
    values.pop(name, None)
  save(values, root)


def describe(root: Path | None = None) -> list[tuple[str, str, str, str]]:
  """(name, value, source, help) per option, for `buildutil config`."""
  saved = load(root)
  rows = []
  for name, spec in sorted(OPTIONS.items()):
    env = os.environ.get(f"BUILDUTIL_OPT_{name.upper()}", "")
    if env:
      value, source = env, "env"
    elif name in saved:
      value, source = saved[name], "saved"
    else:
      value, source = spec["default"], "default"
    rows.append((name, value, source, spec["help"]))
  return rows
