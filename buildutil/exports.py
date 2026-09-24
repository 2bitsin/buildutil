"""The export scan: `_Public_(n)` marks read from ELF objects become a shared module's version script."""
from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from .embed import write_if_changed

SECTION = ".text._Public_."
VERSION = re.compile(r"(0|[1-9][0-9]*)?")
SHT_SYMTAB, SHT_DYNSYM, SHT_SYMTAB_SHNDX = 2, 11, 18
SHN_UNDEF, SHN_LORESERVE, SHN_XINDEX = 0, 0xff00, 0xffff
SHF_EXECINSTR = 0x4
SPECIAL = -1
STB_LOCAL, STB_GLOBAL, STB_WEAK = 0, 1, 2
STT_NOTYPE, STT_FUNC, STT_SECTION, STT_GNU_IFUNC = 0, 2, 3, 10
TYPES = {0: "NOTYPE", 1: "OBJECT", 2: "FUNC", 3: "SECTION", 4: "FILE", 5: "COMMON", 6: "TLS",
         10: "GNU_IFUNC"}
LTO_SECTION, LTO_SLIM = ".gnu.lto_", "__gnu_lto_slim"


@dataclass(frozen=True)
class Section:
  name: str
  kind: int
  flags: int
  offset: int
  size: int
  link: int


@dataclass(frozen=True)
class Symbol:
  name: str
  bind: int
  kind: int
  section: int


@dataclass(frozen=True)
class Mark:
  symbol: str
  text: str
  origin: str


class Elf:
  """The section table and symbol tables of one ELF file, 32 or 64 bit, either byte order."""

  def __init__(self, label: str, data: bytes):
    self.label = label
    self.data = data
    if data[:4] != b"\x7fELF":
      raise SystemExit(f"buildutil exports: {label} is not an ELF object; the export scan reads "
                       "ELF objects only (an LTO bitcode object carries no sections: build with "
                       "clang 17 or later, where buildutil adds -ffat-lto-objects, or without LTO)")
    self.wide = data[4] == 2
    self.order = "<" if data[5] == 1 else ">"
    self.sections = self._sections()

  @classmethod
  def load(cls, path: Path) -> Elf:
    return cls(str(path), path.read_bytes())

  def _unpack(self, layout: str, offset: int) -> tuple:
    return struct.unpack_from(self.order + layout, self.data, offset)

  def _sections(self) -> list[Section]:
    fields = self._unpack("HHIQQQIHHHHHH" if self.wide else "HHIIIIIHHHHHH", 16)
    offset, entry, count, names = fields[5], fields[10], fields[11], fields[12]
    if offset == 0:
      return []
    first = self._section_fields(offset)
    count = count or first[5]
    names = first[6] if names == SHN_XINDEX else names
    raw = [self._section_fields(offset + index * entry) for index in range(count)]
    table = raw[names][4]
    return [Section(self._string(table, name), kind, flags, at, size, link)
            for name, kind, flags, _address, at, size, link, *_rest in raw]

  def _section_fields(self, offset: int) -> tuple:
    return self._unpack("IIQQQQIIQQ" if self.wide else "IIIIIIIIII", offset)

  def _string(self, table: int, offset: int) -> str:
    end = self.data.index(b"\0", table + offset)
    return self.data[table + offset:end].decode("utf-8", "replace")

  def symbols(self, kind: int) -> list[Symbol]:
    """Every named symbol of the tables of one kind (SHT_SYMTAB or SHT_DYNSYM)."""
    found = []
    for index, table in enumerate(self.sections):
      if table.kind == kind:
        found += self._table(index, table)
    return found

  def _table(self, index: int, table: Section) -> list[Symbol]:
    size = 24 if self.wide else 16
    layout = "IBBHQQ" if self.wide else "IIIBBH"
    strings = self.sections[table.link].offset
    extended = self._extended_indices(index)
    found = []
    for number in range(table.size // size):
      fields = self._unpack(layout, table.offset + number * size)
      name, info, shndx = (fields[0], fields[1], fields[3]) if self.wide else \
                          (fields[0], fields[3], fields[5])
      if shndx == SHN_XINDEX:
        shndx = extended[number]
      elif shndx >= SHN_LORESERVE:
        shndx = SPECIAL
      if name:
        found.append(Symbol(self._string(strings, name), info >> 4, info & 0xf, shndx))
    return found

  def _extended_indices(self, symtab: int) -> list[int]:
    for table in self.sections:
      if table.kind == SHT_SYMTAB_SHNDX and table.link == symtab:
        return list(struct.unpack_from(f"{self.order}{table.size // 4}I", self.data, table.offset))
    return []

  @cached_property
  def _marked(self) -> dict[int, list[Symbol]]:
    """The symbols of each mark section, by section index; no symbol table is read without one."""
    marked = {index: [] for index, section in enumerate(self.sections) if section.name.startswith(SECTION)}
    if marked:
      for symbol in self.symbols(SHT_SYMTAB):
        if symbol.section in marked and symbol.kind != STT_SECTION:
          marked[symbol.section].append(symbol)
    return marked

  def slim(self) -> bool:
    """GCC's slim LTO object, which says so by a symbol: intermediate code only, no mark in a section yet."""
    lto = any(section.name.startswith(LTO_SECTION) for section in self.sections)
    return lto and any(symbol.name == LTO_SLIM for symbol in self.symbols(SHT_SYMTAB))

  def marks(self) -> list[Mark]:
    """Every global or weak function defined in a mark section, with the text after _Public_."""
    return [Mark(symbol.name, self.sections[index].name[len(SECTION):], self.label)
            for index, symbols in self._marked.items() for symbol in symbols
            if _exportable(symbol, self.sections[index])]

  def faults(self) -> list[str]:
    """Why a mark of this object cannot be exported as written, one line each."""
    if self.slim():
      return [f"{self.label} is a GCC slim LTO object, whose marks no scan can read: "
              "build with -ffat-lto-objects or without LTO"]
    return [fault for index, symbols in self._marked.items()
            for fault in _section_faults(self.label, self.sections[index], symbols)]

  def exported(self) -> set[str]:
    """The names a shared library's dynamic symbol table defines."""
    return {symbol.name for symbol in self.symbols(SHT_DYNSYM) if symbol.section > SHN_UNDEF}


def _exportable(symbol: Symbol, section: Section) -> bool:
  return (symbol.kind in (STT_FUNC, STT_GNU_IFUNC) and symbol.bind in (STB_GLOBAL, STB_WEAK)
          and bool(section.flags & SHF_EXECINSTR))


def _section_faults(label: str, section: Section, symbols: list[Symbol]) -> list[str]:
  faults = [_symbol_fault(label, section, symbol) for symbol in symbols
            if not _exportable(symbol, section) and not _generated(symbol)]
  if not faults and not any(_exportable(symbol, section) for symbol in symbols):
    faults.append(f"{label}: {section.name} holds no global function symbol (a ppc64 ELFv1 function "
                  "lives in .opd, which the scan does not read)")
  return faults


def _generated(symbol: Symbol) -> bool:
  """A symbol the toolchain adds: a mapping symbol ($x, $d on ARM and RISC-V), a local label, a clone."""
  local = symbol.bind == STB_LOCAL
  return local and (symbol.kind == STT_NOTYPE or "." in symbol.name)


def _symbol_fault(label: str, section: Section, symbol: Symbol) -> str:
  if symbol.bind == STB_LOCAL:
    return (f"`{symbol.name}` in {label} is marked but local (static or in an anonymous namespace): "
            "_Public_ exports an external definition only")
  kind = TYPES.get(symbol.kind, str(symbol.kind))
  where = "a code" if section.flags & SHF_EXECINSTR else "a data"
  return (f"`{symbol.name}` in {label} is {kind} in {where} section: _Public_ marks functions only; "
          "data is not exported")


def versions(paths: list[Path], major: int) -> dict[str, int]:
  """Every marked symbol of the objects at its version, or SystemExit naming each refusal."""
  elves = [Elf.load(path) for path in paths]
  refused = [fault for elf in elves for fault in elf.faults()]
  found, unresolved = _resolve([mark for elf in elves for mark in elf.marks()], major)
  refused += unresolved
  if refused:
    raise SystemExit("buildutil exports:\n  " + "\n  ".join(refused))
  return found


def _resolve(marks: list[Mark], major: int) -> tuple[dict[str, int], list[str]]:
  found, origin, refused = {}, {}, []
  for mark in marks:
    if not VERSION.fullmatch(mark.text):
      refused.append(f"`_Public_({mark.text})` on `{mark.symbol}` in {mark.origin}: "
                     "n must be a non-negative integer or empty")
      continue
    number = int(mark.text) if mark.text else major
    if found.setdefault(mark.symbol, number) != number:
      refused.append(f"`{mark.symbol}` is marked at {found[mark.symbol]} in {origin[mark.symbol]} "
                     f"and at {number} in {mark.origin}")
    origin.setdefault(mark.symbol, mark.origin)
  return found, refused


def version_script(node: str, marks: dict[str, int]) -> str:
  """One node per version, each newer node inheriting the older, everything unmarked local."""
  text, parent = [], ""
  for number in sorted(set(marks.values())):
    names = "".join(f"    {name};\n" for name in sorted(n for n, v in marks.items() if v == number))
    local = "  local:\n    *;\n" if not parent else ""
    text.append(f"{node}_{number} {{\n  global:\n{names}{local}}}{parent};\n")
    parent = f" {node}_{number}"
  return "".join(text)


def _argument(text: str) -> str:
  return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def link(options: argparse.Namespace) -> None:
  """Before the link: the marks, the version script, and the linker argument that applies it."""
  paths = [Path(line) for line in Path(options.objects).read_text().splitlines() if line]
  marks = versions(paths, options.major)
  script = options.map
  if not script and marks:
    script = options.map_out
    write_if_changed(Path(script), version_script(options.node, marks))
  arguments = [f"--version-script={script}"] if script else []
  write_if_changed(Path(options.rsp), "".join(_argument(a) + "\n" for a in arguments))
  record = {"marks": marks, "map": script}
  write_if_changed(Path(options.record), json.dumps(record, indent=1, sort_keys=True) + "\n")


def verify(options: argparse.Namespace) -> None:
  """After the link: every mark is exported, else the library is removed and the build fails."""
  record = json.loads(Path(options.record).read_text())
  library = Path(options.library)
  missing = sorted(set(record["marks"]) - Elf.load(library).exported())
  if missing:
    library.unlink()
    raise SystemExit(f"buildutil exports: {record['map']} does not export what _Public_ marks: "
                     + ", ".join(missing))


def main(argv: list[str]) -> None:
  parser = argparse.ArgumentParser(prog="python -m buildutil.exports")
  steps = parser.add_subparsers(dest="step", required=True)
  before = steps.add_parser("link")
  before.add_argument("--objects", required=True)
  before.add_argument("--major", type=int, required=True)
  before.add_argument("--node", required=True)
  before.add_argument("--map", default="")
  before.add_argument("--map-out", required=True)
  before.add_argument("--rsp", required=True)
  before.add_argument("--record", required=True)
  after = steps.add_parser("verify")
  after.add_argument("--library", required=True)
  after.add_argument("--record", required=True)
  options = parser.parse_args(argv)
  {"link": link, "verify": verify}[options.step](options)


if __name__ == "__main__":
  main(sys.argv[1:])
