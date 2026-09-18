"""vscode generation (0.7.0 shape): per-module tasks for what each
module actually HAS, launch configs for every executable target in the
debug-info flavors, remembered prompt values, and an UPSERTED
c_cpp_properties with the just-built profile on top."""
import json
import re

import pytest

from buildutil import vscode

MODS = {
  "hello":   {"rel": "hello", "app": True, "tests": True, "benches": True},
  "libonly": {"rel": "libonly", "app": False, "tests": False, "benches": False},
  "x-y":     {"rel": "x/y", "app": False, "tests": True, "benches": False},
  # a module inside a GROUP that ships an app: target name stays joined
  # (mstools-rc), binary is the leaf (rc)
  "mstools-rc": {"rel": "mstools/rc", "app": True, "tests": False,
                 "benches": False},
}


@pytest.fixture
def store(tmp_path, monkeypatch):
  monkeypatch.setattr(vscode, "_INPUTS_STORE", tmp_path / "inputs.json")
  return tmp_path / "inputs.json"


def _labels(doc):
  return [t["label"] for t in doc["tasks"]]


def _text(arg):
  """A task arg is a plain string or the quoted form {value, quoting}."""
  return arg["value"] if isinstance(arg, dict) else arg


def test_tasks_invoke_buildutil_on_path(store):
  doc = vscode._tasks_document(MODS, has_pytests=False)
  commands = {t["command"] for t in doc["tasks"]
              if "buildutil" in str(t.get("command", ""))}
  assert commands == {"buildutil"}
  assert not any("${workspaceFolder}/buildutil" in str(t) for t in doc["tasks"])


def test_per_module_tasks_match_what_exists(store):
  doc = vscode._tasks_document(MODS, has_pytests=False)
  labels = _labels(doc)
  P = vscode.PROJECT_NAME
  for flavor in ("debug", "release", "relwithdebinfo"):
    assert f"{P}: build hello ({flavor})" in labels
    assert f"{P}: run hello ({flavor})" in labels
    assert f"{P}: test hello ({flavor})" in labels
    assert f"{P}: build libonly ({flavor})" in labels
    # libonly has no app/tests/benches — no run/test/bench tasks
    assert f"{P}: run libonly ({flavor})" not in labels
    assert f"{P}: test libonly ({flavor})" not in labels
  # bench: release only — exactly one bench task, no flavor variants
  assert f"{P}: bench hello" in labels
  assert not any(lbl.startswith(f"{P}: bench hello (") for lbl in labels)
  # deps tasks: all + per configuration
  assert f"{P}: deps (all configurations)" in labels
  for flavor in ("debug", "release", "relwithdebinfo"):
    assert f"{P}: deps ({flavor})" in labels


def test_godbolt_tasks_per_module_and_whole_tree(store):
  vscode.record_input("godboltfilter", "hello", "greet")
  doc = vscode._tasks_document(MODS, has_pytests=False)
  labels = _labels(doc)
  P = vscode.PROJECT_NAME
  for flavor in ("debug", "release", "relwithdebinfo"):
    assert f"{P}: godbolt ({flavor})" in labels          # whole tree
    for name in MODS:                                    # every module
      assert f"{P}: godbolt {name} ({flavor})" in labels
  hello = next(t for t in doc["tasks"]
               if t["label"] == f"{P}: godbolt hello (release)")
  assert ["godbolt", "-m", "hello", "--release",
          {"value": "--function=${input:godboltfilter-hello}",
           "quoting": "strong"}] == hello["args"][-5:]
  inputs = {i["id"]: i for i in doc["inputs"]}
  assert inputs["godboltfilter-hello"]["default"] == "greet"
  assert inputs["godboltfilter-libonly"]["default"] == ""  # empty allowed


def test_python_variants_gated_on_pytests(store):
  without = _labels(vscode._tasks_document(MODS, has_pytests=False))
  with_py = _labels(vscode._tasks_document(MODS, has_pytests=True))
  P = vscode.PROJECT_NAME
  assert f"{P}: test (python only)" not in without
  assert f"{P}: test (python only)" in with_py


def test_prompts_carry_remembered_defaults(store):
  vscode.record_input("args", "hello", "--fast -n 3")
  vscode.record_input("filter", "hello", "Foo.*")
  doc = vscode._tasks_document(MODS, has_pytests=False)
  inputs = {i["id"]: i for i in doc["inputs"]}
  assert inputs["args-hello"]["default"] == "--fast -n 3"
  assert inputs["filter-hello"]["default"] == "Foo.*"
  assert inputs["benchfilter-hello"]["default"] == ""   # empty allowed
  assert "args-libonly" not in inputs
  # the run task routes the prompt through --args (shlex-split by run)
  run_tasks = [t for t in doc["tasks"]
               if t["label"].startswith(f"{vscode.PROJECT_NAME}: run hello")]
  assert run_tasks
  for task in run_tasks:
    assert {"value": "--args=${input:args-hello}",
            "quoting": "strong"} in task["args"]


def test_launch_covers_every_executable_in_debug_flavors(store):
  vscode.record_input("args", "hello", "--fast -n 3")
  vscode.record_input("filter", "x-y", "Bar")
  doc = vscode._launch_document(MODS, "x86_64-linux-gcc")
  by_name = {c["name"]: c for c in doc["configurations"]}
  for flavor in ("debug", "relwithdebinfo"):
    for bin_name in ("hello", "hello-tests", "hello-benches", "x-y-tests"):
      assert f"{bin_name} ({flavor})" in by_name, bin_name
  assert not any("(release)" in n for n in by_name)
  cfg = by_name["hello (debug)"]
  assert cfg["program"] == "${workspaceFolder}/_install/hello"
  assert cfg["preLaunchTask"] == f"{vscode.PROJECT_NAME}: build (debug)"
  assert cfg["args"] == ["--fast", "-n", "3"]        # remembered, SPLIT
  assert by_name["x-y-tests (debug)"]["args"] == ["--gtest_filter=Bar"]
  assert by_name["x-y-tests (debug)"]["program"] == (
    "${workspaceFolder}/_install/x/x-y-tests")


def test_grouped_app_launches_under_its_leaf_name(store):
  """A grouped module's app is named for its LEAF dir, and its remembered
  args are keyed by that name — the key `buildutil run --target rc` writes."""
  vscode.record_input("args", "rc", "-q")
  vscode.record_input("args", "mstools-rc", "WRONG")
  doc = vscode._launch_document(MODS, "x86_64-linux-gcc")
  by_name = {c["name"]: c for c in doc["configurations"]}
  assert "rc (debug)" in by_name
  assert "mstools-rc (debug)" not in by_name
  cfg = by_name["rc (debug)"]
  assert cfg["program"] == "${workspaceFolder}/_install/mstools/rc"
  assert cfg["args"] == ["-q"]
  assert cfg["preLaunchTask"] == f"{vscode.PROJECT_NAME}: build (debug)"
  # no native configs without a profile prefix; debugpy always present
  bare = vscode._launch_document(MODS, None)
  assert [c["type"] for c in bare["configurations"]] == ["debugpy"]


def test_launches_run_the_installed_binary_and_a_bundle_on_macos(
    store, monkeypatch):
  """A targeted build never installs, so every launch is preceded by the
  flavour's whole-tree build and runs what that installed; on macOS a
  [bundle.macos] app is a directory with the binary inside it."""
  mods = {**MODS, "hello": {**MODS["hello"], "bundle": True}}
  monkeypatch.setattr(vscode.platform, "system", lambda: "Darwin")
  doc = vscode._launch_document(mods, "armv8-macos-apple-clang")
  by_name = {c["name"]: c for c in doc["configurations"]}
  hello = by_name["hello (debug)"]
  assert hello["program"] == (
    "${workspaceFolder}/_install/hello.app/Contents/MacOS/hello")
  assert hello["MIMode"] == "lldb" and "miDebuggerPath" not in hello
  assert by_name["hello-tests (debug)"]["program"] == (
    "${workspaceFolder}/_install/hello-tests")
  monkeypatch.setattr(vscode.platform, "system", lambda: "Linux")
  linux = vscode._launch_document(mods, "x86_64-linux-gcc")["configurations"]
  hello = next(c for c in linux if c["name"] == "hello (debug)")
  assert hello["program"] == "${workspaceFolder}/_install/hello"
  assert hello["MIMode"] == "gdb" and hello["miDebuggerPath"] == "gdb"
  monkeypatch.setattr(vscode.platform, "system", lambda: "Windows")
  win = vscode._launch_document(mods, "x86_64-windows-msvc")["configurations"]
  assert next(c for c in win if c["name"] == "hello (debug)")["program"] == (
    "${workspaceFolder}/_install/hello.exe")


def test_a_bundle_helper_gets_no_launch(store):
  """A [bundle.macos.helpers] executable is spawned by the app; launched
  alone it has no browser to serve."""
  mods = {**MODS, "helper": {"rel": "helper.macos", "app": True,
                              "tests": False, "benches": False,
                              "helper": True}}
  names = [c["name"] for c in
           vscode._launch_document(mods, "armv8-macos-apple-clang")["configurations"]]
  assert not any(n.startswith("helper") for n in names), names
  assert "hello (debug)" in names


def test_every_prelaunch_task_exists(store):
  tasks = set(_labels(vscode._tasks_document(MODS, has_pytests=False)))
  launch = vscode._launch_document(MODS, "x86_64-linux-gcc")
  missing = [c["preLaunchTask"] for c in launch["configurations"]
             if "preLaunchTask" in c and c["preLaunchTask"] not in tasks]
  assert not missing, missing


def test_cpp_upserts_and_promotes_active(tmp_path, monkeypatch):
  monkeypatch.setattr(vscode, "REPO_ROOT", tmp_path)
  vsdir = tmp_path / ".vscode"
  vsdir.mkdir()
  hand_added = {"name": "my-cross", "cppStandard": "c++20"}
  (vsdir / "c_cpp_properties.json").write_text(json.dumps(
    {"version": 4, "configurations": [hand_added]}))
  doc = vscode._cpp_document("x86_64-linux-gcc", "x86_64-linux-gcc-release")
  names = [c["name"] for c in doc["configurations"]]
  assert names[0] == "x86_64-linux-gcc-release"      # active on top
  for flavor in ("debug", "relwithdebinfo"):
    assert f"x86_64-linux-gcc-{flavor}" in names     # expected, pre-build
  assert "my-cross" in names                         # upsert keeps others
  assert doc["configurations"][0]["compileCommands"].endswith(
    "_build/x86_64-linux-gcc-release/compile_commands.json")


def test_record_roundtrip_including_empty(store):
  vscode.record_input("args", "hello", "abc")
  assert vscode.recorded("args", "hello") == "abc"
  vscode.record_input("args", "hello", "")           # cleared is remembered
  assert vscode.recorded("args", "hello") == ""
  assert vscode.recorded("args", "nothing") == ""


def test_module_names_join_with_dash(tmp_path, monkeypatch):
  # '_' before 0.7.0 — buildutil.cmake names nested targets a-b
  monkeypatch.setattr(vscode, "SOURCES", tmp_path / "sources")
  nested = tmp_path / "sources" / "a" / "b"
  nested.mkdir(parents=True)
  (nested / "CMakeLists.txt").write_text("Init_submodule()\n")
  monkeypatch.setattr(vscode, "REPO_ROOT", tmp_path)
  assert list(vscode.discover()) == ["a-b"]


def test_every_task_disarms_the_watchdog(store):
  """vscode tasks are human-driven — the watchdog exists for agent
  invocations, and a human watching the terminal interrupts long work
  themselves (owner ruling). Every buildutil-invoking task must say so."""
  doc = vscode._tasks_document(MODS, has_pytests=True)
  buildutil_tasks = [t for t in doc["tasks"]
                     if "buildutil" in str(t.get("command", ""))]
  assert buildutil_tasks
  for t in buildutil_tasks:
    assert "--no-watchdog" in t["args"], t["label"]


def test_logging_tasks_clear_logs_themselves_with_no_shell_step(store):
  """`rm -rf _build/*.log` as a shell task failed under zsh when nothing
  matched; the driver now clears its own logs, so no task shells out."""
  doc = vscode._tasks_document(MODS, has_pytests=True)
  assert "clear log" not in _labels(doc)
  for t in doc["tasks"]:
    assert "dependsOn" not in t, t["label"]
    assert t["command"] != "rm", t["label"]
    if "--write-log-to" in t["args"]:
      assert "--clear-logs" in t["args"], t["label"]


def test_clear_logs_beside_removes_siblings_and_tolerates_none(tmp_path):
  from buildutil.app import _clear_logs_beside
  logs = tmp_path / "_build"
  logs.mkdir()
  (logs / "old-build.log").write_text("x")
  (logs / "other.log").write_text("y")
  (logs / "keep.txt").write_text("z")
  _clear_logs_beside(logs / "new.log")
  assert sorted(p.name for p in logs.iterdir()) == ["keep.txt"]
  _clear_logs_beside(logs / "new.log")
  _clear_logs_beside(tmp_path / "absent" / "new.log")


def test_tasks_prefer_the_repo_root_launcher(store, tmp_path, monkeypatch):
  """A project carrying the 0.42.0 ./buildutil launcher gets tasks that
  invoke IT — the venv-backed, vendored-copy-aware entry — instead of
  whatever PATH resolves."""
  launcher = tmp_path / "buildutil"
  launcher.write_text("#!/bin/sh\n")
  launcher.chmod(0o755)
  monkeypatch.setattr(vscode, "REPO_ROOT", tmp_path)
  doc = vscode._tasks_document(MODS, has_pytests=False)
  commands = {t["command"] for t in doc["tasks"]
              if "buildutil" in str(t.get("command", ""))}
  assert commands == {"./buildutil"}


def test_prompt_values_ride_one_strongly_quoted_token(store):
  """These are `"type": "shell"` tasks, so a prompt split over two
  tokens (`-f`, `${input:...}`) breaks twice: an EMPTY answer — which
  every prompt advertises as "empty = all" — drops the value token and
  leaves a dangling option that eats the next argument, and an answer
  like `*` or `Foo.*` is a live glob the shell expands before buildutil
  runs. Every input rides ONE `--long=${input:id}` token, strongly
  quoted."""
  doc = vscode._tasks_document(MODS, has_pytests=True)
  options = set()
  for task in doc["tasks"]:
    for arg in task["args"]:
      if "${input:" not in _text(arg):
        continue
      assert isinstance(arg, dict), (task["label"], arg)     # never bare
      assert arg["quoting"] == "strong", task["label"]
      option, sep, value = arg["value"].partition("=")
      assert sep == "=", task["label"]                       # joined, not split
      assert option.startswith("--"), task["label"]          # long form only
      assert re.fullmatch(r"\$\{input:[\w-]+\}", value), task["label"]
      options.add(option)
  assert options == {"--args", "--filter", "--function"}


def test_prompted_options_are_real_options_of_their_verb(store):
  """The =-joined form only parses as the LONG spelling, and each verb
  spells it its own way (godbolt's filter is --function). Pin every
  emitted option against the receiving command's actual signature."""
  from typer.main import get_command

  from buildutil.app import app
  from buildutil.commands import bench, godbolt, run, test  # noqa: F401
  cli = get_command(app)
  doc = vscode._tasks_document(MODS, has_pytests=True)
  checked = 0
  for task in doc["tasks"]:
    args = task["args"]
    prompted = [a for a in args if "${input:" in _text(a)]
    if not prompted:
      continue
    verb = args[args.index("--write-log-to") + 2]
    accepted = {opt for p in cli.commands[verb].params for opt in p.opts}
    for arg in prompted:
      assert arg["value"].partition("=")[0] in accepted, (task["label"], verb)
      checked += 1
  assert checked


def test_every_referenced_input_is_declared(store):
  doc = vscode._tasks_document(MODS, has_pytests=True)
  used = {ref for t in doc["tasks"] for a in t["args"]
          for ref in re.findall(r"\$\{input:([\w-]+)\}", _text(a))}
  assert used == {i["id"] for i in doc["inputs"]}


def test_filter_prompts_default_to_empty_not_a_glob(store):
  """testFilter shipped `*`: not a ctest -R regex (which is what the
  task hands it to), and a bare glob for the shell to expand. Empty is
  what "empty = all tests" promises and what the command accepts."""
  doc = vscode._tasks_document(MODS, has_pytests=True)
  inputs = {i["id"]: i for i in doc["inputs"]}
  assert inputs["testFilter"]["default"] == ""
  assert "empty = all tests" in inputs["testFilter"]["description"]
  # no prompt may advertise a default the command would choke on
  assert all(i["default"] != "*" for i in doc["inputs"])


def test_launch_configs_bake_values_and_never_prompt(store):
  """launch.json has no promptString escape hatch — it replays the
  RECORDED value, baked in at generation time as one =-joined argv
  entry (and omitted entirely when empty)."""
  vscode.record_input("filter", "x-y", "Bar")
  doc = vscode._launch_document(MODS, "x86_64-linux-gcc")
  assert "${input:" not in json.dumps(doc)
  by_name = {c["name"]: c for c in doc["configurations"]}
  assert by_name["x-y-tests (debug)"]["args"] == ["--gtest_filter=Bar"]
  assert by_name["hello-tests (debug)"]["args"] == []   # nothing recorded


def test_nested_module_args_default_reads_the_app_key(store):
  """run.py records --args under the APP (leaf) name; the prompt default
  for a nested module must read that key, not the joined name."""
  vscode.record_input("args", "rc", "--verbose")
  doc = vscode._tasks_document(MODS, has_pytests=False)
  inputs = {i["id"]: i for i in doc["inputs"]}
  assert inputs["args-mstools-rc"]["default"] == "--verbose"
