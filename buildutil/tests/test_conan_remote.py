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
def conan_runs(monkeypatch):
  """Every conan invocation with the environment it was handed."""
  runs = []
  monkeypatch.setattr(bootstrap, "_conan_bin", lambda: "/usr/bin/conan")
  def record(cmd, env=None, check=None, **kwargs):
    runs.append({"argv": cmd, "env": dict(env or {})})
    class R:
      returncode = 0
      stdout = ""
      stderr = ""
    return R()
  monkeypatch.setattr(bootstrap.subprocess, "check_call",
                      lambda cmd, env=None: record(cmd, env) and None)
  monkeypatch.setattr(bootstrap.subprocess, "run", record)
  return runs


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
  assert ["remote", "login", "mirror", "alice"] in conan_calls
  assert ["remote", "remove", "conancenter"] in conan_calls


def test_login_never_puts_the_password_on_argv(clean_env, conan_runs,
                                              monkeypatch):
  """argv is readable in the process table and lands in every log."""
  monkeypatch.setenv("CONAN_REMOTE_NAME", "my-mirror")
  monkeypatch.setenv("CONAN_REMOTE_URL", "https://repo.example/conan")
  monkeypatch.setenv("CONAN_REMOTE_USER", "alice")
  monkeypatch.setenv("CONAN_REMOTE_PASS", "s3cret")
  assert bootstrap.register_conan_remote() is True
  for run in conan_runs:
    assert "s3cret" not in " ".join(run["argv"]), run["argv"]


def test_login_hands_the_password_to_the_child_environment(clean_env,
                                                           conan_runs,
                                                           monkeypatch):
  """conan 2 reads per-remote credentials from the environment; the
  remote name is upper-cased with dashes as underscores."""
  monkeypatch.setenv("CONAN_REMOTE_NAME", "my-mirror")
  monkeypatch.setenv("CONAN_REMOTE_URL", "https://repo.example/conan")
  monkeypatch.setenv("CONAN_REMOTE_USER", "alice")
  monkeypatch.setenv("CONAN_REMOTE_PASS", "s3cret")
  bootstrap.register_conan_remote()
  logins = [r for r in conan_runs if r["argv"][1:3] == ["remote", "login"]]
  assert len(logins) == 1
  assert logins[0]["env"]["CONAN_PASSWORD_MY_MIRROR"] == "s3cret"
  assert logins[0]["env"]["CONAN_LOGIN_USERNAME_MY_MIRROR"] == "alice"
  others = [r for r in conan_runs if r is not logins[0]]
  assert not any("CONAN_PASSWORD_MY_MIRROR" in r["env"] for r in others)


def test_the_remote_url_prints_masked(clean_env, conan_runs, monkeypatch,
                                      capsys):
  """Credentials in the authority are an artifactory/GitLab idiom."""
  monkeypatch.setenv("CONAN_REMOTE_URL",
                     "https://oauth2:sekrit@repo.example/conan")
  bootstrap.register_conan_remote()
  out = capsys.readouterr().out
  assert "sekrit" not in out
  assert "https://<credentials>@repo.example/conan" in out


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


# ---------------------------------------------------- the upload gate
# Pushing needs a write credential the server accepted. An anonymous
# remote cannot take an upload and a refused login must not be re-tried
# once per build, so both skip with a note instead.

def test_upload_target_skips_without_a_url(clean_env):
  name, note = bootstrap.upload_target()
  assert name is None
  assert "conan remote unset" in note


def test_upload_target_skips_an_anonymous_remote(clean_env, conan_calls,
                                                 monkeypatch):
  monkeypatch.setenv("CONAN_REMOTE_NAME", "mirror")
  monkeypatch.setenv("CONAN_REMOTE_URL", "https://repo.example/conan")
  bootstrap.register_conan_remote()
  name, note = bootstrap.upload_target()
  assert name is None
  assert "anonymous" in note and "mirror" in note


def test_upload_target_names_the_remote_after_an_accepted_login(
    clean_env, conan_calls, monkeypatch):
  monkeypatch.setenv("CONAN_REMOTE_NAME", "mirror")
  monkeypatch.setenv("CONAN_REMOTE_URL", "https://repo.example/conan")
  monkeypatch.setenv("CONAN_REMOTE_USER", "alice")
  monkeypatch.setenv("CONAN_REMOTE_PASS", "s3cret")
  bootstrap.register_conan_remote()
  assert bootstrap.upload_target() == ("mirror", "")


def test_upload_target_skips_a_refused_login(clean_env, monkeypatch):
  _register_with_login(
    monkeypatch,
    [(1, "ERROR: Wrong user or password. [Remote: myremote]")])
  name, note = bootstrap.upload_target()
  assert name is None
  assert "login" in note and "mirror" in note


def test_a_rotated_password_invalidates_the_accepted_login(
    clean_env, conan_calls, monkeypatch):
  """The stamp is fingerprinted on the whole seam: credentials that
  never went through a login are not credentials the server accepted."""
  monkeypatch.setenv("CONAN_REMOTE_NAME", "mirror")
  monkeypatch.setenv("CONAN_REMOTE_URL", "https://repo.example/conan")
  monkeypatch.setenv("CONAN_REMOTE_USER", "alice")
  monkeypatch.setenv("CONAN_REMOTE_PASS", "s3cret")
  bootstrap.register_conan_remote()
  monkeypatch.setenv("CONAN_REMOTE_PASS", "rotated")
  assert bootstrap.upload_target()[0] is None


def test_the_upload_note_never_carries_a_credential(clean_env, monkeypatch):
  monkeypatch.setenv("CONAN_REMOTE_URL",
                     "https://oauth2:sekrit@repo.example/conan")
  monkeypatch.setenv("CONAN_REMOTE_USER", "alice")
  monkeypatch.setenv("CONAN_REMOTE_PASS", "s3cret")
  note = bootstrap.upload_target()[1]
  assert "sekrit" not in note and "s3cret" not in note


def test_the_build_upload_obeys_the_gate(clean_env, monkeypatch, capsys):
  """engine._upload_to_remote is the caller: gated means no conan runs
  at all, not an upload that fails at the server."""
  from buildutil import engine
  ran = []
  monkeypatch.setattr(engine.subprocess, "check_call", lambda *a, **k: ran.append(a))
  monkeypatch.setattr(engine.subprocess, "run", lambda *a, **k: ran.append(a))
  monkeypatch.setenv("CONAN_REMOTE_URL", "https://repo.example/conan")
  engine._upload_to_remote([])
  assert ran == []
  assert "skipping upload" in capsys.readouterr().out


@pytest.mark.parametrize("configured", [True, False])
def test_dependency_upload_filters_and_merges_graphs(monkeypatch, tmp_path,
                                                     configured):
  from pathlib import Path
  from types import SimpleNamespace
  from buildutil import engine, packaging
  uploaded = []
  graphs = [tmp_path / "release.json", tmp_path / "debug.json"]
  monkeypatch.setattr(bootstrap, "upload_target", lambda: ("site", ""))
  monkeypatch.setattr(packaging, "configured", lambda: configured)
  monkeypatch.setattr(packaging, "package_name", lambda: "own")
  monkeypatch.setattr(engine.subprocess, "run",
                      lambda *a, **k: SimpleNamespace(stdout="site:"))

  def invoke(argv, **kwargs):
    if argv[1] == "list":
      assert "--graph-binaries=build" in argv
      assert "--graph-binaries=cache" in argv
      graph = next(a.split("=", 1)[1] for a in argv if a.startswith("--graph="))
      output = next(a.split("=", 1)[1] for a in argv if a.startswith("--out-file="))
      data = {"dep/1": {"revisions": {"r": {"packages": {
        Path(graph).stem: {"revisions": {"p": {}}}}}}},
        "own/1": {}, "own/2@buildutil/smoke": {}, "own-extra/1": {}}
      Path(output).write_text(json.dumps({"Local Cache": data}))
    else:
      assert argv[:3] == ["conan", "upload", "--list"]
      assert "*" not in argv
      uploaded.append(json.loads(Path(argv[3]).read_text())["Local Cache"])

  monkeypatch.setattr(engine.subprocess, "check_call", invoke)
  engine._upload_to_remote(graphs)
  assert set(uploaded[0]) == ({"dep/1", "own-extra/1"} if configured else
                             {"dep/1", "own-extra/1", "own/1", "own/2@buildutil/smoke"})
  assert set(uploaded[0]["dep/1"]["revisions"]["r"]["packages"]) == {"release", "debug"}


def test_empty_dependency_list_skips_upload(monkeypatch, tmp_path, capsys):
  from pathlib import Path
  from buildutil import engine, packaging
  monkeypatch.setattr(bootstrap, "upload_target", lambda: ("site", ""))
  monkeypatch.setattr(packaging, "configured", lambda: True)
  monkeypatch.setattr(packaging, "package_name", lambda: "own")

  def invoke(argv, **kwargs):
    assert argv[1] == "list"
    output = next(a.split("=", 1)[1] for a in argv if a.startswith("--out-file="))
    Path(output).write_text(json.dumps({"Local Cache": {"own/1": {}}}))

  monkeypatch.setattr(engine.subprocess, "check_call", invoke)
  engine._upload_to_remote([tmp_path / "graph.json"])
  assert capsys.readouterr().out == "no dependency binaries to upload\n"


def test_dependency_upload_self_heals_missing_remote(monkeypatch, tmp_path):
  from pathlib import Path
  from types import SimpleNamespace
  from buildutil import engine, packaging
  healed, uploaded = [], []
  monkeypatch.setattr(bootstrap, "upload_target", lambda: ("site", ""))
  monkeypatch.setattr(packaging, "configured", lambda: False)
  monkeypatch.setattr(bootstrap, "ensure_conan_remote",
                      lambda **k: healed.append(k))
  monkeypatch.setattr(engine.subprocess, "run",
                      lambda *a, **k: SimpleNamespace(stdout=""))

  def invoke(argv):
    if argv[1] == "list":
      output = next(a.split("=", 1)[1] for a in argv if a.startswith("--out-file="))
      Path(output).write_text(json.dumps({"Local Cache": {"dep/1": {}}}))
    else:
      assert healed == [{"force": True}]
      uploaded.append(argv)

  monkeypatch.setattr(engine.subprocess, "check_call", invoke)
  engine._upload_to_remote([tmp_path / "graph.json"])
  assert len(uploaded) == 1
  assert uploaded[0][-3:] == ["-r", "site", "--confirm"]
