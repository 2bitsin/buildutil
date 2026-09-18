#include "@NAME@/hello/hello.hpp"

#include <benchmark/benchmark.h>

static void BM_Greeting(benchmark::State& state) {
  for (auto _ : state) {
    benchmark::DoNotOptimize(hello::greeting());
  }
}
BENCHMARK(BM_Greeting);
