"""One bench verb, seam-dispatched. Before 0.4.0 test.py and bench.py
each registered a `bench` command and the bossdeux-specific one
(python `inspector.bench` suite) silently shadowed the generic
*-benches runner — a fresh project's bench died importing a bossdeux
module."""


def test_bench_registered_exactly_once():
  from buildutil.app import app
  from buildutil.commands import bench, test  # noqa: F401 — registration
  names = [c.name or c.callback.__name__ for c in app.registered_commands]
  assert names.count("bench") == 1


def test_generic_default_no_suite_module_named():
  import inspect

  from buildutil.commands import bench
  src = inspect.getsource(bench)
  # the bossdeux module may appear only as the toml EXAMPLE, never as
  # a fallback the code would run without configuration
  assert 'PROJECT["bench_suite"]' in src
  assert '"-m", suite' in src
  assert '"-m", "inspector.bench"' not in src
