# Everything is presence-driven from the files in this directory:
# *.cpp (minus main.cpp) -> the `hello` static library,
# main.cpp              -> the `hello-app` executable (installed as `hello`),
# *.test.cpp            -> `hello-tests` (GoogleTest, runs under ctest),
# *.bench.cpp           -> `hello-benches` (google-benchmark, `buildutil bench`).
Init_submodule()
