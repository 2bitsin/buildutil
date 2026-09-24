"""The export scan's parts: the ELF reader, the refusals, the version script."""
import shutil
import struct
import subprocess

import pytest

from buildutil import exports

needs_cc = pytest.mark.skipif(shutil.which("cc") is None, reason="needs a C compiler")

GLOBAL, WEAK, LOCAL, UNIQUE = exports.STB_GLOBAL, exports.STB_WEAK, exports.STB_LOCAL, 10
NOTYPE, OBJECT, FUNC, TLS, IFUNC = 0, 1, exports.STT_FUNC, 6, exports.STT_GNU_IFUNC
CODE, DATA = 0x6, 0x3
MARK = exports.SECTION


def _elf(sections, symbols):
  """A 64-bit little-endian relocatable: sections (name, flags, size), symbols (name, bind, type, section)."""
  names = ["", ".shstrtab", ".strtab", ".symtab"] + [name for name, _flags, _size in sections]
  shstrtab, strtab = _strings(names), _strings([""] + [name for name, *_rest in symbols])
  symtab = bytes(24) + b"".join(
    struct.pack("<IBBHQQ", strtab[1][index + 1], bind << 4 | kind, 0, names.index(section), 0, 1)
    for index, (_name, bind, kind, section) in enumerate(symbols))
  bodies = [shstrtab[0], strtab[0], symtab] + [bytes(size) for _name, _flags, size in sections]
  offsets, cursor = [], 64
  for body in bodies:
    offsets.append(cursor)
    cursor += len(body)
  kinds = [3, 3, exports.SHT_SYMTAB] + [1] * len(sections)
  flags = [0, 0, 0] + [flag for _name, flag, _size in sections]
  links = [0, 0, 2] + [0] * len(sections)
  headers = bytes(64) + b"".join(
    struct.pack("<IIQQQQIIQQ", shstrtab[1][index + 1], kinds[index], flags[index], 0, offsets[index],
                len(bodies[index]), links[index], 1 if index == 2 else 0, 1, 24 if index == 2 else 0)
    for index in range(len(bodies)))
  header = b"\x7fELF" + bytes([2, 1, 1]) + bytes(9) + struct.pack(
    "<HHIQQQIHHHHHH", 1, 62, 1, 0, 0, cursor, 0, 64, 0, 0, 64, len(bodies) + 1, 1)
  return exports.Elf("hand.o", header + b"".join(bodies) + headers)


def _strings(names):
  table, offsets = b"", []
  for name in names:
    offsets.append(len(table))
    table += name.encode() + b"\0"
  return table, offsets


def _object(tmp_path, assembly, name="marks"):
  source = tmp_path / f"{name}.s"
  source.write_text(assembly)
  subprocess.run(["cc", "-c", str(source), "-o", str(tmp_path / f"{name}.o")], check=True)
  return tmp_path / f"{name}.o"


def _function(name, section, bind=".globl"):
  return f'.section "{section}","ax"\n{bind} {name}\n.type {name},@function\n{name}:\nret\n'


def _marks(elf):
  return sorted((mark.symbol, mark.text) for mark in elf.marks())


@needs_cc
def test_the_marks_are_the_global_functions_of_the_mark_sections(tmp_path):
  assembly = (_function("current", MARK) + _function("pinned", MARK + "2")
              + _function("plain", ".text") + _function("hidden", MARK + "2", ".local"))
  elf = exports.Elf.load(_object(tmp_path, assembly))
  assert _marks(elf) == [("current", ""), ("pinned", "2")]


@needs_cc
def test_an_object_past_the_section_limit_is_read_through_the_extended_index(tmp_path):
  """-ffunction-sections on a large unit crosses 65280 sections, where the indices move out of line."""
  filler = "".join(f'.section ".text.f{n}","ax"\nnop\n' for n in range(70000))
  path = _object(tmp_path, filler + _function("late", MARK + "4"))
  assert _marks(exports.Elf.load(path)) == [("late", "4")]


def test_a_file_that_is_not_elf_is_named(tmp_path):
  (tmp_path / "bitcode.o").write_bytes(b"BC\xc0\xde")
  with pytest.raises(SystemExit, match="bitcode.o is not an ELF object.*clang 17 or later, where buildutil "
                                       "adds -ffat-lto-objects"):
    exports.Elf.load(tmp_path / "bitcode.o")


@pytest.mark.parametrize("bind, kind", [(GLOBAL, FUNC), (WEAK, FUNC), (GLOBAL, IFUNC)])
def test_a_global_or_weak_function_in_code_is_a_mark(bind, kind):
  elf = _elf([(MARK + "3", CODE, 4)], [("f", bind, kind, MARK + "3")])
  assert (_marks(elf), elf.faults()) == ([("f", "3")], [])


@pytest.mark.parametrize("kind, section, flags, words", [
  (OBJECT, MARK + "9", DATA, "`counter` in hand.o is OBJECT in a data section"),
  (TLS, MARK + "9", DATA, "`counter` in hand.o is TLS in a data section"),
  (NOTYPE, MARK + "9", CODE, "`counter` in hand.o is NOTYPE in a code section"),
  (FUNC, MARK + "9", DATA, "`counter` in hand.o is FUNC in a data section"),
])
def test_anything_but_a_function_in_code_is_refused_naming_symbol_object_and_type(kind, section, flags,
                                                                                   words):
  elf = _elf([(section, flags, 4)], [("counter", GLOBAL, kind, section)])
  assert elf.marks() == []
  assert [fault.split(":")[0] for fault in elf.faults()] == [words]


def test_a_gnu_unique_symbol_is_refused():
  elf = _elf([(MARK, CODE, 4)], [("once", UNIQUE, OBJECT, MARK), ("f", GLOBAL, FUNC, MARK)])
  assert elf.faults() == ["`once` in hand.o is OBJECT in a code section: _Public_ marks functions only; "
                          "data is not exported"]


def test_a_local_mark_is_refused_naming_the_local_symbol():
  elf = _elf([(MARK + "4", CODE, 4)], [("s", LOCAL, FUNC, MARK + "4")])
  assert elf.faults() == ["`s` in hand.o is marked but local (static or in an anonymous namespace): "
                          "_Public_ exports an external definition only"]


def test_a_local_mark_beside_a_global_one_is_refused_too():
  elf = _elf([(MARK, CODE, 8)], [("s", LOCAL, FUNC, MARK), ("f", GLOBAL, FUNC, MARK)])
  assert [fault.split(" is ")[0] for fault in elf.faults()] == ["`s` in hand.o"]


def test_a_compiler_clone_of_a_mark_keeps_its_section_and_is_not_a_fault():
  elf = _elf([(MARK, CODE, 8)], [("f.constprop.0", LOCAL, FUNC, MARK), ("f", GLOBAL, FUNC, MARK)])
  assert (_marks(elf), elf.faults()) == ([("f", "")], [])


def test_a_mapping_symbol_in_a_mark_section_is_not_a_fault():
  """AArch64 and RISC-V objects label every code run `$x`, local and untyped."""
  elf = _elf([(MARK, CODE, 8)], [("$x", LOCAL, NOTYPE, MARK), ("f", GLOBAL, FUNC, MARK)])
  assert (_marks(elf), elf.faults()) == ([("f", "")], [])


def test_a_mark_section_with_no_global_function_is_refused_as_on_ppc64_elfv1():
  """ELFv1 functions are descriptors in .opd; the mark section itself holds no named symbol."""
  elf = _elf([(MARK + "3", CODE, 4), (".opd", DATA, 24)], [("g", GLOBAL, FUNC, ".opd")])
  assert elf.faults() == [f"hand.o: {MARK}3 holds no global function symbol (a ppc64 ELFv1 function "
                          "lives in .opd, which the scan does not read)"]


def test_a_slim_lto_object_is_refused():
  elf = _elf([(".text", CODE, 0), (".gnu.lto_.opts", 0, 8)], [("__gnu_lto_slim", GLOBAL, OBJECT, ".text")])
  assert elf.faults() == ["hand.o is a GCC slim LTO object, whose marks no scan can read: "
                          "build with -ffat-lto-objects or without LTO"]


def test_a_fat_lto_object_is_scanned_like_any_other():
  elf = _elf([(MARK, CODE, 4), (".gnu.lto_.opts", 0, 8)], [("f", GLOBAL, FUNC, MARK)])
  assert (_marks(elf), elf.faults()) == ([("f", "")], [])


def test_a_fat_lto_object_of_data_only_is_scanned_not_refused():
  """A unit of tables (an .embed resource, an empty.cpp) has no code however it is compiled."""
  elf = _elf([(".data", DATA, 16), (".gnu.lto_.opts", 0, 8)], [("table", GLOBAL, OBJECT, ".data")])
  assert (elf.slim(), _marks(elf), elf.faults()) == (False, [], [])


def test_an_object_without_a_mark_section_reads_no_symbol_table():
  elf = _elf([(".text", CODE, 4)], [("f", GLOBAL, FUNC, ".text")])
  elf.symbols = None
  assert (elf.marks(), elf.faults()) == ([], [])


@needs_cc
def test_one_symbol_marked_at_two_versions_is_refused_naming_both_objects(tmp_path):
  first = _object(tmp_path, _function("twice", MARK + "1", ".weak"), "first")
  second = _object(tmp_path, _function("twice", MARK + "2", ".weak"), "second")
  with pytest.raises(SystemExit, match=f"`twice` is marked at 1 in {first} and at 2 in {second}"):
    exports.versions([first, second], 7)


@needs_cc
def test_one_symbol_marked_at_one_version_twice_is_one_mark(tmp_path):
  first = _object(tmp_path, _function("inline", MARK, ".weak"), "first")
  second = _object(tmp_path, _function("inline", MARK, ".weak"), "second")
  assert exports.versions([first, second], 7) == {"inline": 7}


def test_a_malformed_version_is_refused_naming_symbol_object_and_text():
  marks = [exports.Mark("f", "1.5", "a.o"), exports.Mark("g", "07", "b.o"), exports.Mark("h", "", "c.o")]
  assert exports._resolve(marks, 3) == ({"h": 3}, [
    "`_Public_(1.5)` on `f` in a.o: n must be a non-negative integer or empty",
    "`_Public_(07)` on `g` in b.o: n must be a non-negative integer or empty"])


def test_each_version_is_a_node_inheriting_the_one_below():
  assert exports.version_script("CORE", {"b": 7, "a": 3, "c": 7}) == (
    "CORE_3 {\n  global:\n    a;\n  local:\n    *;\n};\n"
    "CORE_7 {\n  global:\n    b;\n    c;\n} CORE_3;\n")
