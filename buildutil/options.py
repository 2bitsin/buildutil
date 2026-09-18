"""The project's [options]: a value chosen per build, injected as a macro."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .embed import write_if_changed

# Not configopts.py, which is buildutil's own build-time configuration:
# these belong to the PROJECT. The rendered cmake machinery calls this
# module (`python -m buildutil.options`) for the header it force-includes.

KINDS = {bool: "bool", int: "int", str: "string"}
_TRUE = ("on", "true", "yes", "1")
_FALSE = ("off", "false", "no", "0")

# A string value travels to cmake inside a rendered, quoted cmake string
# before it is a C++ literal, so these mean something on the way.
TEXT_REFUSED = '";\\|$\n\r\t'


def kind_of(value) -> str | None:
  """The kind a declared default is, or None for a value that is none."""
  return KINDS.get(type(value))


def refused_characters(text: str) -> str:
  """What a string option may not carry, wherever the value came from."""
  return "".join(sorted(set(text) & set(TEXT_REFUSED)))


def macro_name(prefix: str, name: str) -> str:
  return f"{prefix}_{name.upper()}"


def cache_variable(prefix: str, name: str) -> str:
  return f"{prefix}_OPTION_{name.upper()}"


def _environment_variable(name: str) -> str:
  return f"BUILDUTIL_OPTION_{name.upper()}"


def parse_value(kind: str, text: str, where: str):
  """One text value in its declared kind, or a refusal reading `where`."""
  if kind == "bool":
    if text.lower() in _TRUE:
      return True
    if text.lower() in _FALSE:
      return False
    raise ValueError(f"{where} is a boolean option: on, off, true or false "
                     f"-- not {text!r}")
  if kind == "int":
    try:
      return int(text, 0)
    except ValueError:
      raise ValueError(f"{where} is an integer option -- not {text!r}") from None
  refused = refused_characters(text)
  if refused:
    raise ValueError(f"{where} may not hold {refused!r} -- the value "
                     "travels through a cmake string on its way to the "
                     "compiler")
  return text


def cmake_value(value) -> str:
  if isinstance(value, bool):
    return "ON" if value else "OFF"
  return str(value)


def macro_value(value) -> str:
  """A bool is 0 or 1 so `#if` reads it; a string is a C++ literal."""
  if isinstance(value, bool):
    return "1" if value else "0"
  if isinstance(value, str):
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
  return str(value)


def display_value(value) -> str:
  return ("on" if value else "off") if isinstance(value, bool) else str(value)


def declared(project: dict | None = None) -> dict:
  if project is None:
    from .config import PROJECT
    project = PROJECT
  return project["options"]


def parse_flags(pairs, options: dict) -> dict:
  """`name=value` arguments against the declarations."""
  known = ", ".join(sorted(options)) or "none -- declare them in [options]"
  chosen = {}
  for pair in pairs or []:
    name, separator, text = pair.partition("=")
    if not separator:
      raise ValueError(f"--option takes name=value, not {pair!r} "
                       f"(declared: {known})")
    if name not in options:
      raise ValueError(f"--option: no project option {name!r} "
                       f"(declared: {known})")
    chosen[name] = parse_value(kind_of(options[name]), text,
                               f"--option {name}")
  return chosen


def apply(pairs, options: dict | None = None) -> None:
  """Record the choice for the rest of the run, so every verb that
  configures reads the same answer without carrying it through."""
  options = declared() if options is None else options
  for name, value in parse_flags(pairs, options).items():
    os.environ[_environment_variable(name)] = cmake_value(value)


def chosen(options: dict | None = None) -> dict:
  """Absence is the default; an empty string is a chosen value. Every
  consumer is outside the CLI's refusal path, so a value only the
  environment carries is refused here rather than traced back there."""
  options = declared() if options is None else options
  out = {}
  for name, default in options.items():
    variable = _environment_variable(name)
    text = os.environ.get(variable)
    if text is None:
      out[name] = default
      continue
    try:
      out[name] = parse_value(kind_of(default), text, variable)
    except ValueError as error:
      sys.exit(f"buildutil: {error}")
  return out


def cmake_arguments(prefix: str, options: dict | None = None) -> list[str]:
  """Every option, always: one left out would be inherited from the cache
  of whatever the last build of this tree chose."""
  return [f"-D{cache_variable(prefix, name)}={cmake_value(value)}"
          for name, value in chosen(options).items()]


def declaration_list(options: dict) -> str:
  """The declarations as a cmake list of `name|kind|default` entries."""
  return ";".join(f"{name}|{kind_of(value)}|{cmake_value(value)}"
                  for name, value in options.items())


def _differing(options: dict | None) -> list[tuple[str, object]]:
  options = declared() if options is None else options
  return [(name, value) for name, value in chosen(options).items()
          if value != options[name]]


def flag_arguments(options: dict | None = None) -> list[str]:
  """The `--option` arguments that reproduce this run's choices."""
  out = []
  for name, value in _differing(options):
    out += ["--option", f"{name}={display_value(value)}"]
  return out


# The verbs app._option_option() is attached to; the root callback
# declares no --option, which is why the flag goes after the verb.
VERBS = ("analyze", "bench", "build", "coverage", "godbolt", "run",
         "test", "vscode")


def with_chosen(verb_args: list) -> list:
  """A verb's arguments carrying this run's choices (editor tasks)."""
  if not verb_args or verb_args[0] not in VERBS:
    return list(verb_args)
  return [verb_args[0], *flag_arguments(), *verb_args[1:]]


def profile_note(options: dict | None = None) -> str:
  """What the profile line gains when a build is not on the defaults."""
  differing = ", ".join(f"{name}={display_value(value)}"
                        for name, value in _differing(options))
  return f" options: {differing}" if differing else ""


def header_text(prefix: str, values: list[tuple[str, object]]) -> str:
  defines = "\n".join(f"#define {macro_name(prefix, name)} "
                      f"{macro_value(value)}" for name, value in values)
  return f"""\
// Generated by buildutil -- do not edit, and do not commit.
// The project's [options], force-included into every translation unit;
// read them with `#if`, never `#ifdef` (every one of them is defined).
#pragma once

{defines}
"""


def write_header(path, prefix: str, values: list[tuple[str, object]]) -> Path:
  path = Path(path)
  write_if_changed(path, header_text(prefix, values))
  return path


def _declaration(text: str) -> tuple[str, object]:
  """One NAME:KIND:VALUE argument of the generator's own command line."""
  name, _, rest = text.partition(":")
  kind, separator, value = rest.partition(":")
  if not name or not separator or kind not in KINDS.values():
    raise ValueError(
      f"option {name or text!r} is not NAME:KIND:VALUE with KIND one of "
      f"{', '.join(sorted(KINDS.values()))}")
  return name, parse_value(kind, value, f"option {name}")


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(
    prog="buildutil.options", description=__doc__.splitlines()[0])
  parser.add_argument("--prefix", required=True)
  parser.add_argument("--output", required=True, type=Path)
  parser.add_argument("--option", action="append", default=[],
                      metavar="NAME:KIND:VALUE")
  args = parser.parse_args(argv)
  write_header(args.output, args.prefix,
               [_declaration(one) for one in args.option])
  return 0


if __name__ == "__main__":
  try:
    sys.exit(main())
  except ValueError as error:
    sys.exit(f"buildutil.options: {error}")
