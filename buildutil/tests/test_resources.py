"""[resources] declarations: parsing, validation, and the generator's two
back ends agreeing byte for byte.

The e2e half (real cmake, real compilers, the ccache trap) is in
test_resources_e2e.py; this file is the fast half.
"""
import tomllib
from pathlib import Path

import pytest

from buildutil import deposit, embed
from buildutil.config import _resource_sets


def sets(text: str, name="demo"):
  return _resource_sets(tomllib.loads(text), name)


# ------------------------------------------------------- declaration --

def test_single_table_takes_every_default():
  declared = sets('[resources]\ndir = "assets"\n')
  assert declared == [{"module": "demo", "dir": "assets", "files": [],
                       "namespace": "demo", "prefix": "", "mime": []}]


def test_array_of_tables_declares_several_sets():
  declared = sets('[[resources]]\ndir = "a"\nmodule = "one"\n'
                  '[[resources]]\ndir = "b"\nmodule = "two"\n')
  assert [entry["module"] for entry in declared] == ["one", "two"]


def test_absent_section_is_no_sets():
  assert sets('[project]\nname = "demo"\n') == []


def test_project_name_becomes_an_identifier():
  # `bossdeux-main` is a fine project name; `namespace bossdeux-main` is
  # not a namespace, and the failure would be in generated C++.
  assert sets('[resources]\ndir = "a"\n', "bossdeux-main")[0]["namespace"] \
    == "bossdeux_main"


def test_files_accepts_a_bare_string():
  assert sets('[resources]\ndir = "a"\nfiles = "*.html"\n')[0]["files"] \
    == ["*.html"]


def test_mime_overrides_are_flattened_to_pairs():
  declared = sets('[resources]\ndir = "a"\n[resources.mime]\n'
                  '".xyz" = "application/x-xyz"\n')
  assert declared[0]["mime"] == [".xyz=application/x-xyz"]


@pytest.mark.parametrize("text, message", [
  ('[resources]\n', "needs `dir`"),
  ('[resources]\ndir = "/etc"\n', "inside the repo"),
  ('[resources]\ndir = "../elsewhere"\n', "inside the repo"),
  ('[resources]\ndir = "a"\nwat = 1\n', "unknown key"),
  ('[resources]\ndir = "a"\nmime = 3\n', "mime must be a table"),
  ('[resources]\ndir = "a"\n[resources.mime]\nxyz = "t"\n', "start with"),
  ('[resources]\ndir = "a|b"\n', "may not contain"),
  ('[resources]\ndir = "a"\nfiles = ["x,y"]\n', "may not contain"),
])
def test_a_malformed_declaration_names_itself(text, message):
  with pytest.raises(SystemExit) as raised:
    sets(text)
  assert message in str(raised.value)


def test_declarations_render_into_the_deposit(tmp_path):
  cfg = {"cmake_option_prefix": "DEMO", "module_define_prefix": "DEM",
         "name": "demo",
         "resources": sets('[resources]\ndir = "assets"\n'
                           'files = ["*.html", "*.js"]\nprefix = "ui/"\n')}
  out = deposit.ensure(tmp_path, cfg)
  machinery = (out / "buildutil.cmake").read_text()
  assert ('_buildutil_resource_set("demo" "assets" "*.html,*.js" "ui/" '
          '"demo" "")') in machinery
  # and the placeholder never survives into rendered cmake
  assert "@RESOURCE_SETS@" not in machinery
  assert "@RESOURCE_NAMESPACE@" not in machinery


def test_a_project_with_no_resources_renders_no_calls(tmp_path):
  out = deposit.ensure(tmp_path, {"cmake_option_prefix": "A",
                                  "module_define_prefix": "A"})
  assert "_buildutil_resource_set(" not in \
    (out / "buildutil.cmake").read_text().split("# One [resources]")[-1] \
      .split("function(_buildutil_check_resource_claims")[0] \
      .replace("function(_buildutil_resource_set", "")


# ----------------------------------------------------- the generator --

def _fixture(tmp_path):
  """text, an empty file, and every byte value -- the three shapes that
  break a generator: escaping, a zero-size array, and non-ASCII bytes."""
  root = tmp_path / "assets"
  (root / "sub").mkdir(parents=True, exist_ok=True)
  (root / "index.html").write_bytes(b"<h1>hi</h1>\n")
  (root / "empty.dat").write_bytes(b"")
  (root / "sub" / "all.bin").write_bytes(bytes(range(256)))
  manifest = tmp_path / "m.txt"
  manifest.write_text("".join(
    f"{name}\t{root / name}\n"
    for name in ("index.html", "empty.dat", "sub/all.bin")))
  return manifest


def _generate(tmp_path, mode):
  manifest = _fixture(tmp_path)
  out = tmp_path / mode
  assert embed.main([
    "--manifest", str(manifest), "--header", str(out / "resources.hpp"),
    "--source", str(out / "resources.cpp"), "--namespace", "demo::resources",
    "--mode", mode]) == 0
  return out


def test_the_two_back_ends_describe_the_same_table(tmp_path):
  """Byte-for-byte identity is asserted at RUNTIME in the e2e suite; here
  the claim is that the table, the names, the sizes and the digests do not
  differ between them -- so a difference at runtime could only come from
  the compiler, which is what the e2e run covers."""
  arrays = (_generate(tmp_path / "a", "array") / "resources.cpp").read_text()
  embeds = (_generate(tmp_path / "b", "embed") / "resources.cpp").read_text()
  def table(text):
    return text.split("constexpr Resource kAll[] = {")[1].split("};")[0]
  def digests(text):
    return text.split("constexpr std::string_view kDigests[] = {")[1] \
               .split("};")[0]
  assert table(arrays) == table(embeds)
  assert digests(arrays) == digests(embeds)


def test_the_embed_back_end_names_the_files_and_carries_no_bytes(tmp_path):
  text = (_generate(tmp_path, "embed") / "resources.cpp").read_text()
  assert '#embed "' in text
  assert "0x2a," not in text, "the #embed path must not inline any bytes"


def test_the_array_back_end_carries_every_byte(tmp_path):
  text = (_generate(tmp_path, "array") / "resources.cpp").read_text()
  assert not [line for line in text.splitlines() if line.startswith("#embed")]
  # all 256 values, and an array not a string literal (MSVC caps a string
  # literal at 16 KB; an initializer has no cap)
  for value in range(256):
    assert f"{value:#04x}," in text
  assert "const unsigned char kR" in text


def test_an_empty_file_is_a_null_pointer_not_a_zero_size_array(tmp_path):
  """`unsigned char x[] = {}` does not compile, and `#embed` of an empty
  file expands to nothing -- so both back ends have to special-case it."""
  for mode in ("array", "embed"):
    text = (_generate(tmp_path / mode, mode) / "resources.cpp").read_text()
    assert '{"empty.dat", "application/octet-stream", nullptr, 0}' in text


def test_names_are_sorted_so_find_can_binary_search(tmp_path):
  text = (_generate(tmp_path, "array") / "resources.hpp").read_text()
  listed = [line.split("//   ")[1] for line in text.splitlines()
            if line.startswith("//   ")]
  assert listed == sorted(listed) == ["empty.dat", "index.html", "sub/all.bin"]


def test_content_digests_survive_the_preprocessor(tmp_path):
  """The ccache defence. A comment would be stripped before ccache ever
  hashes the source; a constexpr string is not."""
  text = (_generate(tmp_path, "embed") / "resources.cpp").read_text()
  assert "constexpr std::string_view kDigests[]" in text
  # sha256 of b"" -- the empty resource, proving every entry is digested
  assert ("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
          in text)


def test_a_changed_byte_changes_the_generated_source(tmp_path):
  out = _generate(tmp_path, "embed")
  before = (out / "resources.cpp").read_text()
  (tmp_path / "assets" / "index.html").write_bytes(b"<h1>different</h1>\n")
  embed.main(["--manifest", str(tmp_path / "m.txt"),
              "--header", str(out / "resources.hpp"),
              "--source", str(out / "resources.cpp"),
              "--namespace", "demo::resources", "--mode", "embed"])
  assert (out / "resources.cpp").read_text() != before


def test_content_types_come_from_the_extension():
  assert embed.mime_for("a/b/index.html", {}) == "text/html"
  assert embed.mime_for("app.JS", {}) == "text/javascript"
  assert embed.mime_for("blob.unknown", {}) == "application/octet-stream"
  assert embed.mime_for("x.xyz", {".xyz": "application/x-xyz"}) \
    == "application/x-xyz"


def test_the_generator_refuses_a_missing_input(tmp_path, capsys):
  manifest = tmp_path / "m.txt"
  manifest.write_text(f"gone.txt\t{tmp_path / 'gone.txt'}\n")
  assert embed.main([
    "--manifest", str(manifest), "--header", str(tmp_path / "h.hpp"),
    "--source", str(tmp_path / "s.cpp"), "--namespace", "n",
    "--mode", "array"]) == 1
  assert "is not a file" in capsys.readouterr().err


def test_an_unchanged_regeneration_leaves_the_file_alone(tmp_path):
  """Restamping an identical header would rebuild every TU that includes
  it, on every configure."""
  out = _generate(tmp_path, "array")
  header = out / "resources.hpp"
  stamp = header.stat().st_mtime_ns
  _generate(tmp_path, "array")
  assert header.stat().st_mtime_ns == stamp


def test_the_fallback_switch_reaches_the_configure(monkeypatch):
  """A diagnostic, reachable without hand-editing a cmake cache: the env
  var is what a person types, the -D is what cmake needs."""
  from buildutil import engine
  monkeypatch.setenv("BUILDUTIL_EMBED_FALLBACK", "1")
  assert engine._embed_fallback_arg() == "-DBUILDUTIL_EMBED_FALLBACK=ON"
  monkeypatch.delenv("BUILDUTIL_EMBED_FALLBACK")
  assert engine._embed_fallback_arg() == "-DBUILDUTIL_EMBED_FALLBACK=OFF"
