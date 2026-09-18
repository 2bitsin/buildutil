"""Generate a macOS Info.plist for a [bundle.macos] declaration.

Invoked by the cmake machinery at configure time, never by hand:

    python -m buildutil.bundle --output <plist> --kind app|helper \\
        --executable NAME --identifier ID --name CFBundleName \\
        --version V [--extra 'Key=type:value']...

Why a python step rather than a configure_file template: a project must
not carry the template. `[bundle.macos] plist = { ... }` is arbitrary
TOML with real types -- a bool is <true/>, a string is <string>, an
integer is <integer> -- and a text template cannot type them. plistlib
is stdlib and writes the whole file correctly, including the escaping a
`&` in a display name would otherwise break.
"""
from __future__ import annotations

import argparse
import plistlib
import sys
from pathlib import Path

# What every bundle this machinery makes needs, whatever the project
# said. A project's own keys are merged OVER these -- so a declaration
# can raise LSMinimumSystemVersion or drop the Dock-hiding on the main
# app -- but it never has to restate them.
COMMON = {
  "CFBundleDevelopmentRegion": "en",
  "CFBundleInfoDictionaryVersion": "6.0",
  "CFBundlePackageType": "APPL",
  "CFBundleSignature": "????",
  "NSSupportsAutomaticGraphicsSwitching": True,
}
APP_ONLY = {
  "NSHighResolutionCapable": True,
  # An app whose window is created by the framework it loads still wants
  # a principal class; every CEF-shaped app names this one, and a project
  # that means something else says so in [bundle.macos] plist.
  "NSPrincipalClass": "NSApplication",
}
HELPER_ONLY = {
  # A sub-process must never take the Dock icon or the focus.
  "LSUIElement": True,
  "LSFileQuarantineEnabled": True,
}


def parse_extra(pairs: list[str]) -> dict:
  """'Key=type:value', because the toml types have to survive the trip
  through a rendered cmake call. Rendered by config.py, so a bad spelling
  here is a buildutil bug rather than a user's typo -- it still refuses
  loudly instead of writing a plist with a string where a bool belongs."""
  out: dict = {}
  for pair in pairs:
    key, _, typed = pair.partition("=")
    kind, _, value = typed.partition(":")
    if not key or not kind:
      raise SystemExit(f"buildutil.bundle: bad --extra {pair!r}")
    if kind == "bool":
      out[key] = value.lower() in ("1", "true", "yes")
    elif kind == "int":
      out[key] = int(value)
    elif kind == "string":
      out[key] = value
    elif kind == "array":
      out[key] = [item for item in value.split("\x1f") if item]
    else:
      raise SystemExit(f"buildutil.bundle: unknown --extra type {kind!r}")
  return out


def build(kind: str, executable: str, identifier: str, name: str,
          version: str, extra: dict) -> dict:
  plist = dict(COMMON)
  plist.update(APP_ONLY if kind == "app" else HELPER_ONLY)
  plist.update({
    "CFBundleExecutable": executable,
    "CFBundleIdentifier": identifier,
    "CFBundleName": name,
    "CFBundleVersion": version,
    "CFBundleShortVersionString": version,
  })
  if kind == "helper":
    plist["CFBundleDisplayName"] = name
  plist.update(extra)
  return plist


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(prog="buildutil.bundle")
  parser.add_argument("--output", required=True, type=Path)
  parser.add_argument("--kind", required=True, choices=("app", "helper"))
  parser.add_argument("--executable", required=True)
  parser.add_argument("--identifier", required=True)
  parser.add_argument("--name", required=True)
  parser.add_argument("--version", default="0.0.0")
  parser.add_argument("--extra", action="append", default=[])
  opts = parser.parse_args(argv)
  plist = build(opts.kind, opts.executable, opts.identifier, opts.name,
                opts.version, parse_extra(opts.extra))
  text = plistlib.dumps(plist, sort_keys=True)
  opts.output.parent.mkdir(parents=True, exist_ok=True)
  # write-if-changed: restamping the plist would re-run every POST_BUILD
  # copy that depends on it, on every configure
  if not opts.output.exists() or opts.output.read_bytes() != text:
    opts.output.write_bytes(text)
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
