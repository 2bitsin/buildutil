"""No bossdeux artifact may appear in CODE — string constants the
driver would act on (binary names, module paths, suite modules,
vendored-path filters). Docstrings and comments may cite bossdeux as
the worked example of the seam; code may not fall back to it."""
import ast
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]

# names of bossdeux things the extraction moved behind buildutil.toml
BANNED = ("bdxmcp", "bdxgui", "bdx86emu", "decodex86", "corex86",
          "inspector.bench", "image/contrib", "BOSSDEUX_")


def _code_strings(tree: ast.AST) -> list[str]:
  """Every string constant that is NOT a docstring position."""
  docstrings = set()
  for node in ast.walk(tree):
    if isinstance(node, (ast.Module, ast.ClassDef,
                         ast.FunctionDef, ast.AsyncFunctionDef)):
      body = node.body
      if (body and isinstance(body[0], ast.Expr)
          and isinstance(body[0].value, ast.Constant)
          and isinstance(body[0].value.value, str)):
        docstrings.add(id(body[0].value))
  return [node.value for node in ast.walk(tree)
          if isinstance(node, ast.Constant) and isinstance(node.value, str)
          and id(node) not in docstrings]


def test_no_bossdeux_names_in_code_strings():
  offenders = []
  for src in sorted(PKG.rglob("*.py")):
    parts = src.relative_to(PKG).parts
    if parts[0] in ("tests", "__pycache__", "templates"):
      continue  # templates/ scaffold is rendered text, checked elsewhere
    tree = ast.parse(src.read_text(encoding="utf-8"))
    for value in _code_strings(tree):
      for token in BANNED:
        if token in value:
          offenders.append(f"{'/'.join(parts)}: {token!r} in {value!r}")
  assert not offenders, "\n".join(offenders)


def test_scaffold_templates_carry_no_bossdeux_names():
  tpl = PKG / "templates"
  for src in sorted(p for p in tpl.rglob("*") if p.is_file()):
    if "__pycache__" in src.parts:
      continue
    text = src.read_text(encoding="utf-8")
    for token in ("bdxmcp", "bdxgui", "bdx86emu", "decodex86",
                  "inspector.bench", "image/contrib"):
      assert token not in text, f"{src}: {token}"
