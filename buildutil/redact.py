"""Masking for values that reach a log. Stdlib-only: `update` runs
pre-project and pre-venv, so nothing here may import config."""
from __future__ import annotations

import re

_AUTHORITY_CREDENTIALS = re.compile(r"//[^@/]+@")


def credentials(text: str) -> str:
  """A URL's authority userinfo replaced by a placeholder; the username
  goes with the token, it identifies as much."""
  return _AUTHORITY_CREDENTIALS.sub("//<credentials>@", text)
