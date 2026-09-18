"""The conan remote seam: .env loading, env resolution (CONAN_REMOTE_* /
CI_ARTIFACTORY_* / defaults), the stamp-guarded every-run registration,
and the register flow's conancenter replacement + optional login."""
import json

import pytest

from buildutil import bootstrap, config


ENV_KEYS = ("CONAN_REMOTE_NAME", "CONAN_REMOTE_URL", "CONAN_REMOTE_USER",
            "CONAN_REMOTE_PASS", "CI_ARTIFACTORY_NAME", "CI_ARTIFACTORY_HREF",
            "CI_ARTIFACTORY_USER", "CI_ARTIFACTORY_PASS", "CONAN_HOME")


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
  for key in ENV_KEYS:
    monkeypatch.delenv(key, raising=False)
  monkeypatch.setenv("CONAN_HOME", str(tmp_path / "conanhome"))
  return tmp_path / "conanhome"


# ------------------------------------------------------------------ .env

def test_dotenv_loads_without_overriding_env(monkeypatch, tmp_path):
  monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
  (tmp_path / ".env").write_text(
    "# secrets\n"
    "CONAN_REMOTE_URL=https://repo.example/conan\n"
    'CONAN_REMOTE_USER="alice"\n'
    "CONAN_REMOTE_PASS='s3cret=with=equals'\n"
    "ALREADY_SET=from-dotenv\n"
    "not a kv line\n")
  monkeypatch.delenv("CONAN_REMOTE_URL", raising=False)
  monkeypatch.delenv("CONAN_REMOTE_USER", raising=False)
  monkeypatch.delenv("CONAN_REMOTE_PASS", raising=False)
  monkeypatch.setenv("ALREADY_SET", "from-env")
  config.load_dotenv()
  import os
  assert os.environ["CONAN_REMOTE_URL"] == "https://repo.example/conan"
  assert os.environ["CONAN_REMOTE_USER"] == "alice"          # quotes stripped
  assert os.environ["CONAN_REMOTE_PASS"] == "s3cret=with=equals"
  assert os.environ["ALREADY_SET"] == "from-env"             # env wins


def test_dotenv_missing_file_is_fine(monkeypatch, tmp_path):
  monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
  config.load_dotenv()                                       # no raise


# ------------------------------------------------------- env resolution

def test_remote_env_defaults_name_to_conancenter(clean_env, monkeypatch):
  monkeypatch.setenv("CONAN_REMOTE_URL", "https://repo.example/conan")
  name, url, user, pw = bootstrap.conan_remote_env()
  assert (name, url, user, pw) == (
    "conancenter", "https://repo.example/conan", None, None)


def test_remote_env_unset_url_gates(clean_env):
  assert bootstrap.conan_remote_env()[1] is None


def test_remote_env_ci_fallback_and_precedence(clean_env, monkeypatch):
  monkeypatch.setenv("CI_ARTIFACTORY_NAME", "jfrog")
  monkeypatch.setenv("CI_ARTIFACTORY_HREF", "https://ci.example")
  assert bootstrap.conan_remote_env()[:2] == ("jfrog", "https://ci.example")
  monkeypatch.setenv("CONAN_REMOTE_URL", "https://mine.example")
  assert bootstrap.conan_remote_env()[1] == "https://mine.example"


# ----------------------------------------------------- stamp-guarded gate

@pytest.fixture
def registration(monkeypatch):
  calls = []
  def fake_register():
    calls.append(True)
    return fake_register.result
  fake_register.result = True
  monkeypatch.setattr(bootstrap, "register_conan_remote", fake_register)
  return calls, fake_register


def _remotes_json(home, name, url):
  home.mkdir(parents=True, exist_ok=True)
  (home / "remotes.json").write_text(json.dumps(
    {"remotes": [{"name": name, "url": url, "verify_ssl": True}]}))


def test_ensure_skips_without_url(clean_env, registration):
  calls, _ = registration
  bootstrap.ensure_conan_remote()
  assert calls == []


def test_ensure_registers_once_then_stamps(clean_env, registration, monkeypatch):
  calls, _ = registration
  monkeypatch.setenv("CONAN_REMOTE_URL", "https://repo.example/conan")
  bootstrap.ensure_conan_remote()
  assert len(calls) == 1
  # a successful registration writes the stamp; simulate conan's side
  _remotes_json(clean_env, "conancenter", "https://repo.example/conan")
  bootstrap.ensure_conan_remote()
  assert len(calls) == 1                     # stamped + registered: no-op


def test_ensure_reregisters_on_env_change(clean_env, registration, monkeypatch):
  calls, _ = registration
  monkeypatch.setenv("CONAN_REMOTE_URL", "https://repo.example/conan")
  bootstrap.ensure_conan_remote()
  _remotes_json(clean_env, "conancenter", "https://repo.example/conan")
  monkeypatch.setenv("CONAN_REMOTE_PASS", "rotated")
  bootstrap.ensure_conan_remote()
  assert len(calls) == 2                     # fingerprint moved


def test_ensure_reregisters_when_remote_vanishes(clean_env, registration,
                                                monkeypatch):
  calls, _ = registration
  monkeypatch.setenv("CONAN_REMOTE_URL", "https://repo.example/conan")
  bootstrap.ensure_conan_remote()            # stamps, but no remotes.json
  bootstrap.ensure_conan_remote()
  assert len(calls) == 2                     # stamp alone is not enough


def test_ensure_failed_registration_leaves_no_stamp(clean_env, registration,
                                                    monkeypatch):
  calls, fake = registration
  fake.result = False
  monkeypatch.setenv("CONAN_REMOTE_URL", "https://repo.example/conan")
  bootstrap.ensure_conan_remote()
  assert not (clean_env / "buildutil-remote.stamp").exists()
  bootstrap.ensure_conan_remote()
  assert len(calls) == 2                     # retried


def test_ensure_force_always_registers(clean_env, registration, monkeypatch):
  calls, _ = registration
  monkeypatch.setenv("CONAN_REMOTE_URL", "https://repo.example/conan")
  _remotes_json(clean_env, "conancenter", "https://repo.example/conan")
  bootstrap.ensure_conan_remote()
  bootstrap.ensure_conan_remote(force=True)
  assert len(calls) == 2


# ------------------------------------------------------------ register flow

@pytest.fixture
def conan_calls(monkeypatch):
  calls = []
  monkeypatch.setattr(bootstrap, "_conan_bin", lambda: "/usr/bin/conan")
  def record_call(cmd, env=None, check=None, **kwargs):
    calls.append(cmd[1:])                    # drop the binary
    class R:
      returncode = 0
      stdout = ""
      stderr = ""
    return R()
  monkeypatch.setattr(bootstrap.subprocess, "check_call",
                      lambda cmd, env=None: record_call(cmd, env) and None)
  monkeypatch.setattr(bootstrap.subprocess, "run", record_call)
  return calls


def test_register_anonymous_replaces_conancenter_in_place(clean_env, conan_calls,
                                                          monkeypatch):
  monkeypatch.setenv("CONAN_REMOTE_URL", "https://repo.example/conan")
  assert bootstrap.register_conan_remote() is True
  assert ["remote", "add", "conancenter",
          "https://repo.example/conan", "--force"] in conan_calls
  assert not any("login" in c for c in conan_calls)      # no creds, no login
  assert not any(c[:2] == ["remote", "remove"] for c in conan_calls)


def test_register_custom_name_logs_in_and_drops_conancenter(clean_env,
                                                            conan_calls,
                                                            monkeypatch):
  monkeypatch.setenv("CONAN_REMOTE_NAME", "mirror")
  monkeypatch.setenv("CONAN_REMOTE_URL", "https://repo.example/conan")
  monkeypatch.setenv("CONAN_REMOTE_USER", "alice")
  monkeypatch.setenv("CONAN_REMOTE_PASS", "s3cret")
  assert bootstrap.register_conan_remote() is True
  assert ["remote", "add", "mirror",
          "https://repo.example/conan", "--force"] in conan_calls
  assert ["remote", "login", "mirror", "alice", "-p", "s3cret"] in conan_calls
  assert ["remote", "remove", "conancenter"] in conan_calls


def test_register_skips_cleanly_without_url(clean_env, conan_calls):
  assert bootstrap.register_conan_remote() is False
  assert conan_calls == []


# -------------------------------- login failure classification --
# The lockout this prevents: ~6 fast retries against a WRONG password
# trip JFrog's brute-force protection, after which even the correct
# password gets a 403 that reads like bad credentials — and every
# further retry re-arms the block.

def test_failure_kinds_classify_the_field_outputs():
  auth = 'ERROR: Wrong user or password. [Remote: myremote]'
  lock = ('ERROR: 403 "This request is blocked due to recurrent request '
          'failures, please try again in 50 second(s)"')
  assert bootstrap._login_failure_kind(auth) == "auth"
  assert bootstrap._login_failure_kind(lock) == "lockout"
  assert bootstrap._login_failure_kind(
    "ERROR: HTTPSConnectionPool: no route to host") == "transient"
  assert bootstrap._login_failure_kind("Connection timed out") == "transient"


def test_a_lockout_that_masquerades_as_bad_credentials_is_a_lockout():
  """JFrog answers the blocked window with BOTH texts; retrying because
  it 'looks like' an auth error would re-arm the block — the 403 must
  win the classification."""
  both = ('ERROR: Wrong user or password. '
          'ERROR: 403 "This request is blocked due to recurrent request '
          'failures, please try again in 50 second(s)"')
  assert bootstrap._login_failure_kind(both) == "lockout"


def _register_with_login(monkeypatch, results):
  """Drive register_conan_remote with scripted login outcomes; every
  other conan call succeeds silently. Returns (attempts, result)."""
  monkeypatch.setenv("CONAN_REMOTE_NAME", "mirror")
  monkeypatch.setenv("CONAN_REMOTE_URL", "https://repo.example/conan")
  monkeypatch.setenv("CONAN_REMOTE_USER", "alice")
  monkeypatch.setenv("CONAN_REMOTE_PASS", "s3cret")
  monkeypatch.setattr(bootstrap, "_conan_bin", lambda: "/usr/bin/conan")
  monkeypatch.setattr(bootstrap.time, "sleep", lambda s: None)
  monkeypatch.setattr(bootstrap.subprocess, "check_call",
                      lambda cmd, env=None: None)
  attempts = []
  def run(cmd, env=None, check=None, **kwargs):
    class R:
      returncode = 0
      stdout = ""
      stderr = ""
    if cmd[1:3] == ["remote", "login"]:
      attempts.append(cmd)
      R.returncode, R.stderr = results[min(len(attempts) - 1,
                                           len(results) - 1)]
    return R()
  monkeypatch.setattr(bootstrap.subprocess, "run", run)
  return attempts, bootstrap.register_conan_remote()


def test_wrong_password_is_terminal_one_attempt_only(clean_env, monkeypatch):
  attempts, ok = _register_with_login(
    monkeypatch,
    [(1, "ERROR: Wrong user or password. [Remote: myremote]")])
  assert len(attempts) == 1, "a definitive rejection must not be retried"
  assert ok is False


def test_a_lockout_response_stops_the_loop(clean_env, monkeypatch):
  attempts, ok = _register_with_login(
    monkeypatch,
    [(1, 'ERROR: 403 "This request is blocked due to recurrent request '
         'failures, please try again in 50 second(s)"')])
  assert len(attempts) == 1, "retrying a lockout re-arms the block"
  assert ok is False


def test_transient_failures_still_retry_to_success(clean_env, monkeypatch):
  attempts, ok = _register_with_login(
    monkeypatch,
    [(1, "no route to host"), (1, "no route to host"), (0, "")])
  assert len(attempts) == 3
  assert ok is True
