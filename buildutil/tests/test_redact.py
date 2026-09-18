"""One masking rule for every place a URL reaches a log."""
import pytest

from buildutil import redact


@pytest.mark.parametrize("raw, masked", [
  ("https://oauth2:sekrit@gitlab.example/api/v4/simple",
   "https://<credentials>@gitlab.example/api/v4/simple"),
  ("https://sekrit@gitlab.example/simple",
   "https://<credentials>@gitlab.example/simple"),
  ("https://repo.example/conan", "https://repo.example/conan"),
  ("--index-url", "--index-url"),
])
def test_the_authority_credentials_are_masked(raw, masked):
  assert redact.credentials(raw) == masked


def test_a_path_with_an_at_sign_survives():
  url = "https://repo.example/conan/pkg@1.0/file"
  assert redact.credentials(url) == url
